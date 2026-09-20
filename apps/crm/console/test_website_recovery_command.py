from __future__ import annotations

from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class DatabaseMustNotBeAccessed:
    @property
    def vendor(self):
        raise AssertionError('database accessed before apply acknowledgement refusal')


class WebsiteRecoveryCommandGuardTests(SimpleTestCase):
    def test_apply_requires_all_stale_state_acknowledgements_before_database_access(self):
        target = (
            'console.management.commands.reconcile_website_serving_cache.connection'
        )
        with patch(target, new=DatabaseMustNotBeAccessed()):
            with self.assertRaisesRegex(CommandError, 'Apply requires'):
                call_command(
                    'reconcile_website_serving_cache',
                    site_code='siteos_demo',
                    expect_database='synthetic_restore',
                    apply=True,
                )

    @override_settings(
        NEWCROWN_ALLOW_EXTERNAL_IO=True,
        SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False,
        NEWCROWN_RUN_SCHEDULED_TASKS=False,
    )
    def test_apply_refuses_live_external_io_before_database_access(self):
        target = (
            'console.management.commands.reconcile_website_serving_cache.connection'
        )
        with patch(target, new=DatabaseMustNotBeAccessed()):
            with self.assertRaisesRegex(CommandError, 'must remain disabled'):
                call_command(
                    'reconcile_website_serving_cache',
                    site_code='siteos_demo',
                    expect_database='synthetic_restore',
                    expect_deployment_id=1,
                    expect_serving_version='bundled',
                    apply=True,
                    writers_stopped=True,
                )

    @override_settings(
        NEWCROWN_ALLOW_EXTERNAL_IO=False,
        SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False,
        NEWCROWN_RUN_SCHEDULED_TASKS=False,
    )
    def test_non_postgresql_target_is_refused(self):
        target = (
            'console.management.commands.reconcile_website_serving_cache.connection'
        )
        with patch(target) as database:
            database.vendor = 'sqlite'
            with self.assertRaisesRegex(CommandError, 'requires PostgreSQL'):
                call_command(
                    'reconcile_website_serving_cache',
                    site_code='siteos_demo',
                    expect_database='synthetic_restore',
                )
            database.cursor.assert_not_called()
