from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from . import payloads
from .marketing_queries import _currency_value_summary, _integration_state


class FakeEvents:
    def __init__(self, rows):
        self.rows = list(rows)

    def values_list(self, field, flat=False):
        assert flat
        return [row.get(field) for row in self.rows]

    def filter(self, **conditions):
        rows = self.rows
        for key, expected in conditions.items():
            if key.endswith('__isnull'):
                field = key.removesuffix('__isnull')
                rows = [row for row in rows if (row.get(field) is None) is expected]
            else:
                rows = [row for row in rows if row.get(key) == expected]
        return FakeEvents(rows)

    def exists(self):
        return bool(self.rows)


class StaticQuery:
    """Small immutable queryset stand-in for payload contract tests."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def select_related(self, *_fields):
        return self

    def filter(self, **_conditions):
        return self

    def order_by(self, *_fields):
        return self

    def __getitem__(self, key):
        return StaticQuery(self.rows[key]) if isinstance(key, slice) else self.rows[key]

    def __iter__(self):
        return iter(self.rows)


def integration(provider, integration_type, config=None):
    checks = Mock()
    checks.order_by.return_value.first.return_value = None
    return SimpleNamespace(
        id=1,
        provider=SimpleNamespace(code=provider, name=provider.title()),
        integration_type=integration_type,
        name=f'{provider} {integration_type}',
        enabled=True,
        public_id='123456789',
        secret_ref='vault:configured',
        config_json=dict(config or {}),
        checks=checks,
    )


class IntegrationStateTests(SimpleTestCase):
    def test_currency_value_summary_never_adds_different_currencies(self):
        summary = _currency_value_summary([
            {'buyer_currency': 'usd', 'amount': '120.50'},
            {'buyer_currency': 'USD', 'amount': '79.50'},
            {'buyer_currency': 'EUR', 'amount': '300'},
        ])

        self.assertEqual(
            summary['items'],
            [
                {'currency': 'EUR', 'amount': '300'},
                {'currency': 'USD', 'amount': '200.00'},
            ],
        )
        self.assertEqual(summary['label'], 'EUR 300 / USD 200.00')

    def test_inbound_adapter_uses_webhook_receipts_as_its_operational_evidence(self):
        item = integration(
            'meta',
            'leadgen',
            {
                'form_code': 'project-inquiry',
                'app_secret_ref': 'vault:app-secret',
                'webhook_verify_token_ref': 'vault:verify-token',
                'queue_initial_crm_event': True,
                'initial_crm_event_name': 'initial_lead',
            },
        )
        inbound = FakeEvents([
            {'status': 'processed', 'processed_at': object()},
            {'status': 'failed', 'processed_at': None},
        ])

        state = _integration_state(item, FakeEvents([]), inbound)

        self.assertEqual(state['flow_direction'], 'inbound')
        self.assertTrue(state['inbound_evidence']['received'])
        self.assertTrue(state['inbound_evidence']['processed'])
        self.assertEqual(state['inbound_evidence']['event_counts'], {'processed': 1, 'failed': 1})
        self.assertFalse(state['live_event_received'])

    def test_outbound_adapter_separates_test_and_live_provider_evidence(self):
        item = integration('meta', 'pixel')
        outboxes = FakeEvents([
            {
                'status': 'validated',
                'delivery_mode': 'test',
                'provider_received_at': object(),
                'provider_processed_at': object(),
                'match_status': 'unknown',
            },
            {
                'status': 'sent',
                'delivery_mode': 'live',
                'provider_received_at': object(),
                'provider_processed_at': object(),
                'match_status': 'not_available',
            },
            {
                'status': 'partial',
                'delivery_mode': 'live',
                'provider_received_at': object(),
                'provider_processed_at': object(),
                'match_status': 'not_available',
            },
        ])

        state = _integration_state(item, outboxes, FakeEvents([]))

        self.assertEqual(state['flow_direction'], 'outbound')
        self.assertTrue(state['outbound_evidence']['test_received'])
        self.assertTrue(state['outbound_evidence']['live_received'])
        self.assertTrue(state['outbound_evidence']['provider_processed'])
        self.assertEqual(state['outbound_evidence']['event_counts']['partial'], 1)
        self.assertFalse(state['inbound_evidence']['received'])


class MarketingPayloadScopeTests(SimpleTestCase):
    def test_site_scoped_payload_does_not_advertise_a_fake_locale(self):
        site = SimpleNamespace(id=1, code='siteos_demo', name='New Crown')
        locale = SimpleNamespace(locale_code='en', label='English')
        provider = SimpleNamespace(code='meta', name='Meta')
        integration_item = SimpleNamespace(
            id=17,
            provider=provider,
            integration_type='pixel',
            name='Meta CAPI',
            public_id='pixel-123',
            enabled=True,
            consent_category='marketing',
            updated_at=None,
        )
        overview = {
            'sources': [],
            'matching_completeness': {
                'eligible_leads': 0,
                'contactable': {'count': 0, 'percent': 0.0},
                'browser_identifier': {'count': 0, 'percent': 0.0},
                'ad_click_identifier': {'count': 0, 'percent': 0.0},
                'external_lead_identifier': {'count': 0, 'percent': 0.0},
            },
            'funnel': {
                'total': 0,
                'contacted': 0,
                'qualified': 0,
                'won': 0,
                'won_value': '0',
            },
            'event_health': {
                'total': 0,
                'failed': 0,
                'failure_rate_percent': 0.0,
                'partial': 0,
                'provider_received': 0,
                'validated_only': 0,
            },
            'inbound_health': {
                'total': 0,
                'processed': 0,
                'processing': 0,
                'failed': 0,
            },
            'privacy_queue': {},
        }
        manager_rows = {
            payloads.MarketingIntegration: [integration_item],
            payloads.Cta: [],
            payloads.TrackingRule: [],
            payloads.ProviderEventMapping: [],
        }

        manager_patches = [
            patch.object(model, 'objects', StaticQuery(rows))
            for model, rows in manager_rows.items()
        ]
        for manager_patch in manager_patches:
            manager_patch.start()
        try:
            with (
                patch('console.payloads.default_site_locale', return_value=(site, locale)),
                patch('console.payloads.build_marketing_overview', return_value=overview),
                patch('console.payloads.inspect_marketing_integration', return_value=[]),
                patch('console.payloads.checks_overall', return_value='pass'),
            ):
                page = payloads.marketing_payload()
        finally:
            for manager_patch in reversed(manager_patches):
                manager_patch.stop()

        self.assertEqual(
            page['summary'],
            [{'label': '当前站点', 'value': 'New Crown', 'meta': 'siteos_demo'}],
        )
        edit_action = page['views']['integrations']['table']['rows'][0]['actions'][0]
        self.assertEqual(
            edit_action['href'],
            reverse('console:marketing_integration_edit', args=[integration_item.id]),
        )
        self.assertNotIn('locale=', edit_action['href'])

        self.assertNotIn('outbound', page['views'])
        self.assertNotIn('inbound', page['views'])
        self.assertNotIn('privacy', page['views'])
        self.assertEqual(
            [tab['key'] for tab in page['tabs']],
            ['overview', 'matching', 'integrations', 'ctas', 'rules', 'mappings'],
        )
