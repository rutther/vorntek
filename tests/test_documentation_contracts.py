from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUIDES = {
    'docs/CONFIGURATION.md': (
        'python3 scripts/configure.py',
        'docker compose config --quiet',
        'release_preflight --strict',
        'SITEOS_EMAIL_HOST_PASSWORD_FILE',
    ),
    'docs/ADMINISTRATION.md': (
        'docker compose up -d',
        'createsuperuser',
        'run_scheduled_tasks --healthcheck',
        'docker compose stop website crm exports scheduler',
    ),
    'docs/DEVELOPMENT.md': (
        'apps/crm/requirements.lock',
        'requirements-dev.txt',
        'python3 -m unittest discover -s tests -v',
        'node --test',
        'scripts/run_application_tests.py',
        'pip_audit',
        'scripts/test_postgres_install.py',
        'scripts/run_browser_acceptance_fixture.py',
        'scripts/release_manifest.py --require-clean',
    ),
    'docs/TROUBLESHOOTING.md': (
        'docker compose config --quiet',
        'docker compose ps -a',
        'docker compose logs',
        '/healthz/',
        'release_preflight --strict',
        'client_max_body_size',
        'still require Linux container validation',
    ),
}


class DocumentationContractTests(unittest.TestCase):
    def test_readmes_link_every_operator_guide(self):
        for readme_name in ('README.md', 'README.zh-CN.md'):
            text = (ROOT/readme_name).read_text(encoding='utf-8')
            for guide in GUIDES:
                with self.subTest(readme=readme_name, guide=guide):
                    self.assertIn(f']({guide})', text)

    def test_operator_guides_are_bilingual_and_command_backed(self):
        for relative, required in GUIDES.items():
            path = ROOT/relative
            with self.subTest(guide=relative):
                self.assertTrue(path.is_file())
                text = path.read_text(encoding='utf-8')
                self.assertRegex(text.splitlines()[0], r'^# .+ / .+$')
                self.assertRegex(text, r'[\u4e00-\u9fff]')
                for contract in required:
                    self.assertIn(contract, text)

    def test_referenced_management_commands_exist(self):
        commands = ROOT/'apps'/'crm'/'console'/'management'/'commands'
        for name in ('release_preflight', 'run_scheduled_tasks'):
            with self.subTest(command=name):
                self.assertTrue((commands/f'{name}.py').is_file())

    def test_configuration_inputs_exist(self):
        for relative in ('.env.example', 'scripts/configure.py', 'compose.yaml'):
            with self.subTest(path=relative):
                self.assertTrue((ROOT/relative).is_file())

    def test_local_links_resolve(self):
        link_pattern = re.compile(r'(?<!!)\[[^\]]+\]\(([^)]+)\)')
        for relative in ('README.md', 'README.zh-CN.md', *GUIDES):
            source = ROOT/relative
            text = source.read_text(encoding='utf-8')
            for raw_target in link_pattern.findall(text):
                target = raw_target.strip().split('#', 1)[0]
                if not target or re.match(r'^[a-z][a-z0-9+.-]*:', target, re.I):
                    continue
                resolved = (source.parent/target).resolve()
                with self.subTest(source=relative, target=raw_target):
                    self.assertTrue(resolved.exists(), f'broken local link: {raw_target}')


if __name__ == '__main__':
    unittest.main()
