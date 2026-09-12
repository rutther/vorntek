import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from console.demo_data import customer_fixtures
from scripts.prepare_candidate_demo import main as retired_main


class DemoDataContracts(SimpleTestCase):
    def test_plan_is_deterministic_and_never_queries_or_writes_database(self):
        output = StringIO()
        call_command('seed_vorntek_demo', stdout=output)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary['result'], 'PLAN ONLY')
        self.assertEqual(summary['companies'], 200)
        self.assertEqual(summary['business_lines'], 7)
        self.assertEqual(summary['states'], {'available': 150, 'review': 40, 'archived': 10})
        self.assertEqual(set(summary['sources'].values()), {40})
        self.assertEqual(customer_fixtures(), customer_fixtures())

    def test_all_contacts_and_websites_are_reserved_synthetic_values(self):
        rows = customer_fixtures()
        self.assertEqual(len({row['name'] for row in rows}), 200)
        self.assertEqual(len({row['email'] for row in rows}), 200)
        for row in rows:
            self.assertIn('Synthetic', row['name'])
            self.assertTrue(row['website'].endswith('.example.invalid'))
            self.assertTrue(row['email'].endswith('@example.invalid'))
            self.assertNotIn('phone', row)
            self.assertNotIn('password', row)

    def test_apply_requires_explicit_synthetic_target_acknowledgement(self):
        with self.assertRaisesRegex(CommandError, 'synthetic-only'):
            call_command('seed_vorntek_demo', apply=True)

    def test_live_external_delivery_refused_before_database_access(self):
        for io, whatsapp in [(True, False), (False, True), (True, True)]:
            with override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=io, SITEOS_WHATSAPP_ALLOW_LIVE_SEND=whatsapp):
                with self.assertRaisesRegex(CommandError, 'remain disabled'):
                    call_command('seed_vorntek_demo', apply=True, synthetic_only=True)

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False, SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False)
    def test_non_postgres_apply_refused(self):
        with patch('console.management.commands.seed_vorntek_demo.connection') as connection:
            connection.vendor = 'sqlite'
            with self.assertRaisesRegex(CommandError, 'PostgreSQL'):
                call_command('seed_vorntek_demo', apply=True, synthetic_only=True)
            connection.cursor.assert_not_called()

    def test_legacy_cli_cannot_reset_any_schema(self):
        with self.assertRaisesRegex(SystemExit, 'retired'):
            retired_main()
