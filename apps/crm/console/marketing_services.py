from __future__ import annotations

import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from leads.crm_relationships import all_linked_targets_in_scope_q
from leads.models import (
    Activity,
    Company,
    ConsentRecord,
    Contact,
    CrmAttachment,
    LeadEventOutbox,
    LeadInboundEvent,
    LeadSubmission,
    Opportunity,
    PrivacyRequest,
    Task,
)
from marketing.models import IntegrationCheck, MarketingIntegration

from .marketing_diagnostics import diagnose_marketing_integration
from .crm_files import retire_private_attachments


PRIVACY_TRANSITIONS = {
    'pending': frozenset({'verified', 'rejected'}),
    'verified': frozenset({'processing', 'rejected'}),
    'processing': frozenset({'completed', 'rejected'}),
    'completed': frozenset(),
    'rejected': frozenset(),
}

PRIVACY_REQUEST_TYPES = frozenset({'export', 'delete', 'restrict', 'withdraw_consent'})


@transaction.atomic
def register_privacy_request(
    *,
    site,
    request_type: str,
    requester_name: str = '',
    requester_email: str = '',
    requester_phone: str = '',
    submission: LeadSubmission | None = None,
    source: str = 'console',
) -> tuple[PrivacyRequest, bool]:
    cleaned_type = str(request_type or '').strip().lower()
    cleaned_name = str(requester_name or '').strip()[:500]
    cleaned_email = str(requester_email or '').strip().lower()[:500]
    cleaned_phone = str(requester_phone or '').strip()[:500]
    if cleaned_type not in PRIVACY_REQUEST_TYPES:
        raise ValidationError({'request_type': '请求类型无效。'})
    if not cleaned_email and not cleaned_phone:
        raise ValidationError({'__all__': '邮箱或电话至少填写一项。'})
    if submission is not None and submission.site_id != site.id:
        raise ValidationError({'submission_id': '关联线索不属于当前站点。'})

    # Serialize registration per site so concurrent requests resolve to one active case.
    site.__class__.objects.select_for_update().get(pk=site.pk)
    subject_material = f'{site.id}|{cleaned_email}|{cleaned_phone}'
    subject_key = hashlib.sha256(subject_material.encode('utf-8')).hexdigest()
    existing = PrivacyRequest.objects.filter(
        site=site,
        request_type=cleaned_type,
        subject_key=subject_key,
        status__in=['pending', 'verified', 'processing'],
    ).order_by('-requested_at', '-id').first()
    if existing:
        return existing, False

    item = PrivacyRequest.objects.create(
        site=site,
        submission=submission,
        request_type=cleaned_type,
        requester_name=cleaned_name,
        requester_email=cleaned_email,
        requester_phone=cleaned_phone,
        subject_key=subject_key,
        request_json={'source': str(source or 'console')[:80]},
        requested_at=timezone.now(),
    )
    return item, True


def _diagnostic_evidence_level(diagnostic: dict) -> str:
    provider_checks = {
        'token', 'dataset_access', 'page_token', 'leadgen_forms',
        'whatsapp_token', 'waba_access', 'google_credentials',
    }
    if any(
        check.get('status') == 'pass' and check.get('code') in provider_checks
        for check in diagnostic.get('checks', [])
    ):
        return 'provider_response'
    return 'local'


@transaction.atomic
def record_integration_diagnostic(
    *,
    integration: MarketingIntegration,
    actor,
) -> tuple[dict, IntegrationCheck]:
    diagnostic = diagnose_marketing_integration(integration)
    evidence = IntegrationCheck.objects.create(
        integration=integration,
        actor_user=actor if getattr(actor, 'is_authenticated', False) else None,
        check_type='connection',
        delivery_mode='validation',
        status={
            'pass': 'passed',
            'warn': 'warning',
            'fail': 'failed',
        }.get(str(diagnostic.get('overall') or ''), 'unknown'),
        evidence_level=_diagnostic_evidence_level(diagnostic),
        summary=str(diagnostic.get('summary') or ''),
        details_json=diagnostic,
    )
    integration.config_json = {**(integration.config_json or {}), 'last_diagnostic': diagnostic}
    integration.save(update_fields=['config_json', 'updated_at'])
    return diagnostic, evidence


