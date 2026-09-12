from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import timedelta, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pycountry
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from leads.models import ConsentRecord, LeadEventOutbox, LeadFormDefinition, LeadSubmission, SalesTeam
from leads.google_data_manager import (
    GoogleDataManagerError,
    build_google_data_manager_payload,
    dispatch_google_outbox_event,
    normalize_google_phone,
)
from marketing.models import MarketingIntegration
from sitecore.models import Cta, PageRoute, Site, SiteLocale
from console.secret_store import load_secret


EMAIL_RE = re.compile(r'^\S+@\S+\.\S+$')
PHONE_CLEAN_RE = re.compile(r'[^0-9+]')
META_DEFAULT_VERSION = os.getenv('SITEOS_META_GRAPH_API_VERSION', 'v26.0')
META_GRAPH_VERSION_RE = re.compile(r'^v\d+\.\d+$')
SOURCE_CHANNELS = {'meta_ads', 'paid_search', 'organic', 'referral', 'direct', 'website', 'whatsapp', 'email', 'other'}
URL_RE = re.compile(r'https?://', re.IGNORECASE)
GOOGLE_SESSION_ATTRIBUTE_KEYS = {
    'gad_source',
    'gad_campaignid',
    'session_start_time_usec',
    'landing_page_url',
    'landing_page_referrer',
    'landing_page_user_agent',
}
GOOGLE_REQUIRED_SESSION_ATTRIBUTE_KEYS = {
    'gad_source',
    'gad_campaignid',
    'session_start_time_usec',
    'landing_page_user_agent',
}
MARKETING_QUERY_KEYS = {
    'fbclid',
    'gclid',
    'gbraid',
    'wbraid',
    'utm_source',
    'utm_medium',
    'utm_campaign',
    'utm_content',
    'utm_term',
}
MARKETING_IDENTIFIER_KEYS = {
    'fbp',
    'fbc',
    'fbclid',
    'meta_lead_id',
    'leadgen_id',
    'meta_campaign_id',
    'meta_adset_id',
    'meta_ad_id',
    'gclid',
    'gbraid',
    'wbraid',
    'ctwa_clid',
    'whatsapp_id',
    'wa_id',
}
BUSINESS_MESSAGING_STAGE_EVENTS = {
    'qualified': 'LeadSubmitted',
    'won': 'Purchase',
}
CTWA_IDENTITY_MODES = {'waba', 'page'}


def sales_team_for_form(form_definition: LeadFormDefinition) -> SalesTeam | None:
    config = dict(form_definition.config_json or {})
    preferred_code = str(config.get('sales_team_code') or 'default').strip() or 'default'
    teams = SalesTeam.objects.filter(site=form_definition.site, enabled=True)
    return teams.filter(code=preferred_code).first() or teams.filter(code='default').first()
META_COUNTRY_ALIASES = {
    'uae': 'AE',
    'u a e': 'AE',
    'ksa': 'SA',
    'uk': 'GB',
    'u k': 'GB',
    'south korea': 'KR',
    'north korea': 'KP',
    'russia': 'RU',
    'iran': 'IR',
    'syria': 'SY',
    'vietnam': 'VN',
    'laos': 'LA',
    'bolivia': 'BO',
    'venezuela': 'VE',
    'moldova': 'MD',
    'tanzania': 'TZ',
    'brunei': 'BN',
    'palestine': 'PS',
    'ivory coast': 'CI',
    'czech republic': 'CZ',
    'cape verde': 'CV',
    'swaziland': 'SZ',
    'taiwan': 'TW',
    'macau': 'MO',
    'الإمارات العربية المتحدة': 'AE',
    'الامارات العربية المتحدة': 'AE',
    'الإمارات': 'AE',
    'الامارات': 'AE',
    'المملكة العربية السعودية': 'SA',
    'السعودية': 'SA',
    'قطر': 'QA',
    'الكويت': 'KW',
    'عمان': 'OM',
    'عُمان': 'OM',
    'البحرين': 'BH',
    'مصر': 'EG',
    'العراق': 'IQ',
    'الأردن': 'JO',
    'الاردن': 'JO',
    'لبنان': 'LB',
    'المغرب': 'MA',
    'الجزائر': 'DZ',
    'تونس': 'TN',
    'ليبيا': 'LY',
    'السودان': 'SD',
    'اليمن': 'YE',
    'فلسطين': 'PS',
    'سوريا': 'SY',
}


@dataclass(frozen=True)
class LeadCaptureResult:
    submission: LeadSubmission
    queued_events: int
    dispatch_attempted: int
    dispatch_succeeded: int
    duplicate: bool = False
    notification_sent: bool = False


