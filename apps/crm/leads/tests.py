from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core import mail
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from leads.services import (
    LeadCaptureError,
    MetaPayloadError,
    build_meta_payload,
    clean_submission_input,
    dispatch_meta_outbox_event,
    meta_api_version,
    meta_platform_event_name,
    normalize_meta_country,
    update_submission_stage,
)
from leads.google_data_manager import (
    GoogleDataManagerError,
    build_google_data_manager_payload,
    dispatch_google_outbox_event,
    google_response_errors,
    google_response_warnings,
    normalize_google_email,
    normalize_google_phone,
)
from leads.inbound import (
    InboundEventError,
    _configured_meta_page_id,
    _configured_waba_id,
    _queue_initial_crm_event,
    _timestamp,
    fetch_meta_lead,
    meta_leadgen_events,
    process_meta_lead_receipt,
    process_whatsapp_receipt,
    verify_sha256_signature,
    whatsapp_message_events,
    whatsapp_webhook_events,
)
from leads.email_delivery import (
    SMTP_CONFIG_SECRET_KEY,
    SMTP_PASSWORD_SECRET_KEY,
    load_runtime_email_config,
)
from leads.notifications import email_delivery_configured, send_smtp_test_notification
from leads.readiness import inspect_form_definition, inspect_marketing_integration


class LeadInputValidationTests(SimpleTestCase):
    def setUp(self):
        self.form = SimpleNamespace(site_id=7, id=11)

    def valid_payload(self) -> dict:
        return {
            'full_name': 'Nadia Rahman',
            'email': 'Nadia@Example.com',
            'phone': '',
            'company': 'North Coast Beverages',
            'country': 'United Arab Emirates',
            'message': 'We are planning a new carbonated drink canning line.',
            'source_url': 'https://vorntek.example/?utm_source=facebook',
            'referrer_url': 'https://facebook.com/',
            'fbclid': 'test-click-id',
            'gclid': 'test-google-click-id',
            'gbraid': 'test-google-braid',
            'wbraid': 'test-google-web-braid',
            'form_started_at': (timezone.now() - timedelta(seconds=5)).isoformat(),
            'utm': {'utm_source': 'facebook', 'utm_campaign': 'can-line-video'},
            'consent': {'contact': True, 'privacy_notice': True, 'marketing': True},
            'extra_fields': {
                'product_category': 'Carbonated drink',
                'capacity': '24,000 cans/hour',
            },
        }

    def test_valid_meta_lead_is_normalized(self):
        cleaned = clean_submission_input(self.valid_payload(), form_definition=self.form)
        self.assertEqual(cleaned['email'], 'nadia@example.com')
        self.assertEqual(cleaned['source_channel'], 'meta_ads')
        self.assertEqual(cleaned['payload_json']['capacity'], '24,000 cans/hour')
        self.assertTrue(cleaned['consent_json']['marketing'])
        self.assertEqual(cleaned['identifiers_json']['fbclid'], 'test-click-id')
        self.assertEqual(cleaned['identifiers_json']['gclid'], 'test-google-click-id')
        self.assertEqual(cleaned['identifiers_json']['gbraid'], 'test-google-braid')
        self.assertEqual(cleaned['identifiers_json']['wbraid'], 'test-google-web-braid')
        self.assertEqual(len(cleaned['contact_key']), 64)
        self.assertEqual(len(cleaned['dedupe_key']), 64)

    def test_google_session_attributes_are_validated_and_require_marketing_consent(self):
        attributes = {
            'gad_source': '1',
            'gad_campaignid': '1234567890',
            'session_start_time_usec': '1767711548052000',
            'landing_page_url': 'https://vorntek.example/products/?gad_source=1&gad_campaignid=1234567890',
            'landing_page_referrer': 'https://www.google.com/',
            'landing_page_user_agent': 'Mozilla/5.0 test browser',
            'unexpected_field': 'must-not-be-forwarded',
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(attributes, separators=(',', ':')).encode('utf-8')
        ).decode('ascii').rstrip('=')
        payload = self.valid_payload()
        payload['google_session_attributes'] = encoded

        cleaned = clean_submission_input(payload, form_definition=self.form)
        stored = cleaned['identifiers_json']['google_session_attributes']
        decoded = json.loads(base64.urlsafe_b64decode(stored + ('=' * (-len(stored) % 4))))
        self.assertEqual(decoded['gad_campaignid'], '1234567890')
        self.assertNotIn('unexpected_field', decoded)

        payload['consent']['marketing'] = False
        cleaned_without_consent = clean_submission_input(payload, form_definition=self.form)
        self.assertEqual(cleaned_without_consent['identifiers_json']['google_session_attributes'], '')

        payload['consent']['marketing'] = True
        payload['google_session_attributes'] = 'not-valid-base64!'
        cleaned_invalid = clean_submission_input(payload, form_definition=self.form)
        self.assertEqual(cleaned_invalid['identifiers_json']['google_session_attributes'], '')

        incomplete_attributes = dict(attributes)
        incomplete_attributes.pop('gad_campaignid')
        payload['google_session_attributes'] = base64.urlsafe_b64encode(
            json.dumps(incomplete_attributes, separators=(',', ':')).encode('utf-8')
        ).decode('ascii').rstrip('=')
        cleaned_incomplete = clean_submission_input(payload, form_definition=self.form)
        self.assertEqual(cleaned_incomplete['identifiers_json']['google_session_attributes'], '')

    def test_rejected_marketing_consent_removes_tracking_data(self):
        payload = self.valid_payload()
        payload.update({
            'source_url': 'https://vorntek.example/?lang=en&fbclid=fb-1&gclid=google-1&utm_campaign=can-line',
            'referrer_url': 'https://www.google.com/search?q=filling&gad_source=1',
            'source_channel': 'meta_ads',
            'source_detail': 'injected campaign',
            'fbp': 'fb.1.test',
            'fbc': 'fb.1.click',
            'meta_lead_id': 'lead-1',
            'meta_campaign_id': 'campaign-1',
            'meta_adset_id': 'adset-1',
            'meta_ad_id': 'ad-1',
            'ctwa_clid': 'ctwa-1',
            'whatsapp_id': 'wa-1',
            'client_event_id': 'browser-event-1',
        })
        payload['consent']['marketing'] = False

        cleaned = clean_submission_input(payload, form_definition=self.form)

        for key in (
            'fbp', 'fbc', 'fbclid', 'external_id', 'meta_lead_id', 'meta_campaign_id',
            'meta_adset_id', 'meta_ad_id', 'gclid', 'gbraid', 'wbraid', 'ctwa_clid',
            'whatsapp_id', 'google_session_attributes',
        ):
            with self.subTest(identifier=key):
                self.assertEqual(cleaned['identifiers_json'][key], '')
        self.assertEqual(cleaned['identifiers_json']['client_event_id'], 'browser-event-1')
        self.assertEqual(cleaned['utm_json'], {})
        self.assertEqual(cleaned['source_url'], 'https://vorntek.example/?lang=en')
        self.assertEqual(cleaned['referrer_url'], 'https://www.google.com/search?q=filling')
        self.assertEqual(cleaned['source_channel'], 'referral')
        self.assertNotEqual(cleaned['source_detail'], 'injected campaign')

    def test_email_or_phone_is_required(self):
        payload = self.valid_payload()
        payload['email'] = ''
        payload['phone'] = ''
        with self.assertRaisesRegex(LeadCaptureError, '至少填写一项'):
            clean_submission_input(payload, form_definition=self.form)

    def test_company_product_capacity_and_consent_are_required(self):
        cases = [
            ('company', '', '公司'),
            ('extra_fields', {'product_category': '', 'capacity': '24,000 cans/hour'}, '产品类别'),
            ('extra_fields', {'product_category': 'Water', 'capacity': ''}, '产能目标'),
            ('consent', {'contact': False, 'privacy_notice': True}, '联系许可'),
        ]
        for key, value, expected in cases:
            with self.subTest(key=key, expected=expected):
                payload = self.valid_payload()
                payload[key] = value
                with self.assertRaisesRegex(LeadCaptureError, expected):
                    clean_submission_input(payload, form_definition=self.form)

    def test_honeypot_and_too_fast_submission_are_rejected(self):
        honeypot = self.valid_payload()
        honeypot['website'] = 'https://spam.example'
        with self.assertRaises(LeadCaptureError) as caught:
            clean_submission_input(honeypot, form_definition=self.form)
        self.assertEqual(caught.exception.code, 'spam_detected')

        too_fast = self.valid_payload()
        too_fast['form_started_at'] = timezone.now().isoformat()
        with self.assertRaises(LeadCaptureError) as caught:
            clean_submission_input(too_fast, form_definition=self.form)
        self.assertEqual(caught.exception.code, 'spam_detected')

    def test_invalid_buyer_value_returns_validation_error(self):
        payload = self.valid_payload()
        payload['buyer_value'] = 'not-a-number'
        with self.assertRaisesRegex(LeadCaptureError, '成交金额格式无效'):
            clean_submission_input(payload, form_definition=self.form)

    def test_international_phone_prefix_is_preserved_for_matching(self):
        payload = self.valid_payload()
        payload['email'] = ''
        payload['phone'] = '00 971 50 123 4567'
        cleaned = clean_submission_input(payload, form_definition=self.form)
        self.assertEqual(cleaned['phone'], '+971501234567')


