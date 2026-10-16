"""Real committed PG + valid-CSRF writers, synthetic provider boundaries only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
import threading
from unittest import skipUnless
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from console import test_public_lead_capture as capture_fixtures
from console.access import GROUP_BY_ROLE, ROLE_MARKETING_OPS
from console.marketing_review_forms import MarketingReviewForm
from console.models import AuditLog
from leads.marketing_delivery import _payload_hash
from leads.models import LeadEventAttempt, LeadEventOutbox, LeadEventResult, LeadFormDefinition, LeadSubmission
from marketing.models import MarketingIntegration, MarketingProvider
from sitecore.models import Site, SiteLocale


@skipUnless(connection.vendor == 'postgresql', 'Requires committed marketing PostgreSQL HTTP writers')
@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True)
class MarketingReviewConcurrencyTests(TransactionTestCase):
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
        return None  # Owner disposes the full-SQL DB; never truncate authoritative FKs.

    def setUp(self):
        self.site, _ = Site.objects.get_or_create(code='siteos_demo', defaults={
            'name': 'Synthetic marketing review', 'default_locale': 'en', 'enabled': True})
        SiteLocale.objects.get_or_create(site=self.site, locale_code='en', defaults={
            'label': 'English', 'is_default': True, 'enabled': True})
        self.form, _ = LeadFormDefinition.objects.get_or_create(site=self.site, code='marketing-review-races',
            defaults={'name': 'Synthetic only', 'capi_enabled': True, 'notify_emails': ''})
        self.group, _ = Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_MARKETING_OPS])
        now = timezone.now()
        self.lead = LeadSubmission.objects.create(site=self.site, form=self.form, full_name='Synthetic race',
            submitted_at=now, stage='qualified', stage_updated_at=now, consent_json={'marketing': True})
        provider, _ = MarketingProvider.objects.get_or_create(code='meta', defaults={'name': 'Meta'})
        self.integration = MarketingIntegration.objects.create(site=self.site, provider=provider,
            integration_type='pixel', public_id=str(1000000000000 + uuid.uuid4().int % 1000000000000),
            name='Synthetic review', enabled=True, routing_mode='legacy_site')
        self.event = LeadEventOutbox.objects.create(submission=self.lead, integration=self.integration,
            event_id='synthetic-race-' + uuid.uuid4().hex, stage_key='qualified', event_name='qualified_lead',
            payload_json={'fixture': True}, status='unknown', attempts=1)
        self.attempt = LeadEventAttempt.objects.create(outbox=self.event, generation=1, attempt_number=1,
            state='unknown', result_status='unknown', lead_version=self.lead.updated_at,
            integration_version=self.integration.updated_at, event_id=self.event.event_id,
            public_id=self.integration.public_id, payload_hash=_payload_hash(self.event), delivery_mode='live',
            handoff_at=now - timedelta(minutes=20), finished_at=now - timedelta(minutes=15))
        self.page = f'/admin/marketing/events/outbound/{self.event.pk}/review/'
        self.action = f'/admin/marketing/events/outbound/{self.event.pk}/reconcile/'

    def make_client(self, actor=None):
        actor = actor or get_user_model().objects.create_user(username='marketing-review-' + uuid.uuid4().hex)
        actor.groups.add(self.group)
        client = Client(enforce_csrf_checks=True)
        client.force_login(actor)
        response = client.get(self.page)
        self.assertEqual(response.status_code, 200)
        data = {'version': response.context['review_form']['version'].value(), 'event_id': self.event.event_id,
                'outcome': 'accepted', 'request_id': '', 'evidence': '合成平台日志对应完整事件编号，已确认发送结束。',
                'confirmed': 'on', 'stopped': 'on', 'csrfmiddlewaretoken': client.cookies['csrftoken'].value}
        return client, data, actor

    def race(self, clients, outcomes):
        barrier = threading.Barrier(2, timeout=10)
        original = MarketingReviewForm.clean

        def synchronized(form):
            result = original(form)
            barrier.wait()
            return result

        def post(index):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                client, data, actor = clients[index]
                response = client.post(self.action, dict(data, outcome=outcomes[index]))
                return response.status_code, pid, outcomes[index]
            finally:
                close_old_connections()

        with patch.object(MarketingReviewForm, 'clean', synchronized), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(post, (0, 1)))
        self.assertEqual(len({row[1] for row in results}), 2)
        return results

    def test_two_conflicting_valid_csrf_observations_commit_once(self):
        results = self.race((self.make_client(), self.make_client()), ('accepted', 'not_accepted'))
        self.assertEqual(sorted(row[0] for row in results), [302, 409])
        winner = next(row[2] for row in results if row[0] == 302)
        self.attempt.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.attempt.state, 'accepted' if winner == 'accepted' else 'failed')
        self.assertEqual(self.event.status, 'sent' if winner == 'accepted' else 'failed')
        self.assertEqual(AuditLog.objects.filter(action='marketing_attempt_reconciled', entity_id=self.event.pk).count(), 1)

    def test_same_actor_duplicate_replay_has_one_audit_and_two_successes(self):
        first = self.make_client()
        second = self.make_client(first[2])
        second[1]['version'] = first[1]['version']
        results = self.race((first, second), ('accepted', 'accepted'))
        self.assertEqual([row[0] for row in results], [302, 302])
        self.assertEqual(AuditLog.objects.filter(action='marketing_attempt_reconciled', entity_id=self.event.pk).count(), 1)

    def test_real_worker_late_success_cannot_replace_negative_manual_review(self):
        from leads.services import dispatch_outbox_event
        event = LeadEventOutbox.objects.create(submission=self.lead, integration=self.integration,
            event_id='synthetic-worker-' + uuid.uuid4().hex, stage_key='won', event_name='Purchase',
            payload_json={'fixture': True}, status='pending')
        self.event, self.attempt = event, None
        self.page = f'/admin/marketing/events/outbound/{event.pk}/review/'
        self.action = f'/admin/marketing/events/outbound/{event.pk}/reconcile/'
        arrived, release = threading.Event(), threading.Event()
        calls = []

        def transport(request, **kwargs):
            calls.append(request.full_url)
            arrived.set()
            if not release.wait(10):
                raise AssertionError('Synthetic worker not released')
            return BytesIO(b'{"events_received":1}')

        def worker():
            close_old_connections()
            try:
                return dispatch_outbox_event(LeadEventOutbox.objects.get(pk=event.pk))
            finally:
                close_old_connections()

        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', side_effect=transport), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(worker)
            try:
                self.assertTrue(arrived.wait(10))
                client, data, actor = self.make_client()
                with patch('leads.marketing_review.timezone.now', return_value=timezone.now() + timedelta(minutes=20)):
                    response = client.post(self.action, dict(data, outcome='not_accepted'))
                self.assertEqual(response.status_code, 302)
            finally:
                release.set()
            self.assertFalse(pending.result(timeout=10))
        event.refresh_from_db()
        self.assertEqual(event.status, 'failed')
        self.assertEqual(len(calls), 1)
        self.assertEqual(LeadEventAttempt.objects.get(outbox=event).state, 'failed')

    def test_exact_http_retry_rechecks_role_revoked_after_initial_read(self):
        from console import marketing_views
        event = LeadEventOutbox.objects.create(submission=self.lead, integration=self.integration,
            event_id='synthetic-retry-' + uuid.uuid4().hex, stage_key='won', event_name='Purchase',
            payload_json={'fixture': True}, status='failed')
        client, data, actor = self.make_client()
        original = marketing_views.outbox_retry_contract

        def revoke(outbox, *args, **kwargs):
            result = original(outbox, *args, **kwargs)
            actor.groups.clear()
            return result

        with patch.object(marketing_views, 'outbox_retry_contract', side_effect=revoke), \
                patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', return_value=BytesIO(b'{"events_received":1}')) as provider:
            response = client.post(f'/admin/api/marketing/outbox/{event.pk}/retry/',
                {'csrfmiddlewaretoken': data['csrfmiddlewaretoken']})
        self.assertEqual(response.status_code, 403)
        provider.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.status, 'failed')
        self.assertEqual(LeadEventAttempt.objects.filter(outbox=event).count(), 0)

    def google_http_retry_after_old_poll(self, late):
        """A real POST retry advances the request while an old GET is in flight."""
        from leads.services import dispatch_outbox_event
        provider, _ = MarketingProvider.objects.get_or_create(code='google', defaults={'name': 'Google'})
        integration = MarketingIntegration.objects.create(site=self.site, provider=provider,
            integration_type='data_manager', name='Synthetic retry account', enabled=True,
            public_id=str(1000000000000 + uuid.uuid4().int % 1000000000000),
            config_json={'qualified_conversion_action_id': '123', 'validate_only': False})
        now = timezone.now()
        old_id, new_id = 'old-' + uuid.uuid4().hex, 'new-' + uuid.uuid4().hex
        event = LeadEventOutbox.objects.create(submission=self.lead, integration=integration,
            stage_key='qualified', event_name='qualified_lead', event_id='synthetic-http-' + uuid.uuid4().hex,
            payload_json={'events': [{'transactionId': 'synthetic-only'}], 'validateOnly': False},
            status='processing', attempts=1, provider_request_id=old_id, provider_received_at=now,
            response_json={'requestId': old_id})
        first = LeadEventAttempt.objects.create(outbox=event, generation=1, attempt_number=1,
            state='accepted', result_status='processing', lead_version=self.lead.updated_at,
            integration_version=integration.updated_at, event_id=event.event_id,
            public_id=integration.public_id, payload_hash=_payload_hash(event), delivery_mode='live',
            provider_request_id=old_id, handoff_at=now, accepted_at=now, finished_at=now,
            response_json={'requestId': old_id})
        client, data, actor = self.make_client()
        arrived, release = threading.Event(), threading.Event()
        calls, backend_pids = [], []

        def platform(**kwargs):
            with connection.cursor() as cursor:
                backend_pids.append(cursor.execute('SELECT pg_backend_pid()').fetchone()[0])
            method = kwargs['method']
            calls.append((method, kwargs['url']))
            if method == 'POST':
                self.assertEqual(kwargs['payload'], event.payload_json)
                return {'requestId': new_id}
            self.assertEqual(method, 'GET')
            if old_id in kwargs['url']:
                if not arrived.is_set():
                    arrived.set()
                    if not release.wait(15):
                        raise AssertionError('Synthetic old GET not released')
                    if isinstance(late, Exception):
                        raise late
                    return {'requestStatusPerDestination': [{'requestStatus': late}]}
                return {'requestStatusPerDestination': [{'requestStatus': 'FAILED'}]}
            self.assertIn(new_id, kwargs['url'])
            return {'requestStatusPerDestination': [{'requestStatus': 'SUCCESS'}]}

        def old_worker():
            close_old_connections()
            try:
                return dispatch_outbox_event(LeadEventOutbox.objects.get(pk=event.pk))
            finally:
                close_old_connections()

        with patch('leads.google_data_manager._load_google_credentials', return_value='synthetic-only'), \
                patch('leads.google_data_manager._google_request', side_effect=platform), \
                ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(old_worker)
            try:
                self.assertTrue(arrived.wait(10))
                self.assertFalse(dispatch_outbox_event(LeadEventOutbox.objects.get(pk=event.pk)))
                event.refresh_from_db()
                self.assertEqual(event.status, 'failed')
                # Advance only the isolated test clock past the original delay.
                with patch('console.marketing_views.timezone.now', return_value=now + timedelta(minutes=10)):
                    result = client.post(f'/admin/api/marketing/outbox/{event.pk}/retry/',
                        {'csrfmiddlewaretoken': data['csrfmiddlewaretoken']})
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.json()['ok'])
                event.refresh_from_db()
                self.assertEqual((event.status, event.attempts, event.provider_request_id), ('sent', 2, new_id))
            finally:
                release.set()
            self.assertFalse(pending.result(timeout=10))
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts, event.provider_request_id), ('sent', 2, new_id))
        self.assertIsNone(event.next_attempt_at)
        self.assertEqual(event.response_json['request_status']['requestStatusPerDestination'][0]['requestStatus'], 'SUCCESS')
        self.assertEqual(sum(method == 'POST' for method, _ in calls), 1)
        self.assertGreaterEqual(len(set(backend_pids)), 2)
        self.assertEqual(list(LeadEventAttempt.objects.filter(outbox=event).order_by('generation')
                             .values_list('generation', 'state')), [(1, 'accepted'), (2, 'accepted')])
        self.assertEqual(LeadEventResult.objects.get(attempt=first).status, 'failed')
        self.assertEqual(LeadEventResult.objects.get(outbox=event, generation=2).status, 'sent')
        self.assertEqual(AuditLog.objects.filter(action='outbox_retry_claimed', entity_id=event.pk).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='outbox_retry_completed', entity_id=event.pk).count(), 1)

    def test_google_http_new_retry_ignores_late_old_processing(self):
        self.google_http_retry_after_old_poll('PROCESSING')

    def test_google_http_new_retry_ignores_late_old_success(self):
        self.google_http_retry_after_old_poll('SUCCESS')

    def test_google_http_new_retry_ignores_late_old_read_error(self):
        from leads.google_data_manager import GoogleDataManagerError
        self.google_http_retry_after_old_poll(GoogleDataManagerError('Synthetic late GET failure'))
