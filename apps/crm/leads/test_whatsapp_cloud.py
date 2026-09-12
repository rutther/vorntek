from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import urllib.error
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from leads.whatsapp_cloud import (
    GraphWhatsAppCloudClient,
    MockWhatsAppCloudClient,
    WhatsAppCloudError,
    WhatsAppPolicyError,
    client_for_integration,
)


class WhatsAppCloudClientTests(SimpleTestCase):
    def graph_client(self) -> GraphWhatsAppCloudClient:
        client = GraphWhatsAppCloudClient.__new__(GraphWhatsAppCloudClient)
        client.token = 'unit-test-secret-token'
        client.phone_number_id = '123456789'
        client.api_version = 'v23.0'
        client.timeout = 2
        return client

    def http_error(self, status: int, body: bytes) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            'https://graph.facebook.com/test',
            status,
            'error',
            {'x-fb-trace-id': 'trace-1'},
            BytesIO(body),
        )

    def test_mock_send_is_deterministic_for_same_idempotent_request(self):
        client = MockWhatsAppCloudClient()
        payload = {
            'messaging_product': 'whatsapp',
            'to': '971501234567',
            'type': 'text',
            'text': {'body': 'Hello'},
        }

        first = client.send_message(payload=payload, idempotency_key='a' * 64)
        second = client.send_message(payload=payload, idempotency_key='a' * 64)

        self.assertEqual(first.external_message_id, second.external_message_id)
        self.assertTrue(first.external_message_id.startswith('wamid.mock.'))
        self.assertTrue(first.response_json['mock'])

    @patch('leads.whatsapp_cloud.urllib.request.urlopen')
    def test_graph_429_is_retryable_without_exposing_token(self, mocked_urlopen):
        mocked_urlopen.side_effect = self.http_error(
            429,
            b'{"error":{"code":130429,"message":"Rate limit"}}',
        )
        client = self.graph_client()

        with self.assertRaises(WhatsAppCloudError) as raised:
            client._request(url='https://graph.facebook.com/test', body=b'{}', content_type='application/json')

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.code, '130429')
        self.assertNotIn(client.token, str(raised.exception))

    @patch('leads.whatsapp_cloud.urllib.request.urlopen')
    def test_graph_400_is_not_retryable(self, mocked_urlopen):
        mocked_urlopen.side_effect = self.http_error(
            400,
            b'{"error":{"code":131009,"message":"Invalid parameter"}}',
        )
        client = self.graph_client()

        with self.assertRaises(WhatsAppCloudError) as raised:
            client._request(url='https://graph.facebook.com/test', body=b'{}', content_type='application/json')

        self.assertFalse(raised.exception.retryable)
        self.assertFalse(raised.exception.ambiguous)
        self.assertEqual(raised.exception.http_status, 400)

    @patch('leads.whatsapp_cloud.urllib.request.urlopen')
    def test_graph_5xx_is_retryable(self, mocked_urlopen):
        mocked_urlopen.side_effect = self.http_error(
            503,
            b'{"error":{"code":2,"message":"Temporarily unavailable"}}',
        )
        client = self.graph_client()

        with self.assertRaises(WhatsAppCloudError) as raised:
            client._request(url='https://graph.facebook.com/test', body=b'{}', content_type='application/json')

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.http_status, 503)

    @patch('leads.whatsapp_cloud.urllib.request.urlopen')
    def test_transport_timeout_is_ambiguous_and_not_auto_retryable(self, mocked_urlopen):
        mocked_urlopen.side_effect = urllib.error.URLError('timed out')
        client = self.graph_client()

        with self.assertRaises(WhatsAppCloudError) as raised:
            client._request(url='https://graph.facebook.com/test', body=b'{}', content_type='application/json')

        self.assertTrue(raised.exception.ambiguous)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.code, 'ambiguous_transport')

    def test_mock_is_default_delivery_mode(self):
        integration = SimpleNamespace(config_json={})
        self.assertIsInstance(client_for_integration(integration), MockWhatsAppCloudClient)

    @override_settings(DEBUG=False, SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False)
    def test_live_mode_requires_explicit_server_gate(self):
        integration = SimpleNamespace(config_json={'delivery_mode': 'live'})
        with self.assertRaisesRegex(WhatsAppPolicyError, '未显式允许'):
            client_for_integration(integration)

    @override_settings(DEBUG=True, SITEOS_WHATSAPP_ALLOW_LIVE_SEND=True)
    def test_debug_environment_cannot_send_live(self):
        integration = SimpleNamespace(config_json={'delivery_mode': 'live'})
        with self.assertRaisesRegex(WhatsAppPolicyError, 'DEBUG'):
            client_for_integration(integration)

    @patch('leads.whatsapp_ycloud.integration_secret', return_value='ycloud-api-key')
    @override_settings(DEBUG=False, SITEOS_WHATSAPP_ALLOW_LIVE_SEND=True)
    def test_live_ycloud_transport_uses_official_provider_client(self, _secret):
        from leads.whatsapp_ycloud import YCloudWhatsAppClient

        integration = SimpleNamespace(
            config_json={
                'delivery_mode': 'live',
                'transport_provider': 'ycloud',
                'business_phone_e164': '+15592028155',
            },
        )
        self.assertIsInstance(client_for_integration(integration), YCloudWhatsAppClient)
