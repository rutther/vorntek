from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from leads.management.commands.apply_customer_pool_upgrade import (
    MIGRATION,
    without_embedded_transaction,
)
from leads.management.commands.apply_whatsapp_crm_upgrade import verified_sql


class CustomerPoolUpgradeCommandTests(SimpleTestCase):
    def test_pinned_migration_hash_is_accepted(self):
        filename, expected = MIGRATION
        sql = verified_sql(filename, expected)
        self.assertIn('CREATE TABLE IF NOT EXISTS crm_company_pool_state', sql)

    def test_hash_mismatch_fails_closed(self):
        with self.assertRaises(CommandError):
            verified_sql(MIGRATION[0], '0' * 64)

    def test_embedded_transaction_is_removed_for_django_atomic_boundary(self):
        self.assertEqual(without_embedded_transaction('BEGIN;\nSELECT 1;\nCOMMIT;'), 'SELECT 1;')
        with self.assertRaises(CommandError):
            without_embedded_transaction('SELECT 1;')

    def test_missing_migration_file_fails_closed(self):
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            with self.assertRaises(CommandError):
                verified_sql(MIGRATION[0], MIGRATION[1])
