from pathlib import Path
from unittest.mock import Mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.urls import resolve, reverse

from console.access import (
    GROUP_BY_ROLE,
    ROLE_CONTENT_OPS,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
)
from console.content_access import (
    ASSETS_READ,
    ASSETS_WRITE,
    CONTENT_READ,
    CONTENT_WRITE,
    RELEASES_PREVIEW_BUILD,
    RELEASES_READ,
)
from console.models import ContentAccessGrant
from console.navigation import (
    ADMIN_DOMAINS,
    active_domain_key,
    build_navigation,
    content_navigation_entries,
)
from console.views import navigation_payload
from sitecore.models import Site, SiteLocale


def role_user(role: str):
    user = Mock()
    user.pk = None
    user.is_authenticated = True
    user.is_superuser = False
    user.groups.values_list.return_value = [GROUP_BY_ROLE[role]]
    return user


class CanonicalNavigationTests(SimpleTestCase):
    def test_five_domain_order_and_canonical_entry_routes_are_frozen(self):
        self.assertEqual(
            [domain.key for domain in ADMIN_DOMAINS],
            ['workbench', 'sales', 'marketing', 'content', 'system'],
        )
        self.assertEqual(reverse('console:workbench'), '/admin/workbench/')
        self.assertEqual(reverse('console:lead_workspace_v2'), '/admin/sales/leads/')
        self.assertEqual(
            reverse('console:marketing_events'),
            '/admin/marketing/events/',
        )
        self.assertEqual(
            reverse('console:marketing_attribution'),
            '/admin/marketing/attribution/',
        )
        self.assertEqual(reverse('console:marketing_forms'), '/admin/marketing/forms/')
        self.assertEqual(
            reverse('console:content_articles'),
            '/admin/content/articles/',
        )
        self.assertEqual(reverse('console:system_users'), '/admin/system/users/')
        self.assertEqual(reverse('console:system_teams'), '/admin/system/teams/')
        self.assertEqual(reverse('console:system_email'), '/admin/system/email/')

    def test_navigation_requires_an_effective_grant_beyond_the_content_role(self):
        expected = {
            ROLE_SALES: ['workbench', 'sales'],
            ROLE_SALES_MANAGER: ['workbench', 'sales'],
            ROLE_MARKETING_OPS: ['workbench', 'marketing'],
            ROLE_CONTENT_OPS: ['workbench'],
            ROLE_SYSTEM_ADMIN: [
                'workbench',
                'sales',
                'marketing',
                'content',
                'system',
            ],
        }

        for role, keys in expected.items():
            with self.subTest(role=role):
                self.assertEqual(
                    [item['key'] for item in build_navigation('workbench', role_user(role))],
                    keys,
                )

        marketing_navigation = build_navigation(
            'marketing_events', role_user(ROLE_MARKETING_OPS),
        )
        marketing_item = next(
            item for item in marketing_navigation if item['key'] == 'marketing'
        )
        self.assertEqual(marketing_item['href'], reverse('console:marketing_events'))
        self.assertTrue(marketing_item['active'])

    def test_legacy_page_keys_activate_one_canonical_domain(self):
        expected = {
            'home': 'workbench',
            'workbench': 'workbench',
            'sales': 'sales',
            'leads': 'sales',
            'opportunities': 'sales',
            'marketing': 'marketing',
            'marketing_forms': 'marketing',
            'marketing_privacy': 'marketing',
            'articles': 'content',
            'assets': 'content',
            'releases': 'content',
            'system': 'system',
            'system_users': 'system',
            'system_teams': 'system',
            'system_email': 'system',
            'system_data_governance': 'system',
        }
        for legacy_key, domain_key in expected.items():
            with self.subTest(legacy_key=legacy_key):
                self.assertEqual(active_domain_key(legacy_key), domain_key)

        admin_navigation = build_navigation('assets', role_user(ROLE_SYSTEM_ADMIN))
        self.assertEqual(
            [item['key'] for item in admin_navigation if item['active']],
            ['content'],
        )
        content_item = next(item for item in admin_navigation if item['key'] == 'content')
        self.assertEqual(
            [child['key'] for child in content_item['children']],
            ['articles', 'assets', 'releases'],
        )
        self.assertEqual(content_item['children'][2]['label'], '预览与快照')
        self.assertEqual(
            [child['key'] for child in content_item['children'] if child['active']],
            ['assets'],
        )

        system_navigation = build_navigation('system_teams', role_user(ROLE_SYSTEM_ADMIN))
        system_item = next(item for item in system_navigation if item['key'] == 'system')
        self.assertEqual(
            [child['key'] for child in system_item['children']],
            ['system_users', 'system_teams', 'system_email', 'system_data_governance'],
        )
        self.assertEqual(
            [child['key'] for child in system_item['children'] if child['active']],
            ['system_teams'],
        )

        marketing_navigation = build_navigation(
            'marketing_forms', role_user(ROLE_MARKETING_OPS)
        )
        marketing_item = next(
            item for item in marketing_navigation if item['key'] == 'marketing'
        )
        self.assertEqual(
            [child['key'] for child in marketing_item['children']],
            ['marketing_events', 'marketing_forms', 'marketing_privacy', 'marketing'],
        )
        self.assertEqual(
            [child['key'] for child in marketing_item['children'] if child['active']],
            ['marketing_forms'],
        )

    def test_legacy_renderer_delegates_to_the_same_navigation_builder(self):
        user = role_user(ROLE_SYSTEM_ADMIN)
        self.assertEqual(
            navigation_payload('leads', user),
            build_navigation('leads', user),
        )

    def test_canonical_aliases_resolve_only_to_existing_get_renderers(self):
        self.assertEqual(resolve('/admin/workbench/').url_name, 'workbench')
        self.assertEqual(
            resolve('/admin/marketing/attribution/').url_name,
            'marketing_attribution',
        )
        self.assertEqual(
            resolve('/admin/marketing/events/').url_name,
            'marketing_events',
        )
        self.assertEqual(
            resolve('/admin/marketing/privacy/').url_name,
            'marketing_privacy',
        )
        self.assertEqual(
            resolve('/admin/system/data-governance/').url_name,
            'system_data_governance',
        )
        self.assertEqual(
            resolve('/admin/content/articles/').url_name,
            'content_articles',
        )


