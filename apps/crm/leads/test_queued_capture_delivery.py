"""Real committed HTTP capture and existing schedulers, synthetic transport only."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import timedelta
from io import BytesIO, StringIO
import json
import threading
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from leads.models import LeadEventAttempt, LeadEventOutbox, LeadFormDefinition, LeadNotificationAttempt, LeadSubmission
from marketing.models import MarketingIntegration, MarketingProvider
from sitecore.models import Site, SiteLocale


@skipUnless(connection.vendor == 'postgresql', 'Requires actual capture commit and separate request connection')
@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', DEBUG=False,
    ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False, SITEOS_META_RUNTIME_ENABLED=False,
    SITEOS_META_RUNTIME_ENVIRONMENT='production', SITEOS_LEAD_REMINDER_HOURS=99999)
class QueuedCaptureDeliveryTests(TransactionTestCase):
    def _fixture_teardown(self):
        # Tests own distinct synthetic sites in a disposable full-SQL database.
        return None

    def _notification_form(self, code):
        site = Site.objects.create(code=code, name='Synthetic scheduler notification', default_locale='en')
        locale = SiteLocale.objects.create(site=site, locale_code='en', label='English', is_default=True, enabled=True)
        return LeadFormDefinition.objects.create(site=site, code=code, name='Synthetic scheduler inquiry',
            locale=locale, notify_emails='synthetic-recipient@example.test', status='active',
            category='sales', channel='website', scope_type='site', scope_value='*')

    @override_settings(SITEOS_LEAD_REMINDER_HOURS=4, SITEOS_LEAD_REMINDER_REPEAT_HOURS=24)
    def test_failed_initial_and_reminder_wait_between_automatic_retries(self):
        form = self._notification_form('queued_retry')
        now = timezone.now()
        initial = LeadSubmission.objects.create(site=form.site, form=form, full_name='Synthetic initial retry',
            notification_status='pending', submitted_at=now - timedelta(minutes=20), stage_updated_at=now,
            config_json={'capture_delivery': 'queued'})
        reminder = LeadSubmission.objects.create(site=form.site, form=form, full_name='Synthetic reminder retry',
            notification_status='sent', notified_at=now - timedelta(hours=5),
            submitted_at=now - timedelta(hours=5), stage_updated_at=now)
        deliveries = []

        def refused_then_accepted(messages):
            deliveries.extend(message.subject for message in messages)
            return 0 if len(deliveries) <= 2 else 1

        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=refused_then_accepted):
            call_command('send_lead_reminders', stdout=StringIO())
            self.assertEqual(len(deliveries), 2)
            self.assertEqual(LeadNotificationAttempt.objects.filter(submission__in=[initial, reminder], state='failed').count(), 2)
            call_command('send_lead_reminders', stdout=StringIO())
            self.assertEqual(len(deliveries), 2, 'Every minute tick must not retry a recently refused SMTP delivery')
            # Advance selection time only; transport claim leases use real time.
            with patch('leads.management.commands.send_lead_reminders.timezone',
                       SimpleNamespace(now=lambda: now + timedelta(minutes=16))):
                call_command('send_lead_reminders', stdout=StringIO())
                call_command('send_lead_reminders', stdout=StringIO())
            self.assertEqual(len(deliveries), 4)
        initial.refresh_from_db()
        reminder.refresh_from_db()
        self.assertEqual(initial.notification_status, 'sent')
        self.assertIsNotNone(reminder.reminder_sent_at)
        self.assertEqual(LeadNotificationAttempt.objects.filter(submission__in=[initial, reminder], state='accepted').count(), 2)

    def test_new_queued_initial_is_not_starved_by_old_failed_backlog(self):
        form = self._notification_form('queued_backlog')
        now = timezone.now()
        LeadSubmission.objects.bulk_create([
            LeadSubmission(site=form.site, form=form, full_name=f'Synthetic old refusal {index}',
                notification_status='failed', submitted_at=now - timedelta(hours=1), stage_updated_at=now)
            for index in range(100)
        ])
        fresh = LeadSubmission.objects.create(site=form.site, form=form, full_name='Synthetic fresh queued inquiry',
            notification_status='pending', submitted_at=now, stage_updated_at=now,
            config_json={'capture_delivery': 'queued'})
        deliveries = []

        def first_accepted(messages):
            deliveries.extend(message.subject for message in messages)
            return 1 if 'Synthetic fresh queued inquiry' in messages[0].subject else 0

        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=first_accepted):
            call_command('send_lead_reminders', stdout=StringIO())
        fresh.refresh_from_db()
        self.assertEqual(fresh.notification_status, 'sent', 'Old failed retries must not consume all initial-delivery slots')
        self.assertIn('Synthetic fresh queued inquiry', deliveries[0])
        self.assertEqual(len(deliveries), 100, 'The existing initial-delivery batch limit must remain bounded')
        self.assertEqual(LeadNotificationAttempt.objects.get(submission=fresh).state, 'accepted')

    def test_capture_returns_without_transport_then_existing_workers_deliver_once(self):
        site = Site.objects.create(code='siteos_demo', name='Synthetic queued capture', default_locale='en')
        locale = SiteLocale.objects.create(site=site, locale_code='en', label='English', is_default=True, enabled=True)
        form = LeadFormDefinition.objects.create(site=site, code='queued-inquiry', name='Synthetic queued inquiry',
            locale=locale, capi_enabled=True, submission_event_name='Lead', notify_emails='synthetic-recipient@example.test',
            status='active', category='sales', channel='website', scope_type='site', scope_value='*')
        provider, _ = MarketingProvider.objects.get_or_create(code='meta', defaults={'name': 'Meta'})
        MarketingIntegration.objects.create(site=site, provider=provider, integration_type='pixel', enabled=True,
            name='Synthetic original target', public_id='1234567890', routing_mode='legacy_site', environment='production')
        payload = {'full_name': 'Synthetic queued inquiry', 'email': 'synthetic-buyer@example.test',
            'company': 'Synthetic buyer company', 'country': 'United Arab Emirates',
            'extra_fields': {'product_category': 'water', 'capacity': '12000 BPH'},
            'message': 'Synthetic complete inquiry', 'source_url': 'https://example.test/contact/',
            'form_started_at': (timezone.now() - timedelta(seconds=10)).isoformat(),
            'client_event_id': str(uuid4()), 'consent': {'contact': True, 'privacy_notice': True, 'marketing': True}}
        url = reverse('console:public_lead_submit', args=[form.code]) + '?locale=en'
        mail_messages, meta_requests = [], []
        release_transport = threading.Event()

        def mail_transport(messages):
            mail_messages.extend(messages)
            if not release_transport.wait(8):
                raise RuntimeError('Synthetic mail transport was not released')
            return 1

        def meta_transport(request, **kwargs):
            meta_requests.append(json.loads(request.data)['data'][0]['event_id'])
            if not release_transport.wait(8):
                raise RuntimeError('Synthetic Meta transport was not released')
            return BytesIO(b'{"events_received":1,"fbtrace_id":"synthetic-queued-ack"}')

        def capture():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                response = Client().post(url, json.dumps(payload), content_type='application/json',
                    REMOTE_ADDR='127.0.0.1', HTTP_USER_AGENT='Synthetic queued capture browser')
                return response, pid
            finally:
                close_old_connections()

        with patch('django.core.mail.backends.locmem.EmailBackend.send_messages', side_effect=mail_transport), \
             patch('leads.services.resolve_meta_access_token', return_value='synthetic-only'), \
             patch('leads.services.urllib.request.urlopen', side_effect=meta_transport):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(capture)
                try:
                    try:
                        response, request_pid = pending.result(timeout=2)
                    except TimeoutError:
                        self.fail('Committed capture response waited for blocked external delivery')
                    self.assertEqual(response.status_code, 200, response.content[:500])
                    self.assertEqual(mail_messages, [])
                    self.assertEqual(meta_requests, [])
                finally:
                    release_transport.set()
            body = response.json()
            self.assertEqual((body['dispatch_attempted'], body['dispatch_succeeded'], body['notification_sent']), (0, 0, False))
            self.assertEqual(body['queued_events'], 1, {
                'body': body,
                'targets': list(MarketingIntegration.objects.filter(site=site).values('enabled', 'routing_mode', 'environment')),
                'events': list(LeadEventOutbox.objects.values('status', 'last_error')),
            })
            lead = LeadSubmission.objects.get(pk=body['submission_id'])
            event = LeadEventOutbox.objects.get(submission=lead)
            self.assertEqual((lead.notification_status, event.status), ('pending', 'pending'))
            self.assertEqual(LeadNotificationAttempt.objects.filter(submission=lead).count(), 0)
            self.assertEqual(LeadEventAttempt.objects.filter(outbox=event).count(), 0)
            duplicate = Client().post(url, json.dumps(payload), content_type='application/json',
                REMOTE_ADDR='127.0.0.1', HTTP_USER_AGENT='Synthetic queued capture browser')
            self.assertTrue(duplicate.json()['duplicate'])
            self.assertEqual(duplicate.json()['submission_id'], lead.pk)
            self.assertEqual((len(mail_messages), len(meta_requests)), (0, 0))
            # Preserve the original immediate-initial-notification eligibility
            # even if sales processes this newly queued inquiry before the tick.
            lead.stage = 'contacted'
            lead.contacted_at = timezone.now()
            lead.save(update_fields=['stage', 'contacted_at', 'updated_at'])
            legacy = LeadSubmission.objects.create(site=site, form=form, full_name='Synthetic old processed pending',
                stage='contacted', contacted_at=timezone.now(), notification_status='pending',
                submitted_at=timezone.now() - timedelta(hours=1), stage_updated_at=timezone.now())
            command_output = StringIO()
            call_command('send_lead_reminders', stdout=command_output)
            call_command('dispatch_lead_outbox', stdout=command_output)
            lead.refresh_from_db()
            event.refresh_from_db()
            legacy.refresh_from_db()
            self.assertEqual((lead.notification_status, event.status), ('sent', 'sent'))
            self.assertEqual(legacy.notification_status, 'pending')
            self.assertEqual((len(mail_messages), meta_requests), (1, [event.event_id]))
            self.assertEqual(LeadNotificationAttempt.objects.get(submission=lead).state, 'accepted')
            self.assertEqual(LeadEventAttempt.objects.get(outbox=event).state, 'accepted')
            call_command('send_lead_reminders', stdout=command_output)
            call_command('dispatch_lead_outbox', stdout=command_output)
            self.assertEqual((len(mail_messages), len(meta_requests)), (1, 1))
            self.assertEqual(LeadSubmission.objects.filter(form=form).count(), 2)
            print('QUEUED_CAPTURE_REAL_HTTP ' + json.dumps({'request_pid': request_pid,
                'capture_dispatch_attempts': body['dispatch_attempted'], 'queued_events': body['queued_events'],
                'smtp_transports': len(mail_messages), 'meta_transports': len(meta_requests),
                'legacy_processed_pending_unchanged': legacy.notification_status == 'pending'}))
