"""Exercise complete site generation and its untrusted-content boundaries."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def builder():
    spec = importlib.util.spec_from_file_location('corporate_builder', ROOT/'scripts/build_vorntek_site.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Refs(HTMLParser):
    def __init__(self):
        super().__init__(); self.refs=[]; self.headings=0; self.ids=[]

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        self.headings += tag=='h1'
        if attrs.get('id'): self.ids.append(attrs['id'])
        for key in ('src','href','poster'):
            if attrs.get(key): self.refs.append(attrs[key])


class CorporateSiteTests(unittest.TestCase):
    def catalog(self):
        return json.loads((ROOT/'docs/vorntekDemo/catalog.json').read_text(encoding='utf-8'))

    def test_product_and_solution_journeys_have_real_local_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'website'
            builder().main(catalog=self.catalog(),output=output)
            for route in ('products/inspect','products/measure','products/assemble','products/convey',
                          'products/mark','products/connect','products/series/inspection',
                          'solutions/component-inspection','solutions/assembly-cell',
                          'solutions/line-retrofit','resources','demo'):
                self.assertTrue((output/route/'index.html').is_file(),route)
            for path in output.rglob('*.html'):
                parser=Refs();parser.feed(path.read_text(encoding='utf-8'))
                self.assertEqual(parser.headings,1,path)
                self.assertEqual(len(parser.ids),len(set(parser.ids)),path)
                for ref in parser.refs:
                    url=urlsplit(ref)
                    if url.scheme or url.netloc or not url.path or url.path.startswith('/admin/'):
                        continue
                    # Managed articles are merged by the existing release pipeline.
                    if '/articles/' in url.path: continue
                    target=output/url.path.lstrip('/')
                    if url.path.endswith('/'):target=target/'index.html'
                    self.assertTrue(target.is_file(),f'{path}: {ref}')

    def test_catalogue_escape_and_route_validation_apply_before_any_output(self):
        catalog=self.catalog()
        catalog['productLines'][0]['name']='<script>not executable</script>'
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'escaped'
            builder().main(catalog=catalog,output=output)
            self.assertTrue((output/'products/inspect/index.html').is_file())
            page=(output/'products/inspect/index.html').read_text(encoding='utf-8')
            self.assertIn('&lt;script&gt;not executable&lt;/script&gt;',page)
            self.assertNotIn('<script>not executable</script>',page)
            for bad in ('../outside','/absolute','nested/route'):
                catalog['productLines'][0]['id']=bad
                target=Path(temporary)/'must-not-exist'
                with self.assertRaises(ValueError):builder().main(catalog=catalog,output=target)
                self.assertFalse(target.exists())

    def test_business_content_is_separate_from_demo_explanation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'site'
            builder().main(catalog=self.catalog(),output=output)
            company=(output/'company/index.html').read_text(encoding='utf-8')
            for wrong in ('Real software workflows','软件操作是真实的','不是软件固定分类','evidence needed for the next decision'):
                self.assertNotIn(wrong,company)
            product=(output/'products/inspect/index.html').read_text(encoding='utf-8')
            self.assertIn('data-product-context="inspect"',product)
            self.assertIn('data-configured-form', (output/'contact/index.html').read_text(encoding='utf-8'))
            self.assertTrue((output/'assets/vorntek/models/inspect.glb').stat().st_size>10000)

    def test_dimension_drawing_uses_the_equipment_envelope_and_cannot_accept_nonfinite_sizes(self):
        catalog=self.catalog()
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'drawings'
            builder().main(catalog=catalog,output=output)
            page=(output/'products/inspect/index.html').read_text(encoding='utf-8')
            drawings=re.findall(r'<svg class="dimension-drawing".*?</svg>',page,re.S)
            self.assertEqual(len(drawings),1)
            diagram=ET.fromstring(drawings[0])
            self.assertEqual(len(diagram.findall('rect')),2)
            text=' '.join(diagram.itertext())
            for label in ('W 1000 mm','H 1800 mm','D 800 mm'):
                self.assertIn(label,text)
            catalog['productLines'][0]['dimensions']=[1000,float('nan'),800]
            with self.assertRaises(ValueError):
                builder().main(catalog=catalog,output=Path(temporary)/'invalid')


if __name__=='__main__':unittest.main()
