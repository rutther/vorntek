"""Committed two-connection SMTP claim regression, with no real transport."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
import threading
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from console import test_public_lead_capture as capture_fixtures
from leads.models import LeadFormDefinition, LeadNotificationAttempt, LeadSubmission
from leads.notifications import send_submission_notification
from sitecore.models import Site


@skipUnless(connection.vendor == 'postgresql', 'Requires two committed PostgreSQL connections')
@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SITEOS_LEAD_REMINDER_REPEAT_HOURS=24)
class NotificationConcurrencyTests(TransactionTestCase):
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
        # This driver owns a disposable full-SQL database; managed auth flush
        # cannot truncate tables referenced by authoritative unmanaged tables.
        return None

    def setUp(self):
        self.site, _ = Site.objects.get_or_create(code='notification_concurrency', defaults={
            'name': 'Synthetic notification concurrency', 'base_url': 'https://example.test',
            'default_locale': 'en', 'enabled': True,
        })
        self.form, _ = LeadFormDefinition.objects.get_or_create(site=self.site, code='notify-concurrency', defaults={
            'name': 'Synthetic only', 'notify_emails': 'synthetic-recipient@example.test', 'capi_enabled': False,
        })

    def lead(self, name, reminder):
        now = timezone.now()
        return LeadSubmission.objects.create(
            site=self.site, form=self.form, full_name=name, stage='new',
            submitted_at=now - timedelta(hours=5), stage_updated_at=now,
            notification_status='sent' if reminder else 'pending',
        )

    def send(self, lead, reminder):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            return send_submission_notification(lead, reminder=reminder), pid
        finally:
            close_old_connections()

    def test_initial_and_reminder_claims_exclude_overlap_without_global_lock(self):
        for reminder in (False, True):
            with self.subTest(reminder=reminder):
                primary = self.lead(f'Synthetic primary {reminder}', reminder)
                independent = self.lead(f'Synthetic independent {reminder}', reminder)
                arrived = threading.Event()
                release = threading.Event()
                transported = []
                guard = threading.Lock()

                def transport(messages):
                    subject = messages[0].subject
                    with guard:
                        first_primary = primary.full_name in subject and not any(primary.full_name in s for s in transported)
                        transported.append(subject)
                    if first_primary:
                        arrived.set()
                        if not release.wait(10):
                            raise AssertionError('Synthetic transport was not released')
                    return 1

                with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=transport), ThreadPoolExecutor(max_workers=3) as pool:
                    first = pool.submit(self.send, primary, reminder)
                    try:
                        self.assertTrue(arrived.wait(10))
                        second_result, second_pid = pool.submit(self.send, primary, reminder).result(timeout=5)
                        other_result, other_pid = pool.submit(self.send, independent, reminder).result(timeout=5)
                        self.assertFalse(second_result)
                        self.assertTrue(other_result)
                    finally:
                        release.set()
                    first_result, first_pid = first.result(timeout=10)
                self.assertTrue(first_result)
                self.assertEqual(len({first_pid, second_pid, other_pid}), 3)
                self.assertEqual(sum(primary.full_name in subject for subject in transported), 1)
                self.assertEqual(LeadNotificationAttempt.objects.filter(submission=primary).count(), 1)
                self.assertEqual(LeadNotificationAttempt.objects.get(submission=primary).state, 'accepted')
                primary.refresh_from_db()
                self.assertIsNotNone(primary.reminder_sent_at if reminder else primary.notified_at)

    def test_ambient_transaction_defers_transport_and_discards_rollback(self):
        lead = self.lead('Synthetic transaction deferral', False)
        transported = []

        def transport(messages):
            transported.extend(messages)
            return 1

        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=transport):
            with self.assertRaisesRegex(RuntimeError, 'synthetic rollback'), transaction.atomic():
                self.assertFalse(send_submission_notification(lead))
                self.assertEqual(len(transported), 0)
                self.assertEqual(LeadNotificationAttempt.objects.filter(submission=lead).count(), 0)
                raise RuntimeError('synthetic rollback')
            self.assertEqual(len(transported), 0)
            with transaction.atomic():
                self.assertFalse(send_submission_notification(lead))
                self.assertEqual(len(transported), 0)
            self.assertEqual(len(transported), 1)
        accepted = LeadNotificationAttempt.objects.get(submission=lead)
        self.assertEqual((accepted.state, accepted.generation), ('accepted', 1))

    @override_settings(SITEOS_LEAD_REMINDER_HOURS=9999)
    def test_actual_direct_commands_share_one_initial_claim(self):
        lead = self.lead('Synthetic direct command primary', False)
        arrived = threading.Event()
        release = threading.Event()
        transported = []

        def transport(messages):
            transported.extend(messages)
            if len(transported) == 1:
                arrived.set()
                if not release.wait(10):
                    raise AssertionError('Synthetic command transport not released')
            return 1

        def command():
            close_old_connections()
            try:
                output = StringIO()
                call_command('send_lead_reminders', stdout=output)
                return output.getvalue()
            finally:
                close_old_connections()

        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=transport), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(command)
            try:
                self.assertTrue(arrived.wait(10))
                second = pool.submit(command).result(timeout=5)
                self.assertIn('notification retries 0/0', second)
            finally:
                release.set()
            self.assertIn('notification retries 1/1', first.result(timeout=10))
        self.assertEqual(len(transported), 1)
        self.assertEqual(LeadNotificationAttempt.objects.get(submission=lead).state, 'accepted')
