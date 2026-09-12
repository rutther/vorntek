from __future__ import annotations

import hashlib
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from leads.management.commands.apply_whatsapp_crm_upgrade import (
    CONFIRM_TOKEN,
    MIGRATIONS,
    advisory_lock_id,
    verified_sql,
    without_embedded_transaction,
)


class WhatsAppUpgradeCommandTests(SimpleTestCase):
    def test_release_migration_hashes_match_pinned_contract(self):
        for filename, expected_sha256 in MIGRATIONS:
            self.assertTrue(verified_sql(filename, expected_sha256).strip())

    def test_hash_mismatch_fails_closed(self):
        with patch('pathlib.Path.read_bytes', return_value=b'SELECT 1;'):
            with self.assertRaises(CommandError):
                verified_sql('0024_content_access_grants.sql', '0' * 64)

    def test_hash_accepts_equivalent_crlf_release_payload(self):
        sql = 'SELECT 1;\nSELECT 2;\n'
        expected = hashlib.sha256(sql.encode('utf-8')).hexdigest()
        with patch(
            'pathlib.Path.read_bytes',
            return_value=sql.replace('\n', '\r\n').encode('utf-8'),
        ):
            self.assertEqual(
                verified_sql('0024_content_access_grants.sql', expected),
                sql,
            )

    def test_non_utf8_sql_fails_closed(self):
        with patch('pathlib.Path.read_bytes', return_value=b'\xff\xfe'):
            with self.assertRaisesMessage(CommandError, 'UTF-8'):
                verified_sql('0024_content_access_grants.sql', '0' * 64)

    def test_embedded_transaction_is_removed_only_from_0026(self):
        sql = 'BEGIN;\nSELECT 1;\nCOMMIT;\n'
        self.assertEqual(
            without_embedded_transaction(sql, filename='0026_whatsapp_crm_core.sql'),
            'SELECT 1;',
        )
        self.assertEqual(
            without_embedded_transaction(' SELECT 2; ', filename='0025_task_activity_integrity.sql'),
            'SELECT 2;',
        )

    def test_malformed_0026_transaction_boundary_fails_closed(self):
        with self.assertRaises(CommandError):
            without_embedded_transaction(
                'SELECT 1;\nCOMMIT;', filename='0026_whatsapp_crm_core.sql'
            )

    def test_apply_refuses_non_postgresql_even_with_confirmation(self):
        with patch(
            'leads.management.commands.apply_whatsapp_crm_upgrade.connection.vendor',
            'sqlite',
        ):
            with self.assertRaisesMessage(CommandError, '只允许在 PostgreSQL'):
                call_command(
                    'apply_whatsapp_crm_upgrade',
                    apply=True,
                    confirm=CONFIRM_TOKEN,
                )

    def test_advisory_lock_id_is_stable_signed_bigint(self):
        lock_id = advisory_lock_id()
        self.assertGreaterEqual(lock_id, -(2**63))
        self.assertLessEqual(lock_id, 2**63 - 1)
        self.assertEqual(lock_id, advisory_lock_id())
