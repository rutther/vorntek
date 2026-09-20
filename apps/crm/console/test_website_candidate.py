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
from .article_preview import build_private_article_preview
from .website_candidate import (
    article_live_store,
    build_website_candidate,
    website_release_store,
)


WEBSITE_ROOT = Path(__file__).resolve().parents[2] / 'website'


class WebsiteCandidateTests(TestCase):
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
        self.temporary = tempfile.TemporaryDirectory(prefix='vorntek-candidate-')
        root = Path(self.temporary.name)
        self.settings_override = override_settings(
            SITEOS_ARTICLE_PREVIEW_ROOT=root / 'previews',
            SITEOS_ARTICLE_RELEASE_ROOT=root / 'article-releases',
            SITEOS_WEBSITE_RELEASE_ROOT=root / 'website-releases',
            SITEOS_WEBSITE_SOURCE_ROOT=WEBSITE_ROOT,
            SITEOS_ARTICLE_PUBLIC_ORIGIN='https://example.invalid',
        )
        self.settings_override.enable()
        self.site = Site.objects.create(
            code='candidateTest',
            name='Vorntek',
            base_url='https://example.invalid',
            default_locale='en',
            enabled=True,
            config_json={
                'articleDelivery': {
                    'baseRouteMode': 'shared',
                    'brandLogoUrl': '/assets/vorntek/vorntekLogo.png',
                }
            },
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
        self.settings_override.disable()
        self.temporary.cleanup()

    def preview(self):
        return build_private_article_preview(site=self.site, actor='reviewer')

    def test_reviewed_preview_builds_repeatable_exact_candidate_without_selection(self):
        preview = self.preview()
        first = build_website_candidate(
            site=self.site,
            preview_record_id=preview['recordId'],
            actor='operator',
        )
        second = build_website_candidate(
            site=self.site,
            preview_record_id=preview['recordId'],
            actor='operator',
        )

        self.assertEqual(first, second)
        self.assertEqual(first['fileCount'], 32)
        self.assertEqual(article_live_store(self.site, initialize=False).current(), '')
        self.assertEqual(website_release_store(self.site, initialize=False).current(), '')
        record = Release.objects.get(pk=first['recordId'])
        self.assertEqual(record.status, 'built')
        self.assertIsNone(record.published_at)
        self.assertEqual(record.builds.count(), 1)

    def test_content_change_after_preview_is_rejected_and_audited(self):
        preview = self.preview()
        article = self.articles['zh']
        article.body_markdown = 'Changed after review'
        article.save(update_fields=['body_markdown'])

        with self.assertRaisesRegex(
            ArticleDeliveryError, 'content_changed_since_preview'
        ):
            build_website_candidate(
                site=self.site,
                preview_record_id=preview['recordId'],
                actor='operator',
            )

        failed = Release.objects.get(status='failed')
        self.assertEqual(failed.snapshot_manifest['errorCode'], 'content_changed_since_preview')
        self.assertEqual(failed.builds.get().status, 'failed')

    def test_missing_translation_preview_cannot_become_a_candidate(self):
        article = self.articles['zh']
        article.status = 'draft'
        article.save(update_fields=['status'])
        preview = self.preview()

        with self.assertRaisesRegex(ArticleDeliveryError, 'translations_not_ready'):
            build_website_candidate(
                site=self.site,
                preview_record_id=preview['recordId'],
            )

        self.assertFalse(
            Release.objects.filter(
                status='built', release_key__startswith='website-candidate:'
            ).exists()
        )

    def test_unreviewed_or_other_site_record_is_rejected_before_composition(self):
        preview = self.preview()
        other = Site.objects.create(
            code='otherCandidate',
            name='Other',
            base_url='https://other.example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )

        with self.assertRaisesRegex(
            ArticleDeliveryError, 'reviewed_article_preview_missing'
        ):
            build_website_candidate(
                site=other,
                preview_record_id=preview['recordId'],
            )


if __name__ == '__main__':
    import unittest

    unittest.main()
