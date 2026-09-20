import subprocess
import tempfile
from pathlib import Path
import unittest

from scripts.audit_release_history import audit_range


class ReleaseHistoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git('init', '--initial-branch=main')
        self.git('config', 'user.name', 'Synthetic Test')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        (self.root/'README.md').write_text('synthetic baseline\n', encoding='utf-8')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'baseline')
        self.base = self.git('rev-parse', 'HEAD').strip()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ['git', *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout

    def commit_file(self, name, content, message='candidate'):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        self.git('add', name)
        self.git('commit', '-m', message)

    def test_clean_candidate_range_reports_exact_objects(self):
        self.commit_file('apps/example.py', 'VALUE = "synthetic"\n')
        report = audit_range(self.root, self.base, require_clean=True)
        self.assertEqual(report['commit_count'], 1)
        self.assertEqual(report['finding_count'], 0)
        self.assertGreaterEqual(report['unique_blob_count'], 1)
        self.assertEqual(report['largest_blob_path'], 'apps/example.py')

    def test_removed_secret_blob_is_still_rejected(self):
        shaped_secret = 'AKIA' + '1234567890ABCDEF'
        self.commit_file('notes.txt', shaped_secret + '\n', 'introduce fixture secret')
        (self.root/'notes.txt').unlink()
        self.git('add', '-u')
        self.git('commit', '-m', 'remove fixture secret')
        report = audit_range(self.root, self.base)
        self.assertIn('AWS access key', {item['kind'] for item in report['findings']})
        self.assertNotIn(shaped_secret, str(report))

    def test_blocked_paths_runtime_identifiers_and_large_blobs_fail(self):
        self.commit_file('.env', 'SAFE=synthetic\n', 'blocked environment path')
        shared = '# production host: ' + 'filline' + '.com\n' + ('x' * 128)
        self.commit_file('tests/shared.txt', shared, 'allowed path for shared blob')
        self.commit_file(
            'scripts/shared.py', shared, 'same blob under runtime path',
        )
        report = audit_range(self.root, self.base, max_blob_bytes=64)
        kinds = {item['kind'] for item in report['findings']}
        self.assertIn('blocked environment file', kinds)
        self.assertIn('production domain', kinds)
        self.assertIn('oversized blob', kinds)
        self.assertIn(
            'scripts/shared.py',
            {item['path'] for item in report['findings'] if item['kind'] == 'production domain'},
        )


if __name__ == '__main__':
    unittest.main()
