import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT/'apps/website'


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []
        self.h1 = 0
        self.fields = set()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.h1 += tag == 'h1'
        if attrs.get('id'):
            self.ids.append(attrs['id'])
        for key in ('href', 'src'):
            if attrs.get(key):
                self.refs.append(attrs[key])
        if tag in ('input', 'select', 'textarea'):
            self.fields.add(attrs.get('name'))


class VorntekWebsiteTests(unittest.TestCase):
    def test_default_public_content_has_no_retired_customer_or_unrelated_business(self):
        for path in SITE.rglob('*.html'):
            source = path.read_text(encoding='utf-8').lower()
            for retired in ('circulating water', 'propane', 'turbojet',
                            '循环水', '丙烷', '涡喷', 'vorntekworkshop.png', 'vorntekpumpskid.png'):
                with self.subTest(page=str(path.relative_to(SITE)), retired=retired):
                    self.assertNotIn(retired, source)

    def test_configured_catalogue_supports_unrelated_ids_and_escapes_content(self):
        spec = importlib.util.spec_from_file_location('site_builder', ROOT/'scripts/build_vorntek_site.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        catalog = json.loads((ROOT/'docs/vorntekDemo/catalog.json').read_text(encoding='utf-8'))
        catalog['productLines'] = [{
            'id':'personal-advice', 'name':'Advice <example>', 'nameZh':'个人咨询示例',
            'headline':'Start with a question.', 'headlineZh':'从一个问题开始。',
            'description':'A synthetic personal request, not equipment procurement.',
            'descriptionZh':'合成个人请求，不需要企业资料。',
            'features':[{'en':'Clarify the question','zh':'明确问题'}], 'asset':None,
        }, {
            'id':'project-review', 'name':'Project review', 'nameZh':'项目评估',
            'headline':'Review before a decision.', 'headlineZh':'决定前先评估。',
            'description':'A separate synthetic team scenario.', 'descriptionZh':'独立团队合成场景。',
            'features':[{'en':'Define the scope','zh':'确定范围'}], 'asset':None,
        }]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'site'
            builder.main(catalog=catalog, output=output)
            self.assertTrue((output/'products/personal-advice/index.html').is_file())
            self.assertFalse((output/'products/inspect/index.html').exists())
            personal = (output/'products/personal-advice/index.html').read_text(encoding='utf-8')
            listing = (output/'products/index.html').read_text(encoding='utf-8')
            self.assertIn('Advice &lt;example&gt;', personal)
            self.assertIn('Clarify the question', personal)
            self.assertIn('/products/project-review/', listing)
            self.assertNotIn('circulatingWaterControl', listing)
            self.assertTrue((output/'contact/index.html').is_file())
            self.assertIn('data-configured-form', (output/'contact/index.html').read_text(encoding='utf-8'))
            parser=PageParser()
            parser.feed(personal)
            for ref in parser.refs:
                path=urlsplit(ref).path
                if not path or path.startswith('/admin/') or '/articles/' in path:
                    continue
                target=output/path.lstrip('/')
                self.assertTrue((target/'index.html' if path.endswith('/') else target).is_file(),ref)

    def test_bad_catalogue_routes_refused_before_output(self):
        spec = importlib.util.spec_from_file_location('site_builder', ROOT/'scripts/build_vorntek_site.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        catalog = json.loads((ROOT/'docs/vorntekDemo/catalog.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            for bad in ('../escape','/absolute','same/route'):
                catalog['productLines'][0]['id']=bad
                output=Path(directory)/'site'
                with self.assertRaises(ValueError):
                    builder.main(catalog=catalog, output=output)
                self.assertFalse(output.exists())

    def test_empty_catalogue_keeps_navigation_contact_and_non_catalogue_pages(self):
        spec=importlib.util.spec_from_file_location('site_builder',ROOT/'scripts/build_vorntek_site.py')
        builder=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        catalog=json.loads((ROOT/'docs/vorntekDemo/catalog.json').read_text(encoding='utf-8'))
        catalog['productLines']=[]
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'site'
            builder.main(catalog=catalog,output=output)
            self.assertFalse(list((output/'products').glob('*/index.html')) and
                             (output/'products/inspect/index.html').exists())
            self.assertIn('data-configured-form',(output/'contact/index.html').read_text(encoding='utf-8'))
            self.assertIn('/company/',(output/'index.html').read_text(encoding='utf-8'))

    def test_default_forms_load_configured_fields_without_fixed_industry_controls(self):
        for route in ('index.html', 'contact/index.html'):
            source = (SITE / route).read_text(encoding='utf-8')
            self.assertIn('data-configured-form', source)
            self.assertIn('data-inquiry-fields', source)
            self.assertIn('/inquiry-form.js?v=', source)
            self.assertNotIn('name="business_line"', source)
            self.assertNotIn('name="capacity"', source)

        runtime = (SITE / 'inquiry-form.js').read_text(encoding='utf-8')
        self.assertIn('schema_version:schemaVersion', runtime)
        self.assertIn("'?locale='+encodeURIComponent(locale)", runtime)
        self.assertIn("base+'definition/'+localeQuery", runtime)
        self.assertIn("base+'submit/'+localeQuery", runtime)

    def test_all_pages_have_one_heading_no_broken_public_links_or_duplicate_ids(self):
        pages = list(SITE.rglob('*.html'))
        manifest=json.loads((SITE/'corporate-build.json').read_text(encoding='utf-8'))
        expected={route if route.endswith('.html') else (route+'/' if route else '')+'index.html' for route in manifest['routes']}
        self.assertEqual({path.relative_to(SITE).as_posix() for path in pages},expected)
        for path in pages:
            with self.subTest(page=str(path.relative_to(SITE))):
                source = path.read_text(encoding='utf-8')
                parser = PageParser()
                parser.feed(source)
                self.assertEqual(parser.h1, 1)
                self.assertEqual(len(parser.ids), len(set(parser.ids)))
                for old in ('New Crown', 'filline.com', 'bottles/hour', 'newcrownmachine'):
                    self.assertNotIn(old.lower(), source.lower())
                self.assertIn('Vorntek', source)
                for ref in parser.refs:
                    parts = urlsplit(ref)
                    self.assertFalse(parts.netloc, ref)
                    if not parts.path or parts.path.startswith('/admin/') or '/articles/' in parts.path:
                        continue
                    target = SITE/parts.path.lstrip('/')
                    if parts.path.endswith('/'):
                        target = target/'index.html'
                    self.assertTrue(target.is_file(), ref)
                    if target.suffix in ('.js', '.css') and '/vendor/' not in parts.path:
                        checksum = hashlib.sha256(target.read_text(encoding='utf-8').encode()).hexdigest()[:12]
                        self.assertEqual(parse_qs(parts.query).get('v'), [checksum])
                if 'business_line' in parser.fields:
                    self.assertNotIn('capacity', parser.fields)
                    self.assertTrue({'company', 'country', 'email', 'phone', 'message', 'application_context', 'consent'} <= parser.fields)

    def test_published_visuals_match_neutral_sources_and_preserve_logo(self):
        originals = ROOT/'docs/vorntekDemo/assets'
        expected = {name: hashlib.sha256((originals/name).read_bytes()).hexdigest() for name in
            ('vorntekLogo.png','example-conversation.svg','example-process.svg','example-collaboration.svg')}
        for name,checksum in expected.items():
            self.assertEqual(hashlib.sha256((SITE/'assets/vorntek'/name).read_bytes()).hexdigest(),checksum)
        catalog=json.loads((ROOT/'docs/vorntekDemo/catalog.json').read_text(encoding='utf-8'))
        for row in catalog['productLines']:
            self.assertTrue((SITE/'assets/vorntek/models'/row['model']).is_file())
        mark = ROOT/'apps/crm/console/static/console/vorntekLogo.png'
        self.assertEqual(hashlib.sha256(mark.read_bytes()).hexdigest(), expected['vorntekLogo.png'])
        self.assertFalse((mark.parent/'new-crown-mark.png').exists())

    def test_catalogue_entries_are_example_content_not_fixed_backend_enums(self):
        catalog = json.loads((ROOT/'docs/vorntekDemo/catalog.json').read_text(encoding='utf-8'))
        self.assertTrue(catalog['demoOnly'])
        self.assertEqual(catalog['brand']['contactPlaceholder'],'inquiries@example.invalid')
        for entry in catalog['productLines']:
            page=(SITE/'products'/entry['id']/'index.html').read_text(encoding='utf-8')
            self.assertIn('name="robots" content="noindex,nofollow"',page)
            self.assertNotIn('Fictional example only.',page)
            self.assertIn('/demo/',page)

    def test_clean_generator_reproduces_checked_in_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in ('scripts/build_vorntek_site.py', 'scripts/corporate_site.py',
                         'docs/vorntekDemo/catalog.json','docs/vorntekDemo/pages.json',
                         'apps/website/app.js', 'apps/website/inquiry-form.js',
                         'apps/website/styles.css', 'apps/website/assets/meta-pixel.js'):
                destination = target/name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT/name, destination)
            shutil.copytree(ROOT/'docs/vorntekDemo/assets',target/'docs/vorntekDemo/assets')
            shutil.copytree(ROOT/'docs/vorntekDemo/templates',target/'docs/vorntekDemo/templates')
            shutil.copytree(ROOT/'apps/crm/console/static/console/vendor/model-viewer',target/'apps/crm/console/static/console/vendor/model-viewer')
            subprocess.run([sys.executable, str(target/'scripts/build_vorntek_site.py')], check=True, capture_output=True)
            built = target/'apps/website'
            self.assertEqual({str(p.relative_to(built)) for p in built.rglob('*.html')},
                             {str(p.relative_to(SITE)) for p in SITE.rglob('*.html')})
            for path in SITE.rglob('*.html'):
                self.assertEqual(path.read_text(encoding='utf-8'), (built/path.relative_to(SITE)).read_text(encoding='utf-8'))
