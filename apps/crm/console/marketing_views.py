from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from leads.models import (
    LeadEventOutbox,
    LeadInboundEvent,
    LeadSubmission,
    PrivacyRequest,
    RetentionPolicy,
)
from leads.services import dispatch_claimed_outbox_event
from marketing.models import IntegrationCheck, MarketingIntegration

from .access import MARKETING_ROLES, SYSTEM_ROLES, safe_next_url, user_has_any_role
from .audit import outbox_snapshot, privacy_request_snapshot, record_audit
from .marketing_event_queries import redact_event_error
from .marketing_queries import (
    INTEGRATION_DIRECTIONS,
    build_marketing_overview,
    marketing_retry_max_attempts,
    outbox_retry_contract,
    retryable_outbox_q,
)
from .marketing_services import (
    build_privacy_export,
    execute_privacy_action,
    record_integration_diagnostic,
    register_privacy_request,
    transition_privacy_request,
)
from .payloads import default_site_locale
from .privacy_retention import execute_retention_policy, retention_preview, update_retention_policy


def _requested_locale(request) -> str | None:
    return (request.GET.get('locale') or request.POST.get('locale') or '').strip().lower() or None


def _validation_errors(exc: ValidationError) -> dict:
    if hasattr(exc, 'message_dict'):
        return exc.message_dict
    return {'__all__': exc.messages}


def _privacy_payload(item: PrivacyRequest) -> dict:
    return {
        'id': item.id,
        'request_type': item.request_type,
        'status': item.status,
        'requester_name': item.requester_name,
        'requester_email': item.requester_email,
        'requester_phone': item.requester_phone,
        'submission_id': item.submission_id,
        'handled_by': (
            item.handled_by_user.get_full_name().strip()
            or item.handled_by_user.get_username()
        ) if item.handled_by_user else '',
        'resolution': item.resolution,
        'requested_at': item.requested_at,
        'verified_at': item.verified_at,
        'completed_at': item.completed_at,
        'updated_at': item.updated_at,
    }


@login_required
@require_GET
def marketing_overview_data(request):
    site, _locale = default_site_locale(_requested_locale(request))
    return JsonResponse({'ok': True, **build_marketing_overview(site=site)})


