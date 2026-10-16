"""Two real PostgreSQL HTTP writers reconcile one SMTP fact, never send."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
from unittest import skipUnless
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
from django.utils import timezone

from console.access import GROUP_BY_ROLE, ROLE_SYSTEM_ADMIN
from console.models import AuditLog
from console.notification_forms import NotificationReviewForm
from console.test_public_lead_capture import PublicLeadCaptureVerticalTests
from leads.models import LeadFormDefinition, LeadNotificationAttempt, LeadSubmission
from sitecore.models import Site, SiteLocale


@skipUnless(connection.vendor == 'postgresql', 'Requires committed PostgreSQL HTTP writers')
class NotificationReviewConcurrencyTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        with connection.schema_editor() as editor:
            for model in PublicLeadCaptureVerticalTests.unmanaged_models:
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
        # Fresh disposable full-SQL database is owned by the test driver;
        # managed-only auth flush cannot truncate authoritative SQL FKs.
        return None

    def setUp(self):
        site, _ = Site.objects.get_or_create(code='siteos_demo', defaults={
            'name': 'Synthetic review concurrency', 'default_locale': 'en', 'enabled': True})
        SiteLocale.objects.get_or_create(site=site, locale_code='en', defaults={
            'label': 'English', 'is_default': True, 'enabled': True})
        form, _ = LeadFormDefinition.objects.get_or_create(site=site, code='notification-concurrent-review',
            defaults={'name': 'Synthetic only', 'notify_emails': 'synthetic-recipient@example.test',
                      'capi_enabled': False})
        now = timezone.now()
        self.lead = LeadSubmission.objects.create(site=site, form=form, full_name='Synthetic receipt race',
            submitted_at=now - timedelta(hours=1), stage_updated_at=now)
        self.attempt = LeadNotificationAttempt.objects.create(submission=self.lead, kind='initial', generation=1,
            state='unknown', lead_version=self.lead.updated_at, handoff_at=now - timedelta(minutes=20),
            message_hash='c' * 64, message_id='<synthetic-review-race@example.test>')
        self.page = f'/admin/sales/leads/{self.lead.pk}/notifications/'
        self.action = self.page + f'{self.attempt.pk}/reconcile/'
        self.group, _ = Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])

    def clients(self, same_actor=False):
        users = []
        clients = []
        for index in range(2):
            if same_actor and users:
                user = users[0]
            else:
                user = get_user_model().objects.create_user(username=f'notification-race-{uuid.uuid4().hex}')
                user.groups.add(self.group)
            users.append(user)
            client = Client(enforce_csrf_checks=True)
            client.force_login(user)
            response = client.get(self.page)
            self.assertEqual(response.status_code, 200)
            form = next(item['form'] for item in response.context['review_items']
                        if item['attempt'].pk == self.attempt.pk)
            clients.append((client, form['version'].value(), client.cookies['csrftoken'].value))
        return clients

    def race(self, clients, outcomes):
        barrier = threading.Barrier(2, timeout=10)
        original_clean = NotificationReviewForm.clean

        def synchronized_clean(form):
            result = original_clean(form)
            barrier.wait()
            return result

        def post(index):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                client, version, csrf = clients[index]
                response = client.post(self.action, {
                    'version': version, 'outcome': outcomes[index],
                    'evidence': '合成邮件平台记录已核对，并确认发送线程结束。',
                    'confirmed': 'on', 'stopped': 'on', 'csrfmiddlewaretoken': csrf,
                })
                return response.status_code, pid, outcomes[index]
            finally:
                close_old_connections()

        with patch.object(NotificationReviewForm, 'clean', new=synchronized_clean), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(post, (0, 1)))
        self.assertEqual(len({pid for _status, pid, _outcome in results}), 2)
        self.assertEqual(len(mail.outbox), 0)
        return results

    def test_conflicting_admin_observations_commit_once_and_reject_stale_result(self):
        results = self.race(self.clients(), ('accepted', 'not_accepted'))
        self.assertEqual(sorted(status for status, _pid, _outcome in results), [302, 409])
        winner = next(outcome for status, _pid, outcome in results if status == 302)
        self.attempt.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertEqual(self.attempt.state, 'accepted' if winner == 'accepted' else 'failed')
        self.assertEqual(self.lead.notification_status, 'sent' if winner == 'accepted' else 'failed')
        audits = AuditLog.objects.filter(action='notification_attempt_reconciled', entity_id=self.lead.pk)
        self.assertEqual(audits.count(), 1)
        self.assertEqual(audits.get().metadata_json['outcome'], winner)

    def test_concurrent_duplicate_same_actor_is_success_without_second_audit(self):
        clients = self.clients(same_actor=True)
        # The exact same browser request/version is replayed using two sessions.
        clients[1] = (clients[1][0], clients[0][1], clients[1][2])
        results = self.race(clients, ('accepted', 'accepted'))
        self.assertEqual([status for status, _pid, _outcome in results], [302, 302])
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.state, 'accepted')
        self.assertEqual(AuditLog.objects.filter(action='notification_attempt_reconciled', entity_id=self.lead.pk).count(), 1)
