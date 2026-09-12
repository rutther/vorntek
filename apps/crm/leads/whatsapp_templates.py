from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from leads.inbound import integration_secret
from leads.models import WhatsAppTemplate
from leads.services import meta_api_version
from leads.whatsapp_cloud import WhatsAppCloudError, WhatsAppPolicyError, require_whatsapp_external_io


ALLOWED_TEMPLATE_CATEGORIES = {'authentication', 'marketing', 'utility'}
ALLOWED_TEMPLATE_STATUSES = {'pending', 'approved', 'paused', 'disabled', 'rejected', 'deleted'}
STATUS_MAP = {
    'PENDING': 'pending',
    'APPROVED': 'approved',
    'PAUSED': 'paused',
    'DISABLED': 'disabled',
    'REJECTED': 'rejected',
    'DELETED': 'deleted',
    'IN_APPEAL': 'pending',
    'PENDING_DELETION': 'deleted',
    'ARCHIVED': 'deleted',
}


@dataclass(frozen=True)
class TemplateSyncResult:
    received: int
    created: int
    updated: int


def _graph_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value or '').strip())
    if parsed.scheme != 'https' or str(parsed.hostname or '').lower() != 'graph.facebook.com':
        raise WhatsAppCloudError('模板分页地址不是受信任的 Meta Graph URL。', code='unsafe_paging_url')
    return parsed.geturl()


def _normalized_template(raw: dict) -> dict:
    name = str(raw.get('name') or '').strip()
    language = str(raw.get('language') or '').strip().replace('-', '_')
    category = str(raw.get('category') or '').strip().lower()
    status = STATUS_MAP.get(str(raw.get('status') or '').strip().upper(), 'pending')
    if not name or not language:
        raise WhatsAppCloudError('Meta 模板缺少名称或语言。', code='invalid_template')
    if category not in ALLOWED_TEMPLATE_CATEGORIES:
        raise WhatsAppCloudError('Meta 模板分类不受支持。', code='invalid_template_category')
    if status not in ALLOWED_TEMPLATE_STATUSES:
        status = 'pending'
    components = raw.get('components') if isinstance(raw.get('components'), list) else []
    return {
        'name': name[:512],
        'language': language[:32],
        'category': category,
        'status': status,
        'provider_template_id': str(raw.get('id') or '').strip()[:512],
        'components_json': components,
        'quality_rating': str(raw.get('quality_score') or raw.get('quality_rating') or '').strip()[:120],
        'rejection_reason': str(raw.get('rejected_reason') or raw.get('rejection_reason') or '').strip()[:1000],
    }


