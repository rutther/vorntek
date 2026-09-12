from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from leads.models import (
    Activity,
    ConsentRecord,
    Contact,
    CrmAttachment,
    LeadConversion,
    LeadEventOutbox,
    LeadInboundEvent,
    LeadSubmission,
    PrivacyRequest,
    RetentionPolicy,
    WhatsAppConversation,
    WhatsAppMedia,
    WhatsAppMessage,
)

from .crm_files import retire_private_attachments
from leads.crm_relationships import all_linked_targets_in_scope_q


TERMINAL_LEAD_STAGES = frozenset({'won', 'lost', 'spam'})
TERMINAL_OUTBOX_STATES = frozenset({'validated', 'sent', 'partial', 'failed', 'skipped'})
TERMINAL_INBOUND_STATES = frozenset({'processed', 'failed', 'skipped'})


def _lead_candidates(policy: RetentionPolicy, *, now):
    if policy.lead_pii_retention_days <= 0:
        return LeadSubmission.objects.none()
    cutoff = now - timedelta(days=policy.lead_pii_retention_days)
    return LeadSubmission.objects.filter(
        site=policy.site,
        stage__in=TERMINAL_LEAD_STAGES,
        stage_updated_at__lt=cutoff,
    ).filter(
        ~Q(full_name='') | ~Q(email='') | ~Q(phone='') | ~Q(message='')
    )


def _retention_subject_scope(*, policy: RetentionPolicy, lead_ids) -> dict[str, list[int]]:
    """Resolve only same-site CRM targets explicitly converted from these leads."""

    lead_ids = list(lead_ids)
    conversions = LeadConversion.objects.filter(
        submission_id__in=lead_ids,
        submission__site=policy.site,
    )
    return {
        'submission_ids': lead_ids,
        'company_ids': list(
            conversions.filter(company__site=policy.site)
            .values_list('company_id', flat=True).distinct()
        ),
        'contact_ids': list(
            conversions.filter(contact__site=policy.site)
            .values_list('contact_id', flat=True).distinct()
        ),
        'opportunity_ids': list(
            conversions.filter(opportunity__site=policy.site)
            .values_list('opportunity_id', flat=True).distinct()
        ),
    }


def _retention_attachment_queryset(*, policy: RetentionPolicy, subject_scope):
    return CrmAttachment.objects.filter(
        site=policy.site,
        status='active',
    ).filter(
        all_linked_targets_in_scope_q(**subject_scope)
    ).exclude(storage_path='')


def retention_preview(policy: RetentionPolicy, *, now=None) -> dict:
    now = now or timezone.now()
    inbound_cutoff = now - timedelta(days=policy.inbound_payload_retention_days)
    outbox_cutoff = now - timedelta(days=policy.outbox_payload_retention_days)
    request_cutoff = now - timedelta(days=policy.privacy_request_retention_days)
    whatsapp_message_cutoff = now - timedelta(days=policy.whatsapp_message_retention_days)
    whatsapp_media_cutoff = now - timedelta(days=policy.whatsapp_media_retention_days)
    leads = _lead_candidates(policy, now=now)
    lead_ids = list(leads.values_list('id', flat=True))
    subject_scope = _retention_subject_scope(policy=policy, lead_ids=lead_ids)
    return {
        'generated_at': now,
        'enabled': policy.enabled,
        'policy': {
            'lead_pii_retention_days': policy.lead_pii_retention_days,
            'inbound_payload_retention_days': policy.inbound_payload_retention_days,
            'outbox_payload_retention_days': policy.outbox_payload_retention_days,
            'privacy_request_retention_days': policy.privacy_request_retention_days,
            'whatsapp_message_retention_days': policy.whatsapp_message_retention_days,
            'whatsapp_media_retention_days': policy.whatsapp_media_retention_days,
        },
        'candidates': {
            'lead_pii': len(lead_ids),
            'private_attachments': _retention_attachment_queryset(
                policy=policy,
                subject_scope=subject_scope,
            ).count(),
            'inbound_payloads': LeadInboundEvent.objects.filter(
                integration__site=policy.site,
                status__in=TERMINAL_INBOUND_STATES,
                received_at__lt=inbound_cutoff,
            ).exclude(payload_json={}).count(),
            'outbox_payloads': LeadEventOutbox.objects.filter(
                submission__site=policy.site,
                status__in=TERMINAL_OUTBOX_STATES,
                created_at__lt=outbox_cutoff,
            ).filter(~Q(payload_json={}) | ~Q(response_json={})).count(),
            'privacy_request_identity': PrivacyRequest.objects.filter(
                site=policy.site,
                status__in=['completed', 'rejected'],
                completed_at__lt=request_cutoff,
            ).filter(
                ~Q(requester_name='') | ~Q(requester_email='') | ~Q(requester_phone='')
            ).count(),
            'whatsapp_message_contents': WhatsAppMessage.objects.filter(
                conversation__site=policy.site,
                created_at__lt=whatsapp_message_cutoff,
            ).filter(
                ~Q(body='') | ~Q(sender_id='') | ~Q(recipient_id='')
                | ~Q(payload_json={}) | ~Q(error_message='')
            ).count(),
            'whatsapp_media_files': WhatsAppMedia.objects.filter(
                message__conversation__site=policy.site,
                created_at__lt=whatsapp_media_cutoff,
                status='ready',
            ).exclude(storage_path='').count(),
            'retained_immutable_activities': Activity.objects.filter(
                site=policy.site,
                submission_id__in=lead_ids,
            ).count(),
        },
        'lead_pii_auto_anonymization': (
            'disabled' if policy.lead_pii_retention_days <= 0 else 'enabled'
        ),
    }