class LeadCaptureError(ValueError):
    def __init__(self, message: str, *, code: str = 'invalid_submission', status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class MetaPayloadError(ValueError):
    pass


def canonicalize_email(value: str) -> str:
    return (value or '').strip().lower()


def canonicalize_phone(value: str) -> str:
    raw = (value or '').strip()
    digits = re.sub(r'\D', '', raw)
    if raw.startswith('00'):
        return f'+{digits[2:]}'
    if raw.startswith('+'):
        return f'+{digits}'
    return digits


def normalize_meta_country(value: str) -> str:
    raw = re.sub(r'\s+', ' ', (value or '').strip())
    if not raw:
        return ''

    alias_key = re.sub(r'[._-]+', ' ', raw.casefold())
    alias_key = re.sub(r'\s+', ' ', alias_key).strip()
    aliased = META_COUNTRY_ALIASES.get(alias_key)
    if aliased:
        return aliased.lower()

    try:
        country = pycountry.countries.lookup(raw)
    except LookupError:
        return ''
    return country.alpha_2.lower()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def split_name(full_name: str) -> tuple[str, str]:
    tokens = [part for part in (full_name or '').strip().split() if part]
    if not tokens:
        return '', ''
    if len(tokens) == 1:
        return tokens[0], ''
    return tokens[0], ' '.join(tokens[1:])


def resolve_meta_access_token(integration: MarketingIntegration) -> str:
    secret_ref = (integration.secret_ref or '').strip()
    if secret_ref:
        if secret_ref.startswith('vault:'):
            return load_secret(secret_ref.removeprefix('vault:'))
        return os.getenv(secret_ref, '').strip()
    config_token = (integration.config_json or {}).get('access_token', '')
    return str(config_token or '').strip()


def meta_api_version(integration: MarketingIntegration) -> str:
    configured = str((integration.config_json or {}).get('api_version') or '').strip()
    if META_GRAPH_VERSION_RE.fullmatch(configured):
        return configured
    if META_GRAPH_VERSION_RE.fullmatch(META_DEFAULT_VERSION):
        return META_DEFAULT_VERSION
    return 'v26.0'


def enabled_meta_integrations(site: Site) -> list[MarketingIntegration]:
    return list(
        MarketingIntegration.objects.select_related('provider')
        .filter(site=site, provider__code='meta', integration_type='pixel', enabled=True)
        .order_by('id')
    )


def enabled_google_integrations(site: Site) -> list[MarketingIntegration]:
    return list(
        MarketingIntegration.objects.select_related('provider')
        .filter(site=site, provider__code='google', integration_type='data_manager', enabled=True)
        .order_by('id')
    )


def integration_delivery_mode(integration: MarketingIntegration) -> str:
    config = dict(integration.config_json or {})
    if str(config.get('test_event_code') or '').strip():
        return 'test'
    if bool(config.get('validate_only')):
        return 'validation'
    return 'live'


def record_initial_consent_snapshot(
    submission: LeadSubmission,
    *,
    source: str = '',
) -> list[ConsentRecord]:
    """Persist the immutable consent decisions captured with a new lead."""
    if ConsentRecord.objects.filter(submission=submission).exists():
        return []
    consent = dict(submission.consent_json or {})
    recorded_source = (
        str(source or consent.get('source_contract') or 'website_form').strip()[:80]
        or 'website_form'
    )
    policy_version = str(consent.get('policy_version') or '').strip()[:80]
    evidence = {
        'form_code': submission.form.code,
        'source_channel': submission.source_channel,
        'source_url': submission.source_url,
        'client_event_id': str((submission.identifiers_json or {}).get('client_event_id') or ''),
    }
    records = [
        ConsentRecord(
            submission=submission,
            purpose=purpose,
            decision='granted' if bool(consent.get(purpose)) else 'denied',
            source=recorded_source,
            policy_version=policy_version,
            evidence_json=evidence,
        )
        for purpose in ('contact', 'privacy_notice', 'marketing')
    ]
    return ConsentRecord.objects.bulk_create(records)


def _bounded_text(payload: dict[str, Any], key: str, max_length: int, *, fallback: str = '') -> str:
    value = str(payload.get(key) or fallback).strip()
    if len(value) > max_length:
        raise LeadCaptureError(f'{key} 内容过长。')
    return value


def _json_object(payload: dict[str, Any], key: str, *, max_bytes: int = 20000) -> dict[str, Any]:
    value = payload.get(key) or {}
    if not isinstance(value, dict):
        raise LeadCaptureError(f'{key} 必须是 JSON 对象。')
    if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > max_bytes:
        raise LeadCaptureError(f'{key} 内容过大。')
    return value


def _clean_google_session_attributes(value: Any, *, marketing_consent: bool) -> str:
    if not marketing_consent:
        return ''
    raw = str(value or '').strip()
    if not raw or len(raw) > 8192 or not re.fullmatch(r'[A-Za-z0-9_-]+', raw):
        return ''
    try:
        padded = raw + ('=' * (-len(raw) % 4))
        decoded = base64.b64decode(padded, altchars=b'-_', validate=True)
    except (binascii.Error, ValueError):
        return ''
    if not decoded or len(decoded) > 5120:
        return ''
    try:
        attributes = json.loads(decoded.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ''
    if not isinstance(attributes, dict):
        return ''

    cleaned: dict[str, str] = {}
    for raw_key, raw_value in attributes.items():
        key = str(raw_key or '').strip()
        if key not in GOOGLE_SESSION_ATTRIBUTE_KEYS and not (
            key.startswith('gad_') and len(key) <= 80 and re.fullmatch(r'[a-z0-9_]+', key)
        ):
            continue
        if not isinstance(raw_value, str):
            continue
        text = raw_value.strip()
        max_length = 2000 if key in {'landing_page_url', 'landing_page_referrer'} else 1024
        if not text or len(text) > max_length:
            continue
        if key in {'landing_page_url', 'landing_page_referrer'}:
            parsed_url = urlsplit(text)
            if parsed_url.scheme not in {'http', 'https'} or not parsed_url.hostname:
                continue
        cleaned[key] = text

    if not GOOGLE_REQUIRED_SESSION_ATTRIBUTE_KEYS <= cleaned.keys():
        return ''
    if not re.fullmatch(r'\d{1,30}', cleaned['gad_source']):
        return ''
    if not re.fullmatch(r'\d{1,30}', cleaned['gad_campaignid']):
        return ''
    if not re.fullmatch(r'\d{13,20}', cleaned['session_start_time_usec']):
        return ''

    normalized = json.dumps(cleaned, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(normalized) > 5120:
        return ''
    return base64.urlsafe_b64encode(normalized).decode('ascii').rstrip('=')


def _strip_marketing_query(url: str) -> str:
    if not url:
        return ''
    try:
        parsed = urlsplit(url)
        query = urlencode([
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in MARKETING_QUERY_KEYS and not key.lower().startswith('gad_')
        ], doseq=True)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    except ValueError:
        return ''


def _derive_source(payload: dict[str, Any], utm: dict[str, Any], referrer_url: str) -> tuple[str, str]:
    requested = str(payload.get('source_channel') or '').strip().lower()
    source = str(utm.get('utm_source') or '').strip().lower()
    medium = str(utm.get('utm_medium') or '').strip().lower()
    if requested in SOURCE_CHANNELS:
        channel = requested
    elif source in {'facebook', 'instagram', 'meta', 'fb', 'ig'} or payload.get('fbclid') or payload.get('meta_lead_id'):
        channel = 'meta_ads'
    elif medium in {'cpc', 'ppc', 'paid', 'paid_search'} or payload.get('gclid') or payload.get('gbraid') or payload.get('wbraid'):
        channel = 'paid_search'
    elif payload.get('ctwa_clid'):
        channel = 'whatsapp'
    elif source in {'google', 'bing', 'baidu'} and medium in {'organic', ''}:
        channel = 'organic'
    elif referrer_url:
        channel = 'referral'
    else:
        channel = 'direct'

    detail = _bounded_text(payload, 'source_detail', 500)
    if not detail:
        parts = [
            str(utm.get('utm_campaign') or '').strip(),
            str(utm.get('utm_content') or '').strip(),
            (urlsplit(referrer_url).hostname or '') if referrer_url else '',
        ]
        detail = ' / '.join(item for item in parts if item)[:500]
    return channel, detail


def clean_submission_input(payload: dict[str, Any], *, form_definition: LeadFormDefinition) -> dict[str, Any]:
    if str(payload.get('website') or '').strip():
        raise LeadCaptureError('提交未通过安全检查。', code='spam_detected')

    full_name = _bounded_text(payload, 'full_name', 120, fallback=str(payload.get('name') or ''))
    company = _bounded_text(payload, 'company', 200)
    country = _bounded_text(payload, 'country', 120)
    email = canonicalize_email(_bounded_text(payload, 'email', 254))
    phone = canonicalize_phone(_bounded_text(payload, 'phone', 40))
    message = _bounded_text(payload, 'message', 4000)
    if len(full_name) < 2:
        raise LeadCaptureError('请填写姓名。')
    if len(company) < 2:
        raise LeadCaptureError('请填写公司名称。')
    if len(country) < 2:
        raise LeadCaptureError('请填写国家或地区。')
    if email and not EMAIL_RE.match(email):
        raise LeadCaptureError('邮箱格式无效。')
    phone_digits = re.sub(r'\D', '', phone)
    if phone and not 7 <= len(phone_digits) <= 15:
        raise LeadCaptureError('电话或 WhatsApp 号码格式无效。')
    if not email and not phone:
        raise LeadCaptureError('邮箱或电话/WhatsApp 至少填写一项。')

    extra_fields = _json_object(payload, 'extra_fields')
    product_category = str(extra_fields.get('product_category') or '').strip()
    capacity = str(extra_fields.get('capacity') or '').strip()
    if not product_category:
        raise LeadCaptureError('请选择产品类别。')
    if getattr(form_definition, 'category', 'project') == 'industrial':
        from leads.industrial import BUSINESS_LINES
        business_line = extra_fields.get('business_line')
        if not isinstance(business_line, str) or business_line not in BUSINESS_LINES:
            raise LeadCaptureError('请选择有效的业务方向。')
        extra_fields = dict(extra_fields, product_category=BUSINESS_LINES[business_line])
        if len(message) < 2:
            raise LeadCaptureError('请填写项目说明。')
    elif not capacity:
        # Legacy form contracts remain valid; industrial inquiries do not use
        # bottle/line capacity and must not fabricate it for compatibility.
        raise LeadCaptureError('请填写产能目标。')
    for key, value in extra_fields.items():
        if len(str(key)) > 80 or len(str(value)) > 500:
            raise LeadCaptureError('项目字段内容过长。')

    consent = _json_object(payload, 'consent', max_bytes=4000)
    if not bool(consent.get('contact')) or not bool(consent.get('privacy_notice')):
        raise LeadCaptureError('请确认联系许可并阅读隐私说明。')

    form_started_at = str(payload.get('form_started_at') or '').strip()
    if form_started_at:
        started = parse_datetime(form_started_at)
        if not started:
            raise LeadCaptureError('表单时间标识无效。', code='spam_detected')
        if started and timezone.is_naive(started):
            started = timezone.make_aware(started, datetime_timezone.utc)
        if started and (timezone.now() - started).total_seconds() < 1:
            raise LeadCaptureError('提交速度过快，请稍后重试。', code='spam_detected')

    if len(URL_RE.findall(message)) > 2:
        raise LeadCaptureError('留言中的链接数量过多。', code='spam_detected')

    buyer_value = payload.get('buyer_value')
    parsed_value: Decimal | None = None
    if buyer_value not in (None, ''):
        try:
            parsed_value = Decimal(str(buyer_value))
        except (InvalidOperation, ValueError) as exc:
            raise LeadCaptureError('成交金额格式无效。') from exc
        if parsed_value < 0:
            raise LeadCaptureError('成交金额不能为负数。')

    buyer_currency = str(payload.get('buyer_currency') or 'USD').strip().upper() or 'USD'
    if not re.fullmatch(r'[A-Z]{3,8}', buyer_currency):
        raise LeadCaptureError('币种格式无效。')

    marketing_consent = bool(consent.get('marketing'))
    source_url = _bounded_text(payload, 'source_url', 2000)
    referrer_url = _bounded_text(payload, 'referrer_url', 2000)
    utm = _json_object(payload, 'utm', max_bytes=8000) if marketing_consent else {}
    source_payload = payload
    if not marketing_consent:
        source_url = _strip_marketing_query(source_url)
        referrer_url = _strip_marketing_query(referrer_url)
        source_payload = dict(payload)
        for key in MARKETING_IDENTIFIER_KEYS:
            source_payload[key] = ''
        source_payload['source_channel'] = ''
        source_payload['source_detail'] = ''
    source_channel, source_detail = _derive_source(source_payload, utm, referrer_url)
    google_session_attributes = _clean_google_session_attributes(
        payload.get('google_session_attributes') or payload.get('session_attributes'),
        marketing_consent=marketing_consent,
    )
    def marketing_text(key: str, max_length: int, *, fallback: str = '') -> str:
        return _bounded_text(payload, key, max_length, fallback=fallback) if marketing_consent else ''

    identifiers = {
        'fbp': marketing_text('fbp', 255),
        'fbc': marketing_text('fbc', 255),
        'client_event_id': _bounded_text(payload, 'client_event_id', 120, fallback=str(payload.get('event_id') or '')),
        'external_id': marketing_text('external_id', 255),
        'fbclid': marketing_text('fbclid', 500),
        'meta_lead_id': marketing_text('meta_lead_id', 255, fallback=str(payload.get('leadgen_id') or '')),
        'meta_campaign_id': marketing_text('meta_campaign_id', 255),
        'meta_adset_id': marketing_text('meta_adset_id', 255),
        'meta_ad_id': marketing_text('meta_ad_id', 255),
        'gclid': marketing_text('gclid', 500),
        'gbraid': marketing_text('gbraid', 500),
        'wbraid': marketing_text('wbraid', 500),
        'google_session_attributes': google_session_attributes,
        'ctwa_clid': marketing_text('ctwa_clid', 1000),
        'whatsapp_id': marketing_text('whatsapp_id', 80, fallback=str(payload.get('wa_id') or '')),
    }
    contact_key = sha256_text(f'{form_definition.site_id}|{email}|{phone}')
    dedupe_material = json.dumps(
        {
            'form': form_definition.id,
            'email': email,
            'phone': phone,
            'company': company.lower(),
            'country': country.lower(),
            'message': message.lower(),
            'extra_fields': extra_fields,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )

    return {
        'full_name': full_name,
        'email': email,
        'phone': phone,
        'company': company,
        'country': country,
        'message': message,
        'source_url': source_url,
        'referrer_url': referrer_url,
        'source_channel': source_channel,
        'source_detail': source_detail,
        'buyer_value': parsed_value,
        'buyer_currency': buyer_currency,
        'payload_json': extra_fields,
        'utm_json': utm,
        'consent_json': consent,
        'identifiers_json': identifiers,
        'contact_key': contact_key,
        'dedupe_key': sha256_text(dedupe_material),
        'route_path': _bounded_text(payload, 'route_path', 500),
        'cta_code': _bounded_text(payload, 'cta_code', 120),
        'config_json': {'form_started_at': form_started_at} if form_started_at else {},
    }


def _acquire_dedupe_lock(dedupe_key: str) -> None:
    if connection.vendor != 'postgresql':
        return
    lock_id = int(dedupe_key[:16], 16)
    if lock_id >= 2**63:
        lock_id -= 2**64
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])


def build_meta_user_data(submission: LeadSubmission, *, include_client_context: bool = True) -> dict[str, Any]:
    first_name, last_name = split_name(submission.full_name)
    identifiers = submission.identifiers_json or {}
    user_data: dict[str, Any] = {}

    if submission.email:
        user_data['em'] = [sha256_text(canonicalize_email(submission.email))]
    if submission.phone:
        normalized_phone = normalize_google_phone(submission.phone).removeprefix('+')
        if normalized_phone:
            user_data['ph'] = [sha256_text(normalized_phone)]
    if first_name:
        user_data['fn'] = [sha256_text(first_name.strip().lower())]
    if last_name:
        user_data['ln'] = [sha256_text(last_name.strip().lower())]
    normalized_country = normalize_meta_country(submission.country)
    if normalized_country:
        user_data['country'] = [sha256_text(normalized_country)]
    external_id = str(identifiers.get('external_id') or submission.submission_key)
    if external_id:
        user_data['external_id'] = [sha256_text(external_id.lower())]
    if identifiers.get('fbp'):
        user_data['fbp'] = identifiers['fbp']
    if identifiers.get('fbc'):
        user_data['fbc'] = identifiers['fbc']
    if identifiers.get('meta_lead_id'):
        user_data['lead_id'] = identifiers['meta_lead_id']
    if identifiers.get('ctwa_clid'):
        user_data['ctwa_clid'] = identifiers['ctwa_clid']
    if include_client_context and submission.client_ip:
        user_data['client_ip_address'] = submission.client_ip
    if include_client_context and submission.user_agent:
        user_data['client_user_agent'] = submission.user_agent
    return user_data


def build_meta_ctwa_user_data(submission: LeadSubmission) -> dict[str, Any]:
    identifiers = dict(submission.identifiers_json or {})
    ctwa_clid = str(identifiers.get('ctwa_clid') or '').strip()
    waba_id = str(identifiers.get('whatsapp_business_account_id') or '').strip()
    page_id = str(identifiers.get('meta_page_id') or '').strip()
    identity_mode = str(identifiers.get('ctwa_identity_mode') or '').strip().lower()
    if identity_mode not in CTWA_IDENTITY_MODES:
        identity_mode = 'waba'
    if not ctwa_clid:
        raise MetaPayloadError('CTWA CAPI 回传缺少 ctwa_clid。')
    if identity_mode == 'waba':
        if not waba_id:
            raise MetaPayloadError('CTWA CAPI 回传缺少 WhatsApp Business Account ID。')
        return {
            'ctwa_clid': ctwa_clid,
            'whatsapp_business_account_id': waba_id,
        }
    if not page_id:
        raise MetaPayloadError('CTWA CAPI 旧版 Page 身份模式缺少 Meta Page ID。')
    return {'ctwa_clid': ctwa_clid, 'page_id': page_id}


def meta_platform_event_name(
    submission: LeadSubmission,
    *,
    stage_key: str,
    crm_event_name: str,
) -> str:
    """Map CRM stages to Meta's narrower Business Messaging vocabulary."""

    identifiers = dict(submission.identifiers_json or {})
    if not identifiers.get('ctwa_clid'):
        return crm_event_name
    event_name = BUSINESS_MESSAGING_STAGE_EVENTS.get(stage_key)
    if not event_name:
        raise MetaPayloadError(
            f'CTWA CAPI 不回传 CRM 阶段 {stage_key}；仅回传合格线索和真实成交。'
        )
    return event_name


def meta_stage_event_time(submission: LeadSubmission, stage_key: str):
    timestamps = {
        'submitted': submission.submitted_at,
        'contacted': submission.contacted_at,
        'qualified': submission.qualified_at,
        'won': submission.won_at,
        'lost': submission.lost_at,
    }
    return timestamps.get(stage_key) or submission.stage_updated_at or submission.submitted_at


def build_meta_payload(
    submission: LeadSubmission,
    integration: MarketingIntegration,
    *,
    stage_key: str,
    event_name: str,
    crm_event_name: str | None = None,
) -> dict[str, Any]:
    identifiers = submission.identifiers_json or {}
    is_ctwa = bool(identifiers.get('ctwa_clid'))
    is_meta_instant_form = bool(identifiers.get('meta_lead_id')) and not identifiers.get('client_event_id')
    is_website_submission = stage_key == 'submitted' and not is_ctwa and not is_meta_instant_form
    if is_website_submission and not str(submission.source_url or '').strip():
        raise MetaPayloadError('网站 CAPI 事件缺少真实 event_source_url，未发送。')
    if is_website_submission and not str(submission.user_agent or '').strip():
        raise MetaPayloadError('网站 CAPI 事件缺少浏览器 User-Agent，未发送。')
    if is_website_submission and identifiers.get('client_event_id'):
        event_id = str(identifiers['client_event_id'])
    else:
        event_id = f'{submission.submission_key}:{stage_key}:{integration.id}'
    if is_ctwa:
        action_source = 'business_messaging'
    elif is_website_submission:
        action_source = 'website'
    else:
        action_source = 'system_generated'
    payload: dict[str, Any] = {
        'event_name': event_name,
        'event_time': int(meta_stage_event_time(submission, stage_key).timestamp()),
        'event_id': event_id,
        'action_source': action_source,
        'user_data': (
            build_meta_ctwa_user_data(submission)
            if is_ctwa
            else build_meta_user_data(submission, include_client_context=is_website_submission)
        ),
        'custom_data': {
            'form_code': submission.form.code,
            'form_category': submission.form.category,
            'lead_stage': stage_key,
        },
    }
    if is_ctwa:
        payload['messaging_channel'] = 'whatsapp'
        payload['custom_data']['event_source'] = 'crm' if stage_key != 'submitted' else 'business_messaging'
        if crm_event_name and crm_event_name != event_name:
            payload['custom_data']['crm_event_name'] = crm_event_name
        if stage_key == 'won' and submission.buyer_value is None:
            raise MetaPayloadError('WhatsApp Purchase 回传必须填写真实成交金额。')
    elif is_website_submission:
        payload['event_source_url'] = str(submission.source_url).strip()
    else:
        payload['custom_data']['event_source'] = 'crm'
        payload['custom_data']['lead_event_source'] = 'siteos_crm'
        payload['custom_data']['qualification_score'] = submission.qualification_score
    if stage_key == 'won' and submission.buyer_value is not None:
        payload['custom_data']['value'] = float(submission.buyer_value)
        payload['custom_data']['currency'] = submission.buyer_currency or 'USD'
    return payload


def queue_submission_event(
    submission: LeadSubmission,
    *,
    stage_key: str,
    event_name: str,
    include_external_meta: bool = False,
) -> list[LeadEventOutbox]:
    queued: list[LeadEventOutbox] = []
    identifiers = dict(submission.identifiers_json or {})
    is_external_submission = bool(identifiers.get('meta_lead_id') or identifiers.get('whatsapp_id'))
    is_website_submission = stage_key == 'submitted' and not identifiers.get('ctwa_clid') and not (
        identifiers.get('meta_lead_id') and not identifiers.get('client_event_id')
    )
    skipped_action_source = (
        'business_messaging'
        if identifiers.get('ctwa_clid')
        else 'website'
        if is_website_submission
        else 'system_generated'
    )
    skip_external_initial = stage_key == 'submitted' and is_external_submission and not include_external_meta
    meta_integrations = [] if skip_external_initial else enabled_meta_integrations(submission.site)
    if identifiers.get('ctwa_clid') and stage_key not in BUSINESS_MESSAGING_STAGE_EVENTS:
        meta_integrations = []
    for integration in meta_integrations:
        delivery_mode = integration_delivery_mode(integration)
        event_id = f'{submission.submission_key}:{stage_key}:{integration.id}'
        platform_event_name = event_name
        try:
            platform_event_name = meta_platform_event_name(
                submission,
                stage_key=stage_key,
                crm_event_name=event_name,
            )
            payload = build_meta_payload(
                submission,
                integration,
                stage_key=stage_key,
                event_name=platform_event_name,
                crm_event_name=event_name,
            )
        except MetaPayloadError as exc:
            LeadEventOutbox.objects.get_or_create(
                submission=submission,
                integration=integration,
                stage_key=stage_key,
                event_name=platform_event_name,
                defaults={
                    'event_id': event_id,
                    'action_source': skipped_action_source,
                    'payload_json': {},
                    'status': 'skipped',
                    'delivery_mode': delivery_mode,
                    'last_error': str(exc)[:1000],
                },
            )
            continue
        outbox, created = LeadEventOutbox.objects.get_or_create(
            submission=submission,
            integration=integration,
            stage_key=stage_key,
            event_name=platform_event_name,
            defaults={
                'event_id': payload['event_id'],
                'action_source': payload['action_source'],
                'payload_json': payload,
                'status': 'pending',
                'delivery_mode': delivery_mode,
            },
        )
        if created:
            queued.append(outbox)
    if stage_key in {'qualified', 'won'}:
        for integration in enabled_google_integrations(submission.site):
            delivery_mode = integration_delivery_mode(integration)
            event_id = f'{submission.submission_key}:{stage_key}:{integration.id}'
            try:
                payload = build_google_data_manager_payload(submission, integration, stage_key=stage_key)
            except GoogleDataManagerError as exc:
                LeadEventOutbox.objects.get_or_create(
                    submission=submission,
                    integration=integration,
                    stage_key=stage_key,
                    event_name=event_name,
                    defaults={
                        'event_id': event_id,
                        'action_source': 'business_messaging' if identifiers.get('ctwa_clid') else 'system_generated',
                        'payload_json': {},
                        'status': 'skipped',
                        'delivery_mode': delivery_mode,
                        'last_error': str(exc)[:1000],
                    },
                )
                continue
            outbox, created = LeadEventOutbox.objects.get_or_create(
                submission=submission,
                integration=integration,
                stage_key=stage_key,
                event_name=event_name,
                defaults={
                    'event_id': event_id,
                    'action_source': 'business_messaging' if identifiers.get('ctwa_clid') else 'system_generated',
                    'payload_json': payload,
                    'status': 'pending',
                    'delivery_mode': delivery_mode,
                },
            )
            if created:
                queued.append(outbox)
    return queued


def dispatch_meta_outbox_event(outbox: LeadEventOutbox) -> bool:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return False
    integration = outbox.integration
    attempted_at = timezone.now()
    outbox.delivery_mode = integration_delivery_mode(integration)
    outbox.last_attempt_at = attempted_at

    def fail(message: str, response: dict[str, Any] | None = None) -> bool:
        attempt_number = outbox.attempts + 1
        delay_minutes = min(60, 2 ** min(max(attempt_number - 1, 0), 6))
        outbox.status = 'failed'
        outbox.attempts = attempt_number
        outbox.last_error = message[:1000]
        if response is not None:
            outbox.response_json = response
        outbox.next_attempt_at = attempted_at + timedelta(minutes=delay_minutes)
        outbox.updated_at = attempted_at
        outbox.save(
            update_fields=[
                'status', 'delivery_mode', 'attempts', 'last_error', 'response_json',
                'last_attempt_at', 'next_attempt_at', 'updated_at',
            ]
        )
        return False

    access_token = resolve_meta_access_token(integration)
    if not access_token:
        return fail('Meta access token 未配置。')

    api_version = meta_api_version(integration)
    endpoint = f'https://graph.facebook.com/{api_version}/{integration.public_id}/events'
    request_payload = {
        'data': [outbox.payload_json],
        'partner_agent': 'siteos-leads-v1',
        'test_event_code': str((integration.config_json or {}).get('test_event_code') or '').strip() or None,
    }
    if not request_payload['test_event_code']:
        request_payload.pop('test_event_code')

    body = json.dumps(request_payload).encode('utf-8')
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8', errors='replace')
            parsed = json.loads(raw or '{}')
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        return fail(
            f'HTTP {exc.code}: {raw[:800]}',
            {'http_status': exc.code, 'body': raw[:4000]},
        )
    except Exception as exc:  # pragma: no cover
        return fail(str(exc)[:800])

    if not isinstance(parsed, dict) or parsed.get('error'):
        return fail('Meta CAPI 返回了错误响应。', parsed if isinstance(parsed, dict) else {'body': raw[:4000]})
    try:
        events_received = int(parsed.get('events_received', 0))
    except (TypeError, ValueError):
        events_received = 0
    if events_received < 1:
        return fail('Meta CAPI 未确认接收到事件。', parsed)

    outbox.status = 'sent'
    outbox.attempts += 1
    outbox.last_error = ''
    outbox.response_json = parsed
    outbox.provider_request_id = str(parsed.get('fbtrace_id') or '').strip()
    outbox.provider_received_at = attempted_at
    outbox.match_status = 'unknown'
    outbox.next_attempt_at = None
    outbox.dispatched_at = attempted_at
    outbox.updated_at = attempted_at
    outbox.save(
        update_fields=[
            'status', 'delivery_mode', 'attempts', 'last_error', 'response_json',
            'provider_request_id', 'provider_received_at', 'match_status',
            'last_attempt_at', 'next_attempt_at', 'dispatched_at', 'updated_at',
        ]
    )
    return True


def dispatch_claimed_outbox_event(outbox: LeadEventOutbox) -> bool:
    """Send one event after a caller has exclusively claimed its row."""

    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return False
    if outbox.status not in {'sending', 'processing'}:
        return False
    provider_code = outbox.integration.provider.code
    if provider_code == 'meta' and outbox.integration.integration_type == 'pixel':
        return dispatch_meta_outbox_event(outbox)
    if provider_code == 'google' and outbox.integration.integration_type == 'data_manager':
        return dispatch_google_outbox_event(outbox)
    outbox.status = 'failed'
    outbox.attempts += 1
    outbox.last_error = f'不支持的回传接入：{provider_code}/{outbox.integration.integration_type}'
    outbox.last_attempt_at = timezone.now()
    outbox.next_attempt_at = outbox.last_attempt_at + timedelta(minutes=60)
    outbox.updated_at = outbox.last_attempt_at
    outbox.save(
        update_fields=[
            'status', 'attempts', 'last_error', 'last_attempt_at',
            'next_attempt_at', 'updated_at',
        ]
    )
    return False


def dispatch_outbox_event(outbox: LeadEventOutbox) -> bool:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return False
    if outbox.status in {'pending', 'failed'}:
        claimed_at = timezone.now()
        claimed = LeadEventOutbox.objects.filter(
            id=outbox.id,
            status=outbox.status,
        ).update(status='sending', updated_at=claimed_at)
        if claimed != 1:
            return False
        outbox.status = 'sending'
        outbox.updated_at = claimed_at
    elif outbox.status != 'processing':
        return False

    return dispatch_claimed_outbox_event(outbox)


def dispatch_pending_outbox(
    *,
    submission: LeadSubmission | None = None,
    site: Site | None = None,
    limit: int = 20,
    max_attempts: int | None = None,
    failed_before=None,
) -> tuple[int, int]:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return 0, 0
    now = timezone.now()
    claim_timeout = max(int(getattr(settings, 'SITEOS_OUTBOX_CLAIM_TIMEOUT_MINUTES', 5)), 1)
    stale_sending_before = now - timedelta(minutes=claim_timeout)
    LeadEventOutbox.objects.filter(
        status='sending',
        updated_at__lte=stale_sending_before,
    ).update(
        status='failed',
        last_error='发送认领超时，已重新进入安全重试队列。',
        next_attempt_at=now,
        updated_at=now,
    )
    retry_filter = Q(status='pending') | Q(status='processing') | Q(status='failed')
    if max_attempts is not None:
        retry_filter = Q(status='pending') | Q(status='processing') | Q(status='failed', attempts__lt=max_attempts)
    if failed_before is not None:
        retry_filter &= Q(status__in=['pending', 'processing']) | Q(status='failed', updated_at__lte=failed_before)
    retry_filter &= Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)
    queryset = LeadEventOutbox.objects.select_related('integration__provider').filter(retry_filter).order_by('created_at', 'id')
    if submission is not None:
        queryset = queryset.filter(submission=submission)
    if site is not None:
        queryset = queryset.filter(submission__site=site, integration__site=site)
    attempted = 0
    succeeded = 0
    for outbox in queryset[:limit]:
        attempted += 1
        if dispatch_outbox_event(outbox):
            succeeded += 1
    return attempted, succeeded


def create_submission(
    *,
    form_definition: LeadFormDefinition,
    locale: SiteLocale | None,
    payload: dict[str, Any],
    client_ip: str,
    user_agent: str,
) -> LeadCaptureResult:
    cleaned = clean_submission_input(payload, form_definition=form_definition)
    route = None
    cta = None
    route_path = cleaned.pop('route_path')
    cta_code = cleaned.pop('cta_code')
    now = timezone.now()

    if client_ip:
        rate_cutoff = now - timedelta(minutes=settings.SITEOS_LEAD_RATE_LIMIT_MINUTES)
        recent_count = LeadSubmission.objects.filter(
            site=form_definition.site,
            client_ip=client_ip,
            submitted_at__gte=rate_cutoff,
        ).count()
        if recent_count >= settings.SITEOS_LEAD_RATE_LIMIT_COUNT:
            raise LeadCaptureError(
                '提交过于频繁，请稍后重试或通过 WhatsApp 联系我们。',
                code='rate_limited',
                status=429,
            )

    if route_path:
        route = PageRoute.objects.filter(site=form_definition.site, path=route_path).first()
    if cta_code:
        cta = Cta.objects.filter(site=form_definition.site, code=cta_code).first()

    duplicate_cutoff = now - timedelta(minutes=settings.SITEOS_LEAD_DUPLICATE_MINUTES)
    existing = (
        LeadSubmission.objects.filter(
            site=form_definition.site,
            form=form_definition,
            dedupe_key=cleaned['dedupe_key'],
            submitted_at__gte=duplicate_cutoff,
        )
        .order_by('-submitted_at', '-id')
        .first()
    )
    if existing:
        return LeadCaptureResult(
            submission=existing,
            queued_events=0,
            dispatch_attempted=0,
            dispatch_succeeded=0,
            duplicate=True,
            notification_sent=existing.notification_status == 'sent',
        )

    previous_contact = (
        LeadSubmission.objects.filter(
            site=form_definition.site,
            contact_key=cleaned['contact_key'],
            submitted_at__gte=now - timedelta(days=180),
        )
        .order_by('-submitted_at', '-id')
        .first()
    )

    with transaction.atomic():
        _acquire_dedupe_lock(cleaned['dedupe_key'])
        locked_duplicate = (
            LeadSubmission.objects.select_for_update()
            .filter(
                site=form_definition.site,
                form=form_definition,
                dedupe_key=cleaned['dedupe_key'],
                submitted_at__gte=duplicate_cutoff,
            )
            .order_by('-submitted_at', '-id')
            .first()
        )
        if locked_duplicate:
            return LeadCaptureResult(
                submission=locked_duplicate,
                queued_events=0,
                dispatch_attempted=0,
                dispatch_succeeded=0,
                duplicate=True,
                notification_sent=locked_duplicate.notification_status == 'sent',
            )

        submission = LeadSubmission.objects.create(
            submission_key=uuid4(),
            form=form_definition,
            site=form_definition.site,
            locale=locale or form_definition.locale,
            route=route,
            cta=cta,
            team=sales_team_for_form(form_definition),
            stage='new',
            full_name=cleaned['full_name'],
            email=cleaned['email'],
            phone=cleaned['phone'],
            company=cleaned['company'],
            country=cleaned['country'],
            message=cleaned['message'],
            source_url=cleaned['source_url'],
            referrer_url=cleaned['referrer_url'],
            source_channel=cleaned['source_channel'],
            source_detail=cleaned['source_detail'],
            client_ip=(client_ip or '')[:120],
            user_agent=(user_agent or '')[:1000],
            buyer_value=cleaned['buyer_value'],
            buyer_currency=cleaned['buyer_currency'],
            payload_json=cleaned['payload_json'],
            identifiers_json=cleaned['identifiers_json'],
            utm_json=cleaned['utm_json'],
            consent_json=cleaned['consent_json'],
            config_json=cleaned['config_json'],
            contact_key=cleaned['contact_key'],
            dedupe_key=cleaned['dedupe_key'],
            duplicate_of=previous_contact,
            notification_status='pending',
            submitted_at=now,
            stage_updated_at=now,
        )
        record_initial_consent_snapshot(submission, source='website_form')
        queued_outbox: list[LeadEventOutbox] = []
        if form_definition.capi_enabled and bool(cleaned['consent_json'].get('marketing')):
            queued_outbox = queue_submission_event(
                submission,
                stage_key='submitted',
                event_name=form_definition.submission_event_name or 'Lead',
            )

    from leads.notifications import send_submission_notification

    notification_sent = send_submission_notification(submission)
    attempted = 0
    succeeded = 0
    for outbox in queued_outbox:
        attempted += 1
        if dispatch_outbox_event(outbox):
            succeeded += 1
    return LeadCaptureResult(
        submission=submission,
        queued_events=len(queued_outbox),
        dispatch_attempted=attempted,
        dispatch_succeeded=succeeded,
        notification_sent=notification_sent,
    )


def update_submission_stage(
    *,
    submission: LeadSubmission,
    stage: str,
    buyer_value: Decimal | None,
    buyer_currency: str,
) -> tuple[LeadSubmission, int, int, int]:
    qualification_score = int(getattr(submission, 'qualification_score', 0) or 0)
    qualification_overridden = bool(getattr(submission, 'qualification_overridden', False))
    if stage in {'qualified', 'won'} and qualification_score < 80 and not qualification_overridden:
        raise LeadCaptureError(
            '标记为高质量或成交前，必须满足五项合格判定中的至少四项；特殊情况需人工覆盖并说明原因。',
            code='qualification_required',
        )

    previous_stage = submission.stage
    stage_changed = previous_stage != stage
    contacted_was_missing = submission.contacted_at is None
    qualified_was_missing = submission.qualified_at is None
    now = timezone.now()
    submission.stage = stage
    if stage_changed:
        submission.stage_updated_at = now
    if stage in {'contacted', 'qualified', 'won'} and submission.contacted_at is None:
        submission.contacted_at = now
    if stage in {'qualified', 'won'} and submission.qualified_at is None:
        submission.qualified_at = now
    if stage == 'won' and submission.won_at is None:
        submission.won_at = now
    if stage == 'lost' and submission.lost_at is None:
        submission.lost_at = now
    submission.buyer_value = buyer_value
    submission.buyer_currency = buyer_currency
    submission.updated_at = now
    submission.save(
        update_fields=[
            'stage',
            'stage_updated_at',
            'contacted_at',
            'qualified_at',
            'won_at',
            'lost_at',
            'buyer_value',
            'buyer_currency',
            'updated_at',
        ]
    )

    queued_outbox: list[LeadEventOutbox] = []
    attempted = 0
    succeeded = 0
    event_specs: list[tuple[str, str]] = []
    if stage_changed and stage in {'contacted', 'qualified', 'won'}:
        if stage == 'contacted' or contacted_was_missing:
            event_specs.append(('contacted', submission.form.contacted_event_name or 'contacted_lead'))
        if stage == 'qualified' or (stage == 'won' and qualified_was_missing):
            event_specs.append(('qualified', submission.form.qualified_event_name or 'qualified_lead'))
        if stage == 'won':
            event_specs.append(('won', submission.form.won_event_name or 'converted'))
    if event_specs and submission.form.capi_enabled and bool((submission.consent_json or {}).get('marketing')):
        for stage_key, event_name in event_specs:
            queued_outbox.extend(
                queue_submission_event(
                    submission,
                    stage_key=stage_key,
                    event_name=event_name,
                )
            )
        for outbox in queued_outbox:
            attempted += 1
            if dispatch_outbox_event(outbox):
                succeeded += 1
    return submission, len(queued_outbox), attempted, succeeded