@login_required
@require_GET
def integration_evidence_data(request, integration_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    integration = get_object_or_404(
        MarketingIntegration.objects.select_related('provider'),
        id=integration_id,
        site=site,
    )
    checks = IntegrationCheck.objects.filter(integration=integration).select_related('actor_user')[:100]
    outboxes = LeadEventOutbox.objects.filter(
        integration=integration,
        submission__site=site,
    ).select_related('submission').order_by('-created_at', '-id')[:100]
    inbound_events = LeadInboundEvent.objects.filter(
        integration=integration,
    ).select_related('submission').order_by('-received_at', '-id')[:100]
    outbound_payload = [
        {
            'id': item.id,
            'submission_id': item.submission_id,
            'event_name': item.event_name,
            'event_id': item.event_id,
            'status': item.status,
            'delivery_mode': item.delivery_mode,
            'attempts': item.attempts,
            'provider_request_id': item.provider_request_id,
            'provider_received_at': item.provider_received_at,
            'provider_processed_at': item.provider_processed_at,
            'match_status': item.match_status,
            'last_error': redact_event_error(item.last_error),
            'last_attempt_at': item.last_attempt_at,
            'next_attempt_at': item.next_attempt_at,
            'created_at': item.created_at,
        }
        for item in outboxes
    ]
    return JsonResponse({
        'ok': True,
        'integration': {
            'id': integration.id,
            'provider': integration.provider.code,
            'name': integration.name,
            'integration_type': integration.integration_type,
            'enabled': integration.enabled,
            'flow_direction': INTEGRATION_DIRECTIONS.get(
                (integration.provider.code, integration.integration_type),
                'unknown',
            ),
        },
        'checks': [
            {
                'id': item.id,
                'check_type': item.check_type,
                'delivery_mode': item.delivery_mode,
                'status': item.status,
                'evidence_level': item.evidence_level,
                'summary': item.summary,
                'details': item.details_json,
                'actor': item.actor_user.get_username() if item.actor_user else '',
                'checked_at': item.checked_at,
            }
            for item in checks
        ],
        'events': outbound_payload,
        'outbound_events': outbound_payload,
        'inbound_events': [
            {
                'id': item.id,
                'submission_id': item.submission_id,
                'provider_code': item.provider_code,
                'event_type': item.event_type,
                'external_event_id': item.external_event_id,
                'status': item.status,
                'attempts': item.attempts,
                'last_error': redact_event_error(item.last_error),
                'received_at': item.received_at,
                'processed_at': item.processed_at,
                'created_at': item.created_at,
            }
            for item in inbound_events
        ],
    })


@login_required
@require_POST
def integration_diagnose(request, integration_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    integration = get_object_or_404(
        MarketingIntegration.objects.select_related('provider'),
        id=integration_id,
        site=site,
    )
    diagnostic, evidence = record_integration_diagnostic(
        integration=integration,
        actor=request.user,
    )
    record_audit(
        actor=request.user,
        action='marketing_integration_diagnosed',
        entity_table='marketing_integration',
        entity_id=integration.id,
        request=request,
        metadata={
            'diagnostic_overall': diagnostic.get('overall', 'unknown'),
            'integration_check_id': evidence.id,
        },
    )
    return JsonResponse({
        'ok': True,
        'check_id': evidence.id,
        'diagnostic': diagnostic,
    })


@login_required
@require_POST
def marketing_outbox_retry(request, outbox_id: int):
    if not user_has_any_role(request.user, MARKETING_ROLES):
        raise PermissionDenied('当前账号没有处理营销事件的权限。')
    site, _locale = default_site_locale(None)
    outbox = get_object_or_404(
        LeadEventOutbox.objects.select_related('integration__provider', 'submission'),
        id=outbox_id,
        submission__site=site,
        integration__site=site,
    )
    retryable, retry_reason = outbox_retry_contract(outbox)

    def snapshot(item):
        payload = outbox_snapshot(item)
        payload['last_error'] = redact_event_error(item.last_error)
        return payload

    def respond(*, status, payload, message, level):
        # ``next`` is accepted only for the retired per-row compatibility URL.
        # Both values still pass through ``safe_next_url`` below, so the alias
        # cannot turn into an open redirect while old forms migrate.
        return_to = str(
            request.POST.get('return_to') or request.POST.get('next') or ''
        ).strip()
        if return_to:
            getattr(messages, level)(request, message)
            fallback = reverse('console:marketing_events')
            return redirect(safe_next_url(request, return_to, fallback))
        return JsonResponse(payload, status=status)

    if not retryable:
        return respond(status=409, payload={
            'ok': False,
            'errors': {'status': [retry_reason]},
            'outbox': snapshot(outbox),
        }, message=f'事件已不能重试：{retry_reason}', level='warning')

    before = snapshot(outbox)
    claimed_at = timezone.now()
    with transaction.atomic():
        claimed = (
            LeadEventOutbox.objects.filter(
                id=outbox.id,
                submission__site=site,
                integration__site=site,
            )
            .filter(
                retryable_outbox_q(
                    now=claimed_at,
                    max_attempts=marketing_retry_max_attempts(),
                )
            )
            .update(status='sending', updated_at=claimed_at)
        )
        if claimed == 1:
            outbox.status = 'sending'
            outbox.updated_at = claimed_at
            record_audit(
                actor=request.user,
                action='outbox_retry_claimed',
                entity_table='lead_event_outbox',
                entity_id=outbox.id,
                before=before,
                after=snapshot(outbox),
                request=request,
                metadata={'exact_retry': True, 'intent': True},
            )
    if claimed != 1:
        outbox.refresh_from_db()
        return respond(status=409, payload={
            'ok': False,
            'errors': {'status': ['事件已被其他操作人或 worker 处理，请重新载入状态。']},
            'outbox': snapshot(outbox),
        }, message='事件已被其他操作人或 worker 处理，请重新载入状态。', level='warning')

    succeeded = dispatch_claimed_outbox_event(outbox)
    outbox.refresh_from_db()
    after = snapshot(outbox)
    record_audit(
        actor=request.user,
        action='outbox_retry_completed',
        entity_table='lead_event_outbox',
        entity_id=outbox.id,
        before=before,
        after=after,
        request=request,
        metadata={'succeeded': succeeded, 'exact_retry': True},
    )
    accepted = outbox.status in {'processing', 'validated', 'sent'}
    if outbox.status == 'partial':
        response_status = 207
    else:
        response_status = 202 if outbox.status == 'processing' else (200 if accepted else 502)
    payload = {
        'ok': accepted,
        'completed': succeeded,
        'needs_manual_review': outbox.status == 'partial',
        'outbox': after,
    }
    if accepted:
        message = (
            f'事件 Outbox #{outbox.id} 已重新提交，平台仍在处理。'
            if outbox.status == 'processing'
            else f'事件 Outbox #{outbox.id} 已由平台接收。'
        )
        level = 'success'
    elif outbox.status == 'partial':
        message = f'事件 Outbox #{outbox.id} 仅部分成功，已停止自动重试，请人工核对。'
        level = 'warning'
    else:
        message = (
            f'事件 Outbox #{outbox.id} 仍未恢复：'
            f'{redact_event_error(outbox.last_error)}'
        )
        level = 'error'
    return respond(
        status=response_status,
        payload=payload,
        message=message,
        level=level,
    )


@login_required
@require_GET
def privacy_requests_data(request):
    site, _locale = default_site_locale(_requested_locale(request))
    status_filter = str(request.GET.get('status') or '').strip().lower()
    queryset = PrivacyRequest.objects.filter(site=site).select_related('handled_by_user')
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    return JsonResponse({
        'ok': True,
        'requests': [_privacy_payload(item) for item in queryset[:250]],
    })


@login_required
@require_POST
def privacy_request_create(request):
    site, _locale = default_site_locale(_requested_locale(request))
    submission = None
    submission_id = str(request.POST.get('submission_id') or '').strip()
    if submission_id:
        if not submission_id.isdigit():
            return JsonResponse({'ok': False, 'errors': {'submission_id': ['线索编号无效。']}}, status=400)
        submission = get_object_or_404(LeadSubmission.objects.filter(site=site), id=int(submission_id))
    try:
        with transaction.atomic():
            item, created = register_privacy_request(
                site=site,
                request_type=request.POST.get('request_type', ''),
                requester_name=request.POST.get('requester_name', ''),
                requester_email=request.POST.get('requester_email', ''),
                requester_phone=request.POST.get('requester_phone', ''),
                submission=submission,
                source='console_api',
            )
            if created:
                record_audit(
                    actor=request.user,
                    action='privacy_request_created',
                    entity_table='privacy_request',
                    entity_id=item.id,
                    request=request,
                    metadata={'request_type': item.request_type},
                )
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'errors': _validation_errors(exc)}, status=400)
    return JsonResponse(
        {'ok': True, 'created': created, 'request': _privacy_payload(item)},
        status=201 if created else 200,
    )


