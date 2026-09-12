from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone as datetime_timezone
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from leads.inbound import inbound_claim_stale_before, inbound_receipt_retryability
from leads.models import LeadEventOutbox, LeadInboundEvent
from marketing.models import MarketingIntegration

from .marketing_queries import (
    marketing_retry_max_attempts,
    outbox_retry_contract,
    retryable_outbox_q,
)


DEFAULT_PAGE_SIZE = 25
PAGE_SIZE_OPTIONS = (25, 50, 100)
DEFAULT_STALE_MINUTES = 15

_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r'''(?i)(?<![\w])(["']?(?:authorization|access[_ -]?token|refresh[_ -]?token|'''
    r'''app[_ -]?secret|client[_ -]?secret|api[_ -]?key|token|secret|password|cookie)["']?)'''
    r'''(?![\w])(\s*[:=]\s*)("[^"]*"|'[^']*'|[^,;}\s]+)'''
)
_BEARER_RE = re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+')
_SENSITIVE_FIELD_RE = re.compile(
    r'''(?i)(["']?(?:email|phone|full_name|first_name|last_name|client_ip|payload|user_data)["']?'''
    r'''\s*[:=]\s*)("[^"]*"|'[^']*'|[^,;}\s]+)'''
)
_EMAIL_RE = re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b')
_PHONE_RE = re.compile(r'(?<![\w#])(?:\+?\d[\d\s().-]{7,}\d)(?!\w)')
_WHITESPACE_RE = re.compile(r'\s+')

OUTBOUND_STATUS = {
    'pending': ('等待发送', 'secondary'),
    'sending': ('正在发送', 'info'),
    'processing': ('平台处理中', 'info'),
    'validated': ('校验通过', 'success'),
    'sent': ('平台已接收', 'success'),
    'partial': ('部分成功', 'warning'),
    'failed': ('发送失败', 'danger'),
    'skipped': ('已跳过', 'secondary'),
}
INBOUND_STATUS = {
    'pending': ('等待处理', 'secondary'),
    'processing': ('正在处理', 'info'),
    'processed': ('处理完成', 'success'),
    'failed': ('处理失败', 'danger'),
}

# Defined after the status label maps so query canonicalization and list
# filtering cannot drift apart.
ALLOWED_EVENT_STATUSES = frozenset({
    'needs_attention', 'all', 'waiting', 'recovered_24h',
    *OUTBOUND_STATUS.keys(), *INBOUND_STATUS.keys(),
})


def marketing_event_stale_minutes() -> int:
    return max(int(getattr(settings, 'SITEOS_MARKETING_EVENT_STALE_MINUTES', DEFAULT_STALE_MINUTES)), 1)


def redact_event_error(value, *, limit: int = 180) -> str:
    """Return an operations-safe error summary, never a credential dump."""

    text = _WHITESPACE_RE.sub(' ', str(value or '')).strip()
    if not text:
        return '系统没有留下可读错误，请查看接入状态或人工核对。'
    text = _BEARER_RE.sub('Bearer [已隐藏]', text)
    text = _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f'{match.group(1)}{match.group(2)}[已隐藏]', text)
    text = _SENSITIVE_FIELD_RE.sub(lambda match: f'{match.group(1)}[字段已隐藏]', text)
    text = _EMAIL_RE.sub('[邮箱已隐藏]', text)
    text = _PHONE_RE.sub('[电话已隐藏]', text)
    return text if len(text) <= limit else f'{text[: max(limit - 1, 1)].rstrip()}…'


def _bounded_query(params) -> str:
    return str(params.get('q') or '').strip()[:200]


def _choice(params, key: str, allowed: set[str], default: str = '') -> str:
    value = str(params.get(key) or '').strip().lower()
    return value if value in allowed else default