@transaction.atomic
def transition_privacy_request(
    *,
    privacy_request: PrivacyRequest,
    target_status: str,
    actor,
    resolution: str = '',
) -> PrivacyRequest:
    target = str(target_status or '').strip().lower()
    allowed = PRIVACY_TRANSITIONS.get(privacy_request.status, frozenset())
    if target not in allowed:
        raise ValidationError(
            {'status': f'隐私请求不能从 {privacy_request.status} 变更为 {target or "空状态"}。'}
        )
    cleaned_resolution = str(resolution or '').strip()
    if target in {'completed', 'rejected'} and not cleaned_resolution:
        raise ValidationError({'resolution': '完成或拒绝隐私请求时必须填写处理结论。'})

    now = timezone.now()
    privacy_request.status = target
    privacy_request.handled_by_user = actor
    privacy_request.resolution = cleaned_resolution
    if target == 'verified' and privacy_request.verified_at is None:
        privacy_request.verified_at = now
    if target in {'completed', 'rejected'}:
        privacy_request.completed_at = now
    privacy_request.updated_at = now
    privacy_request.save(
        update_fields=[
            'status', 'handled_by_user', 'resolution', 'verified_at',
            'completed_at', 'updated_at',
        ]
    )
    return privacy_request


def privacy_subject_submissions(privacy_request: PrivacyRequest):
    queryset = LeadSubmission.objects.filter(site=privacy_request.site)
    if privacy_request.submission_id:
        return queryset.filter(id=privacy_request.submission_id)
    identity_filter = Q(pk__in=[])
    if privacy_request.requester_email:
        identity_filter |= Q(email__iexact=privacy_request.requester_email.strip())
    if privacy_request.requester_phone:
        identity_filter |= Q(phone=privacy_request.requester_phone.strip())
    return queryset.filter(identity_filter)


def privacy_subject_relationship_ids(*, site, submission_ids) -> dict[str, list[int]]:
    """Resolve the exact CRM object graph attributable to verified leads."""

    normalized_submission_ids = list(submission_ids)
    contact_ids = list(
        Contact.objects.filter(
            site=site,
            lead_conversions__submission_id__in=normalized_submission_ids,
        )
        .values_list('id', flat=True)
        .distinct()
    )
    opportunity_ids = list(
        Opportunity.objects.filter(
            site=site,
            source_submission_id__in=normalized_submission_ids,
        )
        .values_list('id', flat=True)
        .distinct()
    )
    company_ids = list(
        Company.objects.filter(site=site)
        .filter(
            Q(lead_conversions__submission_id__in=normalized_submission_ids)
            | Q(contacts__id__in=contact_ids)
            | Q(opportunities__id__in=opportunity_ids)
        )
        .values_list('id', flat=True)
        .distinct()
    )
    return {
        'submission_ids': normalized_submission_ids,
        'company_ids': company_ids,
        'contact_ids': contact_ids,
        'opportunity_ids': opportunity_ids,
    }


