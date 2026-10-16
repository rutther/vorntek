"""Committed Meta inbound ownership races; only credential/HTTP boundaries fake."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
import json
import threading
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from leads.inbound import process_inbound_receipt, recover_stale_inbound_claims, retry_inbound_receipt_by_id
from leads.models import LeadFormDefinition, LeadInboundEvent, LeadNotificationAttempt, LeadSubmission
from marketing.models import MarketingIntegration, MarketingProvider
from sitecore.models import Site, SiteLocale


@skipUnless(connection.vendor == 'postgresql', 'Requires committed full-SQL Meta inbound ownership')
@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MetaInboundClaimTests(TransactionTestCase):
    def _fixture_teardown(self):
        return None  # Each fixture is unique; disposable full-SQL DB belongs to driver.

    def fixture(self):
        code = 'meta_inbound_' + uuid4().hex[:12]
        site = Site.objects.create(code=code, name='Synthetic Meta inbound claim', default_locale='en')
        locale = SiteLocale.objects.create(site=site, locale_code='en', label='English', is_default=True, enabled=True)
        form = LeadFormDefinition.objects.create(site=site, locale=locale, code=code, name='Synthetic inbound',
            status='active', capi_enabled=False, notify_emails='')
        provider, _ = MarketingProvider.objects.get_or_create(code='meta', defaults={'name': 'Meta'})
        integration = MarketingIntegration.objects.create(site=site, provider=provider, integration_type='leadgen',
            enabled=True, name='Synthetic original Page', public_id=str(100000000000000 + uuid4().int % 100000000000000),
            config_json={'form_code': form.code, 'leadgen_form_ids': ['10001'], 'contact_consent_confirmed': True})
        receipt = LeadInboundEvent.objects.create(integration=integration, provider_code='meta', event_type='leadgen',
            external_event_id='synthetic-' + uuid4().hex, received_at=timezone.now(),
            payload_json={'leadgen_id': 'synthetic-' + uuid4().hex, 'page_id': integration.public_id, 'form_id': '10001'})
        return receipt

    def worker(self, pk):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            return process_inbound_receipt(LeadInboundEvent.objects.get(pk=pk)), pid
        finally:
            close_old_connections()

    def run_late_result(self, *, failure=False, replacement=True, recover=True):
        receipt = self.fixture()
        arrived, release = threading.Event(), threading.Event()
        requests = []
        original_payload = dict(receipt.payload_json)

        def transport(request, **kwargs):
            self.assertTrue(request.full_url.startswith('https://graph.facebook.com/'))
            requests.append(request.full_url)
            first = len(requests) == 1
            if first:
                arrived.set()
                if not release.wait(8):
                    raise RuntimeError('Synthetic inbound gate was not released')
                if failure:
                    raise RuntimeError('Synthetic old fetch failed late')
            lead_data = {'id': original_payload['leadgen_id'], 'form_id': '10001',
                'created_time': receipt.received_at.isoformat(), 'field_data': [
                    {'name': 'full_name', 'values': ['Synthetic old result' if first else 'Synthetic current result']},
                    {'name': 'email', 'values': ['synthetic-buyer@example.test']}]}
            return BytesIO(json.dumps(lead_data).encode())

        with patch('leads.inbound.integration_secret', return_value='synthetic-only'), \
             patch('leads.inbound.urllib.request.urlopen', side_effect=transport), \
             ThreadPoolExecutor(max_workers=1) as pool:
            old = pool.submit(self.worker, receipt.pk)
            try:
                self.assertTrue(arrived.wait(3), 'Old worker never reached external read')
                receipt.refresh_from_db()
                self.assertEqual((receipt.status, receipt.attempts), ('processing', 1))
                if recover:
                    self.assertEqual(recover_stale_inbound_claims(
                        now=timezone.now() + timedelta(minutes=16),
                        queryset=LeadInboundEvent.objects.filter(pk=receipt.pk)), 1)
                if replacement:
                    current, succeeded = retry_inbound_receipt_by_id(receipt_id=receipt.pk,
                        site_id=receipt.integration.site_id)
                    self.assertTrue(succeeded)
                    self.assertEqual((current.status, current.attempts), ('processed', 2))
                elif not recover:
                    self.assertFalse(process_inbound_receipt(LeadInboundEvent.objects.get(pk=receipt.pk)))
                receipt.refresh_from_db()
                before = (receipt.status, receipt.attempts, receipt.payload_json, receipt.submission_id,
                    receipt.processed_at, receipt.updated_at)
            finally:
                release.set()
            old_succeeded, request_pid = old.result(timeout=5)
        receipt.refresh_from_db()
        if recover:
            self.assertFalse(old_succeeded, 'Replaced/recovered worker must not report successful materialization')
            self.assertEqual((receipt.status, receipt.attempts, receipt.payload_json, receipt.submission_id,
                receipt.processed_at, receipt.updated_at), before, 'Late old result rewrote current durable receipt')
            self.assertEqual(LeadSubmission.objects.filter(site=receipt.integration.site).count(), 1 if replacement else 0)
            if replacement:
                self.assertEqual(receipt.submission.full_name, 'Synthetic current result')
                self.assertEqual(LeadNotificationAttempt.objects.filter(submission=receipt.submission).count(), 1)
        else:
            self.assertTrue(old_succeeded)
            self.assertEqual((receipt.status, receipt.attempts), ('processed', 1))
            self.assertEqual(LeadSubmission.objects.filter(site=receipt.integration.site).count(), 1)
        self.assertEqual(len(requests), 2 if replacement else 1)
        print('META_INBOUND_COMMITTED_RACE ' + json.dumps({'request_pid': request_pid, 'late_failure': failure,
            'replacement': replacement, 'recovered': recover, 'provider_reads': len(requests), 'attempts': receipt.attempts}))

    def test_late_old_failure_cannot_replace_new_success(self):
        self.run_late_result(failure=True)

    def test_late_old_success_cannot_replace_new_payload(self):
        self.run_late_result()

    def test_recovered_claim_cannot_create_lead_without_new_owner(self):
        self.run_late_result(replacement=False)

    def test_live_second_claim_does_not_duplicate_provider_read(self):
        self.run_late_result(replacement=False, recover=False)
