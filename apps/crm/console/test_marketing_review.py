"""Real scoped HTTP/manual marketing receipts; provider I/O never occurs here."""
from datetime import timedelta
from io import BytesIO
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.db import DatabaseError, connection
from django.test import TestCase, override_settings
from django.utils import timezone

from console import test_public_lead_capture as capture_fixtures
from console.access import GROUP_BY_ROLE, ROLE_MARKETING_OPS
from console.models import AuditLog
from leads.models import LeadEventAttempt, LeadEventOutbox, LeadFormDefinition, LeadInboundEvent, LeadSubmission
from marketing.models import IntegrationCheck, MarketingIntegration, MarketingProvider
from sitecore.models import Site


@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True)
class MarketingReviewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        with connection.schema_editor() as editor:
            for model in (*capture_fixtures.PublicLeadCaptureVerticalTests.unmanaged_models, LeadInboundEvent, IntegrationCheck):
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
        self.lead = LeadSubmission.objects.create(site=self.site, form=self.form, team=self.team,
            full_name='合成敏感姓名不能出现在营销核对页', email='private-synthetic@example.test',
            stage='qualified', submitted_at=now - timedelta(hours=1), stage_updated_at=now,
            consent_json={'marketing': True})
        self.set_event()

    def set_event(self, provider='meta', validation=False, legacy=False, test_mode=False):
        now = timezone.now()
        integration_provider, _ = MarketingProvider.objects.get_or_create(code=provider, defaults={'name': provider.title()})
        integration = MarketingIntegration.objects.create(site=self.site, provider=integration_provider,
            name='合成核对接入', public_id=str(1000000000000 + uuid.uuid4().int % 1000000000000), enabled=True, routing_mode='legacy_site',
            integration_type='pixel' if provider == 'meta' else 'data_manager',
            config_json={'validate_only': validation, 'qualified_conversion_action_id': '111',
                         'test_event_code': 'synthetic-only' if test_mode else ''})
        self.event = LeadEventOutbox.objects.create(submission=self.lead, integration=integration,
            stage_key='qualified', event_name='qualified_lead', event_id='synthetic-complete-event-' + str(integration.pk),
            payload_json={'fixture': True, 'validateOnly': validation}, status='unknown', attempts=1,
            delivery_mode='validation' if validation else 'test' if test_mode else 'live')
        LeadEventOutbox.objects.filter(pk=self.event.pk).update(updated_at=now - timedelta(minutes=15),
            last_attempt_at=now - timedelta(minutes=15))
        self.event.refresh_from_db()
        self.attempt = None
        if not legacy:
            from leads.marketing_delivery import _payload_hash
            self.attempt = LeadEventAttempt.objects.create(outbox=self.event, generation=1, attempt_number=1,
                state='unknown', result_status='unknown', lead_version=self.lead.updated_at,
                integration_version=integration.updated_at, event_id=self.event.event_id, public_id=integration.public_id,
                payload_hash=_payload_hash(self.event), delivery_mode=self.event.delivery_mode,
                created_at=now - timedelta(minutes=20), handoff_at=now - timedelta(minutes=15),
                finished_at=now - timedelta(minutes=12), error='合成未确认响应')
        self.page = f'/admin/marketing/events/outbound/{self.event.pk}/review/'
        self.action = f'/admin/marketing/events/outbound/{self.event.pk}/reconcile/'

    def data(self, outcome='accepted', request_id=''):
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        form = response.context['review_form']
        return {'version': form['version'].value(), 'event_id': self.event.event_id,
                'outcome': outcome, 'request_id': request_id,
                'evidence': '合成平台日志与本次完整事件编号一致，已核对交接时间。',
                'confirmed': 'on', 'stopped': 'on'}

    def test_acceptance_is_manual_scoped_audited_idempotent_and_never_sends(self):
        self.client.force_login(self.admin)
        data = self.data()
        with patch('leads.services.urllib.request.urlopen') as meta, \
                patch('leads.google_data_manager._google_request') as google:
            self.assertEqual(self.client.post(self.action, data).status_code, 302)
            self.assertEqual(self.client.post(self.action, data).status_code, 302)
        meta.assert_not_called()
        google.assert_not_called()
        self.event.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual((self.event.status, self.attempt.state), ('sent', 'accepted'))
        self.assertEqual(AuditLog.objects.filter(action='marketing_attempt_reconciled').count(), 1)
        response = self.client.get(self.page)
        self.assertContains(response, '人工核对')
        self.assertNotContains(response, self.lead.email)
        self.assertNotContains(response, self.lead.full_name)

    def test_not_accepted_restores_original_sender_and_old_completion_is_ignored(self):
        from leads.marketing_delivery import dispatch_meta_now, finish_meta_attempt
        self.client.force_login(self.admin)
        with patch('leads.services.urllib.request.urlopen') as provider:
            self.assertEqual(self.client.post(self.action, self.data('not_accepted')).status_code, 302)
        provider.assert_not_called()
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'failed')
        self.assertEqual(self.event.attempts, 1)
        self.assertIsNotNone(self.event.next_attempt_at)
        with patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
                patch('leads.services.urllib.request.urlopen', return_value=BytesIO(b'{"events_received":1}')) as provider:
            self.assertTrue(dispatch_meta_now(self.event))
        self.assertEqual(provider.call_count, 1)
        self.assertFalse(finish_meta_attempt(self.attempt.pk, 'accepted'))
        self.assertEqual(LeadEventAttempt.objects.filter(outbox=self.event).count(), 2)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'sent')

    def test_google_acceptance_requires_request_id_and_means_processing_not_success(self):
        self.set_event('google')
        self.client.force_login(self.admin)
        data = self.data()
        self.assertEqual(self.client.post(self.action, data).status_code, 400)
        self.assertEqual(self.client.post(self.action, dict(data, request_id='synthetic-google-original')).status_code, 302)
        self.attempt.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual((self.attempt.state, self.event.status), ('accepted', 'processing'))
        self.assertEqual(self.event.provider_request_id, 'synthetic-google-original')
        self.assertIsNone(self.event.provider_processed_at)

    def test_validation_and_legacy_acceptance_do_not_fabricate_platform_success(self):
        self.set_event('google', validation=True)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.action, self.data()).status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'validated')
        self.set_event('meta', legacy=True)
        self.assertEqual(self.client.post(self.action, self.data()).status_code, 302)
        self.assertEqual(LeadEventAttempt.objects.filter(outbox=self.event).count(), 0)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'sent')
        self.assertEqual(AuditLog.objects.filter(action='marketing_attempt_reconciled').count(), 2)

    def test_invalid_stale_and_event_mismatch_keep_input_without_mutation(self):
        self.client.force_login(self.admin)
        data = self.data()
        response = self.client.post(self.action, dict(data, event_id='only-a-prefix'))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, data['evidence'], status_code=400)
        self.assertIn('autofocus', str(response.context['review_form']['event_id']))
        stale = self.client.post(self.action, dict(data, version='tampered'))
        self.assertEqual(stale.status_code, 409)
        self.assertContains(stale, data['evidence'], status_code=409)
        self.assertNotEqual(stale.context['review_form']['version'].value(), 'tampered')
        self.assertFalse(stale.context['review_form']['confirmed'].value())
        self.assertEqual(self.client.post(self.action, data).status_code, 302)
        self.assertEqual(self.client.post(self.action, dict(data, outcome='not_accepted')).status_code, 409)

    def test_marketing_role_is_allowed_but_sales_and_other_site_are_denied(self):
        group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_MARKETING_OPS])
        actor = get_user_model().objects.create_user(username='synthetic-marketing')
        actor.groups.add(group)
        self.client.force_login(actor)
        data = self.data()
        self.assertEqual(self.client.post(self.action, data).status_code, 302)
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.assertEqual(self.client.post(self.action, data).status_code, 403)
        other = Site.objects.create(code='marketing_review_other', name='Synthetic other', default_locale='en')
        other_form = LeadFormDefinition.objects.create(site=other, code='other-review', name='Synthetic other')
        other_lead = LeadSubmission.objects.create(site=other, form=other_form, full_name='Synthetic other',
            submitted_at=timezone.now(), stage_updated_at=timezone.now())
        other_integration = MarketingIntegration.objects.create(site=other, provider=self.event.integration.provider,
            name='Synthetic other', integration_type='pixel', enabled=True,
            public_id=str(1000000000000 + uuid.uuid4().int % 1000000000000))
        other_event = LeadEventOutbox.objects.create(submission=other_lead, integration=other_integration,
            event_id='synthetic-other', stage_key='submitted', event_name='Lead', payload_json={}, status='unknown')
        self.page = f'/admin/marketing/events/outbound/{other_event.pk}/review/'
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.page).status_code, 404)

    def test_recent_negative_handoff_is_rejected_and_audit_failure_rolls_back(self):
        self.client.force_login(self.admin)
        # SQL handoff identity is immutable: create the recent attempt rather than changing it.
        self.set_event('meta', legacy=True)
        from leads.marketing_delivery import _payload_hash
        now = timezone.now()
        self.attempt = LeadEventAttempt.objects.create(outbox=self.event, generation=1, attempt_number=1,
            state='handoff', lead_version=self.lead.updated_at, integration_version=self.event.integration.updated_at,
            event_id=self.event.event_id, public_id=self.event.integration.public_id, payload_hash=_payload_hash(self.event),
            delivery_mode='live', handoff_at=now)
        self.assertEqual(self.client.post(self.action, self.data('not_accepted')).status_code, 409)
        data = self.data()
        with patch.object(AuditLog.objects, 'create', side_effect=DatabaseError('synthetic audit failure')):
            response = self.client.post(self.action, data)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, data['evidence'], status_code=503)
        self.attempt.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual((self.attempt.state, self.event.status), ('handoff', 'unknown'))
        self.assertEqual(AuditLog.objects.filter(action='marketing_attempt_reconciled').count(), 0)

    def test_trash_and_revocation_are_not_undone_by_review(self):
        self.client.force_login(self.admin)
        LeadSubmission.objects.filter(pk=self.lead.pk).update(trashed_at=timezone.now(), consent_json={'marketing': False})
        self.assertEqual(self.client.post(self.action, self.data('not_accepted')).status_code, 302)
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.trashed_at)
        self.assertFalse(self.lead.consent_json['marketing'])
        from leads.marketing_delivery import dispatch_meta_now
        with patch('leads.services.urllib.request.urlopen') as provider:
            self.assertFalse(dispatch_meta_now(self.event))
        provider.assert_not_called()

    def test_submit_role_and_session_are_rechecked_after_form_validation(self):
        self.client.force_login(self.admin)
        data = self.data()
        from console.marketing_review_forms import MarketingReviewForm
        original = MarketingReviewForm.clean

        def revoke(form):
            cleaned = original(form)
            self.admin.groups.clear()
            return cleaned

        with patch.object(MarketingReviewForm, 'clean', revoke):
            self.assertEqual(self.client.post(self.action, data).status_code, 403)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.state, 'unknown')

    def test_deleted_session_and_changed_password_cannot_commit_cached_authority(self):
        from console.marketing_review_forms import MarketingReviewForm
        original = MarketingReviewForm.clean
        for change in ('session', 'password'):
            with self.subTest(change=change):
                self.client.force_login(self.admin)
                data = self.data()
                session_key = self.client.session.session_key

                def withdraw(form):
                    cleaned = original(form)
                    if change == 'session':
                        Session.objects.filter(session_key=session_key).delete()
                    else:
                        actor = get_user_model().objects.get(pk=self.admin.pk)
                        actor.set_password('changed-synthetic-password')
                        actor.save(update_fields=['password'])
                    return cleaned

                with patch.object(MarketingReviewForm, 'clean', withdraw):
                    self.assertEqual(self.client.post(self.action, data).status_code, 403)
                self.attempt.refresh_from_db()
                self.assertEqual(self.attempt.state, 'unknown')
        self.assertEqual(AuditLog.objects.filter(action='marketing_attempt_reconciled').count(), 0)

    def test_unknown_queue_has_a_working_review_entry_and_return_preserves_filters(self):
        self.client.force_login(self.admin)
        queue = '/admin/marketing/events/?provider=meta&status=unknown'
        response = self.client.get(queue)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.page)
        response = self.client.get(self.page, {'return_to': queue})
        self.assertEqual(response.context['back_url'], queue)
        response = self.client.get(self.page, {'return_to': '/admin/sales/leads/8/../9/'})
        self.assertEqual(response.context['back_url'], '/admin/marketing/events/')

    def test_exact_retry_rechecks_authority_before_claim_or_audit_write(self):
        from console import marketing_views
        self.client.force_login(self.admin)
        LeadEventOutbox.objects.filter(pk=self.event.pk).update(status='failed')
        original = marketing_views.outbox_retry_contract

        def revoke(outbox, *args, **kwargs):
            result = original(outbox, *args, **kwargs)
            self.admin.groups.clear()
            return result

        with patch.object(marketing_views, 'outbox_retry_contract', side_effect=revoke):
            response = self.client.post(f'/admin/api/marketing/outbox/{self.event.pk}/retry/')
        self.assertEqual(response.status_code, 403)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, 'failed')
        self.assertEqual(AuditLog.objects.filter(action='outbox_retry_claimed').count(), 0)

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False)
    def test_silent_environment_cannot_claim_an_event_for_manual_retry(self):
        self.client.force_login(self.admin)
        LeadEventOutbox.objects.filter(pk=self.event.pk).update(status='failed')
        response = self.client.post(f'/admin/api/marketing/outbox/{self.event.pk}/retry/')
        self.assertEqual(response.status_code, 409)
        self.event.refresh_from_db()
        self.assertEqual((self.event.status, self.event.attempts), ('failed', 1))
        self.assertEqual(AuditLog.objects.filter(action='outbox_retry_claimed').count(), 0)

    def test_saved_negative_result_shows_the_actual_retry_deadline(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.action, self.data('not_accepted')).status_code, 302)
        self.event.refresh_from_db()
        response = self.client.get(self.page)
        deadline = timezone.localtime(self.event.next_attempt_at).isoformat()
        self.assertContains(response, f'datetime="{deadline}"')
        self.assertContains(self.client.get('/admin/marketing/events/?status=failed'), '查看核对记录')

    def test_historical_review_does_not_label_a_new_failed_attempt_as_reviewed(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.action, self.data('not_accepted')).status_code, 302)
        from leads.marketing_delivery import _payload_hash, repair_event_projection
        latest = LeadEventAttempt.objects.create(outbox=self.event, generation=2, attempt_number=2,
            state='failed', result_status='failed', lead_version=self.lead.updated_at,
            integration_version=self.event.integration.updated_at, event_id=self.event.event_id,
            public_id=self.event.integration.public_id, payload_hash=_payload_hash(self.event), delivery_mode='live',
            finished_at=timezone.now(), error='合成新一代准备失败')
        repair_event_projection(latest)
        self.event.refresh_from_db()
        self.assertFalse(self.event.response_json.get('manual_review'))
        response = self.client.get(self.page)
        self.assertNotContains(response, '发送失败 · 人工核对')

    def test_mismatched_provider_adapter_is_not_presented_as_a_supported_review(self):
        wrong = MarketingIntegration.objects.create(site=self.site, provider=self.event.integration.provider,
            integration_type='data_manager', public_id='synthetic-wrong-adapter', name='Synthetic wrong adapter')
        event = LeadEventOutbox.objects.create(submission=self.lead, integration=wrong,
            event_id='synthetic-wrong-adapter', stage_key='qualified', event_name='qualified_lead', payload_json={}, status='unknown')
        self.client.force_login(self.admin)
        response = self.client.get(f'/admin/marketing/events/outbound/{event.pk}/review/')
        self.assertEqual(response.status_code, 404)

    def test_original_dataset_and_actual_mode_are_visible_before_review(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.page)
        self.assertContains(response, self.event.integration.public_id)
        self.assertNotContains(response, '同一数据集 → 测试事件')
        self.set_event(test_mode=True)
        response = self.client.get(self.page)
        self.assertContains(response, self.event.integration.public_id)
        self.assertContains(response, '测试事件')

    def test_google_target_uses_original_payload_not_current_account_configuration(self):
        self.set_event('google', legacy=True)
        original_id = self.event.integration.public_id
        LeadEventOutbox.objects.filter(pk=self.event.pk).update(payload_json={
            'destinations': [{'operatingAccount': {'accountId': original_id}}], 'fixture': True})
        self.event.integration.public_id = 'synthetic-changed-account'
        self.event.integration.save(update_fields=['public_id', 'updated_at'])
        self.client.force_login(self.admin)
        response = self.client.get(self.page)
        self.assertContains(response, original_id)
        self.assertNotContains(response, 'synthetic-changed-account')
