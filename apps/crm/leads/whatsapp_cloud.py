from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from leads.inbound import integration_secret
from leads.models import (
    ConsentRecord,
    WhatsAppConversation,
    WhatsAppDeliveryEvent,
    WhatsAppMedia,
    WhatsAppMessage,
    WhatsAppTemplate,
)
from leads.services import meta_api_version


logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_RATE_LIMIT_PER_MINUTE = 60
ALLOWED_OUTBOUND_MEDIA = {
    'image': {
        'image/jpeg', 'image/png', 'image/webp',
    },
    'video': {
        'video/mp4', 'video/3gpp',
    },
    'audio': {
        'audio/aac', 'audio/mp4', 'audio/mpeg', 'audio/amr',
        'audio/ogg', 'audio/opus',
    },
    'document': {
        'application/pdf', 'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-powerpoint',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'text/plain',
    },
}


class WhatsAppPolicyError(ValueError):
    pass


def require_whatsapp_external_io() -> None:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        raise WhatsAppPolicyError('当前环境禁止外部连接；WhatsApp 保持暂停。')


class WhatsAppIdempotencyConflict(WhatsAppPolicyError):
    pass


class WhatsAppCloudError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = '',
        http_status: int | None = None,
        retryable: bool = False,
        ambiguous: bool = False,
        request_id: str = '',
    ):
        super().__init__(message)
        self.code = str(code or '')[:120]
        self.http_status = http_status
        self.retryable = bool(retryable and not ambiguous)
        self.ambiguous = ambiguous
        self.request_id = str(request_id or '')[:255]


@dataclass(frozen=True)
class WhatsAppCloudResult:
    external_message_id: str
    request_id: str = ''
    response_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class WhatsAppMediaUploadResult:
    external_media_id: str
    request_id: str = ''


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _safe_error_message(payload: dict[str, Any], fallback: str) -> tuple[str, str]:
    error = payload.get('error') if isinstance(payload.get('error'), dict) else {}
    code = str(error.get('code') or error.get('error_subcode') or '')[:120]
    message = str(error.get('message') or fallback).strip()[:1000]
    return code, message


def _latest_consent_decision(conversation: WhatsAppConversation, purpose: str) -> str:
    if not conversation.submission_id:
        return ''
    record = (
        ConsentRecord.objects.filter(
            submission_id=conversation.submission_id,
            purpose=purpose,
        )
        .order_by('-captured_at', '-id')
        .first()
    )
    return str(record.decision if record else '').strip().lower()


def _assert_conversation_contactable(conversation: WhatsAppConversation) -> None:
    if conversation.status == 'blocked' or conversation.opted_out_at:
        raise WhatsAppPolicyError('客户已停止联系或会话已被阻止。')
    if conversation.status == 'closed':
        raise WhatsAppPolicyError('会话已关闭；请先重新打开再发送。')
    if conversation.contact_id and conversation.contact.status == 'do_not_contact':
        raise WhatsAppPolicyError('联系人已标记为禁止联系。')
    if conversation.submission_id and conversation.submission.stage == 'spam':
        raise WhatsAppPolicyError('垃圾线索不能发送 WhatsApp 消息。')


def _assert_send_policy(
    *,
    conversation: WhatsAppConversation,
    template: WhatsAppTemplate | None,
    now,
) -> None:
    _assert_conversation_contactable(conversation)
    inside_window = bool(
        conversation.service_window_expires_at
        and conversation.service_window_expires_at > now
    )
    if template is None and not inside_window:
        raise WhatsAppPolicyError('24 小时客服窗口已关闭；必须改用已批准模板。')
    if template is not None:
        if template.integration_id != conversation.integration_id:
            raise WhatsAppPolicyError('模板不属于当前 WhatsApp 接入。')
        if template.status != 'approved':
            raise WhatsAppPolicyError('只能发送 Meta 已批准的模板。')
        if template.category == 'marketing':
            if _latest_consent_decision(conversation, 'marketing') != 'granted':
                raise WhatsAppPolicyError('客户没有有效营销同意，禁止主动营销。')


