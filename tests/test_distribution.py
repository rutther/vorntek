import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('newcrown_configure', ROOT/'scripts'/'configure.py')
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class DistributionTests(unittest.TestCase):
    def test_maintenance_does_not_inherit_default_private_key_layers(self):
        dockerfile = (ROOT/'deploy/Dockerfile.maintenance').read_text(encoding='utf-8')
        self.assertIn('AS maintenance_filesystem', dockerfile)
        self.assertIn('rm -f /etc/ssl/private/ssl-cert-snakeoil.key', dockerfile)
        final = dockerfile.split('FROM scratch\n', 1)[1]
        self.assertIn('COPY --from=maintenance_filesystem / /', final)
        self.assertIn('/usr/lib/postgresql/18/bin', final)
        self.assertIn('USER 10001:10001', final)
        self.assertIn('ENTRYPOINT ["python3", "/tools/database_archive.py"]', final)

    def test_database_secret_trims_generated_line_ending(self):
        sql = (ROOT/'deploy/initdb/10-app-role.sql').read_text(encoding='utf-8')
        self.assertIn("btrim(pg_read_file('/run/secrets/db_password'), E' \\t\\r\\n')", sql)

    def test_website_images_seed_the_same_owned_atomic_serving_root(self):
        owner = '{"kind":"websiteServingStore","schemaVersion":1}'
        for name in ('Dockerfile.crm', 'Dockerfile.website'):
            dockerfile = (ROOT/'deploy'/name).read_text(encoding='utf-8')
            self.assertIn('COPY apps/website/ /srv/website/releases/bundled/', dockerfile)
            self.assertIn(owner, dockerfile)
            self.assertIn('ln -s releases/bundled /srv/website/current', dockerfile)
        nginx = (ROOT/'deploy/nginx.conf').read_text(encoding='utf-8')
        self.assertIn('root /srv/website/current;', nginx)
        self.assertIn('location ~ /\\.', nginx)
        self.assertNotIn('root /usr/share/nginx/html;', nginx)

    def setUp(self):
        self.compose = yaml.safe_load((ROOT/'compose.yaml').read_text(encoding='utf-8'))

    def test_only_local_frontdoor_is_published(self):
        for name, service in self.compose['services'].items():
            if name != 'website':
                self.assertNotIn('ports', service, name)
        self.assertTrue(self.compose['services']['website']['ports'][0].startswith('127.0.0.1:'))

    def test_maintenance_is_explicit_private_and_cannot_start_workers(self):
        service = self.compose['services']['maintenance']
        self.assertEqual(service['profiles'], ['maintenance'])
        self.assertEqual(service['networks'], ['private'])
        self.assertEqual(service['secrets'], ['db_password'])
        self.assertNotIn('depends_on', service)
        self.assertEqual(service['restart'], 'no')
        self.assertTrue(service['read_only'])
        self.assertFalse(service['volumes'][0]['bind']['create_host_path'])

    def test_apps_are_on_internal_network_with_sending_disabled(self):
        self.assertTrue(self.compose['networks']['private']['internal'])
        for name in ('crm', 'initialize', 'exports', 'scheduler'):
            service = self.compose['services'][name]
            self.assertEqual(service['networks'], ['private'])
            self.assertEqual(service['environment']['NEWCROWN_ALLOW_EXTERNAL_IO'], '0')
            self.assertEqual(service['environment']['NEWCROWN_RUN_SCHEDULED_TASKS'], '0')
            self.assertTrue(service['read_only'])
            self.assertNotIn('db_admin_password', service['secrets'])

    def test_file_operations_have_no_network_credentials_or_automatic_startup(self):
        for name in ('filebackup', 'filerestore'):
            service = self.compose['services'][name]
            self.assertEqual(service['profiles'], ['maintenance'])
            self.assertEqual(service['network_mode'], 'none')
            self.assertNotIn('secrets', service)
            self.assertNotIn('depends_on', service)
            self.assertEqual(service['restart'], 'no')
        self.assertIn('crm_files:/data:ro', self.compose['services']['filebackup']['volumes'])
        self.assertTrue(self.compose['services']['filerestore']['volumes'][-1]['read_only'])

    def test_readiness_and_init_failures_gate_startup(self):
        services = self.compose['services']
        self.assertEqual(services['initialize']['depends_on']['db']['condition'], 'service_healthy')
        for name in ('crm', 'exports', 'scheduler'):
            self.assertEqual(services[name]['depends_on']['initialize']['condition'], 'service_completed_successfully')
        self.assertEqual(services['website']['depends_on']['crm']['condition'], 'service_healthy')

    def test_postgres_18_data_mount_and_private_volumes(self):
        services = self.compose['services']
        self.assertIn('postgres_data:/var/lib/postgresql', services['db']['volumes'])
        self.assertIn('crm_files:/data', services['crm']['volumes'])
        self.assertIn('crm_runtime:/app/.runtime', services['exports']['volumes'])
        self.assertIn('website_runtime:/srv/website', services['crm']['volumes'])
        for name in ('initialize', 'exports', 'scheduler'):
            self.assertNotIn('website_runtime:/srv/website', services[name]['volumes'])
        self.assertEqual(
            services['website']['volumes'],
            ['crm_static:/srv/crm-static:ro', 'website_runtime:/srv/website:ro'],
        )

    def test_new_configuration_is_unique_private_and_not_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'compose.yaml').write_text('services: {}', encoding='utf-8')
            (root/'.env.example').write_text('EXAMPLE=value\n', encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                configure.configure(root)
            values = [p.read_text().strip() for p in (root/'.secrets').iterdir()]
            self.assertEqual(len(set(values)), 4)
            for value in values:
                self.assertEqual(len(value), 64)
                self.assertNotIn(value, output.getvalue())
            self.assertEqual((root/'.env').read_text(), 'EXAMPLE=value\n')
            if os.name == 'posix':
                self.assertEqual((root/'.secrets').stat().st_mode & 0o777, 0o700)
            with self.assertRaises(SystemExit):
                configure.configure(root)
            self.assertEqual(values, [p.read_text().strip() for p in (root/'.secrets').iterdir()])

    def test_bilingual_readmes_have_identical_quickstart(self):
        english = (ROOT/'README.md').read_text(encoding='utf-8').split('```sh')[1].split('```')[0]
        chinese = (ROOT/'README.zh-CN.md').read_text(encoding='utf-8').split('```sh')[1].split('```')[0]
        self.assertEqual(english, chinese)


if __name__ == '__main__':
    unittest.main()
