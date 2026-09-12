from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from leads.models import LeadEventOutbox, LeadInboundEvent, LeadSubmission, PrivacyRequest
from leads.readiness import checks_overall, inspect_marketing_integration
from marketing.models import MarketingIntegration


SUPPORTED_ADAPTERS = frozenset({
    ('meta', 'pixel'),
    ('meta', 'leadgen'),
    ('whatsapp', 'cloud_api'),
    ('google', 'data_manager'),
})

INTEGRATION_DIRECTIONS = {
    ('meta', 'pixel'): 'outbound',
    ('google', 'data_manager'): 'outbound',
    ('meta', 'leadgen'): 'inbound',
    ('whatsapp', 'cloud_api'): 'inbound',
}


def marketing_retry_max_attempts() -> int:
    return max(int(getattr(settings, 'SITEOS_MARKETING_RETRY_MAX_ATTEMPTS', 8)), 1)


def retryable_outbox_q(*, now=None, max_attempts: int | None = None) -> Q:
    """Return the single database contract for an exact safe retry."""

    cutoff = now or timezone.now()
    attempts = max_attempts or marketing_retry_max_attempts()
    return (
        Q(status='failed', attempts__lt=attempts, integration__enabled=True)
        & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=cutoff))
    )


def outbox_retry_contract(
    item,
    *,
    now=None,
    max_attempts: int | None = None,
) -> tuple[bool, str]:
    """Mirror :func:`retryable_outbox_q` with an operator-readable reason."""

    cutoff = now or timezone.now()
    attempts = max_attempts or marketing_retry_max_attempts()
    if item.status != 'failed':
        return False, f'当前状态 {item.status} 已不能重试。'
    if not item.integration.enabled:
        return False, '平台接入已停用，请先核对接入配置。'
    if item.attempts >= attempts:
        return False, f'已达到最多 {attempts} 次发送上限。'
    if item.next_attempt_at and item.next_attempt_at > cutoff:
        return False, '尚未到服务端允许的下次重试时间。'
    return True, ''


def outbox_retry_is_due(item, *, now=None, max_attempts: int | None = None) -> bool:
    """Boolean compatibility wrapper around the canonical retry contract."""

    retryable, _reason = outbox_retry_contract(
        item,
        now=now,
        max_attempts=max_attempts,
    )
    return retryable


def _money(value) -> str:
    return str(value if value is not None else Decimal('0'))


def _currency_value_summary(
    rows,
    *,
    currency_key: str = 'buyer_currency',
    amount_key: str = 'amount',
) -> dict:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        currency = str(row.get(currency_key) or 'USD').strip().upper() or 'USD'
        totals[currency] += Decimal(str(row.get(amount_key) or 0))
    items = [
        {'currency': currency, 'amount': _money(amount)}
        for currency, amount in sorted(totals.items())
    ]
    return {
        'items': items,
        'label': ' / '.join(f'{item["currency"]} {item["amount"]}' for item in items) or '0',
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100 / denominator, 1)


def _matching_completeness(leads) -> dict:
    total = 0
    contactable = 0
    browser_identified = 0
    ad_click_identified = 0
    external_lead_identified = 0
    for item in leads.values('email', 'phone', 'identifiers_json', 'consent_json').iterator(chunk_size=1000):
        consent = dict(item.get('consent_json') or {})
        if not bool(consent.get('marketing')):
            continue
        total += 1
        identifiers = dict(item.get('identifiers_json') or {})
        contactable += int(bool(str(item.get('email') or '').strip() or str(item.get('phone') or '').strip()))
        browser_identified += int(bool(str(identifiers.get('fbp') or '').strip()))
        ad_click_identified += int(any(
            bool(str(identifiers.get(key) or '').strip())
            for key in ('fbc', 'fbclid', 'gclid', 'gbraid', 'wbraid', 'ctwa_clid')
        ))
        external_lead_identified += int(any(
            bool(str(identifiers.get(key) or '').strip())
            for key in ('meta_lead_id', 'whatsapp_id', 'external_id')
        ))
    return {
        'eligible_leads': total,
        'contactable': {'count': contactable, 'percent': _ratio(contactable, total)},
        'browser_identifier': {'count': browser_identified, 'percent': _ratio(browser_identified, total)},
        'ad_click_identifier': {'count': ad_click_identified, 'percent': _ratio(ad_click_identified, total)},
        'external_lead_identifier': {
            'count': external_lead_identified,
            'percent': _ratio(external_lead_identified, total),
        },
    }


