"""Scheduling must refresh local evidence even when all providers are silent."""
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from siteos_admin.job_scheduler import jobs, tick


class SchedulerRuntimeTests(SimpleTestCase):
    def test_silent_tick_refreshes_catalog_without_claiming_or_sending_business_work(self):
        executed = []
        schedule = jobs(10)
        results = tick(schedule, now=10, enabled=False,
            execute=lambda job: executed.append(job.name), clock=lambda: 10)
        self.assertEqual(executed, ['refresh_pixel_page_catalog'])
        self.assertEqual(results, [{'job': 'refresh_pixel_page_catalog', 'status': 'completed'}])
        tick(schedule, now=70, enabled=False, execute=lambda job: executed.append(job.name), clock=lambda: 70)
        self.assertEqual(executed, ['refresh_pixel_page_catalog', 'refresh_pixel_page_catalog'])

    def test_initial_delivery_is_scheduled_within_a_minute_without_enabling_whatsapp(self):
        executed = []
        schedule = jobs(0)
        tick(schedule, now=0, enabled=True, execute=lambda job: executed.append(job.name), clock=lambda: 0)
        tick(schedule, now=60, enabled=True, execute=lambda job: executed.append(job.name), clock=lambda: 60)
        self.assertEqual(executed, ['refresh_pixel_page_catalog', 'refresh_pixel_page_catalog',
            'dispatch_lead_outbox', 'send_lead_reminders'])
        self.assertFalse(any('whatsapp' in job.name for job in schedule))

    @override_settings(NEWCROWN_RUN_SCHEDULED_TASKS=False, NEWCROWN_ALLOW_EXTERNAL_IO=False)
    def test_real_paused_command_writes_only_local_catalog_and_stopped_heartbeat(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            serving = directory / 'serving/current'
            serving.mkdir(parents=True)
            (serving / 'index.html').write_text('<title>Published synthetic home</title>'
                '<form data-form-code="general-inquiry"></form>', encoding='utf-8')
            output = directory / 'runtime/catalog.json'
            heartbeat = directory / 'runtime/heartbeat.json'
            with override_settings(SITEOS_WEBSITE_SERVING_ROOT=serving.parent,
                                   SITEOS_PIXEL_CATALOG_PATH=output), \
                 patch('console.management.commands.run_scheduled_tasks.connection') as db:
                db.vendor = 'postgresql'
                db.cursor.return_value.__enter__.return_value.fetchone.side_effect = [(True, 123)] + [(123,)] * 10
                call_command('run_scheduled_tasks', once=True, heartbeat=heartbeat, stdout=io.StringIO())
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['pages'][0]['formCodes'], ['general-inquiry'])
            state = json.loads(heartbeat.read_text(encoding='utf-8'))
            self.assertEqual(state['state'], 'stopped')
            self.assertEqual(state['jobs']['refresh_pixel_page_catalog']['status'], 'completed')
            self.assertEqual({name for name, result in state['jobs'].items() if result['status'] == 'completed'},
                {'refresh_pixel_page_catalog'})