class MetaPayloadTests(SimpleTestCase):
    def setUp(self):
        now = timezone.now()
        self.integration = SimpleNamespace(id=19)
        self.submission = SimpleNamespace(
            submission_key='18dc82f8-3310-4de6-80b7-aac75ab1ad3c',
            form=SimpleNamespace(code='project-inquiry', category='project'),
            full_name='Nadia Rahman',
            email='nadia@example.com',
            phone='+971 50 123 4567',
            country='United Arab Emirates',
            identifiers_json={
                'client_event_id': 'browser-lead-event-1',
                'external_id': 'crm-contact-1',
                'fbp': 'fb.1.1700000000.123456789',
                'fbc': 'fb.1.1700000000.test-click-id',
                'meta_lead_id': 'meta-instant-form-lead-1',
            },
            consent_json={'marketing': True},
            client_ip='203.0.113.10',
            user_agent='Mozilla/5.0 test',
            submitted_at=now - timedelta(days=7),
            contacted_at=now - timedelta(days=6),
            qualified_at=now - timedelta(days=3),
            won_at=now,
            lost_at=None,
            stage_updated_at=now,
            stage='qualified',
            source_url='https://vorntek.example/?utm_source=facebook',
            qualification_score=80,
            buyer_value=Decimal('125000.50'),
            buyer_currency='USD',
        )

    def test_submitted_payload_deduplicates_browser_and_server_lead(self):
        payload = build_meta_payload(
            self.submission,
            self.integration,
            stage_key='submitted',
            event_name='Lead',
        )

        self.assertEqual(payload['event_id'], 'browser-lead-event-1')
        self.assertEqual(payload['event_time'], int(self.submission.submitted_at.timestamp()))
        self.assertEqual(payload['action_source'], 'website')
        self.assertEqual(payload['event_source_url'], self.submission.source_url)
        self.assertEqual(payload['user_data']['client_ip_address'], '203.0.113.10')
        self.assertEqual(payload['user_data']['client_user_agent'], 'Mozilla/5.0 test')
        self.assertEqual(payload['user_data']['lead_id'], 'meta-instant-form-lead-1')
        self.assertEqual(payload['user_data']['country'], [hashlib.sha256(b'ae').hexdigest()])

    def test_website_payload_fails_closed_without_required_browser_context(self):
        self.submission.source_url = ''
        with self.assertRaisesRegex(MetaPayloadError, 'event_source_url'):
            build_meta_payload(
                self.submission,
                self.integration,
                stage_key='submitted',
                event_name='Lead',
            )

        self.submission.source_url = 'https://vorntek.example/products/filling/'
        self.submission.user_agent = ''
        with self.assertRaisesRegex(MetaPayloadError, 'User-Agent'):
            build_meta_payload(
                self.submission,
                self.integration,
                stage_key='submitted',
                event_name='Lead',
            )

    def test_country_matching_uses_iso_alpha_2_without_changing_crm_value(self):
        self.assertEqual(normalize_meta_country('United Arab Emirates'), 'ae')
        self.assertEqual(normalize_meta_country('UAE'), 'ae')
        self.assertEqual(normalize_meta_country('المملكة العربية السعودية'), 'sa')
        self.assertEqual(normalize_meta_country('SA'), 'sa')
        self.assertEqual(self.submission.country, 'United Arab Emirates')

    def test_unrecognized_country_is_omitted_instead_of_hashed_incorrectly(self):
        self.submission.country = 'Dubai Industrial Area'
        payload = build_meta_payload(
            self.submission,
            self.integration,
            stage_key='qualified',
            event_name='qualified_lead',
        )
        self.assertNotIn('country', payload['user_data'])

    def test_invalid_graph_version_falls_back_to_a_safe_version(self):
        integration = SimpleNamespace(config_json={'api_version': '../latest'})

        version = meta_api_version(integration)

        self.assertRegex(version, r'^v\d+\.\d+$')
        self.assertNotIn('/', version)

    def test_qualified_payload_uses_actual_crm_stage_time_and_stable_id(self):
        payload = build_meta_payload(
            self.submission,
            self.integration,
            stage_key='qualified',
            event_name='qualified_lead',
        )

        self.assertEqual(
            payload['event_id'],
            '18dc82f8-3310-4de6-80b7-aac75ab1ad3c:qualified:19',
        )
        self.assertEqual(payload['event_time'], int(self.submission.qualified_at.timestamp()))
        self.assertEqual(payload['action_source'], 'system_generated')
        self.assertNotIn('event_source_url', payload)
        self.assertNotIn('client_ip_address', payload['user_data'])
        self.assertNotIn('client_user_agent', payload['user_data'])
        self.assertEqual(payload['custom_data']['event_source'], 'crm')
        self.assertEqual(payload['custom_data']['qualification_score'], 80)
        self.assertEqual(payload['custom_data']['lead_stage'], 'qualified')

    def test_won_payload_uses_actual_won_time_and_value(self):
        payload = build_meta_payload(
            self.submission,
            self.integration,
            stage_key='won',
            event_name='converted',
        )

        self.assertEqual(payload['event_time'], int(self.submission.won_at.timestamp()))
        self.assertEqual(payload['custom_data']['value'], 125000.50)
        self.assertEqual(payload['custom_data']['currency'], 'USD')

    def test_ctwa_payload_uses_business_messaging_identifiers(self):
        self.submission.identifiers_json = {
            'external_id': '971501234567',
            'whatsapp_id': '971501234567',
            'ctwa_clid': 'ARAHCTWA-CLICK-ID',
            'whatsapp_business_account_id': '123456789012345',
            'meta_page_id': '998877665544332',
        }
        self.submission.source_channel = 'meta_ads'

        payload = build_meta_payload(
            self.submission,
            self.integration,
            stage_key='qualified',
            event_name='qualified_lead',
        )

        self.assertEqual(payload['action_source'], 'business_messaging')
        self.assertEqual(payload['messaging_channel'], 'whatsapp')
        self.assertEqual(
            payload['user_data'],
            {
                'ctwa_clid': 'ARAHCTWA-CLICK-ID',
                'whatsapp_business_account_id': '123456789012345',
            },
        )
        self.assertNotIn('page_id', payload['user_data'])
        self.assertNotIn('event_source_url', payload)

    def test_ctwa_legacy_page_identity_mode_is_explicit(self):
        self.submission.identifiers_json = {
            'ctwa_clid': 'ARAHCTWA-CLICK-ID',
            'whatsapp_business_account_id': '123456789012345',
            'meta_page_id': '998877665544332',
            'ctwa_identity_mode': 'page',
        }

        payload = build_meta_payload(
            self.submission,
            self.integration,
            stage_key='qualified',
            event_name='LeadSubmitted',
        )

        self.assertEqual(
            payload['user_data'],
            {'ctwa_clid': 'ARAHCTWA-CLICK-ID', 'page_id': '998877665544332'},
        )

    def test_ctwa_internal_stage_names_map_to_meta_messaging_events(self):
        self.submission.identifiers_json = {'ctwa_clid': 'ARAHCTWA-CLICK-ID'}

        self.assertEqual(
            meta_platform_event_name(
                self.submission,
                stage_key='qualified',
                crm_event_name='qualified_lead',
            ),
            'LeadSubmitted',
        )
        self.assertEqual(
            meta_platform_event_name(
                self.submission,
                stage_key='won',
                crm_event_name='converted',
            ),
            'Purchase',
        )
        with self.assertRaisesRegex(MetaPayloadError, '仅回传合格线索和真实成交'):
            meta_platform_event_name(
                self.submission,
                stage_key='contacted',
                crm_event_name='contacted_lead',
            )

    def test_local_phone_is_not_hashed_as_an_international_identifier(self):
        self.submission.phone = '050 123 4567'
        payload = build_meta_payload(
            self.submission,
            self.integration,
            stage_key='qualified',
            event_name='qualified_lead',
        )
        self.assertNotIn('ph', payload['user_data'])

    @patch('leads.services.resolve_meta_access_token', return_value='secret-capi-token')
    @patch('leads.services.urllib.request.urlopen')
    def test_capi_access_token_uses_authorization_header(self, mocked_urlopen, _mocked_token):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"events_received":1}'
        mocked_urlopen.return_value = response
        integration = SimpleNamespace(
            public_id='123456789012345',
            config_json={'api_version': 'v23.0'},
        )
        outbox = SimpleNamespace(
            integration=integration,
            payload_json={'event_name': 'Lead'},
            status='pending',
            attempts=0,
            last_error='',
            response_json={},
            dispatched_at=None,
            updated_at=None,
            save=Mock(),
        )

        self.assertTrue(dispatch_meta_outbox_event(outbox))
        request = mocked_urlopen.call_args.args[0]
        self.assertNotIn('access_token', request.full_url)
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-capi-token')
        self.assertEqual(outbox.delivery_mode, 'live')
        self.assertEqual(outbox.status, 'sent')
        self.assertIsNotNone(outbox.provider_received_at)
        self.assertEqual(outbox.match_status, 'unknown')

    @patch('leads.services.resolve_meta_access_token', return_value='secret-capi-token')
    @patch('leads.services.urllib.request.urlopen')
    def test_capi_does_not_claim_success_without_events_received(self, mocked_urlopen, _mocked_token):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{}'
        mocked_urlopen.return_value = response
        integration = SimpleNamespace(
            public_id='123456789012345',
            config_json={'api_version': 'v23.0', 'test_event_code': 'TEST123'},
        )
        outbox = SimpleNamespace(
            integration=integration,
            payload_json={'event_name': 'Lead'},
            status='pending',
            attempts=0,
            last_error='',
            response_json={},
            dispatched_at=None,
            updated_at=None,
            save=Mock(),
        )

        self.assertFalse(dispatch_meta_outbox_event(outbox))
        self.assertEqual(outbox.status, 'failed')
        self.assertEqual(outbox.delivery_mode, 'test')
        self.assertIsNotNone(outbox.next_attempt_at)
        self.assertIn('未确认接收到事件', outbox.last_error)


