import importlib.util
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CIContractTests(unittest.TestCase):
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
        commands = '\n'.join(step.get('run', '') for job in workflow['jobs'].values() for step in job['steps'])
        for required in ('scripts/run_application_tests.py', 'scripts/test_postgres_install.py', '--http',
                         'docker compose build', 'scripts/check_local_stack.py', 'maintenance --help'):
            self.assertIn(required, commands)

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