@login_required
@require_POST
def privacy_request_update(request, privacy_request_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    target_status = str(request.POST.get('status') or '').strip().lower()
    try:
        with transaction.atomic():
            item = get_object_or_404(
                PrivacyRequest.objects.select_for_update(of=('self',)).select_related('handled_by_user'),
                id=privacy_request_id,
                site=site,
            )
            if target_status == 'completed':
                if item.request_type != 'export':
                    return JsonResponse({
                        'ok': False,
                        'errors': {'status': ['非导出请求只能由系统管理员执行实际动作后完成。']},
                    }, status=409)
                if not user_has_any_role(request.user, SYSTEM_ROLES):
                    raise PermissionDenied
            before = privacy_request_snapshot(item)
            transition_privacy_request(
                privacy_request=item,
                target_status=target_status,
                actor=request.user,
                resolution=request.POST.get('resolution', ''),
            )
            record_audit(
                actor=request.user,
                action='privacy_request_status_changed',
                entity_table='privacy_request',
                entity_id=item.id,
                before=before,
                after=privacy_request_snapshot(item),
                request=request,
            )
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'errors': _validation_errors(exc)}, status=400)
    return JsonResponse({'ok': True, 'request': _privacy_payload(item)})


@login_required
@require_GET
def privacy_request_export(request, privacy_request_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    item = get_object_or_404(PrivacyRequest, id=privacy_request_id, site=site)
    try:
        payload = build_privacy_export(item)
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'errors': _validation_errors(exc)}, status=409)
    record_audit(
        actor=request.user,
        action='privacy_request_exported',
        entity_table='privacy_request',
        entity_id=item.id,
        request=request,
        metadata={'request_type': item.request_type},
    )
    response = HttpResponse(
        json.dumps(payload, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder),
        content_type='application/json; charset=utf-8',
    )
    response['Content-Disposition'] = f'attachment; filename="privacy-export-{item.id}.json"'
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_POST
def privacy_request_execute(request, privacy_request_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    item = get_object_or_404(PrivacyRequest, id=privacy_request_id, site=site)
    required_confirmation = 'DELETE' if item.request_type == 'delete' else 'EXECUTE'
    if str(request.POST.get('confirm') or '').strip() != required_confirmation:
        return JsonResponse({
            'ok': False,
            'errors': {
                'confirm': [f'请输入 {required_confirmation} 确认执行。'],
            },
        }, status=400)
    try:
        with transaction.atomic():
            item = get_object_or_404(
                PrivacyRequest.objects.select_for_update(),
                id=privacy_request_id,
                site=site,
            )
            before = privacy_request_snapshot(item)
            result = execute_privacy_action(privacy_request=item, actor=request.user)
            record_audit(
                actor=request.user,
                action='privacy_request_executed',
                entity_table='privacy_request',
                entity_id=item.id,
                before=before,
                after=privacy_request_snapshot(item),
                request=request,
                metadata=result,
            )
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'errors': _validation_errors(exc)}, status=409)
    return JsonResponse({'ok': True, 'request': _privacy_payload(item), 'result': result})


