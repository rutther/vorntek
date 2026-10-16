"""Committed Google upload/poll races against real PG, with provider I/O replaced."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
from unittest import skipUnless
from unittest.mock import patch
import uuid

from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from console import test_public_lead_capture as capture_fixtures
from leads.google_data_manager import GoogleDataManagerError
from leads.models import LeadEventAttempt, LeadEventOutbox, LeadFormDefinition, LeadSubmission
from leads.services import dispatch_outbox_event, dispatch_pending_outbox
from marketing.models import MarketingIntegration, MarketingProvider
from sitecore.models import Site


@skipUnless(connection.vendor == 'postgresql', 'Requires committed Google PostgreSQL delivery writers')
@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True)
class GoogleDeliveryConcurrencyTests(TransactionTestCase):
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
        # Isolated full-SQL DB is discarded by its owner; don't truncate auth
        # tables behind authoritative unmanaged foreign keys.
        return None

    def setUp(self):
        self.site, _ = Site.objects.get_or_create(code='google_delivery_races', defaults={
            'name': 'Synthetic Google races', 'default_locale': 'en', 'enabled': True})
        self.form, _ = LeadFormDefinition.objects.get_or_create(site=self.site, code='google-races',
            defaults={'name': 'Synthetic form', 'capi_enabled': True, 'notify_emails': ''})
        provider, _ = MarketingProvider.objects.get_or_create(code='google', defaults={'name': 'Google'})
        self.integration, _ = MarketingIntegration.objects.get_or_create(site=self.site, provider=provider,
            integration_type='data_manager', defaults={'name': 'Synthetic Google account', 'public_id': '1234567890',
                'enabled': True, 'config_json': {'qualified_conversion_action_id': '123', 'validate_only': False}})

    def event(self, status='processing'):
        now = timezone.now()
        lead = LeadSubmission.objects.create(site=self.site, form=self.form, full_name='Synthetic Google',
            submitted_at=now, stage='qualified', stage_updated_at=now, consent_json={'marketing': True})
        request_id = 'synthetic-' + uuid.uuid4().hex if status == 'processing' else ''
        return LeadEventOutbox.objects.create(submission=lead, integration=self.integration,
            stage_key='qualified', event_name='qualified_lead', event_id=str(uuid.uuid4()),
            payload_json={'validateOnly': False, 'events': [{'transactionId': 'synthetic-only'}]},
            status=status, attempts=1 if status == 'processing' else 0,
            provider_request_id=request_id, response_json={'requestId': request_id} if request_id else {})

    @staticmethod
    def response(status):
        return {'requestStatusPerDestination': [{'requestStatus': status}]}

    def worker(self, pk):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            return dispatch_outbox_event(LeadEventOutbox.objects.get(pk=pk)), pid
        finally:
            close_old_connections()

    def delayed_poll(self, late):
        event = self.event()
        arrived, release = threading.Event(), threading.Event()
        calls = []

        def provider(**kwargs):
            self.assertEqual(kwargs['method'], 'GET')
            self.assertIn(event.provider_request_id, kwargs['url'])
            calls.append(kwargs['method'])
            if len(calls) == 1:
                arrived.set()
                if not release.wait(10):
                    raise AssertionError('Synthetic poll not released')
                if isinstance(late, Exception):
                    raise late
                return self.response(late)
            return self.response('SUCCESS')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider), ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(self.worker, event.pk)
            try:
                self.assertTrue(arrived.wait(10))
                fast = pool.submit(self.worker, event.pk).result(timeout=5)
                self.assertTrue(fast[0])
            finally:
                release.set()
            old = slow.result(timeout=10)
        self.assertNotEqual(fast[1], old[1])
        self.assertEqual(calls, ['GET', 'GET'])
        event.refresh_from_db()
        self.assertEqual(event.status, 'sent')
        self.assertEqual(event.last_error, '')
        self.assertIsNone(event.next_attempt_at)
        self.assertEqual(event.response_json['request_status']['requestStatusPerDestination'][0]['requestStatus'], 'SUCCESS')

    def test_late_processing_cannot_overwrite_success(self):
        self.delayed_poll('PROCESSING')

    def test_late_failure_cannot_overwrite_success(self):
        self.delayed_poll('FAILED')

    def test_late_get_error_cannot_inject_retry_into_success(self):
        self.delayed_poll(GoogleDataManagerError('synthetic late read failure'))

    def test_old_request_poll_cannot_write_a_replaced_request(self):
        event = self.event()
        arrived, release = threading.Event(), threading.Event()

        def provider(**kwargs):
            self.assertEqual(kwargs['method'], 'GET')
            arrived.set()
            if not release.wait(10):
                raise AssertionError('Synthetic old request not released')
            return self.response('SUCCESS')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider), ThreadPoolExecutor(max_workers=1) as pool:
            slow = pool.submit(self.worker, event.pk)
            try:
                self.assertTrue(arrived.wait(10))
                LeadEventOutbox.objects.filter(pk=event.pk).update(provider_request_id='synthetic-new-request',
                    response_json={'requestId': 'synthetic-new-request'}, updated_at=timezone.now())
            finally:
                release.set()
            slow.result(timeout=10)
        event.refresh_from_db()
        self.assertEqual((event.status, event.provider_request_id), ('processing', 'synthetic-new-request'))
        self.assertEqual(event.response_json, {'requestId': 'synthetic-new-request'})

    def test_ambiguous_post_does_not_enter_another_upload(self):
        event = self.event('pending')
        methods = []

        def provider(**kwargs):
            methods.append(kwargs['method'])
            raise GoogleDataManagerError('synthetic POST response lost after possible acceptance')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider):
            self.assertFalse(dispatch_outbox_event(event))
            LeadEventOutbox.objects.filter(pk=event.pk).update(next_attempt_at=None)
            dispatch_pending_outbox(submission=event.submission, failed_before=None)
        self.assertEqual(methods, ['POST'])
        event.refresh_from_db()
        self.assertEqual(event.status, 'unknown')

    def test_same_request_id_does_not_allow_an_old_generation_to_commit(self):
        event = self.event()
        arrived, release = threading.Event(), threading.Event()

        def provider(**kwargs):
            self.assertEqual(kwargs['method'], 'GET')
            arrived.set()
            if not release.wait(10):
                raise AssertionError('Synthetic old generation not released')
            return self.response('SUCCESS')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider), ThreadPoolExecutor(max_workers=1) as pool:
            slow = pool.submit(self.worker, event.pk)
            try:
                self.assertTrue(arrived.wait(10))
                LeadEventOutbox.objects.filter(pk=event.pk).update(attempts=2, updated_at=timezone.now())
            finally:
                release.set()
            slow.result(timeout=10)
        event.refresh_from_db()
        self.assertEqual(event.status, 'processing')
        self.assertEqual(event.attempts, 2)
        self.assertNotIn('request_status', event.response_json)

    def test_success_can_follow_a_newer_processing_poll_of_the_same_request(self):
        event = self.event()
        arrived, release = threading.Event(), threading.Event()
        calls = []

        def provider(**kwargs):
            self.assertEqual(kwargs['method'], 'GET')
            calls.append(kwargs['method'])
            if len(calls) == 1:
                arrived.set()
                if not release.wait(10):
                    raise AssertionError('Synthetic terminal poll not released')
                return self.response('SUCCESS')
            return self.response('PROCESSING')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider), ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(self.worker, event.pk)
            try:
                self.assertTrue(arrived.wait(10))
                fast = pool.submit(self.worker, event.pk).result(timeout=5)
                self.assertFalse(fast[0])
            finally:
                release.set()
            terminal = slow.result(timeout=10)
        event.refresh_from_db()
        self.assertTrue(terminal[0])
        self.assertEqual(event.status, 'sent')
        self.assertEqual(calls, ['GET', 'GET'])

    def test_revocation_during_oauth_preparation_prevents_post(self):
        event = self.event('pending')
        methods = []

        def revoke(_integration):
            LeadSubmission.objects.filter(pk=event.submission_id).update(consent_json={'marketing': False})
            return 'synthetic-only'

        def provider(**kwargs):
            methods.append(kwargs['method'])
            return {'requestId': 'synthetic-accepted'} if kwargs['method'] == 'POST' else self.response('SUCCESS')

        with patch('leads.google_data_manager._load_google_credentials', side_effect=revoke), \
                patch('leads.google_data_manager._google_request', side_effect=provider):
            result = dispatch_outbox_event(event)
        self.assertEqual(methods, [])
        self.assertFalse(result)

    def test_preparation_rechecks_lifecycle_configuration_and_site(self):
        original_config = self.integration.config_json
        cases = [
            ('trash', LeadSubmission, {'trashed_at': timezone.now()}, 'pending'),
            ('privacy', LeadSubmission, {'config_json': {'privacy_deleted': True}}, 'skipped'),
            ('archive', LeadSubmission, {'config_json': {'archived': True}}, 'skipped'),
            ('spam', LeadSubmission, {'stage': 'spam'}, 'skipped'),
            ('site_off', Site, {'enabled': False}, 'skipped'),
            ('integration_off', MarketingIntegration, {'enabled': False}, 'skipped'),
            ('configuration_changed', MarketingIntegration,
             {'config_json': {'synthetic-new-config': True}, 'updated_at': timezone.now()}, 'pending'),
        ]
        try:
            for label, model, changes, want in cases:
                with self.subTest(case=label):
                    Site.objects.filter(pk=self.site.pk).update(enabled=True)
                    MarketingIntegration.objects.filter(pk=self.integration.pk).update(
                        enabled=True, config_json=original_config, updated_at=timezone.now())
                    event = self.event('pending')
                    original_id = event.event_id
                    target = {LeadSubmission: event.submission_id, Site: self.site.pk,
                              MarketingIntegration: self.integration.pk}[model]

                    def withdraw(_integration):
                        model.objects.filter(pk=target).update(**changes)
                        return 'synthetic-only'

                    with patch('leads.google_data_manager._load_google_credentials', side_effect=withdraw), \
                            patch('leads.google_data_manager._google_request') as platform:
                        self.assertFalse(dispatch_outbox_event(event))
                    platform.assert_not_called()
                    event.refresh_from_db()
                    attempt = LeadEventAttempt.objects.get(outbox=event)
                    self.assertEqual((event.status, event.attempts, event.event_id), (want, 0, original_id))
                    self.assertEqual((attempt.state, attempt.handoff_at), ('cancelled', None))
        finally:
            Site.objects.filter(pk=self.site.pk).update(enabled=True)
            MarketingIntegration.objects.filter(pk=self.integration.pk).update(
                enabled=True, config_json=original_config, updated_at=timezone.now())

    def test_trash_after_committed_post_keeps_accepted_request_without_new_upload(self):
        event = self.event('pending')
        methods = []

        def platform(**kwargs):
            methods.append(kwargs['method'])
            if kwargs['method'] == 'POST':
                self.assertFalse(connection.in_atomic_block)
                self.assertEqual(LeadEventAttempt.objects.get(outbox=event).state, 'handoff')
                LeadSubmission.objects.filter(pk=event.submission_id).update(trashed_at=timezone.now())
                return {'requestId': 'synthetic-accepted-before-trash'}
            return self.response('SUCCESS')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=platform):
            dispatch_outbox_event(event)
            self.assertFalse(dispatch_outbox_event(event))
        self.assertEqual(methods.count('POST'), 1)
        self.assertEqual(LeadEventAttempt.objects.get(outbox=event).state, 'accepted')
        event.refresh_from_db()
        self.assertEqual(event.provider_request_id, 'synthetic-accepted-before-trash')
        self.assertIn(event.status, ('processing', 'sent'))

    def test_upload_claim_is_durable_and_an_independent_event_can_complete(self):
        event, independent = self.event('pending'), self.event('pending')
        arrived, release = threading.Event(), threading.Event()
        calls = []

        def provider(**kwargs):
            calls.append(kwargs['method'])
            if kwargs['method'] == 'GET':
                return self.response('SUCCESS')
            if not arrived.is_set():
                arrived.set()
                if not release.wait(10):
                    raise AssertionError('Synthetic upload not released')
            return {'requestId': 'synthetic-' + uuid.uuid4().hex}

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider), ThreadPoolExecutor(max_workers=3) as pool:
            slow = pool.submit(self.worker, event.pk)
            try:
                self.assertTrue(arrived.wait(10))
                self.assertTrue(LeadEventAttempt.objects.filter(outbox=event, state='handoff').exists())
                duplicate = pool.submit(self.worker, event.pk).result(timeout=5)
                other = pool.submit(self.worker, independent.pk).result(timeout=5)
                self.assertFalse(duplicate[0])
                self.assertTrue(other[0])
            finally:
                release.set()
            finished = slow.result(timeout=10)
        self.assertTrue(finished[0])
        self.assertEqual(calls.count('POST'), 2)
        self.assertEqual(len({finished[1], duplicate[1], other[1]}), 3)
        event.refresh_from_db()
        self.assertEqual(event.status, 'sent')
        self.assertEqual(LeadEventAttempt.objects.filter(outbox=event).count(), 1)

    def test_outer_rollback_has_no_upload_and_commit_has_one(self):
        event = self.event('pending')
        methods = []

        def provider(**kwargs):
            methods.append(kwargs['method'])
            return {'requestId': 'synthetic-commit'} if kwargs['method'] == 'POST' else self.response('SUCCESS')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider):
            with transaction.atomic():
                self.assertFalse(dispatch_outbox_event(event))
                transaction.set_rollback(True)
            self.assertEqual(methods, [])
            with transaction.atomic():
                self.assertFalse(dispatch_outbox_event(event))
                self.assertEqual(methods, [])
            self.assertEqual(methods, ['POST', 'GET'])
        event.refresh_from_db()
        self.assertEqual(event.status, 'sent')

    def test_positive_upload_receipt_survives_outbox_projection_failure(self):
        event = self.event('pending')
        methods = []
        original_save = LeadEventOutbox.save

        def failing_summary(instance, *args, **kwargs):
            if instance.pk == event.pk and instance.status == 'processing':
                raise DatabaseError('synthetic accepted summary failure')
            return original_save(instance, *args, **kwargs)

        def provider(**kwargs):
            methods.append(kwargs['method'])
            return {'requestId': 'synthetic-persisted-receipt'} if kwargs['method'] == 'POST' else self.response('SUCCESS')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider):
            with patch.object(LeadEventOutbox, 'save', failing_summary):
                try:
                    dispatch_outbox_event(event)
                except DatabaseError:
                    pass
            self.assertTrue(LeadEventAttempt.objects.filter(outbox=event, state='accepted',
                provider_request_id='synthetic-persisted-receipt').exists())
            dispatch_pending_outbox(submission=event.submission, failed_before=None)
        self.assertEqual(methods.count('POST'), 1)
        event.refresh_from_db()
        self.assertEqual(event.status, 'sent')

    def test_final_receipt_survives_projection_failure_and_is_not_mutable(self):
        event = self.event()
        methods = []
        original_save = LeadEventOutbox.save

        def failing_summary(instance, *args, **kwargs):
            if instance.pk == event.pk and instance.status == 'sent':
                raise DatabaseError('synthetic final summary failure')
            return original_save(instance, *args, **kwargs)

        def provider(**kwargs):
            methods.append(kwargs['method'])
            return self.response('SUCCESS')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider):
            with patch.object(LeadEventOutbox, 'save', failing_summary):
                try:
                    dispatch_outbox_event(event)
                except DatabaseError:
                    pass
            # The final provider fact must survive a failed legacy summary.
            table = 'lead_event_result'
            self.assertIn(table, connection.introspection.table_names())
            with connection.cursor() as cursor:
                cursor.execute('SELECT id, status FROM lead_event_result WHERE outbox_id=%s', [event.pk])
                receipt = cursor.fetchone()
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt[1], 'sent')
            with self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE lead_event_result SET status='failed' WHERE id=%s", [receipt[0]])
            dispatch_pending_outbox(submission=event.submission, failed_before=None)
        event.refresh_from_db()
        self.assertEqual(event.status, 'sent')
        self.assertEqual(methods, ['GET'])

    def test_missing_legacy_processing_request_stays_unknown_not_retryable(self):
        event = self.event()
        LeadEventOutbox.objects.filter(pk=event.pk).update(provider_request_id='', response_json={})
        event.refresh_from_db()
        with patch('leads.google_data_manager._google_request') as provider:
            self.assertFalse(dispatch_outbox_event(event))
            dispatch_pending_outbox(submission=event.submission, failed_before=None)
        provider.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.status, 'unknown')

    def test_failed_request_then_failed_preparation_can_still_retry_without_polling_old_request(self):
        event = self.event('pending')
        methods = []
        posts = []

        def provider(**kwargs):
            methods.append(kwargs['method'])
            if kwargs['method'] == 'POST':
                posts.append(kwargs)
                return {'requestId': 'synthetic-request-' + str(len(posts))}
            return self.response('FAILED' if len(posts) == 1 else 'SUCCESS')

        with patch('leads.google_data_manager._google_request', side_effect=provider):
            with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'):
                self.assertFalse(dispatch_outbox_event(event))
            with patch('leads.google_data_manager._load_google_credentials', side_effect=GoogleDataManagerError('synthetic preflight')):
                self.assertFalse(dispatch_outbox_event(event))
            with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'):
                self.assertTrue(dispatch_outbox_event(event))
        self.assertEqual(methods, ['POST', 'GET', 'POST', 'GET'])
        event.refresh_from_db()
        self.assertEqual(event.status, 'sent')
        self.assertEqual(event.attempts, 3)
        self.assertEqual(list(LeadEventAttempt.objects.filter(outbox=event).order_by('generation')
            .values_list('state', flat=True)), ['accepted', 'failed', 'accepted'])

    def test_trash_pause_preserves_processing_and_restore_only_polls(self):
        event = self.event()
        LeadSubmission.objects.filter(pk=event.submission_id).update(trashed_at=timezone.now())
        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', return_value=self.response('SUCCESS')) as provider:
            self.assertFalse(dispatch_outbox_event(event))
            event.refresh_from_db()
            self.assertEqual(event.status, 'processing')
            LeadSubmission.objects.filter(pk=event.submission_id).update(trashed_at=None)
            self.assertTrue(dispatch_outbox_event(event))
        self.assertEqual(provider.call_args.kwargs['method'], 'GET')
        event.refresh_from_db()
        self.assertEqual(event.status, 'sent')

    def test_terminal_poll_keeps_the_latest_observation_time(self):
        event = self.event()
        original_time = timezone.now() - timedelta(days=1)
        LeadEventOutbox.objects.filter(pk=event.pk).update(last_attempt_at=original_time)
        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', return_value=self.response('SUCCESS')):
            self.assertTrue(dispatch_outbox_event(event))
        event.refresh_from_db()
        self.assertGreater(event.last_attempt_at, original_time)

    def test_expired_google_handoff_cannot_open_a_second_upload(self):
        event = self.event('pending')
        arrived, release = threading.Event(), threading.Event()
        methods = []

        def provider(**kwargs):
            methods.append(kwargs['method'])
            arrived.set()
            if not release.wait(10):
                raise AssertionError('Synthetic expired upload not released')
            raise GoogleDataManagerError('synthetic ambiguous old response')

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=provider), ThreadPoolExecutor(max_workers=1) as pool:
            slow = pool.submit(self.worker, event.pk)
            try:
                self.assertTrue(arrived.wait(10))
                with patch('leads.marketing_delivery.timezone.now', return_value=timezone.now() + timedelta(minutes=20)):
                    dispatch_pending_outbox(submission=event.submission, failed_before=None)
            finally:
                release.set()
            slow.result(timeout=10)
        self.assertEqual(methods, ['POST'])
        event.refresh_from_db()
        self.assertEqual(event.status, 'unknown')
        self.assertEqual(LeadEventAttempt.objects.filter(outbox=event).count(), 1)