def build_privacy_export(privacy_request: PrivacyRequest) -> dict:
    if privacy_request.status not in {'verified', 'processing', 'completed'}:
        raise ValidationError({'status': '完成身份核验后才能导出个人数据。'})
    submissions = privacy_subject_submissions(privacy_request)
    submission_ids = list(submissions.values_list('id', flat=True))
    relationship_ids = privacy_subject_relationship_ids(
        site=privacy_request.site,
        submission_ids=submission_ids,
    )
    company_ids = relationship_ids['company_ids']
    contact_ids = relationship_ids['contact_ids']
    opportunity_ids = relationship_ids['opportunity_ids']
    subject_scope = all_linked_targets_in_scope_q(
        **relationship_ids,
    )
    activities = Activity.objects.filter(
        site=privacy_request.site,
    ).filter(subject_scope)
    tasks = Task.objects.filter(site=privacy_request.site).filter(subject_scope)
    attachments = CrmAttachment.objects.filter(
        site=privacy_request.site,
        status='active',
    ).filter(subject_scope)
    return {
        'generated_at': timezone.now(),
        'request': {
            'id': privacy_request.id,
            'request_type': privacy_request.request_type,
            'status': privacy_request.status,
            'requested_at': privacy_request.requested_at,
        },
        'submissions': list(submissions.values(
            'id', 'submission_key', 'stage', 'full_name', 'email', 'phone', 'company',
            'country', 'message', 'source_url', 'referrer_url', 'source_channel',
            'source_detail', 'payload_json', 'identifiers_json', 'utm_json',
            'consent_json', 'submitted_at', 'updated_at',
        )),
        'consent_records': list(
            ConsentRecord.objects.filter(submission_id__in=submission_ids).values(
                'id', 'submission_id', 'purpose', 'decision', 'source',
                'policy_version', 'evidence_json', 'captured_at',
            )
        ),
        'contacts': list(Contact.objects.filter(id__in=contact_ids).values(
            'id', 'company_id', 'full_name', 'job_title', 'email', 'phone',
            'whatsapp_phone', 'country', 'preferred_language', 'status', 'notes',
            'created_at', 'updated_at',
        )),
        'opportunities': list(Opportunity.objects.filter(id__in=opportunity_ids).values(
            'id', 'company_id', 'primary_contact_id', 'source_submission_id', 'name',
            'stage', 'value_amount', 'currency', 'product_scope', 'capacity_target',
            'packaging_format', 'source_channel', 'source_detail', 'next_step',
            'created_at', 'updated_at',
        )),
        'activities': list(activities.values(
            'id', 'submission_id', 'contact_id', 'opportunity_id', 'activity_type',
            'direction', 'subject', 'body', 'metadata_json', 'occurred_at', 'created_at',
        )),
        'tasks': list(tasks.values(
            'id', 'submission_id', 'contact_id', 'opportunity_id', 'title', 'description',
            'task_type', 'priority', 'status', 'due_at', 'completed_at', 'outcome',
            'created_at', 'updated_at',
        )),
        'attachments': list(attachments.values(
            'id', 'submission_id', 'contact_id', 'opportunity_id', 'title',
            'original_name', 'mime_type', 'file_size_bytes', 'sha256', 'status',
            'created_at',
        )),
        'marketing_events': list(
            LeadEventOutbox.objects.filter(submission_id__in=submission_ids).values(
                'id', 'submission_id', 'event_name', 'event_id', 'delivery_mode',
                'status', 'provider_request_id', 'provider_received_at',
                'provider_processed_at', 'match_status', 'payload_json', 'created_at',
            )
        ),
        'scope_notice': (
            'The export contains records linked by the verified submission, email, or phone. '
            'Attachment metadata is included, while private file binaries require a separate '
            'authenticated transfer. Internal security credentials and unrelated company '
            'records are excluded.'
        ),
    }


