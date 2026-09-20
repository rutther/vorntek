from django.test import TestCase

from siteos_admin.release import release_version


class HealthEndpointTests(TestCase):
    def test_health_identifies_service_and_release(self):
        response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                'status': 'ok',
                'service': 'vorntek-crm',
                'version': release_version(),
                'database': 'ok',
            },
        )