def _assert_reply_target(
    conversation: WhatsAppConversation,
    reply_to: WhatsAppMessage | None,
) -> None:
    if reply_to is not None and reply_to.conversation_id != conversation.id:
        raise WhatsAppPolicyError('只能回复当前会话中的消息。')


def _safe_private_media_path(path_value: str) -> Path:
    media_root = Path(settings.SITEOS_WHATSAPP_MEDIA_ROOT).resolve()
    candidate = Path(path_value).resolve()
    try:
        candidate.relative_to(media_root)
    except ValueError as exc:
        raise WhatsAppPolicyError('附件不在 WhatsApp 私有媒体目录内。') from exc
    if not candidate.is_file():
        raise WhatsAppPolicyError('附件文件不存在。')
    if candidate.stat().st_size <= 0:
        raise WhatsAppPolicyError('附件文件为空。')
    if candidate.stat().st_size > settings.SITEOS_WHATSAPP_MEDIA_MAX_BYTES:
        raise WhatsAppPolicyError('附件超过允许大小。')
    return candidate


def _validate_media_input(
    *,
    media_type: str,
    media_path: str,
    mime_type: str,
) -> tuple[Path, str]:
    normalized_type = str(media_type or '').strip().lower()
    if normalized_type not in ALLOWED_OUTBOUND_MEDIA:
        raise WhatsAppPolicyError('不支持的 WhatsApp 附件类型。')
    path = _safe_private_media_path(media_path)
    normalized_mime = str(mime_type or '').strip().lower()
    if not normalized_mime:
        normalized_mime = str(mimetypes.guess_type(path.name)[0] or '').lower()
    if normalized_mime not in ALLOWED_OUTBOUND_MEDIA[normalized_type]:
        raise WhatsAppPolicyError('附件 MIME 类型不在允许列表中。')
    return path, normalized_mime


def _request_fingerprint(
    *,
    conversation_id: int,
    message_type: str,
    body: str,
    template_id: int | None,
    template_parameters: list[dict[str, Any]],
    reply_to_id: int | None,
    media: dict[str, Any] | None,
) -> str:
    return _canonical_hash({
        'conversation_id': conversation_id,
        'message_type': message_type,
        'body': body,
        'template_id': template_id,
        'template_parameters': template_parameters,
        'reply_to_id': reply_to_id,
        'media': media or {},
    })