def update_retention_policy(
    *,
    policy: RetentionPolicy,
    actor,
    enabled: bool,
    lead_pii_retention_days: int,
    inbound_payload_retention_days: int,
    outbox_payload_retention_days: int,
    privacy_request_retention_days: int,
    whatsapp_message_retention_days: int = 1095,
    whatsapp_media_retention_days: int = 365,
) -> RetentionPolicy:
    values = {
        'lead_pii_retention_days': lead_pii_retention_days,
        'inbound_payload_retention_days': inbound_payload_retention_days,
        'outbox_payload_retention_days': outbox_payload_retention_days,
        'privacy_request_retention_days': privacy_request_retention_days,
        'whatsapp_message_retention_days': whatsapp_message_retention_days,
        'whatsapp_media_retention_days': whatsapp_media_retention_days,
    }
    for key, value in values.items():
        minimum = 0 if key == 'lead_pii_retention_days' else 1
        if value < minimum or value > 36500:
            raise ValidationError({key: f'保留天数必须在 {minimum} 到 36500 之间。'})
    policy.enabled = enabled
    for key, value in values.items():
        setattr(policy, key, value)
    policy.updated_by_user = actor
    policy.save(update_fields=['enabled', *values.keys(), 'updated_by_user', 'updated_at'])
    return policy


