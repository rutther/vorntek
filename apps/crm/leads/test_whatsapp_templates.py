import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from leads.whatsapp_cloud import WhatsAppCloudError
from leads.whatsapp_templates import _graph_url, _normalized_template, fetch_whatsapp_templates


class _TemplateResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.body


class WhatsAppTemplateSyncTests(SimpleTestCase):
    def test_meta_template_is_normalized_without_secret_material(self):
        item = _normalized_template({
            'id': 'template-1',
            'name': 'project_follow_up',
            'language': 'en-US',
            'category': 'UTILITY',
            'status': 'APPROVED',
            'components': [{'type': 'BODY', 'text': 'Hello {{1}}'}],
        })
        self.assertEqual(item['language'], 'en_US')
        self.assertEqual(item['category'], 'utility')
        self.assertEqual(item['status'], 'approved')
        self.assertEqual(item['provider_template_id'], 'template-1')

    def test_paging_url_is_pinned_to_graph_facebook_com(self):
        self.assertEqual(
            _graph_url('https://graph.facebook.com/v23.0/123/message_templates'),
            'https://graph.facebook.com/v23.0/123/message_templates',
        )
        for value in ('http://graph.facebook.com/x', 'https://example.com/x'):
            with self.subTest(value=value), self.assertRaises(WhatsAppCloudError):
                _graph_url(value)

    @patch('leads.whatsapp_templates.integration_secret', return_value='ycloud-key')
    @patch('leads.whatsapp_templates.urllib.request.urlopen')
    def test_ycloud_template_sync_reads_only_configured_waba(self, urlopen, _secret):
        urlopen.return_value = _TemplateResponse({
            'items': [
                {
                    'id': 'template-ycloud-1',
                    'name': 'project_follow_up',
                    'language': 'en',
                    'category': 'UTILITY',
                    'status': 'APPROVED',
                    'components': [],
                }
            ],
            'total': 1,
        })
        integration = SimpleNamespace(
            config_json={
                'delivery_mode': 'live',
                'transport_provider': 'ycloud',
                'waba_id': '4185566811756317',
            },
        )

        templates = fetch_whatsapp_templates(integration)

        self.assertEqual(templates[0]['id'], 'template-ycloud-1')
        request = urlopen.call_args.args[0]
        self.assertIn('filter.wabaId=4185566811756317', request.full_url)
        self.assertEqual(request.get_header('X-api-key'), 'ycloud-key')
