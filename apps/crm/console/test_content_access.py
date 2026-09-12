from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import SimpleTestCase, TestCase

from console.access import (
    GROUP_BY_ROLE,
    ROLE_CONTENT_OPS,
    ROLE_MARKETING_OPS,
    ROLE_SYSTEM_ADMIN,
    _REQUEST_ROLE_KEYS_ATTR,
)
from console.content_access import (
    ALL_CONTENT_CAPABILITIES,
    ASSETS_IMPORT_LOCAL,
    ASSETS_READ,
    ASSETS_WRITE,
    CONTENT_LOCALE_MANAGE,
    CONTENT_READ,
    CONTENT_SET_PUBLISHED,
    CONTENT_WRITE,
    HIGH_RISK_CONTENT_CAPABILITIES,
    RELEASES_PREVIEW_BUILD,
    RELEASES_READ,
    effective_content_capabilities,
    has_content_capability,
    require_content_capability,
)
from console.models import CONTENT_ACCESS_CAPABILITIES, ContentAccessGrant
from sitecore.models import Site, SiteLocale


class ContentCapabilityConstantsTests(SimpleTestCase):
    def test_capability_contract_is_exact_and_immutable(self):
        expected = frozenset({
            'content.read',
            'content.write',
            'content.set_published',
            'content.locale.manage',
            'assets.read',
            'assets.write',
            'assets.import_local',
            'releases.read',
            'releases.preview_build',
        })

        self.assertIsInstance(ALL_CONTENT_CAPABILITIES, frozenset)
        self.assertEqual(ALL_CONTENT_CAPABILITIES, expected)
        self.assertEqual(
            ALL_CONTENT_CAPABILITIES,
            frozenset(CONTENT_ACCESS_CAPABILITIES),
        )
        self.assertEqual(
            HIGH_RISK_CONTENT_CAPABILITIES,
            frozenset({ASSETS_IMPORT_LOCAL, RELEASES_PREVIEW_BUILD}),
        )