class InboundWebhookParsingTests(SimpleTestCase):
    def test_sha256_signature_uses_meta_app_secret(self):
        body = b'{"object":"page"}'
        signature = 'sha256=703eda4acf6d31d5b4fdd07887302c53639dcde8453f05f71727ea203f0166c1'
        self.assertTrue(verify_sha256_signature(body, signature, 'test-secret'))
        self.assertFalse(verify_sha256_signature(body, signature, 'wrong-secret'))

    def test_meta_leadgen_payload_is_flattened(self):
        events = meta_leadgen_events(
            {
                'object': 'page',
                'entry': [
                    {
                        'id': '123456',
                        'changes': [
                            {
                                'field': 'leadgen',
                                'value': {'leadgen_id': '987654', 'form_id': '444', 'ad_id': '555'},
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(events[0]['page_id'], '123456')
        self.assertEqual(events[0]['leadgen_id'], '987654')
        self.assertEqual(events[0]['value']['form_id'], '444')

    def test_meta_lead_id_survives_webhook_intake_and_qualified_capi_payload(self):
        received_at = timezone.now()
        qualified_at = received_at + timedelta(days=2)
        integration = SimpleNamespace(
            id=71,
            integration_type='leadgen',
            public_id='fallback-page-id',
            config_json={
                'source_url': 'https://vorntek.example/meta-lead',
                'leadgen_form_ids': ['form-456'],
            },
        )
        receipt = SimpleNamespace(
            submission_id=None,
            integration=integration,
            received_at=received_at,
            payload_json={
                'page_id': 'page-123',
                'leadgen_id': 'lead-987',
                'value': {
                    'form_id': 'form-456',
                    'ad_id': 'ad-789',
                    'campaign_id': 'campaign-webhook',
                },
            },
        )
        lead_data = {
            'id': 'lead-987',
            'created_time': received_at.isoformat(),
            'campaign_id': 'campaign-321',
            'adset_id': 'adset-654',
            'ad_id': 'ad-789',
            'form_id': 'form-456',
            'field_data': [
                {'name': 'full_name', 'values': ['Nadia Rahman']},
                {'name': 'email', 'values': ['nadia@example.com']},
                {'name': 'phone_number', 'values': ['+971501234567']},
                {'name': 'country', 'values': ['United Arab Emirates']},
            ],
        }

        def create_submission(**kwargs):
            return SimpleNamespace(
                submission_key='8ec691d6-c4bc-48c6-84ba-3cf31769f3cd',
                form=SimpleNamespace(code='meta-leadgen', category='project'),
                full_name=kwargs['full_name'],
                email=kwargs['email'],
                phone=kwargs['phone'],
                country=kwargs['country'],
                identifiers_json=kwargs['identifiers_json'],
                consent_json={'marketing': True},
                client_ip='',
                user_agent='',
                submitted_at=kwargs['submitted_at'],
                contacted_at=qualified_at - timedelta(days=1),
                qualified_at=qualified_at,
                won_at=None,
                lost_at=None,
                stage_updated_at=qualified_at,
                source_url=kwargs['source_url'],
                qualification_score=80,
                buyer_value=None,
                buyer_currency='USD',
            )

        with (
            patch('leads.inbound.fetch_meta_lead', return_value=lead_data) as mocked_fetch,
            patch('leads.inbound._create_external_submission', side_effect=create_submission) as mocked_create,
            patch('leads.inbound._queue_initial_crm_event', return_value=1) as mocked_initial,
        ):
            submission = process_meta_lead_receipt(receipt)

        mocked_fetch.assert_called_once_with(integration, 'lead-987')
        mocked_initial.assert_called_once_with(submission, integration)
        identifiers = mocked_create.call_args.kwargs['identifiers_json']
        self.assertEqual(identifiers['meta_lead_id'], 'lead-987')
        self.assertEqual(identifiers['meta_page_id'], 'page-123')
        self.assertEqual(identifiers['meta_form_id'], 'form-456')
        self.assertEqual(identifiers['meta_ad_id'], 'ad-789')
        self.assertEqual(receipt.payload_json['initial_crm_events_queued'], 1)

        initial_payload = build_meta_payload(
            submission,
            integration,
            stage_key='submitted',
            event_name='initial_lead',
        )
        qualified_payload = build_meta_payload(
            submission,
            integration,
            stage_key='qualified',
            event_name='qualified_lead',
        )

        for payload in [initial_payload, qualified_payload]:
            self.assertEqual(payload['user_data']['lead_id'], 'lead-987')
            self.assertEqual(payload['action_source'], 'system_generated')
            self.assertEqual(payload['custom_data']['event_source'], 'crm')
            self.assertEqual(payload['custom_data']['lead_event_source'], 'siteos_crm')
            self.assertNotIn('event_source_url', payload)
        self.assertEqual(qualified_payload['event_time'], int(qualified_at.timestamp()))

    def test_whatsapp_ctwa_payload_preserves_referral(self):
        events = whatsapp_message_events(
            {
                'object': 'whatsapp_business_account',
                'entry': [
                    {
                        'id': 'waba-1',
                        'changes': [
                            {
                                'field': 'messages',
                                'value': {
                                    'metadata': {'phone_number_id': 'phone-id-1'},
                                    'contacts': [{'wa_id': '971501234567', 'profile': {'name': 'Nadia'}}],
                                    'messages': [
                                        {
                                            'id': 'wamid.abc',
                                            'from': '971501234567',
                                            'type': 'text',
                                            'text': {'body': 'Need a 24,000 CPH canning line'},
                                            'referral': {'ctwa_clid': 'ctwa-click-1', 'source_id': 'ad-1'},
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(events[0]['phone_number_id'], 'phone-id-1')
        self.assertEqual(events[0]['message_id'], 'wamid.abc')
        self.assertEqual(events[0]['message']['referral']['ctwa_clid'], 'ctwa-click-1')

    def test_whatsapp_status_events_are_normalized_and_fingerprinted(self):
        payload = {
            'object': 'whatsapp_business_account',
            'entry': [
                {
                    'id': 'waba-1',
                    'changes': [
                        {
                            'field': 'messages',
                            'value': {
                                'metadata': {'phone_number_id': 'phone-id-1'},
                                'statuses': [
                                    {
                                        'id': 'wamid.outbound-1',
                                        'status': 'failed',
                                        'timestamp': '1788200000',
                                        'recipient_id': '971501234567',
                                        'errors': [
                                            {
                                                'code': 131056,
                                                'title': 'Pair rate limit hit',
                                            }
                                        ],
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        first = whatsapp_webhook_events(payload)
        second = whatsapp_webhook_events(payload)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]['event_type'], 'status')
        self.assertEqual(first[0]['delivery_status'], 'failed')
        self.assertEqual(first[0]['message_id'], 'wamid.outbound-1')
        self.assertEqual(len(first[0]['event_fingerprint']), 64)
        self.assertEqual(first[0]['event_fingerprint'], second[0]['event_fingerprint'])
        self.assertEqual(whatsapp_message_events(payload), [])

    def test_whatsapp_ctwa_identifiers_survive_webhook_to_qualified_capi_payload(self):
        received_at = timezone.now()
        qualified_at = received_at + timedelta(days=3)
        integration = SimpleNamespace(
            id=81,
            public_id='phone-id-1',
            config_json={
                'waba_id': 'waba-1',
                'page_id': 'page-ctwa-123',
            },
        )
        event = whatsapp_message_events(
            {
                'object': 'whatsapp_business_account',
                'entry': [
                    {
                        'id': 'waba-1',
                        'changes': [
                            {
                                'field': 'messages',
                                'value': {
                                    'metadata': {'phone_number_id': 'phone-id-1'},
                                    'contacts': [{'wa_id': '971501234567', 'profile': {'name': 'Nadia'}}],
                                    'messages': [
                                        {
                                            'id': 'wamid.ctwa-1',
                                            'from': '971501234567',
                                            'timestamp': str(int(received_at.timestamp())),
                                            'type': 'text',
                                            'text': {'body': 'Need a 24,000 CPH canning line'},
                                            'referral': {
                                                'ctwa_clid': 'ctwa-click-1',
                                                'source_id': 'ad-1',
                                                'source_url': 'https://fb.me/ctwa-ad-1',
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                ],
            }
        )[0]
        receipt = SimpleNamespace(
            submission_id=None,
            integration=integration,
            received_at=received_at,
            payload_json=event,
        )
        empty_queryset = Mock()
        empty_queryset.select_for_update.return_value = empty_queryset
        empty_queryset.exclude.return_value = empty_queryset
        empty_queryset.order_by.return_value = empty_queryset
        empty_queryset.first.return_value = None

        def create_submission(**kwargs):
            return SimpleNamespace(
                submission_key='ea389d60-82b8-4ea7-b2ee-f38416c10531',
                form=SimpleNamespace(code='project-inquiry', category='project'),
                full_name=kwargs['full_name'],
                email='',
                phone=kwargs['phone'],
                country='',
                identifiers_json=kwargs['identifiers_json'],
                consent_json={'marketing': True},
                client_ip='',
                user_agent='',
                submitted_at=kwargs['submitted_at'],
                contacted_at=qualified_at - timedelta(days=1),
                qualified_at=qualified_at,
                won_at=None,
                lost_at=None,
                stage_updated_at=qualified_at,
                source_url=kwargs['source_url'],
                qualification_score=80,
                buyer_value=None,
                buyer_currency='USD',
            )

        with (
            patch('leads.inbound._form_for_integration', return_value=SimpleNamespace(site='site-1')),
            patch('leads.inbound.LeadSubmission.objects.filter', return_value=empty_queryset),
            patch('leads.inbound._acquire_external_submission_lock'),
            patch('leads.inbound._create_external_submission', side_effect=create_submission) as mocked_create,
            patch(
                'leads.inbound._persist_normalized_whatsapp_message',
                side_effect=lambda **kwargs: kwargs['submission'],
            ),
        ):
            submission = process_whatsapp_receipt.__wrapped__(receipt)

        identifiers = mocked_create.call_args.kwargs['identifiers_json']
        self.assertEqual(identifiers['ctwa_clid'], 'ctwa-click-1')
        self.assertEqual(identifiers['meta_page_id'], 'page-ctwa-123')
        self.assertEqual(identifiers['whatsapp_business_account_id'], 'waba-1')
        self.assertEqual(identifiers['ctwa_identity_mode'], 'waba')
        self.assertEqual(identifiers['whatsapp_phone_number_id'], 'phone-id-1')

        payload = build_meta_payload(
            submission,
            SimpleNamespace(id=91),
            stage_key='qualified',
            event_name='qualified_lead',
        )
        self.assertEqual(payload['action_source'], 'business_messaging')
        self.assertEqual(payload['messaging_channel'], 'whatsapp')
        self.assertEqual(
            payload['user_data'],
            {
                'ctwa_clid': 'ctwa-click-1',
                'whatsapp_business_account_id': 'waba-1',
            },
        )
        self.assertNotIn('page_id', payload['user_data'])
        self.assertEqual(payload['event_time'], int(qualified_at.timestamp()))

    def test_ctwa_capi_payload_fails_closed_without_waba_id(self):
        submission = SimpleNamespace(
            identifiers_json={'ctwa_clid': 'ctwa-click-1'},
            submission_key='ea389d60-82b8-4ea7-b2ee-f38416c10531',
            submitted_at=timezone.now(),
            contacted_at=None,
            qualified_at=timezone.now(),
            won_at=None,
            lost_at=None,
            stage_updated_at=timezone.now(),
            form=SimpleNamespace(code='project-inquiry', category='project'),
        )

        with self.assertRaisesRegex(MetaPayloadError, 'WhatsApp Business Account ID'):
            build_meta_payload(
                submission,
                SimpleNamespace(id=91),
                stage_key='qualified',
                event_name='qualified_lead',
            )

    def test_meta_iso_created_time_is_preserved(self):
        parsed = _timestamp('2026-08-19T12:34:56+0000')
        self.assertEqual(parsed.isoformat(), '2026-08-19T12:34:56+00:00')

    def test_waba_id_falls_back_to_integration_configuration(self):
        integration = SimpleNamespace(config_json={'waba_id': 'configured-waba-id'})
        self.assertEqual(_configured_waba_id(integration, {}), 'configured-waba-id')
        self.assertEqual(_configured_waba_id(integration, {'waba_id': 'payload-waba-id'}), 'payload-waba-id')

    def test_ctwa_page_id_falls_back_to_integration_configuration(self):
        integration = SimpleNamespace(config_json={'page_id': 'configured-page-id'})
        self.assertEqual(_configured_meta_page_id(integration, {}), 'configured-page-id')
        self.assertEqual(_configured_meta_page_id(integration, {'page_id': 'payload-page-id'}), 'payload-page-id')

    @patch('leads.inbound.integration_secret', return_value='secret-page-token')
    @patch('leads.inbound.urllib.request.urlopen')
    def test_meta_lead_access_token_uses_authorization_header(self, mocked_urlopen, _mocked_token):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"id":"lead-1","field_data":[]}'
        mocked_urlopen.return_value = response
        integration = SimpleNamespace(config_json={'api_version': 'v23.0'})

        payload = fetch_meta_lead(integration, 'lead-1')

        self.assertEqual(payload['id'], 'lead-1')
        request = mocked_urlopen.call_args.args[0]
        self.assertNotIn('access_token', request.full_url)
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-page-token')

    @patch('leads.inbound.integration_secret', return_value='secret-page-token')
    @patch('leads.inbound.urllib.request.urlopen')
    def test_meta_provider_error_does_not_leak_response_pii(self, mocked_urlopen, _mocked_token):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = (
            b'{"error":{"code":190,"message":"customer@example.com token-secret"}}'
        )
        mocked_urlopen.return_value = response
        integration = SimpleNamespace(config_json={'api_version': 'v23.0'})

        with self.assertRaises(InboundEventError) as captured:
            fetch_meta_lead(integration, 'lead-1')

        rendered = str(captured.exception)
        self.assertIn('190', rendered)
        self.assertNotIn('customer@example.com', rendered)
        self.assertNotIn('token-secret', rendered)

    @patch('leads.inbound.fetch_meta_lead')
    def test_meta_receipt_rejects_fetched_form_identity_mismatch(self, fetch_meta_lead):
        integration = SimpleNamespace(
            id=71,
            integration_type='leadgen',
            public_id='page-123',
            config_json={'leadgen_form_ids': ['form-456']},
        )
        receipt = SimpleNamespace(
            submission_id=None,
            integration=integration,
            received_at=timezone.now(),
            payload_json={
                'page_id': 'page-123',
                'leadgen_id': 'lead-987',
                'value': {'form_id': 'form-456'},
            },
        )
        fetch_meta_lead.return_value = {
            'id': 'lead-987',
            'form_id': 'form-other',
            'field_data': [],
        }

        with self.assertRaisesRegex(InboundEventError, '已验签 webhook 不一致'):
            process_meta_lead_receipt(receipt)

    @patch('leads.inbound.queue_submission_event', return_value=[SimpleNamespace(id=1)])
    def test_meta_leadgen_queues_initial_crm_event(self, mocked_queue):
        integration = SimpleNamespace(
            integration_type='leadgen',
            config_json={'queue_initial_crm_event': True, 'initial_crm_event_name': 'initial_lead'},
        )
        submission = SimpleNamespace(
            form=SimpleNamespace(
                capi_enabled=True,
                submission_event_name='Lead',
            ),
            consent_json={'marketing': True},
        )

        self.assertEqual(_queue_initial_crm_event(submission, integration), 1)
        mocked_queue.assert_called_once_with(
            submission,
            stage_key='submitted',
            event_name='initial_lead',
            include_external_meta=True,
        )

    @patch('leads.inbound.queue_submission_event', return_value=[SimpleNamespace(id=1)])
    def test_meta_leadgen_defaults_initial_crm_event_to_initial_lead(self, mocked_queue):
        integration = SimpleNamespace(
            integration_type='leadgen',
            config_json={'queue_initial_crm_event': True},
        )
        submission = SimpleNamespace(
            form=SimpleNamespace(capi_enabled=True, submission_event_name='Lead'),
            consent_json={'marketing': True},
        )

        self.assertEqual(_queue_initial_crm_event(submission, integration), 1)
        mocked_queue.assert_called_once_with(
            submission,
            stage_key='submitted',
            event_name='initial_lead',
            include_external_meta=True,
        )

    @patch('leads.inbound.queue_submission_event')
    def test_initial_crm_event_requires_marketing_consent(self, mocked_queue):
        integration = SimpleNamespace(
            integration_type='leadgen',
            config_json={'queue_initial_crm_event': True},
        )
        submission = SimpleNamespace(
            form=SimpleNamespace(capi_enabled=True, submission_event_name='Lead'),
            consent_json={'marketing': False},
        )

        self.assertEqual(_queue_initial_crm_event(submission, integration), 0)
        mocked_queue.assert_not_called()


class GoogleDataManagerPayloadTests(SimpleTestCase):
    def setUp(self):
        now = timezone.now()
        self.integration = SimpleNamespace(
            id=29,
            public_id='123-456-7890',
            config_json={
                'login_account_id': '999-888-7777',
                'qualified_conversion_action_id': '111111111',
                'converted_conversion_action_id': '222222222',
                'validate_only': True,
            },
        )
        session_attributes = base64.urlsafe_b64encode(json.dumps({
            'gad_source': '1',
            'gad_campaignid': '1234567890',
            'session_start_time_usec': '1767711548052000',
            'landing_page_user_agent': 'Mozilla/5.0 test browser',
        }, separators=(',', ':')).encode('utf-8')).decode('ascii').rstrip('=')
        self.submission = SimpleNamespace(
            submission_key='3c4d5678-1111-2222-3333-444455556666',
            email='Nadia@Example.com',
            phone='+971 50 123 4567',
            identifiers_json={
                'gclid': 'google-click-1',
                'gbraid': '',
                'wbraid': '',
                'google_session_attributes': session_attributes,
            },
            consent_json={'marketing': True},
            source_channel='paid_search',
            submitted_at=now - timedelta(days=10),
            stage_updated_at=now - timedelta(days=2),
            qualified_at=now - timedelta(days=2),
            won_at=now,
            updated_at=now,
            buyer_value=Decimal('125000.50'),
            buyer_currency='USD',
        )

    def test_qualified_lead_payload_has_destination_hashes_and_click_id(self):
        payload = build_google_data_manager_payload(
            self.submission,
            self.integration,
            stage_key='qualified',
        )

        destination = payload['destinations'][0]
        event = payload['events'][0]
        self.assertEqual(destination['operatingAccount']['accountId'], '1234567890')
        self.assertEqual(destination['loginAccount']['accountId'], '9998887777')
        self.assertEqual(destination['productDestinationId'], '111111111')
        self.assertEqual(event['adIdentifiers']['gclid'], 'google-click-1')
        self.assertEqual(
            event['adIdentifiers']['sessionAttributes'],
            self.submission.identifiers_json['google_session_attributes'],
        )
        self.assertEqual(event['eventSource'], 'OTHER')
        self.assertEqual(event['consent']['adUserData'], 'CONSENT_GRANTED')
        self.assertEqual(event['consent']['adPersonalization'], 'CONSENT_GRANTED')
        self.assertEqual(payload['encoding'], 'HEX')
        self.assertTrue(payload['validateOnly'])
        self.assertRegex(event['userData']['userIdentifiers'][0]['emailAddress'], r'^[0-9A-F]{64}$')

    def test_google_email_normalization_matches_official_gmail_rules(self):
        self.assertEqual(
            normalize_google_email(' Cloudy.SanFrancisco+Shopping@GMAIL.com '),
            'cloudysanfrancisco@gmail.com',
        )
        self.assertEqual(
            normalize_google_email(' User.Name+NYC@Example.com '),
            'user.name+nyc@example.com',
        )

    def test_clean_form_to_crm_to_google_payload_preserves_matching_identifiers(self):
        form = SimpleNamespace(site_id=7, id=11)
        session_attributes = base64.urlsafe_b64encode(json.dumps({
            'gad_source': '1',
            'gad_campaignid': '9876543210',
            'session_start_time_usec': '1767711548052000',
            'landing_page_url': 'https://vorntek.example/?gad_source=1&gad_campaignid=9876543210',
            'landing_page_user_agent': 'Mozilla/5.0 full-chain test',
        }, separators=(',', ':')).encode('utf-8')).decode('ascii').rstrip('=')
        cleaned = clean_submission_input({
            'full_name': 'Cloudy Buyer',
            'email': 'Cloudy.SanFrancisco+Shopping@GMAIL.com',
            'phone': '+1 800 555 0100',
            'company': 'Example Beverages',
            'country': 'United States',
            'message': 'New beverage filling line project.',
            'source_url': 'https://vorntek.example/?gclid=click-full-chain&gad_source=1&gad_campaignid=9876543210',
            'referrer_url': 'https://www.google.com/',
            'gclid': 'click-full-chain',
            'google_session_attributes': session_attributes,
            'form_started_at': (timezone.now() - timedelta(seconds=5)).isoformat(),
            'utm': {'utm_source': 'google', 'utm_medium': 'cpc'},
            'consent': {'contact': True, 'privacy_notice': True, 'marketing': True},
            'extra_fields': {'product_category': 'Carbonated drink', 'capacity': '36,000 BPH'},
        }, form_definition=form)
        now = timezone.now()
        submission = SimpleNamespace(
            submission_key='full-chain-submission',
            email=cleaned['email'],
            phone=cleaned['phone'],
            identifiers_json=cleaned['identifiers_json'],
            consent_json=cleaned['consent_json'],
            source_channel=cleaned['source_channel'],
            submitted_at=now - timedelta(days=7),
            stage_updated_at=now,
            qualified_at=now,
            won_at=None,
            updated_at=now,
            buyer_value=None,
            buyer_currency='USD',
        )
        payload = build_google_data_manager_payload(submission, self.integration, stage_key='qualified')
        event = payload['events'][0]

        self.assertEqual(event['adIdentifiers']['gclid'], 'click-full-chain')
        self.assertEqual(event['adIdentifiers']['sessionAttributes'], cleaned['identifiers_json']['google_session_attributes'])
        self.assertEqual(event['eventSource'], 'OTHER')
        expected_email = hashlib.sha256('cloudysanfrancisco@gmail.com'.encode('utf-8')).hexdigest().upper()
        self.assertEqual(event['userData']['userIdentifiers'][0]['emailAddress'], expected_email)

    def test_won_lead_uses_converted_action_and_project_value(self):
        payload = build_google_data_manager_payload(
            self.submission,
            self.integration,
            stage_key='won',
        )
        destination = payload['destinations'][0]
        event = payload['events'][0]
        self.assertEqual(destination['productDestinationId'], '222222222')
        self.assertEqual(event['conversionValue'], 125000.50)
        self.assertEqual(event['currency'], 'USD')
        self.assertEqual(normalize_google_phone(self.submission.phone), '+971501234567')
        self.assertEqual(normalize_google_phone('00 971 50 123 4567'), '+971501234567')
        self.assertEqual(normalize_google_phone('050 123 4567'), '')

    def test_payload_rejects_lead_without_marketing_consent(self):
        self.submission.consent_json = {'marketing': False}
        with self.assertRaisesRegex(GoogleDataManagerError, '未授权广告测量'):
            build_google_data_manager_payload(
                self.submission,
                self.integration,
                stage_key='qualified',
            )

    @patch('leads.google_data_manager._google_request', return_value={})
    @patch('leads.google_data_manager._load_google_credentials', return_value='access-token')
    def test_validate_only_success_is_explicitly_validated_not_sent(self, _credentials, mocked_request):
        outbox = SimpleNamespace(
            status='pending',
            payload_json={'validateOnly': True},
            integration=self.integration,
            attempts=0,
            last_error='',
            response_json={},
            updated_at=None,
            save=Mock(),
        )

        self.assertTrue(dispatch_google_outbox_event(outbox))
        self.assertEqual(outbox.status, 'validated')
        self.assertEqual(outbox.delivery_mode, 'validation')
        self.assertEqual(outbox.match_status, 'not_available')
        self.assertIsNotNone(outbox.provider_received_at)
        self.assertIsNotNone(outbox.provider_processed_at)
        self.assertTrue(outbox.response_json['validationOnly'])
        self.assertEqual(mocked_request.call_count, 1)

    @patch('leads.google_data_manager._google_request')
    @patch('leads.google_data_manager._load_google_credentials', return_value='access-token')
    def test_formal_upload_polls_final_status_and_retains_warnings(self, _credentials, mocked_request):
        mocked_request.side_effect = [
            {
                'requestId': 'request-123',
                'fieldWarnings': [{
                    'field': 'events.events[0].optional_field',
                    'description': 'Optional field ignored.',
                }],
            },
            {
                'requestStatusPerDestination': [{
                    'requestStatus': 'SUCCESS',
                    'warningInfo': {
                        'warningCounts': [{
                            'reason': 'PROCESSING_WARNING_REASON_TEST',
                            'recordCount': '1',
                        }],
                    },
                }],
            },
        ]
        outbox = SimpleNamespace(
            status='pending',
            payload_json={'validateOnly': False},
            integration=self.integration,
            attempts=0,
            last_error='',
            response_json={},
            updated_at=None,
            dispatched_at=None,
            save=Mock(),
        )

        self.assertTrue(dispatch_google_outbox_event(outbox))
        self.assertEqual(outbox.status, 'sent')
        self.assertEqual(outbox.provider_request_id, 'request-123')
        self.assertEqual(outbox.match_status, 'not_available')
        self.assertIsNotNone(outbox.provider_processed_at)
        self.assertEqual(mocked_request.call_count, 2)
        warnings = google_response_warnings(outbox.response_json)
        self.assertTrue(any('Optional field ignored.' in warning for warning in warnings))
        self.assertTrue(any('PROCESSING_WARNING_REASON_TEST' in warning for warning in warnings))

    @patch('leads.google_data_manager._google_request')
    @patch('leads.google_data_manager._load_google_credentials', return_value='access-token')
    def test_partial_success_is_terminal_and_uses_provider_record_counts(self, _credentials, mocked_request):
        mocked_request.side_effect = [
            {'requestId': 'request-partial'},
            {
                'requestStatusPerDestination': [{
                    'requestStatus': 'PARTIAL_SUCCESS',
                    'errorInfo': {
                        'errorCounts': [{
                            'reason': 'PROCESSING_ERROR_REASON_INVALID_EVENT',
                            'recordCount': '1',
                        }],
                    },
                }],
            },
        ]
        outbox = SimpleNamespace(
            status='pending',
            payload_json={'validateOnly': False},
            integration=self.integration,
            attempts=0,
            last_error='',
            response_json={},
            updated_at=None,
            dispatched_at=None,
            save=Mock(),
        )

        self.assertFalse(dispatch_google_outbox_event(outbox))
        self.assertEqual(outbox.status, 'partial')
        self.assertIn('部分记录成功', outbox.last_error)
        self.assertIn('PROCESSING_ERROR_REASON_INVALID_EVENT x 1', outbox.last_error)
        self.assertIn('停止自动重试', outbox.last_error)
        self.assertIsNone(outbox.next_attempt_at)
        self.assertIsNotNone(outbox.provider_processed_at)
        self.assertEqual(
            google_response_errors(outbox.response_json),
            ['PROCESSING_ERROR_REASON_INVALID_EVENT x 1'],
        )


class LeadStageTransitionTests(SimpleTestCase):
    @patch('leads.services.dispatch_outbox_event', return_value=True)
    @patch('leads.services.queue_submission_event')
    def test_direct_won_transition_records_and_queues_full_b2b_funnel(
        self,
        mocked_queue,
        mocked_dispatch,
    ):
        contacted_outbox = SimpleNamespace(id=1)
        qualified_outbox = SimpleNamespace(id=2)
        won_outbox = SimpleNamespace(id=3)
        mocked_queue.side_effect = [[contacted_outbox], [qualified_outbox], [won_outbox]]
        submission = SimpleNamespace(
            stage='new',
            qualification_score=80,
            qualification_overridden=False,
            stage_updated_at=None,
            contacted_at=None,
            qualified_at=None,
            won_at=None,
            lost_at=None,
            buyer_value=None,
            buyer_currency='USD',
            updated_at=None,
            consent_json={'marketing': True},
            form=SimpleNamespace(
                capi_enabled=True,
                contacted_event_name='contacted_lead',
                qualified_event_name='qualified_lead',
                won_event_name='converted',
            ),
            save=lambda **kwargs: None,
        )

        _, queued, attempted, succeeded = update_submission_stage(
            submission=submission,
            stage='won',
            buyer_value=Decimal('250000'),
            buyer_currency='USD',
        )

        self.assertIsNotNone(submission.qualified_at)
        self.assertEqual(submission.qualified_at, submission.won_at)
        self.assertEqual(
            [call.kwargs['stage_key'] for call in mocked_queue.call_args_list],
            ['contacted', 'qualified', 'won'],
        )
        self.assertEqual(
            [call.kwargs['event_name'] for call in mocked_queue.call_args_list],
            ['contacted_lead', 'qualified_lead', 'converted'],
        )
        self.assertEqual(queued, 3)
        self.assertEqual(attempted, 3)
        self.assertEqual(succeeded, 3)
        self.assertEqual(mocked_dispatch.call_count, 3)

    @patch('leads.services.dispatch_outbox_event', return_value=True)
    @patch('leads.services.queue_submission_event')
    def test_direct_qualified_transition_queues_contacted_then_qualified(
        self,
        mocked_queue,
        mocked_dispatch,
    ):
        contacted_outbox = SimpleNamespace(id=1)
        qualified_outbox = SimpleNamespace(id=2)
        mocked_queue.side_effect = [[contacted_outbox], [qualified_outbox]]
        submission = SimpleNamespace(
            stage='new',
            qualification_score=80,
            qualification_overridden=False,
            stage_updated_at=None,
            contacted_at=None,
            qualified_at=None,
            won_at=None,
            lost_at=None,
            buyer_value=None,
            buyer_currency='USD',
            updated_at=None,
            consent_json={'marketing': True},
            form=SimpleNamespace(
                capi_enabled=True,
                contacted_event_name='contacted_lead',
                qualified_event_name='qualified_lead',
                won_event_name='converted',
            ),
            save=lambda **kwargs: None,
        )

        _, queued, attempted, succeeded = update_submission_stage(
            submission=submission,
            stage='qualified',
            buyer_value=None,
            buyer_currency='USD',
        )

        self.assertEqual(
            [call.kwargs['stage_key'] for call in mocked_queue.call_args_list],
            ['contacted', 'qualified'],
        )
        self.assertEqual(
            [call.kwargs['event_name'] for call in mocked_queue.call_args_list],
            ['contacted_lead', 'qualified_lead'],
        )
        self.assertEqual((queued, attempted, succeeded), (2, 2, 2))
        self.assertEqual(mocked_dispatch.call_count, 2)

    @patch('leads.services.dispatch_outbox_event', return_value=True)
    @patch('leads.services.queue_submission_event')
    def test_qualified_to_won_queues_only_converted(self, mocked_queue, mocked_dispatch):
        converted_outbox = SimpleNamespace(id=3)
        mocked_queue.return_value = [converted_outbox]
        now = timezone.now()
        submission = SimpleNamespace(
            stage='qualified',
            qualification_score=80,
            qualification_overridden=False,
            stage_updated_at=now,
            contacted_at=now,
            qualified_at=now,
            won_at=None,
            lost_at=None,
            buyer_value=None,
            buyer_currency='USD',
            updated_at=now,
            consent_json={'marketing': True},
            form=SimpleNamespace(
                capi_enabled=True,
                contacted_event_name='contacted_lead',
                qualified_event_name='qualified_lead',
                won_event_name='converted',
            ),
            save=lambda **kwargs: None,
        )

        _, queued, attempted, succeeded = update_submission_stage(
            submission=submission,
            stage='won',
            buyer_value=Decimal('250000'),
            buyer_currency='USD',
        )

        mocked_queue.assert_called_once_with(
            submission,
            stage_key='won',
            event_name='converted',
        )
        self.assertEqual((queued, attempted, succeeded), (1, 1, 1))
        mocked_dispatch.assert_called_once_with(converted_outbox)

    @patch('leads.services.dispatch_outbox_event', return_value=True)
    @patch('leads.services.queue_submission_event')
    def test_contacted_transition_queues_contacted_lead_event(self, mocked_queue, mocked_dispatch):
        outbox = SimpleNamespace(id=1)
        mocked_queue.return_value = [outbox]
        submission = SimpleNamespace(
            stage='new',
            qualification_score=0,
            qualification_overridden=False,
            stage_updated_at=None,
            contacted_at=None,
            qualified_at=None,
            won_at=None,
            lost_at=None,
            buyer_value=None,
            buyer_currency='USD',
            updated_at=None,
            consent_json={'marketing': True},
            form=SimpleNamespace(
                capi_enabled=True,
                contacted_event_name='contacted_lead',
                qualified_event_name='qualified_lead',
                won_event_name='converted',
            ),
            save=lambda **kwargs: None,
        )

        _, queued, attempted, succeeded = update_submission_stage(
            submission=submission,
            stage='contacted',
            buyer_value=None,
            buyer_currency='USD',
        )

        mocked_queue.assert_called_once_with(
            submission,
            stage_key='contacted',
            event_name='contacted_lead',
        )
        self.assertEqual((queued, attempted, succeeded), (1, 1, 1))
        mocked_dispatch.assert_called_once_with(outbox)

    @patch('leads.services.dispatch_outbox_event', return_value=True)
    @patch('leads.services.queue_submission_event')
    def test_unqualified_lead_cannot_bypass_stage_gate(self, mocked_queue, mocked_dispatch):
        submission = SimpleNamespace(
            stage='new',
            qualification_score=60,
            qualification_overridden=False,
        )

        with self.assertRaisesRegex(LeadCaptureError, '至少四项'):
            update_submission_stage(
                submission=submission,
                stage='qualified',
                buyer_value=None,
                buyer_currency='USD',
            )

        self.assertEqual(submission.stage, 'new')
        mocked_queue.assert_not_called()
        mocked_dispatch.assert_not_called()


class EmailDeliveryConfigurationTests(SimpleTestCase):
    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='localhost',
        EMAIL_HOST_USER='',
        EMAIL_HOST_PASSWORD='',
    )
    def test_unconfigured_local_smtp_is_not_ready(self):
        self.assertFalse(email_delivery_configured())

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='smtp.example.com',
        EMAIL_HOST_USER='website@example.com',
        EMAIL_HOST_PASSWORD='app-password',
    )
    def test_authenticated_remote_smtp_is_ready(self):
        self.assertTrue(email_delivery_configured())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_non_smtp_test_backend_is_ready(self):
        self.assertTrue(email_delivery_configured())

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='website@vorntek.example',
    )
    def test_smtp_test_notification_uses_runtime_delivery_backend(self):
        sent, error = send_smtp_test_notification('sales@example.com')

        self.assertTrue(sent)
        self.assertEqual(error, '')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['sales@example.com'])

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='localhost',
        EMAIL_HOST_USER='',
        EMAIL_HOST_PASSWORD='',
    )
    @patch('leads.email_delivery.load_secret')
    def test_encrypted_vault_smtp_config_is_ready(self, mocked_load_secret):
        stored = json.dumps(
            {
                'enabled': True,
                'host': 'smtp.example.com',
                'port': 465,
                'security': 'ssl',
                'username': 'website@example.com',
                'from_email': 'website@example.com',
                'timeout': 8,
            }
        )
        mocked_load_secret.side_effect = lambda key: {
            SMTP_CONFIG_SECRET_KEY: stored,
            SMTP_PASSWORD_SECRET_KEY: 'app-password',
        }.get(key, '')

        config = load_runtime_email_config()

        self.assertTrue(config.ready)
        self.assertEqual(config.source, 'vault')
        self.assertEqual(config.host, 'smtp.example.com')
        self.assertTrue(config.use_ssl)
        self.assertFalse(config.use_tls)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='smtp.env.example.com',
        EMAIL_HOST_USER='env@example.com',
        EMAIL_HOST_PASSWORD='env-password',
        DEFAULT_FROM_EMAIL='env@example.com',
        EMAIL_PORT=587,
        EMAIL_USE_TLS=True,
        EMAIL_USE_SSL=False,
    )
    @patch('leads.email_delivery.load_secret')
    def test_complete_environment_config_has_priority(self, mocked_load_secret):
        config = load_runtime_email_config()

        self.assertTrue(config.ready)
        self.assertEqual(config.source, 'environment')
        self.assertEqual(config.host, 'smtp.env.example.com')
        mocked_load_secret.assert_not_called()


class MarketingReadinessTests(SimpleTestCase):
    def form(self, **overrides):
        values = {
            'code': 'project-inquiry',
            'status': 'active',
            'capi_enabled': True,
            'submission_event_name': 'Lead',
            'contacted_event_name': 'contacted_lead',
            'qualified_event_name': 'qualified_lead',
            'won_event_name': 'converted',
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def integration(self, provider: str, integration_type: str, **overrides):
        values = {
            'provider': SimpleNamespace(code=provider),
            'integration_type': integration_type,
            'public_id': '123456789',
            'secret_ref': 'vault:configured-reference',
            'enabled': True,
            'config_json': {},
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_recommended_b2b_form_events_pass(self):
        checks = inspect_form_definition(self.form())

        self.assertFalse([item for item in checks if item['required'] and item['status'] == 'fail'])

    def test_purchase_is_rejected_as_project_won_event(self):
        checks = inspect_form_definition(self.form(won_event_name='Purchase'))

        won = next(item for item in checks if item['code'].endswith('.won_event_name'))
        self.assertEqual(won['status'], 'fail')
        self.assertIn('converted', won['detail'])

    def test_placeholder_meta_leadgen_assets_fail(self):
        integration = self.integration(
            'meta',
            'leadgen',
            public_id='pending-page-id',
            secret_ref='',
            config_json={
                'form_code': 'project-inquiry',
                'queue_initial_crm_event': True,
                'initial_crm_event_name': 'initial_lead',
            },
        )
        checks = inspect_marketing_integration(integration)

        failed_codes = {item['code'] for item in checks if item['status'] == 'fail'}
        self.assertIn('integration.meta.leadgen.public_id', failed_codes)
        self.assertIn('integration.meta.leadgen.secret_ref', failed_codes)
        self.assertIn('integration.meta.leadgen.app_secret_ref', failed_codes)

    def test_structurally_complete_google_data_manager_config_passes(self):
        integration = self.integration(
            'google',
            'data_manager',
            public_id='123-456-7890',
            secret_ref='',
            config_json={
                'credentials_mode': 'adc',
                'qualified_conversion_action_id': '111111111',
                'converted_conversion_action_id': '222222222',
                'validate_only': False,
            },
        )
        checks = inspect_marketing_integration(integration)

        self.assertFalse([item for item in checks if item['required'] and item['status'] == 'fail'])

    def test_whatsapp_ctwa_readiness_defaults_to_waba_identity(self):
        integration = self.integration(
            'whatsapp',
            'cloud_api',
            config_json={
                'form_code': 'project-inquiry',
                'app_secret_ref': 'vault:app-secret',
                'webhook_verify_token_ref': 'vault:verify-token',
                'waba_id': 'waba-1',
            },
        )

        checks = inspect_marketing_integration(integration)
        identity = next(item for item in checks if item['code'].endswith('.ctwa_identity_mode'))
        self.assertEqual(identity['status'], 'pass')
        self.assertFalse([item for item in checks if item['code'].endswith('.page_id')])

        integration.config_json['ctwa_identity_mode'] = 'page'
        checks = inspect_marketing_integration(integration)
        page = next(item for item in checks if item['code'].endswith('.page_id'))
        self.assertEqual(page['status'], 'fail')

    def test_ycloud_whatsapp_readiness_uses_provider_specific_webhook_contract(self):
        integration = self.integration(
            'whatsapp',
            'cloud_api',
            config_json={
                'transport_provider': 'ycloud',
                'form_code': 'project-inquiry',
                'ycloud_webhook_secret_ref': 'vault:ycloud-webhook-secret',
                'ycloud_webhook_endpoint_id': 'wep-8155',
                'business_phone_e164': '+15592028155',
                'waba_id': '4185566811756317',
            },
        )

        checks = inspect_marketing_integration(integration)
        failed = [item for item in checks if item['required'] and item['status'] == 'fail']
        codes = {item['code'] for item in checks}
        self.assertFalse(failed)
        self.assertIn('integration.whatsapp.cloud_api.ycloud_webhook_secret_ref', codes)
        self.assertNotIn('integration.whatsapp.cloud_api.app_secret_ref', codes)
