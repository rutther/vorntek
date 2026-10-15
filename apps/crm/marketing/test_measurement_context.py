import importlib
from unittest.mock import patch
from django.test import RequestFactory
from marketing.test_flow_runtime import FlowRuntimeTests


class MeasurementContextTests(FlowRuntimeTests):
    def test_http_config_is_scoped_to_verified_host_and_exact_path(self):
        self.applied()
        endpoint = '/api/marketing/measurement-config/'
        response = self.client.get(endpoint, {'path':'/contact/'}, HTTP_HOST='127.0.0.1:25000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store, max-age=0')
        self.assertEqual(response.json()['metaPixels'][0]['events'], ['Lead'])
        self.assertTrue(response.json()['measurementContext'])
        self.assertEqual(self.client.get(endpoint, {'path':'/'},
            HTTP_HOST='unknown.example.test', HTTP_X_FORWARDED_HOST='127.0.0.1:25000').status_code, 400)
        self.assertEqual(self.client.get(endpoint, {'path':'//elsewhere/'},
            HTTP_HOST='127.0.0.1:25000').status_code, 400)

    def test_public_config_contains_only_exact_allowed_targets(self):
        integration, _ = self.applied()
        from marketing.public_config import public_measurement_config
        data = public_measurement_config(site=self.site, path='/')
        self.assertEqual([p['pixelId'] for p in data['metaPixels']], ['123456789'])
        self.assertEqual(data['metaPixels'][0]['events'], ['PageView'])
        other = public_measurement_config(site=self.site, path='/unselected/')
        self.assertEqual(other['metaPixels'], [])
        self.assertNotIn('TEST_ONLY_TOKEN_REF', str(data))

    def test_context_tamper_wrong_path_form_and_expiry(self):
        self.applied()
        from marketing.public_config import public_measurement_config
        module = importlib.import_module('marketing.measurement_context')
        data = public_measurement_config(site=self.site, path='/contact/')
        token = data['measurementContext']
        valid = module.validate_measurement_context(site=self.site, form=self.form,
            path='/contact/', token=token)
        self.assertEqual(len(valid['revisions']), 1)
        for path, candidate in [('/', token), ('/contact/', token+'bad')]:
            self.assertIsNone(module.validate_measurement_context(site=self.site, form=self.form, path=path, token=candidate))
        with patch('django.core.signing.time.time', return_value=9999999999):
            self.assertIsNone(module.validate_measurement_context(site=self.site, form=self.form, path='/contact/', token=token))

    def test_unknown_host_and_forwarded_host_cannot_select_site(self):
        module = importlib.import_module('marketing.measurement_context')
        request = RequestFactory().get('/', HTTP_HOST='unknown.example.test', HTTP_X_FORWARDED_HOST='127.0.0.1:25000')
        self.assertIsNone(module.resolve_measurement_site(request))
        request = RequestFactory().get('/?site=other_site', HTTP_HOST='127.0.0.1:25000')
        self.assertEqual(module.resolve_measurement_site(request).pk, self.site.pk)
