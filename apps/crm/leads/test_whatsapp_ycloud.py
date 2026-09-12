from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from leads.webhooks import ycloud_whatsapp_webhook
from leads.whatsapp_cloud import WhatsAppCloudError
from leads.whatsapp_ycloud import (
    YCloudWhatsAppClient,
    verify_ycloud_signature,
    ycloud_event_matches_integration,
    ycloud_webhook_events,
)


class _Response:
    def __init__(self, body: bytes, *, headers=None, status=200):
        self._body = body
        self.headers = headers or {}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self._body


class YCloudSignatureTests(SimpleTestCase):
    secret = 'whsec-unit-test'
    body = b'{"id":"evt-1"}'

    def signature(self, timestamp: int) -> str:
        digest = hmac.new(
            self.secret.encode('utf-8'),
            str(timestamp).encode('ascii') + b'.' + self.body,
            hashlib.sha256,
        ).hexdigest()
        return f't={timestamp},s={digest}'

    def test_valid_signature_uses_raw_body_and_constant_contract(self):
        self.assertTrue(
            verify_ycloud_signature(
                self.body,
                self.signature(1_788_200_000),
                self.secret,
                now=1_788_200_100,
            )
        )
        self.assertFalse(
            verify_ycloud_signature(
                self.body + b' ',
                self.signature(1_788_200_000),
                self.secret,
                now=1_788_200_100,
            )
        )

    def test_stale_future_wrong_and_malformed_signatures_fail_closed(self):
        timestamp = 1_788_200_000
        for header, now in (
            (self.signature(timestamp), timestamp + 301),
            (self.signature(timestamp), timestamp - 301),
            (f't={timestamp},s={"0" * 64}', timestamp),
            ('missing-fields', timestamp),
        ):
            with self.subTest(header=header, now=now):
                self.assertFalse(
                    verify_ycloud_signature(self.body, header, self.secret, now=now)
                )


