from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings

from leads.inbound import integration_secret
from leads.whatsapp_cloud import (
    WhatsAppCloudError,
    WhatsAppCloudResult,
    WhatsAppMediaUploadResult,
    WhatsAppPolicyError,
    require_whatsapp_external_io,
)


YCLOUD_API_BASE = 'https://api.ycloud.com/v2'
DEFAULT_SIGNATURE_TOLERANCE_SECONDS = 300
SUPPORTED_EVENT_TYPES = frozenset({
    'whatsapp.inbound_message.received',
    'whatsapp.message.updated',
    'whatsapp.smb.message.echoes',
    'whatsapp.smb.history',
    'whatsapp.smb.app.state.sync',
})


def whatsapp_transport_provider(integration) -> str:
    value = str((integration.config_json or {}).get('transport_provider') or 'meta').strip().lower()
    if value not in {'meta', 'ycloud'}:
        raise WhatsAppPolicyError('WhatsApp transport_provider 必须是 meta 或 ycloud。')
    return value


def normalize_phone_identity(value: Any) -> str:
    raw = str(value or '').strip()
    digits = re.sub(r'\D', '', raw)
    if raw and digits and (raw.startswith('+') or re.fullmatch(r'[\d\s().-]+', raw)):
        return digits
    return raw


def e164_phone(value: Any) -> str:
    digits = normalize_phone_identity(value)
    if not str(digits).isdigit() or not 8 <= len(str(digits)) <= 15:
        raise WhatsAppPolicyError('YCloud 号码必须是有效的 E.164 国际号码。')
    return f'+{digits}'


def verify_ycloud_signature(
    raw_body: bytes,
    signature_header: str,
    secret: str,
    *,
    now: int | None = None,
    tolerance_seconds: int = DEFAULT_SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """Verify YCloud's ``t=...,s=...`` HMAC over ``timestamp.raw_body``.

    The freshness check is deliberately mandatory so a captured valid request
    cannot be replayed indefinitely. Durable event IDs provide the second
    idempotency boundary after this transport-level replay protection.
    """

    if not raw_body or not secret:
        return False
    pairs: dict[str, str] = {}
    for part in str(signature_header or '').split(','):
        key, separator, value = part.strip().partition('=')
        if separator and key and value:
            pairs[key] = value
    try:
        timestamp = int(pairs.get('t') or '')
    except (TypeError, ValueError):
        return False
    supplied = str(pairs.get('s') or '').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}', supplied):
        return False
    effective_now = int(time.time() if now is None else now)
    tolerance = max(int(tolerance_seconds), 1)
    if abs(effective_now - timestamp) > tolerance:
        return False
    signed_payload = str(timestamp).encode('ascii') + b'.' + raw_body
    expected = hmac.new(secret.encode('utf-8'), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, expected)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _message_time(message: dict[str, Any], status: str = '') -> str:
    ordered_keys = {
        'read': ('readTime', 'deliverTime', 'sendTime', 'updateTime', 'createTime'),
        'delivered': ('deliverTime', 'sendTime', 'updateTime', 'createTime'),
        'sent': ('sendTime', 'updateTime', 'createTime'),
        'failed': ('updateTime', 'sendTime', 'createTime'),
    }.get(status, ('sendTime', 'createTime', 'updateTime'))
    for key in ordered_keys:
        value = str(message.get(key) or '').strip()
        if value:
            return value
    return ''


def _internal_message(raw: dict[str, Any], *, inbound: bool) -> tuple[dict[str, Any], str, str]:
    message = dict(raw)
    ycloud_id = str(message.get('id') or '').strip()
    wamid = str(message.get('wamid') or '').strip()
    message_id = wamid or ycloud_id
    if not message_id:
        return {}, '', ''
    sender = normalize_phone_identity(message.get('from') or message.get('fromUserId'))
    recipient = normalize_phone_identity(message.get('to') or message.get('toUserId'))
    wa_id = sender if inbound else recipient
    message['id'] = message_id
    message['timestamp'] = _message_time(message)
    if ycloud_id:
        message['ycloud_message_id'] = ycloud_id
    if wamid:
        message['wamid'] = wamid
    return message, message_id, wa_id


