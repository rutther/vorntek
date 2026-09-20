from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .article_build_input import read_active_article_build_input, read_article_build_input
from .article_delivery import (
    ArticleDeliveryError,
    ArticleLocale,
    ArticleVersion,
    build_article_release,
)
from .article_release_store import ArticleReleaseStore


class ArticleBuildInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix='vorntek-article-build-input-'
        )
        self.store = ArticleReleaseStore.initialize(
            Path(self.temporary.name) / 'store', site_code='testSite', preview=True
        )
        self.locales = [
            ArticleLocale('en', 'English', is_default=True),
            ArticleLocale('zh', '中文'),
        ]
        self.version = self.store.stage(self.release())
        self.store.activate(self.version, expected='')

    def tearDown(self):
        self.temporary.cleanup()

    def release(self, *, empty=False, body='Complete public body'):
        versions = [] if empty else [
            ArticleVersion(
                content_key='guide',
                locale=language,
                slug='guide',
                title='Guide',
                markdown=body,
                status='published',
                route_status='published',
            )
            for language in ('en', 'zh')
        ]
        return build_article_release(
            versions,
            locales=self.locales,
            site_code='testSite',
            site_name='Vorntek',
            public_origin='https://example.invalid',
        )

    def test_pinned_read_preserves_exact_artifacts_without_activation(self):
        result = read_active_article_build_input(
            self.store, expected_version=self.version, release_mode=False
        )
        self.assertEqual(result['manifest'], self.store.verify(self.version))
        for name, text in result['documents'].items():
            self.assertEqual(
                text.encode('utf-8'), self.store.read_version_file(self.version, name)
            )
        self.assertEqual(self.store.current(), self.version)

    def test_preview_artifact_cannot_be_promoted_by_a_build_flag(self):
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'preview_artifact_not_publishable'
        ):
            read_active_article_build_input(
                self.store, expected_version=self.version, release_mode=True
            )

    def test_explicit_verified_version_can_feed_composer_without_activation(self):
        other = self.store.stage(self.release(body='Changed complete public body'))
        self.assertNotEqual(other, self.version)

        result = read_article_build_input(
            self.store,
            version=other,
            release_mode=False,
        )

        self.assertEqual(result['version'], other)
        self.assertEqual(self.store.current(), self.version)

    def test_stale_or_changing_pointer_is_rejected(self):
        with self.assertRaisesRegex(ArticleDeliveryError, 'active_version_changed'):
            read_active_article_build_input(
                self.store, expected_version='0' * 64, release_mode=False
            )
        with patch.object(
            self.store, 'current', side_effect=[self.version, '0' * 64]
        ):
            with self.assertRaisesRegex(
                ArticleDeliveryError, 'active_version_changed'
            ):
                read_active_article_build_input(
                    self.store, expected_version=self.version, release_mode=False
                )

    def test_withdrawn_content_is_not_recovered_from_previous_release(self):
        withdrawn = self.store.stage(self.release(empty=True))
        self.store.activate(withdrawn, expected=self.version)
        result = read_active_article_build_input(
            self.store, expected_version=withdrawn, release_mode=False
        )
        self.assertIn('zh/articles/index.html', result['documents'])
        self.assertNotIn('zh/articles/guide/index.html', result['documents'])
        self.assertIn(
            'zh/articles/guide/index.html', self.store.verify(self.version)['files']
        )

    def test_changed_artifact_is_rejected(self):
        target = (
            self.store.root
            / 'releases'
            / self.version
            / 'zh/articles/guide/index.html'
        )
        target.write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ArticleDeliveryError, 'artifact_file_changed'):
            read_active_article_build_input(
                self.store, expected_version=self.version, release_mode=False
            )


if __name__ == '__main__':
    unittest.main()
