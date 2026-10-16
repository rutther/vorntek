"""Persist SMTP claim/handoff facts separately from repairable lead summaries."""

from datetime import timedelta
import hashlib
import json
from email.utils import parseaddr
import re

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import Max
from django.utils import timezone

from leads.email_delivery import load_runtime_email_config
from leads.models import LeadNotificationAttempt, LeadSubmission


BLOCKING_STATES = ('claimed', 'handoff', 'unknown')
CLAIM_LEASE = timedelta(minutes=10)


def repair_notification_projection(attempt: LeadNotificationAttempt) -> None:
    with transaction.atomic():
        lead = LeadSubmission.objects.select_for_update().get(pk=attempt.submission_id)
        if LeadNotificationAttempt.objects.filter(submission=lead, kind=attempt.kind, generation__gt=attempt.generation).exists():
            return
        if attempt.state == 'accepted':
            if attempt.kind == 'initial':
                lead.notification_status = 'sent'
                lead.notified_at = attempt.accepted_at
                fields = ['notification_status', 'notified_at']
            else:
                if lead.reminder_sent_at and lead.reminder_sent_at >= attempt.accepted_at:
                    return
                lead.reminder_sent_at = attempt.accepted_at
                fields = ['reminder_sent_at']
            lead.notification_error = ''
        else:
            lead.notification_error = attempt.error
            fields = []
            if attempt.kind == 'initial' and attempt.state == 'failed':
                if lead.notification_status == 'sent':
                    return
                lead.notification_status = 'disabled' if attempt.policy_disabled else 'failed'
                fields.append('notification_status')
        lead.save(update_fields=fields + ['notification_error', 'updated_at'])


def _repair_safely(attempt):
    try:
        repair_notification_projection(attempt)
    except DatabaseError:
        # The attempt fact, including a positive receipt, survives projection
        # failure. The next sender repairs it instead of resending a message.
        pass


def claim_notification(submission_id: int, kind: str):
    if kind not in ('initial', 'reminder'):
        raise ValueError('Unknown notification kind')
    with transaction.atomic():
        lead = LeadSubmission.objects.select_for_update(of=('self',)).select_related('form').filter(pk=submission_id).first()
        if lead is None or lead.trashed_at is not None:
            return None
        if kind == 'initial' and lead.notification_status == 'sent':
            return None
        if kind == 'reminder' and lead.stage != 'new':
            return None
        accepted = LeadNotificationAttempt.objects.filter(submission=lead, kind=kind, state='accepted').first()
        cutoff = timezone.now() - timedelta(hours=settings.SITEOS_LEAD_REMINDER_REPEAT_HOURS)
        if accepted and (kind == 'initial' or accepted.accepted_at > cutoff):
            _repair_safely(accepted)
            return None
        if kind == 'reminder' and lead.reminder_sent_at and lead.reminder_sent_at > cutoff:
            return None
        active = LeadNotificationAttempt.objects.filter(submission=lead, state__in=BLOCKING_STATES).first()
        if active:
            if active.state != 'claimed' or active.created_at > timezone.now() - CLAIM_LEASE:
                return None
            # A lease can only be replaced before handoff; an expired UUID
            # cannot later transition because the conditional handoff checks it.
            LeadNotificationAttempt.objects.filter(pk=active.pk, state='claimed').update(
                state='cancelled', finished_at=timezone.now(), error='发送前认领已过期。')
        previous = LeadNotificationAttempt.objects.filter(submission=lead, kind=kind).aggregate(value=Max('generation'))['value'] or 0
        attempt = LeadNotificationAttempt.objects.create(submission=lead, kind=kind, generation=previous + 1, lead_version=lead.updated_at)
        return lead, attempt


def begin_notification_handoff(attempt_id, recipients, delivery, message_hash, message_id=''):
    identity = LeadNotificationAttempt.objects.only('submission_id').get(pk=attempt_id)
    with transaction.atomic():
        lead = LeadSubmission.objects.select_for_update(of=('self',)).select_related('form', 'site').get(pk=identity.submission_id)
        attempt = LeadNotificationAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.state != 'claimed':
            return None
        fresh_recipients = [item.strip() for item in (lead.form.notify_emails or '').replace(';', ',').replace('\n', ',').split(',') if item.strip()]
        eligible = (lead.trashed_at is None and lead.updated_at == attempt.lead_version
                    and lead.site.enabled and lead.form.status == 'active'
                    and fresh_recipients == recipients
                    and attempt.created_at > timezone.now() - CLAIM_LEASE
                    and (attempt.kind != 'reminder' or lead.stage == 'new')
                    and (attempt.kind != 'initial' or lead.notification_status != 'sent'))
        if eligible:
            fresh_delivery = load_runtime_email_config()
            eligible = fresh_delivery.ready and fresh_delivery == delivery
        if not eligible:
            attempt.state = 'cancelled'
            attempt.error = '发送前记录或通知配置已变化；本次未交接。'
            attempt.finished_at = timezone.now()
            attempt.save(update_fields=['state', 'error', 'finished_at'])
            return None
        attempt.state = 'handoff'
        attempt.handoff_at = timezone.now()
        attempt.message_hash = message_hash
        attempt.message_id = message_id
        attempt.save(update_fields=['state', 'handoff_at', 'message_hash', 'message_id'])
        return lead


def finish_notification_attempt(attempt_id, state: str, error: str = '', *, disabled: bool = False) -> bool:
    if state not in ('accepted', 'failed', 'unknown'):
        raise ValueError('Unknown notification result')
    try:
        with transaction.atomic():
            attempt = LeadNotificationAttempt.objects.select_for_update().get(pk=attempt_id)
            allowed = ('handoff',) if state in ('accepted', 'unknown') else ('claimed', 'handoff')
            if attempt.state not in allowed:
                return False
            attempt.state = state
            attempt.error = error[:700]
            attempt.policy_disabled = disabled and state == 'failed'
            attempt.finished_at = timezone.now()
            if state == 'accepted':
                attempt.accepted_at = attempt.finished_at
            attempt.save(update_fields=['state', 'error', 'policy_disabled', 'finished_at', 'accepted_at'])
    except DatabaseError:
        # Persistent handoff/claim blocks retries even if receipt persistence
        # fails. No DB rollback can retract an already attempted external send.
        return False
    _repair_safely(attempt)
    return True


def notification_message_hash(message) -> str:
    snapshot = {'subject': message.subject, 'body': message.body, 'to': message.to, 'from': message.from_email,
                'message_id': message.extra_headers.get('Message-ID', '')}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def notification_message_id(attempt_id, from_email):
    address = parseaddr(str(from_email))[1]
    domain = address.rsplit('@', 1)[-1] if '@' in address else ''
    try:
        domain = domain.encode('idna').decode('ascii').lower()
    except UnicodeError:
        domain = ''
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', domain):
        domain = 'notification.invalid'
    return f'<{attempt_id}@{domain}>'


def blocked_notification_ids():
    from django.db.models import Q

    return LeadNotificationAttempt.objects.filter(
        Q(state__in=('handoff', 'unknown'))
        | Q(state='claimed', created_at__gt=timezone.now() - CLAIM_LEASE)
    ).values('submission_id')