def _status_error(message: dict[str, Any]) -> list[dict[str, Any]]:
    code = str(message.get('errorCode') or '').strip()
    error = message.get('whatsappApiError') if isinstance(message.get('whatsappApiError'), dict) else {}
    detail = str(
        message.get('errorMessage')
        or error.get('message')
        or ((error.get('error_data') or {}).get('details') if isinstance(error.get('error_data'), dict) else '')
        or ''
    ).strip()
    if not code and not detail:
        return []
    return [{
        'code': code or str(error.get('code') or ''),
        'message': detail,
        'error_data': error.get('error_data') if isinstance(error.get('error_data'), dict) else {},
    }]


def ycloud_webhook_events(payload: dict[str, Any], *, integration) -> list[dict[str, Any]]:
    event_id = str(payload.get('id') or '').strip()
    event_type = str(payload.get('type') or '').strip()
    if not event_id or event_type not in SUPPORTED_EVENT_TYPES:
        return []
    config = dict(integration.config_json or {})
    configured_waba_id = str(config.get('waba_id') or '').strip()
    phone_number_id = str(integration.public_id or '').strip()
    if not configured_waba_id or not phone_number_id:
        return []

    events: list[dict[str, Any]] = []
    if event_type == 'whatsapp.inbound_message.received':
        raw = payload.get('whatsappInboundMessage')
        if not isinstance(raw, dict):
            return []
        waba_id = str(raw.get('wabaId') or configured_waba_id).strip()
        message, message_id, wa_id = _internal_message(raw, inbound=True)
        profile = raw.get('customerProfile') if isinstance(raw.get('customerProfile'), dict) else {}
        if not message_id or not wa_id:
            return []
        events.append({
            'event_type': 'message',
            'waba_id': waba_id,
            'phone_number_id': phone_number_id,
            'message_id': message_id,
            'wa_id': wa_id,
            'contact': {'wa_id': wa_id, 'profile': profile},
            'message': message,
            'value': {'provider': 'ycloud', 'event_id': event_id},
            'provider_event_id': event_id,
        })
    elif event_type == 'whatsapp.smb.message.echoes':
        raw = payload.get('whatsappMessage')
        if not isinstance(raw, dict):
            return []
        waba_id = str(raw.get('wabaId') or configured_waba_id).strip()
        message, message_id, wa_id = _internal_message(raw, inbound=False)
        if not message_id or not wa_id:
            return []
        events.append({
            'event_type': 'message_echo',
            'waba_id': waba_id,
            'phone_number_id': phone_number_id,
            'message_id': message_id,
            'wa_id': wa_id,
            'contact': {},
            'message': message,
            'value': {'provider': 'ycloud', 'event_id': event_id},
            'provider_event_id': event_id,
        })
    elif event_type == 'whatsapp.smb.history':
        inbound = payload.get('whatsappInboundMessage')
        outbound = payload.get('whatsappMessage')
        raw = inbound if isinstance(inbound, dict) else outbound
        if not isinstance(raw, dict):
            return []
        events.append({
            'event_type': 'history',
            'waba_id': str(raw.get('wabaId') or configured_waba_id).strip(),
            'phone_number_id': phone_number_id,
            'event_fingerprint': _fingerprint({'ycloud_event_id': event_id}),
            'value': {
                'provider': 'ycloud',
                'event_id': event_id,
                'direction': 'inbound' if isinstance(inbound, dict) else 'outbound',
                'history_message': raw,
            },
            'provider_event_id': event_id,
        })
    elif event_type == 'whatsapp.smb.app.state.sync':
        raw = payload.get('whatsappSmbAppStateSync')
        if not isinstance(raw, dict):
            return []
        events.append({
            'event_type': 'contact_sync',
            'waba_id': str(raw.get('wabaId') or configured_waba_id).strip(),
            'phone_number_id': phone_number_id,
            'event_fingerprint': _fingerprint({'ycloud_event_id': event_id}),
            'value': {
                'provider': 'ycloud',
                'event_id': event_id,
                'state_sync': raw,
            },
            'provider_event_id': event_id,
        })
    else:
        raw = payload.get('whatsappMessage')
        if not isinstance(raw, dict):
            return []
        status = str(raw.get('status') or '').strip().lower()
        ycloud_message_id = str(raw.get('id') or '').strip()
        if not ycloud_message_id or status not in {'sent', 'delivered', 'read', 'failed'}:
            return []
        status_payload = {
            'id': ycloud_message_id,
            'status': status,
            'timestamp': _message_time(raw, status=status),
            'recipient_id': normalize_phone_identity(raw.get('to')),
            'errors': _status_error(raw),
            'conversation': {},
            'pricing': {
                'pricing_model': raw.get('pricingModel'),
                'pricing_type': raw.get('pricingType'),
                'category': raw.get('pricingCategory'),
                'total_price': raw.get('totalPrice'),
                'currency': raw.get('currency'),
            },
            'ycloud_event_id': event_id,
            'wamid': str(raw.get('wamid') or '').strip(),
            'external_id': str(raw.get('externalId') or '').strip(),
        }
        events.append({
            'event_type': 'status',
            'waba_id': str(raw.get('wabaId') or configured_waba_id).strip(),
            'phone_number_id': phone_number_id,
            'message_id': ycloud_message_id,
            'delivery_status': status,
            'event_fingerprint': _fingerprint({'ycloud_event_id': event_id}),
            'status': status_payload,
            'value': {'provider': 'ycloud', 'event_id': event_id},
            'provider_event_id': event_id,
        })
    return events


