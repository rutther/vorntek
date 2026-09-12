from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from leads.inbound import integration_secret
from leads.models import WhatsAppMedia, WhatsAppMessage
from leads.services import meta_api_version
from leads.whatsapp_cloud import WhatsAppCloudError, WhatsAppPolicyError, require_whatsapp_external_io


logger = logging.getLogger(__name__)

ALLOWED_INBOUND_MEDIA = {
    'image': {'image/jpeg', 'image/png', 'image/webp'},
    'sticker': {'image/webp'},
    'video': {'video/mp4', 'video/3gpp'},
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
META_MEDIA_HOST_SUFFIXES = ('.facebook.com', '.fbcdn.net', '.fbsbx.com')
YCLOUD_MEDIA_HOST = 'api.ycloud.com'
YCLOUD_MEDIA_PATH_PREFIX = '/v2/whatsapp/media/download/'


@dataclass(frozen=True)
class WhatsAppMediaDownload:
    content: bytes
    mime_type: str
    original_name: str = ''


def _safe_meta_media_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value or '').strip())
    host = str(parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not host:
        raise WhatsAppCloudError('Meta 媒体地址不是受信任的 HTTPS URL。', code='unsafe_media_url')
    if host != 'graph.facebook.com' and not any(host.endswith(item) for item in META_MEDIA_HOST_SUFFIXES):
        raise WhatsAppCloudError('Meta 媒体地址主机不在允许列表。', code='unsafe_media_url')
    return parsed.geturl()


def _safe_ycloud_media_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value or '').strip())
    host = str(parsed.hostname or '').lower()
    if (
        parsed.scheme != 'https'
        or host != YCLOUD_MEDIA_HOST
        or not parsed.path.startswith(YCLOUD_MEDIA_PATH_PREFIX)
        or parsed.username
        or parsed.password
    ):
        raise WhatsAppCloudError('YCloud 媒体地址不在允许列表。', code='unsafe_media_url')
    return parsed.geturl()


class MockWhatsAppMediaClient:
    def download(
        self,
        *,
        external_media_id: str,
        expected_mime_type: str = '',
        download_url: str = '',
    ) -> WhatsAppMediaDownload:
        del download_url
        mime = expected_mime_type or 'image/jpeg'
        content = f'SITEOS MOCK WHATSAPP MEDIA {external_media_id}'.encode('utf-8')
        return WhatsAppMediaDownload(content=content, mime_type=mime, original_name='mock-media.bin')


class GraphWhatsAppMediaClient:
    def __init__(self, *, integration):
        require_whatsapp_external_io()
        token = integration_secret(integration, 'primary')
        if not token:
            raise WhatsAppPolicyError('WhatsApp Access Token 未配置。')
        self.token = token
        self.api_version = meta_api_version(integration)
        self.timeout = max(int(settings.SITEOS_WHATSAPP_HTTP_TIMEOUT_SECONDS), 1)

    def _open(self, request: urllib.request.Request):
        require_whatsapp_external_io()
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            raise WhatsAppCloudError(
                f'Meta 媒体读取失败，HTTP {exc.code}。',
                code=f'http_{exc.code}',
                http_status=exc.code,
                retryable=retryable,
            ) from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise WhatsAppCloudError(
                'Meta 媒体读取暂时失败。',
                code='media_transport',
                retryable=True,
            ) from exc

    def download(
        self,
        *,
        external_media_id: str,
        expected_mime_type: str = '',
        download_url: str = '',
    ) -> WhatsAppMediaDownload:
        del download_url
        media_id = urllib.parse.quote(str(external_media_id or '').strip(), safe='')
        if not media_id:
            raise WhatsAppCloudError('缺少 Meta Media ID。', code='missing_media_id')
        metadata_request = urllib.request.Request(
            f'https://graph.facebook.com/{self.api_version}/{media_id}',
            headers={'Accept': 'application/json', 'Authorization': f'Bearer {self.token}'},
        )
        with self._open(metadata_request) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise WhatsAppCloudError('Meta 媒体元数据响应过大。', code='metadata_too_large')
        try:
            metadata = json.loads(raw.decode('utf-8', errors='strict') or '{}')
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WhatsAppCloudError('Meta 媒体元数据无法解析。', code='invalid_media_metadata') from exc
        if not isinstance(metadata, dict) or metadata.get('error'):
            raise WhatsAppCloudError('Meta 媒体元数据返回异常。', code='invalid_media_metadata')
        media_url = _safe_meta_media_url(str(metadata.get('url') or ''))
        content_request = urllib.request.Request(
            media_url,
            headers={'Authorization': f'Bearer {self.token}'},
        )
        with self._open(content_request) as response:
            final_url = _safe_meta_media_url(str(response.geturl() or media_url))
            del final_url
            declared_length = int(response.headers.get('Content-Length') or 0)
            if declared_length > settings.SITEOS_WHATSAPP_MEDIA_MAX_BYTES:
                raise WhatsAppCloudError('WhatsApp 媒体超过允许大小。', code='media_too_large')
            content = response.read(settings.SITEOS_WHATSAPP_MEDIA_MAX_BYTES + 1)
            response_mime = str(response.headers.get_content_type() or '').lower()
        if not content or len(content) > settings.SITEOS_WHATSAPP_MEDIA_MAX_BYTES:
            raise WhatsAppCloudError('WhatsApp 媒体为空或超过允许大小。', code='media_too_large')
        return WhatsAppMediaDownload(
            content=content,
            mime_type=str(metadata.get('mime_type') or response_mime or expected_mime_type).lower(),
        )


