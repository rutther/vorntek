import importlib.util
from pathlib import Path
import re
import shlex
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CIContractTests(unittest.TestCase):
    def test_standalone_postgres_uses_its_own_posix_socket_directory(self):
        runner = load_script('test_postgres_install')
        directory = Path('/tmp/vorntek test/socket')
        options = runner.postgres_start_options(directory, 18543, platform='posix')
        self.assertEqual(shlex.split(options), ['-h', '127.0.0.1', '-p', '18543', '-k', str(directory)])

    def test_standalone_postgres_keeps_windows_tcp_behavior(self):
        runner = load_script('test_postgres_install')
        self.assertEqual(runner.postgres_start_options(Path('C:/test'), 18543, platform='nt'),
                         '-h 127.0.0.1 -p 18543')

    def test_postgres_upgrade_span_tracks_the_current_chain(self):
        runner = load_script('test_postgres_install')
        with tempfile.TemporaryDirectory() as directory:
            migration_dir = Path(directory)
            for name in ('0001_one.sql', '0002_two.sql', '0003_three.sql'):
                (migration_dir / name).touch()
            (migration_dir / 'README.md').touch()
            self.assertEqual(runner.pending_migration_count(migration_dir, 1), 2)
            with self.assertRaisesRegex(RuntimeError, 'no longer extends'):
                runner.pending_migration_count(migration_dir, 3)

    def test_read_only_ephemeral_workflows_have_pinned_actions_and_no_publish_steps(self):
        text = (ROOT/'.github/workflows/ci.yml').read_text(encoding='utf-8')
        workflow = yaml.safe_load(text)
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertEqual(set(workflow['on']), {'push', 'pull_request', 'workflow_dispatch'})
        self.assertNotIn('${{ secrets.', text)
        for job in workflow['jobs'].values():
            self.assertEqual(job['runs-on'], 'ubuntu-24.04')
            self.assertLessEqual(job['timeout-minutes'], 30)
            for step in job['steps']:
                if 'uses' in step:
                    self.assertRegex(step['uses'], r'^actions/(checkout|setup-python|setup-node)@[a-f0-9]{40}$')
                    if step['uses'].startswith('actions/checkout@'):
                        self.assertFalse(step['with']['persist-credentials'])
                command = step.get('run', '')
                self.assertNotRegex(command, r'(?m)\b(?:git push|docker push|docker login|ssh|scp)\b')
                self.assertNotIn('down -v', command)

    def test_ci_runs_full_suite_postgres_http_and_container_smoke(self):
        workflow = yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text(encoding='utf-8'))
        application_checkout = next(
            step for step in workflow['jobs']['application']['steps']
            if step.get('uses', '').startswith('actions/checkout@')
        )
        self.assertEqual(application_checkout['with']['fetch-depth'], 0)
        commands = '\n'.join(step.get('run', '') for job in workflow['jobs'].values() for step in job['steps'])
        for required in ('scripts/run_application_tests.py', 'scripts/test_postgres_install.py', '--http',
                         'docker compose build', 'scripts/check_local_stack.py',
                         'python -m pip_audit -r apps/crm/requirements.lock',
                         'scripts/audit_release_history.py --base v0.1.0-rc.1 --require-clean',
                         'scripts/release_manifest.py --require-clean',
                         'release_preflight --strict', 'maintenance --help',
                         'run_synthetic_website_deployment_acceptance',
                         '--require-bundled-baseline',
                         'docker compose restart crm website',
                         "docker inspect --format '{{json .State.Health}}'",
                         'docker compose logs --no-color --tail 200',
                         '/articles/acceptance-guide/'):
            self.assertIn(required, commands)
        self.assertNotIn('.Config.Env', commands)
        self.assertIn('pip-audit==2.10.1', (ROOT/'requirements-dev.txt').read_text())
        postgres_runner = (ROOT/'scripts'/'test_postgres_install.py').read_text(
            encoding='utf-8'
        )
        for required in (
            'file_archive.py',
            'reconcile_website_serving_cache',
            'restored_database_and_runtime_rebuild_serving_cache_without_database_writes',
        ):
            self.assertIn(required, postgres_runner)

    def test_socket_guard_recognizes_only_loopback(self):
        runner = load_script('run_application_tests')
        for address in [('127.0.0.1', 80), ('::1', 80), ('localhost', 80)]:
            self.assertTrue(runner.allowed(address))
        for address in [('192.0.2.1', 80), ('example.invalid', 443), '/var/run/socket', ()]:
            self.assertFalse(runner.allowed(address))

    def test_smoke_checker_refuses_external_urls_credentials_and_redirects(self):
        checker = load_script('check_local_stack')
        checker.require_local('http://127.0.0.1:8088/')
        for url in ('https://example.invalid/', 'http://192.0.2.1/', 'http://user:secret@localhost/'):
            with self.assertRaises(ValueError):
                checker.require_local(url)
        with self.assertRaises(ValueError):
            checker.LocalRedirects().redirect_request(None, None, 302, '', {}, 'https://example.invalid/')


if __name__ == '__main__':
    unittest.main()