def ycloud_event_matches_integration(payload: dict[str, Any], *, integration) -> bool:
    config = dict(integration.config_json or {})
    expected_waba = str(config.get('waba_id') or '').strip()
    expected_business_phone = normalize_phone_identity(config.get('business_phone_e164'))
    event_type = str(payload.get('type') or '')
    inbound_history = False
    if event_type == 'whatsapp.smb.app.state.sync':
        raw = payload.get('whatsappSmbAppStateSync')
    else:
        raw = payload.get('whatsappInboundMessage')
        inbound_history = event_type == 'whatsapp.smb.history' and isinstance(raw, dict)
        if not isinstance(raw, dict):
            raw = payload.get('whatsappMessage')
    if not isinstance(raw, dict):
        raw = {}
    actual_waba = str(raw.get('wabaId') or '').strip()
    if actual_waba and expected_waba and actual_waba != expected_waba:
        return False
    if expected_business_phone:
        if event_type == 'whatsapp.smb.app.state.sync':
            business_side = raw.get('phoneNumber')
        elif event_type == 'whatsapp.inbound_message.received' or inbound_history:
            business_side = raw.get('to')
        else:
            business_side = raw.get('from')
        actual_business_phone = normalize_phone_identity(business_side)
        if actual_business_phone and actual_business_phone != expected_business_phone:
            return False
    return True


def _safe_error(payload: dict[str, Any], fallback: str) -> tuple[str, str]:
    nested = payload.get('error') if isinstance(payload.get('error'), dict) else {}
    code = str(
        payload.get('errorCode')
        or payload.get('code')
        or nested.get('code')
        or ''
    ).strip()[:120]
    message = str(
        payload.get('errorMessage')
        or payload.get('message')
        or nested.get('message')
        or fallback
    ).strip()[:1000]
    return code, message


