import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.test_release_upgrade import validate_migration_prefix, preserved_original_rows


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
