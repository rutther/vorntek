from __future__ import annotations

import hashlib
import hmac
import json
import os
import base64
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone as datetime_timezone
from typing import Any, Callable, Iterable

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from console.secret_store import load_secret
from leads.models import (
    ConsentRecord,
    LeadConversion,
    LeadFormDefinition,
    LeadInboundEvent,
    LeadSubmission,
    WhatsAppConversation,
    WhatsAppDeliveryEvent,
    WhatsAppMedia,
    WhatsAppMessage,
)
from leads.notifications import send_submission_notification
from leads.services import (
    canonicalize_email,
    canonicalize_phone,
    meta_api_version,
    queue_submission_event,
    record_initial_consent_snapshot,
    sales_team_for_form,
    sha256_text,
)
from marketing.models import MarketingIntegration


MAX_WEBHOOK_BYTES = 2_000_000
DEFAULT_INBOUND_CLAIM_TIMEOUT_MINUTES = 15
INBOUND_CLAIM_TIMEOUT_ERROR = '入站处理认领超时，已重新进入安全重试队列。'
WHATSAPP_CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)
WHATSAPP_MESSAGE_TYPES = frozenset({
    'text', 'image', 'video', 'audio', 'document', 'sticker', 'button',
    'interactive', 'location', 'contacts', 'system',
})
WHATSAPP_DELIVERY_STATUSES = frozenset({'sent', 'delivered', 'read', 'failed', 'deleted', 'warning'})
WHATSAPP_DELIVERY_RANK = {'sent': 1, 'delivered': 2, 'read': 3}
WHATSAPP_OPT_OUT_KEYWORDS = frozenset({
    'STOP', 'STOPALL', 'UNSUBSCRIBE', 'CANCEL', 'END', 'QUIT', '停止', '退订',
})


class InboundEventError(ValueError):
    pass


class InboundReceiptRetryConflict(ValueError):
    """The durable receipt cannot be safely claimed for an exact retry."""

    pass


def inbound_claim_timeout_minutes() -> int:
    """Return the one timeout used by the worker, UI and exact retry action."""

    return max(
        int(
            getattr(
                settings,
                'SITEOS_INBOUND_CLAIM_TIMEOUT_MINUTES',
                DEFAULT_INBOUND_CLAIM_TIMEOUT_MINUTES,
            )
        ),
        1,
    )


def inbound_claim_stale_before(*, now=None):
    effective_now = now or timezone.now()
    return effective_now - timedelta(minutes=inbound_claim_timeout_minutes())


def inbound_processing_claim_is_stale(receipt: LeadInboundEvent, *, now=None) -> bool:
    if receipt.status != 'processing' or receipt.updated_at is None:
        return False
    return receipt.updated_at <= inbound_claim_stale_before(now=now)


@transaction.atomic
def recover_stale_inbound_claims(*, now=None, queryset=None) -> int:
    """Atomically return abandoned processing claims to the failed queue.

    A fresh ``processing`` row remains owned by its current worker.  Only rows
    older than the shared claim timeout become retry candidates again.
    """

    effective_now = now or timezone.now()
    candidates = queryset if queryset is not None else LeadInboundEvent.objects.all()
    return candidates.filter(
        status='processing',
        updated_at__lte=inbound_claim_stale_before(now=effective_now),
    ).update(
        status='failed',
        last_error=INBOUND_CLAIM_TIMEOUT_ERROR,
        updated_at=effective_now,
    )


def resolve_secret_reference(reference: str | None) -> str:
    ref = str(reference or '').strip()
    if not ref:
        return ''
    if ref.startswith('vault:'):
        return load_secret(ref.removeprefix('vault:'))
    return os.getenv(ref, '').strip()


def integration_secret(integration: MarketingIntegration, key: str) -> str:
    config = dict(integration.config_json or {})
    if key == 'primary':
        return resolve_secret_reference(integration.secret_ref)
    configured_ref = str(config.get(f'{key}_ref') or '').strip()
    if configured_ref:
        return resolve_secret_reference(configured_ref)
    return load_secret(f'marketing_integration:{integration.id}:{key}')


def verify_sha256_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    supplied = str(signature_header or '').strip()
    if not supplied.startswith('sha256=') or not app_secret:
        return False
    expected = hmac.new(app_secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied.removeprefix('sha256='), expected)


def webhook_verify_challenge(*, mode: str, token: str, challenge: str, integrations: Iterable[MarketingIntegration]) -> str | None:
    if mode != 'subscribe' or not token or not challenge:
        return None
    for integration in integrations:
        expected = integration_secret(integration, 'webhook_verify_token')
        if expected and hmac.compare_digest(token, expected):
            return challenge
    return None


