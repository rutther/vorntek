"""Durable Meta attempt/handoff/receipt; no network transaction or blind retry."""

from datetime import timedelta
import hashlib
import json
import urllib.error
import urllib.request

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import Exists, F, Max, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from leads.models import LeadEventAttempt, LeadEventOutbox, LeadEventResult, LeadSubmission
from leads.measurement_routing import integration_sendable
from marketing.models import MarketingIntegration

ACTIVE = ('claimed', 'handoff', 'unknown')


def _lease():
    return timedelta(minutes=max(int(getattr(settings, 'SITEOS_OUTBOX_CLAIM_TIMEOUT_MINUTES', 5)), 1))


def _payload_hash(outbox):
    envelope = {'payload': outbox.payload_json, 'submission_id': outbox.submission_id,
                'integration_id': outbox.integration_id, 'stage_key': outbox.stage_key,
                'event_name': outbox.event_name, 'event_id': outbox.event_id}
    return hashlib.sha256(json.dumps(envelope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def repair_event_projection(attempt):
    with transaction.atomic():
        identity = LeadEventOutbox.objects.only('submission_id').get(pk=attempt.outbox_id)
        LeadSubmission.objects.select_for_update().get(pk=identity.submission_id)
        outbox = LeadEventOutbox.objects.select_for_update().get(pk=attempt.outbox_id)
        if LeadEventAttempt.objects.filter(outbox=outbox, generation__gt=attempt.generation).exists():
            return
        if not attempt.result_status:
            return
        if attempt.state == 'accepted' and attempt.result_status == 'processing':
            from leads.google_data_manager import apply_google_result
            result = LeadEventResult.objects.filter(attempt=attempt).first()
            if result:
                outbox.attempts = max(outbox.attempts, attempt.attempt_number)
                outbox.provider_request_id = attempt.provider_request_id
                outbox.provider_received_at = attempt.accepted_at
                outbox.delivery_mode = attempt.delivery_mode
                fields = apply_google_result(outbox, result)
                fields += ['attempts', 'provider_request_id', 'provider_received_at', 'delivery_mode']
                outbox.save(update_fields=fields)
                return
            if (outbox.status == 'processing' and outbox.provider_request_id == attempt.provider_request_id
                    and outbox.attempts == attempt.attempt_number):
                return  # Keep newer poll evidence and its next read time.
        outbox.status = attempt.result_status
        outbox.last_error = attempt.error
        outbox.delivery_mode = attempt.delivery_mode
        fields = ['status', 'last_error', 'delivery_mode', 'updated_at']
        if attempt.state != 'cancelled':
            outbox.attempts = max(outbox.attempts, attempt.attempt_number)
            outbox.last_attempt_at = attempt.handoff_at or attempt.finished_at
            fields += ['attempts', 'last_attempt_at']
        outbox.next_attempt_at = None
        if attempt.state == 'failed':
            delay = min(60, 2 ** min(max(attempt.attempt_number - 1, 0), 6))
            outbox.next_attempt_at = attempt.finished_at + timedelta(minutes=delay)
        fields += ['next_attempt_at']
        if attempt.state == 'accepted':
            outbox.response_json = attempt.response_json
            outbox.provider_request_id = attempt.provider_request_id
            outbox.provider_received_at = attempt.accepted_at
            outbox.dispatched_at = attempt.accepted_at
            outbox.match_status = 'not_available' if attempt.result_status in ('processing', 'validated') else 'unknown'
            if attempt.result_status == 'processing':
                outbox.next_attempt_at = attempt.accepted_at
            elif attempt.result_status == 'validated':
                outbox.provider_processed_at = attempt.accepted_at
                fields += ['provider_processed_at']
            fields += ['response_json', 'provider_request_id', 'provider_received_at', 'dispatched_at', 'match_status']
        elif attempt.response_json or attempt.state == 'failed':
            outbox.response_json = attempt.response_json
            fields += ['response_json']
        if attempt.state == 'failed' and outbox.integration.provider.code == 'google':
            # Failed preparation/rejection belongs to this attempt, not the
            # preceding accepted request. Its old facts remain in their tables.
            outbox.provider_request_id = ''
            outbox.provider_received_at = None
            outbox.provider_processed_at = None
            outbox.response_json = attempt.response_json
            fields += ['provider_request_id', 'provider_received_at', 'provider_processed_at', 'response_json']
        outbox.save(update_fields=fields)


def _repair_safely(attempt):
    try:
        repair_event_projection(attempt)
    except DatabaseError:
        # Positive receipt remains durable even if the legacy summary fails.
        pass


def claim_meta_event(outbox_id, *, legacy_claimed_at=None, google=False):
    from leads.services import integration_delivery_mode
    identity = LeadEventOutbox.objects.only('submission_id').get(pk=outbox_id)
    with transaction.atomic():
        lead = LeadSubmission.objects.select_for_update(of=('self',)).select_related('form', 'site').get(pk=identity.submission_id)
        outbox = LeadEventOutbox.objects.select_for_update(of=('self',)).select_related('integration__provider').get(pk=outbox_id)
        if lead.trashed_at is not None or outbox.submission_id != lead.pk:
            # Old manual/claimed compatibility entry may already have set sending.
            if legacy_claimed_at and outbox.status == 'sending' and outbox.updated_at == legacy_claimed_at:
                outbox.status = 'pending'
                outbox.save(update_fields=['status', 'updated_at'])
            return None
        latest = LeadEventAttempt.objects.filter(outbox=outbox).first()
        accepted = latest if google and latest and latest.state == 'accepted' else None
        if not google:
            accepted = LeadEventAttempt.objects.filter(outbox=outbox, state='accepted').first()
        if accepted:
            known_failure = google and LeadEventResult.objects.filter(attempt=accepted, status='failed').exists()
            if not known_failure:
                _repair_safely(accepted)
                return None
        active = LeadEventAttempt.objects.filter(outbox=outbox, state__in=ACTIVE).first()
        if active:
            if active.state != 'claimed' or active.created_at > timezone.now() - _lease():
                return None
            active.state = 'cancelled'
            active.result_status = 'pending'
            active.finished_at = timezone.now()
            active.error = '发送前认领过期，本次没有交接。'
            active.save(update_fields=['state', 'result_status', 'finished_at', 'error'])
            outbox.status = 'pending'
        if outbox.status not in ('pending', 'failed'):
            if not (legacy_claimed_at and outbox.status == 'sending' and outbox.updated_at == legacy_claimed_at):
                return None
        generation = LeadEventAttempt.objects.filter(outbox=outbox).aggregate(n=Max('generation'))['n'] or 0
        attempt = LeadEventAttempt.objects.create(outbox=outbox, generation=generation + 1, created_at=timezone.now(),
            attempt_number=outbox.attempts + 1, lead_version=lead.updated_at,
            integration_version=outbox.integration.updated_at, event_id=outbox.event_id,
            public_id=outbox.integration.public_id, payload_hash=_payload_hash(outbox),
            delivery_mode=integration_delivery_mode(outbox.integration))
        outbox.status = 'sending'
        outbox.save(update_fields=['status', 'updated_at'])
        return outbox, attempt


def _cancel(attempt, status, error):
    attempt.state = 'cancelled'
    attempt.result_status = status
    attempt.error = error
    attempt.finished_at = timezone.now()
    attempt.save(update_fields=['state', 'result_status', 'error', 'finished_at'])


def begin_meta_handoff(attempt_id, *, google=False):
    identity = LeadEventAttempt.objects.select_related('outbox').get(pk=attempt_id)
    with transaction.atomic():
        lead = LeadSubmission.objects.select_for_update(of=('self',)).select_related('form', 'site').get(pk=identity.outbox.submission_id)
        outbox = LeadEventOutbox.objects.select_for_update().get(pk=identity.outbox_id)
        integration = MarketingIntegration.objects.select_for_update().get(pk=outbox.integration_id)
        attempt = LeadEventAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.state != 'claimed':
            return False
        if lead.trashed_at is not None:
            _cancel(attempt, 'pending', '线索在回收站，本次未交接；恢复后按原资格继续。')
        elif not lead.site.enabled or not (_google_sendable(lead, integration, outbox.stage_key) if google
                                          else integration_sendable(lead, integration, outbox.stage_key)):
            _cancel(attempt, 'skipped', '当前同意、接入或原始路由已不允许发送，本次未交接。')
        elif (lead.updated_at != attempt.lead_version or integration.updated_at != attempt.integration_version
              or outbox.event_id != attempt.event_id or integration.public_id != attempt.public_id
              or _payload_hash(outbox) != attempt.payload_hash or outbox.status != 'sending'
              or attempt.created_at <= timezone.now() - _lease()):
            _cancel(attempt, 'pending', '发送前记录或配置已变化，本次未交接。')
        else:
            attempt.state = 'handoff'
            attempt.handoff_at = timezone.now()
            attempt.save(update_fields=['state', 'handoff_at'])
            return True
    _repair_safely(attempt)
    return False


def _google_sendable(lead, integration, stage_key):
    # Google has no Pixel runtime route. The stored outbox remains the original
    # target; consent/lifecycle and current enablement are freshly checked.
    return (integration.enabled and integration.site_id == lead.site_id
            and integration.provider.code == 'google' and integration.integration_type == 'data_manager'
            and stage_key in ('qualified', 'won') and lead.stage != 'spam'
            and bool((lead.consent_json or {}).get('marketing'))
            and not (lead.config_json or {}).get('archived')
            and not (lead.config_json or {}).get('privacy_deleted'))


def finish_meta_attempt(attempt_id, state, *, error='', response=None, google=False, validation=False):
    if state not in ('accepted', 'failed', 'unknown'):
        raise ValueError('Unknown marketing result')
    try:
        with transaction.atomic():
            attempt = LeadEventAttempt.objects.select_for_update().get(pk=attempt_id)
            allowed = ('handoff', 'unknown') if state == 'accepted' else ('claimed', 'handoff') if state == 'failed' else ('handoff',)
            if attempt.state not in allowed:
                return False
            attempt.state = state
            accepted_status = ('validated' if validation else 'processing') if google else 'sent'
            attempt.result_status = {'accepted': accepted_status, 'failed': 'failed', 'unknown': 'unknown'}[state]
            attempt.error = error[:1000]
            attempt.response_json = response or {}
            attempt.finished_at = timezone.now()
            if state == 'accepted':
                attempt.accepted_at = attempt.finished_at
                attempt.provider_request_id = str(attempt.response_json.get('requestId' if google else 'fbtrace_id') or '').strip()
            attempt.save(update_fields=['state', 'result_status', 'error', 'response_json', 'finished_at', 'accepted_at', 'provider_request_id'])
    except DatabaseError:
        # Persistent handoff blocks retries after externally attempted delivery.
        return False
    _repair_safely(attempt)
    return state == 'accepted'


def dispatch_meta_now(outbox, *, claimed=False, legacy_claimed_at=None):
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return False
    from leads import services
    claim_time = legacy_claimed_at if legacy_claimed_at is not None else outbox.updated_at if claimed else None
    result = claim_meta_event(outbox.pk, legacy_claimed_at=claim_time)
    if result is None:
        outbox.refresh_from_db()
        return False
    current, attempt = result
    try:
        token = services.resolve_meta_access_token(current.integration)
        if not token:
            finish_meta_attempt(attempt.pk, 'failed', error='Meta access token 未配置。')
            outbox.refresh_from_db()
            return False
        payload = {'data': [current.payload_json], 'partner_agent': 'siteos-leads-v1'}
        test_code = str((current.integration.config_json or {}).get('test_event_code') or '').strip()
        if test_code:
            payload['test_event_code'] = test_code
        request = urllib.request.Request(
            f'https://graph.facebook.com/{services.meta_api_version(current.integration)}/{current.integration.public_id}/events',
            data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}, method='POST')
    except Exception as exc:
        finish_meta_attempt(attempt.pk, 'failed', error=f'发送前准备失败：{type(exc).__name__}')
        outbox.refresh_from_db()
        return False
    if not begin_meta_handoff(attempt.pk):
        outbox.refresh_from_db()
        return False
    try:
        with services.urllib.request.urlopen(request, timeout=20) as response:
            parsed = json.loads(response.read().decode('utf-8', errors='replace') or '{}')
    except urllib.error.HTTPError as exc:
        # Explicit auth/request rejection is a known non-acceptance. Server
        # errors/timeouts remain ambiguous, never an automatic resend proof.
        known = exc.code in (400, 401, 403, 404, 405, 413, 415, 422, 429)
        success = finish_meta_attempt(attempt.pk, 'failed' if known else 'unknown',
            error=f'Meta HTTP {exc.code}；' + ('平台拒绝本次请求。' if known else '接收结果待核对。'),
            response={'http_status': exc.code})
    except Exception as exc:
        success = finish_meta_attempt(attempt.pk, 'unknown', error=f'Meta交接结果待核对：{type(exc).__name__}')
    else:
        count = parsed.get('events_received') if isinstance(parsed, dict) else None
        if isinstance(count, int) and not isinstance(count, bool) and count >= 1:
            success = finish_meta_attempt(attempt.pk, 'accepted', response=parsed)
        elif isinstance(parsed, dict) and parsed.get('error'):
            success = finish_meta_attempt(attempt.pk, 'failed', error='Meta CAPI明确返回错误响应。', response=parsed)
        else:
            success = finish_meta_attempt(attempt.pk, 'unknown', error='Meta CAPI未确认接收，本次结果待核对。', response=parsed if isinstance(parsed, dict) else {})
    outbox.refresh_from_db()
    return success


def dispatch_meta(outbox, *, claimed=False):
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return False
    claim_time = outbox.updated_at if claimed else None
    if connection.in_atomic_block:
        pk = outbox.pk
        # Claim only after the outer transaction really commits. It is then a
        # fresh normal pending/failed event, not an old caller-owned snapshot.
        transaction.on_commit(lambda: dispatch_meta_now(LeadEventOutbox.objects.get(pk=pk),
            legacy_claimed_at=claim_time), robust=True)
        return False
    return dispatch_meta_now(outbox, legacy_claimed_at=claim_time)


def meta_entry_eligible(outbox, *, claimed=False):
    """Fresh no-transport gate also used before deferring an outer transaction."""
    original_claimed_at = outbox.updated_at
    identity = LeadEventOutbox.objects.only('submission_id').get(pk=outbox.pk)
    with transaction.atomic():
        lead = LeadSubmission.objects.select_for_update(of=('self',)).select_related('form', 'site').get(pk=identity.submission_id)
        current = LeadEventOutbox.objects.select_for_update(of=('self',)).select_related('integration__provider').get(pk=outbox.pk)
        has_handoff = LeadEventAttempt.objects.filter(outbox=current, state__in=('handoff', 'unknown', 'accepted')).exists()
        if has_handoff or current.status not in ('pending', 'failed', 'sending'):
            return True  # Durable claim/receipt path must repair, not preflight overwrite.
        allowed = lead.trashed_at is None and lead.site.enabled and integration_sendable(lead, current.integration, current.stage_key)
        if not allowed:
            if current.status == 'sending' and (not claimed or current.updated_at != original_claimed_at):
                return False
            current.status = 'pending' if lead.trashed_at is not None else 'skipped'
            current.last_error = '线索在回收站，本次未交接。' if lead.trashed_at is not None else '当前同意、接入或原始路由已不允许发送。'
            current.next_attempt_at = None
            current.save(update_fields=['status', 'last_error', 'next_attempt_at', 'updated_at'])
            outbox.refresh_from_db()
            return False
    return True


def recover_legacy_meta_handoffs(now):
    from leads.google_data_manager import repair_google_result
    newer = LeadEventAttempt.objects.filter(outbox_id=OuterRef('outbox_id'), generation__gt=OuterRef('generation'))
    # Filter obsolete requests before applying the finite work window. A final
    # fact for an earlier upload cannot repair the current request projection.
    results = LeadEventResult.objects.filter(outbox__status='processing',
        outbox__attempts=F('attempt_number'), outbox__provider_request_id=F('provider_request_id')) \
        .annotate(has_later=Exists(newer)).filter(has_later=False).order_by('completed_at', 'id')
    for result in results[:1000]:
        try:
            repair_google_result(result)
        except DatabaseError:
            pass
    final_status = LeadEventResult.objects.filter(attempt_id=OuterRef('pk')).values('status')[:1]
    receipts = LeadEventAttempt.objects.filter(state__in=('accepted', 'failed', 'unknown', 'cancelled')) \
        .annotate(has_later=Exists(newer), projection_status=Coalesce(Subquery(final_status), F('result_status'))) \
        .filter(has_later=False).exclude(projection_status='') \
        .exclude(outbox__status=F('projection_status')).order_by('finished_at', 'id')
    for attempt in receipts[:1000]:
        _repair_safely(attempt)
    stale_claims = list(LeadEventAttempt.objects.filter(state='claimed', created_at__lte=now - _lease())
                        .values_list('outbox_id', flat=True)[:1000])
    for outbox_id in stale_claims:
        identity = LeadEventOutbox.objects.only('submission_id').get(pk=outbox_id)
        with transaction.atomic():
            LeadSubmission.objects.select_for_update().get(pk=identity.submission_id)
            outbox = LeadEventOutbox.objects.select_for_update().get(pk=outbox_id)
            attempt = LeadEventAttempt.objects.select_for_update().filter(outbox=outbox,
                state='claimed', created_at__lte=now - _lease()).first()
            if attempt:
                _cancel(attempt, 'pending', '发送前认领过期，本次未交接。')
                outbox.status = 'pending'
                outbox.next_attempt_at = None
                outbox.save(update_fields=['status', 'next_attempt_at', 'updated_at'])
    # A legacy sending row has no durable boundary: it may have reached Meta.
    # Quarantine rather than inventing a safe retry after the upgrade.
    LeadEventOutbox.objects.filter(status='sending', updated_at__lte=now - _lease(),
        delivery_attempts__isnull=True).filter(
            models_provider_filter()).update(status='unknown', next_attempt_at=None,
            last_error='旧版发送结果未确认，需核对原事件后再处理。', updated_at=now)


def models_provider_filter():
    from django.db.models import Q
    return (Q(integration__provider__code='meta', integration__integration_type='pixel')
            | Q(integration__provider__code='google', integration__integration_type='data_manager'))
