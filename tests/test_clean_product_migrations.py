"""Upgrade compatibility for the complete guided-loop and recovery model set.

The frozen prefix digest was measured from the original 6d3eefe7 product.
PostgreSQL installation/rollback is verified separately against a real database.
"""
import hashlib
from pathlib import Path
import sys
import unittest

from django.core.management.base import CommandError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/crm"))
from siteos_admin.schema_install import load_chain, pending_migrations


APPENDED_SUFFIXES = (
    "marketing_guided_loop_orchestration",
    "marketing_pixel_runtime",
    "runtime_observation",
    "lead_trash",
    "notification_attempts",
    "notification_message_id",
    "marketing_delivery_attempts",
    "google_delivery_results",
    "default_site_pixel_identity",
    "crm_attachment_cleanup",
)


class CleanProductMigrationTests(unittest.TestCase):
    def setUp(self):
        self.chain = load_chain(ROOT / "apps/crm/db/migrations")

    def test_existing_installation_gets_the_complete_recovery_and_loop_suffix(self):
        recorded = {item.name: item.checksum for item in self.chain[:32]}
        pending = pending_migrations(self.chain, recorded)
        self.assertEqual(
            [item.name for item in pending],
            [f"{index:04d}_{name}.sql" for index, name in enumerate(APPENDED_SUFFIXES, 33)],
        )
        self.assertEqual(len(pending_migrations(self.chain, {})), 42)

    def test_deployed_legacy_prefix_keeps_its_original_checksums(self):
        prefix_digest = hashlib.sha256(
            b"".join(bytes.fromhex(item.checksum) for item in self.chain[:32])
        ).hexdigest()
        self.assertEqual(prefix_digest, "c373d38f64d0f440b411b68dbdaa7a3fb9b9629075b9d9ffef4b864a2c4d70f6")

    def test_actual_twenty_seven_migration_server_gets_the_intermediate_prefix_too(self):
        recorded = {item.name: item.checksum for item in self.chain[:27]}
        pending = pending_migrations(self.chain, recorded)
        self.assertEqual(len(pending), 15)
        self.assertEqual(pending[0].name, "0028_customer_pool_standard21.sql")
        self.assertEqual(pending[-1].name, "0042_crm_attachment_cleanup.sql")

    def test_complete_installation_has_no_pending_replay(self):
        recorded = {item.name: item.checksum for item in self.chain}
        self.assertEqual(pending_migrations(self.chain, recorded), [])

    def test_hong_kong_fork_cannot_be_mistaken_for_this_upgrade_chain(self):
        recorded = {item.name: item.checksum for item in self.chain[:28]}
        recorded["0029_marketing_guided_loop_orchestration.sql"] = "0" * 64
        with self.assertRaises(CommandError):
            pending_migrations(self.chain, recorded)


if __name__ == "__main__":
    unittest.main()
