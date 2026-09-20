from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase

from console.models import CONTENT_ACCESS_CAPABILITIES, ContentAccessGrant
from sitecore.models import Site, SiteLocale


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / 'db'
    / 'migrations'
    / '0024_content_access_grants.sql'
)
CAPABILITY_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / 'db'
    / 'migrations'
    / '0030_website_candidate_capability.sql'
)


class ContentAccessGrantModelContractTests(SimpleTestCase):
    def test_unmanaged_model_exposes_the_frozen_data_contract(self):
        self.assertFalse(ContentAccessGrant._meta.managed)
        self.assertEqual(ContentAccessGrant._meta.db_table, 'content_access_grant')
        self.assertEqual(
            {field.name for field in ContentAccessGrant._meta.local_fields},
            {
                'id',
                'user',
                'group',
                'site',
                'locale',
                'capability',
                'enabled',
                'granted_by_user',
                'reason',
                'created_at',
                'updated_at',
            },
        )
        self.assertEqual(
            set(CONTENT_ACCESS_CAPABILITIES),
            {
                'content.read',
                'content.write',
                'content.set_published',
                'content.locale.manage',
                'assets.read',
                'assets.write',
                'assets.import_local',
                'releases.read',
                'releases.preview_build',
                'releases.candidate_build',
            },
        )
        self.assertEqual(
            {constraint.name for constraint in ContentAccessGrant._meta.constraints},
            {
                'content_access_grant_subject_xor',
                'content_access_grant_capability_check',
                'uq_content_grant_user_site_active',
                'uq_content_grant_user_locale_active',
                'uq_content_grant_group_site_active',
                'uq_content_grant_group_locale_active',
            },
        )
        self.assertEqual(
            {index.name for index in ContentAccessGrant._meta.indexes},
            {
                'idx_content_grant_user_lookup',
                'idx_content_grant_group_lookup',
            },
        )

    def test_sql_migration_freezes_cross_site_and_active_uniqueness_guards(self):
        sql = MIGRATION_PATH.read_text(encoding='utf-8')

        self.assertIn('(user_id IS NULL) <> (group_id IS NULL)', sql)
        self.assertIn('FOREIGN KEY (site_id, locale_id)', sql)
        self.assertIn('REFERENCES site_locale(site_id, id)', sql)
        original_capabilities = set(CONTENT_ACCESS_CAPABILITIES) - {
            'releases.candidate_build'
        }
        for capability in original_capabilities:
            self.assertIn(f"'{capability}'", sql)
        for name in (
            'uq_content_grant_user_site_active',
            'uq_content_grant_user_locale_active',
            'uq_content_grant_group_site_active',
            'uq_content_grant_group_locale_active',
            'idx_content_grant_user_lookup',
            'idx_content_grant_group_lookup',
        ):
            self.assertIn(name, sql)

    def test_candidate_build_capability_is_an_append_only_constraint_upgrade(self):
        base = MIGRATION_PATH.read_text(encoding='utf-8')
        upgrade = CAPABILITY_MIGRATION_PATH.read_text(encoding='utf-8')

        self.assertNotIn("'releases.candidate_build'", base)
        self.assertIn(
            'DROP CONSTRAINT IF EXISTS content_access_grant_capability_check',
            upgrade,
        )
        for capability in CONTENT_ACCESS_CAPABILITIES:
            self.assertIn(f"'{capability}'", upgrade)


class ContentAccessGrantSQLiteConstraintTests(TestCase):
    unmanaged_models = (Site, SiteLocale, ContentAccessGrant)

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
            code='content_grant_test',
            name='Content grant test',
            default_locale='en',
        )
        self.other_site = Site.objects.create(
            code='content_grant_other',
            name='Content grant other',
            default_locale='en',
        )
        self.locale = SiteLocale.objects.create(
            site=self.site,
            locale_code='en',
            label='English',
            is_default=True,
        )
        self.other_locale = SiteLocale.objects.create(
            site=self.other_site,
            locale_code='en',
            label='English',
            is_default=True,
        )
        self.user = get_user_model().objects.create_user(username='grant-user')
        self.grantor = get_user_model().objects.create_user(username='grant-admin')
        self.group = Group.objects.create(name='Grant group')

    def assert_integrity_error(self, **values):
        capability = values.pop('capability', 'content.read')
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContentAccessGrant.objects.create(
                site=self.site,
                capability=capability,
                **values,
            )

    def test_subject_check_requires_exactly_one_user_or_group(self):
        self.assert_integrity_error()
        self.assert_integrity_error(user=self.user, group=self.group)

        user_grant = ContentAccessGrant.objects.create(
            user=self.user,
            site=self.site,
            capability='content.read',
            granted_by_user=self.grantor,
            reason='Direct test grant',
        )
        group_grant = ContentAccessGrant.objects.create(
            group=self.group,
            site=self.site,
            capability='content.read',
            granted_by_user=self.grantor,
            reason='Group test grant',
        )

        self.assertEqual(user_grant.user, self.user)
        self.assertIsNone(user_grant.group_id)
        self.assertEqual(group_grant.group, self.group)
        self.assertIsNone(group_grant.user_id)

    def test_capability_check_rejects_unknown_values(self):
        self.assert_integrity_error(user=self.user, capability='content.typo')

    def test_only_one_enabled_user_grant_exists_per_effective_scope(self):
        ContentAccessGrant.objects.create(
            user=self.user,
            site=self.site,
            capability='content.write',
        )
        self.assert_integrity_error(user=self.user, capability='content.write')

        ContentAccessGrant.objects.create(
            user=self.user,
            site=self.site,
            capability='content.write',
            enabled=False,
        )
        ContentAccessGrant.objects.create(
            user=self.user,
            site=self.site,
            capability='content.write',
            enabled=False,
        )

        ContentAccessGrant.objects.create(
            user=self.user,
            site=self.site,
            locale=self.locale,
            capability='content.write',
        )
        self.assert_integrity_error(
            user=self.user,
            locale=self.locale,
            capability='content.write',
        )

    def test_only_one_enabled_group_grant_exists_per_effective_scope(self):
        ContentAccessGrant.objects.create(
            group=self.group,
            site=self.site,
            capability='assets.read',
        )
        self.assert_integrity_error(group=self.group, capability='assets.read')

        ContentAccessGrant.objects.create(
            group=self.group,
            site=self.site,
            locale=self.locale,
            capability='assets.read',
        )
        self.assert_integrity_error(
            group=self.group,
            locale=self.locale,
            capability='assets.read',
        )
