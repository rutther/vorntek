from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.contrib.sessions.models import Session
from django.db import DatabaseError, connection
from django.test import Client, TestCase
from django.utils import timezone

from console import test_public_lead_capture as capture_fixtures
from console.models import AuditLog
from leads.models import LeadNotificationAttempt, LeadSubmission
from sitecore.models import Site


class NotificationReviewTests(TestCase):
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
        capture_fixtures.PublicLeadCaptureVerticalTests.setUp(self)
        now = timezone.now()
        self.lead = LeadSubmission.objects.create(
            site=self.site, form=self.form, team=self.team, assignee=self.sales,
            full_name='合成通知核对线索', submitted_at=now - timedelta(hours=1), stage_updated_at=now,
        )
        self.attempt = LeadNotificationAttempt.objects.create(
            submission=self.lead, kind='initial', generation=1, state='unknown', lead_version=self.lead.updated_at,
            created_at=now - timedelta(minutes=20), handoff_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=12), message_hash='f' * 64, error='发送结果未确认。',
        )
        self.page = f'/admin/sales/leads/{self.lead.pk}/notifications/'
        self.action = self.page + f'{self.attempt.pk}/reconcile/'

    def data(self, *, outcome='accepted'):
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        item = next(row for row in response.context['review_items'] if row['attempt'].pk == self.attempt.pk)
        return {'version': item['form']['version'].value(), 'outcome': outcome,
                'evidence': '合成SMTP日志核对记录与本次尝试一致。', 'confirmed': 'on', 'stopped': 'on'}

    def test_accepted_review_records_audit_without_resending_and_is_idempotent(self):
        self.client.force_login(self.admin)
        data = self.data()
        response = self.client.post(self.action, data)
        self.assertEqual(response.status_code, 302)
        self.attempt.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertEqual(self.attempt.state, 'accepted')
        self.assertEqual(self.lead.notification_status, 'sent')
        self.assertIsNotNone(self.lead.notified_at)
        self.assertEqual(AuditLog.objects.filter(action='notification_attempt_reconciled').count(), 1)
        self.assertContains(self.client.get(self.page), '人工核对')
        self.assertEqual(self.client.post(self.action, data).status_code, 302)
        self.assertEqual(AuditLog.objects.filter(action='notification_attempt_reconciled').count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_not_accepted_review_restores_retry_eligibility_without_immediate_send(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.action, self.data(outcome='not_accepted'))
        self.assertEqual(response.status_code, 302)
        self.attempt.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertEqual(self.attempt.state, 'failed')
        self.assertEqual(self.lead.notification_status, 'failed')
        self.assertIsNone(self.lead.notified_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_reviewed_rejection_allows_original_sender_once_not_old_generation(self):
        from leads.notifications import _send_submission_notification_now
        from leads.notification_attempts import finish_notification_attempt
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.action, self.data(outcome='not_accepted')).status_code, 302)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(_send_submission_notification_now(self.lead))
        self.assertFalse(finish_notification_attempt(self.attempt.pk, 'accepted'))
        self.assertFalse(_send_submission_notification_now(self.lead))
        self.lead.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.lead.notification_status, 'sent')
        self.assertEqual(self.attempt.state, 'failed')
        current = LeadNotificationAttempt.objects.get(submission=self.lead, state='accepted')
        self.assertEqual(current.generation, 2)
        self.assertEqual(len(mail.outbox), 1)

    def test_invalid_and_stale_review_preserve_input_and_do_not_mutate(self):
        self.client.force_login(self.admin)
        data = self.data()
        invalid = dict(data, confirmed='')
        self.assertEqual(self.client.post(self.action, invalid).status_code, 400)
        stale = self.client.post(self.action, dict(data, version='tampered'))
        self.assertEqual(stale.status_code, 409)
        self.assertContains(stale, data['evidence'], status_code=409)
        fresh_form = next(row['form'] for row in stale.context['review_items'] if row['attempt'].pk == self.attempt.pk)
        self.assertNotEqual(fresh_form['version'].value(), 'tampered')
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.state, 'unknown')
        self.assertEqual(AuditLog.objects.filter(action='notification_attempt_reconciled').count(), 0)
        self.assertEqual(self.client.post(self.action, data).status_code, 302)
        self.assertEqual(self.client.post(self.action, dict(data, outcome='not_accepted')).status_code, 409)

    def test_sales_reads_own_status_but_cannot_reconcile(self):
        self.client.force_login(self.sales)
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '结果待核对')
        self.assertNotContains(response, 'name="outcome"')
        self.assertEqual(self.client.post(self.action, {'outcome': 'accepted'}).status_code, 403)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.state, 'unknown')

    def test_actor_role_revoked_after_form_validation_cannot_commit(self):
        self.client.force_login(self.admin)
        data = self.data()
        from console.notification_forms import NotificationReviewForm
        original = NotificationReviewForm.clean

        def withdraw(form):
            cleaned = original(form)
            self.admin.groups.clear()
            return cleaned

        with patch.object(NotificationReviewForm, 'clean', new=withdraw):
            self.assertEqual(self.client.post(self.action, data).status_code, 403)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.state, 'unknown')
        self.assertEqual(AuditLog.objects.filter(action='notification_attempt_reconciled').count(), 0)

    def test_audit_failure_rolls_back_review_and_projection(self):
        self.client.force_login(self.admin)
        data = self.data()
        with patch.object(AuditLog.objects, 'create', side_effect=DatabaseError('synthetic audit unavailable')):
            response = self.client.post(self.action, data)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, data['evidence'], status_code=503)
        self.attempt.refresh_from_db()
        self.lead.refresh_from_db()
        self.assertEqual(self.attempt.state, 'unknown')
        self.assertEqual(self.lead.notification_status, 'pending')
        self.assertEqual(len(mail.outbox), 0)

    def test_recent_handoff_cannot_be_opened_for_retry(self):
        # Build a fresh handoff; identity/handoff columns are immutable once set.
        self.attempt.delete()
        self.attempt = LeadNotificationAttempt.objects.create(
            submission=self.lead, kind='initial', generation=2, state='handoff', lead_version=self.lead.updated_at,
            handoff_at=timezone.now(), message_hash='e' * 64,
        )
        self.action = self.page + f'{self.attempt.pk}/reconcile/'
        self.client.force_login(self.admin)
        response = self.client.post(self.action, self.data(outcome='not_accepted'))
        self.assertEqual(response.status_code, 409)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.state, 'handoff')
        self.assertEqual(len(mail.outbox), 0)

    def test_valid_csrf_required_for_actual_review_post(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.admin)
        data = self.data()
        self.assertEqual(self.client.post(self.action, data).status_code, 403)
        data['csrfmiddlewaretoken'] = self.client.cookies['csrftoken'].value
        self.assertEqual(self.client.post(self.action, data).status_code, 302)
        self.assertEqual(AuditLog.objects.filter(action='notification_attempt_reconciled').count(), 1)

    def test_cross_site_or_other_lead_attempt_is_not_disclosed(self):
        self.client.force_login(self.admin)
        other = Site.objects.create(code='notification_other', name='Other synthetic site', default_locale='en')
        LeadSubmission.objects.filter(pk=self.lead.pk).update(site=other)
        self.assertEqual(self.client.get(self.page).status_code, 404)
        self.assertEqual(self.client.post(self.action, {'outcome': 'accepted'}).status_code, 404)

    def test_policy_disabled_attempt_is_not_presented_as_automatic_retry(self):
        self.attempt.delete()
        LeadNotificationAttempt.objects.create(submission=self.lead, kind='initial', generation=2,
            lead_version=self.lead.updated_at, state='failed', policy_disabled=True, error='未配置通知邮箱。')
        self.client.force_login(self.admin)
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '通知未配置或已停用')
        self.assertNotContains(response, 'name="outcome"')

    def test_revoked_session_after_validation_cannot_commit(self):
        self.client.force_login(self.admin)
        data = self.data()
        session_key = self.client.session.session_key
        from console.notification_forms import NotificationReviewForm
        original = NotificationReviewForm.clean

        def withdraw_session(form):
            cleaned = original(form)
            Session.objects.filter(session_key=session_key).delete()
            return cleaned

        with patch.object(NotificationReviewForm, 'clean', new=withdraw_session):
            self.assertEqual(self.client.post(self.action, data).status_code, 403)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.state, 'unknown')
        self.assertEqual(AuditLog.objects.filter(action='notification_attempt_reconciled').count(), 0)

    def test_preserve_queue_return_but_reject_external_or_wrong_path(self):
        self.client.force_login(self.admin)
        desired = f'/admin/sales/leads/{self.lead.pk}/?sort=updated&page_size=25'
        self.assertEqual(self.client.get(self.page, {'next': desired}).context['back_url'], desired)
        for bad in ('https://elsewhere.example/', f'/admin/sales/leads/{self.lead.pk}/../../system/users/'):
            self.assertEqual(self.client.get(self.page, {'next': bad}).context['back_url'], f'/admin/sales/leads/{self.lead.pk}/')