class YCloudWhatsAppMediaClient:
    def __init__(self, *, integration):
        require_whatsapp_external_io()
        api_key = integration_secret(integration, 'primary')
        if not api_key:
            raise WhatsAppPolicyError('YCloud API Key 未配置。')
        self.api_key = api_key
        self.timeout = max(int(settings.SITEOS_WHATSAPP_HTTP_TIMEOUT_SECONDS), 1)

    def download(
        self,
        *,
        external_media_id: str,
        expected_mime_type: str = '',
        download_url: str = '',
    ) -> WhatsAppMediaDownload:
        del external_media_id
        require_whatsapp_external_io()
        media_url = _safe_ycloud_media_url(download_url)
        request = urllib.request.Request(
            media_url,
            headers={'Accept': '*/*', 'X-API-Key': self.api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                _safe_ycloud_media_url(str(response.geturl() or media_url))
                declared_length = int(response.headers.get('Content-Length') or 0)
                if declared_length > settings.SITEOS_WHATSAPP_MEDIA_MAX_BYTES:
                    raise WhatsAppCloudError('WhatsApp 媒体超过允许大小。', code='media_too_large')
                content = response.read(settings.SITEOS_WHATSAPP_MEDIA_MAX_BYTES + 1)
                response_mime = str(response.headers.get_content_type() or '').lower()
        except urllib.error.HTTPError as exc:
            raise WhatsAppCloudError(
                f'YCloud 媒体读取失败，HTTP {exc.code}。',
                code=f'http_{exc.code}',
                http_status=exc.code,
                retryable=exc.code == 429 or 500 <= exc.code <= 599,
            ) from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise WhatsAppCloudError(
                'YCloud 媒体读取暂时失败。',
                code='media_transport',
                retryable=True,
            ) from exc
        if not content or len(content) > settings.SITEOS_WHATSAPP_MEDIA_MAX_BYTES:
            raise WhatsAppCloudError('WhatsApp 媒体为空或超过允许大小。', code='media_too_large')
        return WhatsAppMediaDownload(
            content=content,
            mime_type=response_mime or expected_mime_type,
        )


def media_client_for_integration(integration):
    mode = str((integration.config_json or {}).get('delivery_mode') or 'mock').strip().lower()
    if mode == 'mock':
        return MockWhatsAppMediaClient()
    if mode != 'live':
        raise WhatsAppPolicyError('WhatsApp delivery_mode 必须是 mock 或 live。')
    from leads.whatsapp_ycloud import whatsapp_transport_provider

    if whatsapp_transport_provider(integration) == 'ycloud':
        return YCloudWhatsAppMediaClient(integration=integration)
    return GraphWhatsAppMediaClient(integration=integration)


def _validate_download(media: WhatsAppMedia, result: WhatsAppMediaDownload) -> tuple[str, str]:
    mime = str(result.mime_type or '').split(';', 1)[0].strip().lower()
    if mime not in ALLOWED_INBOUND_MEDIA.get(media.media_type, set()):
        raise WhatsAppCloudError('WhatsApp 媒体 MIME 类型不在允许列表。', code='unsupported_media_type')
    digest = hashlib.sha256(result.content).hexdigest()
    expected_digest = str(media.sha256 or '').strip().lower()
    if expected_digest and expected_digest != digest:
        raise WhatsAppCloudError('WhatsApp 媒体哈希校验失败。', code='media_hash_mismatch')
    return mime, digest


def _store_download(media: WhatsAppMedia, result: WhatsAppMediaDownload, digest: str) -> Path:
    root = Path(settings.SITEOS_WHATSAPP_MEDIA_ROOT).resolve()
    folder = root / 'inbound' / str(media.message_id)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(result.original_name or media.original_name or '').suffix.lower()[:16]
    destination = folder / f'{uuid.uuid4().hex}{suffix}'
    with destination.open('xb') as handle:
        handle.write(result.content)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        destination.unlink(missing_ok=True)
        raise WhatsAppCloudError('私有媒体落盘校验失败。', code='storage_hash_mismatch')
    return destination


def download_whatsapp_media(*, media_id: int, now=None) -> tuple[WhatsAppMedia, bool]:
    now = now or timezone.now()
    with transaction.atomic():
        media = (
            WhatsAppMedia.objects.select_for_update()
            .select_related('message__conversation__integration')
            .get(pk=media_id)
        )
        if media.status == 'ready':
            return media, False
        if media.status == 'deleted':
            raise WhatsAppPolicyError('媒体已按保留策略删除。')
        if media.status == 'downloading':
            return media, False
        media.status = 'downloading'
        media.attempts += 1
        media.error_message = ''
        media.next_attempt_at = None
        media.save(update_fields=['status', 'attempts', 'error_message', 'next_attempt_at', 'updated_at'])

    destination = None
    try:
        integration = media.message.conversation.integration
        content_payload = (
            (media.message.payload_json or {}).get('content')
            if isinstance((media.message.payload_json or {}).get('content'), dict)
            else {}
        )
        result = media_client_for_integration(integration).download(
            external_media_id=media.external_media_id,
            expected_mime_type=media.mime_type,
            download_url=str(content_payload.get('link') or ''),
        )
        mime, digest = _validate_download(media, result)
        destination = _store_download(media, result, digest)
        with transaction.atomic():
            media = WhatsAppMedia.objects.select_for_update().get(pk=media_id)
            media.mime_type = mime
            media.file_size_bytes = len(result.content)
            media.sha256 = digest
            media.storage_path = str(destination)
            media.status = 'ready'
            media.error_message = ''
            media.downloaded_at = now
            media.next_attempt_at = None
            media.save(update_fields=[
                'mime_type', 'file_size_bytes', 'sha256', 'storage_path', 'status',
                'error_message', 'downloaded_at', 'next_attempt_at', 'updated_at',
            ])
            message = WhatsAppMessage.objects.select_for_update().get(pk=media.message_id)
            message_payload = dict(message.payload_json or {})
            content_payload = (
                dict(message_payload.get('content'))
                if isinstance(message_payload.get('content'), dict)
                else {}
            )
            if content_payload.pop('link', None) is not None:
                message_payload['content'] = content_payload
                message.payload_json = message_payload
                message.save(update_fields=['payload_json', 'updated_at'])
        return media, True
    except Exception as exc:
        if destination is not None:
            destination.unlink(missing_ok=True)
        with transaction.atomic():
            media = WhatsAppMedia.objects.select_for_update().get(pk=media_id)
            retryable = isinstance(exc, WhatsAppCloudError) and exc.retryable
            exhausted = media.attempts >= settings.SITEOS_WHATSAPP_MEDIA_MAX_ATTEMPTS
            media.status = 'failed' if retryable and not exhausted else 'quarantined'
            media.error_message = str(exc)[:1000]
            media.next_attempt_at = (
                now + timedelta(minutes=min(2 ** media.attempts, 60))
                if retryable and not exhausted else None
            )
            media.save(update_fields=['status', 'error_message', 'next_attempt_at', 'updated_at'])
        logger.warning('WhatsApp media download failed media=%s retryable=%s', media_id, retryable)
        return media, False


def download_pending_whatsapp_media(*, limit: int = 25, now=None) -> dict[str, int]:
    now = now or timezone.now()
    ids = list(
        WhatsAppMedia.objects.filter(status__in=['pending', 'failed'])
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .order_by('created_at', 'id')
        .values_list('id', flat=True)[:max(1, min(int(limit), 250))]
    )
    result = {'selected': len(ids), 'downloaded': 0, 'failed': 0}
    for media_id in ids:
        media, downloaded = download_whatsapp_media(media_id=media_id, now=now)
        if downloaded:
            result['downloaded'] += 1
        elif media.status in {'failed', 'quarantined'}:
            result['failed'] += 1
    return result