@transaction.atomic
def execute_privacy_action(
    *,
    privacy_request: PrivacyRequest,
    actor,
) -> dict:
    if privacy_request.status != 'processing':
        raise ValidationError({'status': '隐私请求进入“处理中”后才能执行。'})
    if privacy_request.request_type == 'export':
        raise ValidationError({'request_type': '导出请求应下载数据包，不执行匿名化。'})

    submissions = privacy_subject_submissions(privacy_request).select_for_update()
    submission_ids = list(submissions.values_list('id', flat=True))
    if not submission_ids:
        raise ValidationError({'submission': '没有找到经过核验的数据主体记录。'})
    relationship_ids = privacy_subject_relationship_ids(
        site=privacy_request.site,
        submission_ids=submission_ids,
    )
    contact_ids = relationship_ids['contact_ids']
    now = timezone.now()

    for submission in submissions.select_related('form'):
        current_consent = dict(submission.consent_json or {})
        purposes = ('marketing', 'analytics', 'personalization')
        ConsentRecord.objects.bulk_create([
            ConsentRecord(
                submission=submission,
                actor_user=actor,
                purpose=purpose,
                decision='withdrawn',
                source='privacy_request',
                policy_version=str(current_consent.get('policy_version') or '')[:80],
                evidence_json={'privacy_request_id': privacy_request.id},
            )
            for purpose in purposes
            if bool(current_consent.get(purpose)) or purpose == 'marketing'
        ])

    if privacy_request.request_type == 'withdraw_consent':
        for submission in submissions:
            consent = dict(submission.consent_json or {})
            consent.update({'marketing': False, 'analytics': False, 'personalization': False})
            submission.consent_json = consent
            submission.identifiers_json = {}
            submission.utm_json = {}
            submission.updated_at = now
            submission.save(
                update_fields=['consent_json', 'identifiers_json', 'utm_json', 'updated_at']
            )
        resolution = f'已为 {len(submission_ids)} 条线索撤回营销测量同意并清除广告标识符。'
        retained_evidence = Activity.objects.filter(submission_id__in=submission_ids).count()
    elif privacy_request.request_type == 'restrict':
        for submission in submissions:
            submission.config_json = {
                **(submission.config_json or {}),
                'privacy_restricted': True,
                'privacy_request_id': privacy_request.id,
            }
            submission.updated_at = now
            submission.save(update_fields=['config_json', 'updated_at'])
        Contact.objects.filter(id__in=contact_ids).update(status='inactive', updated_at=now)
        resolution = f'已限制 {len(submission_ids)} 条线索及 {len(contact_ids)} 个联系人继续处理。'
        retained_evidence = Activity.objects.filter(submission_id__in=submission_ids).count()
    elif privacy_request.request_type == 'delete':
        attachments_removed = retire_private_attachments(
            CrmAttachment.objects.filter(site=privacy_request.site).filter(
                all_linked_targets_in_scope_q(**relationship_ids)
            ),
            reason='privacy_request',
        )
        LeadEventOutbox.objects.filter(submission_id__in=submission_ids).update(
            payload_json={},
            response_json={},
            last_error='',
            updated_at=now,
        )
        LeadInboundEvent.objects.filter(submission_id__in=submission_ids).update(
            payload_json={},
            last_error='',
            updated_at=now,
        )
        LeadSubmission.objects.filter(id__in=submission_ids).update(
            full_name='Deleted data subject',
            email='',
            phone='',
            message='',
            source_url='',
            referrer_url='',
            client_ip='',
            user_agent='',
            payload_json={},
            identifiers_json={},
            utm_json={},
            consent_json={'privacy_deleted': True},
            follow_up_notes='',
            contact_key='',
            dedupe_key='',
            config_json={'privacy_deleted': True, 'privacy_request_id': privacy_request.id},
            updated_at=now,
        )
        Contact.objects.filter(id__in=contact_ids).update(
            full_name='Deleted data subject',
            job_title='',
            email='',
            email_normalized='',
            phone='',
            phone_normalized='',
            whatsapp_phone='',
            preferred_language='',
            notes='',
            status='inactive',
            updated_at=now,
        )
        retained_evidence = Activity.objects.filter(
            Q(submission_id__in=submission_ids) | Q(contact_id__in=contact_ids)
        ).count()
        resolution = (
            f'已匿名化 {len(submission_ids)} 条线索和 {len(contact_ids)} 个联系人；'
            f'删除 {attachments_removed} 个仅绑定个人记录的私有附件；'
            f'{retained_evidence} 条不可变业务活动记录按审计策略保留。'
        )
    else:
        raise ValidationError({'request_type': '当前隐私请求类型不支持自动执行。'})

    privacy_request.request_json = {
        **(privacy_request.request_json or {}),
        'executed_at': now.isoformat(),
        'submission_count': len(submission_ids),
        'contact_count': len(contact_ids),
        'retained_immutable_activity_count': retained_evidence,
    }
    privacy_request.save(update_fields=['request_json', 'updated_at'])
    transition_privacy_request(
        privacy_request=privacy_request,
        target_status='completed',
        actor=actor,
        resolution=resolution,
    )
    if privacy_request.request_type == 'delete':
        privacy_request.requester_name = ''
        privacy_request.requester_email = ''
        privacy_request.requester_phone = ''
        privacy_request.save(
            update_fields=['requester_name', 'requester_email', 'requester_phone', 'updated_at']
        )
    return {
        'submission_count': len(submission_ids),
        'contact_count': len(contact_ids),
        'retained_immutable_activity_count': retained_evidence,
        'private_attachments_removed': attachments_removed if privacy_request.request_type == 'delete' else 0,
        'resolution': resolution,
    }
