"""Real durable rows; only the provider transport is synthetic."""
from io import BytesIO
from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from console.test_public_lead_capture import PublicLeadCaptureVerticalTests as Fixture
from leads.models import LeadEventAttempt, LeadEventOutbox, LeadFormDefinition, LeadSubmission
from leads.services import dispatch_outbox_event
from marketing.models import MarketingIntegration, MarketingProvider
from sitecore.models import Site


class DurableMarketingDeliveryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.created_models = []
        existing = set(connection.introspection.table_names())
        with connection.schema_editor() as editor:
            for model in Fixture.unmanaged_models:
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
        site = Site.objects.create(code='durable_delivery', name='Synthetic delivery', default_locale='en')
        form = LeadFormDefinition.objects.create(site=site, code='general', name='Synthetic inquiry', capi_enabled=True)
        provider = MarketingProvider.objects.create(code='meta', name='Meta')
        integration = MarketingIntegration.objects.create(site=site, provider=provider, name='Synthetic only',
            integration_type='pixel', public_id='100000001', enabled=True, routing_mode='legacy_site')
        lead = LeadSubmission.objects.create(site=site, form=form, full_name='Synthetic inquiry',
            submitted_at=timezone.now(), stage_updated_at=timezone.now(), consent_json={'marketing': True})
        self.event = LeadEventOutbox.objects.create(submission=lead, integration=integration,
            event_id='synthetic-attempt-1', event_name='Lead', stage_key='submitted',
            payload_json={'event_id': 'synthetic-attempt-1', 'event_name': 'Lead'})

    def send(self):
        # Exercise the autocommit core; real commit/rollback is separately tested on PG.
        from leads.marketing_delivery import dispatch_meta_now
        return dispatch_meta_now(self.event)

    def test_timeout_is_unknown_and_cannot_open_a_second_handoff(self):
        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
             patch('leads.services.urllib.request.urlopen', side_effect=TimeoutError('synthetic transport')) as transport:
            self.assertFalse(self.send())
            self.event.refresh_from_db()
            self.assertEqual(self.event.status, 'unknown')
            self.assertIsNone(self.event.next_attempt_at)
            self.assertFalse(self.send())
        self.assertEqual(transport.call_count, 1)


        self.assertEqual(list(LeadEventAttempt.objects.values_list('state', flat=True)), ['unknown'])

    def test_accepted_receipt_repairs_summary_without_posting_again(self):
        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
             patch('leads.services.urllib.request.urlopen', return_value=BytesIO(b'{"events_received":1,"fbtrace_id":"synthetic-ack"}')) as transport:
            with patch('leads.marketing_delivery.repair_event_projection', side_effect=DatabaseError('synthetic projection failure')):
                self.assertTrue(self.send())
            self.assertEqual(LeadEventAttempt.objects.get().state, 'accepted')
            self.assertFalse(self.send())
        self.event.refresh_from_db()
        self.assertEqual((self.event.status, self.event.provider_request_id), ('sent', 'synthetic-ack'))
        self.assertEqual(transport.call_count, 1)

    def test_recycled_lead_is_not_offered_a_manual_or_automatic_retry(self):
        from console.marketing_queries import outbox_retry_contract, retryable_outbox_q
        self.event.status = 'failed'
        self.event.save(update_fields=['status'])
        LeadSubmission.objects.filter(pk=self.event.submission_id).update(trashed_at=timezone.now())
        self.event.submission.refresh_from_db()
        allowed, reason = outbox_retry_contract(self.event)
        self.assertFalse(allowed)
        self.assertTrue(reason)
        self.assertFalse(LeadEventOutbox.objects.filter(retryable_outbox_q(), pk=self.event.pk).exists())


@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False)
class SilentDeliveryBoundaryTests(SimpleTestCase):
    def test_all_delivery_entries_stop_before_reading_rows_or_credentials(self):
        from leads.google_data_manager import dispatch_google_now, dispatch_google_outbox_event, refresh_google_outbox_status
        from leads.marketing_delivery import dispatch_meta, dispatch_meta_now
        from leads.notifications import send_submission_notification, _send_submission_notification_now
        from leads.services import dispatch_meta_outbox_event, dispatch_claimed_outbox_event, dispatch_pending_outbox
        for entry in (dispatch_outbox_event, dispatch_meta_outbox_event, dispatch_claimed_outbox_event,
                      dispatch_google_now, dispatch_google_outbox_event, refresh_google_outbox_status,
                      dispatch_meta, dispatch_meta_now, send_submission_notification, _send_submission_notification_now):
            with self.subTest(entry=entry.__name__):
                self.assertFalse(entry(None))
        self.assertEqual(dispatch_pending_outbox(), (0, 0))
