from unittest.mock import patch

from django.test import SimpleTestCase

from console.marketing_forms import MarketingIntegrationEditorForm


class MarketingCredentialStatusTests(SimpleTestCase):
    @patch('console.marketing_forms.load_secret', return_value='token-value')
    def test_vault_reference_requires_successful_decryption(self, mocked_load):
        self.assertTrue(MarketingIntegrationEditorForm._secret_available('vault:meta-primary'))
        mocked_load.assert_called_once_with('meta-primary')

    @patch('console.marketing_forms.load_secret', side_effect=RuntimeError('cannot decrypt'))
    def test_broken_vault_reference_is_not_reported_as_ready(self, _mocked_load):
        self.assertFalse(MarketingIntegrationEditorForm._secret_available('vault:meta-primary'))