def fetch_whatsapp_templates(integration) -> list[dict]:
    mode = str((integration.config_json or {}).get('delivery_mode') or 'mock').strip().lower()
    if mode == 'mock':
        fixtures = (integration.config_json or {}).get('mock_templates') or []
        return [item for item in fixtures if isinstance(item, dict)]
    if mode != 'live':
        raise WhatsAppPolicyError('WhatsApp delivery_mode 必须是 mock 或 live。')
    require_whatsapp_external_io()
    from leads.whatsapp_ycloud import whatsapp_transport_provider

    if whatsapp_transport_provider(integration) == 'ycloud':
        return _fetch_ycloud_templates(integration)
    token = integration_secret(integration, 'primary')
    if not token:
        raise WhatsAppPolicyError('WhatsApp Access Token 未配置。')
    waba_id = str((integration.config_json or {}).get('waba_id') or '').strip()
    if not waba_id:
        raise WhatsAppPolicyError('WhatsApp Business Account ID 未配置。')
    fields = 'id,name,language,category,status,components,quality_score,rejected_reason'
    url = _graph_url(
        f'https://graph.facebook.com/{meta_api_version(integration)}/'
        f'{urllib.parse.quote(waba_id, safe="")}/message_templates?'
        f'{urllib.parse.urlencode({"fields": fields, "limit": 100})}'
    )
    records = []
    for _page in range(20):
        request = urllib.request.Request(
            url,
            headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(5 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise WhatsAppCloudError(
                f'Meta 模板读取失败，HTTP {exc.code}。',
                code=f'http_{exc.code}',
                http_status=exc.code,
                retryable=exc.code == 429 or 500 <= exc.code <= 599,
            ) from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise WhatsAppCloudError('Meta 模板读取暂时失败。', code='template_transport', retryable=True) from exc
        if len(raw) > 5 * 1024 * 1024:
            raise WhatsAppCloudError('Meta 模板响应过大。', code='template_response_too_large')
        try:
            payload = json.loads(raw.decode('utf-8', errors='strict') or '{}')
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WhatsAppCloudError('Meta 模板响应无法解析。', code='invalid_template_response') from exc
        if not isinstance(payload, dict) or payload.get('error'):
            raise WhatsAppCloudError('Meta 模板响应异常。', code='invalid_template_response')
        records.extend(item for item in payload.get('data', []) if isinstance(item, dict))
        paging = payload.get('paging') if isinstance(payload.get('paging'), dict) else {}
        next_url = str(paging.get('next') or '').strip()
        if not next_url:
            return records
        url = _graph_url(next_url)
    raise WhatsAppCloudError('Meta 模板分页超过安全上限。', code='template_paging_limit')


def _fetch_ycloud_templates(integration) -> list[dict]:
    require_whatsapp_external_io()
    api_key = integration_secret(integration, 'primary')
    if not api_key:
        raise WhatsAppPolicyError('YCloud API Key 未配置。')
    waba_id = str((integration.config_json or {}).get('waba_id') or '').strip()
    if not waba_id:
        raise WhatsAppPolicyError('WhatsApp Business Account ID 未配置。')
    records: list[dict] = []
    for page in range(1, 101):
        query = urllib.parse.urlencode({
            'page': page,
            'limit': 100,
            'includeTotal': 'true',
            'filter.wabaId': waba_id,
        })
        request = urllib.request.Request(
            f'https://api.ycloud.com/v2/whatsapp/templates?{query}',
            headers={'Accept': 'application/json', 'X-API-Key': api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(5 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise WhatsAppCloudError(
                f'YCloud 模板读取失败，HTTP {exc.code}。',
                code=f'http_{exc.code}',
                http_status=exc.code,
                retryable=exc.code == 429 or 500 <= exc.code <= 599,
            ) from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise WhatsAppCloudError('YCloud 模板读取暂时失败。', code='template_transport', retryable=True) from exc
        if len(raw) > 5 * 1024 * 1024:
            raise WhatsAppCloudError('YCloud 模板响应过大。', code='template_response_too_large')
        try:
            payload = json.loads(raw.decode('utf-8', errors='strict') or '{}')
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WhatsAppCloudError('YCloud 模板响应无法解析。', code='invalid_template_response') from exc
        if isinstance(payload, list):
            page_records = [item for item in payload if isinstance(item, dict)]
            total = None
        elif isinstance(payload, dict) and not payload.get('error'):
            candidates = payload.get('items')
            if not isinstance(candidates, list):
                candidates = payload.get('data')
            if not isinstance(candidates, list):
                candidates = payload.get('records')
            page_records = [item for item in (candidates or []) if isinstance(item, dict)]
            total = payload.get('total')
        else:
            raise WhatsAppCloudError('YCloud 模板响应异常。', code='invalid_template_response')
        records.extend(page_records)
        if len(page_records) < 100:
            return records
        try:
            if total is not None and len(records) >= int(total):
                return records
        except (TypeError, ValueError):
            pass
    raise WhatsAppCloudError('YCloud 模板分页超过安全上限。', code='template_paging_limit')


@transaction.atomic
def sync_whatsapp_templates(*, integration, raw_templates=None, now=None) -> TemplateSyncResult:
    now = now or timezone.now()
    records = list(raw_templates if raw_templates is not None else fetch_whatsapp_templates(integration))
    created = updated = 0
    for raw in records:
        item = _normalized_template(raw)
        latest = (
            WhatsAppTemplate.objects.select_for_update()
            .filter(integration=integration, name=item['name'], language=item['language'])
            .order_by('-version', '-id')
            .first()
        )
        structural_change = bool(
            latest
            and (
                latest.category != item['category']
                or latest.components_json != item['components_json']
                or latest.provider_template_id != item['provider_template_id']
            )
        )
        if latest is None or structural_change:
            WhatsAppTemplate.objects.create(
                integration=integration,
                version=(latest.version + 1 if latest else 1),
                synced_at=now,
                **item,
            )
            created += 1
        else:
            for field, value in item.items():
                setattr(latest, field, value)
            latest.synced_at = now
            latest.save(update_fields=[*item.keys(), 'synced_at', 'updated_at'])
            updated += 1
    return TemplateSyncResult(received=len(records), created=created, updated=updated)
