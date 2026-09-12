from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from console.access import GROUP_BY_ROLE, ROLE_SALES, ROLE_SALES_MANAGER
from leads.models import (
    Company,
    Contact,
    LeadFormDefinition,
    LeadSubmission,
    Opportunity,
    SalesTeam,
    SalesTeamMember,
    Task,
)
from marketing.models import CanonicalEvent
from sitecore.models import Cta, PageRoute, Site, SiteLocale


class SalesWorkspaceAssignmentScopeTests(TestCase):
    """The canonical task bulk chooser must not leak unmanaged-team users."""

    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
        LeadFormDefinition,
        SalesTeam,
        SalesTeamMember,
        LeadSubmission,
        Company,
        Contact,
        Opportunity,
        Task,
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
        sales_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES])
        manager_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES_MANAGER])
        self.manager = get_user_model().objects.create_user(
            username='team-a-manager',
            password='test-password',
        )
        self.manager.groups.add(manager_group)
        self.team_a_member = get_user_model().objects.create_user(
            username='team-a-member',
            password='test-password',
        )
        self.team_a_member.groups.add(sales_group)
        self.team_b_member = get_user_model().objects.create_user(
            username='team-b-member',
            password='test-password',
        )
        self.team_b_member.groups.add(sales_group)

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
        team_a = SalesTeam.objects.create(
            site=self.site,
            code='team-a',
            name='Team A',
            enabled=True,
        )
        team_b = SalesTeam.objects.create(
            site=self.site,
            code='team-b',
            name='Team B',
            enabled=True,
        )
        SalesTeamMember.objects.create(
            team=team_a,
            user=self.manager,
            membership_role='manager',
        )
        SalesTeamMember.objects.create(
            team=team_a,
            user=self.team_a_member,
            membership_role='member',
        )
        SalesTeamMember.objects.create(
            team=team_b,
            user=self.team_b_member,
            membership_role='member',
        )

    def test_manager_assignment_payload_contains_only_managed_team_users(self):
        self.client.force_login(self.manager)

        response = self.client.get(
            reverse('console:task_workspace_v2'),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        assignment_user_ids = {
            int(item['value']) for item in response.context['workspace']['assign_candidates']
        }
        self.assertEqual(
            assignment_user_ids,
            {self.manager.id, self.team_a_member.id},
        )
        self.assertNotIn(self.team_b_member.id, assignment_user_ids)