def _retention_policy_payload(policy: RetentionPolicy) -> dict:
    return {
        'id': policy.id,
        'enabled': policy.enabled,
        'lead_pii_retention_days': policy.lead_pii_retention_days,
        'inbound_payload_retention_days': policy.inbound_payload_retention_days,
        'outbox_payload_retention_days': policy.outbox_payload_retention_days,
        'privacy_request_retention_days': policy.privacy_request_retention_days,
        'whatsapp_message_retention_days': policy.whatsapp_message_retention_days,
        'whatsapp_media_retention_days': policy.whatsapp_media_retention_days,
        'updated_by_user_id': policy.updated_by_user_id,
        'updated_at': policy.updated_at,
    }


@login_required
@require_GET
def retention_policy_data(request):
    site, _locale = default_site_locale(_requested_locale(request))
    policy, _created = RetentionPolicy.objects.get_or_create(site=site)
    return JsonResponse({
        'ok': True,
        'policy': _retention_policy_payload(policy),
        'preview': retention_preview(policy),
    })


@login_required
@require_POST
def retention_policy_update(request):
    site, _locale = default_site_locale(_requested_locale(request))
    raw_values = {
        'lead_pii_retention_days': request.POST.get('lead_pii_retention_days', '0'),
        'inbound_payload_retention_days': request.POST.get('inbound_payload_retention_days', '90'),
        'outbox_payload_retention_days': request.POST.get('outbox_payload_retention_days', '180'),
        'privacy_request_retention_days': request.POST.get('privacy_request_retention_days', '365'),
        'whatsapp_message_retention_days': request.POST.get('whatsapp_message_retention_days', '1095'),
        'whatsapp_media_retention_days': request.POST.get('whatsapp_media_retention_days', '365'),
    }
    if not all(str(value).strip().isdigit() for value in raw_values.values()):
        return JsonResponse({'ok': False, 'errors': {'retention_days': ['保留天数必须是整数。']}}, status=400)
    try:
        with transaction.atomic():
            policy, _created = RetentionPolicy.objects.get_or_create(site=site)
            policy = RetentionPolicy.objects.select_for_update().get(pk=policy.pk)
            before = _retention_policy_payload(policy)
            update_retention_policy(
                policy=policy,
                actor=request.user,
                enabled=str(request.POST.get('enabled') or '').lower() in {'1', 'true', 'on', 'yes'},
                **{key: int(value) for key, value in raw_values.items()},
            )
            record_audit(
                actor=request.user,
                action='privacy_retention_policy_updated',
                entity_table='privacy_retention_policy',
                entity_id=policy.id,
                before=before,
                after=_retention_policy_payload(policy),
                request=request,
            )
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'errors': _validation_errors(exc)}, status=400)
    return JsonResponse({
        'ok': True,
        'policy': _retention_policy_payload(policy),
        'preview': retention_preview(policy),
    })


@login_required
@require_POST
def retention_policy_execute(request):
    site, _locale = default_site_locale(_requested_locale(request))
    policy = get_object_or_404(RetentionPolicy, site=site)
    if str(request.POST.get('confirm') or '').strip() != 'APPLY RETENTION':
        return JsonResponse({
            'ok': False,
            'errors': {'confirm': ['请输入 APPLY RETENTION 确认执行。']},
        }, status=400)
    try:
        with transaction.atomic():
            policy = get_object_or_404(
                RetentionPolicy.objects.select_for_update(),
                pk=policy.pk,
                site=site,
            )
            result = execute_retention_policy(policy=policy, actor=request.user)
            record_audit(
                actor=request.user,
                action='privacy_retention_policy_executed',
                entity_table='privacy_retention_policy',
                entity_id=policy.id,
                request=request,
                metadata=result,
            )
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'errors': _validation_errors(exc)}, status=409)
    return JsonResponse({'ok': True, 'result': result, 'preview': retention_preview(policy)})
