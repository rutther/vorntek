from dataclasses import replace
import json
import unittest

from .article_delivery import (
    ArticleDeliveryError,
    ArticleLocale,
    ArticleVersion,
    build_article_release,
)


class ArticleDeliveryContractTests(unittest.TestCase):
    locales = [
        ArticleLocale('en', 'English', is_default=True),
        ArticleLocale('zh', '中文'),
    ]

    def version(self, locale='en', **changes):
        value = ArticleVersion(
            content_key='engineeringGuide',
            locale=locale,
            slug='engineering_guide',
            public_slug='engineering-guide',
            source_path=f'/{locale}/articles/engineering_guide/',
            title={'en': 'Engineering guide', 'zh': '工程指南'}[locale],
            markdown='## Scope\n\nFull paragraph.\n\n| Capacity | Value |\n| --- | --- |\n| Rate | 12,000 |',
            status='published',
            route_status='published',
            category_key='guides',
            category_label='Guides',
            category_locale=locale,
        )
        return replace(value, **changes)

    def build(self, rows, **changes):
        arguments = {
            'locales': self.locales,
            'site_code': 'siteos_demo',
            'site_name': 'Vorntek',
            'public_origin': 'https://example.invalid',
            'brand_logo_url': '/assets/vorntek/vorntekLogo.png',
        }
        arguments.update(changes)
        return build_article_release(rows, **arguments)

    def assert_invalid(self, code, rows, **kwargs):
        with self.assertRaises(ArticleDeliveryError) as raised:
            self.build(rows, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_two_language_content_has_reciprocal_explicit_links(self):
        release = self.build([self.version('en'), self.version('zh')])

        self.assertEqual(release['missingTranslations'], [])
        self.assertEqual(release['defaultLocale'], 'en')
        self.assertEqual(release['siteName'], 'Vorntek')
        self.assertEqual(len(release['articles']), 2)
        for article in release['articles']:
            self.assertEqual(set(article['alternates']), {'en', 'zh', 'x-default'})
            self.assertEqual(article['canonical'], article['alternates'][article['language']])

    def test_missing_translation_is_explicit_and_never_filled(self):
        release = self.build([self.version()])

        self.assertEqual(
            release['missingTranslations'],
            [{'contentKey': 'engineeringGuide', 'languages': ['zh']}],
        )
        self.assertNotIn('zh', release['articles'][0]['alternates'])

    def test_unpublished_records_never_enter_the_release(self):
        for changes in (
            {'status': 'draft'},
            {'status': 'archived'},
            {'enabled': False},
            {'route_status': 'draft'},
        ):
            with self.subTest(changes=changes):
                release = self.build([self.version(**changes)])
                self.assertEqual(release['articles'], [])
                self.assertEqual(release['redirects'], {})

    def test_release_is_deterministic_and_full_content_changes_identity(self):
        rows = [self.version('en'), self.version('zh')]
        self.assertEqual(self.build(rows), self.build(list(reversed(rows))))
        changed = [replace(rows[0], markdown=rows[0].markdown + '\n\nNew detail.'), rows[1]]
        self.assertNotEqual(
            self.build(rows)['releaseSha256'], self.build(changed)['releaseSha256']
        )

    def test_duplicate_identity_path_and_invalid_content_fail_closed(self):
        self.assert_invalid(
            'duplicate_content_locale',
            [self.version(), self.version(public_slug='another')],
        )
        self.assert_invalid(
            'duplicate_public_path',
            [self.version(), self.version(content_key='otherGuide')],
        )
        self.assert_invalid('missing_content_identity', [self.version(content_key='')])
        self.assert_invalid('empty_published_content', [self.version(markdown=' ')])
        self.assert_invalid(
            'category_locale_mismatch', [self.version('zh', category_locale='en')]
        )
        self.assert_invalid(
            'unsupported_locale', [replace(self.version(), locale='fr')]
        )

    def test_slug_origin_asset_and_alias_boundaries(self):
        for slug in ('../admin', 'guide?private', 'guide%2fadmin', '/guide', 'guide#secret'):
            self.assert_invalid('invalid_article_slug', [self.version(public_slug=slug)])
        self.assert_invalid('unexpected_source_path', [self.version(source_path='/admin/')])
        self.assertEqual(
            self.build([self.version()])['redirects'],
            {'/en/articles/engineering_guide/': '/articles/engineering-guide/'},
        )
        for origin in (
            'http://127.0.0.1:8088',
            'https://10.0.0.1',
            'https://localhost',
            'https://user:secret@example.invalid',
            'https://example.invalid/path',
            'https://example.invalid?token=secret',
        ):
            self.assert_invalid('invalid_public_origin', [], public_origin=origin)
        for value in (
            'javascript:alert(1)',
            'data:text/html,x',
            '//evil.example/x',
            '/assets/../private',
            'https://img.example/a?token=secret',
        ):
            self.assert_invalid('invalid_asset_url', [self.version(cover_url=value)])

    def test_locale_contract_requires_one_default_and_safe_metadata(self):
        for locales, code in (
            ([], 'locales_required'),
            ([ArticleLocale('en', 'English')], 'one_default_locale_required'),
            (
                [
                    ArticleLocale('en', 'English', is_default=True),
                    ArticleLocale('zh', '中文', is_default=True),
                ],
                'one_default_locale_required',
            ),
            ([ArticleLocale('../en', 'English', is_default=True)], 'invalid_locale'),
            ([ArticleLocale('en', '', is_default=True)], 'invalid_locale_label'),
        ):
            with self.subTest(code=code):
                self.assert_invalid(code, [], locales=locales)

    def test_base_route_mode_is_digest_bound_and_validated(self):
        localized = self.build([self.version()], base_route_mode='localized')
        shared = self.build([self.version()], base_route_mode='shared')
        self.assertEqual(shared['baseRouteMode'], 'shared')
        self.assertNotEqual(localized['releaseSha256'], shared['releaseSha256'])
        self.assert_invalid('invalid_base_route_mode', [], base_route_mode='unknown')

    def test_release_contains_no_editor_or_model_configuration(self):
        release = self.build([self.version()])
        serialized = json.dumps(release)
        self.assertNotIn('config_json', serialized)
        self.assertNotIn('source_path', serialized)
        self.assertEqual(self.build([])['articles'], [])


if __name__ == '__main__':
    unittest.main()
