import hashlib
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

from scripts.test_release_upgrade import validate_baseline_checkout, validate_migration_prefix, preserved_original_rows


class ReleaseUpgradeInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.old=self.root/'old';self.new=self.root/'new'
        for directory in (self.old,self.new):
            directory.mkdir()
            (directory/'0001_base.sql').write_text('CREATE TABLE fixture(id int);\n')
        (self.new/'0002_next.sql').write_text('ALTER TABLE fixture ADD note text;\n')
        self.manifests()

    def manifests(self):
        for directory in (self.old,self.new):
            (directory/'SHA256SUMS').write_text(''.join(
                hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
                for p in sorted(directory.glob('*.sql'))))

    def initialize_git_checkout(self, root):
        subprocess.run(['git','init','-q'],cwd=root,check=True)
        subprocess.run(['git','add','.'],cwd=root,check=True)
        subprocess.run(
            ['git','-c','user.name=Release Test','-c','user.email=release@example.invalid',
             'commit','-qm','fixture'],cwd=root,check=True)
        return subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()

    def test_real_matching_prefix_reports_exact_pending_count(self):
        self.assertEqual(validate_migration_prefix(self.old,self.new),(1,1))

    def test_rewritten_old_sql_refuses_even_if_both_manifests_updated(self):
        (self.new/'0001_base.sql').write_text('CREATE TABLE different(id int);\n')
        self.manifests()
        with self.assertRaisesRegex(ValueError,'prefix'):
            validate_migration_prefix(self.old,self.new)

    def test_missing_manifest_entry_or_tampered_file_refuses(self):
        (self.old/'0001_base.sql').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'checksum'):
            validate_migration_prefix(self.old,self.new)
        self.manifests()
        (self.new/'SHA256SUMS').write_text('')
        with self.assertRaisesRegex(ValueError,'manifest'):
            validate_migration_prefix(self.old,self.new)

    def test_no_upgrade_or_downgrade_is_not_accepted_as_upgrade(self):
        with self.assertRaisesRegex(ValueError,'extend'):
            validate_migration_prefix(self.new,self.old)

    def test_dirty_candidate_checkout_is_refused_before_upgrade(self):
        old_migrations=self.old/'apps/crm/db/migrations'
        new_migrations=self.new/'apps/crm/db/migrations'
        old_migrations.mkdir(parents=True)
        new_migrations.mkdir(parents=True)
        for source,target in ((self.old,old_migrations),(self.new,new_migrations)):
            for path in list(source.glob('*.sql'))+[source/'SHA256SUMS']:
                path.replace(target/path.name)
        baseline_revision=self.initialize_git_checkout(self.old)
        candidate_revision=self.initialize_git_checkout(self.new)
        with patch('scripts.test_release_upgrade.PUBLIC_BASELINE',baseline_revision):
            self.assertEqual(
                validate_baseline_checkout(self.old,self.new),
                (1,1,candidate_revision))
            (self.new/'uncommitted.txt').write_text('not part of the candidate commit\n')
            with self.assertRaisesRegex(ValueError,'Candidate checkout is dirty'):
                validate_baseline_checkout(self.old,self.new)

    def test_new_framework_permissions_may_append_but_old_rows_cannot_change(self):
        before={'auth_permission':['old'],'django_content_type':['type'],'lead_submission':['lead']}
        after={'auth_permission':['old','new'],'django_content_type':['type','newtype'],'lead_submission':['lead']}
        self.assertTrue(preserved_original_rows(before,after))
        for table in before:
            broken={**after,table:['changed']}
            self.assertFalse(preserved_original_rows(before,broken),table)

    def test_business_rows_cannot_append_and_duplicate_metadata_cannot_disappear(self):
        self.assertFalse(preserved_original_rows({'lead_submission':['old']},{'lead_submission':['old','new']}))
        self.assertFalse(preserved_original_rows({'auth_permission':['old','old']},{'auth_permission':['old','new']}))
