from __future__ import annotations

import os
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class DatabaseMustNotBeAccessed:
    @property
    def vendor(self):
        raise AssertionError('database accessed before live-setting refusal')


class SyntheticWebsiteDeploymentAcceptanceGuardTests(SimpleTestCase):
    def test_explicit_flag_and_environment_acknowledgement_are_both_required(self):
        with patch.dict(os.environ, {'NEWCROWN_SYNTHETIC_ACCEPTANCE': '0'}):
            with self.assertRaisesRegex(CommandError, 'requires --synthetic-only'):
                call_command(
                    'run_synthetic_website_deployment_acceptance',
                    expect_database='synthetic_test',
                )
            with self.assertRaisesRegex(CommandError, 'NEWCROWN_SYNTHETIC_ACCEPTANCE=1'):
                call_command(
                    'run_synthetic_website_deployment_acceptance',
                    synthetic_only=True,
                    expect_database='synthetic_test',
                )

    def test_live_or_scheduled_execution_is_refused_before_database_access(self):
        settings = (
            (True, False, False),
            (False, True, False),
            (False, False, True),
        )
        with patch.dict(os.environ, {'NEWCROWN_SYNTHETIC_ACCEPTANCE': '1'}):
            for external_io, whatsapp, scheduled in settings:
                with self.subTest(
                    external_io=external_io,
                    whatsapp=whatsapp,
                    scheduled=scheduled,
                ), override_settings(
                    NEWCROWN_ALLOW_EXTERNAL_IO=external_io,
                    SITEOS_WHATSAPP_ALLOW_LIVE_SEND=whatsapp,
                    NEWCROWN_RUN_SCHEDULED_TASKS=scheduled,
                ), patch(
                    'console.management.commands.'
                    'run_synthetic_website_deployment_acceptance.connection',
                    new=DatabaseMustNotBeAccessed(),
                ):
                    with self.assertRaisesRegex(CommandError, 'must remain disabled'):
                        call_command(
                            'run_synthetic_website_deployment_acceptance',
                            synthetic_only=True,
                            expect_database='synthetic_test',
                        )

    @override_settings(
        NEWCROWN_ALLOW_EXTERNAL_IO=False,
        SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False,
        NEWCROWN_RUN_SCHEDULED_TASKS=False,
    )
    def test_non_postgresql_database_is_refused_before_queries_or_files(self):
        command = (
            'console.management.commands.'
            'run_synthetic_website_deployment_acceptance.connection'
        )
        with patch.dict(os.environ, {'NEWCROWN_SYNTHETIC_ACCEPTANCE': '1'}), patch(
            command
        ) as database:
            database.vendor = 'sqlite'
            with self.assertRaisesRegex(CommandError, 'requires PostgreSQL'):
                call_command(
                    'run_synthetic_website_deployment_acceptance',
                    synthetic_only=True,
                    expect_database='synthetic_test',
                )
            database.cursor.assert_not_called()