class ContentAccessServiceTests(TestCase):
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
            code='content-access-a',
            name='Content access A',
            base_url='https://a.example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.other_site = Site.objects.create(
            code='content-access-b',
            name='Content access B',
            base_url='https://b.example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.locale_en = SiteLocale.objects.create(
            site=self.site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )
        self.locale_ar = SiteLocale.objects.create(
            site=self.site,
            locale_code='ar',
            label='Arabic',
            direction='rtl',
            is_default=False,
            enabled=True,
            sort_order=20,
        )
        self.other_locale = SiteLocale.objects.create(
            site=self.other_site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )

        self.content_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_CONTENT_OPS])
        self.marketing_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_MARKETING_OPS])
        self.admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        self.extra_group = Group.objects.create(name='Content Editors A')

        self.content_user = self._user('content-access-operator', self.content_group)
        self.marketing_user = self._user('content-access-marketing', self.marketing_group)
        self.admin_user = self._user('content-access-admin', self.admin_group)
        self.superuser = get_user_model().objects.create_superuser(
            username='content-access-superuser',
            password='LocalTestOnly!992',
            email='superuser@example.invalid',
        )

    @staticmethod
    def _user(username: str, *groups: Group):
        user = get_user_model().objects.create_user(
            username=username,
            password='LocalTestOnly!992',
        )
        user.groups.add(*groups)
        return user

    def _grant(
        self,
        capability: str,
        *,
        user=None,
        group=None,
        site=None,
        locale=None,
        enabled=True,
    ) -> ContentAccessGrant:
        return ContentAccessGrant.objects.create(
            user=user,
            group=group,
            site=site or self.site,
            locale=locale,
            capability=capability,
            enabled=enabled,
            granted_by_user=self.superuser,
            reason='authorization service test',
        )

    def test_direct_and_group_grants_are_merged_and_deduplicated(self):
        self.content_user.groups.add(self.extra_group)
        self._grant(CONTENT_READ, user=self.content_user)
        self._grant(CONTENT_READ, group=self.extra_group)
        self._grant(CONTENT_WRITE, group=self.content_group)

        capabilities = effective_content_capabilities(
            self.content_user,
            site=self.site,
        )

        self.assertIsInstance(capabilities, frozenset)
        self.assertEqual(capabilities, frozenset({CONTENT_READ, CONTENT_WRITE}))

    def test_disabled_and_wrong_site_grants_fail_closed(self):
        self._grant(CONTENT_READ, user=self.content_user, enabled=False)
        self._grant(CONTENT_WRITE, user=self.content_user, site=self.other_site)

        self.assertEqual(
            effective_content_capabilities(self.content_user, site=self.site),
            frozenset(),
        )

    def test_locale_request_accepts_matching_and_site_wide_grants_only(self):
        self._grant(CONTENT_READ, user=self.content_user)
        self._grant(CONTENT_WRITE, user=self.content_user, locale=self.locale_en)
        self._grant(CONTENT_SET_PUBLISHED, user=self.content_user, locale=self.locale_ar)

        self.assertEqual(
            effective_content_capabilities(
                self.content_user,
                site=self.site,
                locale=self.locale_en,
            ),
            frozenset({CONTENT_READ, CONTENT_WRITE}),
        )
        self.assertEqual(
            effective_content_capabilities(
                self.content_user,
                site=self.site,
                locale=self.locale_ar,
            ),
            frozenset({CONTENT_READ, CONTENT_SET_PUBLISHED}),
        )
        self.assertEqual(
            effective_content_capabilities(self.content_user, site=self.site),
            frozenset({CONTENT_READ}),
        )

    def test_cross_site_or_bare_locale_scope_fails_before_grant_lookup(self):
        self._grant(CONTENT_READ, user=self.content_user)
        setattr(
            self.content_user,
            _REQUEST_ROLE_KEYS_ATTR,
            frozenset({ROLE_CONTENT_OPS}),
        )

        with self.assertNumQueries(0):
            cross_site = effective_content_capabilities(
                self.content_user,
                site=self.site,
                locale=self.other_locale,
            )
            bare_locale = effective_content_capabilities(
                self.content_user,
                site=self.site.pk,
                locale=self.locale_en.pk,
            )

        self.assertEqual(cross_site, frozenset())
        self.assertEqual(bare_locale, frozenset())

    def test_invalid_identity_inputs_fail_closed_without_queries(self):
        unsaved_admin = get_user_model()(
            username='unsaved-system-admin',
            is_superuser=True,
        )

        with self.assertNumQueries(0):
            invalid_site = effective_content_capabilities(
                self.content_user,
                site='not-a-site-id',
            )
            unsaved_user = effective_content_capabilities(
                unsaved_admin,
                site=self.site,
            )

        self.assertEqual(invalid_site, frozenset())
        self.assertEqual(unsaved_user, frozenset())

    def test_capabilities_are_exact_and_never_inferred(self):
        self._grant(CONTENT_WRITE, user=self.content_user)
        self._grant(ASSETS_WRITE, user=self.content_user)
        self._grant(RELEASES_READ, user=self.content_user)

        capabilities = effective_content_capabilities(self.content_user, site=self.site)

        self.assertEqual(
            capabilities,
            frozenset({CONTENT_WRITE, ASSETS_WRITE, RELEASES_READ}),
        )
        for denied in (
            CONTENT_READ,
            CONTENT_SET_PUBLISHED,
            CONTENT_LOCALE_MANAGE,
            ASSETS_READ,
            ASSETS_IMPORT_LOCAL,
            RELEASES_PREVIEW_BUILD,
        ):
            with self.subTest(denied=denied):
                self.assertNotIn(denied, capabilities)

    def test_only_content_ops_or_system_admin_can_receive_content_access(self):
        unknown_group = Group.objects.create(name='Unregistered Console Role')
        unknown_user = self._user('content-access-unknown', unknown_group)
        anonymous = type('Anonymous', (), {
            'is_authenticated': False,
            'is_superuser': False,
        })()
        for user in (self.marketing_user, unknown_user):
            self._grant(CONTENT_READ, user=user)

        for user in (self.marketing_user, unknown_user, anonymous):
            with self.subTest(username=getattr(user, 'username', 'anonymous')):
                self.assertEqual(
                    effective_content_capabilities(user, site=self.site),
                    frozenset(),
                )

    def test_system_admin_and_superuser_keep_all_capabilities_without_grants(self):
        self.assertFalse(ContentAccessGrant.objects.exists())

        self.assertEqual(
            effective_content_capabilities(self.admin_user, site=self.site),
            ALL_CONTENT_CAPABILITIES,
        )
        with self.assertNumQueries(0):
            self.assertEqual(
                effective_content_capabilities(self.superuser, site=self.site),
                ALL_CONTENT_CAPABILITIES,
            )

    def test_helpers_check_exact_capability_and_require_server_authorization(self):
        self._grant(ASSETS_READ, user=self.content_user)

        self.assertTrue(
            has_content_capability(
                self.content_user,
                ASSETS_READ,
                site=self.site,
            )
        )
        self.assertFalse(
            has_content_capability(
                self.content_user,
                ASSETS_WRITE,
                site=self.site,
            )
        )
        self.assertFalse(
            has_content_capability(
                self.content_user,
                'assets.read_typo',
                site=self.site,
            )
        )
        self.assertTrue(
            require_content_capability(
                self.content_user,
                ASSETS_READ,
                site=self.site,
            )
        )
        with self.assertRaisesMessage(
            PermissionDenied,
            '当前账号没有在此站点执行该内容操作的权限。',
        ):
            require_content_capability(
                self.content_user,
                ASSETS_WRITE,
                site=self.site,
            )

    def test_content_operator_query_budget_is_one_grant_query_plus_role_resolution(self):
        self._grant(CONTENT_READ, group=self.content_group)

        with self.assertNumQueries(2):
            self.assertEqual(
                effective_content_capabilities(self.content_user, site=self.site),
                frozenset({CONTENT_READ}),
            )

        setattr(
            self.content_user,
            _REQUEST_ROLE_KEYS_ATTR,
            frozenset({ROLE_CONTENT_OPS}),
        )
        with self.assertNumQueries(1):
            self.assertEqual(
                effective_content_capabilities(self.content_user, site=self.site),
                frozenset({CONTENT_READ}),
            )
