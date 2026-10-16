import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from siteos_admin.job_scheduler import jobs, tick


class SchedulerTests(SimpleTestCase):
    def test_running_jobs_requires_both_explicit_switches(self):
        for scheduled, outbound in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(scheduled=scheduled, outbound=outbound), tempfile.TemporaryDirectory() as directory:
                with override_settings(NEWCROWN_RUN_SCHEDULED_TASKS=scheduled, NEWCROWN_ALLOW_EXTERNAL_IO=outbound), \
                     patch('console.management.commands.run_scheduled_tasks.connection') as db, \
                     patch('console.management.commands.run_scheduled_tasks.call_command') as execute:
                    db.vendor = 'postgresql'
                    cursor = db.cursor.return_value.__enter__.return_value
                    cursor.fetchone.side_effect = [(True, 123)] + [(123,)] * 10
                    call_command('run_scheduled_tasks', once=True, heartbeat=Path(directory)/'heartbeat.json', stdout=io.StringIO())
                    names = [item.args[0] for item in execute.call_args_list]
                    self.assertEqual(names, ['refresh_pixel_page_catalog', 'retry_inbound_leads',
                        'dispatch_lead_outbox', 'send_lead_reminders'] if scheduled and outbound
                        else ['refresh_pixel_page_catalog'])
                    db.close.assert_called_once()

    def test_job_list_intervals_and_meta_only_inbound(self):
        schedule = jobs(10)
        self.assertEqual([job.interval for job in schedule], [60, 300, 60, 60])
        self.assertEqual(schedule[1].kwargs, {'provider': 'meta'})
        self.assertFalse(any('whatsapp' in job.name for job in schedule))
        self.assertEqual([job.due_at for job in schedule], [10, 310, 70, 70])

    def test_not_due_jobs_do_not_run(self):
        execute = Mock()
        # Local evidence is due at startup; external jobs are not due yet.
        external = [job for job in jobs(0) if job.requires_external_io]
        self.assertEqual(tick(external, now=59, enabled=True, execute=execute, clock=lambda: 59), [])
        execute.assert_not_called()

    def test_shutdown_stops_before_starting_next_job(self):
        stop = False

        def execute(job):
            nonlocal stop
            stop = True

        results = tick(jobs(0), now=900, enabled=True, execute=execute,
                       clock=lambda: 900, stop_requested=lambda: stop)
        self.assertEqual(len(results), 1)

    def test_paused_jobs_never_execute_or_catch_up(self):
        execute = Mock()
        schedule = [job for job in jobs(0) if job.requires_external_io]
        results = tick(schedule, now=9000, enabled=False, execute=execute, clock=lambda: 9000)
        self.assertEqual([r['status'] for r in results], ['paused'] * 3)
        self.assertEqual([j.due_at for j in schedule], [9300, 9060, 9060])
        execute.assert_not_called()

    def test_failures_are_redacted_and_other_jobs_continue(self):
        execute = Mock(side_effect=[ValueError('SYNTHETIC_PRIVATE_DETAIL'), None, None, None])
        results = tick(jobs(0), now=900, enabled=True, execute=execute, clock=lambda: 1000)
        self.assertEqual([r['status'] for r in results], ['failed', 'completed', 'completed', 'completed'])
        self.assertEqual(results[0]['error_type'], 'ValueError')
        self.assertNotIn('SYNTHETIC_PRIVATE_DETAIL', json.dumps(results))

    def test_healthcheck_requires_fresh_running_or_paused_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'heartbeat.json'
            for state, updated, succeeds in [('paused', time.time(), True), ('running', time.time(), True),
                                             ('stopped', time.time(), False), ('paused', time.time()-180, False)]:
                path.write_text(json.dumps({'state': state, 'updated_at': updated}), encoding='utf-8')
                if succeeds:
                    call_command('run_scheduled_tasks', healthcheck=True, heartbeat=path)
                else:
                    with self.assertRaises(CommandError):
                        call_command('run_scheduled_tasks', healthcheck=True, heartbeat=path)

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False)
    def test_reminder_command_does_not_read_leads_in_silent_environment(self):
        output = io.StringIO()
        call_command('send_lead_reminders', stdout=output)
        self.assertIn('disabled', output.getvalue())

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False)
    def test_direct_google_and_meta_paths_stop_before_secrets_or_network(self):
        from leads.google_data_manager import (_load_google_credentials, _google_request,
                                               dispatch_google_outbox_event, refresh_google_outbox_status,
                                               GoogleDataManagerError)
        from leads.inbound import fetch_meta_lead, InboundEventError
        with patch('urllib.request.urlopen') as network:
            self.assertFalse(dispatch_google_outbox_event(None))
            self.assertFalse(refresh_google_outbox_status(None))
            with self.assertRaises(GoogleDataManagerError):
                _load_google_credentials(None)
            with self.assertRaises(GoogleDataManagerError):
                _google_request(method='GET', url='https://example.invalid', token='synthetic')
            with self.assertRaises(InboundEventError):
                fetch_meta_lead(None, 'synthetic')
            network.assert_not_called()

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False)
    def test_diagnostics_report_disabled_without_loading_secrets(self):
        from console.marketing_diagnostics import diagnose_marketing_integration, _graph_get, _ycloud_get
        integration = SimpleNamespace(provider=SimpleNamespace(code='meta'), integration_type='pixel', enabled=True)
        with patch('urllib.request.urlopen') as network, patch('console.marketing_diagnostics.resolve_meta_access_token') as secret:
            result = diagnose_marketing_integration(integration)
            self.assertEqual(result['overall'], 'fail')
            self.assertEqual(result['checks'][1]['code'], 'external_io')
            self.assertFalse(_graph_get('https://example.invalid', 'synthetic')['ok'])
            self.assertFalse(_ycloud_get('https://example.invalid', 'synthetic')['ok'])
            secret.assert_not_called()
            network.assert_not_called()
