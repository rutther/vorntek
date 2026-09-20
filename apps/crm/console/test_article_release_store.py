from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from .article_delivery import (
    ArticleDeliveryError,
    ArticleLocale,
    ArticleVersion,
    build_article_release,
)
from .article_release_store import ArticleReleaseStore


class ArticleReleaseStoreTests(unittest.TestCase):
    locales = [
        ArticleLocale('en', 'English', is_default=True),
        ArticleLocale('zh', '中文'),
    ]

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vorntek-article-store-')
        self.root = Path(self.temporary.name) / 'private'
        self.store = ArticleReleaseStore.initialize(
            self.root, site_code='testSite', preview=True
        )

    def tearDown(self):
        self.temporary.cleanup()

    def snapshot(self, text='Original complete content', *, empty=False, robots='index,follow'):
        versions = [] if empty else [
            ArticleVersion(
                content_key='guide',
                locale=language,
                slug='guide',
                title='Guide' if language == 'en' else '指南',
                markdown=text,
                robots=robots,
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

    def test_stage_activate_update_withdraw_and_rollback_actual_files(self):
        original = self.store.stage(self.snapshot())
        self.assertIsNone(self.store.read('/zh/articles/guide/'))
        self.store.activate(original, expected='')
        self.assertIn(
            b'Original complete content', self.store.read('/zh/articles/guide/')[0]
        )

        update = self.store.stage(self.snapshot('Updated full content'))
        self.store.activate(update, expected=original)
        self.assertIn(b'Updated full content', self.store.read('/articles/guide/')[0])

        empty = self.store.stage(self.snapshot(empty=True))
        self.store.activate(empty, expected=update)
        self.assertIsNone(self.store.read('/zh/articles/guide/'))
        self.assertIsNotNone(self.store.read('/zh/articles/'))

        self.store.activate(original, expected=empty)
        self.assertIn(b'Original complete content', self.store.read('/articles/guide/')[0])

    def test_sitemap_switches_with_withdrawal_and_rollback(self):
        original = self.store.stage(self.snapshot())
        self.store.activate(original, expected='')

        def urls():
            body = self.store.read('/article-sitemap.xml')[0]
            return [
                node.text
                for node in ET.fromstring(body).iter(
                    '{http://www.sitemaps.org/schemas/sitemap/0.9}loc'
                )
            ]

        self.assertEqual(
            urls(),
            [
                'https://example.invalid/articles/guide/',
                'https://example.invalid/zh/articles/guide/',
            ],
        )
        empty = self.store.stage(self.snapshot(empty=True))
        self.store.activate(empty, expected=original)
        self.assertEqual(urls(), [])
        self.store.activate(original, expected=empty)
        self.assertEqual(len(urls()), 2)

    def test_noindex_article_is_readable_but_absent_from_sitemap(self):
        version = self.store.stage(self.snapshot(robots='noindex,follow'))
        self.store.activate(version, expected='')
        self.assertIsNotNone(self.store.read('/zh/articles/guide/'))
        self.assertNotIn(b'<loc>', self.store.read('/article-sitemap.xml')[0])

    def test_identical_retry_and_activation_are_idempotent(self):
        first = self.store.stage(self.snapshot())
        self.assertEqual(first, self.store.stage(self.snapshot()))
        self.store.activate(first, expected='')
        self.assertFalse(self.store.activate(first, expected=first)['changed'])

    def test_live_store_requires_complete_translations(self):
        live = ArticleReleaseStore.initialize(
            Path(self.temporary.name) / 'live', site_code='testSite', preview=False
        )
        incomplete = build_article_release(
            [
                ArticleVersion(
                    content_key='guide', locale='en', slug='guide', title='Guide',
                    markdown='Complete', status='published', route_status='published',
                )
            ],
            locales=self.locales,
            site_code='testSite',
            site_name='Vorntek',
            public_origin='https://example.invalid',
        )
        with self.assertRaisesRegex(ArticleDeliveryError, 'translations_not_ready'):
            live.stage(incomplete)
        version = live.stage(self.snapshot())
        live.activate(version, expected='')
        self.assertIn(b'index,follow', live.read('/articles/guide/')[0])

    def test_failed_or_stale_activation_keeps_current_version(self):
        old = self.store.stage(self.snapshot())
        self.store.activate(old, expected='')
        new = self.store.stage(self.snapshot('New'))
        with patch(
            'console.article_release_store.os.replace',
            side_effect=OSError('simulated disk failure'),
        ):
            with self.assertRaises(OSError):
                self.store.activate(new, expected=old)
        self.assertEqual(self.store.current(), old)
        with self.assertRaisesRegex(ArticleDeliveryError, 'active_version_changed'):
            self.store.activate(new, expected='')
        self.assertEqual(self.store.current(), old)

    def test_tampered_or_extra_files_are_rejected(self):
        version = self.store.stage(self.snapshot())
        article = self.root / 'releases' / version / 'zh/articles/guide/index.html'
        article.write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(ArticleDeliveryError, 'artifact_file_changed'):
            self.store.activate(version, expected='')

        clean = self.store.stage(self.snapshot('Another release'))
        (self.root / 'releases' / clean / 'unexpected.txt').write_text('extra')
        with self.assertRaisesRegex(ArticleDeliveryError, 'artifact_file_set_mismatch'):
            self.store.activate(clean, expected='')

    def test_lock_store_ownership_and_root_boundaries_fail_closed(self):
        (self.root / 'publish.lock').write_text('another process', encoding='ascii')
        with self.assertRaisesRegex(ArticleDeliveryError, 'publication_in_progress'):
            self.store.stage(self.snapshot())
        (self.root / 'publish.lock').unlink()

        arbitrary = Path(self.temporary.name) / 'user-files'
        arbitrary.mkdir()
        keep = arbitrary / 'keep.txt'
        keep.write_text('user data', encoding='utf-8')
        with self.assertRaises(ArticleDeliveryError):
            ArticleReleaseStore.initialize(
                arbitrary, site_code='testSite', preview=True
            )
        with self.assertRaisesRegex(ArticleDeliveryError, 'store_owner_mismatch'):
            ArticleReleaseStore(self.root, site_code='otherSite', preview=True)
        with self.assertRaisesRegex(ArticleDeliveryError, 'invalid_store_root'):
            ArticleReleaseStore.initialize(
                Path(self.root.anchor), site_code='testSite', preview=True
            )
        self.assertEqual(keep.read_text(encoding='utf-8'), 'user data')

    def test_unsafe_routes_never_read_store_or_other_files(self):
        version = self.store.stage(self.snapshot())
        self.store.activate(version, expected='')
        for route in (
            None,
            42,
            '/admin/',
            '/../owner.json/',
            '/zh/articles/../../admin/',
            '//zh/articles/guide/',
            '/zh/articles/guide//',
            '/zh/articles//guide/',
            '/zh/articles/guide/?secret=x',
        ):
            with self.subTest(route=route):
                self.assertIsNone(self.store.read(route))

    def test_locale_paths_are_generic_not_hard_coded(self):
        locales = [
            ArticleLocale('en', 'English', is_default=True),
            ArticleLocale('pt-BR', 'Português'),
        ]
        release = build_article_release(
            [
                ArticleVersion(
                    content_key='guide', locale=language, slug='guide', title='Guide',
                    markdown='Body', status='published', route_status='published',
                )
                for language in ('en', 'pt-BR')
            ],
            locales=locales,
            site_code='testSite',
            site_name='Vorntek',
            public_origin='https://example.invalid',
        )
        version = self.store.stage(release)
        self.store.activate(version, expected='')
        self.assertIsNotNone(self.store.read('/pt-BR/articles/guide/'))


if __name__ == '__main__':
    unittest.main()
