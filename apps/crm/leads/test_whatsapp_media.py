from email.message import Message
from unittest.mock import patch

from django.test import SimpleTestCase

from leads.whatsapp_cloud import WhatsAppCloudError
from leads.whatsapp_media import (
    MockWhatsAppMediaClient,
    YCloudWhatsAppMediaClient,
    _safe_meta_media_url,
    _safe_ycloud_media_url,
)


class _MediaResponse:
    def __init__(self, body: bytes, url: str, mime_type: str):
        self.body = body
        self.url = url
        self.headers = Message()
        self.headers['Content-Type'] = mime_type
        self.headers['Content-Length'] = str(len(body))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def geturl(self):
        return self.url


class WhatsAppMediaClientTests(SimpleTestCase):
    def test_mock_download_is_deterministic_and_network_free(self):
        client = MockWhatsAppMediaClient()
        first = client.download(external_media_id='media-1', expected_mime_type='image/jpeg')
        second = client.download(external_media_id='media-1', expected_mime_type='image/jpeg')
        self.assertEqual(first, second)
        self.assertEqual(first.mime_type, 'image/jpeg')
        self.assertTrue(first.content)

    def test_media_url_requires_https_and_meta_owned_host(self):
        self.assertEqual(
            _safe_meta_media_url('https://lookaside.fbsbx.com/whatsapp_business/attachments/1'),
            'https://lookaside.fbsbx.com/whatsapp_business/attachments/1',
        )
        for value in ('http://lookaside.fbsbx.com/1', 'https://example.com/1', 'file:///tmp/1'):
            with self.subTest(value=value), self.assertRaises(WhatsAppCloudError):
                _safe_meta_media_url(value)

    def test_ycloud_media_url_is_pinned_to_authenticated_download_endpoint(self):
        valid = 'https://api.ycloud.com/v2/whatsapp/media/download/123?sig=redacted'
        self.assertEqual(_safe_ycloud_media_url(valid), valid)
        for value in (
            'http://api.ycloud.com/v2/whatsapp/media/download/123',
            'https://api.ycloud.com/v2/other/123',
            'https://evil.example/v2/whatsapp/media/download/123',
            'https://user@api.ycloud.com/v2/whatsapp/media/download/123',
        ):
            with self.subTest(value=value), self.assertRaises(WhatsAppCloudError):
                _safe_ycloud_media_url(value)

    @patch('leads.whatsapp_media.urllib.request.urlopen')
    def test_ycloud_media_download_uses_api_key_and_size_bounded_reader(self, urlopen):
        url = 'https://api.ycloud.com/v2/whatsapp/media/download/123?sig=redacted'
        urlopen.return_value = _MediaResponse(b'jpeg-bytes', url, 'image/jpeg')
        client = YCloudWhatsAppMediaClient.__new__(YCloudWhatsAppMediaClient)
        client.api_key = 'unit-test-api-key'
        client.timeout = 2

        result = client.download(
            external_media_id='123',
            expected_mime_type='image/jpeg',
            download_url=url,
        )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header('X-api-key'), 'unit-test-api-key')
        self.assertEqual(result.content, b'jpeg-bytes')
        self.assertEqual(result.mime_type, 'image/jpeg')
