from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings
from django.utils import timezone

from leads.google_data_manager import GoogleDataManagerError, _load_google_credentials
from leads.inbound import configured_meta_leadgen_form_ids, integration_secret
from leads.services import META_DEFAULT_VERSION, resolve_meta_access_token
from marketing.models import MarketingIntegration
from marketing.public_config import normalize_google_tag_id


GRAPH_VERSION_RE = re.compile(r'^v\d+\.\d+$')
INBOUND_META_DEFAULT_VERSION = META_DEFAULT_VERSION
PLACEHOLDER_PREFIX = 'pending-'


def _safe_detail(value: Any, limit: int = 240) -> str:
    detail = re.sub(r'(?i)bearer\s+[a-z0-9._~-]+', 'Bearer [redacted]', str(value or '').strip())
    detail = re.sub(r'(?i)(access[_ -]?token|client[_ -]?secret)\s*[:=]\s*\S+', r'\1=[redacted]', detail)
    return detail[:limit]


def _check(code: str, label: str, status: str, detail: str, *, required: bool = True) -> dict[str, Any]:
    return {
        'code': code,
        'label': label,
        'status': status,
        'detail': _safe_detail(detail),
        'required': required,
    }


def _graph_get(url: str, token: str) -> dict[str, Any]:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return {'ok': False, 'http_status': None, 'error_type': 'external_io_disabled'}
    request = urllib.request.Request(
        url,
        headers={'Accept': 'application/json', 'Authorization': f'Bearer {token}'},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8', errors='replace')
            try:
                payload = json.loads(raw or '{}')
            except json.JSONDecodeError:
                payload = {}
            return {
                'ok': 200 <= response.status < 300 and isinstance(payload, dict) and not payload.get('error'),
                'http_status': response.status,
                'response_version': response.headers.get('facebook-api-version', ''),
                'payload': payload if isinstance(payload, dict) else {},
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            parsed = json.loads(raw or '{}')
        except json.JSONDecodeError:
            parsed = {}
        error = parsed.get('error') if isinstance(parsed, dict) and isinstance(parsed.get('error'), dict) else {}
        return {
            'ok': False,
            'http_status': exc.code,
            'response_version': exc.headers.get('facebook-api-version', ''),
            'error_code': error.get('code'),
            'error_subcode': error.get('error_subcode'),
            'error_type': _safe_detail(error.get('type')),
        }
    except Exception as exc:
        return {'ok': False, 'http_status': None, 'error_type': type(exc).__name__}


def _graph_detail(result: dict[str, Any], success: str) -> str:
    if result.get('ok'):
        version = str(result.get('response_version') or '').strip()
        return f'{success}；响应版本 {version}' if version else success
    parts = []
    if result.get('http_status') is not None:
        parts.append(f'HTTP {result["http_status"]}')
    if result.get('error_code') is not None:
        parts.append(f'错误码 {result["error_code"]}')
    if result.get('error_subcode') is not None:
        parts.append(f'子错误码 {result["error_subcode"]}')
    if result.get('error_type'):
        parts.append(str(result['error_type']))
    return '；'.join(parts) or '平台请求失败'


def _ycloud_get(url: str, api_key: str) -> dict[str, Any]:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return {'ok': False, 'http_status': None, 'error_type': 'external_io_disabled'}
    request = urllib.request.Request(
        url,
        headers={'Accept': 'application/json', 'X-API-Key': api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                return {'ok': False, 'http_status': response.status, 'error_type': 'response_too_large'}
            try:
                payload = json.loads(raw.decode('utf-8', errors='strict') or '{}')
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            return {
                'ok': 200 <= response.status < 300 and isinstance(payload, dict) and not payload.get('error'),
                'http_status': response.status,
                'payload': payload if isinstance(payload, dict) else {},
            }
    except urllib.error.HTTPError as exc:
        return {'ok': False, 'http_status': exc.code, 'error_type': 'YCloud API error'}
    except Exception as exc:
        return {'ok': False, 'http_status': None, 'error_type': type(exc).__name__}


def _ycloud_detail(result: dict[str, Any], success: str) -> str:
    if result.get('ok'):
        return success
    parts = []
    if result.get('http_status') is not None:
        parts.append(f'HTTP {result["http_status"]}')
    if result.get('error_type'):
        parts.append(str(result['error_type']))
    return '；'.join(parts) or 'YCloud 请求失败'


def _configured_public_id(integration: MarketingIntegration) -> bool:
    value = str(integration.public_id or '').strip()
    return bool(value and not value.startswith(PLACEHOLDER_PREFIX))


def _meta_version(integration: MarketingIntegration, *, inbound: bool = False) -> str:
    fallback = INBOUND_META_DEFAULT_VERSION if inbound else META_DEFAULT_VERSION
    version = str((integration.config_json or {}).get('api_version') or fallback).strip()
    return version if GRAPH_VERSION_RE.fullmatch(version) else fallback


def _diagnose_meta_pixel(integration: MarketingIntegration) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    version = _meta_version(integration)
    checks.append(_check('graph_version', 'Graph API 版本', 'pass', f'使用 {version}'))
    if not _configured_public_id(integration):
        checks.append(_check('dataset_id', 'Pixel / Dataset ID', 'fail', '尚未填写真实 ID'))
        return checks
    checks.append(_check('dataset_id', 'Pixel / Dataset ID', 'pass', '已配置真实 ID'))
    try:
        token = resolve_meta_access_token(integration)
    except Exception:
        token = ''
    if not token:
        checks.append(_check('token', 'Meta Access Token', 'fail', '服务器未找到主凭据'))
        return checks
    identity = _graph_get(f'https://graph.facebook.com/{version}/me?fields=id', token)
    checks.append(
        _check(
            'token',
            'Meta Access Token',
            'pass' if identity.get('ok') else 'fail',
            _graph_detail(identity, 'Token 身份验证通过'),
        )
    )
    if identity.get('ok'):
        dataset_id = urllib.parse.quote(str(integration.public_id).strip(), safe='')
        dataset = _graph_get(
            f'https://graph.facebook.com/{version}/{dataset_id}?fields=id,name',
            token,
        )
        checks.append(
            _check(
                'dataset_access',
                'Dataset 元数据权限',
                'pass' if dataset.get('ok') else 'warn',
                _graph_detail(dataset, 'Dataset 可读取'),
                required=False,
            )
        )
    return checks


def _webhook_secret_checks(integration: MarketingIntegration) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for key, label in (
        ('app_secret', 'Meta App Secret'),
        ('webhook_verify_token', 'Webhook Verify Token'),
    ):
        try:
            exists = bool(integration_secret(integration, key))
        except Exception:
            exists = False
        checks.append(_check(key, label, 'pass' if exists else 'fail', '已配置' if exists else '未配置'))
    return checks


def _diagnose_meta_leadgen(integration: MarketingIntegration) -> list[dict[str, Any]]:
    checks = _webhook_secret_checks(integration)
    version = _meta_version(integration, inbound=True)
    checks.insert(0, _check('graph_version', 'Graph API 版本', 'pass', f'使用 {version}'))
    configured_form_ids = set(configured_meta_leadgen_form_ids(integration))
    checks.append(
        _check(
            'leadgen_form_allowlist',
            'Instant Form 白名单',
            'pass' if configured_form_ids else 'fail',
            f'已配置 {len(configured_form_ids)} 个 Form ID'
            if configured_form_ids
            else '未配置允许接收的 Form ID',
        )
    )
    if not _configured_public_id(integration):
        checks.append(_check('page_id', 'Meta Page ID', 'fail', '尚未填写真实 Page ID'))
        return checks
    checks.append(_check('page_id', 'Meta Page ID', 'pass', '已配置真实 Page ID'))
    try:
        token = integration_secret(integration, 'primary')
    except Exception:
        token = ''
    if not token:
        checks.append(_check('page_token', 'Page Access Token', 'fail', '服务器未找到主凭据'))
        return checks
    page_id = urllib.parse.quote(str(integration.public_id).strip(), safe='')
    page = _graph_get(f'https://graph.facebook.com/{version}/{page_id}?fields=id,name', token)
    checks.append(
        _check(
            'page_token',
            'Page 与 Token 权限',
            'pass' if page.get('ok') else 'fail',
            _graph_detail(page, 'Page 可读取'),
        )
    )
    if page.get('ok'):
        forms = _graph_get(
            f'https://graph.facebook.com/{version}/{page_id}/leadgen_forms?fields=id,status&limit=25',
            token,
        )
        count = len((forms.get('payload') or {}).get('data') or []) if forms.get('ok') else 0
        checks.append(
            _check(
                'leadgen_forms',
                'Instant Forms / Leads Access',
                'pass' if forms.get('ok') else 'fail',
                f'可读取 {count} 个表单' if forms.get('ok') else _graph_detail(forms, '表单可读取'),
            )
        )
        if forms.get('ok') and configured_form_ids:
            provider_form_ids = {
                str(item.get('id') or '').strip()
                for item in ((forms.get('payload') or {}).get('data') or [])
                if isinstance(item, dict) and str(item.get('id') or '').strip()
            }
            missing_form_ids = sorted(configured_form_ids - provider_form_ids)
            checks.append(
                _check(
                    'leadgen_form_assets',
                    '白名单表单资产核对',
                    'pass' if not missing_form_ids else 'fail',
                    '所有白名单 Form ID 均可读取'
                    if not missing_form_ids
                    else f'有 {len(missing_form_ids)} 个白名单 Form ID 无法从当前 Page 读取',
                )
            )
    return checks


def _diagnose_whatsapp(integration: MarketingIntegration) -> list[dict[str, Any]]:
    config = dict(integration.config_json or {})
    transport_provider = str(config.get('transport_provider') or 'meta').strip().lower()
    if transport_provider == 'ycloud':
        try:
            webhook_secret_exists = bool(integration_secret(integration, 'ycloud_webhook_secret'))
        except Exception:
            webhook_secret_exists = False
        endpoint_id = str(config.get('ycloud_webhook_endpoint_id') or '').strip()
        checks = [
            _check(
                'ycloud_webhook_secret',
                'YCloud Webhook Signing Secret',
                'pass' if webhook_secret_exists else 'fail',
                '已配置' if webhook_secret_exists else '未配置',
            ),
            _check(
                'ycloud_webhook_endpoint_id',
                'YCloud Webhook Endpoint ID',
                'pass' if endpoint_id else 'fail',
                '已配置' if endpoint_id else '未配置',
            ),
        ]
    else:
        checks = _webhook_secret_checks(integration)
    version = _meta_version(integration, inbound=True)
    checks.insert(
        0,
        _check(
            'transport_provider',
            'WhatsApp 官方传输方式',
            'pass' if transport_provider in {'meta', 'ycloud'} else 'fail',
            'YCloud Coexistence API v2' if transport_provider == 'ycloud' else f'Meta Graph {version}',
        ),
    )
    if not _configured_public_id(integration):
        checks.append(_check('phone_number_id', 'Phone Number ID', 'fail', '尚未填写真实号码 ID'))
        return checks
    checks.append(_check('phone_number_id', 'Phone Number ID', 'pass', '已配置真实号码 ID'))
    waba_id = str(config.get('waba_id') or '').strip()
    page_id = str(config.get('page_id') or '').strip()
    identity_mode = str(config.get('ctwa_identity_mode') or 'waba').strip().lower()
    checks.append(_check('waba_id', 'WhatsApp Business Account ID', 'pass' if waba_id else 'fail', '已配置' if waba_id else '未配置'))
    checks.append(
        _check(
            'ctwa_identity_mode',
            'CTWA 回传身份模式',
            'pass' if identity_mode in {'waba', 'page'} else 'fail',
            'WABA ID + ctwa_clid' if identity_mode == 'waba' else (
                'Page ID + ctwa_clid（旧版）'
                if identity_mode == 'page'
                else f'不支持的模式 {identity_mode}'
            ),
        )
    )
    if identity_mode == 'page':
        checks.append(
            _check(
                'ctwa_page_id',
                'CTWA Meta Page ID（旧版）',
                'pass' if page_id else 'fail',
                '已配置' if page_id else '旧版 Page 身份模式缺少 Page ID',
            )
        )
    if transport_provider == 'ycloud':
        business_phone = str(config.get('business_phone_e164') or '').strip()
        phone_digits = re.sub(r'\D', '', business_phone)
        checks.append(
            _check(
                'business_phone_e164',
                'Coexistence 业务号码',
                'pass' if 8 <= len(phone_digits) <= 15 else 'fail',
                business_phone if business_phone else '未配置',
            )
        )
        try:
            token = integration_secret(integration, 'primary')
        except Exception:
            token = ''
        if not token:
            checks.append(_check('ycloud_api_key', 'YCloud API Key', 'fail', '服务器未找到主凭据'))
            return checks
        if not waba_id or not phone_digits:
            checks.append(_check('ycloud_phone', 'YCloud 号码资产', 'fail', '缺少 WABA 或 E.164 业务号码'))
            return checks
        phone = _ycloud_get(
            'https://api.ycloud.com/v2/whatsapp/phoneNumbers/'
            f'{urllib.parse.quote(waba_id, safe="")}/'
            f'{urllib.parse.quote(f"+{phone_digits}", safe="")}',
            token,
        )
        checks.append(
            _check(
                'ycloud_phone',
                'YCloud 号码与 API Key',
                'pass' if phone.get('ok') else 'fail',
                _ycloud_detail(phone, 'YCloud 已返回指定 WABA 和业务号码'),
            )
        )
        payload = phone.get('payload') if isinstance(phone.get('payload'), dict) else {}
        coexistence_flag = any(
            payload.get(key) is True
            for key in ('isOnBizApp', 'is_on_biz_app', 'coexistence')
        )
        checks.append(
            _check(
                'coexistence',
                'WhatsApp Business App 共存',
                'pass' if coexistence_flag else 'warn',
                (
                    'YCloud 号码响应明确标记 Business App 共存'
                    if coexistence_flag
                    else '号码可读不等同于共存已验收；需在真实回显和手机端同时收发中确认'
                ),
                required=False,
            )
        )
        return checks
    try:
        token = integration_secret(integration, 'primary')
    except Exception:
        token = ''
    if not token:
        checks.append(_check('whatsapp_token', 'WhatsApp Access Token', 'fail', '服务器未找到主凭据'))
        return checks
    phone_id = urllib.parse.quote(str(integration.public_id).strip(), safe='')
    phone = _graph_get(
        (
            f'https://graph.facebook.com/{version}/{phone_id}'
            '?fields=id,display_phone_number,verified_name,platform_type,is_on_biz_app'
        ),
        token,
    )
    checks.append(
        _check(
            'whatsapp_token',
            '号码与 Token 权限',
            'pass' if phone.get('ok') else 'fail',
            _graph_detail(phone, 'WhatsApp 号码可读取'),
        )
    )
    if phone.get('ok'):
        phone_payload = phone.get('payload') if isinstance(phone.get('payload'), dict) else {}
        platform_type = str(phone_payload.get('platform_type') or '').strip().upper()
        is_on_biz_app = phone_payload.get('is_on_biz_app') is True
        cloud_api_ready = platform_type == 'CLOUD_API'
        checks.append(
            _check(
                'cloud_api_registration',
                'Cloud API 注册状态',
                'pass' if cloud_api_ready else 'fail',
                (
                    '平台类型为 CLOUD_API'
                    if cloud_api_ready
                    else f'平台类型为 {platform_type or "未返回"}；号码尚不能作为 Cloud API 通道使用'
                ),
            )
        )
        if cloud_api_ready and is_on_biz_app:
            coexistence_status = 'pass'
            coexistence_detail = 'Meta 返回 is_on_biz_app=true；号码处于官方 Coexistence'
        elif cloud_api_ready:
            coexistence_status = 'info'
            coexistence_detail = '号码是纯 Cloud API；未启用 WhatsApp Business App 共存'
        else:
            coexistence_status = 'warn'
            coexistence_detail = '尚未证明官方 Coexistence；需通过合资格伙伴的 Embedded Signup 核验'
        checks.append(
            _check(
                'coexistence',
                'WhatsApp Business App 共存',
                coexistence_status,
                coexistence_detail,
                required=False,
            )
        )
    if phone.get('ok') and waba_id:
        encoded_waba_id = urllib.parse.quote(waba_id, safe='')
        waba = _graph_get(
            f'https://graph.facebook.com/{version}/{encoded_waba_id}?fields=id,name',
            token,
        )
        checks.append(
            _check(
                'waba_access',
                'WABA 权限',
                'pass' if waba.get('ok') else 'fail',
                _graph_detail(waba, 'WABA 可读取'),
            )
        )
        capi_dataset = _graph_get(
            f'https://graph.facebook.com/{version}/{encoded_waba_id}/dataset',
            token,
        )
        capi_payload = (
            capi_dataset.get('payload')
            if isinstance(capi_dataset.get('payload'), dict)
            else {}
        )
        capi_dataset_id = str(capi_payload.get('id') or '').strip()
        if not capi_dataset_id:
            capi_rows = capi_payload.get('data') if isinstance(capi_payload.get('data'), list) else []
            first_row = capi_rows[0] if capi_rows and isinstance(capi_rows[0], dict) else {}
            capi_dataset_id = str(first_row.get('id') or '').strip()
        if capi_dataset.get('ok') and capi_dataset_id:
            capi_status = 'pass'
            capi_detail = f'WABA 已关联 Dataset {capi_dataset_id}'
        elif capi_dataset.get('ok'):
            capi_status = 'warn'
            capi_detail = 'WABA Dataset 接口可访问，但未返回已关联 Dataset ID'
        else:
            capi_status = 'warn'
            capi_detail = (
                f'{_graph_detail(capi_dataset, "WABA Dataset 可读取")}；'
                '核对 whatsapp_business_manage_events 与 Marketing API Access Tier'
            )
        checks.append(
            _check(
                'whatsapp_capi_dataset',
                'WhatsApp CAPI Dataset 与权限',
                capi_status,
                capi_detail,
                required=False,
            )
        )
    return checks


def _diagnose_google(integration: MarketingIntegration) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    config = dict(integration.config_json or {})
    customer_id = re.sub(r'\D', '', str(integration.public_id or ''))
    configured_customer = bool(customer_id and not str(integration.public_id or '').startswith(PLACEHOLDER_PREFIX))
    checks.append(
        _check(
            'customer_id',
            'Google Ads Customer ID',
            'pass' if configured_customer else 'fail',
            '已配置真实 ID' if configured_customer else '尚未填写真实 Customer ID',
        )
    )
    for key, label in (
        ('qualified_conversion_action_id', 'Qualified Lead 转化操作'),
        ('converted_conversion_action_id', 'Converted Lead 转化操作'),
    ):
        exists = bool(str(config.get(key) or '').strip())
        checks.append(_check(key, label, 'pass' if exists else 'fail', '已配置' if exists else '未配置'))
    try:
        token = _load_google_credentials(integration)
    except GoogleDataManagerError as exc:
        checks.append(_check('google_credentials', 'Google OAuth / ADC', 'fail', str(exc)))
    except Exception as exc:
        checks.append(_check('google_credentials', 'Google OAuth / ADC', 'fail', type(exc).__name__))
    else:
        checks.append(
            _check(
                'google_credentials',
                'Google OAuth / ADC',
                'pass' if token else 'fail',
                '访问令牌获取成功' if token else '访问令牌为空',
            )
        )
    tag_enabled = bool(config.get('google_tag_enabled'))
    tag_id = normalize_google_tag_id(config.get('google_tag_id'))
    if tag_enabled:
        checks.append(
            _check(
                'google_tag',
                'Google 浏览器标签',
                'pass' if tag_id else 'fail',
                f'{tag_id}；Consent Mode v2' if tag_id else '已启用，但 Tag ID 无效',
            )
        )
    else:
        checks.append(_check('google_tag', 'Google 浏览器标签', 'info', '未启用；服务器回传可独立配置', required=False))
    return checks


def diagnose_marketing_integration(integration: MarketingIntegration) -> dict[str, Any]:
    provider = str(integration.provider.code or '').strip().lower()
    integration_type = str(integration.integration_type or '').strip().lower()
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        checks = [_check('external_io', '环境外发开关', 'fail', '当前环境禁止外部连接；未读取凭据或连接平台。')]
    elif provider == 'meta' and integration_type == 'pixel':
        checks = _diagnose_meta_pixel(integration)
    elif provider == 'meta' and integration_type == 'leadgen':
        checks = _diagnose_meta_leadgen(integration)
    elif provider == 'whatsapp' and integration_type == 'cloud_api':
        checks = _diagnose_whatsapp(integration)
    elif provider == 'google' and integration_type == 'data_manager':
        checks = _diagnose_google(integration)
    else:
        checks = [_check('unsupported', '连接诊断', 'warn', '当前接入类型尚无自动诊断', required=False)]

    checks.insert(
        0,
        _check(
            'enabled',
            '接入开关',
            'pass' if integration.enabled else 'warn',
            '已启用' if integration.enabled else '当前停用；可先诊断配置，启用后才会处理业务事件',
            required=False,
        ),
    )
    required = [item for item in checks if item['required']]
    if required and all(item['status'] == 'pass' for item in required):
        if any(item['status'] == 'warn' for item in checks):
            overall = 'warn'
            summary = '基础连接通过，但存在提醒'
        else:
            overall = 'pass'
            summary = '服务器连接检查通过'
    elif any(item['status'] == 'fail' for item in required):
        overall = 'fail'
        summary = '存在阻断项'
    else:
        overall = 'warn'
        summary = '仅部分检查通过'
    if not integration.enabled and overall == 'pass':
        overall = 'warn'
        summary = '配置可连接，但接入尚未启用'
    return {
        'schema_version': 1,
        'tested_at': timezone.now().isoformat(),
        'provider': provider,
        'integration_type': integration_type,
        'overall': overall,
        'summary': summary,
        'checks': checks,
        'acceptance_note': '该结果只验证服务器配置和只读权限；真实事件接收、匹配质量与广告优化状态仍须在平台诊断页验收。',
    }
