from __future__ import annotations

import io
import json

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from siteos_admin.preflight import LEDGER, migration_ledger_report
from siteos_admin.schema_install import Migration


class ReleasePreflightTests(TestCase):
    def test_valid_ledger_reports_only_pending_suffix(self):
        chain = [
            Migration('0001_one.sql', 'a' * 64, ''),
            Migration('0002_two.sql', 'b' * 64, ''),
        ]

        report = migration_ledger_report(
            chain,
            tables={LEDGER},
            recorded={'0001_one.sql': 'a' * 64},
        )

        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['pending'], ['0002_two.sql'])
        self.assertEqual(report['recorded_tip'], '0001_one.sql')

    def test_existing_database_without_ledger_is_refused(self):
        report = migration_ledger_report(
            [Migration('0001_one.sql', 'a' * 64, '')],
            tables={'crm_company'},
            recorded={},
        )

        self.assertEqual(report['status'], 'invalid')
        self.assertEqual(report['error'], 'existing_database_without_installation_ledger')

    @override_settings(
        NEWCROWN_ALLOW_EXTERNAL_IO=False,
        NEWCROWN_RUN_SCHEDULED_TASKS=False,
        SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False,
    )
    def test_json_preflight_is_read_only_and_identifies_sqlite_as_unsupported(self):
        output = io.StringIO()

        call_command('release_preflight', '--json', stdout=output)

        report = json.loads(output.getvalue())
        self.assertEqual(report['service'], 'vorntek-crm')
        self.assertEqual(report['database']['vendor'], 'sqlite')
        self.assertFalse(report['ready_for_upgrade'])
        self.assertTrue(report['outbound']['paused_for_upgrade'])

    @override_settings(
        NEWCROWN_ALLOW_EXTERNAL_IO=True,
        NEWCROWN_RUN_SCHEDULED_TASKS=False,
        SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False,
    )
    def test_preflight_reports_enabled_external_io_as_not_paused(self):
        output = io.StringIO()

        call_command('release_preflight', '--json', stdout=output)

        report = json.loads(output.getvalue())
        self.assertTrue(report['outbound']['external_io'])
        self.assertFalse(report['outbound']['paused_for_upgrade'])

    def test_strict_preflight_exits_nonzero_without_postgresql(self):
        with self.assertRaisesMessage(CommandError, 'no changes were made'):
            call_command('release_preflight', '--strict', stdout=io.StringIO())
