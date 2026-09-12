from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from console.access import (
    GROUP_BY_ROLE,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
)
from console.models import AuditLog
from console.opportunity_versions import opportunity_version_token
from leads.crm_services import OPPORTUNITY_STAGE_TRANSITIONS
from leads.models import (
    Activity,
    Company,
    Contact,
    CrmAttachment,
    LeadFormDefinition,
    LeadSubmission,
    Opportunity,
    OpportunityStageHistory,
    SalesTeam,
    SalesTeamMember,
    Task,
)
from marketing.models import CanonicalEvent
from sitecore.models import Cta, MediaAsset, PageRoute, Site, SiteLocale


class OpportunityViewsV2Tests(TestCase):
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
        Activity,
        Task,
        OpportunityStageHistory,
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
            for role in (ROLE_SALES, ROLE_SALES_MANAGER, ROLE_MARKETING_OPS, ROLE_SYSTEM_ADMIN)
        }
        User = get_user_model()
        self.sales = User.objects.create_user(username='opportunity-sales', password='test-password')
        self.other_sales = User.objects.create_user(username='opportunity-other', password='test-password')
        self.manager = User.objects.create_user(username='opportunity-manager', password='test-password')
        self.marketing = User.objects.create_user(username='opportunity-marketing', password='test-password')
        self.admin = User.objects.create_user(username='opportunity-admin', password='test-password')
        self.sales.groups.add(groups[ROLE_SALES])
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
        self.team_a = SalesTeam.objects.create(site=self.site, code='team-a', name='Team A', enabled=True)
        self.team_b = SalesTeam.objects.create(site=self.site, code='team-b', name='Team B', enabled=True)
        SalesTeamMember.objects.create(team=self.team_a, user=self.sales, membership_role='member')
        SalesTeamMember.objects.create(team=self.team_a, user=self.manager, membership_role='manager')
        SalesTeamMember.objects.create(team=self.team_b, user=self.other_sales, membership_role='member')

        self.company_a = Company.objects.create(
            site=self.site,
            owner_user=self.sales,
            team=self.team_a,
            name='Alpha Bottling',
            normalized_name='alpha bottling',
            country='AE',
        )
        self.contact_a = Contact.objects.create(
            site=self.site,
            company=self.company_a,
            owner_user=self.sales,
            team=self.team_a,
            full_name='Amina Hassan',
        )
        self.company_b = Company.objects.create(
            site=self.site,
            owner_user=self.other_sales,
            team=self.team_b,
            name='Beta Beverage',
            normalized_name='beta beverage',
            country='SA',
        )
        self.owned = self._opportunity(
            name='Alpha aseptic line',
            owner=self.sales,
            team=self.team_a,
            company=self.company_a,
            contact=self.contact_a,
            next_follow_up_at=self.now - timedelta(hours=2),
            next_step='',
        )
        self.other = self._opportunity(
            name='Beta canning line',
            owner=self.other_sales,
            team=self.team_b,
            company=self.company_b,
            contact=None,
        )
        self.unassigned = self._opportunity(
            name='Alpha unassigned project',
            owner=None,
            team=self.team_a,
            company=self.company_a,
            contact=self.contact_a,
        )

    def _opportunity(
        self,
        *,
        name,
        owner,
        team,
        company,
        contact,
        stage='qualification',
        next_follow_up_at=None,
        next_step='Confirm technical scope',
    ):
        return Opportunity.objects.create(
            site=self.site,
            company=company,
            primary_contact=contact,
            owner_user=owner,
            team=team,
            name=name,
            stage=stage,
            value_amount=Decimal('350000.00'),
            currency='USD',
            probability=20,
            expected_close_date=timezone.localdate(self.now) + timedelta(days=5),
            product_scope='Aseptic filling line',
            capacity_target='36,000 BPH',
            packaging_format='PET',
            source_channel='website',
            next_step=next_step,
            next_follow_up_at=next_follow_up_at,
        )

    def _edit_payload(self, opportunity, *, version=None, owner_id=None, name=None):
        return {
            'version': version if version is not None else opportunity_version_token(opportunity.updated_at),
            'name': name or opportunity.name,
            'owner_user': str(owner_id if owner_id is not None else (opportunity.owner_user_id or '')),
            'value_amount': str(opportunity.value_amount or ''),
            'currency': opportunity.currency,
            'probability': str(opportunity.probability),
            'expected_close_date': (
                opportunity.expected_close_date.isoformat()
                if opportunity.expected_close_date else ''
            ),
            'product_scope': opportunity.product_scope,
            'capacity_target': opportunity.capacity_target,
            'packaging_format': opportunity.packaging_format,
            'next_step': opportunity.next_step,
            'next_follow_up_at': (
                timezone.localtime(opportunity.next_follow_up_at).strftime('%Y-%m-%dT%H:%M')
                if opportunity.next_follow_up_at else ''
            ),
        }

    @staticmethod
    def _canonical_query(**overrides):
        values = {
            'sort': 'attention',
            'page': '1',
            'page_size': '25',
            'view': 'list',
        }
        values.update(overrides)
        return values

    def test_role_and_team_scope_are_fail_closed(self):
        self.client.force_login(self.sales)
        response = self.client.get(reverse('console:opportunity_workspace_v2'), self._canonical_query())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Alpha aseptic line')
        self.assertNotContains(response, 'Beta canning line')
        self.assertNotContains(response, 'Alpha unassigned project')
        self.assertContains(response, 'row-cols-xl-3')

        response = self.client.get(
            reverse('console:opportunity_workspace_detail_v2', args=[self.other.id]),
            self._canonical_query(),
        )
        self.assertEqual(response.status_code, 404)

        self.client.force_login(self.manager)
        response = self.client.get(reverse('console:opportunity_workspace_v2'), self._canonical_query())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Alpha aseptic line')
        self.assertContains(response, 'Alpha unassigned project')
        self.assertNotContains(response, 'Beta canning line')
        self.assertContains(response, 'row-cols-xl-4')

        self.client.force_login(self.marketing)
        response = self.client.get(reverse('console:opportunity_workspace_v2'), self._canonical_query())
        self.assertEqual(response.status_code, 403)

    def test_corrupt_cross_site_direct_targets_are_hidden_even_from_admin(self):
        other_site = Site.objects.create(
            code='opportunity-other-site',
            name='Opportunity Other Site',
            base_url='https://opportunity-other.example.com',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        other_locale = SiteLocale.objects.create(
            site=other_site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )
        other_team = SalesTeam.objects.create(
            site=other_site,
            code='opportunity-other-team',
            name='Opportunity Other Team',
            enabled=True,
        )
        other_company = Company.objects.create(
            site=other_site,
            owner_user=self.sales,
            team=other_team,
            name='Hidden Cross-site Company',
            normalized_name='hidden cross-site company',
        )
        other_contact = Contact.objects.create(
            site=other_site,
            company=other_company,
            owner_user=self.sales,
            team=other_team,
            full_name='Hidden Cross-site Contact',
        )
        other_form = LeadFormDefinition.objects.create(
            site=other_site,
            locale=other_locale,
            code='opportunity-cross-site-form',
            name='Cross-site form',
            form_schema={},
            config_json={},
        )
        other_lead = LeadSubmission.objects.create(
            form=other_form,
            site=other_site,
            locale=other_locale,
            assignee=self.sales,
            team=other_team,
            full_name='Hidden Cross-site Lead',
            payload_json={},
            identifiers_json={},
            utm_json={},
            consent_json={},
            config_json={},
            qualification_json={},
            submitted_at=self.now,
            stage_updated_at=self.now,
        )
        corrupt = []
        company_link = self._opportunity(
            name='Corrupt company target', owner=self.sales, team=self.team_a,
            company=self.company_a, contact=self.contact_a,
        )
        Opportunity.objects.filter(pk=company_link.pk).update(company=other_company)
        corrupt.append(company_link)
        contact_link = self._opportunity(
            name='Corrupt contact target', owner=self.sales, team=self.team_a,
            company=self.company_a, contact=self.contact_a,
        )
        Opportunity.objects.filter(pk=contact_link.pk).update(primary_contact=other_contact)
        corrupt.append(contact_link)
        lead_link = self._opportunity(
            name='Corrupt source lead target', owner=self.sales, team=self.team_a,
            company=self.company_a, contact=self.contact_a,
        )
        Opportunity.objects.filter(pk=lead_link.pk).update(source_submission=other_lead)
        corrupt.append(lead_link)

        for user in (self.sales, self.admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse('console:opportunity_workspace_v2'),
                    self._canonical_query(),
                )
                self.assertEqual(response.status_code, 200)
                for item in corrupt:
                    self.assertNotContains(response, item.name)
                    detail = self.client.get(
                        reverse(
                            'console:opportunity_workspace_detail_v2',
                            args=[item.pk],
                        ),
                        self._canonical_query(),
                    )
                    self.assertEqual(detail.status_code, 404)

    def test_write_endpoints_cannot_cross_owner_or_managed_team_scope(self):
        original_name = self.other.name
        self.client.force_login(self.sales)
        response = self.client.post(
            reverse('console:opportunity_update', args=[self.other.id]),
            self._edit_payload(self.other, name='Cross-owner overwrite'),
        )
        self.assertEqual(response.status_code, 404)

        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('console:opportunity_stage', args=[self.other.id]),
            {
                'version': opportunity_version_token(self.other.updated_at),
                'stage': 'discovery',
                'reason': '',
            },
        )
        self.assertEqual(response.status_code, 404)
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, original_name)
        self.assertEqual(self.other.stage, 'qualification')
        self.assertFalse(Activity.objects.filter(opportunity=self.other).exists())
        self.assertFalse(OpportunityStageHistory.objects.filter(opportunity=self.other).exists())

    def test_unknown_and_invalid_query_state_redirects_to_one_canonical_url(self):
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:opportunity_workspace_v2'),
            {
                'q': '  Alpha   aseptic  ',
                'stage': 'not-a-stage',
                'page': '-8',
                'page_size': '999',
                'view': 'tiles',
                'attacker': '<script>',
            },
        )
        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response['Location'])
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, reverse('console:opportunity_workspace_v2'))
        self.assertEqual(params['q'], ['Alpha aseptic'])
        self.assertEqual(params['sort'], ['attention'])
        self.assertEqual(params['page'], ['1'])
        self.assertEqual(params['page_size'], ['25'])
        self.assertEqual(params['view'], ['list'])
        self.assertNotIn('stage', params)
        self.assertNotIn('attacker', params)

        canonical = self.client.get(response['Location'])
        self.assertEqual(canonical.status_code, 200)

    def test_metrics_empty_states_filters_and_pagination_are_truthful(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('console:opportunity_workspace_v2'), self._canonical_query())
        self.assertEqual(response.status_code, 200)
        metrics = {item['key']: item for item in response.context['workspace']['metrics']}
        self.assertEqual(metrics['overdue']['value'], 1)
        self.assertEqual(metrics['missing_next_step']['value'], 1)
        self.assertEqual(metrics['unassigned']['value'], 1)

        response = self.client.get(
            reverse('console:opportunity_workspace_v2'),
            self._canonical_query(q='definitely-no-result'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '没有匹配的销售机会')
        self.assertContains(response, '搜索：definitely-no-result')

        for index in range(27):
            self._opportunity(
                name=f'Paged project {index:02d}',
                owner=self.sales,
                team=self.team_a,
                company=self.company_a,
                contact=self.contact_a,
            )
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:opportunity_workspace_v2'),
            self._canonical_query(page='2', page_size='20'),
        )
        self.assertEqual(response.status_code, 200)
        pagination = response.context['workspace']['pagination']
        self.assertEqual(pagination['current'], 2)
        self.assertEqual(pagination['total_pages'], 2)
        self.assertTrue(pagination['previous_url'])

    def test_detail_renders_relations_allowed_transitions_and_accessible_modals(self):
        Activity.objects.create(
            site=self.site,
            opportunity=self.owned,
            actor_user=self.sales,
            activity_type='call',
            direction='outbound',
            subject='Confirmed line speed',
            body='Customer confirmed 36,000 BPH.',
            occurred_at=self.now,
        )
        Task.objects.create(
            site=self.site,
            opportunity=self.owned,
            owner_user=self.sales,
            team=self.team_a,
            created_by_user=self.sales,
            title='Send revised layout',
            due_at=self.now + timedelta(days=1),
        )
        OpportunityStageHistory.objects.create(
            opportunity=self.owned,
            from_stage='qualification',
            to_stage='discovery',
            changed_by_user=self.sales,
            reason='Scope verified',
        )
        CrmAttachment.objects.create(
            site=self.site,
            opportunity=self.owned,
            uploaded_by_user=self.sales,
            title='Technical layout',
            original_name='layout.pdf',
            mime_type='application/pdf',
            file_size_bytes=2048,
            sha256='a' * 64,
            storage_path='candidate/layout.pdf',
        )
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:opportunity_workspace_detail_v2', args=[self.owned.id]),
            self._canonical_query(q='Alpha'),
        )
        self.assertEqual(response.status_code, 200)
        workspace = response.context['workspace']
        self.assertEqual(
            {item['value'] for item in workspace['stage_targets']},
            set(OPPORTUNITY_STAGE_TRANSITIONS['qualification']),
        )
        self.assertContains(response, 'Confirmed line speed')
        self.assertContains(response, 'Send revised layout')
        self.assertContains(response, 'Scope verified')
        self.assertContains(response, 'Technical layout')
        self.assertContains(response, 'data-nc-opportunity-form="edit"')
        self.assertContains(response, 'data-nc-opportunity-form="stage"')
        self.assertContains(response, 'aria-labelledby="opportunity-edit-title"')
        self.assertContains(response, 'aria-labelledby="opportunity-stage-title"')
        self.assertContains(response, 'name="version"')
        self.assertNotContains(response, '<option value="">未分配</option>', html=True)
        self.assertEqual(response.content.count(b'<h1'), 1)
        self.assertIn('q=Alpha', workspace['back_url'])

    def test_read_only_role_does_not_render_mutation_modals(self):
        # A sales manager may read team objects and may write them; use a
        # patched capability boundary to prove templates obey the explicit
        # server decision instead of role-name assumptions.
        self.client.force_login(self.sales)
        from unittest.mock import patch

        with patch('console.opportunity_workspace_v2.can_sales', return_value=False):
            response = self.client.get(
                reverse('console:opportunity_workspace_detail_v2', args=[self.owned.id]),
                self._canonical_query(),
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-nc-opportunity-form="edit"')
        self.assertNotContains(response, 'data-nc-opportunity-form="stage"')

    def test_old_pipeline_get_redirects_to_canonical_board_without_unknowns(self):
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:sales_workspace'),
            {'view': 'pipeline', 'q': ' Alpha ', 'source_channel': 'website', 'unknown': 'drop-me'},
        )
        self.assertEqual(response.status_code, 302)
        parsed = urlsplit(response['Location'])
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, reverse('console:opportunity_workspace_v2'))
        self.assertEqual(params['view'], ['board'])
        self.assertEqual(params['q'], ['Alpha'])
        self.assertEqual(params['source'], ['website'])
        self.assertNotIn('unknown', params)

    def test_stale_edit_and_stage_tokens_return_409_without_side_effects(self):
        self.client.force_login(self.sales)
        stale_version = opportunity_version_token(self.owned.updated_at)
        Opportunity.objects.filter(pk=self.owned.id).update(
            name='Server-side newer name',
            updated_at=self.owned.updated_at + timedelta(seconds=1),
        )
        self.owned.refresh_from_db()
        activity_count = Activity.objects.filter(opportunity=self.owned).count()
        history_count = OpportunityStageHistory.objects.filter(opportunity=self.owned).count()
        audit_count = AuditLog.objects.count()

        response = self.client.post(
            reverse('console:opportunity_update', args=[self.owned.id]),
            self._edit_payload(
                self.owned,
                version=stale_version,
                name='Stale browser overwrite',
            ),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'opportunity_version_conflict')
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.name, 'Server-side newer name')
        self.assertEqual(Activity.objects.filter(opportunity=self.owned).count(), activity_count)
        self.assertEqual(AuditLog.objects.count(), audit_count)

        response = self.client.post(
            reverse('console:opportunity_stage', args=[self.owned.id]),
            {'version': stale_version, 'stage': 'discovery', 'reason': ''},
        )
        self.assertEqual(response.status_code, 409)
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.stage, 'qualification')
        self.assertEqual(Activity.objects.filter(opportunity=self.owned).count(), activity_count)
        self.assertEqual(
            OpportunityStageHistory.objects.filter(opportunity=self.owned).count(),
            history_count,
        )
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_missing_version_is_a_409_and_cannot_bypass_optimistic_lock(self):
        self.client.force_login(self.sales)
        activity_count = Activity.objects.filter(opportunity=self.owned).count()
        audit_count = AuditLog.objects.count()

        payload = self._edit_payload(self.owned, name='No version overwrite')
        payload.pop('version')
        response = self.client.post(
            reverse('console:opportunity_update', args=[self.owned.id]),
            payload,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'opportunity_version_conflict')
        self.owned.refresh_from_db()
        self.assertNotEqual(self.owned.name, 'No version overwrite')
        self.assertEqual(Activity.objects.filter(opportunity=self.owned).count(), activity_count)
        self.assertEqual(AuditLog.objects.count(), audit_count)

        response = self.client.post(
            reverse('console:opportunity_stage', args=[self.owned.id]),
            {'stage': 'discovery', 'reason': ''},
        )
        self.assertEqual(response.status_code, 409)
        self.owned.refresh_from_db()
        self.assertEqual(self.owned.stage, 'qualification')
        self.assertEqual(Activity.objects.filter(opportunity=self.owned).count(), activity_count)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_inactive_current_owner_is_preserved_for_unrelated_edit(self):
        inactive = get_user_model().objects.create_user(
            username='historical-owner',
            password='test-password',
            is_active=False,
        )
        SalesTeamMember.objects.create(
            team=self.team_a,
            user=inactive,
            membership_role='member',
        )
        historical = self._opportunity(
            name='Historical owner project',
            owner=inactive,
            team=self.team_a,
            company=self.company_a,
            contact=self.contact_a,
        )
        self.client.force_login(self.manager)
        detail = self.client.get(
            reverse('console:opportunity_workspace_detail_v2', args=[historical.id]),
            self._canonical_query(),
        )
        self.assertEqual(detail.status_code, 200)
        current_choice = next(
            choice
            for choice in detail.context['workspace']['owner_choices']
            if choice['value'] == str(inactive.id)
        )
        self.assertTrue(current_choice['selected'])
        self.assertFalse(current_choice['disabled'])

        response = self.client.post(
            reverse('console:opportunity_update', args=[historical.id]),
            self._edit_payload(
                historical,
                owner_id=inactive.id,
                name='Historical owner project revised',
            ),
        )
        self.assertEqual(response.status_code, 200, response.content)
        historical.refresh_from_db()
        self.assertEqual(historical.name, 'Historical owner project revised')
        self.assertEqual(historical.owner_user_id, inactive.id)
        self.assertEqual(historical.team_id, self.team_a.id)

    def test_manager_assignment_cannot_move_record_across_team_boundary(self):
        managed_destination = SalesTeam.objects.create(
            site=self.site,
            code='team-c',
            name='Team C',
            enabled=True,
        )
        current_team = SalesTeam.objects.create(
            site=self.site,
            code='team-d',
            name='Team D',
            enabled=True,
        )
        SalesTeamMember.objects.create(
            team=managed_destination,
            user=self.manager,
            membership_role='manager',
        )
        SalesTeamMember.objects.create(
            team=current_team,
            user=self.manager,
            membership_role='manager',
        )
        # other_sales already belongs to the unmanaged, lower-id Team B.
        SalesTeamMember.objects.create(
            team=managed_destination,
            user=self.other_sales,
            membership_role='member',
        )
        movable = self._opportunity(
            name='Managed reassignment project',
            owner=self.sales,
            team=current_team,
            company=self.company_a,
            contact=self.contact_a,
        )

        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('console:opportunity_update', args=[movable.id]),
            self._edit_payload(movable, owner_id=self.other_sales.id),
        )

        self.assertEqual(response.status_code, 400, response.content)
        movable.refresh_from_db()
        self.assertEqual(movable.owner_user_id, self.sales.id)
        self.assertEqual(movable.team_id, current_team.id)
        self.assertNotEqual(movable.team_id, self.team_b.id)
        self.assertFalse(Activity.objects.filter(opportunity=movable).exists())
