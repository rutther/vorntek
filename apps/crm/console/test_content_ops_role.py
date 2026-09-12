from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase

from console import capabilities
from console.access import (
    ALL_CONSOLE_ROLES,
    GROUP_BY_ROLE,
    ROLE_CONTENT_OPS,
    ROLE_LABELS,
    ROLE_SALES,
    ROLE_SYSTEM_ADMIN,
    user_role_keys,
)
from console.models import AuditLog
from console.system_forms import ConsoleUserForm
from leads.models import SalesTeam, SalesTeamMember
from scripts.prepare_candidate_demo import candidate_demo_password, candidate_role_usernames
from sitecore.models import Site


class ContentOpsRoleSourceTests(SimpleTestCase):
    def test_content_ops_has_one_canonical_role_source_and_local_demo_account(self):
        self.assertIn(ROLE_CONTENT_OPS, ALL_CONSOLE_ROLES)
        self.assertEqual(GROUP_BY_ROLE[ROLE_CONTENT_OPS], 'SiteOS Content Ops')
        self.assertEqual(ROLE_LABELS[ROLE_CONTENT_OPS], '内容运营')
        self.assertIs(capabilities.ROLE_CONTENT_OPS, ROLE_CONTENT_OPS)
        self.assertNotIn(
            'ROLE_CONTENT_OPS =',
            Path(capabilities.__file__).read_text(encoding='utf-8'),
        )

        usernames = candidate_role_usernames()
        self.assertEqual(usernames[ROLE_CONTENT_OPS], 'local-content')
        self.assertEqual(len(usernames), len(ALL_CONSOLE_ROLES))
        self.assertEqual(len(set(usernames.values())), len(usernames))

    def test_candidate_demo_password_has_no_shared_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(SystemExit, 'SITEOS_CANDIDATE_PASSWORD'):
                candidate_demo_password()
        with patch.dict(os.environ, {'SITEOS_CANDIDATE_PASSWORD': 'short'}, clear=True):
            with self.assertRaisesRegex(SystemExit, 'at least 12 characters'):
                candidate_demo_password()
        with patch.dict(
            os.environ,
            {'SITEOS_CANDIDATE_PASSWORD': 'UniqueLocal!234'},
            clear=True,
        ):
            self.assertEqual(candidate_demo_password(), 'UniqueLocal!234')


class ContentOpsUserManagementTests(TestCase):
    unmanaged_models = (
        Site,
        SalesTeam,
        SalesTeamMember,
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
        self.site = Site.objects.create(
            code='content-role-test',
            name='Content role test site',
            base_url='https://example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.team = SalesTeam.objects.create(
            site=self.site,
            code='content_role_sales',
            name='Content role sales team',
            enabled=True,
        )
        admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        self.actor = get_user_model().objects.create_user(
            username='content-role-admin',
            password='AdminTest!4937',
            is_superuser=True,
        )
        self.actor.groups.add(admin_group)

    @staticmethod
    def _form_data(*, username: str, role: str, teams=(), password: str = ''):
        return {
            'username': username,
            'first_name': 'Content',
            'last_name': 'Operator',
            'email': f'{username}@example.invalid',
            'is_active': 'on',
            'role': role,
            'teams': [str(team_id) for team_id in teams],
            'new_password': password,
            'confirm_password': password,
        }

    def test_content_ops_form_rejects_sales_team_and_creates_teamless_role(self):
        invalid = ConsoleUserForm(
            data=self._form_data(
                username='content-with-team',
                role=ROLE_CONTENT_OPS,
                teams=(self.team.pk,),
                password='Z9!bT4#pL7@qR2$x',
            ),
            actor=self.actor,
        )

        self.assertFalse(invalid.is_valid())
        self.assertIn('只有销售与销售经理', invalid.errors['teams'][0])
        self.assertFalse(get_user_model().objects.filter(username='content-with-team').exists())

        valid = ConsoleUserForm(
            data=self._form_data(
                username='content-teamless',
                role=ROLE_CONTENT_OPS,
                password='Z9!bT4#pL7@qR2$x',
            ),
            actor=self.actor,
        )
        self.assertTrue(valid.is_valid(), valid.errors.as_json())
        user = valid.save()

        self.assertEqual(user_role_keys(user), {ROLE_CONTENT_OPS})
        self.assertFalse(SalesTeamMember.objects.filter(user=user).exists())

    def test_editing_sales_user_to_content_ops_removes_every_team_membership(self):
        sales_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES])
        user = get_user_model().objects.create_user(
            username='sales-becoming-content',
            password='SalesTest!4937',
            is_active=True,
        )
        user.groups.add(sales_group)
        SalesTeamMember.objects.create(
            team=self.team,
            user=user,
            membership_role='member',
        )
        form = ConsoleUserForm(
            data=self._form_data(
                username=user.username,
                role=ROLE_CONTENT_OPS,
            ),
            instance=user,
            actor=self.actor,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        saved = form.save()

        self.assertEqual(user_role_keys(saved), {ROLE_CONTENT_OPS})
        self.assertEqual(ConsoleUserForm._primary_role_key(saved), ROLE_CONTENT_OPS)
        self.assertFalse(SalesTeamMember.objects.filter(user=saved).exists())

    def test_bootstrap_registers_all_roles_and_content_assignment_clears_stale_team(self):
        stdout = StringIO()
        call_command('bootstrap_console_roles', stdout=stdout)
        self.assertEqual(
            set(Group.objects.filter(name__in=GROUP_BY_ROLE.values()).values_list('name', flat=True)),
            set(GROUP_BY_ROLE.values()),
        )
        self.assertIn(f'Role groups ready: {len(ALL_CONSOLE_ROLES)}', stdout.getvalue())

        user = get_user_model().objects.create_user(
            username='bootstrap-content-user',
            password='BootstrapTest!4937',
        )
        user.groups.add(Group.objects.get(name=GROUP_BY_ROLE[ROLE_SALES]))
        SalesTeamMember.objects.create(
            team=self.team,
            user=user,
            membership_role='member',
        )

        call_command(
            'bootstrap_console_roles',
            user=user.username,
            role=ROLE_CONTENT_OPS,
            site_code=self.site.code,
            stdout=StringIO(),
        )

        user.refresh_from_db()
        self.assertEqual(user_role_keys(user), {ROLE_CONTENT_OPS})
        self.assertFalse(SalesTeamMember.objects.filter(user=user).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action='console_role_assigned',
                entity_table='auth_user',
                entity_id=user.pk,
                after_json__role=ROLE_CONTENT_OPS,
            ).exists()
        )
