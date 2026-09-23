from pathlib import PurePosixPath

from django.test import SimpleTestCase, TestCase

from siteos_admin.release import _version_candidates, release_version


class ReleaseVersionLayoutTests(SimpleTestCase):
    def test_container_root_depth_has_safe_version_candidates(self):
        base_dir = PurePosixPath('/app')
        self.assertEqual(
            _version_candidates(base_dir),
            (PurePosixPath('/app/VERSION'), PurePosixPath('/VERSION')),
        )


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
