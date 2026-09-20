import hashlib
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT/'apps/crm/console/static/console'


class PublicReleaseInputTests(unittest.TestCase):
    def test_historical_content_rewriters_are_not_release_inputs(self):
        names = [
            'enrich_product_page_sources.py', 'refresh_benchmark_free_pages.py',
            'refresh_products_from_newamstar.py', 'seed_newamstar_articles.py',
            'upgrade_demo_content_v2.py',
        ]
        paths = ['apps/crm/scripts/' + name for name in names]
        for path in paths:
            with self.subTest(path=path):
                self.assertFalse((ROOT/path).exists())
                self.assertIn(path, (ROOT/'.gitignore').read_text())
                self.assertIn(path, (ROOT/'.dockerignore').read_text())
        result = subprocess.run(['git', 'ls-files', '--', *paths], cwd=ROOT,
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), '')
        # Do not execute imports: a historical seeder initializes Django on import.
        for path in (ROOT/'apps/crm').rglob('*.py'):
            if any(part.startswith('.') for part in path.relative_to(ROOT).parts):
                continue
            source = path.read_text(encoding='utf-8-sig')
            for name in names:
                with self.subTest(caller=str(path.relative_to(ROOT)), retired=name):
                    self.assertNotIn(name, source)
                    self.assertNotIn(name.removesuffix('.py'), source)

    def test_old_host_tools_are_retired_from_distribution(self):
        retired = [
            'apps/crm/scripts/build-production-release.ps1',
            'apps/crm/scripts/install-production-release.sh',
            'apps/crm/scripts/install-customer-export-worker.sh',
            'apps/crm/deploy/siteos-customer-export-worker',
            'apps/crm/deploy/siteos-customer-export-worker.service',
            'apps/crm/leads/test_production_release_scripts.py',
        ]
        gitignore = (ROOT/'.gitignore').read_text()
        dockerignore = (ROOT/'.dockerignore').read_text()
        for name in retired:
            with self.subTest(name=name):
                self.assertFalse((ROOT/name).exists())
                self.assertIn(name, gitignore)
                self.assertIn(name, dockerignore)
        result = subprocess.run(['git', 'ls-files', '--', *retired], cwd=ROOT,
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), '')

    def test_private_qa_is_not_tracked_or_in_container_context(self):
        prefix = 'apps/crm/console/static/console/v2/qa/'
        self.assertIn(prefix, (ROOT/'.gitignore').read_text())
        self.assertIn('**/console/static/console/v2/qa', (ROOT/'.dockerignore').read_text())
        result = subprocess.run(['git', 'ls-files', '--', prefix], cwd=ROOT,
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), '')

    def test_added_license_notices_match_verified_upstream_package_bytes(self):
        expected = {
            'lucide.LICENSE.txt': 'b495047bd93a9b06913511076f504daba17d5bbeb3e0650f3bb53a4220329c57',
            'vendor/tabler/1.4.0/BOOTSTRAP_LICENSE': '4620c84ad5ce8602ff65640ed6b7c8b78ebb9e036584f0ebc1ccc88206a4bb51',
            'vendor/tabler/1.4.0/POPPER_LICENSE': 'bf67e2c9b7974543bb18ced3a0b37f53729aaff953b442dd89a3adaf90fd93e8',
        }
        for name, checksum in expected.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((STATIC/name).read_bytes()).hexdigest(), checksum)

    def test_python_inventory_covers_current_lock_without_private_machine_paths(self):
        manifest = json.loads((ROOT/'docs/review/python-dependencies.json').read_text(encoding='utf-8'))
        lock_bytes = (ROOT/'apps/crm/requirements.lock').read_bytes()
        expected = dict(line.split('==', 1) for line in lock_bytes.decode().splitlines()
                        if line and not line.startswith('#'))
        self.assertEqual(manifest['requirements_lock_sha256'], hashlib.sha256(lock_bytes).hexdigest())
        self.assertEqual({p['name']: p['version'] for p in manifest['packages']}, expected)
        for package in manifest['packages']:
            self.assertTrue(package['license_file_evidence'])
            for item in package['license_file_evidence']:
                self.assertNotIn(':', item['path_in_dist_info'])
                self.assertNotIn('..', item['path_in_dist_info'])
                self.assertRegex(item['sha256'], r'^[a-f0-9]{64}$')

    def test_recorded_vendor_byte_matches_still_describe_candidate(self):
        manifest = json.loads((ROOT/'docs/review/vendor-packages.json').read_text(encoding='utf-8'))
        for package in manifest['packages']:
            for name, expected in package['exact_matches'].items():
                with self.subTest(package=package['name'], file=name):
                    self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), expected)

    def test_linux_python_inventory_is_version_bound_and_has_license_evidence(self):
        manifest = json.loads((ROOT/'docs/review/python-dependencies-linux.json').read_text(encoding='utf-8'))
        current_lock = hashlib.sha256((ROOT/'apps/crm/requirements.lock').read_bytes()).hexdigest()
        self.assertRegex(manifest['requirements_lock_sha256'], r'^[a-f0-9]{64}$')
        self.assertNotEqual(manifest['requirements_lock_sha256'], current_lock)
        self.assertRegex(manifest['image_id'], r'^sha256:[a-f0-9]{64}$')
        for package in manifest['packages']:
            self.assertTrue(package['license_file_evidence'])
            for item in package['license_file_evidence']:
                self.assertNotIn('..', item['path_in_dist_info'])
                self.assertNotIn(':', item['path_in_dist_info'])
                self.assertRegex(item['sha256'], r'^[a-f0-9]{64}$')


if __name__ == '__main__':
    unittest.main()
