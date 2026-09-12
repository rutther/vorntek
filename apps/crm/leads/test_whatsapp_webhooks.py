from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from leads.webhooks import whatsapp_webhook


class WhatsAppWebhookViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.integration = SimpleNamespace(
            id=17,
            public_id='phone-id-1',
            config_json={'waba_id': 'waba-1'},
        )
        self.app_secret = 'unit-test-app-secret'

    def signature(self, body: bytes) -> str:
        return 'sha256=' + hmac.new(
            self.app_secret.encode('utf-8'),
            body,
            hashlib.sha256,
        ).hexdigest()

    def post(self, payload: dict, *, signature: str | None = None):
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        request = self.factory.post(
            '/api/webhooks/whatsapp/',
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature if signature is not None else self.signature(body),
        )
        return request

    def message_payload(self, *, phone_id='phone-id-1', waba_id='waba-1') -> dict:
        return {
            'object': 'whatsapp_business_account',
            'entry': [
                {
                    'id': waba_id,
                    'changes': [
                        {
                            'field': 'messages',
                            'value': {
                                'metadata': {'phone_number_id': phone_id},
                                'contacts': [{'wa_id': '971501234567'}],
                                'messages': [
                                    {
                                        'id': 'wamid.inbound-1',
                                        'from': '971501234567',
                                        'type': 'text',
                                        'text': {'body': 'Hello'},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_valid_message_is_recorded(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.app_secret
        record.return_value = (SimpleNamespace(status='processed'), True)
        response = whatsapp_webhook(self.post(self.message_payload()))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'ok': True, 'received': 1, 'processed': 1})
        self.assertEqual(record.call_args.kwargs['event_type'], 'message')
        self.assertEqual(record.call_args.kwargs['external_event_id'], 'wamid.inbound-1')

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_status_event_gets_a_unique_durable_receipt(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.app_secret
        record.return_value = (SimpleNamespace(status='processed'), True)
        payload = self.message_payload()
        value = payload['entry'][0]['changes'][0]['value']
        value.pop('contacts')
        value.pop('messages')
        value['statuses'] = [
            {
                'id': 'wamid.outbound-1',
                'status': 'delivered',
                'timestamp': '1788200000',
                'recipient_id': '971501234567',
            }
        ]

        response = whatsapp_webhook(self.post(payload))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(record.call_args.kwargs['event_type'], 'status')
        self.assertTrue(
            record.call_args.kwargs['external_event_id'].startswith('wamid.outbound-1:')
        )

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_business_app_message_echo_is_mirrored_as_outbound_event(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.app_secret
        record.return_value = (SimpleNamespace(status='processed'), True)
        payload = {
            'object': 'whatsapp_business_account',
            'entry': [
                {
                    'id': 'waba-1',
                    'changes': [
                        {
                            'field': 'smb_message_echoes',
                            'value': {
                                'metadata': {'phone_number_id': 'phone-id-1'},
                                'message_echoes': [
                                    {
                                        'id': 'wamid.business-app-1',
                                        'from': '15592028155',
                                        'to': '971501234567',
                                        'timestamp': '1788200000',
                                        'type': 'text',
                                        'text': {'body': 'Reply from the business app'},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        response = whatsapp_webhook(self.post(payload))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'ok': True, 'received': 1, 'processed': 1})
        event = record.call_args.kwargs
        self.assertEqual(event['event_type'], 'message_echo')
        self.assertEqual(event['external_event_id'], 'wamid.business-app-1')
        self.assertEqual(event['payload']['wa_id'], '971501234567')

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_coexistence_history_and_contacts_are_retained_as_audit_receipts(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.app_secret
        record.return_value = (SimpleNamespace(status='processed'), True)
        payload = {
            'object': 'whatsapp_business_account',
            'entry': [
                {
                    'id': 'waba-1',
                    'changes': [
                        {
                            'field': 'history',
                            'value': {
                                'metadata': {'phone_number_id': 'phone-id-1'},
                                'history': [{'metadata': {'phase': 0, 'chunk_order': 1, 'progress': 50}}],
                            },
                        },
                        {
                            'field': 'smb_app_state_sync',
                            'value': {
                                'metadata': {'phone_number_id': 'phone-id-1'},
                                'state_sync': [{'type': 'contact', 'action': 'add'}],
                            },
                        },
                    ],
                }
            ],
        }

        response = whatsapp_webhook(self.post(payload))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'ok': True, 'received': 2, 'processed': 2})
        event_types = [call.kwargs['event_type'] for call in record.call_args_list]
        self.assertEqual(event_types, ['history', 'contact_sync'])
        for call in record.call_args_list:
            self.assertEqual(len(call.kwargs['external_event_id']), 64)
            self.assertEqual(call.kwargs['payload']['phone_number_id'], 'phone-id-1')

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_invalid_signature_is_rejected_before_persistence(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.app_secret

        response = whatsapp_webhook(self.post(self.message_payload(), signature='sha256=bad'))

        self.assertEqual(response.status_code, 403)
        record.assert_not_called()

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_wrong_phone_number_id_is_rejected(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.app_secret

        response = whatsapp_webhook(
            self.post(self.message_payload(phone_id='wrong-phone-id'))
        )

        self.assertEqual(response.status_code, 403)
        record.assert_not_called()

    @patch('leads.webhooks.record_and_process_event')
    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_wrong_waba_id_is_rejected(self, enabled, secret, record):
        enabled.return_value = [self.integration]
        secret.return_value = self.app_secret

        response = whatsapp_webhook(
            self.post(self.message_payload(waba_id='wrong-waba-id'))
        )

        self.assertEqual(response.status_code, 403)
        record.assert_not_called()

    @patch('leads.webhooks.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_unsupported_but_well_formed_event_still_requires_signature(self, enabled, secret):
        enabled.return_value = [self.integration]
        secret.return_value = self.app_secret
        payload = {'object': 'whatsapp_business_account', 'entry': []}

        invalid = whatsapp_webhook(self.post(payload, signature='sha256=bad'))
        valid = whatsapp_webhook(self.post(payload))

        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(json.loads(valid.content)['received'], 0)

    @patch('leads.inbound.integration_secret')
    @patch('leads.webhooks._enabled_integrations')
    def test_get_challenge_uses_constant_time_token_check(self, enabled, secret):
        enabled.return_value = [self.integration]
        secret.return_value = 'verify-token'
        request = self.factory.get(
            '/api/webhooks/whatsapp/',
            {
                'hub.mode': 'subscribe',
                'hub.verify_token': 'verify-token',
                'hub.challenge': 'challenge-123',
            },
        )

        response = whatsapp_webhook(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'challenge-123')
