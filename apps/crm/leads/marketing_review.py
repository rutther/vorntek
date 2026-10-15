"""Audited manual receipt reconciliation. Never invokes provider transport."""
from datetime import timedelta
import hashlib

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from console.access import MARKETING_ROLES
from console.commit_authorization import current_actor_for_commit
from console.models import AuditLog
from leads.models import LeadEventAttempt, LeadEventOutbox, LeadSubmission
from leads.marketing_delivery import _lease, _payload_hash, repair_event_projection

VERSION_SALT = 'siteos.marketing.receipt.review.v1'
REVIEW_WINDOW = timedelta(minutes=10)


def review_data(outbox, attempt):
    return {'id': outbox.pk, 'status': outbox.status, 'version': outbox.updated_at.isoformat(),
            'event_id': outbox.event_id, 'request_id': outbox.provider_request_id,
            'contract_hash': _payload_hash(outbox), 'attempts': outbox.attempts,
            'attempt_id': str(attempt.pk) if attempt else '',
            'generation': attempt.generation if attempt else 0,
            'state': attempt.state if attempt else '',
            'handoff_at': attempt.handoff_at.isoformat() if attempt and attempt.handoff_at else '',
            'finished_at': attempt.finished_at.isoformat() if attempt and attempt.finished_at else ''}


def marketing_review_version(outbox, attempt):
    return signing.dumps(review_data(outbox, attempt), salt=VERSION_SALT, compress=True)


def reviewable(outbox, attempt):
    if attempt:
        return attempt.state in ('unknown', 'handoff')
    return (outbox.status == 'unknown' or
            (outbox.status == 'sending' and outbox.updated_at <= timezone.now() - _lease()))