class YCloudWhatsAppClient:
    def __init__(self, *, integration):
        require_whatsapp_external_io()
        api_key = integration_secret(integration, 'primary')
        if not api_key:
            raise WhatsAppPolicyError('YCloud API Key 未配置。')
        config = dict(integration.config_json or {})
        self.api_key = api_key
        self.sender = e164_phone(config.get('business_phone_e164'))
        self.timeout = max(int(settings.SITEOS_WHATSAPP_HTTP_TIMEOUT_SECONDS), 1)

    def _request(self, *, url: str, body: bytes, content_type: str) -> tuple[dict[str, Any], str]:
        require_whatsapp_external_io()
        request = urllib.request.Request(
            url,
            data=body,
            method='POST',
            headers={
                'Accept': 'application/json',
                'Content-Type': content_type,
                'X-API-Key': self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(5 * 1024 * 1024 + 1)
                request_id = str(
                    response.headers.get('x-request-id')
                    or response.headers.get('request-id')
                    or ''
                )[:255]
        except urllib.error.HTTPError as exc:
            raw = exc.read(1024 * 1024 + 1)
            try:
                parsed = json.loads(raw.decode('utf-8', errors='replace') or '{}')
            except json.JSONDecodeError:
                parsed = {}
            code, message = _safe_error(parsed if isinstance(parsed, dict) else {}, f'YCloud API HTTP {exc.code}')
            raise WhatsAppCloudError(
                message,
                code=code or f'http_{exc.code}',
                http_status=exc.code,
                retryable=exc.code == 429 or 500 <= exc.code <= 599,
                request_id=str(exc.headers.get('x-request-id') or '')[:255],
            ) from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise WhatsAppCloudError(
                'YCloud API 连接结果不确定，需要人工核对后再重试。',
                code='ambiguous_transport',
                ambiguous=True,
            ) from exc
        if len(raw) > 5 * 1024 * 1024:
            raise WhatsAppCloudError('YCloud API 响应过大。', code='response_too_large', ambiguous=True)
        try:
            parsed = json.loads(raw.decode('utf-8', errors='strict') or '{}')
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WhatsAppCloudError(
                'YCloud API 返回了无法解析的响应。',
                code='invalid_json',
                ambiguous=True,
                request_id=request_id,
            ) from exc
        if not isinstance(parsed, dict) or parsed.get('error'):
            code, message = _safe_error(parsed if isinstance(parsed, dict) else {}, 'YCloud API 返回异常。')
            raise WhatsAppCloudError(message, code=code, request_id=request_id)
        return parsed, request_id

    def upload_media(self, *, path: Path, mime_type: str, filename: str) -> WhatsAppMediaUploadResult:
        boundary = f'----siteos-ycloud-{uuid.uuid4().hex}'
        safe_filename = Path(filename).name.replace('"', '').replace('\r', '').replace('\n', '')
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise WhatsAppCloudError('无法读取待上传的 WhatsApp 私有附件。', code='media_read_error') from exc
        body = b''.join([
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{safe_filename}"\r\nContent-Type: {mime_type}\r\n\r\n'
            ).encode('utf-8'),
            content,
            f'\r\n--{boundary}--\r\n'.encode('ascii'),
        ])
        parsed, request_id = self._request(
            url=(
                f'{YCLOUD_API_BASE}/whatsapp/media/'
                f'{urllib.parse.quote(self.sender, safe="")}/upload'
            ),
            body=body,
            content_type=f'multipart/form-data; boundary={boundary}',
        )
        media_id = str(parsed.get('id') or parsed.get('mediaId') or '').strip()
        if not media_id:
            raise WhatsAppCloudError('YCloud 媒体上传响应缺少 Media ID。', request_id=request_id)
        return WhatsAppMediaUploadResult(media_id, request_id)

    def send_message(self, *, payload: dict[str, Any], idempotency_key: str) -> WhatsAppCloudResult:
        outgoing = {
            key: value
            for key, value in payload.items()
            if key not in {'messaging_product', 'recipient_type'}
        }
        outgoing['from'] = self.sender
        recipient = normalize_phone_identity(outgoing.get('to'))
        if re.fullmatch(r'[A-Z]{2}\.[A-Za-z0-9._-]+', recipient):
            outgoing.pop('to', None)
            outgoing['recipient'] = recipient
        else:
            outgoing['to'] = e164_phone(recipient)
        outgoing['externalId'] = str(idempotency_key)[:255]
        outgoing['filterBlocked'] = True
        outgoing['filterUnsubscribed'] = True
        parsed, request_id = self._request(
            url=f'{YCLOUD_API_BASE}/whatsapp/messages',
            body=json.dumps(outgoing, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
            content_type='application/json; charset=utf-8',
        )
        message_id = str(parsed.get('id') or '').strip()
        if not message_id:
            raise WhatsAppCloudError(
                'YCloud 发送响应缺少 Message ID，结果需要人工核对。',
                code='missing_message_id',
                ambiguous=True,
                request_id=request_id,
            )
        return WhatsAppCloudResult(message_id, request_id, parsed)