class NavigationResponsiveCssTests(SimpleTestCase):
    def test_legacy_shell_keeps_the_full_navigation_hierarchy_at_1280(self):
        css_path = Path(settings.BASE_DIR) / 'console' / 'static' / 'console' / 'admin-shell.css'
        css = css_path.read_text(encoding='utf-8')
        desktop_compact_block = css.split('@media (max-width: 1320px) {', 1)[1].split(
            '@media (max-width: 920px) {',
            1,
        )[0]

        self.assertNotIn('grid-template-columns: 72px minmax(0, 1fr)', desktop_compact_block)
        self.assertNotIn('.nav-copy', desktop_compact_block)
        self.assertNotIn('.nav-item', desktop_compact_block)
        self.assertIn('.editor-layout', desktop_compact_block)

    def test_mobile_drawer_restores_children_after_a_desktop_collapse(self):
        css_path = Path(settings.BASE_DIR) / 'console' / 'static' / 'console' / 'admin-shell.css'
        css = css_path.read_text(encoding='utf-8')
        mobile_block = css.split('@media (max-width: 920px) {', 1)[1]
        restored_children_rule = mobile_block.split(
            '.app.is-collapsed .nav-children {',
            1,
        )[1].split('}', 1)[0]

        self.assertIn('display: grid', restored_children_rule)

    def test_both_shells_expose_grouped_children_and_a_stateful_legacy_toggle(self):
        template_root = Path(settings.BASE_DIR) / 'console' / 'templates' / 'console'
        legacy_shell = (template_root / 'app_shell.html').read_text(encoding='utf-8')
        v2_shell = (template_root / 'v2' / 'base.html').read_text(encoding='utf-8')
        legacy_script = (
            Path(settings.BASE_DIR) / 'console' / 'static' / 'console' / 'admin-shell.js'
        ).read_text(encoding='utf-8')

        self.assertIn('class="nav-children" role="group"', legacy_shell)
        self.assertIn('class="nc-nav-children" role="group"', v2_shell)
        self.assertIn('id="toggleNav"', legacy_shell)
        self.assertIn('aria-pressed="false"', legacy_shell)
        self.assertIn("navToggle.setAttribute('aria-pressed'", legacy_script)
        self.assertIn("collapsed ? '展开主导航' : '收起主导航'", legacy_script)
        self.assertIn("navToggle.setAttribute('aria-label', '关闭主导航')", legacy_script)


class CanonicalNavigationQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        cls.user = get_user_model().objects.create_user(
            username='navigation-query-admin',
            password='LocalOnly!234',
        )
        cls.user.groups.add(group)

    def test_navigation_resolves_role_groups_at_most_once(self):
        # Reload the model so no related-manager or request cache can mask the
        # direct build_navigation() query budget.
        user = get_user_model().objects.get(pk=self.user.pk)

        with self.assertNumQueries(1):
            navigation = build_navigation('workbench', user)

        self.assertEqual(
            [item['key'] for item in navigation],
            ['workbench', 'sales', 'marketing', 'content', 'system'],
        )