def parse_json_body(raw_body: bytes) -> dict[str, Any]:
    if not raw_body or len(raw_body) > MAX_WEBHOOK_BYTES:
        raise InboundEventError('Webhook 请求体为空或超过 2 MB。')
    try:
        parsed = json.loads(raw_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InboundEventError('Webhook JSON 无法解析。') from exc
    if not isinstance(parsed, dict):
        raise InboundEventError('Webhook JSON 必须是对象。')
    return parsed


def meta_leadgen_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get('object') != 'page':
        return []
    events: list[dict[str, Any]] = []
    for entry in payload.get('entry') or []:
        if not isinstance(entry, dict):
            continue
        page_id = str(entry.get('id') or '').strip()
        for change in entry.get('changes') or []:
            if not isinstance(change, dict) or change.get('field') != 'leadgen':
                continue
            value = change.get('value') if isinstance(change.get('value'), dict) else {}
            leadgen_id = str(value.get('leadgen_id') or value.get('id') or '').strip()
            if not page_id or not leadgen_id:
                continue
            events.append({'page_id': page_id, 'leadgen_id': leadgen_id, 'value': value, 'entry': entry})
    return events


def configured_meta_leadgen_form_ids(integration: MarketingIntegration) -> tuple[str, ...]:
    """Return the explicit Instant Form allow-list for one Meta integration."""

    raw = (integration.config_json or {}).get('leadgen_form_ids')
    if isinstance(raw, str):
        values = re.split(r'[\s,;]+', raw)
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = ()
    normalized: list[str] = []
    for value in values:
        form_id = str(value or '').strip()
        if form_id and form_id not in normalized:
            normalized.append(form_id)
    return tuple(normalized)


def meta_leadgen_event_form_id(event: dict[str, Any]) -> str:
    value = event.get('value') if isinstance(event.get('value'), dict) else {}
    return str(event.get('form_id') or value.get('form_id') or '').strip()


def meta_leadgen_event_is_allowed(
    integration: MarketingIntegration,
    event: dict[str, Any],
) -> bool:
    form_id = meta_leadgen_event_form_id(event)
    return bool(form_id and form_id in configured_meta_leadgen_form_ids(integration))


def whatsapp_webhook_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get('object') != 'whatsapp_business_account':
        return []
    events: list[dict[str, Any]] = []
    for entry in payload.get('entry') or []:
        if not isinstance(entry, dict):
            continue
        waba_id = str(entry.get('id') or '').strip()
        for change in entry.get('changes') or []:
            if not isinstance(change, dict):
                continue
            field = str(change.get('field') or '').strip()
            value = change.get('value') if isinstance(change.get('value'), dict) else {}
            metadata = value.get('metadata') if isinstance(value.get('metadata'), dict) else {}
            phone_number_id = str(metadata.get('phone_number_id') or '').strip()
            if field in {'history', 'smb_app_state_sync'}:
                if not phone_number_id:
                    continue
                fingerprint_material = {
                    'field': field,
                    'waba_id': waba_id,
                    'phone_number_id': phone_number_id,
                    'value': value,
                }
                event_fingerprint = hashlib.sha256(
                    json.dumps(
                        fingerprint_material,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(',', ':'),
                    ).encode('utf-8')
                ).hexdigest()
                events.append(
                    {
                        'event_type': 'history' if field == 'history' else 'contact_sync',
                        'waba_id': waba_id,
                        'phone_number_id': phone_number_id,
                        'event_fingerprint': event_fingerprint,
                        'value': value,
                    }
                )
                continue
            if field == 'smb_message_echoes':
                for message in value.get('message_echoes') or []:
                    if not isinstance(message, dict):
                        continue
                    message_id = str(message.get('id') or '').strip()
                    wa_id = str(message.get('to') or '').strip()
                    if not phone_number_id or not message_id or not wa_id:
                        continue
                    events.append(
                        {
                            'event_type': 'message_echo',
                            'waba_id': waba_id,
                            'phone_number_id': phone_number_id,
                            'message_id': message_id,
                            'wa_id': wa_id,
                            'contact': {},
                            'message': message,
                            'value': value,
                        }
                    )
                continue
            if field != 'messages':
                continue
            contacts = value.get('contacts') if isinstance(value.get('contacts'), list) else []
            contacts_by_wa_id = {
                str(item.get('wa_id') or ''): item
                for item in contacts
                if isinstance(item, dict) and item.get('wa_id')
            }
            for message in value.get('messages') or []:
                if not isinstance(message, dict):
                    continue
                message_id = str(message.get('id') or '').strip()
                wa_id = str(message.get('from') or '').strip()
                if not phone_number_id or not message_id:
                    continue
                events.append(
                    {
                        'event_type': 'message',
                        'waba_id': waba_id,
                        'phone_number_id': phone_number_id,
                        'message_id': message_id,
                        'wa_id': wa_id,
                        'contact': contacts_by_wa_id.get(wa_id, {}),
                        'message': message,
                        'value': value,
                    }
                )
            for status in value.get('statuses') or []:
                if not isinstance(status, dict):
                    continue
                message_id = str(status.get('id') or '').strip()
                delivery_status = str(status.get('status') or '').strip().lower()
                if not phone_number_id or not message_id or delivery_status not in WHATSAPP_DELIVERY_STATUSES:
                    continue
                fingerprint_material = {
                    'message_id': message_id,
                    'status': delivery_status,
                    'timestamp': str(status.get('timestamp') or ''),
                    'recipient_id': str(status.get('recipient_id') or ''),
                    'conversation': status.get('conversation') if isinstance(status.get('conversation'), dict) else {},
                    'errors': status.get('errors') if isinstance(status.get('errors'), list) else [],
                }
                event_fingerprint = hashlib.sha256(
                    json.dumps(
                        fingerprint_material,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(',', ':'),
                    ).encode('utf-8')
                ).hexdigest()
                events.append(
                    {
                        'event_type': 'status',
                        'waba_id': waba_id,
                        'phone_number_id': phone_number_id,
                        'message_id': message_id,
                        'delivery_status': delivery_status,
                        'event_fingerprint': event_fingerprint,
                        'status': status,
                        'value': value,
                    }
                )
    return events


def whatsapp_message_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility helper returning only customer-message events."""

    return [
        event for event in whatsapp_webhook_events(payload)
        if event['event_type'] == 'message'
    ]


def _bounded(value: Any, limit: int) -> str:
    return str(value or '').strip()[:limit]


def _timestamp(value: Any, *, fallback=None):
    if isinstance(value, datetime):
        parsed = value
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, datetime_timezone.utc)
        return parsed
    try:
        return datetime.fromtimestamp(int(value), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OSError):
        parsed = parse_datetime(str(value or '').strip())
        if parsed is not None:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, datetime_timezone.utc)
            return parsed
        return fallback or timezone.now()


def _form_for_integration(integration: MarketingIntegration) -> LeadFormDefinition:
    if not integration.site_id:
        raise InboundEventError('营销接入尚未绑定站点，不能创建外部线索。')
    config = dict(integration.config_json or {})
    form_code = str(config.get('form_code') or 'project-inquiry').strip()
    queryset = LeadFormDefinition.objects.select_related('site', 'locale').filter(
        site_id=integration.site_id,
        code=form_code,
        status='active',
    )
    form = queryset.order_by('id').first()
    if not form:
        raise InboundEventError(f'未找到启用的 CRM 表单定义：{form_code}')
    return form


def _consent_for_integration(integration: MarketingIntegration) -> dict[str, Any]:
    config = dict(integration.config_json or {})
    contact = bool(config.get('contact_consent_confirmed'))
    marketing = bool(config.get('marketing_consent_confirmed'))
    return {
        'contact': contact,
        'privacy_notice': contact,
        'marketing': marketing,
        'source_contract': integration.integration_type,
    }


def _acquire_external_submission_lock(dedupe_key: str) -> None:
    """Serialize one provider event without making all lead dedupe keys unique."""

    if connection.vendor != 'postgresql':
        return
    lock_id = int(dedupe_key[:16], 16)
    if lock_id >= 2**63:
        lock_id -= 2**64
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])


def _send_external_submission_notification_if_pending(submission_id: int) -> None:
    submission = LeadSubmission.objects.filter(pk=submission_id).first()
    if submission is not None and submission.notification_status == 'pending':
        send_submission_notification(submission)


@transaction.atomic
def _create_external_submission(
    *,
    integration: MarketingIntegration,
    external_event_id: str,
    submitted_at,
    full_name: str = '',
    email: str = '',
    phone: str = '',
    company: str = '',
    country: str = '',
    message: str = '',
    source_channel: str,
    source_detail: str,
    source_url: str = '',
    payload_json: dict[str, Any] | None = None,
    identifiers_json: dict[str, Any] | None = None,
) -> LeadSubmission:
    form = _form_for_integration(integration)
    normalized_email = canonicalize_email(email)
    normalized_phone = canonicalize_phone(phone)
    identifiers = dict(identifiers_json or {})
    identifiers.setdefault('external_id', external_event_id)
    contact_material = normalized_email or normalized_phone or str(identifiers.get('whatsapp_id') or external_event_id)
    dedupe_key = sha256_text(
        f'{integration.provider.code}|{integration.integration_type}|{external_event_id}'
    )
    _acquire_external_submission_lock(dedupe_key)
    existing = (
        LeadSubmission.objects.select_for_update()
        .select_related('form')
        .filter(site=form.site, dedupe_key=dedupe_key)
        .order_by('-submitted_at', '-id')
        .first()
    )
    if existing is not None:
        return existing

    now = timezone.now()
    submission = LeadSubmission.objects.create(
        form=form,
        site=form.site,
        locale=form.locale,
        team=sales_team_for_form(form),
        stage='new',
        full_name=_bounded(full_name, 120),
        email=_bounded(normalized_email, 254),
        phone=_bounded(normalized_phone, 40),
        company=_bounded(company, 200),
        country=_bounded(country, 120),
        message=_bounded(message, 4000),
        source_url=_bounded(source_url, 2000),
        referrer_url='',
        source_channel=source_channel,
        source_detail=_bounded(source_detail, 500),
        client_ip='',
        user_agent='',
        payload_json=payload_json or {},
        identifiers_json=identifiers,
        utm_json={},
        consent_json=_consent_for_integration(integration),
        config_json={'inbound_integration_id': integration.id},
        contact_key=sha256_text(f'{form.site_id}|{contact_material}'),
        dedupe_key=dedupe_key,
        notification_status='pending',
        submitted_at=submitted_at or now,
        stage_updated_at=submitted_at or now,
    )
    record_initial_consent_snapshot(
        submission,
        source=f'{integration.provider.code}_{integration.integration_type}',
    )
    return submission


def _queue_initial_crm_event(
    submission: LeadSubmission,
    integration: MarketingIntegration,
) -> int:
    config = dict(integration.config_json or {})
    if integration.integration_type != 'leadgen':
        return 0
    if not bool(config.get('queue_initial_crm_event', True)):
        return 0
    if not submission.form.capi_enabled or not bool((submission.consent_json or {}).get('marketing')):
        return 0
    event_name = str(config.get('initial_crm_event_name') or 'initial_lead').strip() or 'initial_lead'
    queued = queue_submission_event(
        submission,
        stage_key='submitted',
        event_name=event_name,
        include_external_meta=True,
    )
    return len(queued)


def _configured_waba_id(integration: MarketingIntegration, payload: dict[str, Any]) -> str:
    return str(
        payload.get('waba_id')
        or (integration.config_json or {}).get('waba_id')
        or ''
    ).strip()


def _configured_meta_page_id(integration: MarketingIntegration, payload: dict[str, Any]) -> str:
    return str(
        payload.get('page_id')
        or (integration.config_json or {}).get('page_id')
        or ''
    ).strip()


def _configured_ctwa_identity_mode(integration: MarketingIntegration) -> str:
    mode = str((integration.config_json or {}).get('ctwa_identity_mode') or 'waba').strip().lower()
    return mode if mode in {'waba', 'page'} else 'waba'


def _field_data_map(lead_data: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in lead_data.get('field_data') or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip().lower()
        values = item.get('values') if isinstance(item.get('values'), list) else []
        if name:
            result[name] = ', '.join(str(value).strip() for value in values if str(value).strip())
    return result


def _pick(fields: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(fields.get(name) or '').strip()
        if value:
            return value
    return ''


def fetch_meta_lead(integration: MarketingIntegration, leadgen_id: str) -> dict[str, Any]:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        raise InboundEventError('External I/O is disabled for this environment.')
    token = integration_secret(integration, 'primary')
    if not token:
        raise InboundEventError('Meta Page access token 未配置。')
    api_version = meta_api_version(integration)
    fields = 'id,created_time,ad_id,adset_id,campaign_id,form_id,field_data'
    query = urllib.parse.urlencode({'fields': fields})
    endpoint = f'https://graph.facebook.com/{api_version}/{urllib.parse.quote(leadgen_id)}?{query}'
    request = urllib.request.Request(
        endpoint,
        headers={
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        raise InboundEventError(f'Meta leadgen 读取失败，HTTP {exc.code}。') from exc
    except Exception as exc:
        raise InboundEventError(f'Meta leadgen 读取失败（{type(exc).__name__}）。') from exc
    try:
        parsed = json.loads(raw or '{}')
    except json.JSONDecodeError as exc:
        raise InboundEventError('Meta leadgen 返回了无法解析的 JSON。') from exc
    if not isinstance(parsed, dict) or parsed.get('error'):
        provider_error = parsed.get('error') if isinstance(parsed, dict) else {}
        error_code = provider_error.get('code') if isinstance(provider_error, dict) else None
        suffix = f'（错误代码 {error_code}）' if error_code is not None else ''
        raise InboundEventError(f'Meta leadgen 返回异常{suffix}。')
    return parsed


def process_meta_lead_receipt(receipt: LeadInboundEvent) -> LeadSubmission:
    if receipt.submission_id:
        return receipt.submission
    payload = dict(receipt.payload_json or {})
    value = payload.get('value') if isinstance(payload.get('value'), dict) else {}
    leadgen_id = str(payload.get('leadgen_id') or value.get('leadgen_id') or '').strip()
    if not leadgen_id:
        raise InboundEventError('Meta webhook 缺少 leadgen_id。')
    webhook_form_id = meta_leadgen_event_form_id(payload)
    configured_form_ids = configured_meta_leadgen_form_ids(receipt.integration)
    if configured_form_ids and webhook_form_id not in configured_form_ids:
        raise InboundEventError('Meta webhook Form ID 与接入白名单不一致。')
    lead_data = fetch_meta_lead(receipt.integration, leadgen_id)
    fetched_form_id = str(lead_data.get('form_id') or '').strip()
    if webhook_form_id and fetched_form_id and fetched_form_id != webhook_form_id:
        raise InboundEventError('Meta leadgen 返回的 Form ID 与已验签 webhook 不一致。')
    if configured_form_ids and fetched_form_id not in configured_form_ids:
        raise InboundEventError('Meta leadgen 返回的 Form ID 与接入白名单不一致。')
    fields = _field_data_map(lead_data)
    first_name = _pick(fields, 'first_name', 'firstname')
    last_name = _pick(fields, 'last_name', 'lastname')
    full_name = _pick(fields, 'full_name', 'name') or ' '.join(item for item in [first_name, last_name] if item)
    email = _pick(fields, 'email', 'email_address')
    phone = _pick(fields, 'phone_number', 'phone', 'mobile_number')
    company = _pick(fields, 'company_name', 'company', 'business_name')
    country = _pick(fields, 'country', 'country_region', 'country_or_region')
    message = _pick(fields, 'project_requirement', 'message', 'comments', 'what_are_you_looking_for')
    known = {
        'first_name', 'firstname', 'last_name', 'lastname', 'full_name', 'name',
        'email', 'email_address', 'phone_number', 'phone', 'mobile_number',
        'company_name', 'company', 'business_name', 'country', 'country_region',
        'country_or_region', 'project_requirement', 'message', 'comments', 'what_are_you_looking_for',
    }
    extra_fields = {key: value for key, value in fields.items() if key not in known}
    created_time = lead_data.get('created_time') or value.get('created_time')
    source_url = str((receipt.integration.config_json or {}).get('source_url') or '').strip()
    identifiers = {
        'external_id': leadgen_id,
        'meta_lead_id': leadgen_id,
        'meta_campaign_id': str(lead_data.get('campaign_id') or value.get('campaign_id') or ''),
        'meta_adset_id': str(lead_data.get('adset_id') or value.get('adgroup_id') or ''),
        'meta_ad_id': str(lead_data.get('ad_id') or value.get('ad_id') or ''),
        'meta_form_id': str(lead_data.get('form_id') or value.get('form_id') or ''),
        'meta_page_id': str(payload.get('page_id') or value.get('page_id') or receipt.integration.public_id),
    }
    submission = _create_external_submission(
        integration=receipt.integration,
        external_event_id=leadgen_id,
        submitted_at=_timestamp(created_time, fallback=receipt.received_at),
        full_name=full_name,
        email=email,
        phone=phone,
        company=company,
        country=country,
        message=message,
        source_channel='meta_ads',
        source_detail='Meta Instant Form',
        source_url=source_url,
        payload_json={'origin': 'meta_instant_form', 'answers': fields, **extra_fields},
        identifiers_json=identifiers,
    )
    initial_events_queued = _queue_initial_crm_event(submission, receipt.integration)
    receipt.payload_json = {
        **payload,
        'lead_data': lead_data,
        'initial_crm_events_queued': initial_events_queued,
    }
    return submission


def _message_text(message: dict[str, Any]) -> str:
    message_type = str(message.get('type') or '').strip()
    typed = message.get(message_type) if isinstance(message.get(message_type), dict) else {}
    if message_type == 'text':
        return _bounded(typed.get('body'), 4000)
    if message_type == 'button':
        return _bounded(typed.get('text') or typed.get('payload'), 4000)
    if message_type == 'interactive':
        for reply_key in ('button_reply', 'list_reply'):
            reply = typed.get(reply_key) if isinstance(typed.get(reply_key), dict) else {}
            if reply:
                return _bounded(reply.get('title') or reply.get('id'), 4000)
        return _bounded(json.dumps(typed, ensure_ascii=False), 4000)
    if message_type in {'image', 'video', 'document'}:
        return _bounded(typed.get('caption'), 4000) or f'[{message_type} message]'
    if message_type == 'audio':
        return '[audio message]'
    if message_type == 'sticker':
        return '[sticker message]'
    if message_type == 'location':
        label = typed.get('name') or typed.get('address')
        return _bounded(label, 4000) or '[location message]'
    if message_type == 'contacts':
        contacts = message.get('contacts') if isinstance(message.get('contacts'), list) else []
        return f'[{len(contacts)} contact(s)]'
    return f'[{message_type or "unknown"} message]'


def _normalized_message_payload(message: dict[str, Any]) -> dict[str, Any]:
    message_type = str(message.get('type') or '').strip().lower()
    typed = message.get(message_type)
    return {
        'context': message.get('context') if isinstance(message.get('context'), dict) else {},
        'referral': message.get('referral') if isinstance(message.get('referral'), dict) else {},
        'content': typed if isinstance(typed, (dict, list)) else {},
        'wamid': _bounded(message.get('wamid'), 512),
        'ycloud_message_id': _bounded(message.get('ycloud_message_id'), 512),
    }


def _conversion_relationships(submission: LeadSubmission):
    conversion = (
        LeadConversion.objects.select_related('company', 'contact')
        .filter(submission=submission)
        .first()
    )
    return (
        conversion.company if conversion else None,
        conversion.contact if conversion else None,
    )


def _ensure_whatsapp_media(message_record: WhatsAppMessage, message: dict[str, Any]) -> None:
    message_type = message_record.message_type
    if message_type not in {'image', 'video', 'audio', 'document', 'sticker'}:
        return
    typed = message.get(message_type) if isinstance(message.get(message_type), dict) else {}
    external_media_id = str(typed.get('id') or '').strip()
    if not external_media_id:
        return
    raw_sha256 = str(typed.get('sha256') or '').strip()
    normalized_sha256 = raw_sha256.lower() if re.fullmatch(r'[0-9a-fA-F]{64}', raw_sha256) else ''
    if raw_sha256 and not normalized_sha256:
        try:
            decoded_sha256 = base64.b64decode(raw_sha256, validate=True)
        except (ValueError, TypeError):
            decoded_sha256 = b''
        if len(decoded_sha256) == 32:
            normalized_sha256 = decoded_sha256.hex()
    media, created = WhatsAppMedia.objects.get_or_create(
        external_media_id=external_media_id,
        defaults={
            'message': message_record,
            'media_type': message_type,
            'mime_type': _bounded(typed.get('mime_type'), 255),
            'original_name': _bounded(typed.get('filename'), 512),
            'sha256': normalized_sha256,
            'status': 'pending',
        },
    )
    if not created and media.message_id != message_record.id:
        raise InboundEventError('WhatsApp Media ID 已关联到其他消息，必须人工核对。')


def _persist_normalized_whatsapp_message(
    *,
    receipt: LeadInboundEvent,
    submission: LeadSubmission,
    payload: dict[str, Any],
    message: dict[str, Any],
    profile: dict[str, Any],
    referral: dict[str, Any],
    wa_id: str,
    message_id: str,
    message_text: str,
    phone_number_id: str,
    ctwa_clid: str,
    direction: str = 'inbound',
) -> LeadSubmission:
    """Persist one already-resolved lead as a normalized conversation event.

    The caller owns the transaction and contact-scoped advisory lock. Keeping
    this storage boundary explicit also lets mapping-only tests avoid touching
    unmanaged PostgreSQL tables.
    """

    if direction not in {'inbound', 'outbound'}:
        raise InboundEventError('WhatsApp 消息方向无效。')
    company, crm_contact = _conversion_relationships(submission)
    conversation, conversation_created = WhatsAppConversation.objects.get_or_create(
        integration=receipt.integration,
        external_contact_id=wa_id,
        defaults={
            'site': submission.site,
            'submission': submission,
            'company': company,
            'contact': crm_contact,
            'owner_user': submission.assignee,
            'team': submission.team,
            'display_name': _bounded(profile.get('name') or submission.full_name, 200),
            'metadata_json': {
                'first_referral': referral,
                'attribution': 'ctwa' if ctwa_clid else 'organic_whatsapp',
            },
        },
    )
    if not conversation_created:
        conversation = WhatsAppConversation.objects.select_for_update().get(pk=conversation.pk)
        if conversation.submission_id and conversation.submission_id != submission.id:
            submission = conversation.submission
        changed_fields = []
        for field_name, value in (
            ('submission', submission),
            ('company', company),
            ('contact', crm_contact),
            ('owner_user', submission.assignee),
            ('team', submission.team),
        ):
            if value is not None and getattr(conversation, f'{field_name}_id') is None:
                setattr(conversation, field_name, value)
                changed_fields.append(field_name)
        display_name = _bounded(profile.get('name'), 200)
        if display_name and display_name != conversation.display_name:
            conversation.display_name = display_name
            changed_fields.append('display_name')
        if changed_fields:
            conversation.updated_at = timezone.now()
            conversation.save(update_fields=[*changed_fields, 'updated_at'])

    occurred_at = _timestamp(message.get('timestamp'), fallback=receipt.received_at)
    message_type = str(message.get('type') or '').strip().lower()
    if message_type not in WHATSAPP_MESSAGE_TYPES:
        message_type = 'unknown'
    normalized_message, message_created = WhatsAppMessage.objects.get_or_create(
        external_message_id=message_id,
        defaults={
            'conversation': conversation,
            'inbound_event': receipt,
            'direction': direction,
            'message_type': message_type,
            'sender_id': wa_id if direction == 'inbound' else phone_number_id,
            'recipient_id': phone_number_id if direction == 'inbound' else wa_id,
            'body': message_text,
            'status': 'received' if direction == 'inbound' else 'sent',
            'payload_json': _normalized_message_payload(message),
            'provider_timestamp': occurred_at,
            'queued_at': occurred_at,
            'sent_at': occurred_at if direction == 'outbound' else None,
        },
    )
    if not message_created and normalized_message.conversation_id != conversation.id:
        raise InboundEventError('WhatsApp Message ID 已关联到其他会话，必须人工核对。')
    _ensure_whatsapp_media(normalized_message, message)

    if message_created:
        current_last = conversation.last_message_at
        if current_last is None or occurred_at >= current_last:
            conversation.last_message_at = occurred_at
            conversation.last_message_preview = message_text
        update_fields = ['last_message_at', 'last_message_preview', 'updated_at']
        if direction == 'outbound':
            conversation.last_outbound_at = max(
                filter(None, [conversation.last_outbound_at, occurred_at])
            )
            update_fields.append('last_outbound_at')
        else:
            conversation.unread_count += 1
            conversation.last_inbound_at = max(
                filter(None, [conversation.last_inbound_at, occurred_at])
            )
            conversation.service_window_expires_at = conversation.last_inbound_at + WHATSAPP_CUSTOMER_SERVICE_WINDOW
            update_fields.extend(['unread_count', 'last_inbound_at', 'service_window_expires_at'])
            opted_out = message_type == 'text' and message_text.strip().upper() in WHATSAPP_OPT_OUT_KEYWORDS
            if opted_out and conversation.opted_out_at is None:
                conversation.status = 'blocked'
                conversation.opted_out_at = occurred_at
                conversation.opt_out_reason = 'customer_keyword'
                update_fields.extend(['status', 'opted_out_at', 'opt_out_reason'])
                if conversation.contact_id:
                    type(conversation.contact).objects.filter(pk=conversation.contact_id).update(
                        status='do_not_contact',
                        updated_at=timezone.now(),
                    )
                if conversation.submission_id:
                    ConsentRecord.objects.create(
                        submission_id=conversation.submission_id,
                        purpose='marketing',
                        decision='withdrawn',
                        source='whatsapp_opt_out',
                        policy_version='',
                        evidence_json={
                            'conversation_id': conversation.id,
                            'message_id': normalized_message.id,
                        },
                    )
        conversation.updated_at = timezone.now()
        conversation.save(update_fields=update_fields)

    return submission


@transaction.atomic
def process_whatsapp_receipt(receipt: LeadInboundEvent) -> LeadSubmission:
    payload = dict(receipt.payload_json or {})
    message = payload.get('message') if isinstance(payload.get('message'), dict) else {}
    contact = payload.get('contact') if isinstance(payload.get('contact'), dict) else {}
    wa_id = str(payload.get('wa_id') or message.get('from') or '').strip()
    message_id = str(payload.get('message_id') or message.get('id') or '').strip()
    if not wa_id or not message_id:
        raise InboundEventError('WhatsApp webhook 缺少联系人或 message ID。')
    phone_number_id = str(payload.get('phone_number_id') or '').strip()
    if receipt.integration.public_id and phone_number_id != receipt.integration.public_id:
        raise InboundEventError('WhatsApp Phone Number ID 与接入配置不一致。')
    contact_lock = sha256_text(
        f'whatsapp-conversation|{receipt.integration.id}|{wa_id}'
    )
    _acquire_external_submission_lock(contact_lock)
    profile = contact.get('profile') if isinstance(contact.get('profile'), dict) else {}
    referral = message.get('referral') if isinstance(message.get('referral'), dict) else {}
    ctwa_clid = str(referral.get('ctwa_clid') or '').strip()
    waba_id = _configured_waba_id(receipt.integration, payload)
    meta_page_id = _configured_meta_page_id(receipt.integration, payload)
    ctwa_identity_mode = _configured_ctwa_identity_mode(receipt.integration)
    form = _form_for_integration(receipt.integration)
    existing = receipt.submission if receipt.submission_id else None
    if existing is None:
        existing = (
            LeadSubmission.objects.filter(
                site=form.site,
                identifiers_json__whatsapp_id=wa_id,
            )
            .select_for_update()
            .exclude(stage__in=['lost', 'spam'])
            .order_by('-submitted_at', '-id')
            .first()
        )
    message_record = {
        'id': message_id,
        'timestamp': str(message.get('timestamp') or ''),
        'type': str(message.get('type') or ''),
        'text': _message_text(message),
    }
    if existing:
        identifiers = dict(existing.identifiers_json or {})
        identifiers.setdefault('whatsapp_id', wa_id)
        if ctwa_clid:
            identifiers.setdefault('ctwa_clid', ctwa_clid)
            identifiers.setdefault('whatsapp_business_account_id', waba_id)
            identifiers.setdefault('ctwa_identity_mode', ctwa_identity_mode)
            if meta_page_id:
                identifiers.setdefault('meta_page_id', meta_page_id)
        existing.identifiers_json = identifiers
        existing.message = existing.message or message_record['text']
        existing.updated_at = timezone.now()
        existing.save(update_fields=['identifiers_json', 'message', 'updated_at'])
        submission = existing
    else:
        source_channel = 'meta_ads' if ctwa_clid else 'whatsapp'
        identifiers = {
            'external_id': wa_id,
            'whatsapp_id': wa_id,
            'ctwa_clid': ctwa_clid,
            'whatsapp_business_account_id': waba_id,
            'ctwa_identity_mode': ctwa_identity_mode,
            'meta_page_id': meta_page_id,
            'whatsapp_phone_number_id': phone_number_id,
        }
        source_detail = 'Click-to-WhatsApp ad' if ctwa_clid else 'WhatsApp conversation'
        submission = _create_external_submission(
            integration=receipt.integration,
            external_event_id=message_id,
            submitted_at=_timestamp(message.get('timestamp'), fallback=receipt.received_at),
            full_name=str(profile.get('name') or ''),
            phone=wa_id,
            message=message_record['text'],
            source_channel=source_channel,
            source_detail=source_detail,
            source_url=str(referral.get('source_url') or ''),
            payload_json={
                'origin': 'whatsapp_ctwa' if ctwa_clid else 'whatsapp',
                'first_referral': referral,
            },
            identifiers_json=identifiers,
        )

    return _persist_normalized_whatsapp_message(
        receipt=receipt,
        submission=submission,
        payload=payload,
        message=message,
        profile=profile,
        referral=referral,
        wa_id=wa_id,
        message_id=message_id,
        message_text=message_record['text'],
        phone_number_id=phone_number_id,
        ctwa_clid=ctwa_clid,
    )


@transaction.atomic
def process_whatsapp_echo_receipt(receipt: LeadInboundEvent) -> LeadSubmission:
    """Mirror a message sent from the WhatsApp Business app into the CRM."""

    payload = dict(receipt.payload_json or {})
    message = payload.get('message') if isinstance(payload.get('message'), dict) else {}
    wa_id = str(payload.get('wa_id') or message.get('to') or '').strip()
    message_id = str(payload.get('message_id') or message.get('id') or '').strip()
    phone_number_id = str(payload.get('phone_number_id') or '').strip()
    if not wa_id or not message_id:
        raise InboundEventError('WhatsApp Business App 回显缺少联系人或 message ID。')
    if receipt.integration.public_id and phone_number_id != receipt.integration.public_id:
        raise InboundEventError('WhatsApp Business App 回显的 Phone Number ID 与接入配置不一致。')

    contact_lock = sha256_text(
        f'whatsapp-conversation|{receipt.integration.id}|{wa_id}'
    )
    _acquire_external_submission_lock(contact_lock)
    form = _form_for_integration(receipt.integration)
    submission = (
        LeadSubmission.objects.filter(
            site=form.site,
            identifiers_json__whatsapp_id=wa_id,
        )
        .select_for_update()
        .exclude(stage__in=['lost', 'spam'])
        .order_by('-submitted_at', '-id')
        .first()
    )
    message_text = _message_text(message)
    if submission is None:
        submission = _create_external_submission(
            integration=receipt.integration,
            external_event_id=f'whatsapp-app-echo:{message_id}',
            submitted_at=_timestamp(message.get('timestamp'), fallback=receipt.received_at),
            phone=wa_id,
            message=message_text,
            source_channel='whatsapp',
            source_detail='WhatsApp Business App conversation',
            payload_json={'origin': 'whatsapp_business_app_echo'},
            identifiers_json={
                'external_id': wa_id,
                'whatsapp_id': wa_id,
                'whatsapp_business_account_id': _configured_waba_id(receipt.integration, payload),
                'whatsapp_phone_number_id': phone_number_id,
            },
        )

    return _persist_normalized_whatsapp_message(
        receipt=receipt,
        submission=submission,
        payload=payload,
        message=message,
        profile={},
        referral={},
        wa_id=wa_id,
        message_id=message_id,
        message_text=message_text,
        phone_number_id=phone_number_id,
        ctwa_clid='',
        direction='outbound',
    )


def process_whatsapp_coexistence_audit_receipt(receipt: LeadInboundEvent) -> None:
    """Validate and retain privacy-governed Coexistence sync evidence.

    History/contact payloads are intentionally not expanded into leads by
    default.  They remain durable receipts so retention jobs can purge the raw
    payload according to the existing privacy policy, without silently
    importing up to 180 days of customer history.
    """

    payload = dict(receipt.payload_json or {})
    phone_number_id = str(payload.get('phone_number_id') or '').strip()
    event_fingerprint = str(payload.get('event_fingerprint') or '').strip().lower()
    if receipt.integration.public_id and phone_number_id != receipt.integration.public_id:
        raise InboundEventError('WhatsApp Coexistence 同步事件与接入号码不一致。')
    if len(event_fingerprint) != 64:
        raise InboundEventError('WhatsApp Coexistence 同步事件缺少有效指纹。')
    return None


def _whatsapp_status_error(status_payload: dict[str, Any]) -> tuple[str, str]:
    errors = status_payload.get('errors') if isinstance(status_payload.get('errors'), list) else []
    first = errors[0] if errors and isinstance(errors[0], dict) else {}
    code = _bounded(first.get('code'), 120)
    details = first.get('error_data') if isinstance(first.get('error_data'), dict) else {}
    message = first.get('message') or first.get('title') or details.get('details')
    return code, _bounded(message, 1000)


@transaction.atomic
def process_whatsapp_status_receipt(receipt: LeadInboundEvent) -> LeadSubmission:
    payload = dict(receipt.payload_json or {})
    message_id = str(payload.get('message_id') or '').strip()
    delivery_status = str(payload.get('delivery_status') or '').strip().lower()
    event_fingerprint = str(payload.get('event_fingerprint') or '').strip().lower()
    phone_number_id = str(payload.get('phone_number_id') or '').strip()
    if not message_id or delivery_status not in WHATSAPP_DELIVERY_STATUSES:
        raise InboundEventError('WhatsApp 状态回执缺少消息 ID 或有效状态。')
    if len(event_fingerprint) != 64:
        raise InboundEventError('WhatsApp 状态回执缺少有效事件指纹。')
    if receipt.integration.public_id and phone_number_id != receipt.integration.public_id:
        raise InboundEventError('WhatsApp 状态 Phone Number ID 与接入配置不一致。')
    try:
        message = (
            WhatsAppMessage.objects.select_for_update(of=('self',))
            .select_related('conversation__submission')
            .get(external_message_id=message_id)
        )
    except WhatsAppMessage.DoesNotExist as exc:
        raise InboundEventError('状态对应的 WhatsApp 消息尚未入库，可稍后重试。') from exc
    if message.conversation.integration_id != receipt.integration_id:
        raise InboundEventError('WhatsApp 状态回执与消息接入不一致。')
    if message.direction != 'outbound':
        raise InboundEventError('WhatsApp 状态回执不能关联入站或内部消息。')

    status_payload = payload.get('status') if isinstance(payload.get('status'), dict) else {}
    occurred_at = _timestamp(status_payload.get('timestamp'), fallback=receipt.received_at)
    error_code, error_message = _whatsapp_status_error(status_payload)
    provider_wamid = _bounded(status_payload.get('wamid'), 512)
    _delivery_event, created = WhatsAppDeliveryEvent.objects.get_or_create(
        event_fingerprint=event_fingerprint,
        defaults={
            'message': message,
            'inbound_event': receipt,
            'status': delivery_status,
            'error_code': error_code,
            'payload_json': {
                'conversation': status_payload.get('conversation', {}),
                'pricing': status_payload.get('pricing', {}),
                'errors': status_payload.get('errors', []),
            },
            'occurred_at': occurred_at,
            'received_at': receipt.received_at,
        },
    )
    if not created:
        return message.conversation.submission

    current_rank = WHATSAPP_DELIVERY_RANK.get(message.status, 0)
    incoming_rank = WHATSAPP_DELIVERY_RANK.get(delivery_status, 0)
    current_event_at = max(
        filter(None, [message.sent_at, message.delivered_at, message.read_at, message.failed_at]),
        default=None,
    )
    is_out_of_order = current_event_at is not None and occurred_at < current_event_at
    update_fields = []
    if provider_wamid and (message.payload_json or {}).get('wamid') != provider_wamid:
        message.payload_json = {**(message.payload_json or {}), 'wamid': provider_wamid}
        update_fields.append('payload_json')
    if delivery_status in WHATSAPP_DELIVERY_RANK and not is_out_of_order and incoming_rank >= current_rank:
        message.status = delivery_status
        setattr(message, f'{delivery_status}_at', occurred_at)
        message.error_code = ''
        message.error_message = ''
        message.retryable = False
        message.next_attempt_at = None
        update_fields.extend([
            'status', f'{delivery_status}_at', 'error_code', 'error_message',
            'retryable', 'next_attempt_at',
        ])
    elif delivery_status == 'failed' and message.status not in {'delivered', 'read'} and not is_out_of_order:
        message.status = 'failed'
        message.failed_at = occurred_at
        message.error_code = error_code
        message.error_message = error_message or 'WhatsApp 平台报告发送失败。'
        message.retryable = error_code in {'130429', '131016', '131048', '131056'}
        message.next_attempt_at = (
            timezone.now() + timedelta(minutes=5) if message.retryable else None
        )
        update_fields.extend([
            'status', 'failed_at', 'error_code', 'error_message',
            'retryable', 'next_attempt_at',
        ])
    if update_fields:
        message.updated_at = timezone.now()
        message.save(update_fields=[*update_fields, 'updated_at'])
    submission = message.conversation.submission
    if submission is None:
        raise InboundEventError('WhatsApp 状态消息尚未关联线索，必须人工核对。')
    return submission


def inbound_receipt_retryability(
    receipt: LeadInboundEvent,
    *,
    max_attempts: int = 8,
    now=None,
) -> tuple[bool, str]:
    """Return the exact, side-effect-free retry contract for one receipt.

    Webhook signatures are checked before a durable receipt is created.  A
    retry therefore works only from the normalized provider evidence stored on
    that receipt; it never accepts a new payload from the console.
    """

    logical_status = receipt.status
    if logical_status == 'processing' and inbound_processing_claim_is_stale(
        receipt,
        now=now,
    ):
        # The exact action will atomically recover this abandoned claim before
        # taking a fresh one.  Treat it as failed for row/action parity without
        # mutating state during a GET.
        logical_status = 'failed'
    if logical_status != 'failed':
        return False, f'当前状态 {receipt.status} 已不能重试。'
    if receipt.attempts >= max_attempts:
        return False, f'已达到最多 {max_attempts} 次处理上限。'

    integration = receipt.integration
    if not integration.enabled:
        return False, '平台接入已停用，请先核对接入配置。'
    if not integration.site_id:
        return False, '平台接入未绑定站点。'
    if receipt.provider_code != integration.provider.code:
        return False, '收据平台与接入配置不一致，必须人工核对。'

    adapter = (receipt.provider_code, integration.integration_type, receipt.event_type)
    if adapter not in {
        ('meta', 'leadgen', 'leadgen'),
        ('whatsapp', 'cloud_api', 'message'),
        ('whatsapp', 'cloud_api', 'message_echo'),
        ('whatsapp', 'cloud_api', 'status'),
        ('whatsapp', 'cloud_api', 'history'),
        ('whatsapp', 'cloud_api', 'contact_sync'),
    }:
        return False, '当前入站事件类型没有安全重试适配器，必须人工核对。'

    # A receipt already linked to a lead is idempotent: the provider-specific
    # processors return that existing record instead of creating another one.
    if receipt.submission_id:
        return True, ''

    payload = dict(receipt.payload_json or {})
    if adapter == ('meta', 'leadgen', 'leadgen'):
        value = payload.get('value') if isinstance(payload.get('value'), dict) else {}
        leadgen_id = str(payload.get('leadgen_id') or value.get('leadgen_id') or '').strip()
        page_id = str(payload.get('page_id') or value.get('page_id') or '').strip()
        if not leadgen_id:
            return False, '缺少已验签收据中的 leadgen_id，必须人工核对。'
        if integration.public_id and page_id and page_id != integration.public_id:
            return False, '收据 Page ID 与当前接入不一致，必须人工核对。'
        return True, ''

    if adapter in {
        ('whatsapp', 'cloud_api', 'message'),
        ('whatsapp', 'cloud_api', 'message_echo'),
        ('whatsapp', 'cloud_api', 'status'),
    }:
        message = payload.get('message') if isinstance(payload.get('message'), dict) else {}
        message_id = str(payload.get('message_id') or message.get('id') or '').strip()
        phone_number_id = str(payload.get('phone_number_id') or '').strip()
        if not message_id:
            return False, '缺少已验签收据中的 message ID，必须人工核对。'
        if integration.public_id and phone_number_id and phone_number_id != integration.public_id:
            return False, '收据 Phone Number ID 与当前接入不一致，必须人工核对。'
        if adapter[-1] == 'status':
            delivery_status = str(payload.get('delivery_status') or '').strip().lower()
            event_fingerprint = str(payload.get('event_fingerprint') or '').strip().lower()
            if delivery_status not in WHATSAPP_DELIVERY_STATUSES or len(event_fingerprint) != 64:
                return False, '状态收据缺少有效状态或事件指纹，必须人工核对。'
        return True, ''

    if adapter in {
        ('whatsapp', 'cloud_api', 'history'),
        ('whatsapp', 'cloud_api', 'contact_sync'),
    }:
        phone_number_id = str(payload.get('phone_number_id') or '').strip()
        event_fingerprint = str(payload.get('event_fingerprint') or '').strip().lower()
        if integration.public_id and phone_number_id != integration.public_id:
            return False, '收据 Phone Number ID 与当前接入不一致，必须人工核对。'
        if len(event_fingerprint) != 64:
            return False, 'Coexistence 同步收据缺少有效事件指纹，必须人工核对。'
        return True, ''

    return False, '当前入站事件类型没有安全重试适配器，必须人工核对。'


def process_claimed_inbound_receipt(receipt: LeadInboundEvent) -> bool:
    """Process a receipt that has already been atomically claimed."""

    try:
        if receipt.provider_code == 'meta' and receipt.event_type == 'leadgen':
            submission = process_meta_lead_receipt(receipt)
        elif receipt.provider_code == 'whatsapp' and receipt.event_type == 'message':
            submission = process_whatsapp_receipt(receipt)
        elif receipt.provider_code == 'whatsapp' and receipt.event_type == 'message_echo':
            submission = process_whatsapp_echo_receipt(receipt)
        elif receipt.provider_code == 'whatsapp' and receipt.event_type == 'status':
            submission = process_whatsapp_status_receipt(receipt)
        elif receipt.provider_code == 'whatsapp' and receipt.event_type in {'history', 'contact_sync'}:
            submission = process_whatsapp_coexistence_audit_receipt(receipt)
        else:
            raise InboundEventError(f'不支持的外部事件：{receipt.provider_code}/{receipt.event_type}')
    except Exception as exc:
        receipt.status = 'failed'
        receipt.last_error = str(exc)[:1000]
        receipt.updated_at = timezone.now()
        receipt.save(update_fields=['status', 'attempts', 'last_error', 'payload_json', 'updated_at'])
        return False
    with transaction.atomic():
        durable_receipt = LeadInboundEvent.objects.select_for_update().get(pk=receipt.pk)
        durable_receipt.submission = submission
        durable_receipt.status = 'processed'
        durable_receipt.last_error = ''
        durable_receipt.payload_json = receipt.payload_json
        durable_receipt.processed_at = timezone.now()
        durable_receipt.updated_at = durable_receipt.processed_at
        durable_receipt.save(
            update_fields=[
                'submission', 'status', 'last_error', 'payload_json',
                'processed_at', 'updated_at',
            ]
        )
        if submission is not None and receipt.event_type in {'leadgen', 'message'}:
            transaction.on_commit(
                lambda submission_id=submission.pk:
                _send_external_submission_notification_if_pending(submission_id)
            )
        receipt.submission = submission
        receipt.status = durable_receipt.status
        receipt.last_error = durable_receipt.last_error
        receipt.processed_at = durable_receipt.processed_at
        receipt.updated_at = durable_receipt.updated_at
    return True


def process_inbound_receipt(receipt: LeadInboundEvent) -> bool:
    """Claim and process a pending/failed receipt without double-processing."""

    claimed_at = timezone.now()
    claimed = LeadInboundEvent.objects.filter(
        id=receipt.id,
        status__in={'pending', 'failed'},
    ).update(
        status='processing',
        attempts=F('attempts') + 1,
        updated_at=claimed_at,
    )
    if claimed != 1:
        return False
    receipt.refresh_from_db()
    return process_claimed_inbound_receipt(receipt)


@transaction.atomic
def record_inbound_event(
    *,
    integration: MarketingIntegration,
    provider_code: str,
    event_type: str,
    external_event_id: str,
    payload: dict[str, Any],
) -> tuple[LeadInboundEvent, bool]:
    """Durably record a verified event without making provider network calls."""

    receipt, created = LeadInboundEvent.objects.get_or_create(
        provider_code=provider_code,
        event_type=event_type,
        external_event_id=external_event_id,
        defaults={
            'integration': integration,
            'payload_json': payload,
            'status': 'pending',
            'received_at': timezone.now(),
        },
    )
    if receipt.integration_id != integration.id:
        raise InboundEventError('外部事件 ID 已被另一接入占用，必须人工核对。')
    return receipt, created


def retry_inbound_receipt_by_id(
    *,
    receipt_id: int,
    site_id: int,
    max_attempts: int = 8,
    on_claim: Callable[[LeadInboundEvent], None] | None = None,
) -> tuple[LeadInboundEvent, bool]:
    """Claim exactly one failed receipt, then process outside the lock.

    The short transaction only protects the state transition.  Provider HTTP
    calls happen after it commits, so a second console request observes
    ``processing`` and receives a conflict instead of duplicating work.
    """

    with transaction.atomic():
        receipt = (
            LeadInboundEvent.objects.select_for_update(of=('self',))
            .select_related('integration__provider', 'submission')
            .get(id=receipt_id, integration__site_id=site_id)
        )
        claimed_at = timezone.now()
        retryable, reason = inbound_receipt_retryability(
            receipt,
            max_attempts=max_attempts,
            now=claimed_at,
        )
        if not retryable:
            raise InboundReceiptRetryConflict(reason)
        if receipt.status == 'processing':
            # ``retryable`` proves the existing claim is stale.  Persist the
            # recovery and the fresh claim in this same short transaction, so
            # an audit callback failure restores the original stale row.
            receipt.status = 'failed'
            receipt.last_error = INBOUND_CLAIM_TIMEOUT_ERROR
            receipt.updated_at = claimed_at
            receipt.save(update_fields=['status', 'last_error', 'updated_at'])
        receipt.status = 'processing'
        receipt.attempts += 1
        receipt.updated_at = claimed_at
        receipt.save(update_fields=['status', 'attempts', 'updated_at'])
        if on_claim is not None:
            on_claim(receipt)

    succeeded = process_claimed_inbound_receipt(receipt)
    return receipt, succeeded


def record_and_process_event(
    *,
    integration: MarketingIntegration,
    provider_code: str,
    event_type: str,
    external_event_id: str,
    payload: dict[str, Any],
) -> tuple[LeadInboundEvent, bool]:
    receipt, created = record_inbound_event(
        integration=integration,
        provider_code=provider_code,
        event_type=event_type,
        external_event_id=external_event_id,
        payload=payload,
    )
    if created or receipt.status in {'pending', 'failed'}:
        process_inbound_receipt(receipt)
    return receipt, created


def retry_inbound_events(*, limit: int = 100, max_attempts: int = 8, provider: str = '') -> tuple[int, int]:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return 0, 0
    effective_now = timezone.now()
    candidates = LeadInboundEvent.objects.all()
    if provider:
        candidates = candidates.filter(provider_code=provider)
    recover_stale_inbound_claims(now=effective_now, queryset=candidates)
    queryset = (
        candidates.select_related('integration__provider', 'submission')
        .filter(status__in=['pending', 'failed'], attempts__lt=max_attempts)
        .order_by('received_at', 'id')
    )
    attempted = 0
    succeeded = 0
    for receipt in queryset.iterator(chunk_size=max(min(limit, 200), 1)):
        if attempted >= limit:
            break
        if receipt.status == 'failed':
            retryable, _reason = inbound_receipt_retryability(
                receipt,
                max_attempts=max_attempts,
                now=effective_now,
            )
            if not retryable:
                continue
            try:
                _receipt, completed = retry_inbound_receipt_by_id(
                    receipt_id=receipt.id,
                    site_id=receipt.integration.site_id,
                    max_attempts=max_attempts,
                )
            except (InboundReceiptRetryConflict, LeadInboundEvent.DoesNotExist):
                continue
            attempted += 1
            if completed:
                succeeded += 1
            continue

        # Pending receipts are first-attempt work, not retries.  They still use
        # the existing compare-and-set claim in ``process_inbound_receipt``.
        attempted += 1
        if process_inbound_receipt(receipt):
            succeeded += 1
    return attempted, succeeded
