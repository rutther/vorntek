from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from leads.google_data_manager import GoogleDataManagerError

from .marketing_diagnostics import diagnose_marketing_integration


def integration(*, provider: str, integration_type: str, public_id: str, enabled: bool = True, config=None):
    return SimpleNamespace(
        id=17,
        provider=SimpleNamespace(code=provider),
        integration_type=integration_type,
        public_id=public_id,
        enabled=enabled,
        secret_ref='vault:marketing_integration:17:primary_secret',
        config_json=dict(config or {}),
    )


class MarketingIntegrationDiagnosticTests(SimpleTestCase):
    @patch('console.marketing_diagnostics.resolve_meta_access_token', return_value='super-secret-token')
    @patch('console.marketing_diagnostics._graph_get')
    def test_meta_pixel_reports_identity_and_dataset_without_leaking_token(self, graph_get, _token):
        graph_get.side_effect = [
            {'ok': True, 'http_status': 200, 'response_version': 'v26.0', 'payload': {'id': 'principal'}},
            {'ok': True, 'http_status': 200, 'response_version': 'v26.0', 'payload': {'id': 'dataset'}},
        ]
        result = diagnose_marketing_integration(
            integration(
                provider='meta',
                integration_type='pixel',
                public_id='123456789012345',
                config={'api_version': 'v26.0'},
            )
        )

        self.assertEqual(result['overall'], 'pass')
        self.assertNotIn('super-secret-token', json.dumps(result))
        self.assertEqual(graph_get.call_count, 2)

    @patch('console.marketing_diagnostics.resolve_meta_access_token', return_value='token')
    @patch('console.marketing_diagnostics._graph_get')
    def test_meta_dataset_permission_failure_is_visible_as_warning(self, graph_get, _token):
        graph_get.side_effect = [
            {'ok': True, 'http_status': 200, 'response_version': 'v26.0', 'payload': {'id': 'principal'}},
            {'ok': False, 'http_status': 400, 'error_code': 100, 'error_type': 'OAuthException'},
        ]
        result = diagnose_marketing_integration(
            integration(provider='meta', integration_type='pixel', public_id='123456789012345')
        )

        self.assertEqual(result['overall'], 'warn')
        dataset = next(item for item in result['checks'] if item['code'] == 'dataset_access')
        self.assertEqual(dataset['status'], 'warn')
        self.assertIn('错误码 100', dataset['detail'])

    @patch('console.marketing_diagnostics.integration_secret', return_value='')
    def test_meta_leadgen_placeholder_state_reports_actionable_failures(self, _secret):
        result = diagnose_marketing_integration(
            integration(provider='meta', integration_type='leadgen', public_id='pending-page-id', enabled=False)
        )

        self.assertEqual(result['overall'], 'fail')
        failed_codes = {item['code'] for item in result['checks'] if item['status'] == 'fail'}
        self.assertTrue({
            'app_secret', 'webhook_verify_token', 'page_id', 'leadgen_form_allowlist',
        } <= failed_codes)

    @patch('console.marketing_diagnostics.integration_secret', return_value='stored-secret')
    @patch('console.marketing_diagnostics._graph_get')
    def test_meta_leadgen_diagnostic_matches_allowlist_to_page_assets(self, graph_get, _secret):
        graph_get.side_effect = [
            {'ok': True, 'payload': {'id': 'page-123', 'name': 'Test Page'}},
            {
                'ok': True,
                'payload': {'data': [
                    {'id': 'form-1', 'status': 'ACTIVE'},
                    {'id': 'form-2', 'status': 'ACTIVE'},
                ]},
            },
        ]
        result = diagnose_marketing_integration(
            integration(
                provider='meta',
                integration_type='leadgen',
                public_id='page-123',
                config={'leadgen_form_ids': ['form-1', 'form-2']},
            )
        )

        by_code = {item['code']: item for item in result['checks']}
        self.assertEqual(by_code['leadgen_form_allowlist']['status'], 'pass')
        self.assertEqual(by_code['leadgen_form_assets']['status'], 'pass')

        graph_get.side_effect = [
            {'ok': True, 'payload': {'id': 'page-123'}},
            {'ok': True, 'payload': {'data': [{'id': 'form-1', 'status': 'ACTIVE'}]}},
        ]
        mismatch = diagnose_marketing_integration(
            integration(
                provider='meta',
                integration_type='leadgen',
                public_id='page-123',
                config={'leadgen_form_ids': ['form-1', 'form-2']},
            )
        )
        self.assertEqual(
            next(item for item in mismatch['checks'] if item['code'] == 'leadgen_form_assets')['status'],
            'fail',
        )

    @patch('console.marketing_diagnostics.integration_secret', return_value='')
    def test_whatsapp_diagnostic_defaults_to_waba_identity(self, _secret):
        configured = diagnose_marketing_integration(
            integration(
                provider='whatsapp',
                integration_type='cloud_api',
                public_id='phone-id-1',
                config={'waba_id': 'waba-1'},
            )
        )
        identity = next(item for item in configured['checks'] if item['code'] == 'ctwa_identity_mode')
        self.assertEqual(identity['status'], 'pass')
        self.assertIn('WABA ID', identity['detail'])
        self.assertFalse([item for item in configured['checks'] if item['code'] == 'ctwa_page_id'])

        missing_page = diagnose_marketing_integration(
            integration(
                provider='whatsapp',
                integration_type='cloud_api',
                public_id='phone-id-1',
                config={'waba_id': 'waba-1', 'ctwa_identity_mode': 'page'},
            )
        )
        page = next(item for item in missing_page['checks'] if item['code'] == 'ctwa_page_id')
        self.assertEqual(page['status'], 'fail')

    @patch('console.marketing_diagnostics.integration_secret', return_value='token')
    @patch('console.marketing_diagnostics._graph_get')
    def test_whatsapp_diagnostic_proves_official_coexistence(self, graph_get, _secret):
        graph_get.side_effect = [
            {
                'ok': True,
                'http_status': 200,
                'response_version': 'v26.0',
                'payload': {
                    'id': '1069437129587829',
                    'display_phone_number': '+1 559-202-8155',
                    'verified_name': 'SzNewCrown',
                    'platform_type': 'CLOUD_API',
                    'is_on_biz_app': True,
                },
            },
            {
                'ok': True,
                'http_status': 200,
                'response_version': 'v26.0',
                'payload': {'id': '4185566811756317', 'name': 'SzNewCrown'},
            },
            {
                'ok': True,
                'http_status': 200,
                'response_version': 'v26.0',
                'payload': {'id': '123456789012345'},
            },
        ]

        result = diagnose_marketing_integration(
            integration(
                provider='whatsapp',
                integration_type='cloud_api',
                public_id='1069437129587829',
                config={'waba_id': '4185566811756317'},
            )
        )

        registration = next(item for item in result['checks'] if item['code'] == 'cloud_api_registration')
        coexistence = next(item for item in result['checks'] if item['code'] == 'coexistence')
        capi_dataset = next(item for item in result['checks'] if item['code'] == 'whatsapp_capi_dataset')
        self.assertEqual(registration['status'], 'pass')
        self.assertEqual(coexistence['status'], 'pass')
        self.assertEqual(capi_dataset['status'], 'pass')
        self.assertIn('123456789012345', capi_dataset['detail'])
        self.assertIn('is_on_biz_app=true', coexistence['detail'])
        self.assertIn('platform_type,is_on_biz_app', graph_get.call_args_list[0].args[0])
        self.assertTrue(graph_get.call_args_list[2].args[0].endswith('/4185566811756317/dataset'))

    @patch('console.marketing_diagnostics.integration_secret', return_value='token')
    @patch('console.marketing_diagnostics._graph_get')
    def test_whatsapp_diagnostic_exposes_missing_business_messaging_capi_permission(self, graph_get, _secret):
        graph_get.side_effect = [
            {
                'ok': True,
                'http_status': 200,
                'response_version': 'v26.0',
                'payload': {
                    'id': '1069437129587829',
                    'platform_type': 'CLOUD_API',
                    'is_on_biz_app': True,
                },
            },
            {
                'ok': True,
                'http_status': 200,
                'response_version': 'v26.0',
                'payload': {'id': '4185566811756317', 'name': 'SzNewCrown'},
            },
            {
                'ok': False,
                'http_status': 403,
                'error_code': 200,
                'error_type': 'OAuthException',
            },
        ]

        result = diagnose_marketing_integration(
            integration(
                provider='whatsapp',
                integration_type='cloud_api',
                public_id='1069437129587829',
                config={'waba_id': '4185566811756317'},
            )
        )

        capi_dataset = next(item for item in result['checks'] if item['code'] == 'whatsapp_capi_dataset')
        self.assertEqual(capi_dataset['status'], 'warn')
        self.assertIn('whatsapp_business_manage_events', capi_dataset['detail'])
        self.assertEqual(result['overall'], 'warn')

    @patch('console.marketing_diagnostics.integration_secret', return_value='token')
    @patch('console.marketing_diagnostics._graph_get')
    def test_whatsapp_diagnostic_rejects_number_not_registered_for_cloud_api(self, graph_get, _secret):
        graph_get.return_value = {
            'ok': True,
            'http_status': 200,
            'response_version': 'v26.0',
            'payload': {
                'id': '1069437129587829',
                'display_phone_number': '+1 559-202-8155',
                'verified_name': 'SzNewCrown',
                'platform_type': 'NOT_APPLICABLE',
                'is_on_biz_app': True,
            },
        }

        result = diagnose_marketing_integration(
            integration(
                provider='whatsapp',
                integration_type='cloud_api',
                public_id='1069437129587829',
                config={'waba_id': ''},
            )
        )

        registration = next(item for item in result['checks'] if item['code'] == 'cloud_api_registration')
        coexistence = next(item for item in result['checks'] if item['code'] == 'coexistence')
        self.assertEqual(registration['status'], 'fail')
        self.assertEqual(coexistence['status'], 'warn')
        self.assertEqual(result['overall'], 'fail')

    @patch(
        'console.marketing_diagnostics._load_google_credentials',
        side_effect=GoogleDataManagerError('Google OAuth 凭据缺失。'),
    )
    def test_google_diagnostic_separates_browser_tag_from_server_credentials(self, _credentials):
        result = diagnose_marketing_integration(
            integration(
                provider='google',
                integration_type='data_manager',
                public_id='pending-customer-id',
                enabled=False,
                config={'google_tag_enabled': False},
            )
        )

        self.assertEqual(result['overall'], 'fail')
        tag = next(item for item in result['checks'] if item['code'] == 'google_tag')
        credentials = next(item for item in result['checks'] if item['code'] == 'google_credentials')
        self.assertEqual(tag['status'], 'info')
        self.assertEqual(credentials['status'], 'fail')
