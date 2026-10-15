from __future__ import annotations

from datetime import timedelta
from io import StringIO
import json
import os
import smtplib
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.db import connection, transaction
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from console import test_public_lead_capture as capture_fixtures
from console.secret_store import store_secret
from leads.email_delivery import SMTP_CONFIG_SECRET_KEY, SMTP_PASSWORD_SECRET_KEY, load_runtime_email_config
from leads.models import LeadFormDefinition, LeadNotificationAttempt, LeadSubmission
from leads.notifications import _send_submission_notification_now as send_submission_notification
from sitecore.models import Site


@override_settings(
    NEWCROWN_ALLOW_EXTERNAL_IO=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='synthetic-notification@example.test',
    SITEOS_LEAD_REMINDER_HOURS=4,
    SITEOS_LEAD_REMINDER_REPEAT_HOURS=24,
)
class NotificationRecoveryTests(TestCase):
    """Committed-send core results; outer-transaction entry tested separately on PG."""

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

    def setUp(self):
        self.site = Site.objects.create(
            code='notification_site', name='Synthetic notification site',
            base_url='https://example.test', default_locale='en', enabled=True,
        )
        self.form = LeadFormDefinition.objects.create(
            site=self.site, code='notification-test', name='Synthetic notification',
            notify_emails='synthetic-recipient@example.test', capi_enabled=False,
        )
        self.now = timezone.now()

    def lead(self, *, age_hours=5, status='sent', reminder_at=None):
        return LeadSubmission.objects.create(
            site=self.site, form=self.form, stage='new',
            full_name='Synthetic notification recipient', email='synthetic-buyer@example.test',
            submitted_at=self.now - timedelta(hours=age_hours), stage_updated_at=self.now,
            notification_status=status, notified_at=self.now if status == 'sent' else None,
            reminder_sent_at=reminder_at,
        )

    def command(self, *, after_minutes=0):
        output = StringIO()
        # TestCase's artificial outer transaction never commits. Exercise the
        # real committed-send implementation here; actual command/autocommit
        # and commit/rollback entry behavior have separate two-connection tests.
        with patch('leads.management.commands.send_lead_reminders.send_submission_notification', new=send_submission_notification), \
             patch('leads.management.commands.send_lead_reminders.timezone',
                   SimpleNamespace(now=lambda: timezone.now() + timedelta(minutes=after_minutes))):
            call_command('send_lead_reminders', stdout=output)
        return output.getvalue()

    def test_zero_reminder_does_not_stamp_success_and_later_command_retries(self):
        lead = self.lead()
        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', return_value=0):
            self.assertFalse(send_submission_notification(lead, reminder=True))
        lead.refresh_from_db()
        self.assertIsNone(lead.reminder_sent_at)
        self.assertTrue(lead.notification_error)
        self.assertEqual(lead.notification_status, 'sent')
        self.assertIn('overdue reminders 0/0', self.command())
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('overdue reminders 1/1', self.command(after_minutes=16))
        lead.refresh_from_db()
        self.assertIsNotNone(lead.reminder_sent_at)
        self.assertEqual(lead.notification_error, '')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('overdue reminders 0/0', self.command())
        self.assertEqual(len(mail.outbox), 1)

    def test_zero_reminder_preserves_previous_success_timestamp(self):
        old = self.now - timedelta(hours=25)
        lead = self.lead(reminder_at=old)
        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', return_value=0):
            self.assertFalse(send_submission_notification(lead, reminder=True))
        lead.refresh_from_db()
        self.assertEqual(lead.reminder_sent_at, old)
        self.assertTrue(lead.notification_error)

    def test_reminder_exception_preserves_success_and_retry_eligibility(self):
        lead = self.lead()
        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=smtplib.SMTPAuthenticationError(535, b'synthetic transport unavailable')):
            self.assertFalse(send_submission_notification(lead, reminder=True))
        lead.refresh_from_db()
        self.assertIsNone(lead.reminder_sent_at)
        self.assertIn('SMTPAuthenticationError', lead.notification_error)
        self.assertIn('overdue reminders 0/0', self.command())
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('overdue reminders 1/1', self.command(after_minutes=16))

    def test_initial_zero_is_failed_then_success_is_not_replayed(self):
        lead = self.lead(age_hours=1, status='pending')
        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', return_value=0):
            self.assertFalse(send_submission_notification(lead))
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'failed')
        self.assertIsNone(lead.notified_at)
        self.assertIn('notification retries 0/0', self.command())
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('notification retries 1/1', self.command(after_minutes=16))
        self.assertIn('notification retries 0/0', self.command())
        self.assertEqual(len(mail.outbox), 1)

    def test_reentrant_sender_cannot_duplicate_same_initial_notification(self):
        lead = self.lead(age_hours=1, status='pending')
        delivered = []

        def transport(messages):
            delivered.extend(messages)
            if len(delivered) == 1:
                send_submission_notification(lead)
            return 1

        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=transport):
            self.assertTrue(send_submission_notification(lead))
        self.assertEqual(len(delivered), 1)
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'sent')

    def test_message_id_is_sent_and_persisted_before_handoff(self):
        lead = self.lead(age_hours=1, status='pending')
        sent_ids = []

        def transport(messages):
            actual = messages[0].message()['Message-ID']
            attempt = LeadNotificationAttempt.objects.get(submission=lead, state='handoff')
            self.assertEqual(attempt.message_id, actual)
            self.assertIn(str(attempt.pk), actual)
            self.assertTrue(actual.endswith('@example.test>'))
            sent_ids.append(actual)
            return 1

        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=transport):
            self.assertTrue(send_submission_notification(lead))
        self.assertEqual(len(sent_ids), 1)
        self.assertEqual(LeadNotificationAttempt.objects.get(submission=lead).message_id, sent_ids[0])

    def test_accepted_mail_projection_failure_does_not_send_again(self):
        lead = self.lead(age_hours=1, status='pending')
        original_save = LeadSubmission.save

        def fail_projection(instance, *args, **kwargs):
            if instance.pk == lead.pk and 'notified_at' in kwargs.get('update_fields', ()):
                raise DatabaseError('synthetic projection unavailable')
            return original_save(instance, *args, **kwargs)

        with patch.object(LeadSubmission, 'save', new=fail_projection):
            self.assertTrue(send_submission_notification(lead))
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(send_submission_notification(lead))
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'sent')
        self.assertEqual(len(mail.outbox), 1)

    def test_ambiguous_transport_failure_does_not_blindly_retry(self):
        lead = self.lead(age_hours=1, status='pending')
        delivered = []

        def ambiguous_transport(messages):
            delivered.extend(messages)
            raise smtplib.SMTPServerDisconnected('synthetic response lost')

        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=ambiguous_transport):
            self.assertFalse(send_submission_notification(lead))
        self.command()
        self.assertEqual(len(delivered), 1)
        self.assertEqual(len(mail.outbox), 0)
        lead.refresh_from_db()
        self.assertTrue(lead.notification_error)
        self.assertIn('notification retries 0/0', self.command())
        self.assertEqual(len(mail.outbox), 0)

    def test_receipt_persistence_failure_leaves_handoff_blocking_resend(self):
        lead = self.lead(age_hours=1, status='pending')
        original_save = LeadNotificationAttempt.save

        def lose_receipt(instance, *args, **kwargs):
            if instance.state == 'accepted':
                raise DatabaseError('synthetic receipt unavailable')
            return original_save(instance, *args, **kwargs)

        with patch.object(LeadNotificationAttempt, 'save', new=lose_receipt):
            self.assertFalse(send_submission_notification(lead))
        self.assertEqual(len(mail.outbox), 1)
        attempt = LeadNotificationAttempt.objects.get(submission=lead)
        self.assertEqual(attempt.state, 'handoff')
        self.assertFalse(send_submission_notification(lead))
        self.assertIn('notification retries 0/0', self.command())
        self.assertEqual(len(mail.outbox), 1)

    def test_expired_claim_uuid_cannot_hand_off_after_new_generation(self):
        from leads.notification_attempts import begin_notification_handoff, claim_notification, finish_notification_attempt

        lead = self.lead(age_hours=1, status='pending')
        old = LeadNotificationAttempt.objects.create(
            submission=lead, kind='initial', generation=1, lead_version=lead.updated_at,
            created_at=self.now - timedelta(minutes=11),
        )
        new_lead, new = claim_notification(lead.pk, 'initial')
        self.assertEqual(new.generation, 2)
        self.assertIsNone(begin_notification_handoff(old.pk, ['synthetic-recipient@example.test'], load_runtime_email_config(), 'f' * 64))
        self.assertFalse(finish_notification_attempt(old.pk, 'accepted'))
        old.refresh_from_db()
        self.assertEqual(old.state, 'cancelled')
        self.assertTrue(finish_notification_attempt(new.pk, 'failed', 'synthetic preparation failure'))
        self.assertTrue(send_submission_notification(new_lead))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(LeadNotificationAttempt.objects.get(submission=lead, state='accepted').generation, 3)

    def test_lifecycle_change_before_handoff_cancels_then_restore_can_send(self):
        from leads.notifications import build_email_connection

        lead = self.lead(age_hours=1, status='pending')

        def retire_during_preparation(config):
            LeadSubmission.objects.filter(pk=lead.pk).update(trashed_at=timezone.now(), updated_at=timezone.now())
            return build_email_connection(config)

        with patch('leads.notifications.build_email_connection', side_effect=retire_during_preparation):
            self.assertFalse(send_submission_notification(lead))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(LeadNotificationAttempt.objects.get(submission=lead).state, 'cancelled')
        LeadSubmission.objects.filter(pk=lead.pk).update(trashed_at=None, restored_at=timezone.now(), updated_at=timezone.now())
        self.assertTrue(send_submission_notification(lead))
        self.assertEqual(len(mail.outbox), 1)

    @skipUnless(connection.vendor == 'postgresql', 'Requires actual SQL attempt update/receipt constraints')
    def test_real_sql_identity_terminal_and_receipt_constraints(self):
        lead = self.lead(age_hours=1, status='pending')
        self.assertTrue(send_submission_notification(lead))
        accepted = LeadNotificationAttempt.objects.get(submission=lead)
        with self.assertRaises(DatabaseError), transaction.atomic():
            LeadNotificationAttempt.objects.filter(pk=accepted.pk).update(generation=99)
        with self.assertRaises(DatabaseError), transaction.atomic():
            LeadNotificationAttempt.objects.filter(pk=accepted.pk).update(state='failed')
        with self.assertRaises(DatabaseError), transaction.atomic():
            LeadNotificationAttempt.objects.create(submission=lead, kind='initial', generation=2,
                                                   lead_version=lead.updated_at, state='accepted')
        accepted.refresh_from_db()
        self.assertEqual((accepted.generation, accepted.state), (1, 'accepted'))

    @skipUnless(connection.vendor == 'postgresql', 'Requires actual SMTP identity update trigger')
    def test_handed_off_message_id_cannot_change_while_resolving_unknown(self):
        lead = self.lead(age_hours=1, status='pending')
        attempt = LeadNotificationAttempt.objects.create(
            submission=lead, kind='initial', generation=1, lead_version=lead.updated_at,
            state='unknown', handoff_at=self.now - timedelta(minutes=15),
            message_hash='a' * 64, message_id='<synthetic-original@example.test>',
        )
        # Neither a direct edit nor a simultaneous otherwise-valid resolution
        # may rebind the SMTP receipt to a different message.
        for values in ({'message_id': '<different@example.test>'},
                       {'state': 'failed', 'finished_at': self.now,
                        'message_id': '<different@example.test>'}):
            with self.assertRaises(DatabaseError), transaction.atomic():
                LeadNotificationAttempt.objects.filter(pk=attempt.pk).update(**values)
        attempt.refresh_from_db()
        self.assertEqual((attempt.state, attempt.message_id),
                         ('unknown', '<synthetic-original@example.test>'))
        LeadNotificationAttempt.objects.filter(pk=attempt.pk).update(state='failed', finished_at=self.now)
        attempt.refresh_from_db()
        self.assertEqual((attempt.state, attempt.message_id),
                         ('failed', '<synthetic-original@example.test>'))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='', EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='')
    def test_vault_read_error_is_retryable_not_policy_disabled(self):
        lead = self.lead(age_hours=1, status='pending')
        with patch('leads.email_delivery.load_secret', side_effect=ValueError('synthetic vault unavailable')):
            self.assertFalse(send_submission_notification(lead))
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'failed')
        self.assertIn('synthetic vault unavailable', lead.notification_error)
        self.assertIsNone(lead.notified_at)
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            self.assertIn('notification retries 0/0', self.command())
            self.assertEqual(len(mail.outbox), 0)
            self.assertIn('notification retries 1/1', self.command(after_minutes=16))
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'sent')
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='', EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='')
    def test_intentional_disabled_policy_does_not_enter_retry(self):
        lead = self.lead(age_hours=1, status='pending')
        stored = json.dumps({'enabled': False, 'host': 'smtp.example.test', 'port': 465, 'username': 'synthetic', 'from_email': 'sender@example.test'})
        with patch('leads.email_delivery.load_secret', side_effect=lambda key: stored if key == SMTP_CONFIG_SECRET_KEY else 'synthetic-only'):
            self.assertFalse(send_submission_notification(lead))
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'disabled')
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            self.assertIn('notification retries 0/0', self.command())
        self.assertEqual(len(mail.outbox), 0)

    def test_empty_recipients_remain_disabled_after_configuration_recovery(self):
        self.form.notify_emails = ''
        self.form.save(update_fields=['notify_emails'])
        lead = self.lead(age_hours=1, status='pending')
        self.assertFalse(send_submission_notification(lead))
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'disabled')
        self.assertIn('notification retries 0/0', self.command())
        self.assertEqual(len(mail.outbox), 0)

    def test_backend_initialization_failure_is_persisted_for_retry(self):
        lead = self.lead(age_hours=1, status='pending')
        with patch('leads.notifications.build_email_connection', side_effect=RuntimeError('synthetic backend unavailable')):
            self.assertFalse(send_submission_notification(lead))
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'failed')
        self.assertIn('synthetic backend unavailable', lead.notification_error)
        self.assertIn('notification retries 0/0', self.command())
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('notification retries 1/1', self.command(after_minutes=16))
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'sent')

    @skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL vault ciphertext/decryption')
    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='', EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='')
    def test_real_vault_wrong_key_recovers_without_aborting_caller_transaction(self):
        lead = self.lead(age_hours=1, status='pending')
        key = 'synthetic-vault-original-not-a-production-key'
        stored = json.dumps({'enabled': True, 'host': 'smtp.example.test', 'port': 465, 'username': 'synthetic', 'from_email': 'sender@example.test'})
        with patch.dict(os.environ, {'SITEOS_SECRET_VAULT_KEY': key}):
            store_secret(secret_key=SMTP_CONFIG_SECRET_KEY, plaintext=stored, updated_by='synthetic-test')
            store_secret(secret_key=SMTP_PASSWORD_SECRET_KEY, plaintext='synthetic-only', updated_by='synthetic-test')
        with patch.dict(os.environ, {'SITEOS_SECRET_VAULT_KEY': 'synthetic-vault-wrong-key'}):
            delivery = load_runtime_email_config()
            self.assertEqual(delivery.source, 'error')
            self.assertFalse(delivery.ready)
            self.assertFalse(send_submission_notification(lead))
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'failed')
        self.assertIsNone(lead.notified_at)
        with patch.dict(os.environ, {'SITEOS_SECRET_VAULT_KEY': key}):
            self.assertTrue(load_runtime_email_config().ready)
            with patch('django.core.mail.backends.smtp.EmailBackend.send_messages', return_value=1):
                self.assertIn('notification retries 0/0', self.command())
                self.assertIn('notification retries 1/1', self.command(after_minutes=16))
                self.assertIn('notification retries 0/0', self.command())
        lead.refresh_from_db()
        self.assertEqual(lead.notification_status, 'sent')
        self.assertIsNotNone(lead.notified_at)
