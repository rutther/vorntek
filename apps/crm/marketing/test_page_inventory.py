"""Published files, not guessed source pages, drive the Pixel scope selector."""
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from marketing.pixel_catalog import pixel_catalog


class PageInventoryTests(SimpleTestCase):
    def test_bundled_forms_declare_the_crm_form_identity(self):
        from django.conf import settings
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'catalog.json'
            bundled = settings.BASE_DIR / 'website-source'
            if not bundled.is_dir():
                bundled = settings.BASE_DIR.parent / 'website'
            call_command('refresh_pixel_page_catalog', root=bundled,
                output=output, site_code='siteos_demo', stdout=io.StringIO())
            pages = {page['path']: page for page in json.loads(output.read_text(encoding='utf-8'))['pages']}
            self.assertEqual(pages['/']['formCodes'], ['project-inquiry'])
            self.assertEqual(pages['/contact/']['formCodes'], ['project-inquiry'])

    def test_catalog_uses_explicit_form_metadata_and_public_directory_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'published'
            root.mkdir()
            (root / 'index.html').write_text('<html lang="en"><title>Example &amp; demo</title>'
                '<form data-form-code="general-request"></form><form id="project-form"></form>', encoding='utf-8')
            (root / 'contact').mkdir()
            (root / 'contact/index.html').write_text('<html lang="zh-CN"><title>联系</title>'
                '<form data-form-code="service-request"></form><form data-form-code="../bad"></form>', encoding='utf-8')
            (root / 'articles').mkdir()
            (root / 'articles/story').mkdir()
            (root / 'articles/story/index.html').write_text('<title>Example article</title>', encoding='utf-8')
            for name in ('admin', 'api', '.private', 'node_modules'):
                (root / name).mkdir()
                (root / name / 'index.html').write_text('<form data-form-code="hidden">', encoding='utf-8')
            output = Path(directory) / 'runtime/catalog.json'
            with patch('socket.create_connection', side_effect=AssertionError('No network in inventory')):
                call_command('refresh_pixel_page_catalog', root=root, output=output,
                    site_code='synthetic-site', stdout=io.StringIO())
            data = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(data['siteCode'], 'synthetic-site')
            self.assertEqual([(p['path'], p['title'], p['locale'], p['formCodes']) for p in data['pages']], [
                ('/', 'Example & demo', 'en', ['general-request']),
                ('/articles/story/', 'Example article', '', []),
                ('/contact/', '联系', 'zh-CN', ['service-request']),
            ])
            self.assertTrue(all(len(p['releaseHash']) == 64 for p in data['pages']))
            with override_settings(SITEOS_PIXEL_CATALOG_PATH=output):
                from types import SimpleNamespace
                read = pixel_catalog(SimpleNamespace(code='synthetic-site'))
                self.assertTrue(read['available'])
                self.assertEqual(len(read['pages']), 3)
                self.assertFalse(pixel_catalog(SimpleNamespace(code='other-site'))['available'])

    def test_missing_served_root_does_not_catalogue_a_different_source_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / 'source'
            source.mkdir()
            (source / 'index.html').write_text('<title>Not deployed</title>', encoding='utf-8')
            output = directory / 'catalog.json'
            output.write_text('{"previous":"preserved"}', encoding='utf-8')
            with override_settings(SITEOS_WEBSITE_SERVING_ROOT=directory / 'serving',
                                   SITEOS_WEBSITE_SOURCE_ROOT=source,
                                   SITEOS_PIXEL_CATALOG_PATH=output):
                with self.assertRaises(CommandError):
                    call_command('refresh_pixel_page_catalog', stdout=io.StringIO())
            self.assertEqual(output.read_text(encoding='utf-8'), '{"previous":"preserved"}')

    def test_oversized_page_is_not_used_as_form_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('<title>Home</title>', encoding='utf-8')
            (root / 'huge').mkdir()
            (root / 'huge/index.html').write_text('<form data-form-code="not-trusted">' + 'x' * 3_000_000, encoding='utf-8')
            output = root / '.runtime/catalog.json'
            call_command('refresh_pixel_page_catalog', root=root, output=output,
                site_code='synthetic-site', stdout=io.StringIO())
            self.assertEqual([p['path'] for p in json.loads(output.read_text())['pages']], ['/'])
