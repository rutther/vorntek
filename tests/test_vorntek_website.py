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
    def test_all_pages_have_one_heading_no_broken_public_links_or_duplicate_ids(self):
        pages = list(SITE.rglob('*.html'))
        self.assertEqual(len(pages), 15)
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
                    if not parts.path or parts.path.startswith('/admin/'):
                        continue
                    target = SITE/parts.path.lstrip('/')
                    if parts.path.endswith('/'):
                        target = target/'index.html'
                    self.assertTrue(target.is_file(), ref)
                    if target.suffix in ('.js', '.css'):
                        checksum = hashlib.sha256(target.read_text(encoding='utf-8').encode()).hexdigest()[:12]
                        self.assertEqual(parse_qs(parts.query).get('v'), [checksum])
                if 'business_line' in parser.fields:
                    self.assertNotIn('capacity', parser.fields)
                    self.assertTrue({'company', 'country', 'email', 'phone', 'message', 'application_context', 'consent'} <= parser.fields)

    def test_published_bitmaps_match_the_eight_ai_originals(self):
        originals = ROOT/'docs/vorntekDemo/assets'
        expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in originals.glob('*.png')}
        self.assertEqual(len(expected), 8)
        public = list(SITE.rglob('*.png'))
        self.assertEqual({p.name for p in public}, set(expected))
        self.assertFalse(list(SITE.rglob('*.jpg')))
        for path in public:
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected[path.name])
        mark = ROOT/'apps/crm/console/static/console/vorntekLogo.png'
        self.assertEqual(hashlib.sha256(mark.read_bytes()).hexdigest(), expected['vorntekLogo.png'])
        self.assertFalse((mark.parent/'new-crown-mark.png').exists())

    def test_catalogue_and_backend_share_business_identifiers(self):
        spec = importlib.util.spec_from_file_location('industrial_catalogue', ROOT/'apps/crm/leads/industrial.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        catalog = json.loads((ROOT/'docs/vorntekDemo/catalog.json').read_text(encoding='utf-8'))
        self.assertEqual({p['id'] for p in catalog['productLines']}, set(module.BUSINESS_LINES))

    def test_clean_generator_reproduces_checked_in_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in ('scripts/build_vorntek_site.py', 'docs/vorntekDemo/catalog.json',
                         'apps/website/app.js', 'apps/website/styles.css', 'apps/website/assets/meta-pixel.js'):
                destination = target/name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT/name, destination)
            subprocess.run([sys.executable, str(target/'scripts/build_vorntek_site.py')], check=True, capture_output=True)
            built = target/'apps/website'
            self.assertEqual({str(p.relative_to(built)) for p in built.rglob('*.html')},
                             {str(p.relative_to(SITE)) for p in SITE.rglob('*.html')})
            for path in SITE.rglob('*.html'):
                self.assertEqual(path.read_text(encoding='utf-8'), (built/path.relative_to(SITE)).read_text(encoding='utf-8'))