class ContentGrantNavigationTests(TestCase):
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
        self.content_group = Group.objects.create(
            name=GROUP_BY_ROLE[ROLE_CONTENT_OPS],
        )
        self.marketing_group = Group.objects.create(
            name=GROUP_BY_ROLE[ROLE_MARKETING_OPS],
        )
        self.extra_group = Group.objects.create(name='Navigation content readers')
        self.content_user = self._user('navigation-content', self.content_group)
        self.marketing_user = self._user(
            'navigation-marketing',
            self.marketing_group,
            self.extra_group,
        )
        self.grantor = get_user_model().objects.create_user(
            username='navigation-grantor',
        )

        self.site = Site.objects.create(
            code='siteos_demo',
            name='Reachable console site',
            base_url='https://console.example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.other_site = Site.objects.create(
            code='unreachable_navigation_site',
            name='Unreachable console site',
            base_url='https://other.example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.locale_en = self._locale(self.site, 'en', sort_order=10, default=True)
        self.locale_ar = self._locale(self.site, 'ar', sort_order=20)
        self.other_locale = self._locale(
            self.other_site,
            'en',
            sort_order=10,
            default=True,
        )

    @staticmethod
    def _user(username: str, *groups: Group):
        user = get_user_model().objects.create_user(username=username)
        user.groups.add(*groups)
        return user

    @staticmethod
    def _locale(site: Site, code: str, *, sort_order: int, default: bool = False):
        return SiteLocale.objects.create(
            site=site,
            locale_code=code,
            label=code.upper(),
            direction='rtl' if code == 'ar' else 'ltr',
            is_default=default,
            enabled=True,
            sort_order=sort_order,
        )

    def _grant(
        self,
        capability: str,
        *,
        user=None,
        group=None,
        site=None,
        locale=None,
        enabled: bool = True,
    ) -> ContentAccessGrant:
        return ContentAccessGrant.objects.create(
            user=user,
            group=group,
            site=site or self.site,
            locale=locale,
            capability=capability,
            enabled=enabled,
            granted_by_user=self.grantor,
            reason='navigation authorization test',
        )

    @staticmethod
    def _content_item(navigation):
        return next(
            (item for item in navigation if item['key'] == 'content'),
            None,
        )

    def test_content_operator_without_read_grant_has_no_content_domain(self):
        with self.assertNumQueries(2):
            navigation = build_navigation('workbench', self.content_user)

        self.assertEqual([item['key'] for item in navigation], ['workbench'])
        self.assertEqual(
            content_navigation_entries(self.content_user),
            [],
        )

    def test_exact_read_grants_expose_only_usable_second_level_entries(self):
        self._grant(CONTENT_READ, user=self.content_user, locale=self.locale_ar)
        self._grant(ASSETS_READ, group=self.content_group)
        self._grant(RELEASES_READ, user=self.content_user)

        navigation = build_navigation('assets', self.content_user)
        content_item = self._content_item(navigation)

        self.assertIsNotNone(content_item)
        self.assertTrue(content_item['active'])
        self.assertEqual(
            content_item['href'],
            f'{reverse("console:content_articles")}?locale=ar',
        )
        self.assertEqual(
            [child['key'] for child in content_item['children']],
            ['articles', 'assets', 'releases'],
        )
        self.assertEqual(
            [child['href'] for child in content_item['children']],
            [
                f'{reverse("console:content_articles")}?locale=ar',
                reverse('console:assets'),
                reverse('console:releases'),
            ],
        )
        self.assertEqual(
            [child['key'] for child in content_item['children'] if child['active']],
            ['assets'],
        )

    def test_site_wide_article_read_prefers_the_canonical_default_locale_url(self):
        self._grant(CONTENT_READ, user=self.content_user, locale=self.locale_ar)
        self._grant(CONTENT_READ, group=self.content_group)

        content_item = self._content_item(
            build_navigation('content_articles', self.content_user),
        )

        self.assertEqual(content_item['href'], reverse('console:content_articles'))
        self.assertEqual(
            content_item['children'][0]['href'],
            reverse('console:content_articles'),
        )
        self.assertTrue(content_item['children'][0]['active'])

    def test_write_and_high_risk_grants_never_imply_a_read_entrance(self):
        self._grant(CONTENT_WRITE, user=self.content_user)
        self._grant(ASSETS_WRITE, user=self.content_user)
        self._grant(ASSETS_READ, user=self.content_user, locale=self.locale_en)
        self._grant(RELEASES_PREVIEW_BUILD, user=self.content_user)

        navigation = build_navigation('workbench', self.content_user)

        self.assertIsNone(self._content_item(navigation))

    def test_wrong_site_disabled_and_non_content_role_grants_fail_closed(self):
        self._grant(
            CONTENT_READ,
            user=self.content_user,
            site=self.other_site,
            locale=self.other_locale,
        )
        self._grant(CONTENT_READ, user=self.content_user, enabled=False)
        self._grant(ASSETS_READ, group=self.extra_group)

        self.assertIsNone(
            self._content_item(build_navigation('workbench', self.content_user)),
        )
        self.assertIsNone(
            self._content_item(build_navigation('workbench', self.marketing_user)),
        )

    def test_first_usable_entry_becomes_the_content_domain_landing(self):
        self._grant(ASSETS_READ, user=self.content_user)

        content_item = self._content_item(
            build_navigation('workbench', self.content_user),
        )

        self.assertEqual(content_item['href'], reverse('console:assets'))
        self.assertEqual(
            [child['key'] for child in content_item['children']],
            ['assets'],
        )
