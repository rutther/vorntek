import json
from io import StringIO

from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from console.models import AuditLog
from console.test_file_lifecycle_contract import FileLifecycleFixture
from sitecore.models import Site


@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False, NEWCROWN_RUN_SCHEDULED_TASKS=False,
                   SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False)
class SiteOriginCommandTests(FileLifecycleFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.site.base_url = 'http://localhost:8088'
        self.site.config_json = {'preserve': 'operator configuration'}
        self.site.save()

    def command(self, **options):
        self.assertIn('configure_site_origin', get_commands(), 'Upgrades need an explicit guarded origin update')
        output = StringIO()
        call_command('configure_site_origin', site_code=self.site.code,
                     origin='https://workspace.example.invalid', expected_current='http://localhost:8088',
                     stdout=output, **options)
        return json.loads(output.getvalue())

    def test_default_plan_preserves_database_and_audit(self):
        before = AuditLog.objects.count()
        result = self.command()
        self.site.refresh_from_db()
        self.assertEqual(result['result'], 'PLAN ONLY')
        self.assertEqual(self.site.base_url, 'http://localhost:8088')
        self.assertEqual(AuditLog.objects.count(), before)

    def test_apply_updates_one_origin_and_records_real_audit(self):
        other = Site.objects.create(code='other_origin', name='Other', base_url='https://other.example.invalid')
        before = AuditLog.objects.count()
        result = self.command(apply=True)
        self.site.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(result['result'], 'origin_updated')
        self.assertEqual(self.site.base_url, 'https://workspace.example.invalid')
        self.assertEqual(self.site.config_json, {'preserve': 'operator configuration'})
        self.assertEqual(other.base_url, 'https://other.example.invalid')
        self.assertEqual(AuditLog.objects.count(), before + 1)
        entry = AuditLog.objects.order_by('-id').first()
        self.assertEqual(entry.before_json, {'base_url': 'http://localhost:8088'})
        self.assertEqual(entry.after_json, {'base_url': 'https://workspace.example.invalid'})

    def test_stale_expected_origin_refuses_before_write(self):
        self.site.base_url = 'https://changed.example.invalid'; self.site.save()
        with self.assertRaises(CommandError): self.command(apply=True)
        self.site.refresh_from_db()
        self.assertEqual(self.site.base_url, 'https://changed.example.invalid')

    def test_repeat_with_current_origin_is_a_noop_without_duplicate_audit(self):
        self.command(apply=True)
        before = AuditLog.objects.count()
        output = StringIO()
        call_command('configure_site_origin', site_code=self.site.code,
                     origin='https://workspace.example.invalid', expected_current='https://workspace.example.invalid',
                     apply=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['result'], 'unchanged')
        self.assertEqual(AuditLog.objects.count(), before)

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True)
    def test_enabled_delivery_refuses_the_write(self):
        with self.assertRaises(CommandError): self.command(apply=True)
        self.site.refresh_from_db()
        self.assertEqual(self.site.base_url, 'http://localhost:8088')

    def test_malformed_or_insecure_public_origins_are_refused(self):
        self.assertIn('configure_site_origin', get_commands())
        for origin in ('https://user:secret@example.invalid', 'https://example.invalid/path',
                       'https://example.invalid/?x=1', 'http://example.invalid', 'https://example.invalid:0'):
            with self.subTest(origin=origin), self.assertRaises(CommandError):
                call_command('configure_site_origin', site_code=self.site.code, origin=origin,
                             expected_current='http://localhost:8088', apply=True, stdout=StringIO())
        self.site.refresh_from_db()
        self.assertEqual(self.site.base_url, 'http://localhost:8088')
