"""Permissioned manual SMTP receipt reconciliation, never an immediate send."""

import hashlib

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from console.access import ROLE_SYSTEM_ADMIN
from console.commit_authorization import current_actor_for_commit
from console.models import AuditLog
from leads.models import LeadNotificationAttempt, LeadSubmission
from leads.notification_attempts import CLAIM_LEASE, repair_notification_projection


VERSION_SALT = 'siteos.notification.review.v1'


def _version_data(attempt):
    return {'id': str(attempt.pk), 'generation': attempt.generation, 'state': attempt.state,
            'message_id': attempt.message_id,
            'handoff_at': attempt.handoff_at.isoformat() if attempt.handoff_at else '',
            'finished_at': attempt.finished_at.isoformat() if attempt.finished_at else ''}


def notification_review_version(attempt):
    return signing.dumps(_version_data(attempt), salt=VERSION_SALT, compress=True)


def _fresh_admin(actor_id, session_key):
    return current_actor_for_commit(actor_id, session_key, {ROLE_SYSTEM_ADMIN})


def reconcile_notification_attempt(*, actor_id, session_key, submission_id, site_id,
                                   attempt_id, version, outcome, evidence, confirmed, stopped):
    if outcome not in ('accepted', 'not_accepted') or not confirmed or not stopped:
        raise ValidationError('请核对结果并确认本次发送已结束。', code='invalid_notification_review')
    evidence = evidence.strip()
    if not 10 <= len(evidence) <= 500:
        raise ValidationError('请填写10至500字的核对依据，不要填写秘密或客户资料。', code='invalid_notification_review')
    try:
        snapshot = signing.loads(version, salt=VERSION_SALT, max_age=3600)
    except signing.BadSignature as exc:
        raise ValidationError('核对页面已过期或记录已变化，请刷新后再核对。', code='stale_notification_review') from exc
    version_hash = hashlib.sha256(version.encode()).hexdigest()
    evidence_hash = hashlib.sha256(evidence.encode()).hexdigest()
    with transaction.atomic():
        actor = _fresh_admin(actor_id, session_key)
        lead = LeadSubmission.objects.select_for_update().get(pk=submission_id, site_id=site_id)
        attempt = LeadNotificationAttempt.objects.select_for_update().get(pk=attempt_id, submission=lead)
        previous = AuditLog.objects.filter(
            action='notification_attempt_reconciled', entity_table='lead_submission', entity_id=lead.pk,
            actor_user=actor, metadata_json__attempt_id=str(attempt.pk), metadata_json__version_hash=version_hash,
            metadata_json__evidence_hash=evidence_hash, metadata_json__outcome=outcome,
        ).exists()
        if previous:
            return False
        if snapshot != _version_data(attempt) or attempt.state not in ('unknown', 'handoff'):
            raise ValidationError('记录已由其他操作处理，请刷新后查看最新结果。', code='stale_notification_review')
        if (outcome == 'not_accepted' and attempt.state == 'handoff'
                and attempt.handoff_at > timezone.now() - CLAIM_LEASE):
            raise ValidationError('这次交接可能仍在进行。暂不能恢复重试，请先确认发送已结束。', code='notification_still_inflight')
        before = _version_data(attempt)
        attempt.state = 'accepted' if outcome == 'accepted' else 'failed'
        attempt.finished_at = timezone.now()
        attempt.accepted_at = attempt.finished_at if outcome == 'accepted' else None
        attempt.error = '' if outcome == 'accepted' else '人工核对邮件平台未接受；按原资格等待重试。'
        attempt.policy_disabled = False
        attempt.save(update_fields=['state', 'finished_at', 'accepted_at', 'error', 'policy_disabled'])
        repair_notification_projection(attempt)
        AuditLog.objects.create(
            actor=actor.get_username(), actor_user=actor, action='notification_attempt_reconciled',
            entity_table='lead_submission', entity_id=lead.pk, source='notification-review',
            before_json=before, after_json=_version_data(attempt),
            metadata_json={'attempt_id': str(attempt.pk), 'site_id': lead.site_id, 'kind': attempt.kind,
                           'version_hash': version_hash, 'evidence_hash': evidence_hash, 'outcome': outcome,
                           'evidence_reference': evidence, 'manual': True, 'confirmed_stopped': True},
        )
        return True
