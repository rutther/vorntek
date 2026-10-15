from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from console import test_public_lead_capture as fixtures
from leads.inbound import process_claimed_inbound_receipt
from leads.models import LeadFormDefinition, LeadInboundEvent, LeadSubmission
from marketing.models import MarketingIntegration, MarketingProvider
from sitecore.models import Site


class InboundOwnershipTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.created_models = []
        existing = set(connection.introspection.table_names())
        with connection.schema_editor() as editor:
            for model in fixtures.PublicLeadCaptureVerticalTests.unmanaged_models + (LeadInboundEvent,):
                if model._meta.db_table not in existing:
                    editor.create_model(model)
                    cls.created_models.append(model)
                    existing.add(model._meta.db_table)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        with connection.schema_editor() as editor:
            for model in reversed(cls.created_models):
                editor.delete_model(model)

    def setUp(self):
        site = Site.objects.create(code='inbound_ownership', name='Synthetic site', default_locale='en')
        form = LeadFormDefinition.objects.create(site=site, code='general', name='Synthetic inquiry', capi_enabled=False)
        provider = MarketingProvider.objects.create(code='meta', name='Meta')
        integration = MarketingIntegration.objects.create(site=site, provider=provider, name='Synthetic Page',
            integration_type='leadgen', public_id='100000002', enabled=True,
            config_json={'form_code': form.code, 'leadgen_form_ids': ['10001']})
        self.receipt = LeadInboundEvent.objects.create(integration=integration, provider_code='meta',
            event_type='leadgen', external_event_id='synthetic-receipt', status='processing', attempts=1,
            received_at=timezone.now(), payload_json={'leadgen_id': 'synthetic-lead', 'form_id': '10001'})
        LeadInboundEvent.objects.filter(pk=self.receipt.pk).update(attempts=2, updated_at=timezone.now(),
            payload_json={'leadgen_id': 'synthetic-current', 'form_id': '10001', 'current_owner': True})

    def assert_new_owner_preserved(self):
        current = LeadInboundEvent.objects.get(pk=self.receipt.pk)
        self.assertEqual((current.status, current.attempts), ('processing', 2))
        self.assertTrue(current.payload_json['current_owner'])
        self.assertIsNone(current.submission_id)
        self.assertEqual(LeadSubmission.objects.count(), 0)

    def test_late_failure_cannot_rewind_replacement_claim(self):
        with patch('leads.inbound.fetch_meta_lead', side_effect=ValueError('synthetic old failure')):
            self.assertFalse(process_claimed_inbound_receipt(self.receipt))
        self.assert_new_owner_preserved()

    def test_late_success_cannot_materialize_a_lead_after_claim_replacement(self):
        data = {'id': 'synthetic-lead', 'form_id': '10001', 'field_data': [
            {'name': 'full_name', 'values': ['Synthetic old result']},
            {'name': 'email', 'values': ['person@example.test']}]}
        with patch('leads.inbound.fetch_meta_lead', return_value=data):
            self.assertFalse(process_claimed_inbound_receipt(self.receipt))
        self.assert_new_owner_preserved()