def _positive_int(value, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def canonical_marketing_event_query(params) -> str:
    """Normalize the bounded GET contract and discard untrusted parameters."""

    query = _bounded_query(params)
    direction = _choice(params, 'direction', {'outbound', 'inbound'})
    raw_provider = str(params.get('provider') or '').strip().lower()[:80]
    provider = raw_provider if re.fullmatch(r'[a-z0-9_-]{1,80}', raw_provider) else ''
    status = _choice(params, 'status', set(ALLOWED_EVENT_STATUSES))
    retry = _choice(params, 'retry', {'due'})
    page = _positive_int(params.get('page'), default=1)
    page_size = _positive_int(params.get('page_size'), default=DEFAULT_PAGE_SIZE)
    if page_size not in PAGE_SIZE_OPTIONS:
        page_size = DEFAULT_PAGE_SIZE

    values = {
        'q': query,
        'direction': direction,
        'provider': provider,
        'status': status if status != 'needs_attention' else '',
        'retry': retry,
        'page': page if page > 1 else '',
        'page_size': page_size if page_size != DEFAULT_PAGE_SIZE else '',
    }
    return urlencode({key: value for key, value in values.items() if value not in {'', None}})


def _outbound_attention_q(*, stale_before) -> Q:
    return Q(status__in={'failed', 'partial'}) | Q(
        status__in={'pending', 'sending', 'processing'},
        updated_at__lte=stale_before,
    )


def _inbound_attention_q(*, stale_before, claim_stale_before) -> Q:
    return (
        Q(status='failed')
        | Q(status='pending', updated_at__lte=stale_before)
        | Q(status='processing', updated_at__lte=claim_stale_before)
    )


def _outbound_waiting_q(*, stale_before) -> Q:
    return (
        Q(status__in={'pending', 'sending', 'processing'}, updated_at__gt=stale_before)
        | Q(status__in={'validated', 'sent'}, provider_processed_at__isnull=True)
    )


def _inbound_waiting_q(*, stale_before, claim_stale_before) -> Q:
    return (
        Q(status='pending', updated_at__gt=stale_before)
        | Q(status='processing', updated_at__gt=claim_stale_before)
    )


def _apply_status(queryset, *, direction: str, status: str, now, stale_before):
    if status == 'all':
        return queryset
    if status == 'needs_attention':
        predicate = (
            _outbound_attention_q(stale_before=stale_before)
            if direction == 'outbound'
            else _inbound_attention_q(
                stale_before=stale_before,
                claim_stale_before=inbound_claim_stale_before(now=now),
            )
        )
        return queryset.filter(predicate)
    if status == 'waiting':
        predicate = (
            _outbound_waiting_q(stale_before=stale_before)
            if direction == 'outbound'
            else _inbound_waiting_q(
                stale_before=stale_before,
                claim_stale_before=inbound_claim_stale_before(now=now),
            )
        )
        return queryset.filter(predicate)
    if status == 'recovered_24h':
        cutoff = now - timedelta(hours=24)
        if direction == 'outbound':
            return queryset.filter(
                status__in={'validated', 'sent'},
                attempts__gt=1,
                updated_at__gte=cutoff,
            )
        return queryset.filter(
            status='processed',
            attempts__gt=1,
            processed_at__gte=cutoff,
        )
    return queryset.filter(status=status)


def _apply_base_filters(*, site, params):
    direction = _choice(params, 'direction', {'outbound', 'inbound'})
    provider = str(params.get('provider') or '').strip().lower()[:80]
    query = _bounded_query(params)

    outboxes = LeadEventOutbox.objects.filter(
        submission__site=site,
        integration__site=site,
    )
    inbound = LeadInboundEvent.objects.filter(integration__site=site)
    if direction == 'outbound':
        inbound = inbound.none()
    elif direction == 'inbound':
        outboxes = outboxes.none()
    if provider:
        outboxes = outboxes.filter(integration__provider__code=provider)
        inbound = inbound.filter(integration__provider__code=provider)
    if query:
        outboxes = outboxes.filter(
            Q(event_name__icontains=query)
            | Q(event_id__icontains=query)
            | Q(provider_request_id__icontains=query)
            | Q(integration__name__icontains=query)
            | Q(integration__provider__name__icontains=query)
            | Q(integration__provider__code__icontains=query)
        )
        inbound = inbound.filter(
            Q(event_type__icontains=query)
            | Q(external_event_id__icontains=query)
            | Q(integration__name__icontains=query)
            | Q(integration__provider__name__icontains=query)
            | Q(integration__provider__code__icontains=query)
        )
    return outboxes, inbound, direction, provider, query


def _aware_sort_value(value) -> float:
    if not isinstance(value, datetime):
        return 0.0
    if timezone.is_naive(value):
        value = timezone.make_aware(value, datetime_timezone.utc)
    return value.timestamp()


def _integration_credential_label(integration: MarketingIntegration) -> str:
    config = dict(integration.config_json or {})
    has_reference = bool(integration.secret_ref) or any(
        str(key).endswith('_ref') and bool(value)
        for key, value in config.items()
    )
    return '凭据已配置' if has_reference else '凭据需检查'


def _is_demo_outbox(item: LeadEventOutbox) -> bool:
    payload = dict(item.payload_json or {})
    return str(item.event_id or '').startswith('ui-demo-') or bool(payload.get('fixture'))


def _is_demo_inbound(item: LeadInboundEvent) -> bool:
    payload = dict(item.payload_json or {})
    return str(item.external_event_id or '').startswith('demo-') or bool(payload.get('fixture'))


def _outbound_row(item: LeadEventOutbox, *, now) -> dict:
    retryable, retry_reason = outbox_retry_contract(item, now=now)
    status_label, status_tone = OUTBOUND_STATUS.get(
        item.status,
        (item.status or '未知状态', 'secondary'),
    )
    action = None
    if retryable:
        action = {
            'kind': 'retry',
            'label': '精确重试',
            'url': reverse('console:marketing_outbox_retry', args=[item.id]),
        }
    elif item.status in {'failed', 'partial'}:
        action = {
            'kind': 'manual',
            'label': '需人工核对',
            'reason': retry_reason or '部分成功不会自动重试。',
        }
    return {
        'key': f'outbound-{item.id}',
        'id': item.id,
        'direction': 'outbound',
        'direction_label': '出站',
        'direction_tone': 'primary',
        'direction_icon': 'send',
        'event_name': item.event_name or '未命名回传事件',
        'record_label': f'Outbox #{item.id}',
        'external_id': str(item.event_id or ''),
        'provider_name': item.integration.provider.name,
        'provider_code': item.integration.provider.code,
        'integration_name': item.integration.name,
        'integration_enabled': item.integration.enabled,
        'credential_label': _integration_credential_label(item.integration),
        'business_object': f'线索 #{item.submission_id}',
        'status': item.status,
        'status_label': status_label,
        'status_tone': status_tone,
        'attempts': item.attempts,
        'last_activity_at': item.last_attempt_at or item.updated_at,
        'next_retry_at': item.next_attempt_at,
        'error_summary': redact_event_error(item.last_error),
        'action': action,
        'demo': _is_demo_outbox(item),
        '_sort_at': item.updated_at,
    }


def _inbound_row(item: LeadInboundEvent, *, now) -> dict:
    retryable, retry_reason = inbound_receipt_retryability(
        item,
        max_attempts=marketing_retry_max_attempts(),
        now=now,
    )
    status_label, status_tone = INBOUND_STATUS.get(
        item.status,
        (item.status or '未知状态', 'secondary'),
    )
    action = None
    if retryable:
        action = {
            'kind': 'retry',
            'label': '精确重试',
            'url': reverse('console:marketing_inbound_retry', args=[item.id]),
        }
    elif item.status == 'failed':
        action = {
            'kind': 'manual',
            'label': '需人工核对',
            'reason': retry_reason,
        }
    return {
        'key': f'inbound-{item.id}',
        'id': item.id,
        'direction': 'inbound',
        'direction_label': '入站',
        'direction_tone': 'azure',
        'direction_icon': 'webhook',
        'event_name': item.event_type or '未命名入站事件',
        'record_label': f'Receipt #{item.id}',
        'external_id': str(item.external_event_id or ''),
        'provider_name': item.integration.provider.name,
        'provider_code': item.integration.provider.code,
        'integration_name': item.integration.name,
        'integration_enabled': item.integration.enabled,
        'credential_label': _integration_credential_label(item.integration),
        'business_object': (
            f'线索 #{item.submission_id}' if item.submission_id else '尚未生成线索'
        ),
        'status': item.status,
        'status_label': status_label,
        'status_tone': status_tone,
        'attempts': item.attempts,
        'last_activity_at': item.updated_at or item.received_at,
        'next_retry_at': None,
        'error_summary': redact_event_error(item.last_error),
        'action': action,
        'demo': _is_demo_inbound(item),
        '_sort_at': item.updated_at or item.received_at,
    }


def _url_builder(*, filters: dict[str, str | int]):
    endpoint = reverse('console:marketing_events')

    def build(**overrides) -> str:
        values = dict(filters)
        values.update(overrides)
        encoded = {
            key: value
            for key, value in values.items()
            if value not in {'', None}
            and not (key == 'page' and value == 1)
            and not (key == 'page_size' and value == DEFAULT_PAGE_SIZE)
            and not (key == 'status' and value == 'needs_attention')
        }
        return f'{endpoint}?{urlencode(encoded)}' if encoded else endpoint

    return build


def build_marketing_event_workspace(*, site, params, now=None) -> dict:
    effective_now = now or timezone.now()
    stale_before = effective_now - timedelta(minutes=marketing_event_stale_minutes())
    base_outboxes, base_inbound, direction, provider, query = _apply_base_filters(
        site=site,
        params=params,
    )
    explicit_status = _choice(params, 'status', set(ALLOWED_EVENT_STATUSES))
    status = explicit_status or 'needs_attention'
    retry_filter = _choice(params, 'retry', {'due'})

    filtered_outboxes = _apply_status(
        base_outboxes,
        direction='outbound',
        status=status,
        now=effective_now,
        stale_before=stale_before,
    )
    filtered_inbound = _apply_status(
        base_inbound,
        direction='inbound',
        status=status,
        now=effective_now,
        stale_before=stale_before,
    )
    if retry_filter == 'due':
        filtered_outboxes = filtered_outboxes.filter(
            retryable_outbox_q(now=effective_now)
        )
        filtered_inbound = filtered_inbound.none()

    page_size_requested = _positive_int(params.get('page_size'), default=DEFAULT_PAGE_SIZE)
    page_size = (
        page_size_requested
        if page_size_requested in PAGE_SIZE_OPTIONS
        else DEFAULT_PAGE_SIZE
    )
    requested_page = _positive_int(params.get('page'), default=1)
    total = filtered_outboxes.count() + filtered_inbound.count()
    total_pages = max(1, math.ceil(total / page_size))
    page = min(requested_page, total_pages)

    window = page * page_size
    outbox_records = list(
        filtered_outboxes.select_related('integration__provider', 'submission')
        .order_by('-updated_at', '-id')[:window]
    )
    inbound_records = list(
        filtered_inbound.select_related('integration__provider', 'submission')
        .order_by('-updated_at', '-id')[:window]
    )
    rows = [
        *(_outbound_row(item, now=effective_now) for item in outbox_records),
        *(_inbound_row(item, now=effective_now) for item in inbound_records),
    ]
    rows.sort(
        key=lambda row: (
            _aware_sort_value(row['_sort_at']),
            row['id'],
            row['direction'],
        ),
        reverse=True,
    )
    start = (page - 1) * page_size
    rows = rows[start:start + page_size]
    for row in rows:
        row.pop('_sort_at', None)

    attention_count = (
        _apply_status(
            base_outboxes,
            direction='outbound',
            status='needs_attention',
            now=effective_now,
            stale_before=stale_before,
        ).count()
        + _apply_status(
            base_inbound,
            direction='inbound',
            status='needs_attention',
            now=effective_now,
            stale_before=stale_before,
        ).count()
    )
    retry_count = base_outboxes.filter(
        retryable_outbox_q(now=effective_now)
    ).count()
    waiting_count = (
        _apply_status(
            base_outboxes,
            direction='outbound',
            status='waiting',
            now=effective_now,
            stale_before=stale_before,
        ).count()
        + _apply_status(
            base_inbound,
            direction='inbound',
            status='waiting',
            now=effective_now,
            stale_before=stale_before,
        ).count()
    )
    recovered_count = (
        _apply_status(
            base_outboxes,
            direction='outbound',
            status='recovered_24h',
            now=effective_now,
            stale_before=stale_before,
        ).count()
        + _apply_status(
            base_inbound,
            direction='inbound',
            status='recovered_24h',
            now=effective_now,
            stale_before=stale_before,
        ).count()
    )

    filters: dict[str, str | int] = {
        'q': query,
        'direction': direction,
        'provider': provider,
        'status': explicit_status,
        'retry': retry_filter,
        'page_size': page_size if page_size != DEFAULT_PAGE_SIZE else '',
        'page': page,
    }
    build_url = _url_builder(filters=filters)
    provider_options = list(
        MarketingIntegration.objects.filter(site=site)
        .values('provider__code', 'provider__name')
        .distinct()
        .order_by('provider__name', 'provider__code')
    )
    total_site_events = (
        LeadEventOutbox.objects.filter(
            submission__site=site,
            integration__site=site,
        ).count()
        + LeadInboundEvent.objects.filter(integration__site=site).count()
    )
    active_filter_labels = []
    if query:
        active_filter_labels.append(f'搜索“{query}”')
    if direction:
        active_filter_labels.append('仅出站' if direction == 'outbound' else '仅入站')
    if provider:
        active_filter_labels.append(f'平台 {provider}')
    if explicit_status and explicit_status != 'needs_attention':
        status_option = next(
            (item for item in _status_options() if item['value'] == explicit_status),
            None,
        )
        active_filter_labels.append(status_option['label'] if status_option else explicit_status)
    if retry_filter:
        active_filter_labels.append('仅可安全重试')

    return {
        'site': site,
        'title': '事件运营',
        'description': '查看平台接收、发送与处理证据，并恢复可安全重试的失败。',
        'filters': {
            'q': query,
            'direction': direction,
            'provider': provider,
            'status': status,
            'explicit_status': explicit_status,
            'retry': retry_filter,
            'page_size': page_size,
        },
        'options': {
            'directions': [
                {'value': '', 'label': '全部方向'},
                {'value': 'outbound', 'label': '出站'},
                {'value': 'inbound', 'label': '入站'},
            ],
            'providers': [
                {'value': '', 'label': '全部平台'},
                *[
                    {
                        'value': item['provider__code'],
                        'label': item['provider__name'],
                    }
                    for item in provider_options
                ],
            ],
            'statuses': _status_options(),
            'page_sizes': PAGE_SIZE_OPTIONS,
        },
        'summary': [
            {
                'label': '需要处理', 'value': attention_count,
                'description': '失败、部分成功或处理超时', 'tone': 'danger',
                'icon': 'circle-alert', 'href': build_url(status='needs_attention', retry='', page=1),
                'active': status == 'needs_attention' and retry_filter != 'due',
            },
            {
                'label': '可安全重试', 'value': retry_count,
                'description': '服务端确认已到期的出站事件', 'tone': 'warning',
                'icon': 'refresh-cw', 'href': build_url(status='needs_attention', retry='due', page=1),
                'active': retry_filter == 'due',
            },
            {
                'label': '等待平台', 'value': waiting_count,
                'description': '仍在合理等待窗口内', 'tone': 'info',
                'icon': 'clock-3', 'href': build_url(status='waiting', retry='', page=1),
                'active': status == 'waiting',
            },
            {
                'label': '最近 24 小时恢复', 'value': recovered_count,
                'description': '重试后进入完成状态', 'tone': 'success',
                'icon': 'circle-check-big', 'href': build_url(status='recovered_24h', retry='', page=1),
                'active': status == 'recovered_24h',
            },
        ],
        'rows': rows,
        'total': total,
        'total_site_events': total_site_events,
        'active_filter_labels': active_filter_labels,
        'clear_filters_url': reverse('console:marketing_events'),
        'view_all_url': build_url(status='all', retry='', page=1),
        'platforms_url': f'{reverse("console:marketing")}?tab=integrations',
        'attribution_url': reverse('console:marketing_attribution'),
        'current_url': build_url(page=page),
        'pagination': {
            'current_page': page,
            'total_pages': total_pages,
            'previous_url': build_url(page=page - 1) if page > 1 else '',
            'next_url': build_url(page=page + 1) if page < total_pages else '',
            'start': start + 1 if total else 0,
            'end': min(start + page_size, total),
        },
    }


def _status_options() -> list[dict[str, str]]:
    return [
        {'value': 'needs_attention', 'label': '需要处理（默认）'},
        {'value': 'all', 'label': '全部状态'},
        {'value': 'failed', 'label': '失败'},
        {'value': 'partial', 'label': '部分成功'},
        {'value': 'waiting', 'label': '等待平台'},
        {'value': 'pending', 'label': '等待处理'},
        {'value': 'processing', 'label': '处理中'},
        {'value': 'sent', 'label': '平台已接收'},
        {'value': 'processed', 'label': '处理完成'},
        {'value': 'recovered_24h', 'label': '最近 24 小时恢复'},
    ]
