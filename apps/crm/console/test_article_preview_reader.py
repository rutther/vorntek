from pathlib import Path
import tempfile
import unittest

from .article_delivery import (
    ArticleDeliveryError,
    ArticleLocale,
    ArticleVersion,
    build_article_release,
)
from .article_preview_reader import read_private_preview
from .article_release_store import ArticleReleaseStore


class ArticlePreviewReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vorntek-private-preview-')
        root = Path(self.temporary.name)
        self.asset_root = root / 'website'
        (self.asset_root / 'assets/vorntek').mkdir(parents=True)
        (self.asset_root / 'styles.css').write_text(
            'body { color: #123; }', encoding='utf-8'
        )
        (self.asset_root / 'assets/vorntek/logo.png').write_bytes(b'PNG-preview-test')
        (self.asset_root / 'assets/vorntek/unreferenced.png').write_bytes(b'private')
        (self.asset_root / 'assets/blocked.js').write_text('alert(1)', encoding='utf-8')
        self.store = ArticleReleaseStore.initialize(
            root / 'store', site_code='testSite', preview=True
        )
        release = build_article_release(
            [
                ArticleVersion(
                    content_key='guide',
                    locale=language,
                    slug='guide',
                    title='Guide',
                    markdown=(
                        '[Other article](/articles/other/)\n\n'
                        '[External](https://external.invalid/private)\n\n'
                        '![External image](https://tracking.invalid/pixel.png)'
                    ),
                    status='published',
                    route_status='published',
                )
                for language in ('en', 'zh')
            ]
            + [
                ArticleVersion(
                    content_key='other',
                    locale=language,
                    slug='other',
                    title='Other',
                    markdown='Other body',
                    status='published',
                    route_status='published',
                )
                for language in ('en', 'zh')
            ],
            locales=[
                ArticleLocale('en', 'English', is_default=True),
                ArticleLocale('zh', '中文'),
            ],
            site_code='testSite',
            site_name='Vorntek',
            public_origin='https://example.invalid',
            brand_logo_url='/assets/vorntek/logo.png',
        )
        self.version = self.store.stage(release)
        self.base = f'/admin/releases/articles/preview/{self.version}/'

    def tearDown(self):
        self.temporary.cleanup()

    def read(self, artifact):
        return read_private_preview(
            version=self.version,
            artifact=artifact,
            base=self.base,
            store=self.store,
            asset_root=self.asset_root,
        )

    def test_html_is_network_silent_and_internal_links_are_rewritten(self):
        raw, mime = self.read('articles/guide/index.html')
        text = raw.decode('utf-8')
        self.assertEqual(mime, 'text/html; charset=utf-8')
        self.assertNotIn('<script', text)
        self.assertNotIn('application/ld+json', text)
        self.assertNotIn('https://external.invalid', text)
        self.assertNotIn('https://tracking.invalid', text)
        self.assertNotIn('srcset=', text)
        self.assertIn(f'href="{self.base}styles.css"', text)
        self.assertIn(
            f'href="{self.base}articles/other/index.html"', text
        )
        self.assertIn(f'src="{self.base}assets/vorntek/logo.png"', text)
        self.assertIn('aria-disabled="true"', text)

    def test_only_referenced_safe_static_resources_are_served(self):
        stylesheet = self.read('styles.css')
        self.assertEqual(stylesheet, (b'body { color: #123; }', 'text/css; charset=utf-8'))
        logo = self.read('assets/vorntek/logo.png')
        self.assertEqual(logo, (b'PNG-preview-test', 'image/png'))
        for artifact in (
            'assets/vorntek/unreferenced.png',
            'assets/blocked.js',
            '../owner.json',
            'manifest.json',
        ):
            with self.subTest(artifact=artifact):
                self.assertIsNone(self.read(artifact))

    def test_stylesheet_with_network_or_asset_loading_is_refused(self):
        for value in (
            '@import "https://example.invalid/x.css";',
            '@\\69mport "https://example.invalid/x.css";',
            'body { background: url(/assets/vorntek/logo.png); }',
            'body { background: image-set("https://example.invalid/x.png" 1x); }',
            'x { width: expression(alert(1)); }',
        ):
            with self.subTest(value=value):
                (self.asset_root / 'styles.css').write_text(value, encoding='utf-8')
                with self.assertRaisesRegex(
                    ArticleDeliveryError, 'preview_stylesheet_not_network_silent'
                ):
                    self.read('styles.css')

    def test_sitemap_is_available_but_invalid_base_and_live_store_are_refused(self):
        sitemap = self.read('article-sitemap.xml')
        self.assertEqual(sitemap[1], 'application/xml; charset=utf-8')
        with self.assertRaisesRegex(ArticleDeliveryError, 'preview_base_invalid'):
            read_private_preview(
                version=self.version,
                artifact='articles/index.html',
                base='https://external.invalid/preview/',
                store=self.store,
                asset_root=self.asset_root,
            )
        live = ArticleReleaseStore.initialize(
            Path(self.temporary.name) / 'live', site_code='testSite', preview=False
        )
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'preview_store_scope_mismatch'
        ):
            read_private_preview(
                version=self.version,
                artifact='articles/index.html',
                base=self.base,
                store=live,
                asset_root=self.asset_root,
            )

    def test_changed_artifact_is_rejected_before_preview(self):
        target = (
            self.store.root
            / 'releases'
            / self.version
            / 'articles/guide/index.html'
        )
        target.write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ArticleDeliveryError, 'artifact_file_changed'):
            self.read('articles/guide/index.html')


if __name__ == '__main__':
    unittest.main()
