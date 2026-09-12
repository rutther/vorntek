from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from console.access import (
    GROUP_BY_ROLE,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    activity_queryset_for_user,
    task_queryset_for_user,
)
from console.crm_files import store_crm_attachment
from console.marketing_services import build_privacy_export
from leads.crm_services import append_activity, create_task
from leads.models import (
    Activity,
    Company,
    ConsentRecord,
    Contact,
    CrmAttachment,
    LeadConversion,
    LeadEventOutbox,
    LeadFormDefinition,
    LeadSubmission,
    Opportunity,
    PrivacyRequest,
    SalesTeam,
    SalesTeamMember,
    Task,
)
from marketing.models import CanonicalEvent, MarketingIntegration, MarketingProvider
from sitecore.models import Cta, MediaAsset, PageRoute, Site, SiteLocale


class CrmRelationshipSecurityTests(TestCase):
    """Multi-target rows must never widen team, site, or DSAR scope."""

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
        SalesTeamMember,
        LeadSubmission,
        Company,
        Contact,
        Opportunity,
        LeadConversion,
        Activity,
        Task,
        CrmAttachment,
        PrivacyRequest,
        ConsentRecord,
        LeadEventOutbox,
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
        sales_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES])
        manager_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES_MANAGER])
        self.sales_a = get_user_model().objects.create_user('scope-sales-a', password='test')
        self.sales_b = get_user_model().objects.create_user('scope-sales-b', password='test')
        self.manager_a = get_user_model().objects.create_user('scope-manager-a', password='test')
        self.sales_a.groups.add(sales_group)
        self.manager_a.groups.add(manager_group)

        self.site_a = self._site('siteos_demo')
        self.site_other = self._site('siteos_other')
        self.locale_a = self._locale(self.site_a, 'en')
        self.locale_other = self._locale(self.site_other, 'en')
        self.form_a = self._form(self.site_a, self.locale_a, 'scope-a')
        self.form_other = self._form(self.site_other, self.locale_other, 'scope-other')

        self.team_a = SalesTeam.objects.create(
            site=self.site_a,
            code='team-a',
            name='Team A',
            enabled=True,
        )
        self.team_b = SalesTeam.objects.create(
            site=self.site_a,
            code='team-b',
            name='Team B',
            enabled=True,
        )
        self.team_other = SalesTeam.objects.create(
            site=self.site_other,
            code='team-other',
            name='Other Site Team',
            enabled=True,
        )
        SalesTeamMember.objects.create(
            team=self.team_a,
            user=self.sales_a,
            membership_role='member',
        )
        SalesTeamMember.objects.create(
            team=self.team_a,
            user=self.manager_a,
            membership_role='manager',
        )
        SalesTeamMember.objects.create(
            team=self.team_b,
            user=self.sales_b,
            membership_role='member',
        )

        self.lead_a = self._lead(
            site=self.site_a,
            locale=self.locale_a,
            form=self.form_a,
            team=self.team_a,
            owner=self.sales_a,
            name='Subject A',
        )
        self.lead_b = self._lead(
            site=self.site_a,
            locale=self.locale_a,
            form=self.form_a,
            team=self.team_b,
            owner=self.sales_b,
            name='Subject B',
        )
        self.lead_other = self._lead(
            site=self.site_other,
            locale=self.locale_other,
            form=self.form_other,
            team=self.team_other,
            owner=self.sales_a,
            name='Other Site Subject',
        )

        self.company_a = self._company(
            site=self.site_a,
            team=self.team_a,
            owner=self.sales_a,
            name='Company A',
        )
        self.contact_a = Contact.objects.create(
            site=self.site_a,
            company=self.company_a,
            owner_user=self.sales_a,
            team=self.team_a,
            full_name='Contact A',
        )
        self.opportunity_a = Opportunity.objects.create(
            site=self.site_a,
            company=self.company_a,
            primary_contact=self.contact_a,
            source_submission=self.lead_a,
            owner_user=self.sales_a,
            team=self.team_a,
            name='Opportunity A',
        )
        LeadConversion.objects.create(
            submission=self.lead_a,
            company=self.company_a,
            contact=self.contact_a,
            opportunity=self.opportunity_a,
            converted_by_user=self.sales_a,
            metadata_json={},
        )

        self.company_b = self._company(
            site=self.site_a,
            team=self.team_b,
            owner=self.sales_b,
            name='Company B',
        )
        self.company_other = self._company(
            site=self.site_other,
            team=self.team_other,
            owner=self.sales_a,
            name='Other Site Company',
        )

    def _site(self, code: str) -> Site:
        return Site.objects.create(
            code=code,
            name=code,
            base_url=f'https://{code}.example',
            default_locale='en',
            enabled=True,
            config_json={},
        )

    def _locale(self, site: Site, code: str) -> SiteLocale:
        return SiteLocale.objects.create(
            site=site,
            locale_code=code,
            label=code,
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )

    def _form(self, site: Site, locale: SiteLocale, code: str) -> LeadFormDefinition:
        return LeadFormDefinition.objects.create(
            site=site,
            locale=locale,
            code=code,
            name=code,
            status='active',
            form_schema={},
            config_json={},
        )

    def _lead(self, *, site, locale, form, team, owner, name) -> LeadSubmission:
        return LeadSubmission.objects.create(
            form=form,
            site=site,
            locale=locale,
            team=team,
            assignee=owner,
            full_name=name,
            email=f'{name.casefold().replace(" ", ".")}@example.com',
            submitted_at=self.now - timedelta(days=1),
            stage_updated_at=self.now - timedelta(days=1),
        )

    def _company(self, *, site, team, owner, name) -> Company:
        return Company.objects.create(
            site=site,
            team=team,
            owner_user=owner,
            name=name,
            normalized_name=name.casefold(),
        )

    def _safe_targets(self) -> dict:
        return {
            'submission': self.lead_a,
            'company': self.company_a,
            'contact': self.contact_a,
            'opportunity': self.opportunity_a,
        }

    def test_creation_paths_reject_cross_team_cross_site_and_inconsistent_links(self):
        with self.assertRaisesMessage(ValidationError, '不同销售团队'):
            append_activity(
                site=self.site_a,
                actor=self.sales_a,
                activity_type='note',
                submission=self.lead_a,
                company=self.company_b,
            )
        self.assertFalse(Activity.objects.exists())

        with self.assertRaisesMessage(ValidationError, '不属于当前站点'):
            create_task(
                site=self.site_a,
                owner_user=self.sales_a,
                team=self.team_a,
                title='Cross-site task',
                due_at=self.now + timedelta(days=1),
                submission=self.lead_a,
                company=self.company_other,
            )
        self.assertFalse(Task.objects.exists())

        other_contact = Contact.objects.create(
            site=self.site_a,
            company=self.company_a,
            owner_user=self.sales_a,
            team=self.team_a,
            full_name='Secondary Contact',
        )
        secondary_activity = append_activity(
            site=self.site_a,
            actor=self.sales_a,
            activity_type='note',
            contact=other_contact,
            opportunity=self.opportunity_a,
        )
        self.assertEqual(secondary_activity.contact_id, other_contact.id)

        unrelated_same_team_company = self._company(
            site=self.site_a,
            team=self.team_a,
            owner=self.sales_a,
            name='Unrelated Team A Company',
        )
        unrelated_contact = Contact.objects.create(
            site=self.site_a,
            company=unrelated_same_team_company,
            owner_user=self.sales_a,
            team=self.team_a,
            full_name='Unrelated Team A Contact',
        )
        with self.assertRaisesMessage(ValidationError, '同一企业'):
            append_activity(
                site=self.site_a,
                actor=self.sales_a,
                activity_type='note',
                contact=unrelated_contact,
                opportunity=self.opportunity_a,
            )

        with patch('console.crm_files.attachment_storage_root') as storage_root:
            with self.assertRaisesMessage(ValidationError, '不同销售团队'):
                store_crm_attachment(
                    site=self.site_a,
                    uploaded_file=SimpleUploadedFile('scope.pdf', b'%PDF-test'),
                    title='Cross-team file',
                    uploaded_by=self.sales_a,
                    target={'submission': self.lead_a, 'company': self.company_b},
                )
            storage_root.assert_not_called()
        self.assertFalse(CrmAttachment.objects.exists())

    def test_same_team_multi_target_paths_remain_supported(self):
        activity = append_activity(
            site=self.site_a,
            actor=self.sales_a,
            activity_type='meeting',
            subject='Qualified project review',
            **self._safe_targets(),
        )
        task = create_task(
            site=self.site_a,
            owner_user=self.sales_a,
            team=self.team_a,
            created_by_user=self.sales_a,
            title='Send proposal',
            due_at=self.now + timedelta(days=1),
            **self._safe_targets(),
        )
        derived_team_task = create_task(
            site=self.site_a,
            owner_user=self.sales_a,
            title='Team is derived from target',
            due_at=self.now + timedelta(days=2),
            submission=self.lead_a,
        )
        storage_root = Path(settings.BASE_DIR) / '.runtime' / f'crm-rel-test-{self.lead_a.id}'
        storage_root.mkdir(parents=True, exist_ok=True)
        with patch('console.crm_files.attachment_storage_root', return_value=storage_root):
            attachment = store_crm_attachment(
                site=self.site_a,
                uploaded_file=SimpleUploadedFile('proposal.pdf', b'%PDF-proposal'),
                title='Proposal',
                uploaded_by=self.sales_a,
                target=self._safe_targets(),
            )
            stored_path = storage_root / attachment.storage_path
            self.assertTrue(stored_path.is_file())
            stored_path.unlink()
            stored_path.parent.rmdir()
            stored_path.parent.parent.rmdir()
            storage_root.rmdir()

        self.assertEqual(activity.submission_id, self.lead_a.id)
        self.assertEqual(task.team_id, self.team_a.id)
        self.assertEqual(derived_team_task.team_id, self.team_a.id)
        self.assertEqual(attachment.opportunity_id, self.opportunity_a.id)

    def test_read_scopes_and_record_detail_hide_legacy_mixed_rows(self):
        safe_activity = Activity.objects.create(
            site=self.site_a,
            actor_user=self.sales_a,
            activity_type='note',
            subject='Safe activity',
            occurred_at=self.now,
            **self._safe_targets(),
        )
        mixed_activity = Activity.objects.create(
            site=self.site_a,
            submission=self.lead_a,
            company=self.company_b,
            actor_user=self.sales_a,
            activity_type='note',
            subject='Mixed activity',
            occurred_at=self.now,
        )
        cross_site_activity = Activity.objects.create(
            site=self.site_a,
            submission=self.lead_a,
            company=self.company_other,
            actor_user=self.sales_a,
            activity_type='note',
            subject='Cross-site activity',
            occurred_at=self.now,
        )
        unlinked_activity = Activity.objects.create(
            site=self.site_a,
            actor_user=self.sales_a,
            activity_type='note',
            subject='Invalid unlinked activity',
            occurred_at=self.now,
        )
        safe_task = Task.objects.create(
            site=self.site_a,
            owner_user=self.sales_a,
            team=self.team_a,
            title='Safe task',
            due_at=self.now + timedelta(days=1),
            **self._safe_targets(),
        )
        mixed_task = Task.objects.create(
            site=self.site_a,
            submission=self.lead_a,
            company=self.company_b,
            owner_user=self.sales_a,
            team=self.team_a,
            title='Mixed task',
            due_at=self.now + timedelta(days=1),
        )
        unlinked_task = Task.objects.create(
            site=self.site_a,
            owner_user=self.sales_a,
            team=self.team_a,
            title='Invalid unlinked task',
            due_at=self.now + timedelta(days=1),
        )
        safe_attachment = CrmAttachment.objects.create(
            site=self.site_a,
            uploaded_by_user=self.sales_a,
            title='Safe attachment',
            original_name='safe.pdf',
            status='active',
            **self._safe_targets(),
        )
        mixed_attachment = CrmAttachment.objects.create(
            site=self.site_a,
            submission=self.lead_a,
            company=self.company_b,
            uploaded_by_user=self.sales_a,
            title='Mixed attachment',
            original_name='mixed.pdf',
            status='active',
        )
        unlinked_attachment = CrmAttachment.objects.create(
            site=self.site_a,
            uploaded_by_user=self.sales_a,
            title='Invalid unlinked attachment',
            original_name='unlinked.pdf',
            status='active',
        )

        for user in (self.sales_a, self.manager_a):
            with self.subTest(user=user.username):
                self.assertEqual(
                    set(
                        activity_queryset_for_user(
                            Activity.objects.filter(site=self.site_a),
                            user,
                        ).values_list('id', flat=True)
                    ),
                    {safe_activity.id},
                )
                self.assertEqual(
                    set(
                        task_queryset_for_user(
                            Task.objects.filter(site=self.site_a),
                            user,
                        ).values_list('id', flat=True)
                    ),
                    {safe_task.id},
                )

        self.client.force_login(self.sales_a)
        response = self.client.get(
            reverse('console:crm_record_detail', args=['lead', self.lead_a.id]),
            {'locale': 'en'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual({item['id'] for item in payload['activities']}, {safe_activity.id})
        self.assertEqual({item['id'] for item in payload['tasks']}, {safe_task.id})
        self.assertEqual(
            {item['id'] for item in payload['attachments']},
            {safe_attachment.id},
        )
        self.assertNotIn(mixed_activity.id, {item['id'] for item in payload['activities']})
        self.assertNotIn(cross_site_activity.id, {item['id'] for item in payload['activities']})
        self.assertNotIn(unlinked_activity.id, {item['id'] for item in payload['activities']})
        self.assertNotIn(mixed_task.id, {item['id'] for item in payload['tasks']})
        self.assertNotIn(unlinked_task.id, {item['id'] for item in payload['tasks']})
        self.assertNotIn(mixed_attachment.id, {item['id'] for item in payload['attachments']})
        self.assertNotIn(
            unlinked_attachment.id,
            {item['id'] for item in payload['attachments']},
        )

    def test_dsar_export_requires_every_target_to_belong_to_the_subject(self):
        good_activity = Activity.objects.create(
            site=self.site_a,
            actor_user=self.sales_a,
            activity_type='note',
            subject='Subject activity',
            occurred_at=self.now,
            **self._safe_targets(),
        )
        good_task = Task.objects.create(
            site=self.site_a,
            owner_user=self.sales_a,
            team=self.team_a,
            title='Subject task',
            due_at=self.now + timedelta(days=1),
            **self._safe_targets(),
        )
        good_attachment = CrmAttachment.objects.create(
            site=self.site_a,
            uploaded_by_user=self.sales_a,
            title='Subject file',
            original_name='subject.pdf',
            status='active',
            **self._safe_targets(),
        )
        mixed_activity = Activity.objects.create(
            site=self.site_a,
            submission=self.lead_a,
            company=self.company_b,
            actor_user=self.sales_a,
            activity_type='note',
            subject='Unrelated company metadata',
            occurred_at=self.now,
        )
        mixed_task = Task.objects.create(
            site=self.site_a,
            submission=self.lead_a,
            company=self.company_b,
            owner_user=self.sales_a,
            team=self.team_a,
            title='Unrelated company task',
            due_at=self.now + timedelta(days=1),
        )
        cross_site_attachment = CrmAttachment.objects.create(
            site=self.site_a,
            submission=self.lead_a,
            company=self.company_other,
            uploaded_by_user=self.sales_a,
            title='Other site metadata',
            original_name='other-site.pdf',
            status='active',
        )
        privacy_request = PrivacyRequest.objects.create(
            site=self.site_a,
            submission=self.lead_a,
            handled_by_user=self.manager_a,
            request_type='export',
            status='verified',
            requester_email=self.lead_a.email,
            verified_at=self.now,
        )

        payload = build_privacy_export(privacy_request)

        self.assertEqual({item['id'] for item in payload['activities']}, {good_activity.id})
        self.assertEqual({item['id'] for item in payload['tasks']}, {good_task.id})
        self.assertEqual(
            {item['id'] for item in payload['attachments']},
            {good_attachment.id},
        )
        self.assertNotIn(mixed_activity.id, {item['id'] for item in payload['activities']})
        self.assertNotIn(mixed_task.id, {item['id'] for item in payload['tasks']})
        self.assertNotIn(
            cross_site_attachment.id,
            {item['id'] for item in payload['attachments']},
        )
