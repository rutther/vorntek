from pathlib import Path
import tempfile

from django.db import connection
from django.test import TestCase, override_settings

from sitecore.models import (
    Article,
    Category,
    PageRoute,
    Release,
    ReleaseBuild,
    Site,
    SiteLocale,
)

from .article_delivery import ArticleDeliveryError
from .article_preview import (
    article_preview_store,
    build_private_article_preview,
    reviewed_article_preview,
)


class ArticlePreviewBuildTests(TestCase):
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        Category,
        Article,
        Release,
        ReleaseBuild,
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
        self.temporary = tempfile.TemporaryDirectory(prefix='vorntek-preview-build-')
        self.root = Path(self.temporary.name) / 'previews'
        self.site = Site.objects.create(
            code='previewTest',
            name='Preview Test',
            base_url='https://example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.articles = {}
        for order, language in enumerate(('en', 'zh')):
            locale = SiteLocale.objects.create(
                site=self.site,
                locale_code=language,
                label='English' if language == 'en' else '中文',
                direction='ltr',
                is_default=language == 'en',
                enabled=True,
                sort_order=order,
            )
            route = PageRoute.objects.create(
                site=self.site,
                locale=locale,
                path=f'/{language}/articles/guide/',
                route_type='article',
                status='published',
                page_title='Guide',
            )
            self.articles[language] = Article.objects.create(
                route=route,
                title='Guide',
                slug='guide',
                body_markdown=f'Complete {language} body',
                status='published',
                config_json={'contentKey': 'guide'},
            )

    def tearDown(self):
        self.temporary.cleanup()

    def settings(self, **changes):
        values = {
            'SITEOS_ARTICLE_PREVIEW_ROOT': self.root,
            'SITEOS_ARTICLE_PUBLIC_ORIGIN': 'https://example.invalid',
        }
        values.update(changes)
        return override_settings(**values)

    def test_build_is_recorded_repeatable_and_never_activated(self):
        with self.settings():
            first = build_private_article_preview(site=self.site, actor='tester')
            second = build_private_article_preview(site=self.site, actor='tester')

            self.assertEqual(first, second)
            self.assertEqual(first['articleCount'], 2)
            self.assertIn('articles/index.html', first['files'])
            self.assertIn('zh/articles/guide/index.html', first['files'])
            self.assertEqual(article_preview_store(self.site, initialize=False).current(), '')

        release = Release.objects.get(pk=first['recordId'])
        self.assertEqual(release.status, 'built')
        self.assertIsNone(release.published_at)
        self.assertEqual(release.created_by, 'tester')
        self.assertEqual(release.builds.count(), 1)
        self.assertIsNotNone(
            reviewed_article_preview(site=self.site, version=first['version'])
        )

    def test_database_change_creates_a_new_reviewable_version(self):
        with self.settings():
            first = build_private_article_preview(site=self.site)
            article = self.articles['zh']
            article.body_markdown = 'Updated complete Chinese body'
            article.save(update_fields=['body_markdown'])
            second = build_private_article_preview(site=self.site)

        self.assertNotEqual(first['version'], second['version'])
        self.assertNotEqual(first['recordId'], second['recordId'])
        self.assertEqual(Release.objects.filter(site=self.site, status='built').count(), 2)

    def test_invalid_content_records_failure_without_activation(self):
        article = self.articles['zh']
        article.config_json = {}
        article.save(update_fields=['config_json'])
        with self.settings():
            with self.assertRaisesRegex(ArticleDeliveryError, 'missing_content_identity'):
                build_private_article_preview(site=self.site, actor='tester')

        failed = Release.objects.get(site=self.site, status='failed')
        self.assertEqual(failed.snapshot_manifest['errorCode'], 'missing_content_identity')
        self.assertEqual(failed.builds.get().status, 'failed')
        self.assertIsNone(failed.published_at)

    def test_unconfigured_relative_target_fails_before_filesystem_write(self):
        relative = Path('relative-preview-root-that-must-not-exist')
        self.assertFalse(relative.exists())
        with self.settings(SITEOS_ARTICLE_PREVIEW_ROOT=relative):
            with self.assertRaisesRegex(ArticleDeliveryError, 'preview_target_not_configured'):
                build_private_article_preview(site=self.site)
        self.assertFalse(relative.exists())
        self.assertEqual(Release.objects.filter(status='failed').count(), 1)

    def test_review_record_is_site_scoped(self):
        with self.settings():
            result = build_private_article_preview(site=self.site)
        other = Site.objects.create(
            code='otherPreview',
            name='Other',
            base_url='https://other.example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.assertIsNone(
            reviewed_article_preview(site=other, version=result['version'])
        )


if __name__ == '__main__':
    import unittest

    unittest.main()
