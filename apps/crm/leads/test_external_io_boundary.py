from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from leads.whatsapp_cloud import GraphWhatsAppCloudClient, WhatsAppPolicyError, client_for_integration
from leads.whatsapp_media import GraphWhatsAppMediaClient, YCloudWhatsAppMediaClient, media_client_for_integration
from leads.whatsapp_templates import fetch_whatsapp_templates, _fetch_ycloud_templates
from leads.whatsapp_ycloud import YCloudWhatsAppClient


@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False)
class ExternalIoBoundaryTests(SimpleTestCase):
    def test_google_credentials_transport_and_status_poll_stay_disabled(self):
        from leads.google_data_manager import (
            GoogleDataManagerError, _google_request, _load_google_credentials,
            dispatch_google_outbox_event, refresh_google_outbox_status,
        )
        with patch('urllib.request.urlopen') as network:
            with self.assertRaises(GoogleDataManagerError):
                _load_google_credentials(None)
            with self.assertRaises(GoogleDataManagerError):
                _google_request(method='GET', url='https://example.invalid/', token='synthetic')
            self.assertFalse(dispatch_google_outbox_event(None))
            self.assertFalse(refresh_google_outbox_status(None))
            network.assert_not_called()

    def test_lead_fetch_and_platform_diagnostics_stay_disabled(self):
        from leads.inbound import InboundEventError, fetch_meta_lead
        from console.marketing_diagnostics import _graph_get, _ycloud_get
        with patch('urllib.request.urlopen') as network, patch('leads.inbound.integration_secret') as secret:
            with self.assertRaises(InboundEventError):
                fetch_meta_lead(None, 'synthetic')
            self.assertEqual(_graph_get('https://example.invalid/', 'synthetic')['error_type'], 'external_io_disabled')
            self.assertEqual(_ycloud_get('https://example.invalid/', 'synthetic')['error_type'], 'external_io_disabled')
            secret.assert_not_called()
            network.assert_not_called()

    def test_live_constructors_refuse_before_reading_secrets_or_network(self):
        cases = [
            (GraphWhatsAppCloudClient, 'leads.whatsapp_cloud'),
            (YCloudWhatsAppClient, 'leads.whatsapp_ycloud'),
            (GraphWhatsAppMediaClient, 'leads.whatsapp_media'),
            (YCloudWhatsAppMediaClient, 'leads.whatsapp_media'),
        ]
        with patch('urllib.request.urlopen') as network:
            for cls, module in cases:
                with self.subTest(client=cls.__name__), patch(module + '.integration_secret') as secret:
                    with self.assertRaises(WhatsAppPolicyError):
                        cls(integration=SimpleNamespace())
                    secret.assert_not_called()
            network.assert_not_called()

    def test_existing_clients_recheck_at_transport_boundary(self):
        # No constructors or credentials: represents a previously created client
        # after the environment policy has been disabled.
        calls = [
            lambda: GraphWhatsAppCloudClient.__new__(GraphWhatsAppCloudClient)._request(
                url='https://example.invalid/', body=b'{}', content_type='application/json'),
            lambda: YCloudWhatsAppClient.__new__(YCloudWhatsAppClient)._request(
                url='https://example.invalid/', body=b'{}', content_type='application/json'),
            lambda: GraphWhatsAppMediaClient.__new__(GraphWhatsAppMediaClient)._open(None),
            lambda: YCloudWhatsAppMediaClient.__new__(YCloudWhatsAppMediaClient).download(
                external_media_id='synthetic', download_url='https://example.invalid/'),
        ]
        with patch('urllib.request.urlopen') as network:
            for index, call in enumerate(calls):
                with self.subTest(transport=index), self.assertRaises(WhatsAppPolicyError):
                    call()
            network.assert_not_called()

    def test_live_template_fetch_refuses_both_providers_and_direct_helper(self):
        with patch('leads.whatsapp_templates.integration_secret') as secret, patch('urllib.request.urlopen') as network:
            for provider in ['meta', 'ycloud']:
                integration = SimpleNamespace(config_json={'delivery_mode': 'live', 'transport_provider': provider})
                with self.subTest(provider=provider), self.assertRaises(WhatsAppPolicyError):
                    fetch_whatsapp_templates(integration)
            with self.assertRaises(WhatsAppPolicyError):
                _fetch_ycloud_templates(SimpleNamespace())
            secret.assert_not_called()
            network.assert_not_called()

    @override_settings(DEBUG=False, SITEOS_WHATSAPP_ALLOW_LIVE_SEND=True)
    def test_live_factories_cannot_override_global_disabled_policy(self):
        with patch('urllib.request.urlopen') as network:
            for factory in [client_for_integration, media_client_for_integration]:
                for provider in ['meta', 'ycloud']:
                    integration = SimpleNamespace(config_json={'delivery_mode': 'live', 'transport_provider': provider})
                    with self.subTest(factory=factory.__name__, provider=provider), self.assertRaises(WhatsAppPolicyError):
                        factory(integration)
            network.assert_not_called()

    def test_mock_clients_and_template_fixtures_remain_network_free(self):
        integration = SimpleNamespace(config_json={'delivery_mode': 'mock', 'mock_templates': [{'name': 'synthetic'}]})
        with patch('urllib.request.urlopen') as network:
            result = client_for_integration(integration).send_message(payload={}, idempotency_key='synthetic')
            self.assertTrue(result.external_message_id.startswith('wamid.mock.'))
            media = media_client_for_integration(integration).download(external_media_id='synthetic')
            self.assertIn(b'synthetic', media.content)
            self.assertEqual(fetch_whatsapp_templates(integration), [{'name': 'synthetic'}])
            network.assert_not_called()