def reconcile_marketing_event(*, actor_id, session_key, outbox_id, site_id, version,
                              event_id, request_id, outcome, evidence, confirmed, stopped):
    if outcome not in ('accepted', 'not_accepted') or not confirmed or not stopped:
        raise ValidationError('请核对完整编号，并确认发送已经结束。', code='invalid_marketing_review')
    evidence, event_id, request_id = evidence.strip(), event_id.strip(), request_id.strip()
    if not 10 <= len(evidence) <= 500:
        raise ValidationError('请填写10至500字的核对依据，不要填写秘密或客户个人资料。', code='invalid_marketing_review')
    try:
        snapshot = signing.loads(version, salt=VERSION_SALT, max_age=3600)
    except signing.BadSignature as exc:
        raise ValidationError('核对页面已过期或记录已变化，请刷新后再次确认。', code='stale_marketing_review') from exc
    version_hash = hashlib.sha256(version.encode()).hexdigest()
    evidence_hash = hashlib.sha256(evidence.encode()).hexdigest()
    with transaction.atomic():
        actor = current_actor_for_commit(actor_id, session_key, MARKETING_ROLES)
        identity = LeadEventOutbox.objects.only('submission_id').get(pk=outbox_id,
            submission__site_id=site_id, integration__site_id=site_id)
        lead = LeadSubmission.objects.select_for_update().get(pk=identity.submission_id, site_id=site_id)
        outbox = LeadEventOutbox.objects.select_for_update(of=('self',)).select_related('integration__provider') \
            .get(pk=outbox_id, submission=lead, integration__site_id=site_id)
        latest = LeadEventAttempt.objects.select_for_update().filter(outbox=outbox).first()
        previous = AuditLog.objects.filter(action='marketing_attempt_reconciled',
            entity_table='lead_event_outbox', entity_id=outbox.pk, actor_user=actor,
            metadata_json__version_hash=version_hash, metadata_json__evidence_hash=evidence_hash,
            metadata_json__event_id=event_id, metadata_json__request_id=request_id,
            metadata_json__outcome=outcome).exists()
        if previous:
            return False
        if snapshot != review_data(outbox, latest) or not reviewable(outbox, latest):
            raise ValidationError('这条记录已被其他操作处理，请刷新后查看；本次输入已保留。', code='stale_marketing_review')
        if event_id != outbox.event_id:
            raise ValidationError('完整事件编号不一致，不能登记为这次发送。', code='invalid_marketing_review')
        provider = outbox.integration.provider.code
        if (provider, outbox.integration.integration_type) not in (('meta', 'pixel'), ('google', 'data_manager')):
            raise ValidationError('本接入不支持这项核对操作。', code='invalid_marketing_review')
        validation = (latest.delivery_mode == 'validation' if latest else
                      bool((outbox.payload_json or {}).get('validateOnly')))
        if outcome == 'accepted' and provider == 'google' and not validation and not request_id:
            raise ValidationError('Google正式上传必须核对完整requestId。', code='invalid_marketing_review')
        if request_id and outbox.provider_request_id and request_id != outbox.provider_request_id:
            raise ValidationError('平台请求编号不一致，不能改成另一条上传。', code='invalid_marketing_review')
        last_handoff = latest.handoff_at if latest else (outbox.last_attempt_at or outbox.updated_at)
        if outcome == 'not_accepted' and last_handoff and last_handoff > timezone.now() - max(REVIEW_WINDOW, _lease()):
            raise ValidationError('这次交接可能仍在处理，暂不能恢复重试。请核实发送结束并稍后重新核对。', code='marketing_still_inflight')
        before = review_data(outbox, latest)
        now = timezone.now()
        status = ('validated' if validation else 'processing' if provider == 'google' else 'sent') if outcome == 'accepted' else 'failed'
        error = '' if outcome == 'accepted' else '人工核对平台未接受，按原资格和重试规则等待执行。'
        receipt = {'manual_review': True, 'outcome': outcome}
        if outcome == 'accepted' and provider == 'google':
            receipt['requestId'] = request_id
            if validation:
                receipt['validationOnly'] = True
        elif outcome == 'accepted' and request_id:
            receipt['fbtrace_id'] = request_id
        if latest:
            latest.state = 'accepted' if outcome == 'accepted' else 'failed'
            latest.result_status, latest.error, latest.response_json = status, error, receipt
            latest.finished_at = now
            latest.accepted_at = now if outcome == 'accepted' else None
            latest.provider_request_id = request_id or outbox.provider_request_id
            latest.save(update_fields=['state', 'result_status', 'error', 'response_json', 'finished_at',
                                       'accepted_at', 'provider_request_id'])
            repair_event_projection(latest)
        else:
            # A legacy observation is a new audited decision, not a fabricated
            # historical handoff/attempt. Keep its original count and contract.
            outbox.status, outbox.last_error, outbox.response_json = status, error, receipt
            outbox.next_attempt_at = None
            fields = ['status', 'last_error', 'response_json', 'next_attempt_at', 'updated_at']
            if outcome == 'accepted':
                outbox.provider_received_at = now
                outbox.provider_request_id = request_id or outbox.provider_request_id
                outbox.match_status = 'not_available' if provider == 'google' else 'unknown'
                outbox.dispatched_at = now
                fields += ['provider_received_at', 'provider_request_id', 'match_status', 'dispatched_at']
                if status == 'processing':
                    outbox.next_attempt_at = now
                elif status == 'validated':
                    outbox.provider_processed_at = now
                    fields += ['provider_processed_at']
            else:
                delay = min(60, 2 ** min(max(outbox.attempts - 1, 0), 6))
                outbox.next_attempt_at = now + timedelta(minutes=delay)
            outbox.save(update_fields=fields)
        outbox.refresh_from_db()
        AuditLog.objects.create(actor=actor.get_username(), actor_user=actor,
            action='marketing_attempt_reconciled', entity_table='lead_event_outbox', entity_id=outbox.pk,
            source='marketing-review', before_json=before, after_json=review_data(outbox, latest),
            metadata_json={'site_id': site_id, 'attempt_id': str(latest.pk) if latest else '',
                'generation': latest.generation if latest else 0, 'version_hash': version_hash,
                'evidence_hash': evidence_hash, 'event_id': event_id, 'request_id': request_id,
                'outcome': outcome, 'evidence_reference': evidence, 'manual': True, 'confirmed_stopped': True})
        return True
