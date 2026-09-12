from django.core.paginator import Paginator
from django.test import RequestFactory, SimpleTestCase

from .leads_workspace import _page_meta, _requested_page_size


class LeadsWorkspacePaginationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_submission_page_size_defaults_to_fifty(self):
        request = self.factory.get('/admin/leads/')

        self.assertEqual(_requested_page_size(request, 'submissions'), 50)

    def test_page_size_accepts_supported_values_and_rejects_others(self):
        supported = self.factory.get('/admin/leads/', {'page_size': '100'})
        unsupported = self.factory.get('/admin/leads/', {'page_size': '250'})

        self.assertEqual(_requested_page_size(supported, 'submissions'), 100)
        self.assertEqual(_requested_page_size(unsupported, 'submissions'), 50)

    def test_page_meta_builds_numbered_navigation_and_preserves_filters(self):
        page = Paginator(range(240), 50).page(3)

        meta = _page_meta(
            page,
            'en',
            extra={'tab': 'submissions', 'stage': 'new', 'page_size': 50},
        )

        self.assertEqual(meta['label'], '共 240 条')
        self.assertEqual(meta['range_label'], '101-150 / 240')
        self.assertEqual(meta['page_size'], 50)
        self.assertTrue(any(item.get('active') and item['label'] == '3' for item in meta['pages']))
        self.assertIn('stage=new', meta['prev_href'])
        self.assertIn('page_size=50', meta['next_href'])
        self.assertIn({'name': 'locale', 'value': 'en'}, meta['preserved_params'])