@transaction.atomic
def queue_whatsapp_message(
    *,
    conversation: WhatsAppConversation,
    actor,
    idempotency_token: str,
    body: str = '',
    template: WhatsAppTemplate | None = None,
    template_parameters: list[dict[str, Any]] | None = None,
    reply_to: WhatsAppMessage | None = None,
    media_type: str = '',
    media_path: str = '',
    mime_type: str = '',
    original_name: str = '',
    now=None,
) -> tuple[WhatsAppMessage, bool]:
    """Validate policy and durably queue one outbound message.

    The same idempotency token and same request returns the original row. Token
    reuse with different content fails closed.
    """

    effective_now = now or timezone.now()
    conversation = (
        WhatsAppConversation.objects.select_for_update(of=('self',))
        .select_related('integration', 'submission', 'contact')
        .get(pk=conversation.pk)
    )
    if not conversation.integration.enabled:
        raise WhatsAppPolicyError('WhatsApp 接入未启用。')
    _assert_reply_target(conversation, reply_to)
    _assert_send_policy(
        conversation=conversation,
        template=template,
        now=effective_now,
    )

    normalized_body = str(body or '').strip()
    parameters = list(template_parameters or [])
    message_type = 'template' if template is not None else 'text'
    media_data = None
    validated_media = None
    if media_type or media_path:
        if template is not None:
            raise WhatsAppPolicyError('当前版本不允许把本地附件直接附加到模板消息。')
        path, normalized_mime = _validate_media_input(
            media_type=media_type,
            media_path=media_path,
            mime_type=mime_type,
        )
        message_type = str(media_type).strip().lower()
        media_data = {
            'media_type': message_type,
            'storage_path': str(path),
            'mime_type': normalized_mime,
            'original_name': str(original_name or path.name).strip()[:512],
            'file_size_bytes': path.stat().st_size,
        }
        validated_media = (path, normalized_mime)
    elif template is None and not normalized_body:
        raise WhatsAppPolicyError('消息正文不能为空。')
    if len(normalized_body) > 4096:
        raise WhatsAppPolicyError('消息正文超过 4096 个字符。')

    token = str(idempotency_token or '').strip()
    if len(token) < 16 or len(token) > 255:
        raise WhatsAppPolicyError('幂等令牌必须为 16–255 个字符。')
    idempotency_key = hashlib.sha256(
        f'{conversation.site_id}|{conversation.id}|{token}'.encode('utf-8')
    ).hexdigest()
    request_fingerprint = _request_fingerprint(
        conversation_id=conversation.id,
        message_type=message_type,
        body=normalized_body,
        template_id=template.id if template else None,
        template_parameters=parameters,
        reply_to_id=reply_to.id if reply_to else None,
        media=media_data,
    )
    existing = WhatsAppMessage.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        if existing.request_fingerprint != request_fingerprint:
            raise WhatsAppIdempotencyConflict('幂等令牌已用于不同的消息请求。')
        return existing, False

    payload_json = {
        'template_parameters': parameters,
        'delivery_mode': str(
            (conversation.integration.config_json or {}).get('delivery_mode') or 'mock'
        ).strip().lower(),
        'transport_provider': str(
            (conversation.integration.config_json or {}).get('transport_provider') or 'meta'
        ).strip().lower(),
    }
    message = WhatsAppMessage.objects.create(
        conversation=conversation,
        template=template,
        reply_to=reply_to,
        actor_user=actor if getattr(actor, 'is_authenticated', False) else None,
        direction='outbound',
        message_type=message_type,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        sender_id=conversation.integration.public_id,
        recipient_id=conversation.external_contact_id,
        body=normalized_body,
        status='queued',
        payload_json=payload_json,
        queued_at=effective_now,
    )
    if validated_media is not None and media_data is not None:
        WhatsAppMedia.objects.create(
            message=message,
            media_type=media_data['media_type'],
            mime_type=media_data['mime_type'],
            original_name=media_data['original_name'],
            file_size_bytes=media_data['file_size_bytes'],
            storage_path=media_data['storage_path'],
            status='pending',
        )
    return message, True


class MockWhatsAppCloudClient:
    """Deterministic, network-free Meta simulator used unless live is explicit."""

    def upload_media(self, *, path: Path, mime_type: str, filename: str) -> WhatsAppMediaUploadResult:
        digest = hashlib.sha256(
            f'{path.resolve()}|{path.stat().st_size}|{mime_type}'.encode('utf-8')
        ).hexdigest()
        return WhatsAppMediaUploadResult(
            external_media_id=f'media.mock.{digest[:28]}',
            request_id=f'mock-upload-{digest[:16]}',
        )

    def send_message(self, *, payload: dict[str, Any], idempotency_key: str) -> WhatsAppCloudResult:
        digest = hashlib.sha256(
            f'{idempotency_key}|{_canonical_hash(payload)}'.encode('utf-8')
        ).hexdigest()
        return WhatsAppCloudResult(
            external_message_id=f'wamid.mock.{digest[:32]}',
            request_id=f'mock-send-{digest[:16]}',
            response_json={'messages': [{'id': f'wamid.mock.{digest[:32]}'}], 'mock': True},
        )


