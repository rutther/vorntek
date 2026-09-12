from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import DatabaseError, connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from console.access import (
    GROUP_BY_ROLE,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
)
from console.account_versions import account_version_token
from console.account_workspace_v2 import (
    build_company_list_workspace_v2,
    build_contact_list_workspace_v2,
)
from console.models import AuditLog
from console.navigation import build_navigation
from leads.models import (
    Activity,
    Company,
    Contact,
    CrmAttachment,
    LeadConversion,
    LeadFormDefinition,
    LeadSubmission,
    Opportunity,
    SalesTeam,
    SalesTeamMember,
    Task,
)
from marketing.models import CanonicalEvent
from sitecore.models import Cta, MediaAsset, PageRoute, Site, SiteLocale


class AccountViewsV2Tests(TestCase):
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
        MediaAsset,
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
        groups = {
            role: Group.objects.create(name=GROUP_BY_ROLE[role])
            for role in (
                ROLE_SALES,
                ROLE_SALES_MANAGER,
                ROLE_MARKETING_OPS,
                ROLE_SYSTEM_ADMIN,
            )
        }
        User = get_user_model()
        self.sales = User.objects.create_user(username='account-sales', password='test-password')
        self.teammate = User.objects.create_user(username='account-teammate', password='test-password')
        self.other_sales = User.objects.create_user(username='account-other', password='test-password')
        self.manager = User.objects.create_user(username='account-manager', password='test-password')
        self.marketing = User.objects.create_user(username='account-marketing', password='test-password')
        self.admin = User.objects.create_user(username='account-admin', password='test-password')
        self.sales.groups.add(groups[ROLE_SALES])
        self.teammate.groups.add(groups[ROLE_SALES])
        self.other_sales.groups.add(groups[ROLE_SALES])
        self.manager.groups.add(groups[ROLE_SALES_MANAGER])
        self.marketing.groups.add(groups[ROLE_MARKETING_OPS])
        self.admin.groups.add(groups[ROLE_SYSTEM_ADMIN])

        self.site = Site.objects.create(
            code='siteos_demo',
            name='SiteOS Demo',
            base_url='https://example.com',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        SiteLocale.objects.create(
            site=self.site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )
        self.other_site = Site.objects.create(
            code='other-site',
            name='Other Site',
            base_url='https://other.example.com',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.team_a = SalesTeam.objects.create(
            site=self.site, code='team-a', name='Team A', enabled=True
        )
        self.team_b = SalesTeam.objects.create(
            site=self.site, code='team-b', name='Team B', enabled=True
        )
        self.other_site_team = SalesTeam.objects.create(
            site=self.other_site, code='other-team', name='Other Team', enabled=True
        )
        SalesTeamMember.objects.create(team=self.team_a, user=self.sales, membership_role='member')
        SalesTeamMember.objects.create(team=self.team_a, user=self.teammate, membership_role='member')
        SalesTeamMember.objects.create(team=self.team_a, user=self.manager, membership_role='manager')
        SalesTeamMember.objects.create(team=self.team_b, user=self.other_sales, membership_role='member')

        self.company_a = self._company(
            name='Alpha Bottling', owner=self.sales, team=self.team_a, country='AE', city='Dubai'
        )
        self.company_b = self._company(
            name='Beta Beverage', owner=self.other_sales, team=self.team_b, country='SA'
        )
        self.other_site_company = Company.objects.create(
            site=self.other_site,
            owner_user=self.sales,
            team=self.other_site_team,
            name='Other Site Account',
            normalized_name='other site account',
        )
        self.contact_a = self._contact(
            name='Amina Hassan', company=self.company_a, owner=self.sales, team=self.team_a
        )
        self.contact_b = self._contact(
            name='Bader Saleh', company=self.company_b, owner=self.other_sales, team=self.team_b
        )
        self.opportunity_a = Opportunity.objects.create(
            site=self.site,
            company=self.company_a,
            primary_contact=self.contact_a,
            owner_user=self.sales,
            team=self.team_a,
            name='Alpha aseptic line',
            stage='discovery',
            value_amount=Decimal('350000.00'),
            currency='USD',
            probability=30,
        )

    def _company(self, *, name, owner, team, country='', city=''):
        return Company.objects.create(
            site=self.site,
            owner_user=owner,
            team=team,
            name=name,
            normalized_name=' '.join(name.casefold().split()),
            industry='Beverage',
            country=country,
            city=city,
        )

    def _contact(self, *, name, company, owner, team):
        return Contact.objects.create(
            site=self.site,
            company=company,
            owner_user=owner,
            team=team,
            full_name=name,
            job_title='Project Director',
            email=f'{name.split()[0].lower()}@example.com',
            email_normalized=f'{name.split()[0].lower()}@example.com',
            phone='+971 50 123 4567',
            phone_normalized='+971501234567',
            whatsapp_phone='+971 50 123 4567',
            country='AE',
            preferred_language='en',
        )

    @staticmethod
    def _canonical(**overrides):
        values = {'sort': 'name', 'page': '1', 'page_size': '25'}
        values.update(overrides)
        return values

    def _company_payload(self, company, *, version=None, include_owner=True, name=None):
        payload = {
            'name': name or company.name,
            'website': company.website,
            'industry': company.industry,
            'country': company.country,
            'city': company.city,
            'status': company.status,
            'notes': company.notes,
        }
        if version is not None:
            payload['version'] = version
        if include_owner:
            payload['owner_user'] = str(company.owner_user_id or '')
        return payload

    def _contact_payload(
        self, contact, *, version=None, include_owner=True, include_company=True, name=None
    ):
        payload = {
            'full_name': name or contact.full_name,
            'job_title': contact.job_title,
            'email': contact.email,
            'phone': contact.phone,
            'whatsapp_phone': contact.whatsapp_phone,
            'country': contact.country,
            'preferred_language': contact.preferred_language,
            'status': contact.status,
            'notes': contact.notes,
        }
        if version is not None:
            payload['version'] = version
        if include_owner:
            payload['owner_user'] = str(contact.owner_user_id or '')
        if include_company:
            payload['company'] = str(contact.company_id or '')
        return payload

    def test_sales_manager_and_marketing_scopes_are_fail_closed_with_pii(self):
        self.client.force_login(self.sales)
        response = self.client.get(reverse('console:company_workspace_v2'), self._canonical())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Alpha Bottling')
        self.assertNotContains(response, 'Beta Beverage')

        response = self.client.get(reverse('console:contact_workspace_v2'), self._canonical())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'amina@example.com')
        self.assertNotContains(response, 'bader@example.com')
        response = self.client.get(
            reverse('console:contact_workspace_detail_v2', args=[self.contact_b.pk]),
            self._canonical(),
        )
        self.assertEqual(response.status_code, 404)

        self.client.force_login(self.manager)
        response = self.client.get(reverse('console:company_workspace_v2'), self._canonical())
        self.assertContains(response, 'Alpha Bottling')
        self.assertNotContains(response, 'Beta Beverage')

        self.client.force_login(self.marketing)
        response = self.client.get(reverse('console:contact_workspace_v2'), self._canonical())
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, 'amina@example.com', status_code=403)

    def test_contact_with_cross_site_company_is_hidden_from_list_and_detail(self):
        corrupt_contact = self._contact(
            name='Cross Site Linked Contact',
            company=self.other_site_company,
            owner=self.sales,
            team=self.team_a,
        )

        for user in (self.sales, self.admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse('console:contact_workspace_v2'), self._canonical()
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, corrupt_contact.full_name)
                self.assertNotContains(response, self.other_site_company.name)
                detail = self.client.get(
                    reverse(
                        'console:contact_workspace_detail_v2',
                        args=[corrupt_contact.pk],
                    ),
                    self._canonical(),
                )
                self.assertEqual(detail.status_code, 404)

    def test_unknown_invalid_and_external_state_redirects_to_canonical_urls(self):
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:company_workspace_v2'),
            {
                'q': '  Alpha   Bottling ',
                'status': 'invalid',
                'owner': '999999',
                'sort': 'danger',
                'page': '-8',
                'page_size': '5000',
                'next': 'https://evil.example/',
            },
        )
        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response['Location'])
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, reverse('console:company_workspace_v2'))
        self.assertEqual(params, {
            'q': ['Alpha Bottling'], 'sort': ['name'], 'page': ['1'], 'page_size': ['25']
        })

        response = self.client.get(
            reverse('console:contact_workspace_detail_v2', args=[self.contact_a.pk]),
            {**self._canonical(), 'company': str(self.company_b.pk), 'debug': '1'},
        )
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlsplit(response['Location']).query)
        self.assertNotIn('company', params)
        self.assertNotIn('debug', params)

    def test_initial_filtered_and_database_error_states_are_distinct(self):
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:company_workspace_v2'), self._canonical(q='no-such-company')
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '没有匹配的企业')
        self.company_a.delete()
        response = self.client.get(reverse('console:company_workspace_v2'), self._canonical())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '还没有企业')

        with patch(
            'console.account_views_v2.build_company_list_workspace_v2',
            side_effect=DatabaseError('database unavailable'),
        ):
            response = self.client.get(reverse('console:company_workspace_v2'), self._canonical())
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, '客户关系数据暂时不可用', status_code=503)

    def test_company_and_contact_list_query_counts_stay_fixed_for_fifty_rows(self):
        for index in range(55):
            company = self._company(
                name=f'Paged Account {index:02d}', owner=self.sales, team=self.team_a
            )
            self._contact(
                name=f'Paged Contact {index:02d}', company=company, owner=self.sales, team=self.team_a
            )
        request = RequestFactory().get(
            reverse('console:company_workspace_v2'), self._canonical(page_size='50')
        )
        request.user = self.manager
        setattr(self.manager, '_console_request_role_keys', frozenset({ROLE_SALES_MANAGER}))
        with CaptureQueriesContext(connection) as captured:
            workspace = build_company_list_workspace_v2(request=request, site=self.site)
        self.assertEqual(len(workspace['rows']), 50)
        self.assertLessEqual(len(captured), 12, [query['sql'] for query in captured])

        request = RequestFactory().get(
            reverse('console:contact_workspace_v2'), self._canonical(page_size='50')
        )
        request.user = self.manager
        with CaptureQueriesContext(connection) as captured:
            workspace = build_contact_list_workspace_v2(request=request, site=self.site)
        self.assertEqual(len(workspace['rows']), 50)
        self.assertNotIn('company_choices', workspace)
        self.assertLessEqual(len(captured), 13, [query['sql'] for query in captured])

    def test_company_lookup_is_exact_scope_and_bounded_without_html_directory(self):
        for index in range(25):
            self._company(
                name=f'Searchable Account {index:02d}', owner=self.sales, team=self.team_a
            )
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:contact_company_options_v2', args=[self.contact_a.pk]),
            {'q': 'Searchable'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload['items']), 20)
        self.assertTrue(payload['has_more'])
        self.assertNotIn('Beta Beverage', {item['name'] for item in payload['items']})

        response = self.client.get(
            reverse('console:contact_workspace_detail_v2', args=[self.contact_a.pk]),
            self._canonical(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Searchable Account 24')
        self.assertContains(response, '结果最多显示 20 条')

    def test_detail_routes_restore_context_render_relationships_and_one_modal(self):
        Activity.objects.create(
            site=self.site,
            company=self.company_a,
            contact=self.contact_a,
            opportunity=self.opportunity_a,
            actor_user=self.sales,
            activity_type='call',
            direction='outbound',
            subject='Confirmed technical scope',
            occurred_at=self.now,
        )
        Task.objects.create(
            site=self.site,
            company=self.company_a,
            contact=self.contact_a,
            opportunity=self.opportunity_a,
            owner_user=self.sales,
            team=self.team_a,
            created_by_user=self.sales,
            title='Send revised layout',
            due_at=self.now + timedelta(days=1),
        )
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:company_workspace_detail_v2', args=[self.company_a.pk]),
            self._canonical(q='Alpha'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confirmed technical scope')
        self.assertContains(response, 'Send revised layout')
        self.assertContains(response, 'Alpha aseptic line')
        self.assertContains(response, 'data-nc-account-form')
        self.assertEqual(response.content.count(b'data-nc-account-modal>'), 1)
        self.assertIn('q=Alpha', response.context['workspace']['back_url'])
        self.assertContains(response, 'name="version"')

        response = self.client.get(
            reverse('console:contact_workspace_detail_v2', args=[self.contact_a.pk]),
            self._canonical(),
        )
        self.assertContains(response, '只复制，不自动发起外部消息')
        self.assertContains(response, 'data-nc-company-search')
        self.assertEqual(response.content.count(b'<h1'), 1)

    def test_stale_tokens_return_409_without_activity_or_audit_side_effects(self):
        self.client.force_login(self.sales)
        stale = account_version_token(self.company_a.updated_at)
        Company.objects.filter(pk=self.company_a.pk).update(
            name='Server newer account',
            updated_at=self.company_a.updated_at + timedelta(seconds=1),
        )
        self.company_a.refresh_from_db()
        activity_count = Activity.objects.filter(company=self.company_a).count()
        audit_count = AuditLog.objects.count()
        response = self.client.post(
            reverse('console:company_update', args=[self.company_a.pk]),
            self._company_payload(self.company_a, version=stale, name='Stale overwrite'),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'company_version_conflict')
        self.company_a.refresh_from_db()
        self.assertEqual(self.company_a.name, 'Server newer account')
        self.assertEqual(Activity.objects.filter(company=self.company_a).count(), activity_count)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_audit_failure_rolls_back_company_contact_and_activity(self):
        self.client.force_login(self.sales)
        company_version = account_version_token(self.company_a.updated_at)
        before_activities = Activity.objects.count()
        with patch('console.sales_views.record_audit', side_effect=RuntimeError('audit down')):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse('console:company_update', args=[self.company_a.pk]),
                    self._company_payload(
                        self.company_a, version=company_version, name='Must roll back account'
                    ),
                )
        self.company_a.refresh_from_db()
        self.assertEqual(self.company_a.name, 'Alpha Bottling')
        self.assertEqual(Activity.objects.count(), before_activities)

        contact_version = account_version_token(self.contact_a.updated_at)
        with patch('console.sales_views.record_audit', side_effect=RuntimeError('audit down')):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse('console:contact_update', args=[self.contact_a.pk]),
                    self._contact_payload(
                        self.contact_a, version=contact_version, name='Must roll back contact'
                    ),
                )
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.full_name, 'Amina Hassan')
        self.assertEqual(Activity.objects.count(), before_activities)

    def test_missing_version_is_409_but_legacy_fields_remain_compatible_with_current_version(self):
        self.client.force_login(self.sales)
        missing_version = self.client.post(
            reverse('console:company_update', args=[self.company_a.pk]),
            self._company_payload(
                self.company_a, include_owner=False, name='Unsafe unversioned edit'
            ),
        )
        self.assertEqual(missing_version.status_code, 409)
        self.company_a.refresh_from_db()
        self.assertEqual(self.company_a.name, 'Alpha Bottling')

        missing_contact_version = self.client.post(
            reverse('console:contact_update', args=[self.contact_a.pk]),
            self._contact_payload(
                self.contact_a,
                include_owner=False,
                include_company=False,
                name='Unsafe unversioned contact edit',
            ),
        )
        self.assertEqual(missing_contact_version.status_code, 409)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.full_name, 'Amina Hassan')

        legacy_payload = self.client.get(
            reverse('console:crm_record_detail', args=['company', self.company_a.pk])
        )
        self.assertEqual(legacy_payload.status_code, 200)
        self.assertEqual(
            legacy_payload.json()['record']['version'],
            account_version_token(self.company_a.updated_at),
        )

        response = self.client.post(
            reverse('console:company_update', args=[self.company_a.pk]),
            self._company_payload(
                self.company_a,
                version=account_version_token(self.company_a.updated_at),
                include_owner=False,
                name='Legacy Account Edit',
            ),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.company_a.refresh_from_db()
        self.assertEqual(self.company_a.name, 'Legacy Account Edit')
        self.assertEqual(self.company_a.owner_user_id, self.sales.pk)

        response = self.client.post(
            reverse('console:contact_update', args=[self.contact_a.pk]),
            self._contact_payload(
                self.contact_a,
                version=account_version_token(self.contact_a.updated_at),
                include_owner=False,
                include_company=False,
                name='Legacy Contact Edit',
            ),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.full_name, 'Legacy Contact Edit')
        self.assertEqual(self.contact_a.owner_user_id, self.sales.pk)
        self.assertEqual(self.contact_a.company_id, self.company_a.pk)

    def test_inactive_history_is_preserved_and_mixed_scope_relationships_are_rejected(self):
        inactive = get_user_model().objects.create_user(
            username='historical-account-owner', password='test-password', is_active=False
        )
        SalesTeamMember.objects.create(team=self.team_a, user=inactive, membership_role='member')
        historical = self._contact(
            name='Historical Contact', company=self.company_a, owner=inactive, team=self.team_a
        )
        self.client.force_login(self.manager)
        detail = self.client.get(
            reverse('console:contact_workspace_detail_v2', args=[historical.pk]), self._canonical()
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '已停用，仅保留')
        response = self.client.post(
            reverse('console:contact_update', args=[historical.pk]),
            self._contact_payload(
                historical,
                version=account_version_token(historical.updated_at),
                name='Historical Contact Revised',
            ),
        )
        self.assertEqual(response.status_code, 200, response.content)
        historical.refresh_from_db()
        self.assertEqual(historical.owner_user_id, inactive.pk)

        before_activity = Activity.objects.count()
        response = self.client.post(
            reverse('console:contact_update', args=[self.contact_a.pk]),
            {
                **self._contact_payload(
                    self.contact_a, version=account_version_token(self.contact_a.updated_at)
                ),
                'company': str(self.company_b.pk),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.company_id, self.company_a.pk)
        self.assertEqual(Activity.objects.count(), before_activity)

        response = self.client.post(
            reverse('console:company_update', args=[self.company_a.pk]),
            {
                **self._company_payload(
                    self.company_a, version=account_version_token(self.company_a.updated_at)
                ),
                'owner_user': str(self.other_sales.pk),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.company_a.refresh_from_db()
        self.assertEqual(self.company_a.owner_user_id, self.sales.pk)

    def test_same_team_owner_changes_cannot_desynchronize_direct_relationships(self):
        self.client.force_login(self.manager)
        before_activity = Activity.objects.count()

        response = self.client.post(
            reverse('console:company_update', args=[self.company_a.pk]),
            {
                **self._company_payload(
                    self.company_a,
                    version=account_version_token(self.company_a.updated_at),
                ),
                'owner_user': str(self.teammate.pk),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('受控账户重分配', response.json()['errors']['owner_user'][0])
        self.company_a.refresh_from_db()
        self.assertEqual(self.company_a.owner_user_id, self.sales.pk)

        response = self.client.post(
            reverse('console:contact_update', args=[self.contact_a.pk]),
            {
                **self._contact_payload(
                    self.contact_a,
                    version=account_version_token(self.contact_a.updated_at),
                ),
                'owner_user': str(self.teammate.pk),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('受控账户重分配', response.json()['errors']['owner_user'][0])
        self.contact_a.refresh_from_db()
        self.assertEqual(self.contact_a.owner_user_id, self.sales.pk)
        self.assertEqual(Activity.objects.count(), before_activity)

        teammate_company = self._company(
            name='Teammate Account', owner=self.teammate, team=self.team_a
        )
        unlinked_contact = self._contact(
            name='Movable Contact', company=self.company_a, owner=self.sales, team=self.team_a
        )
        response = self.client.post(
            reverse('console:contact_update', args=[unlinked_contact.pk]),
            {
                **self._contact_payload(
                    unlinked_contact,
                    version=account_version_token(unlinked_contact.updated_at),
                ),
                'company': str(teammate_company.pk),
                'owner_user': str(self.teammate.pk),
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        unlinked_contact.refresh_from_db()
        self.assertEqual(unlinked_contact.company_id, teammate_company.pk)
        self.assertEqual(unlinked_contact.owner_user_id, self.teammate.pk)

    def test_legacy_gets_and_sales_navigation_point_to_canonical_objects(self):
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:sales_workspace'),
            {'view': 'companies', 'q': ' Alpha ', 'unknown': 'drop-me'},
        )
        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response['Location'])
        self.assertEqual(parsed.path, reverse('console:company_workspace_v2'))
        self.assertEqual(parse_qs(parsed.query), {'q': ['Alpha']})

        navigation = build_navigation('contacts', self.sales)
        sales_domain = next(item for item in navigation if item['key'] == 'sales')
        children = {item['key']: item for item in sales_domain['children']}
        self.assertTrue(children['contacts']['active'])
        self.assertEqual(children['companies']['href'], reverse('console:company_workspace_v2'))
        self.assertEqual(children['tasks']['href'], reverse('console:task_workspace_v2'))