class YCloudEventMappingTests(SimpleTestCase):
    def setUp(self):
        self.integration = SimpleNamespace(
            public_id='1069437129587829',
            config_json={
                'transport_provider': 'ycloud',
                'waba_id': '4185566811756317',
                'business_phone_e164': '+15592028155',
            },
        )

    def test_inbound_text_and_provider_id_are_normalized(self):
        payload = {
            'id': 'evt-inbound-1',
            'type': 'whatsapp.inbound_message.received',
            'apiVersion': 'v2',
            'whatsappInboundMessage': {
                'id': 'ycloud-message-1',
                'wamid': 'wamid.inbound-1',
                'wabaId': '4185566811756317',
                'from': '+971501234567',
                'to': '+15592028155',
                'sendTime': '2026-09-01T10:00:00.000Z',
                'customerProfile': {'name': 'Omar'},
                'type': 'text',
                'text': {'body': 'Need a beverage line'},
                'referral': {
                    'ctwa_clid': 'ctwa-test-1',
                    'source_id': 'ad-1',
                    'source_url': 'https://fb.me/ctwa-ad-1',
                    'headline': 'Chat with New Crown',
                },
            },
        }

        events = ycloud_webhook_events(payload, integration=self.integration)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['event_type'], 'message')
        self.assertEqual(event['phone_number_id'], '1069437129587829')
        self.assertEqual(event['wa_id'], '971501234567')
        self.assertEqual(event['message_id'], 'wamid.inbound-1')
        self.assertEqual(event['message']['ycloud_message_id'], 'ycloud-message-1')
        self.assertEqual(event['message']['referral']['ctwa_clid'], 'ctwa-test-1')
        self.assertEqual(event['message']['referral']['source_url'], 'https://fb.me/ctwa-ad-1')
        self.assertEqual(event['message']['referral']['headline'], 'Chat with New Crown')
        self.assertEqual(event['contact']['profile']['name'], 'Omar')

    def test_inbound_username_only_contact_uses_business_scoped_user_id(self):
        payload = {
            'id': 'evt-bsuid-1',
            'type': 'whatsapp.inbound_message.received',
            'whatsappInboundMessage': {
                'id': 'ycloud-bsuid-1',
                'wamid': 'wamid.bsuid-1',
                'wabaId': '4185566811756317',
                'fromUserId': 'US.13491208655302741918',
                'to': '+15592028155',
                'type': 'text',
                'text': {'body': 'Username-only message'},
            },
        }

        event = ycloud_webhook_events(payload, integration=self.integration)[0]

        self.assertEqual(event['wa_id'], 'US.13491208655302741918')

    def test_business_app_echo_preserves_wamid_for_reply_context(self):
        payload = {
            'id': 'evt-echo-1',
            'type': 'whatsapp.smb.message.echoes',
            'whatsappMessage': {
                'id': 'ycloud-echo-1',
                'wamid': 'wamid.echo-1',
                'wabaId': '4185566811756317',
                'from': '+15592028155',
                'to': '+971501234567',
                'sendTime': '2026-09-01T10:01:00.000Z',
                'type': 'text',
                'text': {'body': 'Reply from Business App'},
            },
        }

        event = ycloud_webhook_events(payload, integration=self.integration)[0]

        self.assertEqual(event['event_type'], 'message_echo')
        self.assertEqual(event['message_id'], 'wamid.echo-1')
        self.assertEqual(event['wa_id'], '971501234567')

    def test_status_uses_ycloud_id_for_accepted_message_lookup_and_keeps_wamid(self):
        payload = {
            'id': 'evt-status-1',
            'type': 'whatsapp.message.updated',
            'whatsappMessage': {
                'id': 'ycloud-message-1',
                'wamid': 'wamid.outbound-1',
                'status': 'delivered',
                'deliverTime': '2026-09-01T10:02:00.000Z',
                'externalId': 'crm-idempotency-key',
                'pricingCategory': 'utility',
            },
        }

        event = ycloud_webhook_events(payload, integration=self.integration)[0]

        self.assertEqual(event['event_type'], 'status')
        self.assertEqual(event['message_id'], 'ycloud-message-1')
        self.assertEqual(event['delivery_status'], 'delivered')
        self.assertEqual(event['status']['wamid'], 'wamid.outbound-1')
        self.assertEqual(len(event['event_fingerprint']), 64)

    def test_history_and_contact_sync_become_privacy_governed_audit_events(self):
        history_payload = {
            'id': 'evt-history-1',
            'type': 'whatsapp.smb.history',
            'whatsappInboundMessage': {
                'id': 'ycloud-history-1',
                'wamid': 'wamid.history-1',
                'wabaId': '4185566811756317',
                'from': '+971501234567',
                'to': '+15592028155',
                'type': 'text',
                'text': {'body': 'Existing customer history'},
            },
        }
        contact_payload = {
            'id': 'evt-contact-sync-1',
            'type': 'whatsapp.smb.app.state.sync',
            'whatsappSmbAppStateSync': {
                'wabaId': '4185566811756317',
                'phoneNumber': '+15592028155',
                'stateSync': [{'action': 'add', 'contact': {'phoneNumber': '+971501234567'}}],
            },
        }

        history = ycloud_webhook_events(history_payload, integration=self.integration)[0]
        contact = ycloud_webhook_events(contact_payload, integration=self.integration)[0]

        self.assertEqual(history['event_type'], 'history')
        self.assertEqual(history['value']['direction'], 'inbound')
        self.assertEqual(history['provider_event_id'], 'evt-history-1')
        self.assertEqual(len(history['event_fingerprint']), 64)
        self.assertEqual(contact['event_type'], 'contact_sync')
        self.assertEqual(contact['provider_event_id'], 'evt-contact-sync-1')
        self.assertEqual(len(contact['event_fingerprint']), 64)
        self.assertTrue(ycloud_event_matches_integration(history_payload, integration=self.integration))
        self.assertTrue(ycloud_event_matches_integration(contact_payload, integration=self.integration))

        contact_payload['whatsappSmbAppStateSync']['phoneNumber'] = '+15550000000'
        self.assertFalse(ycloud_event_matches_integration(contact_payload, integration=self.integration))

    def test_wrong_waba_or_business_number_cannot_match_integration(self):
        wrong_waba = {
            'type': 'whatsapp.inbound_message.received',
            'whatsappInboundMessage': {
                'wabaId': 'wrong-waba',
                'to': '+15592028155',
            },
        }
        wrong_phone = {
            'type': 'whatsapp.inbound_message.received',
            'whatsappInboundMessage': {
                'wabaId': '4185566811756317',
                'to': '+15550000000',
            },
        }
        self.assertFalse(ycloud_event_matches_integration(wrong_waba, integration=self.integration))
        self.assertFalse(ycloud_event_matches_integration(wrong_phone, integration=self.integration))


class YCloudWebhookViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.secret = 'whsec-view-test'
        self.integration = SimpleNamespace(
            id=19,
            public_id='1069437129587829',
            config_json={
                'transport_provider': 'ycloud',
                'waba_id': '4185566811756317',
                'business_phone_e164': '+15592028155',
                'ycloud_webhook_endpoint_id': 'endpoint-8155',
            },
        )

    def payload(self, *, waba_id='4185566811756317') -> dict:
        return {
            'id': 'evt-inbound-view-1',
            'type': 'whatsapp.inbound_message.received',
            'whatsappInboundMessage': {
                'id': 'ycloud-view-1',
                'wamid': 'wamid.view-1',
                'wabaId': waba_id,
                'from': '+971501234567',
                'to': '+15592028155',
                'sendTime': '2026-09-01T10:00:00.000Z',
                'type': 'text',
                'text': {'body': 'Hello'},
            },
        }

    def post(self, payload: dict, *, timestamp=None, signature=None, endpoint_id='endpoint-8155'):
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        timestamp = int(time.time()) if timestamp is None else int(timestamp)
        digest = hmac.new(
            self.secret.encode('utf-8'),
            str(timestamp).encode('ascii') + b'.' + body,
            hashlib.sha256,
        ).hexdigest()
        return self.factory.post(
            '/api/webhooks/whatsapp/ycloud/',
            data=body,
            content_type='application/json',
            HTTP_YCLOUD_SIGNATURE=signature or f't={timestamp},s={digest}',
            HTTP_X_WEBHOOK_ENDPOINT_ID=endpoint_id,
        )

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_valid_event_converges_on_normalized_receipt_pipeline(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.secret
        record.return_value = (SimpleNamespace(status='processed'), True)

        response = ycloud_whatsapp_webhook(self.post(self.payload()))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'ok': True, 'received': 1, 'processed': 1})
        self.assertEqual(record.call_args.kwargs['provider_code'], 'whatsapp')
        self.assertEqual(record.call_args.kwargs['event_type'], 'message')
        self.assertEqual(record.call_args.kwargs['external_event_id'], 'wamid.view-1')

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_invalid_stale_or_wrong_endpoint_request_is_rejected_before_storage(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.secret
        current = int(time.time())
        requests = (
            self.post(self.payload(), signature=f't={current},s={"0" * 64}'),
            self.post(self.payload(), timestamp=current - 301),
            self.post(self.payload(), endpoint_id='wrong-endpoint'),
        )
        for request in requests:
            with self.subTest(headers=dict(request.headers)):
                response = ycloud_whatsapp_webhook(request)
                self.assertEqual(response.status_code, 403)
        record.assert_not_called()

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_valid_signature_cannot_cross_to_wrong_waba(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.secret

        response = ycloud_whatsapp_webhook(self.post(self.payload(waba_id='wrong-waba')))

        self.assertEqual(response.status_code, 403)
        record.assert_not_called()


class YCloudClientTests(SimpleTestCase):
    def make_client(self):
        client = YCloudWhatsAppClient.__new__(YCloudWhatsAppClient)
        client.api_key = 'unit-test-ycloud-key'
        client.sender = '+15592028155'
        client.timeout = 2
        return client

    @patch('leads.whatsapp_ycloud.urllib.request.urlopen')
    def test_send_uses_async_api_key_idempotency_and_opt_out_filters(self, urlopen):
        urlopen.return_value = _Response(
            b'{"id":"ycloud-message-1","status":"accepted"}',
            headers={'x-request-id': 'request-1'},
        )
        client = self.make_client()

        result = client.send_message(
            payload={
                'messaging_product': 'whatsapp',
                'recipient_type': 'individual',
                'to': '971501234567',
                'type': 'text',
                'text': {'body': 'Hello'},
            },
            idempotency_key='a' * 64,
        )

        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://api.ycloud.com/v2/whatsapp/messages')
        self.assertEqual(request.get_header('X-api-key'), 'unit-test-ycloud-key')
        self.assertEqual(body['from'], '+15592028155')
        self.assertEqual(body['to'], '+971501234567')
        self.assertEqual(body['externalId'], 'a' * 64)
        self.assertTrue(body['filterBlocked'])
        self.assertTrue(body['filterUnsubscribed'])
        self.assertNotIn('messaging_product', body)
        self.assertEqual(result.external_message_id, 'ycloud-message-1')

    @patch('leads.whatsapp_ycloud.urllib.request.urlopen')
    def test_429_and_5xx_are_retryable_without_exposing_api_key(self, urlopen):
        client = self.make_client()
        for status in (429, 503):
            urlopen.side_effect = urllib.error.HTTPError(
                'https://api.ycloud.com/v2/whatsapp/messages',
                status,
                'error',
                {},
                BytesIO(b'{"errorCode":"RATE_LIMIT","errorMessage":"Retry later"}'),
            )
            with self.subTest(status=status), self.assertRaises(WhatsAppCloudError) as raised:
                client.send_message(
                    payload={'to': '971501234567', 'type': 'text', 'text': {'body': 'Hello'}},
                    idempotency_key='b' * 64,
                )
            self.assertTrue(raised.exception.retryable)
            self.assertNotIn(client.api_key, str(raised.exception))

    @patch('leads.whatsapp_ycloud.urllib.request.urlopen')
    def test_transport_timeout_is_ambiguous_and_not_automatically_retryable(self, urlopen):
        urlopen.side_effect = urllib.error.URLError('timed out')
        client = self.make_client()

        with self.assertRaises(WhatsAppCloudError) as raised:
            client.send_message(
                payload={'to': '971501234567', 'type': 'text', 'text': {'body': 'Hello'}},
                idempotency_key='c' * 64,
            )

        self.assertTrue(raised.exception.ambiguous)
        self.assertFalse(raised.exception.retryable)

    @patch('leads.whatsapp_ycloud.urllib.request.urlopen')
    def test_media_upload_uses_official_phone_scoped_endpoint(self, urlopen):
        urlopen.return_value = _Response(
            b'{"id":"media-ycloud-1"}',
            headers={'x-request-id': 'upload-request-1'},
        )
        client = self.make_client()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'quote.pdf'
            path.write_bytes(b'pdf-bytes')
            result = client.upload_media(
                path=path,
                mime_type='application/pdf',
                filename='quote.pdf',
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            'https://api.ycloud.com/v2/whatsapp/media/%2B15592028155/upload',
        )
        self.assertEqual(request.get_header('X-api-key'), 'unit-test-ycloud-key')
        self.assertIn(b'filename="quote.pdf"', request.data)
        self.assertIn(b'pdf-bytes', request.data)
        self.assertEqual(result.external_media_id, 'media-ycloud-1')

    @patch('leads.whatsapp_ycloud.urllib.request.urlopen')
    def test_business_scoped_user_id_uses_recipient_field(self, urlopen):
        urlopen.return_value = _Response(b'{"id":"ycloud-message-bsuid"}')
        client = self.make_client()

        client.send_message(
            payload={
                'to': 'US.13491208655302741918',
                'type': 'text',
                'text': {'body': 'Hello'},
            },
            idempotency_key='d' * 64,
        )

        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body['recipient'], 'US.13491208655302741918')
        self.assertNotIn('to', body)
