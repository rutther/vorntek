import hashlib
import json
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

    def allowlist(self, entries):
        path = self.root/'reviewed-assets.json'
        path.write_text(json.dumps({'format':'vorntek-reviewed-assets-v1','assets':entries}))
        return path

    def asset_entry(self, path, content):
        return {'path':path,'sha256':hashlib.sha256(content.encode()).hexdigest(),
                'bytes':len(content.encode()),'source':'authored synthetic fixture',
                'license_or_provenance':'MIT fixture','reason':'test large-resource policy'}

    def test_reviewed_asset_requires_exact_path_bytes_and_digest(self):
        content = 'safe synthetic resource\n'*10
        self.commit_file('apps/asset.txt',content)
        entry = self.asset_entry('apps/asset.txt',content)
        manifest = self.allowlist([entry])
        report = audit_range(self.root,self.base,max_blob_bytes=64,asset_allowlist=manifest)
        self.assertEqual(report['finding_count'],0)
        for key,value in [('sha256','0'*64),('bytes',len(content)+1),('path','apps/other.txt')]:
            with self.subTest(key=key):
                manifest = self.allowlist([{**entry,key:value}])
                report = audit_range(self.root,self.base,max_blob_bytes=64,asset_allowlist=manifest)
                self.assertIn('oversized blob',{r['kind'] for r in report['findings']})
                self.assertIn('unused asset approval',{r['kind'] for r in report['findings']})

    def test_approved_content_under_unreviewed_path_is_rejected(self):
        content = 'safe shared fixture\n'*10
        self.commit_file('apps/approved.txt',content)
        self.commit_file('apps/unreviewed.txt',content)
        manifest = self.allowlist([self.asset_entry('apps/approved.txt',content)])
        report = audit_range(self.root,self.base,max_blob_bytes=64,asset_allowlist=manifest)
        self.assertEqual([(r['kind'],r['path']) for r in report['findings']],
                         [('oversized blob','apps/unreviewed.txt')])

    def test_approved_large_text_still_scans_secrets_and_deleted_history(self):
        content = 'AKIA'+'1234567890ABCDEF'+'\n'+'x'*100
        self.commit_file('apps/approved.txt',content)
        manifest = self.allowlist([self.asset_entry('apps/approved.txt',content)])
        (self.root/'apps/approved.txt').unlink()
        self.git('add','-u'); self.git('commit','-m','remove fixture')
        report = audit_range(self.root,self.base,max_blob_bytes=64,asset_allowlist=manifest)
        self.assertIn('AWS access key',{r['kind'] for r in report['findings']})
        self.assertNotIn('oversized blob',{r['kind'] for r in report['findings']})

    def test_asset_approval_already_in_base_is_not_stale(self):
        content = 'safe base asset\n'*10
        self.commit_file('apps/approved.txt',content)
        base = self.git('rev-parse','HEAD').strip()
        self.commit_file('apps/new.txt','small')
        manifest = self.allowlist([self.asset_entry('apps/approved.txt',content)])
        self.assertEqual(audit_range(self.root,base,max_blob_bytes=64,
                         asset_allowlist=manifest)['finding_count'],0)

    def test_invalid_approval_schema_refuses(self):
        for entry in [self.asset_entry('../escape','safe'),
                      {**self.asset_entry('apps/x','safe'),'reason':''}]:
            with self.subTest(entry=entry),self.assertRaises(ValueError):
                audit_range(self.root,self.base,asset_allowlist=self.allowlist([entry]))


if __name__ == '__main__':
    unittest.main()
