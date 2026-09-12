from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from leads.inbound import retry_inbound_events
from leads.models import (
    ConsentRecord,
    LeadFormDefinition,
    LeadInboundEvent,
    LeadSubmission,
    SalesTeam,
)
from marketing.models import CanonicalEvent, MarketingIntegration, MarketingProvider
from sitecore.models import Cta, PageRoute, Site, SiteLocale


class MetaLeadgenWebhookTests(TestCase):
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
        MarketingProvider,
        MarketingIntegration,
        LeadFormDefinition,
        SalesTeam,
        LeadSubmission,
        ConsentRecord,
        LeadInboundEvent,
    )

    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        try:
            with connection.schema_editor() as editor:
                for model in cls.unmanaged_models:
                    if model._meta.db_table not in existing:
                        editor.create_model(model)
                        cls.created_models.append(model)
                        existing.add(model._meta.db_table)
            super().setUpClass()
        except Exception:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)

    def setUp(self):
        self.site = Site.objects.create(
            code='meta-webhook-test',
            name='Meta webhook test',
            base_url='https://example.test',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.provider = MarketingProvider.objects.create(
            code='meta', name='Meta', enabled=True, capabilities={},
        )
        self.locale = SiteLocale.objects.create(
            site=self.site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )
        self.form = LeadFormDefinition.objects.create(
            site=self.site,
            locale=self.locale,
            code='project-inquiry',
            name='Project inquiry',
            status='active',
            capi_enabled=False,
            form_schema={},
            config_json={},
        )
        self.integration = self._integration(self.site, 'page-123', ['form-456'])

    def _integration(self, site, public_id, form_ids):
        return MarketingIntegration.objects.create(
            site=site,
            provider=self.provider,
            name=f'Meta Leadgen {public_id}',
            integration_type='leadgen',
            public_id=public_id,
            secret_ref='vault:test-primary',
            enabled=True,
            config_json={
                'app_secret_ref': 'vault:test-app-secret',
                'webhook_verify_token_ref': 'vault:test-verify-token',
                'leadgen_form_ids': form_ids,
                'form_code': 'project-inquiry',
                'contact_consent_confirmed': True,
                'marketing_consent_confirmed': False,
                'queue_initial_crm_event': False,
            },
        )

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True)
    def test_meta_only_worker_leaves_other_provider_pending_and_stale_claims_untouched(self):
        now = timezone.now()
        for state in ('pending', 'processing'):
            receipt = LeadInboundEvent.objects.create(
                integration=self.integration, provider_code='whatsapp', event_type='synthetic',
                external_event_id=f'synthetic-paused-{state}', payload_json={}, status=state,
                received_at=now - timedelta(hours=2),
            )
            LeadInboundEvent.objects.filter(pk=receipt.pk).update(updated_at=now-timedelta(hours=2))
        before = list(LeadInboundEvent.objects.order_by('id').values())
        with patch('leads.inbound.process_inbound_receipt') as process:
            self.assertEqual(retry_inbound_events(provider='meta'), (0, 0))
            process.assert_not_called()
        self.assertEqual(list(LeadInboundEvent.objects.order_by('id').values()), before)

    @staticmethod
    def _body(*, page_id='page-123', form_id='form-456', leadgen_id='lead-789'):
        return json.dumps(
            {
                'object': 'page',
                'entry': [{
                    'id': page_id,
                    'changes': [{
                        'field': 'leadgen',
                        'value': {
                            'leadgen_id': leadgen_id,
                            'form_id': form_id,
                        },
                    }],
                }],
            },
            separators=(',', ':'),
        ).encode('utf-8')

    def _post(self, body):
        signature = 'sha256=' + hmac.new(b'app-secret', body, hashlib.sha256).hexdigest()
        with patch('leads.webhooks.integration_secret', return_value='app-secret'):
            return self.client.post(
                reverse('meta_leadgen_webhook'),
                data=body,
                content_type='application/json',
                HTTP_X_HUB_SIGNATURE_256=signature,
            )

    @patch('leads.inbound.fetch_meta_lead')
    def test_verified_event_is_durably_queued_without_graph_call(self, fetch_meta_lead):
        response = self._post(self._body())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {'ok': True, 'received': 1, 'queued': 1, 'processed': 0},
        )
        receipt = LeadInboundEvent.objects.get()
        self.assertEqual(receipt.status, 'pending')
        self.assertEqual(receipt.attempts, 0)
        self.assertEqual(receipt.payload_json['value']['form_id'], 'form-456')
        fetch_meta_lead.assert_not_called()

    def test_duplicate_delivery_reuses_the_same_receipt(self):
        body = self._body()

        self.assertEqual(self._post(body).json()['queued'], 1)
        duplicate = self._post(body)

        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.json()['queued'], 0)
        self.assertEqual(LeadInboundEvent.objects.count(), 1)

    def test_worker_processes_pending_receipt_after_webhook_response(self):
        response = self._post(self._body())
        self.assertEqual(response.status_code, 200)
        provider_payload = {
            'id': 'lead-789',
            'form_id': 'form-456',
            'field_data': [
                {'name': 'full_name', 'values': ['Synthetic Meta Lead']},
                {'name': 'email', 'values': ['meta-lead@example.test']},
            ],
        }

        with patch('leads.inbound.fetch_meta_lead', return_value=provider_payload) as fetch:
            attempted, succeeded = retry_inbound_events(limit=10)

        self.assertEqual((attempted, succeeded), (1, 1))
        receipt = LeadInboundEvent.objects.get()
        self.assertEqual(receipt.status, 'processed')
        self.assertEqual(receipt.attempts, 1)
        self.assertEqual(receipt.submission.source_channel, 'meta_ads')
        self.assertEqual(receipt.submission.full_name, 'Synthetic Meta Lead')
        fetch.assert_called_once_with(self.integration, 'lead-789')

    def test_unlisted_or_missing_form_id_is_rejected_before_receipt(self):
        for body in (
            self._body(form_id='form-not-allowed'),
            self._body(form_id=''),
        ):
            with self.subTest(body=body):
                response = self._post(body)
                self.assertEqual(response.status_code, 403)
        self.assertFalse(LeadInboundEvent.objects.exists())

    def test_cross_integration_event_id_collision_fails_closed(self):
        other_site = Site.objects.create(
            code='meta-webhook-other',
            name='Other site',
            base_url='https://other.example.test',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        other = self._integration(other_site, 'page-999', ['form-999'])
        LeadInboundEvent.objects.create(
            integration=other,
            provider_code='meta',
            event_type='leadgen',
            external_event_id='lead-789',
            payload_json={},
            status='pending',
            received_at=timezone.now(),
        )

        response = self._post(self._body())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(LeadInboundEvent.objects.count(), 1)
