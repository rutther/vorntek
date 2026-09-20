from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from leads.inbound import InboundReceiptRetryConflict, retry_inbound_receipt_by_id
from leads.models import LeadInboundEvent

from .access import MARKETING_ROLES, primary_role_label, safe_next_url, user_has_any_role
from .audit import record_audit
from .error_redaction import redact_event_error
from .marketing_event_queries import (
    build_marketing_event_workspace,
    canonical_marketing_event_query,
)
from .marketing_queries import marketing_retry_max_attempts
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale


def _require_marketing_role(user) -> None:
    if not user_has_any_role(user, MARKETING_ROLES):
        raise PermissionDenied('当前账号没有处理营销事件的权限。')


def _receipt_snapshot(receipt: LeadInboundEvent) -> dict:
    return {
        'status': receipt.status,
        'attempts': receipt.attempts,
        'integration_id': receipt.integration_id,
        'submission_id': receipt.submission_id,
        'last_error': redact_event_error(receipt.last_error),
        'processed_at': receipt.processed_at,
    }


def _action_response(request, *, status: int, payload: dict, message: str, level: str):
    return_to = str(request.POST.get('return_to') or '').strip()
    if return_to:
        getattr(messages, level)(request, message)
        fallback = reverse('console:marketing_events')
        return redirect(safe_next_url(request, return_to, fallback))
    return JsonResponse(payload, status=status)


def _error_workspace(*, site, request) -> dict:
    endpoint = reverse('console:marketing_events')
    return {
        'site': site,
        'title': '事件运营',
        'description': '查看平台接收、发送与处理证据，并恢复可安全重试的失败。',
        'load_error': True,
        'retry_url': request.get_full_path() or endpoint,
        'filters': {
            'q': '', 'direction': '', 'provider': '', 'status': 'needs_attention',
            'explicit_status': '', 'retry': '', 'page_size': 25,
        },
        'options': {'directions': [], 'providers': [], 'statuses': [], 'page_sizes': ()},
        'summary': [],
        'rows': [],
        'total': 0,
        'total_site_events': 0,
        'active_filter_labels': [],
        'clear_filters_url': endpoint,
        'view_all_url': f'{endpoint}?status=all',
        'platforms_url': f'{reverse("console:marketing")}?tab=integrations',
        'attribution_url': reverse('console:marketing_attribution'),
        'current_url': request.get_full_path() or endpoint,
        'pagination': {
            'current_page': 1, 'total_pages': 1, 'previous_url': '',
            'next_url': '', 'start': 0, 'end': 0,
        },
    }


@login_required
@require_GET
def marketing_events(request):
    _require_marketing_role(request.user)
    canonical_query = canonical_marketing_event_query(request.GET)
    if str(request.META.get('QUERY_STRING') or '') != canonical_query:
        target = request.path
        if canonical_query:
            target = f'{target}?{canonical_query}'
        return redirect(target)
    # Marketing evidence is site-scoped, not locale-scoped.  We resolve the
    # site's default locale only for the global "view website" shell action.
    site, locale = default_site_locale(None)
    try:
        workspace = build_marketing_event_workspace(
            site=site,
            params=request.GET,
        )
    except DatabaseError:
        workspace = _error_workspace(site=site, request=request)
    return render(
        request,
        'console/v2/pages/marketing_events.html',
        {
            'workspace': workspace,
            'nav_items': build_navigation('marketing_events', request.user),
            'console_role_label': primary_role_label(request.user),
            'preview_site_url': f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
            'create_actions': [],
        },
        status=503 if workspace.get('load_error') else 200,
    )


@login_required
@require_POST
def marketing_inbound_retry(request, receipt_id: int):
    _require_marketing_role(request.user)
    site, _locale = default_site_locale(None)
    before_record = (
        LeadInboundEvent.objects.select_related('integration__provider', 'submission')
        .filter(id=receipt_id, integration__site=site)
        .first()
    )
    if before_record is None:
        raise Http404('未找到当前站点的入站事件。')
    before = _receipt_snapshot(before_record)

    def record_claim_intent(claimed_receipt):
        record_audit(
            actor=request.user,
            action='inbound_receipt_retry_claimed',
            entity_table='lead_inbound_event',
            entity_id=claimed_receipt.id,
            before=before,
            after=_receipt_snapshot(claimed_receipt),
            request=request,
            metadata={'exact_retry': True, 'intent': True},
        )

    try:
        receipt, succeeded = retry_inbound_receipt_by_id(
            receipt_id=receipt_id,
            site_id=site.id,
            max_attempts=marketing_retry_max_attempts(),
            on_claim=record_claim_intent,
        )
    except LeadInboundEvent.DoesNotExist as exc:
        raise Http404('未找到当前站点的入站事件。') from exc
    except InboundReceiptRetryConflict as exc:
        return _action_response(
            request,
            status=409,
            payload={'ok': False, 'errors': {'status': [str(exc)]}},
            message=f'事件已不能重试：{exc}',
            level='warning',
        )

    receipt.refresh_from_db()
    after = _receipt_snapshot(receipt)
    record_audit(
        actor=request.user,
        action='inbound_receipt_retry_completed',
        entity_table='lead_inbound_event',
        entity_id=receipt.id,
        before=before,
        after=after,
        request=request,
        metadata={'succeeded': succeeded, 'exact_retry': True},
    )
    if succeeded:
        return _action_response(
            request,
            status=200,
            payload={'ok': True, 'completed': True, 'receipt': after},
            message=f'入站事件 Receipt #{receipt.id} 已处理完成。',
            level='success',
        )
    return _action_response(
        request,
        status=502,
        payload={'ok': False, 'completed': False, 'receipt': after},
        message=(
            f'入站事件 Receipt #{receipt.id} 仍未恢复：'
            f'{redact_event_error(receipt.last_error)}'
        ),
        level='error',
    )
