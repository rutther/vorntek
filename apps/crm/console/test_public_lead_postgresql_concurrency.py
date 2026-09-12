from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from console.models import AuditLog
from leads import services as lead_services
from leads.models import (
    Activity,
    Company,
    ConsentRecord,
    Contact,
    LeadConversion,
    LeadEventOutbox,
    LeadFormDefinition,
    LeadSubmission,
    Opportunity,
    SalesTeam,
    SalesTeamMember,
    SavedView,
    Task,
)
from marketing.models import CanonicalEvent, MarketingIntegration, MarketingProvider
from sitecore.models import Cta, PageRoute, Site, SiteLocale


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL is required for the real concurrency contract.')
class PostgreSQLLeadCaptureConcurrencyTests(TransactionTestCase):
    """Prove two independent PostgreSQL connections converge on one inquiry."""

    reset_sequences = True
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        Cta,
        CanonicalEvent,
        MarketingProvider,
        MarketingIntegration,
        LeadFormDefinition,
        SalesTeam,
        SalesTeamMember,
        SavedView,
        LeadSubmission,
        ConsentRecord,
        Company,
        Contact,
        Opportunity,
        LeadConversion,
        Activity,
        Task,
        LeadEventOutbox,
        AuditLog,
    )

    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        try:
            with connection.schema_editor() as editor:
                for model in cls.unmanaged_models:
                    if model._meta.db_table not in existing:
                        editor.create_model(model)
                        cls.created_models.append(model)
                        existing.add(model._meta.db_table)
            super().setUpClass()
        except Exception:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)

    def setUp(self):
        self.site = Site.objects.create(
            code='siteos_demo',
            name='PostgreSQL concurrency site',
            base_url='https://example.test',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.locale = SiteLocale.objects.create(
            site=self.site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )
        self.team = SalesTeam.objects.create(
            site=self.site,
            code='default',
            name='Default Sales',
            enabled=True,
        )
        self.form = LeadFormDefinition.objects.create(
            site=self.site,
            locale=None,
            code='pg-concurrency-inquiry',
            name='PostgreSQL concurrency inquiry',
            category='sales',
            channel='website',
            scope_type='site',
            scope_value='*',
            status='active',
            capi_enabled=True,
            notify_emails='never-send@example.test',
            success_message='Inquiry accepted.',
            form_schema={},
            config_json={'sales_team_code': 'default'},
        )
        self.submit_url = reverse('console:public_lead_submit', args=[self.form.code]) + '?locale=en'

    def _fixture_teardown(self):
        # This class is intentionally one test in a disposable Django test
        # database. Django's managed-table flush cannot truncate auth_user
        # while unmanaged production-shape tables reference it, even with no
        # user rows. The runner destroys the entire test database immediately
        # after the class, which is the stronger isolation boundary here.
        return None

    def _payload(self):
        return {
            'full_name': 'PostgreSQL Concurrency Tester',
            'email': 'pg-concurrency@example.test',
            'phone': '',
            'company': 'PostgreSQL Concurrency Company',
            'country': 'United Arab Emirates',
            'message': 'Two-connection duplicate convergence test.',
            'source_url': 'https://example.test/contact/',
            'referrer_url': '',
            'route_path': '/contact/',
            'client_event_id': 'pg-concurrency-event',
            'form_started_at': (timezone.now() - timedelta(seconds=5)).isoformat(),
            'website': '',
            'utm': {},
            'consent': {
                'contact': True,
                'privacy_notice': True,
                'marketing': False,
                'policy_version': 'test-only',
            },
            'extra_fields': {
                'inquiry_type': 'new_line',
                'product_category': 'water',
                'capacity': '12000 BPH',
                'site_language': 'en',
            },
        }

    def test_two_real_connections_converge_to_one_business_lead(self):
        barrier = threading.Barrier(2, timeout=10)
        original_lock = lead_services._acquire_dedupe_lock

        def synchronized_lock(dedupe_key):
            barrier.wait()
            return original_lock(dedupe_key)

        def submit_from_independent_connection(index):
            close_old_connections()
            try:
                client = Client()
                response = client.post(
                    self.submit_url,
                    data=json.dumps(self._payload()),
                    content_type='application/json',
                    REMOTE_ADDR=f'127.0.0.{index + 1}',
                    HTTP_USER_AGENT='NewCrown PostgreSQL concurrency test',
                )
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with (
            patch('leads.services._acquire_dedupe_lock', side_effect=synchronized_lock),
            patch('leads.notifications.send_submission_notification', return_value=False) as notify,
            patch('leads.services.dispatch_outbox_event') as dispatch,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(submit_from_independent_connection, (0, 1)))

        self.assertEqual([status for status, _body in results], [200, 200])
        bodies = [body for _status, body in results]
        self.assertEqual(sorted(body['duplicate'] for body in bodies), [False, True])
        self.assertEqual(len({body['submission_id'] for body in bodies}), 1)
        self.assertEqual(len({body['submission_key'] for body in bodies}), 1)
        self.assertEqual(LeadSubmission.objects.count(), 1)
        self.assertEqual(ConsentRecord.objects.count(), 3)
        self.assertEqual(LeadEventOutbox.objects.count(), 0)
        notify.assert_called_once()
        dispatch.assert_not_called()
