from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from .marketing_forms import MarketingIntegrationEditorForm


def google_integration(config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        provider=SimpleNamespace(code='google'),
        integration_type='data_manager',
        name='Google Data Manager',
        enabled=False,
        public_id='pending-customer-id',
        consent_category='marketing',
        secret_ref=None,
        config_json=dict(config or {}),
        full_clean=Mock(),
        save=Mock(),
    )


def whatsapp_integration(config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=8,
        provider=SimpleNamespace(code='whatsapp'),
        integration_type='cloud_api',
        name='WhatsApp Cloud API',
        enabled=True,
        public_id='phone-id-1',
        consent_category='marketing',
        secret_ref='vault:marketing_integration:8:primary_secret',
        config_json=dict(config or {}),
        full_clean=Mock(),
        save=Mock(),
    )


def meta_pixel_integration(config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=9,
        provider=SimpleNamespace(code='meta'),
        integration_type='pixel',
        name='Meta CAPI',
        enabled=True,
        public_id='pixel-123',
        consent_category='marketing',
        secret_ref='vault:marketing_integration:9:primary_secret',
        config_json=dict(config or {}),
        full_clean=Mock(),
        save=Mock(),
    )


def meta_leadgen_integration(config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=10,
        provider=SimpleNamespace(code='meta'),
        integration_type='leadgen',
        name='Meta Instant Forms',
        enabled=True,
        public_id='page-123',
        consent_category='marketing',
        secret_ref='vault:marketing_integration:10:primary_secret',
        config_json=dict(config or {}),
        full_clean=Mock(),
        save=Mock(),
    )


