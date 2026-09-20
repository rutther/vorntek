import unittest
from html.parser import HTMLParser

from .article_delivery import (
    ArticleDeliveryError,
    ArticleLocale,
    ArticleVersion,
    build_article_release,
)
from .article_rendering import render_article_documents, render_markdown


class Nodes(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.nodes = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


class ArticleRenderingTests(unittest.TestCase):
    locales = [
        ArticleLocale('en', 'English', is_default=True),
        ArticleLocale('zh', '中文'),
    ]

    def manifest(self, body='## Full heading\n\nComplete text, **not an excerpt**.'):
        return build_article_release(
            [
                ArticleVersion(
                    content_key='guide',
                    locale=language,
                    slug='guide',
                    title={'en': 'Guide', 'zh': '指南'}[language],
                    markdown=body,
                    status='published',
                    route_status='published',
                )
                for language in ('en', 'zh')
            ],
            locales=self.locales,
            site_code='testSite',
            site_name='Vorntek',
            public_origin='https://example.invalid',
            brand_logo_url='/assets/vorntek/vorntekLogo.png',
        )

    def test_every_article_and_hub_has_one_h1_and_no_tracking_script(self):
        documents = render_article_documents(self.manifest())
        self.assertEqual(len(documents), 4)
        for path, text in documents.items():
            language = 'zh' if path.startswith('zh/') else 'en'
            nodes = Nodes(text).nodes
            self.assertEqual(sum(tag == 'h1' for tag, _attrs in nodes), 1, path)
            self.assertIn(('html', {'lang': language, 'dir': 'ltr'}), nodes)
            self.assertNotIn('meta-pixel', text)
            self.assertNotIn('locale-navigation.js', text)
            self.assertIn('noindex,nofollow', text)
            self.assertIn('Vorntek', text)
        self.assertIn(
            '<strong>not an excerpt</strong>',
            documents['zh/articles/guide/index.html'],
        )

    def test_raw_html_scripts_and_executable_links_cannot_run(self):
        body = '<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>\n\n[bad](javascript:alert%281%29)\n\n![bad](data:image/svg+xml,evil)'
        nodes = Nodes(render_markdown(body)).nodes
        self.assertFalse(any(tag in ('script', 'img', 'iframe') for tag, _attrs in nodes))
        self.assertFalse(
            any(attrs.get('href', '').startswith('javascript:') for _tag, attrs in nodes)
        )

    def test_tables_lists_images_and_all_paragraphs_are_preserved(self):
        body = '# Heading\n\nFirst paragraph.\n\n- One\n- Two\n\n| Rate | Unit |\n| --- | --- |\n| 12,000 | BPH |\n\n![Machine](assets/machine.jpg)\n\n[Details](/products/)\n\nLast paragraph.'
        text = render_markdown(body)
        self.assertIn('<h2>Heading</h2>', text)
        self.assertIn('article-table', text)
        self.assertIn('<td>12,000</td>', text)
        self.assertIn('<li>Two</li>', text)
        self.assertIn('src="/assets/machine.jpg"', text)
        self.assertIn('href="/products/"', text)
        self.assertIn('Last paragraph.', text)

    def test_only_exact_leading_duplicate_title_is_suppressed(self):
        result = render_markdown('# Guide\n\nFull text\n\n## Details', page_title='Guide')
        self.assertNotIn('>Guide<', result)
        self.assertIn('Full text', result)
        self.assertIn('<h2>Details</h2>', result)
        self.assertIn('<h2>Different</h2>', render_markdown('# Different', page_title='Guide'))

    def test_translated_article_links_use_explicit_content_identity(self):
        versions = [
            ArticleVersion(
                content_key='target',
                locale=language,
                slug=slug,
                source_path=f'/{language}/articles/{slug}/',
                title=slug,
                markdown='Target',
                status='published',
                route_status='published',
            )
            for language, slug in [('en', 'selection'), ('zh', 'selection-zh')]
        ]
        versions.append(
            ArticleVersion(
                content_key='reader',
                locale='zh',
                slug='reader',
                title='Reader',
                markdown='[Root](/articles/selection/?ref=guide#section)\n\n[Legacy](/en/articles/selection/)\n\n[Absolute](https://example.invalid/articles/selection/)\n\n[External](https://external.invalid/articles/selection/)\n\n[Unknown](/articles/not-migrated/)',
                status='published',
                route_status='published',
            )
        )
        release = build_article_release(
            versions,
            locales=self.locales,
            site_code='testSite',
            site_name='Vorntek',
            public_origin='https://example.invalid',
        )
        text = render_article_documents(release)['zh/articles/reader/index.html']
        self.assertIn('href="/zh/articles/selection-zh/?ref=guide#section"', text)
        self.assertIn('href="/zh/articles/selection-zh/">Legacy', text)
        self.assertIn('href="/zh/articles/selection-zh/">Absolute', text)
        self.assertIn('href="https://external.invalid/articles/selection/"', text)
        self.assertIn('href="/articles/not-migrated/"', text)

    def test_preview_and_live_robots_differ_but_both_remain_network_silent(self):
        release = self.manifest()
        preview = render_article_documents(release)['articles/guide/index.html']
        live = render_article_documents(release, preview=False)['articles/guide/index.html']
        self.assertIn('noindex,nofollow', preview)
        self.assertIn('index,follow,max-image-preview:large', live)
        for text in (preview, live):
            self.assertNotIn('meta-pixel', text)
            self.assertEqual(text.count('<script'), 1)

    def test_title_and_schema_json_cannot_break_out_of_script(self):
        release = build_article_release(
            [
                ArticleVersion(
                    content_key='guide',
                    locale='en',
                    slug='guide',
                    title='</script><script>alert(1)</script>',
                    markdown='Full text',
                    status='published',
                    route_status='published',
                )
            ],
            locales=self.locales,
            site_code='testSite',
            site_name='Vorntek',
            public_origin='https://example.invalid',
        )
        text = render_article_documents(release)['articles/guide/index.html']
        self.assertEqual(text.count('<script'), 1)
        self.assertIn('\\u003c/script\\u003e', text)

    def test_tampered_release_is_refused(self):
        release = self.manifest()
        release['articles'][0]['title'] = 'Changed after freeze'
        with self.assertRaises(ArticleDeliveryError):
            render_article_documents(release)


if __name__ == '__main__':
    unittest.main()
