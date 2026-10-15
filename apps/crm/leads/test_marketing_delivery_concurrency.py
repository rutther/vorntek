"""Real committed marketing delivery races; every provider request is synthetic."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
import json
import threading
from unittest import skipUnless
from unittest.mock import patch
import urllib.error
import uuid

from django.db import close_old_connections, connection, DatabaseError, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from console import test_public_lead_capture as capture_fixtures
from leads.models import LeadEventAttempt, LeadEventOutbox, LeadFormDefinition, LeadSubmission
from leads.services import dispatch_outbox_event, dispatch_pending_outbox
from marketing.models import MarketingIntegration, MarketingProvider
from sitecore.models import Site


@skipUnless(connection.vendor == 'postgresql', 'Requires committed PostgreSQL marketing writers')
@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True)
class MarketingDeliveryConcurrencyTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        with connection.schema_editor() as editor:
            for model in capture_fixtures.PublicLeadCaptureVerticalTests.unmanaged_models:
                if model._meta.db_table not in existing:
                    editor.create_model(model)
                    cls.created_models.append(model)
                    existing.add(model._meta.db_table)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)

    def _fixture_teardown(self):
        # Full-SQL disposable database belongs to the driver. Managed auth
        # flush cannot truncate authoritative SQL foreign-key references.
        return None

    def setUp(self):
        self.site, _ = Site.objects.get_or_create(code='marketing_delivery_races', defaults={
            'name': 'Synthetic marketing races', 'default_locale': 'en', 'enabled': True})
        self.form, _ = LeadFormDefinition.objects.get_or_create(site=self.site, code='delivery-races', defaults={
            'name': 'Synthetic only', 'capi_enabled': True, 'notify_emails': ''})
        provider, _ = MarketingProvider.objects.get_or_create(code='meta', defaults={'name': 'Meta'})
        self.integration, _ = MarketingIntegration.objects.get_or_create(site=self.site, provider=provider,
            integration_type='pixel', defaults={'name': 'Synthetic Pixel', 'enabled': True,
                'public_id': '1234567890', 'routing_mode': 'legacy_site', 'environment': 'production'})

    def event(self):
        now = timezone.now()
        lead = LeadSubmission.objects.create(site=self.site, form=self.form, full_name='Synthetic race',
            submitted_at=now, stage_updated_at=now, consent_json={'marketing': True})
        event_id = str(uuid.uuid4())
        return LeadEventOutbox.objects.create(submission=lead, integration=self.integration,
            stage_key='submitted', event_name='Lead', event_id=event_id, action_source='website',
            payload_json={'event_name': 'Lead', 'event_id': event_id}, status='pending')

    def worker(self, pk):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            return dispatch_outbox_event(LeadEventOutbox.objects.get(pk=pk)), pid
        finally:
            close_old_connections()

    def test_disabled_integration_blocks_stale_failed_worker_snapshot(self):
        # Removing fresh sendability checks must allow an observable transport.
        event=self.event()
        LeadEventOutbox.objects.filter(pk=event.pk).update(status='failed')
        event.refresh_from_db()
        event.integration=self.integration  # Snapshot still says enabled.
        original_id=event.event_id
        MarketingIntegration.objects.filter(pk=self.integration.pk).update(enabled=False)
        calls=[]
        def transport(request,**kwargs):
            calls.append(json.loads(request.data)['data'][0]['event_id'])
            return BytesIO(b'{"events_received":1}')
        try:
            with patch('leads.services.resolve_meta_access_token',return_value='synthetic-only'), \
                    patch('leads.services.urllib.request.urlopen',side_effect=transport):
                self.assertFalse(dispatch_outbox_event(event))
            event.refresh_from_db()
            self.assertEqual((event.status,event.attempts,event.event_id),('skipped',0,original_id))
            self.assertEqual(calls,[])
            self.assertFalse(LeadEventAttempt.objects.filter(outbox=event).exists())
        finally:
            MarketingIntegration.objects.filter(pk=self.integration.pk).update(enabled=True)

    def test_disabled_integration_blocks_scheduler_failed_queue(self):
        event=self.event()
        LeadEventOutbox.objects.filter(pk=event.pk).update(status='failed')
        MarketingIntegration.objects.filter(pk=self.integration.pk).update(enabled=False)
        calls=[]
        def transport(request,**kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1}')
        try:
            with patch('leads.services.resolve_meta_access_token',return_value='synthetic-only'), \
                    patch('leads.services.urllib.request.urlopen',side_effect=transport):
                self.assertEqual(dispatch_pending_outbox(submission=event.submission,failed_before=None),(1,0))
            event.refresh_from_db()
            self.assertEqual((event.status,event.attempts),('skipped',0))
            self.assertEqual(calls,[])
            self.assertFalse(LeadEventAttempt.objects.filter(outbox=event).exists())
        finally:
            MarketingIntegration.objects.filter(pk=self.integration.pk).update(enabled=True)

    def test_disable_between_manual_eligibility_and_claimed_entry_does_not_send(self):
        from console.marketing_queries import outbox_retry_contract
        from leads.services import dispatch_claimed_outbox_event
        event=self.event()
        LeadEventOutbox.objects.filter(pk=event.pk).update(status='failed')
        event.refresh_from_db()
        self.assertEqual(outbox_retry_contract(event),(True,''))
        stamp=timezone.now()
        LeadEventOutbox.objects.filter(pk=event.pk).update(status='sending',updated_at=stamp)
        event.refresh_from_db()
        MarketingIntegration.objects.filter(pk=self.integration.pk).update(enabled=False)
        calls=[]
        def transport(request,**kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1}')
        try:
            with patch('leads.services.resolve_meta_access_token',return_value='synthetic-only'), \
                    patch('leads.services.urllib.request.urlopen',side_effect=transport):
                self.assertFalse(dispatch_claimed_outbox_event(event))
            event.refresh_from_db()
            self.assertEqual((event.status,event.attempts),('skipped',0))
            self.assertEqual(calls,[])
            self.assertFalse(LeadEventAttempt.objects.filter(outbox=event).exists())
        finally:
            MarketingIntegration.objects.filter(pk=self.integration.pk).update(enabled=True)

    def test_enabled_failed_scheduler_event_still_has_one_handoff(self):
        # Positive control: blocking all delivery cannot pass this contract.
        event=self.event()
        LeadEventOutbox.objects.filter(pk=event.pk).update(status='failed')
        calls=[]
        def transport(request,**kwargs):
            calls.append(json.loads(request.data)['data'][0]['event_id'])
            return BytesIO(b'{"events_received":1}')
        with patch('leads.services.resolve_meta_access_token',return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen',side_effect=transport):
            self.assertEqual(dispatch_pending_outbox(submission=event.submission,failed_before=None),(1,1))
        event.refresh_from_db()
        self.assertEqual((event.status,event.attempts),('sent',1))
        self.assertEqual(calls,[event.event_id])
        self.assertEqual(LeadEventAttempt.objects.get(outbox=event).state,'accepted')

    def test_ambiguous_response_is_not_automatically_sent_again(self):
        event = self.event()
        calls = []

        def interrupted(request, **kwargs):
            calls.append(json.loads(request.data)['data'][0]['event_id'])
            raise urllib.error.URLError('synthetic response lost after possible acceptance')

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=interrupted):
            self.assertFalse(dispatch_outbox_event(event))
            LeadEventOutbox.objects.filter(pk=event.pk).update(next_attempt_at=None)
            dispatch_pending_outbox(submission=event.submission, failed_before=None)
        self.assertEqual(calls, [event.event_id])
        event.refresh_from_db()
        self.assertEqual(event.status, 'unknown')

    def test_expired_handoff_cannot_be_reclaimed_or_overwritten_by_old_failure(self):
        event = self.event()
        arrived = threading.Event()
        release = threading.Event()
        calls = []

        def transport(request, **kwargs):
            calls.append(json.loads(request.data)['data'][0]['event_id'])
            if len(calls) == 1:
                arrived.set()
                if not release.wait(10):
                    raise AssertionError('Synthetic handoff was not released')
                raise urllib.error.URLError('synthetic late response lost')
            return BytesIO(b'{"events_received":1,"fbtrace_id":"synthetic-new-ack"}')

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, event.pk)
            try:
                self.assertTrue(arrived.wait(10))
                LeadEventOutbox.objects.filter(pk=event.pk).update(updated_at=timezone.now() - timedelta(minutes=60))
                dispatch_pending_outbox(submission=event.submission, failed_before=None)
            finally:
                release.set()
            first.result(timeout=10)
        self.assertEqual(calls, [event.event_id])
        event.refresh_from_db()
        self.assertEqual(event.status, 'unknown')

    def test_revocation_during_credential_preparation_prevents_platform_handoff(self):
        event = self.event()
        calls = []

        def revoke(_integration):
            LeadSubmission.objects.filter(pk=event.submission_id).update(consent_json={'marketing': False})
            return 'synthetic-only'

        def transport(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1}')

        with patch('leads.services.resolve_meta_access_token', side_effect=revoke), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport):
            result = dispatch_outbox_event(event)
        self.assertEqual(calls, [])
        self.assertFalse(result)
        event.refresh_from_db()
        self.assertEqual(event.status, 'skipped')

    def test_preparation_rechecks_lifecycle_configuration_site_and_form(self):
        original_config = self.integration.config_json
        cases = [
            ('trash', LeadSubmission, {'trashed_at': timezone.now()}, 'pending'),
            ('privacy', LeadSubmission, {'config_json': {'privacy_deleted': True}}, 'skipped'),
            ('archive', LeadSubmission, {'config_json': {'archived': True}}, 'skipped'),
            ('spam', LeadSubmission, {'stage': 'spam'}, 'skipped'),
            ('site_off', Site, {'enabled': False}, 'skipped'),
            ('form_off', LeadFormDefinition, {'capi_enabled': False}, 'skipped'),
            ('integration_off', MarketingIntegration, {'enabled': False}, 'skipped'),
            ('configuration_changed', MarketingIntegration,
             {'config_json': {'synthetic-new-config': True}, 'updated_at': timezone.now()}, 'pending'),
        ]
        try:
            for label, model, changes, want in cases:
                with self.subTest(case=label):
                    Site.objects.filter(pk=self.site.pk).update(enabled=True)
                    LeadFormDefinition.objects.filter(pk=self.form.pk).update(capi_enabled=True)
                    MarketingIntegration.objects.filter(pk=self.integration.pk).update(
                        enabled=True, config_json=original_config, updated_at=timezone.now())
                    event = self.event()
                    original_id = event.event_id
                    target = {LeadSubmission: event.submission_id, Site: self.site.pk,
                              LeadFormDefinition: self.form.pk, MarketingIntegration: self.integration.pk}[model]

                    def withdraw(_integration):
                        model.objects.filter(pk=target).update(**changes)
                        return 'synthetic-only'

                    with patch('leads.services.resolve_meta_access_token', side_effect=withdraw), \
                            patch('leads.services.urllib.request.urlopen') as platform:
                        self.assertFalse(dispatch_outbox_event(event))
                    platform.assert_not_called()
                    event.refresh_from_db()
                    attempt = LeadEventAttempt.objects.get(outbox=event)
                    self.assertEqual((event.status, event.attempts, event.event_id), (want, 0, original_id))
                    self.assertEqual((attempt.state, attempt.handoff_at), ('cancelled', None))
        finally:
            Site.objects.filter(pk=self.site.pk).update(enabled=True)
            LeadFormDefinition.objects.filter(pk=self.form.pk).update(capi_enabled=True)
            MarketingIntegration.objects.filter(pk=self.integration.pk).update(
                enabled=True, config_json=original_config, updated_at=timezone.now())

    def test_trash_after_committed_handoff_keeps_positive_receipt_without_resend(self):
        event = self.event()
        calls = []

        def platform(request, **kwargs):
            self.assertEqual(LeadEventAttempt.objects.get(outbox=event).state, 'handoff')
            self.assertFalse(connection.in_atomic_block)
            LeadSubmission.objects.filter(pk=event.submission_id).update(trashed_at=timezone.now())
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1,"fbtrace_id":"synthetic-after-handoff"}')

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=platform):
            self.assertTrue(dispatch_outbox_event(event))
            self.assertFalse(dispatch_outbox_event(event))
        self.assertEqual(len(calls), 1)
        self.assertEqual(LeadEventAttempt.objects.get(outbox=event).state, 'accepted')
        event.refresh_from_db()
        self.assertEqual((event.status, event.provider_request_id), ('sent', 'synthetic-after-handoff'))

    def test_event_contract_change_during_preparation_cancels_old_payload(self):
        event = self.event()
        calls = []

        def change_contract(_integration):
            LeadEventOutbox.objects.filter(pk=event.pk).update(stage_key='qualified')
            return 'synthetic-only'

        def transport(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1}')

        with patch('leads.services.resolve_meta_access_token', side_effect=change_contract), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport):
            result = dispatch_outbox_event(event)
        self.assertEqual(calls, [])
        self.assertFalse(result)
        self.assertEqual(LeadEventAttempt.objects.get(outbox=event).state, 'cancelled')

    def test_normal_overlap_keeps_one_handoff_and_other_event_can_finish(self):
        event = self.event()
        other = self.event()
        arrived = threading.Event()
        release = threading.Event()
        calls = []

        def transport(request, **kwargs):
            event_id = json.loads(request.data)['data'][0]['event_id']
            calls.append(event_id)
            if event_id == event.event_id:
                arrived.set()
                if not release.wait(10):
                    raise AssertionError('Synthetic normal handoff was not released')
            return BytesIO(b'{"events_received":1}')

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport), ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(self.worker, event.pk)
            try:
                self.assertTrue(arrived.wait(10))
                overlap = pool.submit(self.worker, event.pk).result(timeout=5)
                independent = pool.submit(self.worker, other.pk).result(timeout=5)
                self.assertFalse(overlap[0])
                self.assertTrue(independent[0])
            finally:
                release.set()
            original = first.result(timeout=10)
        self.assertTrue(original[0])
        self.assertEqual(len({original[1], overlap[1], independent[1]}), 3)
        self.assertEqual(sorted(calls), sorted([event.event_id, other.event_id]))

    def test_abandoned_pre_handoff_claim_resumes_without_accepting_old_owner(self):
        from leads.marketing_delivery import begin_meta_handoff, claim_meta_event
        event = self.event()
        _current, old = claim_meta_event(event.pk)
        calls = []

        def transport(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1}')

        future = timezone.now() + timedelta(minutes=10)
        with patch('leads.marketing_delivery.timezone.now', return_value=future), \
                patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport):
            dispatch_pending_outbox(submission=event.submission, failed_before=None)
            self.assertFalse(begin_meta_handoff(old.pk))
        self.assertEqual(len(calls), 1)
        old.refresh_from_db()
        self.assertEqual(old.state, 'cancelled')
        self.assertEqual(LeadEventAttempt.objects.get(outbox=event, state='accepted').generation, 2)

    def test_accepted_fact_survives_projection_failure_and_prevents_resend(self):
        event = self.event()
        original_save = LeadEventOutbox.save
        calls = []

        def fail_projection(instance, *args, **kwargs):
            if instance.pk == event.pk and 'dispatched_at' in kwargs.get('update_fields', ()):
                raise DatabaseError('synthetic summary unavailable')
            return original_save(instance, *args, **kwargs)

        def transport(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1,"fbtrace_id":"synthetic-ack"}')

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport):
            with patch.object(LeadEventOutbox, 'save', new=fail_projection):
                self.assertTrue(dispatch_outbox_event(event))
            self.assertFalse(dispatch_outbox_event(event))
        event.refresh_from_db()
        self.assertEqual((event.status, event.provider_request_id), ('sent', 'synthetic-ack'))
        self.assertEqual(len(calls), 1)

    def test_lost_receipt_persistence_keeps_handoff_blocking_retry(self):
        event = self.event()
        original_save = LeadEventAttempt.save
        calls = []

        def lose_receipt(instance, *args, **kwargs):
            if instance.state == 'accepted':
                raise DatabaseError('synthetic receipt unavailable')
            return original_save(instance, *args, **kwargs)

        def transport(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1}')

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport):
            with patch.object(LeadEventAttempt, 'save', new=lose_receipt):
                self.assertFalse(dispatch_outbox_event(event))
            self.assertFalse(dispatch_outbox_event(event))
        self.assertEqual(LeadEventAttempt.objects.get(outbox=event).state, 'handoff')
        self.assertEqual(len(calls), 1)

    def test_scheduler_repairs_accepted_summary_without_another_post(self):
        event = self.event()
        original_save = LeadEventOutbox.save
        calls = []

        def fail_projection(instance, *args, **kwargs):
            if instance.pk == event.pk and 'dispatched_at' in kwargs.get('update_fields', ()):
                raise DatabaseError('synthetic summary temporarily unavailable')
            return original_save(instance, *args, **kwargs)

        def transport(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1,"fbtrace_id":"synthetic-scheduler-ack"}')

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport):
            with patch.object(LeadEventOutbox, 'save', new=fail_projection):
                self.assertTrue(dispatch_outbox_event(event))
            dispatch_pending_outbox(submission=event.submission)
        event.refresh_from_db()
        self.assertEqual((event.status, event.provider_request_id), ('sent', 'synthetic-scheduler-ack'))
        self.assertEqual(len(calls), 1)

    def test_queued_manual_claim_cannot_adopt_a_later_owner_timestamp(self):
        from leads.services import dispatch_claimed_outbox_event
        event = self.event()
        first_claim = timezone.now() - timedelta(seconds=1)
        LeadEventOutbox.objects.filter(pk=event.pk).update(status='sending', updated_at=first_claim)
        event.refresh_from_db()
        calls = []

        def transport(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1}')

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport):
            with transaction.atomic():
                self.assertFalse(dispatch_claimed_outbox_event(event))
                LeadEventOutbox.objects.filter(pk=event.pk).update(updated_at=timezone.now())
        self.assertEqual(calls, [])
        self.assertEqual(LeadEventAttempt.objects.filter(outbox=event).count(), 0)

    def test_outer_rollback_has_no_handoff_and_commit_runs_once(self):
        event = self.event()
        calls = []

        def transport(request, **kwargs):
            calls.append(request.full_url)
            return BytesIO(b'{"events_received":1}')

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport):
            with self.assertRaisesRegex(RuntimeError, 'synthetic rollback'), transaction.atomic():
                self.assertFalse(dispatch_outbox_event(event))
                self.assertEqual(calls, [])
                self.assertFalse(LeadEventAttempt.objects.filter(outbox=event).exists())
                raise RuntimeError('synthetic rollback')
            self.assertEqual(calls, [])
            with transaction.atomic():
                self.assertFalse(dispatch_outbox_event(event))
                self.assertEqual(calls, [])
            self.assertEqual(len(calls), 1)
        event.refresh_from_db()
        self.assertEqual(event.status, 'sent')

    def test_sql_identity_terminal_state_and_receipt_constraints(self):
        event = self.event()
        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', return_value=BytesIO(b'{"events_received":1}')):
            self.assertTrue(dispatch_outbox_event(event))
        attempt = LeadEventAttempt.objects.get(outbox=event)
        for values in ({'generation': 99}, {'event_id': 'different-event'}, {'state': 'failed'},
                       {'response_json': {'events_received': 0}}):
            with self.assertRaises(DatabaseError), transaction.atomic():
                LeadEventAttempt.objects.filter(pk=attempt.pk).update(**values)
        with self.assertRaises(DatabaseError), transaction.atomic():
            LeadEventAttempt.objects.create(outbox=event, generation=2, attempt_number=2,
                lead_version=attempt.lead_version, integration_version=attempt.integration_version,
                event_id=event.event_id, public_id=attempt.public_id, payload_hash=attempt.payload_hash,
                delivery_mode='live', state='accepted')
