import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / 'apps' / 'crm' / 'db' / 'migrations'


class Standard21MigrationContracts(unittest.TestCase):
    def test_reference_hardening_is_append_only(self):
        base = (MIGRATIONS / '0028_customer_pool_standard21.sql').read_text(encoding='utf-8')
        hardening = (
            MIGRATIONS / '0029_customer_pool_row_reference_integrity.sql'
        ).read_text(encoding='utf-8')

        self.assertIn('CREATE TABLE IF NOT EXISTS crm_customer_pool_row', base)
        self.assertIn('crm_customer_pool_row_import_pair_check', hardening)
        self.assertIn('VALIDATE CONSTRAINT crm_customer_pool_row_import_pair_check', hardening)
        self.assertIn('pool row import batch and row must be set together', hardening)

    def test_checksum_manifest_covers_both_migrations(self):
        manifest = {}
        for line in (MIGRATIONS / 'SHA256SUMS').read_text(encoding='ascii').splitlines():
            digest, name = line.split()
            manifest[name] = digest
        for name in (
            '0028_customer_pool_standard21.sql',
            '0029_customer_pool_row_reference_integrity.sql',
        ):
            expected = hashlib.sha256((MIGRATIONS / name).read_bytes()).hexdigest()
            self.assertEqual(manifest.get(name), expected)


if __name__ == '__main__':
    unittest.main()