def _integration_state(integration: MarketingIntegration, outboxes, inbound_events) -> dict:
    provider_code = integration.provider.code
    adapter_key = (provider_code, integration.integration_type)
    readiness_checks = inspect_marketing_integration(integration)
    readiness = checks_overall(readiness_checks)
    latest_check = integration.checks.order_by('-checked_at', '-id').first()
    status_counts = Counter(outboxes.values_list('status', flat=True))
    mode_counts = Counter(outboxes.values_list('delivery_mode', flat=True))
    match_counts = Counter(outboxes.values_list('match_status', flat=True))
    test_received = outboxes.filter(delivery_mode='test', provider_received_at__isnull=False).exists()
    live_received = outboxes.filter(delivery_mode='live', provider_received_at__isnull=False).exists()
    provider_processed = outboxes.filter(provider_processed_at__isnull=False).exists()
    inbound_status_counts = Counter(inbound_events.values_list('status', flat=True))
    inbound_received = inbound_events.exists()
    inbound_processed = inbound_events.filter(status='processed', processed_at__isnull=False).exists()
    return {
        'id': integration.id,
        'provider': provider_code,
        'provider_name': integration.provider.name,
        'name': integration.name,
        'integration_type': integration.integration_type,
        'enabled': integration.enabled,
        'flow_direction': INTEGRATION_DIRECTIONS.get(adapter_key, 'unknown'),
        'code_capability': 'available' if adapter_key in SUPPORTED_ADAPTERS else 'unsupported',
        'account_configuration': {
            'status': readiness,
            'blocking_checks': [
                check['label']
                for check in readiness_checks
                if check.get('required') and check.get('status') == 'fail'
            ],
        },
        'latest_diagnostic': {
            'status': latest_check.status,
            'evidence_level': latest_check.evidence_level,
            'summary': latest_check.summary,
            'checked_at': latest_check.checked_at,
        } if latest_check else None,
        'test_event_received': test_received,
        'live_event_received': live_received,
        'provider_processed': provider_processed,
        'outbound_evidence': {
            'test_received': test_received,
            'live_received': live_received,
            'provider_processed': provider_processed,
            'event_counts': dict(status_counts),
            'delivery_modes': dict(mode_counts),
            'match_statuses': dict(match_counts),
        },
        'inbound_evidence': {
            'received': inbound_received,
            'processed': inbound_processed,
            'event_counts': dict(inbound_status_counts),
        },
        'optimization_eligibility': 'unverified',
        'event_counts': dict(status_counts),
        'delivery_modes': dict(mode_counts),
        'match_statuses': dict(match_counts),
    }


