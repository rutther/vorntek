from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import HttpResponse
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from console import views
from console.article_workspace import article_workspace_bundle
from console.access import (
    CONTENT_VIEW_NAMES,
    GROUP_BY_ROLE,
    ROLE_CONTENT_OPS,
    ROLE_SYSTEM_ADMIN,
)
from console.content_access import (
    ASSETS_IMPORT_LOCAL,
    ASSETS_READ,
    ASSETS_WRITE,
    CONTENT_LOCALE_MANAGE,
    CONTENT_READ,
    CONTENT_SET_PUBLISHED,
    CONTENT_WRITE,
    RELEASES_PREVIEW_BUILD,
    RELEASES_READ,
)
from console.models import ContentAccessGrant
from sitecore.models import (
    Article,
    Category,
    MediaAsset,
    MediaAssetBinding,
    PageRoute,
    Site,
    SiteLocale,
)


EXPECTED_CONTENT_ROUTE_CAPABILITIES = {
    'content_articles': frozenset({CONTENT_READ}),
    'articles': frozenset({CONTENT_READ}),
    'article_import': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'article_export': frozenset({CONTENT_READ}),
    'article_locale_create': frozenset({CONTENT_LOCALE_MANAGE, CONTENT_SET_PUBLISHED}),
    'article_locale_enable': frozenset({CONTENT_LOCALE_MANAGE, CONTENT_SET_PUBLISHED}),
    'article_locale_disable': frozenset({CONTENT_LOCALE_MANAGE, CONTENT_SET_PUBLISHED}),
    'article_locale_make_default': frozenset({CONTENT_LOCALE_MANAGE, CONTENT_SET_PUBLISHED}),
    'article_categories': frozenset({CONTENT_READ}),
    'category_create': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'category_edit': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'article_create': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'article_edit': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'assets': frozenset({ASSETS_READ}),
    'assets_three_d': frozenset({ASSETS_READ}),
    'three_d_asset_detail': frozenset({ASSETS_READ}),
    'three_d_profile_create': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'three_d_profile_edit': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'three_d_placement_create': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'three_d_placement_edit': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'asset_upload': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'asset_import_path': frozenset({ASSETS_READ, ASSETS_WRITE, ASSETS_IMPORT_LOCAL}),
    'asset_file': frozenset({ASSETS_READ}),
    'releases': frozenset({RELEASES_READ}),
    'release_build_preview': frozenset({RELEASES_PREVIEW_BUILD}),
}


CONTENT_ROUTE_CASES = (
    ('content_articles', (), 'get'),
    ('articles', (), 'get'),
    ('article_import', (), 'get'),
    ('article_export', (), 'get'),
    ('article_locale_create', (), 'get'),
    ('article_locale_enable', (999_991,), 'post'),
    ('article_locale_disable', (999_992,), 'post'),
    ('article_locale_make_default', (999_993,), 'post'),
    ('article_categories', (), 'get'),
    ('category_create', (), 'get'),
    ('category_edit', (999_994,), 'get'),
    ('article_create', (), 'get'),
    ('article_edit', (999_995,), 'get'),
    ('assets', (), 'get'),
    ('assets_three_d', (), 'get'),
    ('three_d_asset_detail', (999_996,), 'get'),
    ('three_d_profile_create', (), 'get'),
    ('three_d_profile_edit', (999_997,), 'get'),
    ('three_d_placement_create', (), 'get'),
    ('three_d_placement_edit', (999_998,), 'get'),
    ('asset_upload', (), 'get'),
    ('asset_import_path', (), 'get'),
    ('asset_file', (999_999,), 'get'),
    ('releases', (), 'get'),
    ('release_build_preview', (), 'post'),
)


class ContentRoutePolicyContractTests(SimpleTestCase):
    def test_explicit_policy_registry_exactly_covers_content_view_names(self):
        registry = getattr(views, 'CONTENT_ROUTE_CAPABILITIES', None)

        self.assertIsNotNone(
            registry,
            'console.views must expose the reviewed CONTENT_ROUTE_CAPABILITIES registry.',
        )
        normalized = {
            route_name: frozenset(capabilities)
            for route_name, capabilities in registry.items()
        }
        self.assertEqual(normalized, EXPECTED_CONTENT_ROUTE_CAPABILITIES)
        self.assertEqual(set(normalized), set(CONTENT_VIEW_NAMES))
        self.assertEqual(len(normalized), 25)

    def test_every_policy_entry_resolves_to_the_named_console_route(self):
        route_cases = {name: args for name, args, _method in CONTENT_ROUTE_CASES}
        self.assertEqual(set(route_cases), set(EXPECTED_CONTENT_ROUTE_CAPABILITIES))

        for route_name, args in route_cases.items():
            with self.subTest(route_name=route_name):
                path = reverse(f'console:{route_name}', args=args)
                self.assertTrue(path.startswith('/admin/'))