@transaction.atomic
def execute_retention_policy(*, policy: RetentionPolicy, actor, now=None) -> dict:
    if not policy.enabled:
        raise ValidationError({'enabled': '保留策略未启用，不能执行。'})
    now = now or timezone.now()
    preview = retention_preview(policy, now=now)
    inbound_cutoff = now - timedelta(days=policy.inbound_payload_retention_days)
    outbox_cutoff = now - timedelta(days=policy.outbox_payload_retention_days)
    request_cutoff = now - timedelta(days=policy.privacy_request_retention_days)
    whatsapp_message_cutoff = now - timedelta(days=policy.whatsapp_message_retention_days)
    whatsapp_media_cutoff = now - timedelta(days=policy.whatsapp_media_retention_days)

    media_removed = 0
    media_root = Path(settings.SITEOS_WHATSAPP_MEDIA_ROOT).resolve()
    media_items = list(
        WhatsAppMedia.objects.select_for_update().filter(
            message__conversation__site=policy.site,
            created_at__lt=whatsapp_media_cutoff,
            status='ready',
        ).exclude(storage_path='')
    )
    for media in media_items:
        path = Path(media.storage_path).resolve()
        try:
            path.relative_to(media_root)
        except ValueError:
            path = None
        if path is not None:
            path.unlink(missing_ok=True)
        media.storage_path = ''
        media.file_size_bytes = 0
        media.sha256 = ''
        media.status = 'deleted'
        media.error_message = ''
        media.next_attempt_at = None
        media.save(update_fields=[
            'storage_path', 'file_size_bytes', 'sha256', 'status',
            'error_message', 'next_attempt_at', 'updated_at',
        ])
        media_removed += 1

    whatsapp_messages_cleared = WhatsAppMessage.objects.filter(
        conversation__site=policy.site,
        created_at__lt=whatsapp_message_cutoff,
    ).filter(
        ~Q(body='') | ~Q(sender_id='') | ~Q(recipient_id='')
        | ~Q(payload_json={}) | ~Q(error_message='')
    ).update(
        body='',
        sender_id='',
        recipient_id='',
        payload_json={},
        error_message='',
        updated_at=now,
    )

    inbound_updated = LeadInboundEvent.objects.filter(
        integration__site=policy.site,
        status__in=TERMINAL_INBOUND_STATES,
        received_at__lt=inbound_cutoff,
    ).exclude(payload_json={}).update(payload_json={}, last_error='', updated_at=now)
    outbox_updated = LeadEventOutbox.objects.filter(
        submission__site=policy.site,
        status__in=TERMINAL_OUTBOX_STATES,
        created_at__lt=outbox_cutoff,
    ).filter(~Q(payload_json={}) | ~Q(response_json={})).update(
        payload_json={},
        response_json={},
        last_error='',
        updated_at=now,
    )
    requests_updated = PrivacyRequest.objects.filter(
        site=policy.site,
        status__in=['completed', 'rejected'],
        completed_at__lt=request_cutoff,
    ).filter(
        ~Q(requester_name='') | ~Q(requester_email='') | ~Q(requester_phone='')
    ).update(
        requester_name='',
        requester_email='',
        requester_phone='',
        updated_at=now,
    )

    leads = list(_lead_candidates(policy, now=now).select_for_update().select_related('form'))
    lead_ids = [item.id for item in leads]
    subject_scope = _retention_subject_scope(policy=policy, lead_ids=lead_ids)
    contact_ids = subject_scope['contact_ids']
    for submission in leads:
        ConsentRecord.objects.create(
            submission=submission,
            actor_user=actor,
            purpose='marketing',
            decision='withdrawn',
            source='retention_policy',
            policy_version='',
            evidence_json={'retention_policy_id': policy.id},
        )
    if lead_ids:
        attachments_removed = retire_private_attachments(
            _retention_attachment_queryset(
                policy=policy,
                subject_scope=subject_scope,
            ),
            reason='retention_policy',
        )
        LeadSubmission.objects.filter(id__in=lead_ids).update(
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
            consent_json={'privacy_deleted': True, 'retention_policy_id': policy.id},
            follow_up_notes='',
            contact_key='',
            dedupe_key='',
            config_json={'privacy_deleted': True, 'retention_policy_id': policy.id},
            updated_at=now,
        )
    else:
        attachments_removed = 0
    contacts_updated = Contact.objects.filter(
        site=policy.site,
        id__in=contact_ids,
    ).update(
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
    conversations_anonymized = 0
    if lead_ids:
        conversations = list(
            WhatsAppConversation.objects.select_for_update().filter(
                site=policy.site,
                submission_id__in=lead_ids,
            )
        )
        for conversation in conversations:
            conversation.external_contact_id = f'deleted:{conversation.id}'
            conversation.display_name = 'Deleted data subject'
            conversation.last_message_preview = ''
            conversation.status = 'blocked'
            conversation.opted_out_at = conversation.opted_out_at or now
            conversation.opt_out_reason = 'retention_policy'
            conversation.metadata_json = {'privacy_deleted': True, 'retention_policy_id': policy.id}
            conversation.updated_at = now
            conversation.save(update_fields=[
                'external_contact_id', 'display_name', 'last_message_preview', 'status',
                'opted_out_at', 'opt_out_reason', 'metadata_json', 'updated_at',
            ])
            conversations_anonymized += 1

    return {
        'executed_at': now,
        'lead_pii_anonymized': len(lead_ids),
        'contacts_anonymized': contacts_updated,
        'private_attachments_removed': attachments_removed,
        'inbound_payloads_cleared': inbound_updated,
        'outbox_payloads_cleared': outbox_updated,
        'privacy_request_identities_cleared': requests_updated,
        'whatsapp_message_contents_cleared': whatsapp_messages_cleared,
        'whatsapp_media_files_removed': media_removed,
        'whatsapp_conversations_anonymized': conversations_anonymized,
        'retained_immutable_activities': preview['candidates']['retained_immutable_activities'],
    }