class GraphWhatsAppCloudClient:
    def __init__(self, *, integration):
        require_whatsapp_external_io()
        token = integration_secret(integration, 'primary')
        if not token:
            raise WhatsAppPolicyError('WhatsApp Access Token 未配置。')
        self.token = token
        self.phone_number_id = str(integration.public_id or '').strip()
        self.api_version = meta_api_version(integration)
        self.timeout = max(int(settings.SITEOS_WHATSAPP_HTTP_TIMEOUT_SECONDS), 1)

    def _request(
        self,
        *,
        url: str,
        body: bytes,
        content_type: str,
    ) -> tuple[dict[str, Any], str]:
        require_whatsapp_external_io()
        request = urllib.request.Request(
            url,
            data=body,
            method='POST',
            headers={
                'Accept': 'application/json',
                'Authorization': f'Bearer {self.token}',
                'Content-Type': content_type,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode('utf-8', errors='replace')
                request_id = str(
                    response.headers.get('x-fb-trace-id')
                    or response.headers.get('x-fb-request-id')
                    or ''
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode('utf-8', errors='replace')
            try:
                parsed = json.loads(raw or '{}')
            except json.JSONDecodeError:
                parsed = {}
            code, message = _safe_error_message(parsed, f'Meta API HTTP {exc.code}')
            raise WhatsAppCloudError(
                message,
                code=code,
                http_status=exc.code,
                retryable=exc.code == 429 or 500 <= exc.code <= 599,
                request_id=str(exc.headers.get('x-fb-trace-id') or ''),
            ) from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            # A transport failure may occur after Meta accepted the request.
            # Retrying automatically could duplicate a customer-visible send.
            raise WhatsAppCloudError(
                'Meta API 连接结果不确定，需要人工核对后再重试。',
                code='ambiguous_transport',
                ambiguous=True,
            ) from exc
        try:
            parsed = json.loads(raw or '{}')
        except json.JSONDecodeError as exc:
            raise WhatsAppCloudError(
                'Meta API 返回了无法解析的响应。',
                code='invalid_json',
                ambiguous=True,
                request_id=request_id,
            ) from exc
        if not isinstance(parsed, dict) or parsed.get('error'):
            code, message = _safe_error_message(parsed if isinstance(parsed, dict) else {}, 'Meta API 返回异常。')
            raise WhatsAppCloudError(message, code=code, request_id=request_id)
        return parsed, request_id

    def upload_media(self, *, path: Path, mime_type: str, filename: str) -> WhatsAppMediaUploadResult:
        boundary = f'----siteos-{uuid.uuid4().hex}'
        safe_filename = Path(filename).name.replace('"', '').replace('\r', '').replace('\n', '')
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'.encode(),
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{safe_filename}"\r\nContent-Type: {mime_type}\r\n\r\n'
            ).encode('utf-8'),
            b'',
            f'\r\n--{boundary}--\r\n'.encode(),
        ]
        try:
            parts[2] = path.read_bytes()
        except OSError as exc:
            raise WhatsAppCloudError(
                '无法读取待上传的 WhatsApp 私有附件。',
                code='media_read_error',
            ) from exc
        parsed, request_id = self._request(
            url=f'https://graph.facebook.com/{self.api_version}/{urllib.parse.quote(self.phone_number_id)}/media',
            body=b''.join(parts),
            content_type=f'multipart/form-data; boundary={boundary}',
        )
        media_id = str(parsed.get('id') or '').strip()
        if not media_id:
            raise WhatsAppCloudError('Meta 媒体上传响应缺少 Media ID。', request_id=request_id)
        return WhatsAppMediaUploadResult(media_id, request_id)

    def send_message(self, *, payload: dict[str, Any], idempotency_key: str) -> WhatsAppCloudResult:
        parsed, request_id = self._request(
            url=f'https://graph.facebook.com/{self.api_version}/{urllib.parse.quote(self.phone_number_id)}/messages',
            body=json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
            content_type='application/json; charset=utf-8',
        )
        messages = parsed.get('messages') if isinstance(parsed.get('messages'), list) else []
        first = messages[0] if messages and isinstance(messages[0], dict) else {}
        message_id = str(first.get('id') or '').strip()
        if not message_id:
            raise WhatsAppCloudError(
                'Meta 发送响应缺少 Message ID，结果需要人工核对。',
                code='missing_message_id',
                ambiguous=True,
                request_id=request_id,
            )
        return WhatsAppCloudResult(message_id, request_id, parsed)


def client_for_integration(integration):
    mode = str((integration.config_json or {}).get('delivery_mode') or 'mock').strip().lower()
    if mode == 'mock':
        return MockWhatsAppCloudClient()
    if mode != 'live':
        raise WhatsAppPolicyError('WhatsApp delivery_mode 必须是 mock 或 live。')
    if not settings.SITEOS_WHATSAPP_ALLOW_LIVE_SEND:
        raise WhatsAppPolicyError('服务器未显式允许真实 WhatsApp 发送。')
    if settings.DEBUG:
        raise WhatsAppPolicyError('DEBUG 环境禁止真实 WhatsApp 发送。')
    from leads.whatsapp_ycloud import YCloudWhatsAppClient, whatsapp_transport_provider

    if whatsapp_transport_provider(integration) == 'ycloud':
        return YCloudWhatsAppClient(integration=integration)
    return GraphWhatsAppCloudClient(integration=integration)


def _message_payload(message: WhatsAppMessage, media: WhatsAppMedia | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': message.recipient_id,
        'type': message.message_type,
    }
    if message.reply_to_id and message.reply_to.external_message_id:
        reply_identity = str(
            (message.reply_to.payload_json or {}).get('wamid')
            or message.reply_to.external_message_id
        ).strip()
        payload['context'] = {'message_id': reply_identity}
    if message.message_type == 'text':
        payload['text'] = {'preview_url': False, 'body': message.body}
    elif message.message_type == 'template':
        parameters = list((message.payload_json or {}).get('template_parameters') or [])
        payload['template'] = {
            'name': message.template.name,
            'language': {'code': message.template.language},
            'components': parameters,
        }
    elif media is not None:
        if not media.external_media_id:
            raise WhatsAppPolicyError('附件尚未取得 Meta Media ID。')
        payload[message.message_type] = {'id': media.external_media_id}
        if message.body and message.message_type in {'image', 'video', 'document'}:
            payload[message.message_type]['caption'] = message.body
        if media.original_name and message.message_type == 'document':
            payload[message.message_type]['filename'] = media.original_name
    else:
        raise WhatsAppPolicyError('消息缺少可发送内容。')
    return payload


def _delivery_fingerprint(message_id: int, status: str, occurred_at, request_id: str) -> str:
    return _canonical_hash({
        'message_id': message_id,
        'status': status,
        'occurred_at': occurred_at.isoformat(),
        'request_id': request_id,
    })


def _rate_limit_delay(message: WhatsAppMessage, now) -> bool:
    config = dict(message.conversation.integration.config_json or {})
    limit = max(int(config.get('max_messages_per_minute') or DEFAULT_RATE_LIMIT_PER_MINUTE), 1)
    recent_count = WhatsAppMessage.objects.filter(
        conversation__integration_id=message.conversation.integration_id,
        direction='outbound',
        sent_at__gte=now - timedelta(minutes=1),
    ).count()
    if recent_count < limit:
        return False
    message.status = 'queued'
    message.next_attempt_at = now + timedelta(seconds=60)
    message.updated_at = now
    message.save(update_fields=['status', 'next_attempt_at', 'updated_at'])
    return True


def dispatch_whatsapp_message(*, message_id: int, client=None, now=None) -> tuple[WhatsAppMessage, bool]:
    effective_now = now or timezone.now()
    with transaction.atomic():
        message = (
            WhatsAppMessage.objects.select_for_update(of=('self',))
            .select_related(
                'conversation__integration', 'conversation__submission',
                'conversation__contact', 'template', 'reply_to',
            )
            .get(pk=message_id)
        )
        if message.status in {'sent', 'delivered', 'read'}:
            return message, False
        if message.direction != 'outbound':
            raise WhatsAppPolicyError('只能派发出站 WhatsApp 消息。')
        if message.status == 'failed' and not message.retryable:
            raise WhatsAppPolicyError('该失败结果不可自动重试，需要人工核对。')
        if message.status not in {'queued', 'failed'}:
            return message, False
        if message.next_attempt_at and message.next_attempt_at > effective_now:
            return message, False
        max_attempts = max(
            int((message.conversation.integration.config_json or {}).get('max_send_attempts') or DEFAULT_MAX_ATTEMPTS),
            1,
        )
        if message.attempts >= max_attempts:
            message.status = 'failed'
            message.retryable = False
            message.failed_at = effective_now
            message.error_code = 'attempt_limit'
            message.error_message = f'已达到最多 {max_attempts} 次发送上限。'
            message.next_attempt_at = None
            message.updated_at = effective_now
            message.save(update_fields=[
                'status', 'retryable', 'failed_at', 'error_code', 'error_message',
                'next_attempt_at', 'updated_at',
            ])
            return message, False
        try:
            _assert_send_policy(
                conversation=message.conversation,
                template=message.template,
                now=effective_now,
            )
        except WhatsAppPolicyError as exc:
            message.status = 'failed'
            message.failed_at = effective_now
            message.error_code = 'policy'
            message.error_message = str(exc)[:1000]
            message.retryable = False
            message.next_attempt_at = None
            message.updated_at = effective_now
            message.save(update_fields=[
                'status', 'failed_at', 'error_code', 'error_message',
                'retryable', 'next_attempt_at', 'updated_at',
            ])
            return message, False
        if _rate_limit_delay(message, effective_now):
            return message, False
        media = message.media_items.first()
        message.status = 'sending'
        message.retryable = False
        message.attempts += 1
        message.next_attempt_at = None
        message.updated_at = effective_now
        message.save(update_fields=[
            'status', 'retryable', 'attempts', 'next_attempt_at', 'updated_at',
        ])
        integration = message.conversation.integration

    try:
        selected_client = client or client_for_integration(integration)
        if media is not None and not media.external_media_id:
            path = _safe_private_media_path(media.storage_path)
            upload = selected_client.upload_media(
                path=path,
                mime_type=media.mime_type,
                filename=media.original_name or path.name,
            )
            with transaction.atomic():
                locked_media = WhatsAppMedia.objects.select_for_update().get(pk=media.pk)
                locked_media.external_media_id = upload.external_media_id
                locked_media.status = 'ready'
                locked_media.updated_at = timezone.now()
                locked_media.save(update_fields=['external_media_id', 'status', 'updated_at'])
                media = locked_media
        payload = _message_payload(message, media)
        result = selected_client.send_message(
            payload=payload,
            idempotency_key=message.idempotency_key,
        )
    except WhatsAppCloudError as exc:
        failed_at = timezone.now()
        with transaction.atomic():
            failed = WhatsAppMessage.objects.select_for_update().get(pk=message_id)
            failed.status = 'failed'
            failed.failed_at = failed_at
            failed.error_code = exc.code or 'cloud_api_error'
            failed.error_message = str(exc)[:1000]
            failed.provider_request_id = exc.request_id
            failed.retryable = bool(exc.retryable and failed.attempts < max_attempts)
            failed.next_attempt_at = (
                failed_at + timedelta(seconds=min(60 * (2 ** max(failed.attempts - 1, 0)), 3600))
                if failed.retryable else None
            )
            failed.updated_at = failed_at
            failed.save(update_fields=[
                'status', 'failed_at', 'error_code', 'error_message',
                'provider_request_id', 'retryable', 'next_attempt_at', 'updated_at',
            ])
        logger.warning(
            'WhatsApp send failed message=%s code=%s retryable=%s ambiguous=%s',
            message_id,
            exc.code,
            exc.retryable,
            exc.ambiguous,
        )
        return failed, False
    except WhatsAppPolicyError as exc:
        failed_at = timezone.now()
        with transaction.atomic():
            failed = WhatsAppMessage.objects.select_for_update().get(pk=message_id)
            failed.status = 'failed'
            failed.failed_at = failed_at
            failed.error_code = 'policy'
            failed.error_message = str(exc)[:1000]
            failed.retryable = False
            failed.next_attempt_at = None
            failed.updated_at = failed_at
            failed.save(update_fields=[
                'status', 'failed_at', 'error_code', 'error_message',
                'retryable', 'next_attempt_at', 'updated_at',
            ])
        return failed, False

    sent_at = timezone.now()
    with transaction.atomic():
        sent = (
            WhatsAppMessage.objects.select_for_update(of=('self',))
            .select_related('conversation')
            .get(pk=message_id)
        )
        if sent.external_message_id and sent.external_message_id != result.external_message_id:
            raise WhatsAppIdempotencyConflict('消息已有不同的 Meta Message ID。')
        sent.external_message_id = result.external_message_id
        sent.provider_request_id = result.request_id
        sent.status = 'sent'
        sent.sent_at = sent_at
        sent.failed_at = None
        sent.error_code = ''
        sent.error_message = ''
        sent.retryable = False
        sent.next_attempt_at = None
        sent.updated_at = sent_at
        sent.save(update_fields=[
            'external_message_id', 'provider_request_id', 'status', 'sent_at',
            'failed_at', 'error_code', 'error_message', 'retryable',
            'next_attempt_at', 'updated_at',
        ])
        WhatsAppDeliveryEvent.objects.get_or_create(
            event_fingerprint=_delivery_fingerprint(sent.id, 'sent', sent_at, result.request_id),
            defaults={
                'message': sent,
                'status': 'sent',
                'payload_json': {
                    'delivery_mode': str((sent.payload_json or {}).get('delivery_mode') or 'mock'),
                    'provider_response': result.response_json or {},
                },
                'occurred_at': sent_at,
                'received_at': sent_at,
            },
        )
        conversation = WhatsAppConversation.objects.select_for_update().get(pk=sent.conversation_id)
        conversation.last_outbound_at = sent_at
        if conversation.last_message_at is None or sent_at >= conversation.last_message_at:
            conversation.last_message_at = sent_at
            conversation.last_message_preview = sent.body or f'[{sent.message_type}]'
        conversation.updated_at = sent_at
        conversation.save(update_fields=[
            'last_outbound_at', 'last_message_at', 'last_message_preview', 'updated_at',
        ])
    logger.info('WhatsApp send accepted message=%s provider_request=%s', message_id, result.request_id)
    return sent, True


def dispatch_pending_whatsapp_messages(*, limit: int = 100) -> tuple[int, int]:
    effective_now = timezone.now()
    recover_stale_whatsapp_sends(now=effective_now)
    ids = list(
        WhatsAppMessage.objects.filter(
            direction='outbound',
        )
        .filter(
            Q(status='queued') | Q(status='failed', retryable=True),
        )
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=effective_now))
        .order_by('queued_at', 'id')
        .values_list('id', flat=True)[:max(min(int(limit), 1000), 1)]
    )
    attempted = 0
    succeeded = 0
    for message_id in ids:
        message, sent = dispatch_whatsapp_message(
            message_id=message_id,
            now=effective_now,
        )
        if message.status in {'sending', 'sent', 'failed'}:
            attempted += 1
        succeeded += int(sent)
    return attempted, succeeded


@transaction.atomic
def recover_stale_whatsapp_sends(*, now=None) -> int:
    effective_now = now or timezone.now()
    stale_before = effective_now - timedelta(
        minutes=max(int(getattr(settings, 'SITEOS_WHATSAPP_SEND_CLAIM_TIMEOUT_MINUTES', 5)), 1)
    )
    return WhatsAppMessage.objects.filter(
        direction='outbound',
        status='sending',
        updated_at__lte=stale_before,
    ).update(
        status='failed',
        failed_at=effective_now,
        error_code='stale_send_claim',
        error_message='发送进程中断，消息结果需要核对后重试。',
        retryable=False,
        next_attempt_at=None,
        updated_at=effective_now,
    )
