from pathlib import Path
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from .article_build_input import read_article_build_input
from .article_delivery import (
    ArticleDeliveryError,
    ArticleLocale,
    ArticleVersion,
    build_article_release,
)
from .article_release_store import ArticleReleaseStore
from .website_release_store import WebsiteReleaseStore


WEBSITE_ROOT = Path(__file__).resolve().parents[2] / 'website'


class WebsiteReleaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vorntek-website-release-')
        root = Path(self.temporary.name)
        self.article_store = ArticleReleaseStore.initialize(
            root / 'articles', site_code='testSite', preview=False
        )
        self.website_store = WebsiteReleaseStore.initialize(
            root / 'website-store', site_code='testSite'
        )
        self.locales = [
            ArticleLocale('en', 'English', is_default=True),
            ArticleLocale('zh', '中文'),
        ]

    def tearDown(self):
        self.temporary.cleanup()

    def article_release(self, *, empty=False, body='Complete body', cover_url=''):
        rows = [] if empty else [
            ArticleVersion(
                content_key='guide',
                locale=language,
                slug='guide',
                title='Guide',
                markdown=body,
                cover_url=cover_url,
                status='published',
                route_status='published',
            )
            for language in ('en', 'zh')
        ]
        return build_article_release(
            rows,
            locales=self.locales,
            site_code='testSite',
            site_name='Vorntek',
            public_origin='https://example.invalid',
            brand_logo_url='/assets/vorntek/vorntekLogo.png',
            base_route_mode='shared',
        )

    def article_input(self, *, empty=False, body='Complete body', cover_url=''):
        version = self.article_store.stage(
            self.article_release(empty=empty, body=body, cover_url=cover_url)
        )
        return read_article_build_input(
            self.article_store,
            version=version,
            release_mode=True,
        )

    def test_current_vorntek_tree_and_live_articles_form_one_verified_bundle(self):
        article_input = self.article_input()

        version = self.website_store.stage(
            base_root=WEBSITE_ROOT,
            article_input=article_input,
        )
        manifest = self.website_store.verify(version)

        self.assertEqual(manifest['articleVersion'], article_input['version'])
        self.assertEqual(len(manifest['files']), 32)
        self.assertIn('app.js', manifest['files'])
        self.assertIn('assets/vorntek/vorntekLogo.png', manifest['files'])
        self.assertIn('articles/guide/index.html', manifest['files'])
        self.assertIn('zh/articles/guide/index.html', manifest['files'])
        self.assertEqual(self.website_store.current(), '')

    def test_source_changes_do_not_change_an_existing_bundle(self):
        source = Path(self.temporary.name) / 'source'
        shutil.copytree(WEBSITE_ROOT, source)
        version = self.website_store.stage(
            base_root=source,
            article_input=self.article_input(),
        )
        frozen = self.website_store.read_version_file(version, 'styles.css')

        (source / 'styles.css').write_text('body { color: red; }', encoding='utf-8')

        self.assertEqual(
            self.website_store.read_version_file(version, 'styles.css'),
            frozen,
        )
        changed = self.website_store.stage(
            base_root=source,
            article_input=self.article_input(),
        )
        self.assertNotEqual(changed, version)

    def test_withdrawal_replaces_the_complete_article_namespace(self):
        published = self.website_store.stage(
            base_root=WEBSITE_ROOT,
            article_input=self.article_input(),
        )
        withdrawn = self.website_store.stage(
            base_root=WEBSITE_ROOT,
            article_input=self.article_input(empty=True),
        )

        self.assertIn('articles/guide/index.html', self.website_store.verify(published)['files'])
        self.assertNotIn('articles/guide/index.html', self.website_store.verify(withdrawn)['files'])
        self.assertIn('articles/index.html', self.website_store.verify(withdrawn)['files'])

    def test_missing_internal_reference_and_preview_input_fail_closed(self):
        source = Path(self.temporary.name) / 'broken-source'
        shutil.copytree(WEBSITE_ROOT, source)
        with (source / 'index.html').open('a', encoding='utf-8') as stream:
            stream.write('<img src="/missing-private.png">')
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'website_internal_reference_missing'
        ):
            self.website_store.stage(
                base_root=source,
                article_input=self.article_input(),
            )

        css_source = Path(self.temporary.name) / 'external-css-source'
        shutil.copytree(WEBSITE_ROOT, css_source)
        with (css_source / 'styles.css').open('a', encoding='utf-8') as stream:
            stream.write('body { background: url(https://images.example.invalid/x.png); }')
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'website_external_resource'
        ):
            self.website_store.stage(
                base_root=css_source,
                article_input=self.article_input(),
            )

        preview = ArticleReleaseStore.initialize(
            Path(self.temporary.name) / 'preview', site_code='testSite', preview=True
        )
        preview_version = preview.stage(self.article_release())
        preview_input = read_article_build_input(
            preview,
            version=preview_version,
            release_mode=False,
        )
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'article_build_input_invalid'
        ):
            self.website_store.stage(
                base_root=WEBSITE_ROOT,
                article_input=preview_input,
            )

        with self.assertRaisesRegex(
            ArticleDeliveryError, 'website_external_resource'
        ):
            self.website_store.stage(
                base_root=WEBSITE_ROOT,
                article_input=self.article_input(
                    cover_url='https://images.example.invalid/guide.png'
                ),
            )

    def test_hardlinked_source_file_is_rejected(self):
        source = Path(self.temporary.name) / 'linked-source'
        shutil.copytree(WEBSITE_ROOT, source)
        try:
            os.link(source / 'styles.css', source / 'hardlinked.css')
        except OSError as error:
            self.skipTest(f'hard links unavailable: {error}')

        with self.assertRaisesRegex(
            ArticleDeliveryError, 'unsafe_website_source_link'
        ):
            self.website_store.stage(
                base_root=source,
                article_input=self.article_input(),
            )

    def test_tampered_or_extra_bundle_files_are_rejected(self):
        version = self.website_store.stage(
            base_root=WEBSITE_ROOT,
            article_input=self.article_input(),
        )
        release = self.website_store.root / 'releases' / version
        (release / 'styles.css').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_file_changed'):
            self.website_store.verify(version)

        # Restore by using a separate deterministic candidate, then add an extra file.
        other = self.website_store.stage(
            base_root=WEBSITE_ROOT,
            article_input=self.article_input(body='Other complete body'),
        )
        (self.website_store.root / 'releases' / other / 'extra.txt').write_text(
            'extra', encoding='utf-8'
        )
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_file_set_mismatch'):
            self.website_store.verify(other)

    def test_cas_selection_supports_update_and_rollback_without_deploying(self):
        first = self.website_store.stage(
            base_root=WEBSITE_ROOT,
            article_input=self.article_input(),
        )
        second = self.website_store.stage(
            base_root=WEBSITE_ROOT,
            article_input=self.article_input(body='Updated complete body'),
        )

        self.website_store.activate(first, expected='')
        with patch(
            'console.website_release_store.os.replace',
            side_effect=OSError('simulated pointer rename failure'),
        ):
            with self.assertRaises(OSError):
                self.website_store.activate(second, expected=first)
        self.assertEqual(self.website_store.current(), first)
        self.assertEqual(
            list(self.website_store.root.glob('active-*.tmp')),
            [],
        )
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'website_active_version_changed'
        ):
            self.website_store.activate(second, expected='')
        self.website_store.activate(second, expected=first)
        rollback = self.website_store.activate(first, expected=second)

        self.assertEqual(rollback['previous'], second)
        self.assertEqual(self.website_store.current(), first)


if __name__ == '__main__':
    unittest.main()