def build_marketing_overview(*, site) -> dict:
    leads = LeadSubmission.objects.filter(site=site)
    outboxes = LeadEventOutbox.objects.filter(
        submission__site=site,
        integration__site=site,
    )
    inbound_events = LeadInboundEvent.objects.filter(integration__site=site)
    integrations = list(
        MarketingIntegration.objects.select_related('provider')
        .filter(site=site)
        .order_by('provider__code', 'integration_type', 'id')
    )

    funnel = leads.aggregate(
        total=Count('id'),
        new=Count('id', filter=Q(stage='new')),
        contacted=Count('id', filter=Q(contacted_at__isnull=False)),
        contacted_current=Count('id', filter=Q(stage='contacted')),
        qualified=Count('id', filter=Q(qualified_at__isnull=False)),
        qualified_current=Count('id', filter=Q(stage='qualified')),
        won=Count('id', filter=Q(stage='won')),
        lost=Count('id', filter=Q(stage='lost')),
        spam=Count('id', filter=Q(stage='spam')),
    )
    funnel_value = _currency_value_summary(
        leads.filter(stage='won', buyer_value__isnull=False)
        .values('buyer_currency')
        .annotate(amount=Sum('buyer_value'))
    )
    source_rows = leads.values('source_channel').annotate(
        leads=Count('id'),
        contacted=Count('id', filter=Q(contacted_at__isnull=False)),
        qualified=Count('id', filter=Q(qualified_at__isnull=False)),
        won=Count('id', filter=Q(stage='won')),
    ).order_by('-leads', 'source_channel')
    source_value_rows = leads.filter(stage='won', buyer_value__isnull=False).values(
        'source_channel', 'buyer_currency'
    ).annotate(amount=Sum('buyer_value'))
    source_values: dict[str, list[dict]] = defaultdict(list)
    for value_row in source_value_rows:
        source_values[str(value_row['source_channel'] or 'unknown')].append(value_row)
    sources = []
    for row in source_rows:
        source = str(row['source_channel'] or 'unknown')
        value_summary = _currency_value_summary(source_values.get(source, []))
        sources.append({
            'source': source,
            'leads': row['leads'],
            'contacted': row['contacted'],
            'qualified': row['qualified'],
            'won': row['won'],
            'contact_rate_percent': _ratio(row['contacted'], row['leads']),
            'qualification_rate_percent': _ratio(row['qualified'], row['leads']),
            'win_rate_percent': _ratio(row['won'], row['leads']),
            'won_value': value_summary['label'],
            'won_value_by_currency': value_summary['items'],
        })

    attempted = outboxes.exclude(status__in=['pending', 'skipped']).count()
    failed = outboxes.filter(status='failed').count()
    partial = outboxes.filter(status='partial').count()
    due_retries = outboxes.filter(retryable_outbox_q()).count()
    event_health = {
        'total': outboxes.count(),
        'pending': outboxes.filter(status='pending').count(),
        'in_progress': outboxes.filter(status__in=['sending', 'processing']).count(),
        'validated_only': outboxes.filter(status='validated').count(),
        'provider_received': outboxes.filter(provider_received_at__isnull=False).count(),
        'provider_processed': outboxes.filter(provider_processed_at__isnull=False).count(),
        'failed': failed,
        'partial': partial,
        'skipped': outboxes.filter(status='skipped').count(),
        'due_retries': due_retries,
        'failure_rate_percent': _ratio(failed, attempted),
        'attention_rate_percent': _ratio(failed + partial, attempted),
        'match_statuses': dict(Counter(outboxes.values_list('match_status', flat=True))),
    }

    return {
        'generated_at': timezone.now(),
        'site': {'id': site.id, 'code': site.code, 'name': site.name},
        'funnel': {
            **funnel,
            'won_value': funnel_value['label'],
            'won_value_by_currency': funnel_value['items'],
        },
        'sources': sources,
        'matching_completeness': _matching_completeness(leads),
        'event_health': event_health,
        'integrations': [
            _integration_state(
                integration,
                outboxes.filter(integration=integration),
                inbound_events.filter(integration=integration),
            )
            for integration in integrations
        ],
        'inbound_health': {
            'total': inbound_events.count(),
            'pending': inbound_events.filter(status='pending').count(),
            'processing': inbound_events.filter(status='processing').count(),
            'processed': inbound_events.filter(status='processed').count(),
            'failed': inbound_events.filter(status='failed').count(),
        },
        'privacy_queue': dict(
            PrivacyRequest.objects.filter(site=site)
            .values_list('status')
            .annotate(total=Count('id'))
        ),
        'proof_boundary': {
            'platform_received_is_not_matched': True,
            'optimization_eligibility_requires_platform_verification': True,
        },
    }