@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True)
class PublicMeasurementConfigTests(SimpleTestCase):
    @patch('marketing.public_config.MarketingIntegration.objects')
    def test_public_config_only_exposes_enabled_valid_tag(self, objects):
        integration = google_integration(
            {
                'google_tag_enabled': True,
                'google_tag_id': 'aw-123456789',
                'credentials_mode': 'service_account',
                'credentials_json': 'must-not-be-exposed',
            }
        )
        objects.select_related.return_value.filter.return_value.order_by.return_value.first.return_value = integration

        response = self.client.get(reverse('public_marketing_measurement_config'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store, max-age=0')
        self.assertEqual(
            response.json(),
            {
                'ok': True,
                'meta_pixel_enabled': False,
                'meta_pixel_id': '',
                'google_tag_enabled': True,
                'google_tag_id': 'AW-123456789',
                'consent_mode': 'v2',
            },
        )

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False)
    @patch('marketing.public_config.MarketingIntegration.objects')
    def test_isolated_endpoint_is_silent_without_reading_integrations(self, objects):
        response = self.client.get(reverse('public_marketing_measurement_config'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['meta_pixel_enabled'])
        self.assertFalse(response.json()['google_tag_enabled'])
        objects.filter.assert_not_called()
        objects.select_related.assert_not_called()

    @patch('marketing.public_config.MarketingIntegration.objects')
    def test_enabled_pixel_exposes_only_public_id(self, objects):
        objects.filter.return_value.order_by.return_value.first.return_value = SimpleNamespace(
            public_id='123456789', secret_ref='must-not-be-exposed',
        )
        objects.select_related.return_value.filter.return_value.order_by.return_value.first.return_value = None
        response = self.client.get(reverse('public_marketing_measurement_config'))
        self.assertTrue(response.json()['meta_pixel_enabled'])
        self.assertEqual(response.json()['meta_pixel_id'], '123456789')
        self.assertNotContains(response, 'must-not-be-exposed')

    @patch('marketing.public_config.MarketingIntegration.objects')
    def test_invalid_or_disabled_tag_is_not_exposed(self, objects):
        for config in (
            {'google_tag_enabled': True, 'google_tag_id': 'not-a-tag'},
            {'google_tag_enabled': False, 'google_tag_id': 'AW-123456789'},
        ):
            objects.select_related.return_value.filter.return_value.order_by.return_value.first.return_value = google_integration(config)
            payload = self.client.get(reverse('public_marketing_measurement_config')).json()
            self.assertFalse(payload['google_tag_enabled'])
            self.assertEqual(payload['google_tag_id'], '')

    @patch('marketing.public_config.MarketingIntegration.objects')
    def test_non_get_method_is_rejected_without_querying_config(self, objects):
        response = self.client.post(reverse('public_marketing_measurement_config'))
        self.assertEqual(response.status_code, 405)
        objects.select_related.assert_not_called()

    def test_editor_validates_and_normalizes_google_tag_id_while_data_manager_is_disabled(self):
        integration = google_integration()
        base_data = {
            'name': integration.name,
            'public_id': integration.public_id,
            'consent_category': 'marketing',
            'google_tag_enabled': 'on',
            'config_json': '',
        }
        invalid = MarketingIntegrationEditorForm(
            data={**base_data, 'google_tag_id': 'bad-tag'},
            integration=integration,
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn('google_tag_id', invalid.errors)

        valid = MarketingIntegrationEditorForm(
            data={**base_data, 'google_tag_id': 'aw-987654321'},
            integration=integration,
        )
        self.assertTrue(valid.is_valid(), valid.errors)
        valid.save()
        self.assertTrue(integration.config_json['google_tag_enabled'])
        self.assertEqual(integration.config_json['google_tag_id'], 'AW-987654321')
        integration.full_clean.assert_called_once_with()
        integration.save.assert_called_once()

    def test_editor_preserves_last_connection_diagnostic_when_saving(self):
        diagnostic = {'schema_version': 1, 'overall': 'warn', 'checks': []}
        integration = google_integration({'last_diagnostic': diagnostic})
        form = MarketingIntegrationEditorForm(
            data={
                'name': integration.name,
                'public_id': integration.public_id,
                'consent_category': 'marketing',
                'config_json': '',
            },
            integration=integration,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(integration.config_json['last_diagnostic'], diagnostic)

    def test_editor_secret_fields_do_not_invite_login_credential_autofill(self):
        form = MarketingIntegrationEditorForm(integration=whatsapp_integration())

        self.assertEqual(form.fields['secret_ref'].widget.attrs['autocomplete'], 'off')
        for field_name in (
            'primary_secret', 'app_secret', 'webhook_verify_token',
            'ycloud_webhook_secret',
        ):
            self.assertEqual(
                form.fields[field_name].widget.attrs['autocomplete'],
                'new-password',
            )

    @patch('console.marketing_forms.store_secret')
    @patch.object(MarketingIntegrationEditorForm, '_secret_available', return_value=True)
    def test_enabled_meta_leadgen_requires_and_normalizes_form_allowlist(
        self,
        _secret_available,
        _store_secret,
    ):
        integration = meta_leadgen_integration()
        base_data = {
            'name': integration.name,
            'enabled': 'on',
            'public_id': integration.public_id,
            'consent_category': 'marketing',
            'secret_ref': integration.secret_ref,
            'form_code': 'project-inquiry',
            'contact_consent_confirmed': 'on',
            'marketing_consent_confirmed': 'on',
            'app_secret': 'app-secret',
            'webhook_verify_token': 'verify-token',
            'api_version': 'v26.0',
            'config_json': '',
        }
        missing = MarketingIntegrationEditorForm(data=base_data, integration=integration)
        self.assertFalse(missing.is_valid())
        self.assertIn('leadgen_form_ids', missing.errors)

        valid = MarketingIntegrationEditorForm(
            data={**base_data, 'leadgen_form_ids': 'form-1\nform-2, form-1'},
            integration=integration,
        )
        self.assertTrue(valid.is_valid(), valid.errors)
        valid.save()
        self.assertEqual(integration.config_json['leadgen_form_ids'], ['form-1', 'form-2'])

    @patch.object(MarketingIntegrationEditorForm, '_secret_available', return_value=True)
    def test_enabled_meta_integration_cannot_clear_its_required_primary_secret(self, _secret_available):
        integration = meta_pixel_integration()
        base_data = {
            'name': integration.name,
            'enabled': 'on',
            'public_id': integration.public_id,
            'consent_category': 'marketing',
            'secret_ref': integration.secret_ref,
            'api_version': 'v26.0',
            'config_json': '',
        }

        clearing = MarketingIntegrationEditorForm(
            data={**base_data, 'clear_stored_secret': 'on'},
            integration=integration,
        )

        self.assertFalse(clearing.is_valid())
        self.assertIn('primary_secret', clearing.errors)

        replacing = MarketingIntegrationEditorForm(
            data={
                **base_data,
                'clear_stored_secret': 'on',
                'primary_secret': 'replacement-token',
            },
            integration=integration,
        )
        self.assertTrue(replacing.is_valid(), replacing.errors)

    @patch.object(MarketingIntegrationEditorForm, '_secret_available', return_value=True)
    def test_enabled_webhook_cannot_clear_required_webhook_secrets(self, _secret_available):
        integration = whatsapp_integration(
            {
                'app_secret_ref': 'vault:marketing_integration:8:app_secret',
                'webhook_verify_token_ref': 'vault:marketing_integration:8:webhook_verify_token',
            }
        )
        form = MarketingIntegrationEditorForm(
            data={
                'name': integration.name,
                'enabled': 'on',
                'public_id': integration.public_id,
                'consent_category': 'marketing',
                'secret_ref': integration.secret_ref,
                'form_code': 'project-inquiry',
                'waba_id': 'waba-1',
                'page_id': 'page-ctwa-123',
                'contact_consent_confirmed': 'on',
                'marketing_consent_confirmed': 'on',
                'clear_webhook_secrets': 'on',
                'api_version': 'v26.0',
                'config_json': '',
            },
            integration=integration,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('app_secret', form.errors)
        self.assertIn('webhook_verify_token', form.errors)

    @patch('console.marketing_forms.store_secret')
    @patch.object(
        MarketingIntegrationEditorForm,
        '_secret_available',
        side_effect=lambda reference: 'ycloud_webhook_secret' not in reference,
    )
    def test_ycloud_transport_requires_official_endpoint_contract_not_meta_webhook_secrets(
        self,
        _secret_available,
        store_secret,
    ):
        integration = whatsapp_integration()
        base_data = {
            'name': integration.name,
            'enabled': 'on',
            'public_id': '1069437129587829',
            'consent_category': 'marketing',
            'secret_ref': integration.secret_ref,
            'transport_provider': 'ycloud',
            'business_phone_e164': '+1 (559) 202-8155',
            'form_code': 'project-inquiry',
            'waba_id': '4185566811756317',
            'contact_consent_confirmed': 'on',
            'marketing_consent_confirmed': 'on',
            'api_version': 'v26.0',
            'config_json': '',
        }
        missing_contract = MarketingIntegrationEditorForm(data=base_data, integration=integration)
        self.assertFalse(missing_contract.is_valid())
        self.assertIn('ycloud_webhook_secret', missing_contract.errors)
        self.assertIn('ycloud_webhook_endpoint_id', missing_contract.errors)
        self.assertNotIn('app_secret', missing_contract.errors)
        self.assertNotIn('webhook_verify_token', missing_contract.errors)

        valid = MarketingIntegrationEditorForm(
            data={
                **base_data,
                'ycloud_webhook_endpoint_id': 'wep-8155',
                'ycloud_webhook_secret': 'whsec-test-value',
            },
            integration=integration,
        )
        self.assertTrue(valid.is_valid(), valid.errors)
        valid.save()
        self.assertEqual(integration.config_json['transport_provider'], 'ycloud')
        self.assertEqual(integration.config_json['business_phone_e164'], '+15592028155')
        self.assertEqual(integration.config_json['ycloud_webhook_endpoint_id'], 'wep-8155')
        self.assertTrue(integration.config_json['ycloud_webhook_secret_ref'].startswith('vault:'))
        store_secret.assert_called_once()

    @patch.object(MarketingIntegrationEditorForm, '_secret_available', return_value=True)
    def test_whatsapp_editor_defaults_to_waba_and_supports_legacy_page_mode(self, _secret_available):
        integration = whatsapp_integration()
        base_data = {
            'name': integration.name,
            'enabled': 'on',
            'public_id': integration.public_id,
            'consent_category': 'marketing',
            'form_code': 'project-inquiry',
            'waba_id': 'waba-1',
            'contact_consent_confirmed': 'on',
            'marketing_consent_confirmed': 'on',
            'config_json': '',
        }
        valid_waba = MarketingIntegrationEditorForm(data=base_data, integration=integration)
        self.assertTrue(valid_waba.is_valid(), valid_waba.errors)
        valid_waba.save()
        self.assertEqual(integration.config_json['waba_id'], 'waba-1')
        self.assertEqual(integration.config_json['ctwa_identity_mode'], 'waba')

        missing_page = MarketingIntegrationEditorForm(
            data={**base_data, 'ctwa_identity_mode': 'page'},
            integration=integration,
        )
        self.assertFalse(missing_page.is_valid())
        self.assertIn('page_id', missing_page.errors)
