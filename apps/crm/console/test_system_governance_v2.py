from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import DatabaseError, connection
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from console.access import (
    GROUP_BY_ROLE,
    ROLE_CONTENT_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
)
from console.models import AuditLog
from console.system_versions import sales_team_version_token, system_user_version_token
from console.views import sales_team_usage_queryset
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


class SystemGovernanceShellContractTests(SimpleTestCase):
    def test_legacy_shell_has_one_page_heading_and_keyboard_skip_link(self):
        source = (
            Path(settings.BASE_DIR)
            / 'console'
            / 'templates'
            / 'console'
            / 'app_shell.html'
        ).read_text(encoding='utf-8')

        self.assertEqual(source.count('<h1'), 1)
        self.assertIn('<h1 class="module-title">{{ page_payload.title }}</h1>', source)
        self.assertIn('class="admin-skip-link" href="#main-content"', source)
        self.assertIn('<main class="main" id="main-content" tabindex="-1">', source)
        self.assertIn('admin-shell.css\' %}?v=20260831a', source)
        self.assertIn('{% static stylesheet %}?v=20260831c', source)


class SystemGovernanceViewTests(TestCase):
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
        self.groups = {
            role: Group.objects.create(name=GROUP_BY_ROLE[role])
            for role in (ROLE_SYSTEM_ADMIN, ROLE_CONTENT_OPS, ROLE_SALES)
        }
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='governance-admin', password='Test-password-234'
        )
        self.admin.groups.add(self.groups[ROLE_SYSTEM_ADMIN])
        self.content_user = User.objects.create_user(
            username='governance-content',
            password='Test-password-234',
            first_name='Content',
            last_name='Operator',
            email='content@example.invalid',
        )
        self.content_user.groups.add(self.groups[ROLE_CONTENT_OPS])
        self.sales_user = User.objects.create_user(
            username='governance-sales', password='Test-password-234'
        )
        self.sales_user.groups.add(self.groups[ROLE_SALES])
        self.site = Site.objects.create(
            code='governance-site',
            name='Governance site',
            base_url='https://example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.form_definition = LeadFormDefinition.objects.create(
            site=self.site,
            code='governance-form',
            name='Governance form',
        )
        self.team = SalesTeam.objects.create(
            site=self.site,
            code='governance_team',
            name='Governance team',
            enabled=True,
        )
        self.client.force_login(self.admin)

    def user_post(self, *, version: str, first_name='Content', is_active=True):
        data = {
            'version': version,
            'username': self.content_user.username,
            'first_name': first_name,
            'last_name': 'Operator',
            'email': 'content@example.invalid',
            'role': ROLE_CONTENT_OPS,
            'teams': [],
            'new_password': '',
            'confirm_password': '',
        }
        if is_active:
            data['is_active'] = 'on'
        return data

    def team_post(self, *, version: str, name: str, enabled=True):
        data = {
            'version': version,
            'site': str(self.site.pk),
            'code': self.team.code,
            'name': name,
        }
        if enabled:
            data['enabled'] = 'on'
        return data

    def user_create_post(self, *, username='new-sales', role=ROLE_SALES, teams=None):
        password = 'New-account-fixture-583!'
        return {
            'username': username,
            'first_name': 'New',
            'last_name': 'Seller',
            'email': f'{username}@example.invalid',
            'is_active': 'on',
            'role': role,
            'teams': [str(team.pk) for team in (teams or [])],
            'new_password': password,
            'confirm_password': password,
        }

    def test_users_page_renders_content_role_and_one_shell_heading(self):
        response = self.client.get(reverse('console:system_users'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.content_user.username)
        self.assertContains(response, '内容运营')
        html = response.content.decode('utf-8')
        self.assertEqual(html.count('<h1'), 1)
        self.assertIn('href="#main-content"', html)
        self.assertIn('id="main-content"', html)

    def test_users_page_separates_role_conflicts_from_sales_without_team(self):
        conflicted = get_user_model().objects.create_user(
            username='governance-conflicted', password='Test-password-234'
        )
        conflicted.groups.add(
            self.groups[ROLE_SYSTEM_ADMIN],
            self.groups[ROLE_SALES],
        )

        response = self.client.get(reverse('console:system_users'))

        self.assertEqual(response.context['system_user_stats']['conflicted'], 1)
        self.assertEqual(response.context['system_user_stats']['needs_team'], 1)
        row = next(
            item for item in response.context['system_user_rows']
            if item['username'] == conflicted.username
        )
        self.assertTrue(row['role_conflict'])
        self.assertEqual(row['role_label'], '系统管理员')
        self.assertContains(response, '系统管理员 · 多角色')

    def test_user_create_page_explains_password_reset_session_effect(self):
        response = self.client.get(reverse('console:system_user_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '重置后旧密码失效')
        self.assertContains(response, '已登录的会话在下次请求时需要重新登录')

    def test_system_pages_are_denied_to_non_admin_roles(self):
        self.client.force_login(self.sales_user)

        for route_name in ('console:system_users', 'console:system_teams'):
            with self.subTest(route_name=route_name):
                self.assertEqual(self.client.get(reverse(route_name)).status_code, 403)

    def test_user_create_hashes_password_assigns_one_role_and_team_and_audits(self):
        data = self.user_create_post(teams=[self.team])

        response = self.client.post(reverse('console:system_user_create'), data)

        self.assertEqual(response.status_code, 200)
        created = get_user_model().objects.get(username=data['username'])
        self.assertTrue(created.check_password(data['new_password']))
        self.assertNotEqual(created.password, data['new_password'])
        self.assertEqual(
            set(created.groups.values_list('name', flat=True)),
            {GROUP_BY_ROLE[ROLE_SALES]},
        )
        membership = SalesTeamMember.objects.get(user=created, team=self.team)
        self.assertEqual(membership.membership_role, 'member')
        audit = AuditLog.objects.get(action='console_user_created')
        self.assertEqual(audit.entity_id, created.pk)
        self.assertEqual(audit.after_json['role_keys'], [ROLE_SALES])
        self.assertEqual(
            audit.after_json['teams'],
            [{'team_id': self.team.pk, 'membership_role': 'member'}],
        )
        evidence = repr((audit.before_json, audit.after_json, audit.metadata_json))
        self.assertNotIn(data['new_password'], evidence)
        self.assertNotIn(created.password, evidence)
        self.assertContains(response, '账号已创建')
        self.assertContains(response, data['username'])
        self.assertContains(response, data['new_password'])
        self.assertIn(
            f'action="{reverse("console:system_user_edit", args=[created.pk])}"',
            response.content.decode('utf-8'),
        )
        self.assertEqual(response['Cache-Control'], 'private, no-store, max-age=0')
        self.assertEqual(response['Pragma'], 'no-cache')

        refreshed = self.client.get(reverse('console:system_user_create'))
        self.assertNotContains(refreshed, data['new_password'])
        self.assertIn(
            f'action="{reverse("console:system_user_create")}"',
            refreshed.content.decode('utf-8'),
        )

    def test_sales_user_create_requires_team_without_partial_account_or_audit(self):
        data = self.user_create_post(username='sales-without-team')

        response = self.client.post(reverse('console:system_user_create'), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '销售与销售经理必须至少属于一个销售团队')
        self.assertFalse(
            get_user_model().objects.filter(username=data['username']).exists()
        )
        self.assertFalse(AuditLog.objects.filter(action='console_user_created').exists())

    def test_role_transition_replaces_role_and_promotes_team_membership(self):
        data = self.user_post(version=system_user_version_token(self.content_user))
        data.update(role=ROLE_SALES_MANAGER, teams=[str(self.team.pk)])

        response = self.client.post(
            reverse('console:system_user_edit', args=[self.content_user.pk]), data
        )

        self.assertEqual(response.status_code, 302)
        self.content_user.refresh_from_db()
        self.assertEqual(
            set(self.content_user.groups.values_list('name', flat=True)),
            {GROUP_BY_ROLE[ROLE_SALES_MANAGER]},
        )
        membership = SalesTeamMember.objects.get(
            user=self.content_user, team=self.team
        )
        self.assertEqual(membership.membership_role, 'manager')
        audit = AuditLog.objects.get(action='console_user_updated')
        self.assertEqual(audit.before_json['role_keys'], [ROLE_CONTENT_OPS])
        self.assertEqual(audit.after_json['role_keys'], [ROLE_SALES_MANAGER])

    def test_user_create_rolls_back_when_audit_write_fails(self):
        data = self.user_create_post(username='create-must-roll-back', teams=[self.team])

        with patch('console.views.record_audit', side_effect=DatabaseError('audit unavailable')):
            with self.assertRaises(DatabaseError):
                self.client.post(reverse('console:system_user_create'), data)

        self.assertFalse(
            get_user_model().objects.filter(username=data['username']).exists()
        )
        self.assertFalse(AuditLog.objects.filter(action='console_user_created').exists())

    def test_user_update_accepts_current_version_and_writes_audit(self):
        token = system_user_version_token(self.content_user)

        response = self.client.post(
            reverse('console:system_user_edit', args=[self.content_user.pk]),
            self.user_post(version=token, first_name='Updated'),
        )

        self.assertEqual(response.status_code, 302)
        self.content_user.refresh_from_db()
        self.assertEqual(self.content_user.first_name, 'Updated')
        audit = AuditLog.objects.get(action='console_user_updated')
        self.assertEqual(audit.entity_id, self.content_user.pk)
        self.assertEqual(audit.before_json['first_name'], 'Content')
        self.assertEqual(audit.after_json['first_name'], 'Updated')

    def test_user_update_rejects_stale_or_tampered_version(self):
        stale_token = system_user_version_token(self.content_user)
        get_user_model().objects.filter(pk=self.content_user.pk).update(first_name='External')

        response = self.client.post(
            reverse('console:system_user_edit', args=[self.content_user.pk]),
            self.user_post(version=stale_token, first_name='Overwritten'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '账号已被其他管理员更新')
        self.content_user.refresh_from_db()
        self.assertEqual(self.content_user.first_name, 'External')
        self.assertFalse(AuditLog.objects.filter(action='console_user_updated').exists())

    def test_disabling_user_rejects_existing_session_and_new_login(self):
        employee = Client()
        employee.force_login(self.content_user)
        response = self.client.post(
            reverse('console:system_user_edit', args=[self.content_user.pk]),
            self.user_post(
                version=system_user_version_token(self.content_user), is_active=False
            ),
        )
        self.assertEqual(response.status_code, 302)
        denied = employee.get(reverse('console:system_users'))
        self.assertEqual(denied.status_code, 302)
        self.assertIn('/login/', denied.url)
        self.assertFalse(employee.login(
            username=self.content_user.username, password='Test-password-234'
        ))
        self.content_user.refresh_from_db()
        self.assertFalse(self.content_user.is_active)

    def test_password_reset_revokes_old_session_without_exposing_password_in_audit(self):
        employee = Client()
        employee.force_login(self.content_user)
        new_password = 'Isolated-reset-fixture-748!'
        data = self.user_post(version=system_user_version_token(self.content_user))
        data.update(new_password=new_password, confirm_password=new_password)
        response = self.client.post(
            reverse('console:system_user_edit', args=[self.content_user.pk]), data
        )
        self.assertEqual(response.status_code, 200)
        self.content_user.refresh_from_db()
        self.assertTrue(self.content_user.check_password(new_password))
        self.assertNotEqual(self.content_user.password, new_password)
        denied = employee.get(reverse('console:system_users'))
        self.assertEqual(denied.status_code, 302)
        self.assertIn('/login/', denied.url)
        self.assertFalse(employee.login(
            username=self.content_user.username, password='Test-password-234'
        ))
        self.assertTrue(employee.login(
            username=self.content_user.username, password=new_password
        ))
        audit = AuditLog.objects.get(action='console_user_updated')
        evidence = repr((audit.before_json, audit.after_json, audit.metadata_json))
        self.assertNotIn(new_password, evidence)
        self.assertNotIn(self.content_user.password, evidence)
        self.assertContains(response, '密码已重置')
        self.assertContains(response, new_password)
        self.assertIn(
            f'action="{reverse("console:system_user_edit", args=[self.content_user.pk])}"',
            response.content.decode('utf-8'),
        )
        self.assertEqual(response['Cache-Control'], 'private, no-store, max-age=0')

    def test_non_admin_cannot_post_user_changes(self):
        token = system_user_version_token(self.content_user)
        self.client.force_login(self.sales_user)
        response = self.client.post(
            reverse('console:system_user_edit', args=[self.content_user.pk]),
            self.user_post(version=token, first_name='Forbidden'),
        )
        self.assertEqual(response.status_code, 403)
        self.content_user.refresh_from_db()
        self.assertEqual(self.content_user.first_name, 'Content')
        self.assertFalse(AuditLog.objects.filter(action='console_user_updated').exists())

    def test_blank_password_edit_preserves_existing_session(self):
        employee = Client()
        employee.force_login(self.content_user)
        original_hash = self.content_user.password
        response = self.client.post(
            reverse('console:system_user_edit', args=[self.content_user.pk]),
            self.user_post(version=system_user_version_token(self.content_user)),
        )
        self.assertEqual(response.status_code, 302)
        self.content_user.refresh_from_db()
        self.assertEqual(self.content_user.password, original_hash)
        # Still authenticated but forbidden: this content user is not an administrator.
        self.assertEqual(employee.get(reverse('console:system_users')).status_code, 403)

    def test_user_update_rolls_back_when_audit_write_fails(self):
        token = system_user_version_token(self.content_user)

        with patch('console.views.record_audit', side_effect=DatabaseError('audit unavailable')):
            with self.assertRaises(DatabaseError):
                self.client.post(
                    reverse('console:system_user_edit', args=[self.content_user.pk]),
                    self.user_post(version=token, first_name='Must roll back'),
                )

        self.content_user.refresh_from_db()
        self.assertEqual(self.content_user.first_name, 'Content')
        self.assertFalse(AuditLog.objects.filter(action='console_user_updated').exists())

    def test_current_and_last_admin_cannot_disable_or_demote_self(self):
        token = system_user_version_token(self.admin)
        response = self.client.post(
            reverse('console:system_user_edit', args=[self.admin.pk]),
            {
                'version': token,
                'username': self.admin.username,
                'first_name': '',
                'last_name': '',
                'email': '',
                'role': ROLE_CONTENT_OPS,
                'teams': [],
                'new_password': '',
                'confirm_password': '',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '不能停用当前正在使用的账号')
        self.assertContains(response, '不能解除当前账号自己的系统管理员角色')
        self.assertContains(response, '系统至少需要保留一个启用的系统管理员账号')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertTrue(self.admin.groups.filter(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN]).exists())
        self.assertFalse(AuditLog.objects.filter(action='console_user_updated').exists())

    def test_team_list_uses_aggregated_governance_counts(self):
        SalesTeam.objects.create(
            site=self.site,
            code='second_team',
            name='Second team',
            enabled=True,
        )

        with self.assertNumQueries(1):
            rows = list(sales_team_usage_queryset(SalesTeam.objects.all()).order_by('id'))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].active_member_count, 0)
        self.assertEqual(rows[0].open_lead_count, 0)
        self.assertEqual(rows[0].actionable_task_count, 0)
        self.assertEqual(rows[0].open_opportunity_count, 0)

    def test_team_disable_is_blocked_by_every_active_usage_type(self):
        SalesTeamMember.objects.create(
            team=self.team, user=self.sales_user, membership_role='member'
        )
        LeadSubmission.objects.create(
            form=self.form_definition,
            site=self.site,
            team=self.team,
            stage='new',
            submitted_at=timezone.now(),
            stage_updated_at=timezone.now(),
        )
        opportunity = Opportunity.objects.create(
            site=self.site,
            team=self.team,
            name='Open opportunity',
            stage='qualification',
        )
        Task.objects.create(
            site=self.site,
            team=self.team,
            opportunity=opportunity,
            owner_user=self.sales_user,
            created_by_user=self.admin,
            title='Open task',
            status='open',
            due_at=timezone.now(),
        )
        token = sales_team_version_token(self.team)

        response = self.client.post(
            reverse('console:system_team_edit', args=[self.team.pk]),
            self.team_post(version=token, name=self.team.name, enabled=False),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '该团队仍有业务范围，不能停用')
        self.assertContains(response, '启用成员 1')
        self.assertContains(response, '开放线索 1')
        self.assertContains(response, '可执行任务 1')
        self.assertContains(response, '开放销售机会 1')
        self.team.refresh_from_db()
        self.assertTrue(self.team.enabled)
        self.assertFalse(AuditLog.objects.filter(action='sales_team_updated').exists())

    def test_terminal_work_and_inactive_member_do_not_block_team_disable(self):
        self.sales_user.is_active = False
        self.sales_user.save(update_fields=['is_active'])
        SalesTeamMember.objects.create(
            team=self.team, user=self.sales_user, membership_role='member'
        )
        LeadSubmission.objects.create(
            form=self.form_definition,
            site=self.site,
            team=self.team,
            stage='spam',
            submitted_at=timezone.now(),
            stage_updated_at=timezone.now(),
        )
        opportunity = Opportunity.objects.create(
            site=self.site,
            team=self.team,
            name='Won opportunity',
            stage='won',
        )
        Task.objects.create(
            site=self.site,
            team=self.team,
            opportunity=opportunity,
            owner_user=self.sales_user,
            created_by_user=self.admin,
            title='Completed task',
            status='completed',
            due_at=timezone.now(),
            completed_at=timezone.now(),
        )
        token = sales_team_version_token(self.team)

        response = self.client.post(
            reverse('console:system_team_edit', args=[self.team.pk]),
            self.team_post(version=token, name='Archived team', enabled=False),
        )

        self.assertEqual(response.status_code, 302)
        self.team.refresh_from_db()
        self.assertFalse(self.team.enabled)
        self.assertEqual(self.team.name, 'Archived team')
        self.assertTrue(AuditLog.objects.filter(action='sales_team_updated').exists())

    def test_team_update_rejects_stale_version(self):
        stale_token = sales_team_version_token(self.team)
        SalesTeam.objects.filter(pk=self.team.pk).update(name='External team name')

        response = self.client.post(
            reverse('console:system_team_edit', args=[self.team.pk]),
            self.team_post(version=stale_token, name='Overwritten team name'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '团队已被其他管理员更新')
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'External team name')
        self.assertFalse(AuditLog.objects.filter(action='sales_team_updated').exists())

    def test_team_update_rolls_back_when_audit_write_fails(self):
        token = sales_team_version_token(self.team)

        with patch('console.views.record_audit', side_effect=DatabaseError('audit unavailable')):
            with self.assertRaises(DatabaseError):
                self.client.post(
                    reverse('console:system_team_edit', args=[self.team.pk]),
                    self.team_post(version=token, name='Must roll back team'),
                )

        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'Governance team')
        self.assertFalse(AuditLog.objects.filter(action='sales_team_updated').exists())
