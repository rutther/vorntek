import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import package_release, release_manifest


class SourcePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.repo=self.root/'repo';self.repo.mkdir()
        self.git('init','--initial-branch=main')
        self.git('config','core.autocrlf','false')
        self.git('config','user.name','Synthetic');self.git('config','user.email','test@example.invalid')
        inputs={'VERSION':'0.2.0-rc.1\n','compose.yaml':'services: {}\n',
                'apps/crm/requirements.lock':'# synthetic\n','apps/website/index.html':'<h1>Synthetic</h1>',
                'apps/crm/db/migrations/0001_base.sql':'SELECT 1;\n','.gitignore':'.secrets/\n.runtime/\n'}
        checksum=hashlib.sha256(inputs['apps/crm/db/migrations/0001_base.sql'].encode()).hexdigest()
        inputs['apps/crm/db/migrations/SHA256SUMS']=checksum+'  0001_base.sql\n'
        for name,content in inputs.items():
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8',newline='\n')
        self.git('add','.');self.git('commit','-m','Synthetic source')
        self.sha=self.git('rev-parse','HEAD').strip()
        scope=patch.object(release_manifest,'ROOT',self.repo);scope.start();self.addCleanup(scope.stop)

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.repo,text=True)

    def test_archives_match_exact_commit_and_do_not_include_ignored_secrets(self):
        (self.repo/'.secrets').mkdir();(self.repo/'.secrets'/'fixture').write_text('synthetic private')
        result=package_release.package_release(self.repo,'HEAD',self.root/'out')
        prefix='vorntek-0.2.0-rc.1/'
        with zipfile.ZipFile(self.root/'out'/result['zip']) as archive:
            self.assertEqual(archive.read(prefix+'apps/website/index.html'),b'<h1>Synthetic</h1>')
            self.assertFalse(any('.secrets' in name or '/.git/' in name for name in archive.namelist()))
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress((self.root/'out'/result['tar_gz']).read_bytes()))) as archive:
            self.assertEqual(archive.extractfile(prefix+'VERSION').read(),b'0.2.0-rc.1\n')
        manifest=json.loads((self.root/'out'/'release-manifest.json').read_text())
        self.assertEqual(manifest['git_commit'],self.sha)
        for line in (self.root/'out'/'SHA256SUMS').read_text().splitlines():
            digest,name=line.split()
            self.assertEqual(hashlib.sha256((self.root/'out'/name).read_bytes()).hexdigest(),digest)

    def test_two_packaging_runs_are_byte_identical(self):
        package_release.package_release(self.repo,'HEAD',self.root/'one')
        package_release.package_release(self.repo,'HEAD',self.root/'two')
        self.assertEqual({p.name:p.read_bytes() for p in (self.root/'one').iterdir()},
                         {p.name:p.read_bytes() for p in (self.root/'two').iterdir()})

    def test_dirty_source_wrong_ref_and_output_reuse_refuse(self):
        package_release.package_release(self.repo,'HEAD',self.root/'out')
        with self.assertRaises(FileExistsError):
            package_release.package_release(self.repo,'HEAD',self.root/'out')
        (self.repo/'VERSION').write_text('0.2.0-rc.2\n')
        with self.assertRaises(release_manifest.Refused):
            package_release.package_release(self.repo,'HEAD',self.root/'dirty')
        self.git('add','VERSION');self.git('commit','-m','next')
        with self.assertRaisesRegex(ValueError,'HEAD'):
            package_release.package_release(self.repo,self.sha,self.root/'old')
