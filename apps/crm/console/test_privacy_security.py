from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from console import marketing_views, views
from console.access import GROUP_BY_ROLE, ROLE_SYSTEM_ADMIN
from console.models import AuditLog
from console.privacy_retention import execute_retention_policy, retention_preview
from console.privacy_versions import (
    privacy_request_version_token,
    retention_policy_version_token,
)
from console.privacy_workspace import build_data_governance_workspace
from leads.models import (
    Activity,
    Company,
    ConsentRecord,
    Contact,
    CrmAttachment,
    LeadConversion,
    LeadEventOutbox,
    LeadFormDefinition,
    LeadInboundEvent,
    LeadSubmission,
    Opportunity,
    PrivacyRequest,
    RetentionPolicy,
    SalesTeam,
    Task,
    WhatsAppConversation,
    WhatsAppMedia,
    WhatsAppMessage,
    WhatsAppTemplate,
)
from marketing.models import CanonicalEvent, MarketingIntegration, MarketingProvider
from sitecore.models import Cta, MediaAsset, PageRoute, Site, SiteLocale


class PrivacySecurityRegressionTests(TestCase):
    """PII deletion and its audit record are one durable operation."""

    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
        MediaAsset,
        MarketingProvider,
        MarketingIntegration,
        LeadFormDefinition,
        SalesTeam,
        LeadSubmission,
        LeadEventOutbox,
        ConsentRecord,
        PrivacyRequest,
        RetentionPolicy,
        LeadInboundEvent,
        Company,
        Contact,
        Opportunity,
        LeadConversion,
        Activity,
        Task,
        CrmAttachment,
        WhatsAppTemplate,
        WhatsAppConversation,
        WhatsAppMessage,
        WhatsAppMedia,
        AuditLog,
    )

    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        try:
            with connection.schema_editor() as editor:
                for model in cls.unmanaged_models:
                    if model._meta.db_table not in existing:
                        editor.create_model(model)
                        cls.created_models.append(model)
                        existing.add(model._meta.db_table)
            super().setUpClass()
        except Exception:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)

    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        self.admin = get_user_model().objects.create_user(
            username='privacy-admin',
            password='test-password',
        )
        self.admin.groups.add(admin_group)
        self.site = Site.objects.create(
            code='siteos_demo',
            name='SiteOS Demo',
            base_url='https://example.com',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.locale = SiteLocale.objects.create(
            site=self.site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )
        self.form = LeadFormDefinition.objects.create(
            site=self.site,
            locale=self.locale,
            code='privacy-source',
            name='Privacy source',
            category='sales',
            channel='website',
            scope_type='site',
            scope_value='*',
            status='active',
            form_schema={},
            config_json={},
        )
        self.factory = RequestFactory()

    def _lead(self, *, full_name: str, stage: str = 'new') -> LeadSubmission:
        return LeadSubmission.objects.create(
            form=self.form,
            site=self.site,
            locale=self.locale,
            stage=stage,
            full_name=full_name,
            email=f'{full_name.lower().replace(" ", ".")}@example.com',
            phone='+971500000000',
            company='Beverage Projects',
            country='AE',
            message='Need a filling line.',
            source_url='https://example.com/contact',
            referrer_url='https://example.com/',
            source_channel='website',
            source_detail='Contact page',
            client_ip='203.0.113.10',
            user_agent='Candidate test browser',
            payload_json={'product': 'filler'},
            identifiers_json={'external_id': 'subject-1'},
            utm_json={'utm_source': 'search'},
            consent_json={'marketing': True},
            follow_up_notes='Call tomorrow',
            contact_key='contact-key',
            dedupe_key='dedupe-key',
            submitted_at=self.now - timedelta(days=30),
            stage_updated_at=self.now - timedelta(days=20),
        )

    def _converted_contact(self, lead: LeadSubmission, *, full_name: str) -> Contact:
        company = Company.objects.create(
            site=self.site,
            owner_user=self.admin,
            name=f'{full_name} Holdings',
            normalized_name=f'{full_name.lower()} holdings',
            country='AE',
        )
        contact = Contact.objects.create(
            site=self.site,
            company=company,
            owner_user=self.admin,
            full_name=full_name,
            job_title='Operations Director',
            email='person@example.com',
            email_normalized='person@example.com',
            phone='+971500000000',
            phone_normalized='+971500000000',
            whatsapp_phone='+971500000000',
            country='AE',
            preferred_language='en',
            status='active',
            notes='High-value contact',
        )
        LeadConversion.objects.create(
            submission=lead,
            company=company,
            contact=contact,
            converted_by_user=self.admin,
            metadata_json={},
        )
        return contact

    def _processing_delete_request(self, lead: LeadSubmission) -> PrivacyRequest:
        return PrivacyRequest.objects.create(
            site=self.site,
            submission=lead,
            handled_by_user=self.admin,
            request_type='delete',
            status='processing',
            requester_name=lead.full_name,
            requester_email=lead.email,
            requester_phone=lead.phone,
            subject_key=f'subject-{lead.id}',
            request_json={'verification': 'ticket-123'},
            resolution='Identity verified.',
            requested_at=self.now - timedelta(days=2),
            verified_at=self.now - timedelta(days=1),
        )

    def _assert_privacy_deletion_rolled_back(
        self,
        *,
        lead: LeadSubmission,
        item: PrivacyRequest,
    ) -> None:
        lead.refresh_from_db()
        item.refresh_from_db()
        self.assertNotEqual(lead.full_name, 'Deleted data subject')
        self.assertTrue(lead.email)
        self.assertEqual(item.status, 'processing')
        self.assertTrue(item.requester_email)
        self.assertFalse(
            ConsentRecord.objects.filter(submission=lead, source='privacy_request').exists()
        )

    def test_retention_anonymizes_converted_contact_and_reports_actual_count(self):
        lead = self._lead(full_name='Retention Subject', stage='won')
        contact = self._converted_contact(lead, full_name='Retention Contact')
        unrelated_contact = Contact.objects.create(
            site=self.site,
            full_name='Unrelated Contact',
            email='unrelated@example.com',
            email_normalized='unrelated@example.com',
            phone='+971511111111',
            phone_normalized='+971511111111',
            status='active',
        )
        policy = RetentionPolicy.objects.create(
            site=self.site,
            enabled=True,
            lead_pii_retention_days=7,
            inbound_payload_retention_days=90,
            outbox_payload_retention_days=180,
            privacy_request_retention_days=365,
            updated_by_user=self.admin,
            config_json={},
        )

        result = execute_retention_policy(
            policy=policy,
            actor=self.admin,
            now=self.now,
        )

        lead.refresh_from_db()
        contact.refresh_from_db()
        unrelated_contact.refresh_from_db()
        self.assertEqual(result['lead_pii_anonymized'], 1)
        self.assertEqual(result['contacts_anonymized'], 1)
        self.assertEqual(lead.full_name, 'Deleted data subject')
        self.assertEqual(contact.full_name, 'Deleted data subject')
        self.assertEqual(contact.email, '')
        self.assertEqual(contact.phone, '')
        self.assertEqual(contact.status, 'inactive')
        self.assertEqual(unrelated_contact.full_name, 'Unrelated Contact')
        self.assertEqual(unrelated_contact.email, 'unrelated@example.com')

    def test_retention_scope_is_all_target_and_never_crosses_site(self):
        lead = self._lead(full_name='Retention Attachment Subject', stage='won')
        contact = self._converted_contact(lead, full_name='Retention Attachment Contact')
        subject_company = contact.company
        unrelated_company = Company.objects.create(
            site=self.site,
            owner_user=self.admin,
            name='Unrelated Retention Company',
            normalized_name='unrelated retention company',
        )
        other_site = Site.objects.create(
            code='retention_other',
            name='Retention Other Site',
            base_url='https://retention-other.example.com',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        other_company = Company.objects.create(
            site=other_site,
            owner_user=self.admin,
            name='Other-site Retention Company',
            normalized_name='other-site retention company',
        )
        other_contact = Contact.objects.create(
            site=other_site,
            company=other_company,
            owner_user=self.admin,
            full_name='Other-site Retention Contact',
            email='other-site@example.com',
            email_normalized='other-site@example.com',
            phone='+15550199999',
            phone_normalized='+15550199999',
            status='active',
        )
        cross_site_lead = self._lead(full_name='Cross-site Conversion Subject', stage='won')
        LeadConversion.objects.create(
            submission=cross_site_lead,
            company=other_company,
            contact=other_contact,
            converted_by_user=self.admin,
            metadata_json={},
        )
        subject_attachment = CrmAttachment.objects.create(
            site=self.site,
            submission=lead,
            company=subject_company,
            contact=contact,
            uploaded_by_user=self.admin,
            title='Subject retention file',
            original_name='subject-retention.pdf',
            storage_path='privacy/subject-retention.pdf',
            status='active',
        )
        mixed_same_site = CrmAttachment.objects.create(
            site=self.site,
            submission=lead,
            company=unrelated_company,
            uploaded_by_user=self.admin,
            title='Mixed same-site file',
            original_name='mixed-same-site.pdf',
            storage_path='privacy/mixed-same-site.pdf',
            status='active',
        )
        mixed_cross_site = CrmAttachment.objects.create(
            site=self.site,
            submission=lead,
            company=other_company,
            uploaded_by_user=self.admin,
            title='Mixed cross-site file',
            original_name='mixed-cross-site.pdf',
            storage_path='privacy/mixed-cross-site.pdf',
            status='active',
        )
        policy = RetentionPolicy.objects.create(
            site=self.site,
            enabled=True,
            lead_pii_retention_days=7,
            inbound_payload_retention_days=90,
            outbox_payload_retention_days=180,
            privacy_request_retention_days=365,
            updated_by_user=self.admin,
            config_json={},
        )

        preview = retention_preview(policy, now=self.now)
        self.assertEqual(preview['candidates']['lead_pii'], 2)
        self.assertEqual(preview['candidates']['private_attachments'], 1)
        result = execute_retention_policy(policy=policy, actor=self.admin, now=self.now)

        self.assertEqual(result['lead_pii_anonymized'], 2)
        self.assertEqual(result['contacts_anonymized'], 1)
        self.assertEqual(result['private_attachments_removed'], 1)
        subject_attachment.refresh_from_db()
        mixed_same_site.refresh_from_db()
        mixed_cross_site.refresh_from_db()
        other_contact.refresh_from_db()
        self.assertEqual(subject_attachment.status, 'archived')
        self.assertEqual(subject_attachment.storage_path, '')
        self.assertEqual(mixed_same_site.status, 'active')
        self.assertEqual(mixed_same_site.storage_path, 'privacy/mixed-same-site.pdf')
        self.assertEqual(mixed_cross_site.status, 'active')
        self.assertEqual(mixed_cross_site.storage_path, 'privacy/mixed-cross-site.pdf')
        self.assertEqual(other_contact.full_name, 'Other-site Retention Contact')
        self.assertEqual(other_contact.email, 'other-site@example.com')

    def test_retention_execution_rolls_back_when_audit_write_fails(self):
        lead = self._lead(full_name='Retention Audit Subject', stage='won')
        contact = self._converted_contact(lead, full_name='Retention Audit Contact')
        RetentionPolicy.objects.create(
            site=self.site,
            enabled=True,
            lead_pii_retention_days=7,
            inbound_payload_retention_days=90,
            outbox_payload_retention_days=180,
            privacy_request_retention_days=365,
            updated_by_user=self.admin,
            config_json={},
        )
        request = self.factory.post(
            reverse('console:retention_policy_execute'),
            {'confirm': 'APPLY RETENTION'},
        )
        request.user = self.admin

        with (
            patch.object(
                marketing_views,
                'default_site_locale',
                return_value=(self.site, self.locale),
            ),
            patch.object(
                marketing_views,
                'record_audit',
                side_effect=RuntimeError('audit unavailable'),
            ),
            self.assertRaisesRegex(RuntimeError, 'audit unavailable'),
        ):
            marketing_views.retention_policy_execute(request)

        lead.refresh_from_db()
        contact.refresh_from_db()
        self.assertEqual(lead.full_name, 'Retention Audit Subject')
        self.assertTrue(lead.email)
        self.assertEqual(contact.full_name, 'Retention Audit Contact')
        self.assertTrue(contact.email)
        self.assertFalse(
            ConsentRecord.objects.filter(
                submission=lead,
                source='retention_policy',
            ).exists()
        )

    def test_privacy_delete_retires_only_all_target_subject_attachments(self):
        lead = self._lead(full_name='Attachment Privacy Subject')
        contact = self._converted_contact(lead, full_name='Attachment Contact')
        subject_company = contact.company
        subject_team = SalesTeam.objects.create(
            site=self.site,
            code='privacy-subject-team',
            name='Privacy Subject Team',
            enabled=True,
        )
        unrelated_team = SalesTeam.objects.create(
            site=self.site,
            code='privacy-unrelated-team',
            name='Privacy Unrelated Team',
            enabled=True,
        )
        LeadSubmission.objects.filter(id=lead.id).update(team=subject_team)
        Company.objects.filter(id=subject_company.id).update(team=subject_team)
        Contact.objects.filter(id=contact.id).update(team=subject_team)
        lead.refresh_from_db()
        subject_company.refresh_from_db()
        contact.refresh_from_db()
        unrelated_company = Company.objects.create(
            site=self.site,
            owner_user=self.admin,
            team=unrelated_team,
            name='Unrelated Same-site Company',
            normalized_name='unrelated same-site company',
        )
        other_site = Site.objects.create(
            code='privacy_other',
            name='Privacy Other',
            base_url='https://other.example.com',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        cross_site_company = Company.objects.create(
            site=other_site,
            owner_user=self.admin,
            name='Cross-site Company',
            normalized_name='cross-site company',
        )
        subject_attachment = CrmAttachment.objects.create(
            site=self.site,
            submission=lead,
            company=subject_company,
            contact=contact,
            uploaded_by_user=self.admin,
            title='Subject identity document',
            original_name='subject.pdf',
            storage_path='privacy/subject.pdf',
            status='active',
        )
        mixed_team_attachment = CrmAttachment.objects.create(
            site=self.site,
            submission=lead,
            company=unrelated_company,
            uploaded_by_user=self.admin,
            title='Unrelated company document',
            original_name='mixed.pdf',
            storage_path='privacy/mixed.pdf',
            status='active',
        )
        mixed_site_attachment = CrmAttachment.objects.create(
            site=self.site,
            submission=lead,
            company=cross_site_company,
            uploaded_by_user=self.admin,
            title='Cross-site document',
            original_name='cross-site.pdf',
            storage_path='privacy/cross-site.pdf',
            status='active',
        )
        item = self._processing_delete_request(lead)
        request = self.factory.post(
            reverse('console:privacy_request_execute', args=[item.id]),
            {'confirm': 'DELETE'},
        )
        request.user = self.admin

        with (
            patch.object(
                marketing_views,
                'default_site_locale',
                return_value=(self.site, self.locale),
            ),
            patch.object(marketing_views, 'record_audit'),
        ):
            response = marketing_views.privacy_request_execute(request, item.id)

        self.assertEqual(response.status_code, 200)
        subject_attachment.refresh_from_db()
        mixed_team_attachment.refresh_from_db()
        mixed_site_attachment.refresh_from_db()
        self.assertEqual(subject_attachment.status, 'archived')
        self.assertEqual(subject_attachment.storage_path, '')
        self.assertEqual(mixed_team_attachment.status, 'active')
        self.assertEqual(mixed_team_attachment.storage_path, 'privacy/mixed.pdf')
        self.assertEqual(mixed_site_attachment.status, 'active')
        self.assertEqual(mixed_site_attachment.storage_path, 'privacy/cross-site.pdf')

    def test_api_privacy_execution_rolls_back_when_audit_write_fails(self):
        lead = self._lead(full_name='API Privacy Subject')
        item = self._processing_delete_request(lead)
        request = self.factory.post(
            reverse('console:privacy_request_execute', args=[item.id]),
            {'confirm': 'DELETE', 'locale': 'en'},
        )
        request.user = self.admin

        with (
            patch.object(marketing_views, 'default_site_locale', return_value=(self.site, self.locale)),
            patch.object(marketing_views, 'record_audit', side_effect=RuntimeError('audit unavailable')),
            self.assertRaisesRegex(RuntimeError, 'audit unavailable'),
        ):
            marketing_views.privacy_request_execute(request, item.id)

        self._assert_privacy_deletion_rolled_back(lead=lead, item=item)

    def test_api_privacy_registration_rolls_back_when_audit_write_fails(self):
        request = self.factory.post(
            reverse('console:privacy_request_create'),
            {
                'request_type': 'delete',
                'requester_name': 'Registration audit subject',
                'requester_email': 'registration-audit@example.com',
                'locale': 'en',
            },
        )
        request.user = self.admin

        with (
            patch.object(marketing_views, 'default_site_locale', return_value=(self.site, self.locale)),
            patch.object(marketing_views, 'record_audit', side_effect=RuntimeError('audit unavailable')),
            self.assertRaisesRegex(RuntimeError, 'audit unavailable'),
        ):
            marketing_views.privacy_request_create(request)

        self.assertFalse(
            PrivacyRequest.objects.filter(
                site=self.site,
                requester_email='registration-audit@example.com',
            ).exists()
        )

    def test_api_privacy_transition_rolls_back_when_audit_write_fails(self):
        lead = self._lead(full_name='Transition audit subject')
        item = PrivacyRequest.objects.create(
            site=self.site,
            submission=lead,
            request_type='export',
            status='pending',
            requester_name=lead.full_name,
            requester_email=lead.email,
            subject_key=f'transition-audit-{lead.id}',
            request_json={},
            requested_at=self.now,
        )
        request = self.factory.post(
            reverse('console:privacy_request_update', args=[item.id]),
            {'status': 'verified', 'resolution': 'Identity checked.', 'locale': 'en'},
        )
        request.user = self.admin

        with (
            patch.object(marketing_views, 'default_site_locale', return_value=(self.site, self.locale)),
            patch.object(marketing_views, 'record_audit', side_effect=RuntimeError('audit unavailable')),
            self.assertRaisesRegex(RuntimeError, 'audit unavailable'),
        ):
            marketing_views.privacy_request_update(request, item.id)

        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.assertIsNone(item.verified_at)

    def test_api_retention_policy_update_rolls_back_when_audit_write_fails(self):
        policy = RetentionPolicy.objects.create(
            site=self.site,
            enabled=False,
            lead_pii_retention_days=0,
            inbound_payload_retention_days=90,
            outbox_payload_retention_days=180,
            privacy_request_retention_days=365,
            updated_by_user=self.admin,
            config_json={},
        )
        request = self.factory.post(
            reverse('console:retention_policy_update'),
            {
                'enabled': 'on',
                'lead_pii_retention_days': '7',
                'inbound_payload_retention_days': '30',
                'outbox_payload_retention_days': '60',
                'privacy_request_retention_days': '365',
                'locale': 'en',
            },
        )
        request.user = self.admin

        with (
            patch.object(marketing_views, 'default_site_locale', return_value=(self.site, self.locale)),
            patch.object(marketing_views, 'record_audit', side_effect=RuntimeError('audit unavailable')),
            self.assertRaisesRegex(RuntimeError, 'audit unavailable'),
        ):
            marketing_views.retention_policy_update(request)

        policy.refresh_from_db()
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.lead_pii_retention_days, 0)
        self.assertEqual(policy.inbound_payload_retention_days, 90)

    def test_page_privacy_execution_rolls_back_when_audit_write_fails(self):
        lead = self._lead(full_name='Page Privacy Subject')
        item = self._processing_delete_request(lead)
        request = self.factory.post(
            reverse('console:privacy_request_manage', args=[item.id]),
            {
                '_action': 'execute',
                'confirm': 'DELETE',
                'resolution': 'Identity verified.',
                'locale': 'en',
                'version': privacy_request_version_token(item),
            },
        )
        request.user = self.admin

        with (
            patch.object(views, 'default_site_locale', return_value=(self.site, self.locale)),
            patch.object(views, 'record_audit', side_effect=RuntimeError('audit unavailable')),
            self.assertRaisesRegex(RuntimeError, 'audit unavailable'),
        ):
            views.privacy_request_manage(request, item.id)

        self._assert_privacy_deletion_rolled_back(lead=lead, item=item)

    def test_page_privacy_workflow_rejects_a_stale_version(self):
        lead = self._lead(full_name='Concurrent Privacy Subject')
        item = PrivacyRequest.objects.create(
            site=self.site,
            submission=lead,
            request_type='delete',
            status='pending',
            requester_name=lead.full_name,
            requester_email=lead.email,
            requester_phone=lead.phone,
            subject_key=f'concurrent-{lead.id}',
            request_json={'source': 'test'},
            requested_at=self.now,
        )
        stale_version = privacy_request_version_token(item)
        PrivacyRequest.objects.filter(pk=item.pk).update(resolution='Updated elsewhere')
        request = self.factory.post(
            reverse('console:privacy_request_manage', args=[item.id]),
            {
                '_action': 'transition:verified',
                'resolution': 'Identity checked.',
                'version': stale_version,
                'locale': 'en',
            },
        )
        request.user = self.admin

        with (
            patch.object(views, 'default_site_locale', return_value=(self.site, self.locale)),
            patch.object(views, 'record_audit') as audit,
        ):
            response = views.privacy_request_manage(request, item.id)

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.assertEqual(item.resolution, 'Updated elsewhere')
        self.assertContains(response, '这条隐私请求已被其他人员更新')
        audit.assert_not_called()

    def test_page_retention_policy_rejects_a_stale_version(self):
        policy = RetentionPolicy.objects.create(
            site=self.site,
            enabled=False,
            lead_pii_retention_days=0,
            inbound_payload_retention_days=90,
            outbox_payload_retention_days=180,
            privacy_request_retention_days=365,
            updated_by_user=self.admin,
            config_json={},
        )
        stale_version = retention_policy_version_token(policy)
        RetentionPolicy.objects.filter(pk=policy.pk).update(inbound_payload_retention_days=120)
        request = self.factory.post(
            reverse('console:system_data_governance'),
            {
                '_action': 'save_policy',
                'version': stale_version,
                'lead_pii_retention_days': '0',
                'inbound_payload_retention_days': '30',
                'outbox_payload_retention_days': '60',
                'privacy_request_retention_days': '365',
                'locale': 'en',
            },
        )
        request.user = self.admin

        with (
            patch.object(views, 'default_site_locale', return_value=(self.site, self.locale)),
            patch.object(views, 'record_audit') as audit,
        ):
            response = views.system_data_governance(request)

        self.assertEqual(response.status_code, 200)
        policy.refresh_from_db()
        self.assertEqual(policy.inbound_payload_retention_days, 120)
        self.assertContains(response, '数据保留策略已被其他管理员更新')
        audit.assert_not_called()

    def test_page_retention_policy_save_rolls_back_when_audit_fails(self):
        policy = RetentionPolicy.objects.create(
            site=self.site,
            enabled=False,
            lead_pii_retention_days=0,
            inbound_payload_retention_days=90,
            outbox_payload_retention_days=180,
            privacy_request_retention_days=365,
            updated_by_user=self.admin,
            config_json={},
        )
        request = self.factory.post(
            reverse('console:system_data_governance'),
            {
                '_action': 'save_policy',
                'version': retention_policy_version_token(policy),
                'enabled': 'on',
                'lead_pii_retention_days': '7',
                'inbound_payload_retention_days': '30',
                'outbox_payload_retention_days': '60',
                'privacy_request_retention_days': '365',
                'locale': 'en',
            },
        )
        request.user = self.admin

        with (
            patch.object(views, 'default_site_locale', return_value=(self.site, self.locale)),
            patch.object(views, 'record_audit', side_effect=RuntimeError('audit unavailable')),
            self.assertRaisesRegex(RuntimeError, 'audit unavailable'),
        ):
            views.system_data_governance(request)

        policy.refresh_from_db()
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.lead_pii_retention_days, 0)
        self.assertEqual(policy.inbound_payload_retention_days, 90)

    def test_data_governance_audit_evidence_is_scoped_to_the_current_site(self):
        policy = RetentionPolicy.objects.create(site=self.site)
        local_request = PrivacyRequest.objects.create(
            site=self.site,
            request_type='export',
            status='pending',
            requester_email='local@example.com',
            subject_key='local-subject',
            request_json={},
            requested_at=self.now,
        )
        other_site = Site.objects.create(
            code='other_audit_site',
            name='Other audit site',
            base_url='https://other.example.com',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        other_request = PrivacyRequest.objects.create(
            site=other_site,
            request_type='export',
            status='pending',
            requester_email='other@example.com',
            subject_key='other-subject',
            request_json={},
            requested_at=self.now,
        )
        local_audit = AuditLog.objects.create(
            actor='local-admin',
            action='privacy_request_created',
            entity_table='privacy_request',
            entity_id=local_request.id,
            metadata_json={},
        )
        AuditLog.objects.create(
            actor='other-admin',
            action='privacy_request_created',
            entity_table='privacy_request',
            entity_id=other_request.id,
            metadata_json={},
        )
        policy_audit = AuditLog.objects.create(
            actor='local-admin',
            action='privacy_retention_policy_updated',
            entity_table='privacy_retention_policy',
            entity_id=policy.id,
            metadata_json={},
        )

        workspace = build_data_governance_workspace(site=self.site)

        self.assertEqual(
            {row['id'] for row in workspace['audit_rows']},
            {local_audit.id, policy_audit.id},
        )