class ContentRouteCapabilityTests(TestCase):
    """Route-level authorization checks against a minimal unmanaged schema."""

    unmanaged_models = (
        Site,
        SiteLocale,
        ContentAccessGrant,
        PageRoute,
        Category,
        MediaAsset,
        Article,
        MediaAssetBinding,
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
            code='siteos_demo',
            name='Content route site',
            base_url='https://content-route.example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.other_site = Site.objects.create(
            code='content_route_other',
            name='Other content route site',
            base_url='https://other-content-route.example.invalid',
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
            label='العربية',
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

        content_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_CONTENT_OPS])
        admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        self.content_user = get_user_model().objects.create_user(
            username='content-route-operator',
            password='LocalTestOnly!4937',
        )
        self.content_user.groups.add(content_group)
        self.admin_user = get_user_model().objects.create_user(
            username='content-route-admin',
            password='LocalTestOnly!4937',
        )
        self.admin_user.groups.add(admin_group)

    def grant(self, capability: str, *, locale: SiteLocale | None = None, site: Site | None = None):
        return ContentAccessGrant.objects.create(
            user=self.content_user,
            site=site or self.site,
            locale=locale,
            capability=capability,
            enabled=True,
            granted_by_user=self.admin_user,
            reason='route capability test',
        )

    @staticmethod
    def _capturing_renderer(storage: dict, context_key: str):
        def render(_request, **kwargs):
            storage[context_key] = kwargs.get('extra_context', {})
            return HttpResponse('rendered')

        return render

    def test_content_operator_without_grants_gets_403_on_all_25_routes_without_side_effects(self):
        self.client.force_login(self.content_user)
        locale_state = list(
            SiteLocale.objects.filter(site=self.site)
            .order_by('id')
            .values_list('id', 'locale_code', 'enabled', 'is_default')
        )
        domain_counts = {
            'routes': PageRoute.objects.count(),
            'categories': Category.objects.count(),
            'articles': Article.objects.count(),
            'assets': MediaAsset.objects.count(),
        }

        with (
            patch('console.views.enable_locale') as enable_locale,
            patch('console.views.disable_locale') as disable_locale,
            patch('console.views.set_default_locale') as set_default_locale,
            patch('console.views.create_locale_from_source') as create_locale,
            patch('console.views.run_preview_build') as preview_build,
        ):
            for route_name, args, method in CONTENT_ROUTE_CASES:
                with self.subTest(route_name=route_name):
                    url = f'{reverse(f"console:{route_name}", args=args)}?locale=en'
                    response = getattr(self.client, method)(url, {})
                    self.assertEqual(response.status_code, 403)

        enable_locale.assert_not_called()
        disable_locale.assert_not_called()
        set_default_locale.assert_not_called()
        create_locale.assert_not_called()
        preview_build.assert_not_called()
        self.assertEqual(
            list(
                SiteLocale.objects.filter(site=self.site)
                .order_by('id')
                .values_list('id', 'locale_code', 'enabled', 'is_default')
            ),
            locale_state,
        )
        self.assertEqual(PageRoute.objects.count(), domain_counts['routes'])
        self.assertEqual(Category.objects.count(), domain_counts['categories'])
        self.assertEqual(Article.objects.count(), domain_counts['articles'])
        self.assertEqual(MediaAsset.objects.count(), domain_counts['assets'])

    def test_read_grant_is_limited_to_the_exact_locale_and_workspace_hides_other_locale(self):
        self.grant(CONTENT_READ, locale=self.locale_en)
        self.client.force_login(self.content_user)
        workspace = {'locale': {'code': 'en'}}

        with (
            patch('console.views.article_workspace_bundle', return_value=({}, workspace)) as bundle,
            patch('console.views.render_form_console', return_value=HttpResponse('ok')),
        ):
            allowed = self.client.get(f'{reverse("console:articles")}?locale=en')
            denied = self.client.get(f'{reverse("console:articles")}?locale=ar')

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        bundle.assert_called_once()
        self.assertEqual(
            bundle.call_args.kwargs['visible_locale_ids'],
            frozenset({self.locale_en.id}),
        )

    def test_site_wide_asset_route_rejects_locale_only_grant(self):
        self.grant(ASSETS_READ, locale=self.locale_en)
        self.client.force_login(self.content_user)

        with (
            patch('console.views.assets_payload', return_value={}) as payload,
            patch('console.views.render_console', return_value=HttpResponse('ok')),
        ):
            denied = self.client.get(f'{reverse("console:assets")}?locale=en')
            self.grant(ASSETS_READ)
            allowed = self.client.get(f'{reverse("console:assets")}?locale=en')

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        payload.assert_called_once()

    def test_article_editor_asset_tools_follow_site_wide_read_and_write_capabilities(self):
        self.grant(CONTENT_READ, locale=self.locale_en)
        self.grant(CONTENT_WRITE, locale=self.locale_en)
        self.client.force_login(self.content_user)
        url = f'{reverse("console:article_create")}?locale=en'

        no_asset_grant = self.client.get(url)

        self.assertEqual(no_asset_grant.status_code, 200)
        self.assertFalse(no_asset_grant.context['canReadAssets'])
        self.assertFalse(no_asset_grant.context['canWriteAssets'])
        self.assertNotIn('cover_asset', no_asset_grant.context['editor_form'].fields)
        self.assertNotContains(no_asset_grant, '查看资产库')
        self.assertNotContains(no_asset_grant, '上传新资产')

        # Locale-scoped asset grants cannot authorize site-wide asset routes or
        # advertise their controls from the locale-scoped article editor.
        self.grant(ASSETS_READ, locale=self.locale_en)
        self.grant(ASSETS_WRITE, locale=self.locale_en)
        locale_only_assets = self.client.get(url)

        self.assertFalse(locale_only_assets.context['canReadAssets'])
        self.assertFalse(locale_only_assets.context['canWriteAssets'])
        self.assertNotContains(locale_only_assets, '查看资产库')
        self.assertNotContains(locale_only_assets, '上传新资产')

        self.grant(ASSETS_READ)
        asset_reader = self.client.get(url)

        self.assertTrue(asset_reader.context['canReadAssets'])
        self.assertFalse(asset_reader.context['canWriteAssets'])
        self.assertIn('cover_asset', asset_reader.context['editor_form'].fields)
        self.assertContains(asset_reader, '查看资产库')
        self.assertNotContains(asset_reader, '上传新资产')

        self.grant(ASSETS_WRITE)
        asset_writer = self.client.get(url)

        self.assertTrue(asset_writer.context['canReadAssets'])
        self.assertTrue(asset_writer.context['canWriteAssets'])
        self.assertContains(asset_writer, '查看资产库')
        self.assertContains(asset_writer, '上传新资产')

    def test_article_edit_never_crosses_locale_or_site_scope(self):
        self.grant(CONTENT_READ, locale=self.locale_en)
        self.grant(CONTENT_WRITE, locale=self.locale_en)
        arabic_route = PageRoute.objects.create(
            site=self.site,
            locale=self.locale_ar,
            path='/ar/articles/arabic-only/',
            route_type='article',
            status='draft',
            page_title='Arabic only',
        )
        arabic_article = Article.objects.create(
            route=arabic_route,
            title='Arabic only',
            slug='arabic-only',
            status='draft',
        )
        other_route = PageRoute.objects.create(
            site=self.other_site,
            locale=self.other_locale,
            path='/en/articles/other-site/',
            route_type='article',
            status='draft',
            page_title='Other site',
        )
        other_article = Article.objects.create(
            route=other_route,
            title='Other site',
            slug='other-site',
            status='draft',
        )
        self.client.force_login(self.content_user)

        for article in (arabic_article, other_article):
            with self.subTest(article_id=article.id):
                response = self.client.get(
                    f'{reverse("console:article_edit", args=[article.id])}?locale=en'
                )
                self.assertEqual(response.status_code, 404)

    def test_enabling_a_locale_requires_manage_and_set_published_together(self):
        self.locale_ar.enabled = False
        self.locale_ar.save(update_fields=['enabled', 'updated_at'])
        self.grant(CONTENT_LOCALE_MANAGE)
        self.client.force_login(self.content_user)
        url = reverse('console:article_locale_enable', args=[self.locale_ar.id])

        denied = self.client.post(url)

        self.assertEqual(denied.status_code, 403)
        self.locale_ar.refresh_from_db()
        self.assertFalse(self.locale_ar.enabled)

        self.grant(CONTENT_SET_PUBLISHED)
        allowed = self.client.post(url)

        self.assertEqual(allowed.status_code, 302)
        self.locale_ar.refresh_from_db()
        self.assertTrue(self.locale_ar.enabled)

    def test_disabling_a_locale_requires_manage_and_set_published_together(self):
        self.grant(CONTENT_LOCALE_MANAGE)
        self.client.force_login(self.content_user)
        url = reverse('console:article_locale_disable', args=[self.locale_ar.id])

        denied = self.client.post(url)

        self.assertEqual(denied.status_code, 403)
        self.locale_ar.refresh_from_db()
        self.assertTrue(self.locale_ar.enabled)

        self.grant(CONTENT_SET_PUBLISHED)
        allowed = self.client.post(url)

        self.assertEqual(allowed.status_code, 302)
        self.locale_ar.refresh_from_db()
        self.assertFalse(self.locale_ar.enabled)

    def test_published_category_requires_set_published_in_addition_to_write(self):
        self.grant(CONTENT_READ, locale=self.locale_en)
        self.grant(CONTENT_WRITE, locale=self.locale_en)
        self.client.force_login(self.content_user)
        captured: dict[str, object] = {}
        data = {
            'name': 'Published category',
            'code': 'published_category',
            'slug': 'published-category',
            'status': 'published',
            'sort_order': '10',
        }

        with patch(
            'console.views.render_form_console',
            side_effect=self._capturing_renderer(captured, 'first'),
        ):
            denied = self.client.post(
                f'{reverse("console:category_create")}?locale=en',
                data,
            )

        self.assertEqual(denied.status_code, 200)
        self.assertFalse(Category.objects.exists())
        denied_form = captured['first']['category_form']
        self.assertIn('status', denied_form.errors)

        self.grant(CONTENT_SET_PUBLISHED, locale=self.locale_en)
        allowed = self.client.post(
            f'{reverse("console:category_create")}?locale=en',
            data,
        )

        self.assertEqual(allowed.status_code, 302)
        category = Category.objects.get(code='published_category')
        self.assertEqual(category.status, 'published')

    def test_published_article_requires_set_published_in_addition_to_write(self):
        self.grant(CONTENT_READ, locale=self.locale_en)
        self.grant(CONTENT_WRITE, locale=self.locale_en)
        self.client.force_login(self.content_user)
        captured: dict[str, object] = {}
        data = {
            'title': 'Published article',
            'slug': 'published-article',
            'status': 'published',
            'reading_minutes': '3',
            'robots': 'index,follow',
        }

        with patch(
            'console.views.render_form_console',
            side_effect=self._capturing_renderer(captured, 'first'),
        ):
            denied = self.client.post(
                f'{reverse("console:article_create")}?locale=en',
                data,
            )

        self.assertEqual(denied.status_code, 200)
        self.assertFalse(PageRoute.objects.exists())
        self.assertFalse(Article.objects.exists())
        denied_form = captured['first']['editor_form']
        self.assertIn('status', denied_form.errors)

        self.grant(CONTENT_SET_PUBLISHED, locale=self.locale_en)
        allowed = self.client.post(
            f'{reverse("console:article_create")}?locale=en',
            data,
        )

        self.assertEqual(allowed.status_code, 302)
        self.assertIn('/admin/articles/', allowed.url)
        self.assertTrue(Article.objects.exists(), allowed.url)
        article = Article.objects.select_related('route').get(slug='published_article')
        self.assertEqual(article.status, 'published')
        self.assertEqual(article.route.status, 'published')

    def test_article_workspace_hides_controls_that_the_publish_policy_would_reject(self):
        category = Category.objects.create(
            site=self.site,
            locale=self.locale_en,
            code='published_workspace_category',
            name='Published workspace category',
            slug='published_workspace_category',
            status='published',
        )
        route = PageRoute.objects.create(
            site=self.site,
            locale=self.locale_en,
            path='/en/articles/published_workspace_article/',
            route_type='article',
            status='published',
            page_title='Published workspace article',
        )
        article = Article.objects.create(
            route=route,
            category=category,
            title='Published workspace article',
            slug='published_workspace_article',
            status='published',
        )

        _page, restricted = article_workspace_bundle(
            'en',
            capabilities=frozenset({CONTENT_READ, CONTENT_WRITE}),
            site_capabilities=frozenset({CONTENT_LOCALE_MANAGE}),
            visible_locale_ids=frozenset({self.locale_en.id}),
        )

        restricted_article = next(row for row in restricted['articles'] if row['id'] == article.id)
        restricted_category = next(row for row in restricted['categories'] if row['id'] == category.id)
        restricted_locale = next(
            row for row in restricted['localeManager']['items'] if row['id'] == self.locale_ar.id
        )
        self.assertEqual(restricted_article['editHref'], '')
        self.assertEqual(restricted_category['editHref'], '')
        self.assertEqual(restricted_locale['disableHref'], '')

        _page, authorized = article_workspace_bundle(
            'en',
            capabilities=frozenset({CONTENT_READ, CONTENT_WRITE, CONTENT_SET_PUBLISHED}),
            site_capabilities=frozenset({CONTENT_LOCALE_MANAGE, CONTENT_SET_PUBLISHED}),
            visible_locale_ids=frozenset({self.locale_en.id}),
        )

        authorized_article = next(row for row in authorized['articles'] if row['id'] == article.id)
        authorized_category = next(row for row in authorized['categories'] if row['id'] == category.id)
        authorized_locale = next(
            row for row in authorized['localeManager']['items'] if row['id'] == self.locale_ar.id
        )
        self.assertIn(f'/admin/articles/{article.id}/', authorized_article['editHref'])
        self.assertIn(f'/admin/articles/categories/{category.id}/', authorized_category['editHref'])
        self.assertEqual(
            authorized_locale['disableHref'],
            reverse('console:article_locale_disable', args=[self.locale_ar.id]),
        )

    def test_article_import_cannot_smuggle_published_front_matter_without_second_grant(self):
        self.grant(CONTENT_READ, locale=self.locale_en)
        self.grant(CONTENT_WRITE, locale=self.locale_en)
        self.client.force_login(self.content_user)
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                'published.md',
                '---\ntitle: Imported published\nslug: imported-published\nstatus: published\n---\nBody',
            )
        upload = SimpleUploadedFile(
            'articles.zip',
            archive_buffer.getvalue(),
            content_type='application/zip',
        )
        captured: dict[str, object] = {}

        with patch(
            'console.views.render_form_console',
            side_effect=self._capturing_renderer(captured, 'first'),
        ):
            response = self.client.post(
                f'{reverse("console:article_import")}?locale=en',
                {'default_status': 'draft', 'import_file': upload},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Article.objects.exists())
        denied_form = captured['first']['import_form']
        self.assertIn('import_file', denied_form.errors)

    def test_article_import_cannot_modify_or_unpublish_existing_published_article_without_second_grant(self):
        route = PageRoute.objects.create(
            site=self.site,
            locale=self.locale_en,
            path='/en/articles/protected_published/',
            route_type='article',
            status='published',
            page_title='Protected published',
        )
        article = Article.objects.create(
            route=route,
            title='Protected published',
            slug='protected_published',
            body_markdown='Original published body',
            status='published',
        )
        self.grant(CONTENT_READ, locale=self.locale_en)
        self.grant(CONTENT_WRITE, locale=self.locale_en)
        self.client.force_login(self.content_user)

        for requested_status in ('draft', 'archived'):
            with self.subTest(requested_status=requested_status):
                archive_buffer = BytesIO()
                with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr(
                        'protected-published.md',
                        '---\n'
                        'title: Unauthorized replacement\n'
                        'slug: protected_published\n'
                        f'status: {requested_status}\n'
                        '---\n'
                        'Unauthorized replacement body',
                    )
                upload = SimpleUploadedFile(
                    'articles.zip',
                    archive_buffer.getvalue(),
                    content_type='application/zip',
                )

                response = self.client.post(
                    f'{reverse("console:article_import")}?locale=en',
                    {'default_status': 'draft', 'import_file': upload},
                )

                self.assertEqual(response.status_code, 200)
                article.refresh_from_db()
                route.refresh_from_db()
                self.assertEqual(article.title, 'Protected published')
                self.assertEqual(article.body_markdown, 'Original published body')
                self.assertEqual(article.status, 'published')
                self.assertEqual(route.status, 'published')
                self.assertEqual(response.context['import_result']['updated'], 0)
                self.assertEqual(response.context['import_result']['failed'], 1)

    def test_article_import_can_update_existing_published_article_with_publish_capability(self):
        route = PageRoute.objects.create(
            site=self.site,
            locale=self.locale_en,
            path='/en/articles/authorized_published/',
            route_type='article',
            status='published',
            page_title='Authorized published',
        )
        article = Article.objects.create(
            route=route,
            title='Authorized published',
            slug='authorized_published',
            body_markdown='Original body',
            status='published',
        )
        self.grant(CONTENT_READ, locale=self.locale_en)
        self.grant(CONTENT_WRITE, locale=self.locale_en)
        self.grant(CONTENT_SET_PUBLISHED, locale=self.locale_en)
        self.client.force_login(self.content_user)
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                'authorized-published.md',
                '---\n'
                'title: Authorized replacement\n'
                'slug: authorized_published\n'
                'status: published\n'
                '---\n'
                'Authorized replacement body',
            )
        upload = SimpleUploadedFile(
            'articles.zip',
            archive_buffer.getvalue(),
            content_type='application/zip',
        )

        response = self.client.post(
            f'{reverse("console:article_import")}?locale=en',
            {'default_status': 'draft', 'import_file': upload},
        )

        self.assertEqual(response.status_code, 200)
        article.refresh_from_db()
        route.refresh_from_db()
        self.assertEqual(article.title, 'Authorized replacement')
        self.assertEqual(article.body_markdown, 'Authorized replacement body')
        self.assertEqual(article.status, 'published')
        self.assertEqual(route.status, 'published')
        self.assertEqual(response.context['import_result']['updated'], 1)
        self.assertEqual(response.context['import_result']['failed'], 0)

    def test_article_import_does_not_disclose_unexpected_exception_details(self):
        self.grant(CONTENT_READ, locale=self.locale_en)
        self.grant(CONTENT_WRITE, locale=self.locale_en)
        self.client.force_login(self.content_user)
        archive_buffer = BytesIO()
        with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                'safe.md',
                '---\ntitle: Safe\nslug: safe\nstatus: draft\n---\nBody',
            )
        upload = SimpleUploadedFile(
            'articles.zip',
            archive_buffer.getvalue(),
            content_type='application/zip',
        )
        sensitive_detail = (
            'postgresql://internal-user:internal-password@db.internal/siteos '
            'constraint private_article_key'
        )

        with (
            patch(
                'console.article_imports.ArticleEditorForm',
                side_effect=RuntimeError(sensitive_detail),
            ),
            patch('console.article_imports.logger.exception') as logged,
        ):
            response = self.client.post(
                f'{reverse("console:article_import")}?locale=en',
                {'default_status': 'draft', 'import_file': upload},
            )

        self.assertEqual(response.status_code, 200)
        result = response.context['import_result']
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['updated'], 0)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(
            result['entries'][0].message,
            '导入失败；未写入数据库。请联系系统管理员查看服务日志。',
        )
        self.assertNotIn(sensitive_detail, response.content.decode('utf-8'))
        logged.assert_called_once_with('Unexpected article ZIP import entry failure.')

    def test_local_asset_import_requires_read_write_and_explicit_high_risk_capability(self):
        self.client.force_login(self.content_user)

        class ReachableForm:
            def __init__(self, *args, **kwargs):
                pass

            def is_valid(self):
                return False

        with (
            patch('console.views.AssetImportPathForm', side_effect=ReachableForm) as form_class,
            patch('console.views.render_form_console', return_value=HttpResponse('ok')),
        ):
            self.grant(ASSETS_WRITE)
            write_only = self.client.get(reverse('console:asset_import_path'))
            self.assertEqual(write_only.status_code, 403)
            form_class.assert_not_called()

            ContentAccessGrant.objects.all().delete()
            self.grant(ASSETS_READ)
            self.grant(ASSETS_IMPORT_LOCAL)
            read_and_import_only = self.client.get(reverse('console:asset_import_path'))
            self.assertEqual(read_and_import_only.status_code, 403)
            form_class.assert_not_called()

            self.grant(ASSETS_WRITE)
            all_three = self.client.get(reverse('console:asset_import_path'))

        self.assertEqual(all_three.status_code, 200)
        form_class.assert_called_once()

    def test_write_capabilities_do_not_bypass_read_on_mutation_forms(self):
        self.grant(CONTENT_WRITE, locale=self.locale_en)
        self.client.force_login(self.content_user)

        for route_name, args in (
            ('article_import', ()),
            ('category_create', ()),
            ('category_edit', (999_994,)),
            ('article_create', ()),
            ('article_edit', (999_995,)),
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(
                    f'{reverse(f"console:{route_name}", args=args)}?locale=en'
                )
                self.assertEqual(response.status_code, 403)

        ContentAccessGrant.objects.all().delete()
        self.grant(ASSETS_WRITE)
        self.grant(ASSETS_IMPORT_LOCAL)
        for route_name, args in (
            ('three_d_profile_create', ()),
            ('three_d_profile_edit', (999_997,)),
            ('three_d_placement_create', ()),
            ('three_d_placement_edit', (999_998,)),
            ('asset_upload', ()),
            ('asset_import_path', ()),
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(f'console:{route_name}', args=args))
                self.assertEqual(response.status_code, 403)

    def test_preview_build_is_not_inferred_from_release_read(self):
        self.grant(RELEASES_READ)
        self.client.force_login(self.content_user)
        build_result = SimpleNamespace(
            succeeded=True,
            build=SimpleNamespace(build_key='content-route-preview'),
        )

        with patch('console.views.run_preview_build', return_value=build_result) as runner:
            denied = self.client.post(reverse('console:release_build_preview'))
            self.assertEqual(denied.status_code, 403)
            runner.assert_not_called()

            ContentAccessGrant.objects.all().delete()
            self.grant(RELEASES_PREVIEW_BUILD)
            allowed = self.client.post(reverse('console:release_build_preview'))

        self.assertEqual(allowed.status_code, 302)
        # A high-risk executor grant does not implicitly reveal release data.
        self.assertEqual(allowed.url, reverse('console:workbench'))
        runner.assert_called_once_with(
            created_by=self.content_user.get_username(),
            site_code=self.site.code,
        )

    def test_system_admin_keeps_representative_read_write_and_high_risk_routes_without_grants(self):
        self.client.force_login(self.admin_user)
        workspace = {'locale': {'code': 'en'}}
        inert_form = SimpleNamespace(is_valid=lambda: False)
        build_result = SimpleNamespace(
            succeeded=True,
            build=SimpleNamespace(build_key='admin-preview'),
        )

        with (
            patch('console.views.article_workspace_bundle', return_value=({}, workspace)),
            patch('console.views.ArticleEditorForm', return_value=inert_form),
            patch('console.views.category_tree_data', return_value=[]),
            patch('console.views.assets_payload', return_value={}),
            patch('console.views.AssetImportPathForm', return_value=inert_form),
            patch('console.views.releases_payload', return_value={}),
            patch('console.views.render_form_console', return_value=HttpResponse('ok')),
            patch('console.views.render_console', return_value=HttpResponse('ok')),
            patch('console.views.run_preview_build', return_value=build_result) as runner,
        ):
            responses = (
                self.client.get(f'{reverse("console:articles")}?locale=en'),
                self.client.get(f'{reverse("console:article_create")}?locale=en'),
                self.client.get(reverse('console:assets')),
                self.client.get(reverse('console:asset_import_path')),
                self.client.get(reverse('console:releases')),
                self.client.post(reverse('console:release_build_preview')),
            )

        self.assertEqual([response.status_code for response in responses], [200, 200, 200, 200, 200, 302])
        self.assertFalse(ContentAccessGrant.objects.exists())
        runner.assert_called_once_with(
            created_by=self.admin_user.get_username(),
            site_code=self.site.code,
        )

    def test_wrong_methods_return_405_before_any_mutation(self):
        self.client.force_login(self.admin_user)

        with (
            patch('console.views.articles') as articles,
            patch('console.views.run_preview_build') as preview_build,
            patch('console.views.disable_locale') as disable_locale,
        ):
            alias_post = self.client.post(reverse('console:content_articles'))
            preview_get = self.client.get(reverse('console:release_build_preview'))
            locale_get = self.client.get(
                reverse('console:article_locale_disable', args=[self.locale_ar.id])
            )

        self.assertEqual(alias_post.status_code, 405)
        self.assertEqual(preview_get.status_code, 405)
        self.assertEqual(locale_get.status_code, 405)
        articles.assert_not_called()
        preview_build.assert_not_called()
        disable_locale.assert_not_called()
