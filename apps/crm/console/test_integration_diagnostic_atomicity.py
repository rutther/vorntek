"""Prepared read-only diagnostics and their durable audit commit as one fact."""
from contextlib import ExitStack
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.access import GROUP_BY_ROLE, ROLE_SYSTEM_ADMIN
from console.audit import record_audit as real_audit
from console.models import AuditLog
from marketing.models import IntegrationCheck, MarketingIntegration, MarketingProvider
from sitecore.models import Site, SiteLocale

DIAGNOSTIC = {'overall': 'pass', 'summary': 'Synthetic read-only diagnostic', 'checks': []}


class DiagnosticFixture:
    def setUp(self):
        self.site = Site.objects.create(code='siteos_demo', name='Synthetic diagnosis', default_locale='en')
        SiteLocale.objects.create(site=self.site, locale_code='en', label='English', enabled=True, is_default=True)
        self.user = get_user_model().objects.create_user(username='diagnostic-admin', password='synthetic-only')
        self.user.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])[0])
        provider = MarketingProvider.objects.get_or_create(code='meta', defaults={'name': 'Meta'})[0]
        self.integration = MarketingIntegration.objects.create(site=self.site, provider=provider,
            name='Synthetic Meta', integration_type='pixel', public_id='123456789', enabled=False,
            config_json={'existing': 'keep'})
        self.client = Client(raise_request_exception=False)
        self.client.force_login(self.user)

    def diagnose(self, entry, callback=None):
        # Stub only external read-only diagnostics; all DB/HTTP/audit writes are real.
        with ExitStack() as patches:
            for name in ('console.marketing_services', 'console.marketing_views', 'console.views'):
                patches.enter_context(patch(name + '.diagnose_marketing_integration',
                    side_effect=callback, return_value=DIAGNOSTIC, create=True))
            if entry == 'api':
                return self.client.post(reverse('console:integration_diagnose', args=[self.integration.pk]))
            return self.client.post(reverse('console:marketing_integration_edit', args=[self.integration.pk]), {'_action': 'diagnose'})


@skipUnless(connection.vendor == 'postgresql', 'Requires full-SQL diagnostic and audit atomicity')
@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class IntegrationDiagnosticAtomicityTests(DiagnosticFixture, TestCase):
    def test_final_audit_failure_rolls_back_evidence_and_summary_for_both_entries(self):
        def fail_final(*args, **kwargs):
            if kwargs.get('action') == 'marketing_integration_diagnosed':
                raise RuntimeError('Synthetic final audit failure')
            return real_audit(*args, **kwargs)
        for entry in ('api', 'page'):
            with self.subTest(entry=entry):
                before = MarketingIntegration.objects.filter(pk=self.integration.pk).values().get()
                checks = IntegrationCheck.objects.filter(integration=self.integration).count()
                with patch('console.marketing_views.record_audit', side_effect=fail_final), \
                     patch('console.views.record_audit', side_effect=fail_final):
                    response = self.diagnose(entry)
                self.assertEqual(response.status_code, 500)
                self.assertEqual(MarketingIntegration.objects.filter(pk=self.integration.pk).values().get(), before)
                self.assertEqual(IntegrationCheck.objects.filter(integration=self.integration).count(), checks)
                self.assertEqual(AuditLog.objects.filter(action='marketing_integration_diagnosed', entity_id=self.integration.pk).count(), 0)

    def test_config_changed_during_read_cannot_save_old_diagnostic(self):
        for entry in ('api', 'page'):
            with self.subTest(entry=entry):
                checks = IntegrationCheck.objects.filter(integration=self.integration).count()
                def change_config(_integration):
                    MarketingIntegration.objects.filter(pk=self.integration.pk).update(
                        name='New operator config', config_json={'new': 'preserve'}, updated_at=timezone.now())
                    return DIAGNOSTIC
                response = self.diagnose(entry, change_config)
                self.assertEqual(response.status_code, 409 if entry == 'api' else 302)
                self.integration.refresh_from_db()
                self.assertEqual(self.integration.config_json, {'new': 'preserve'})
                self.assertEqual(IntegrationCheck.objects.filter(integration=self.integration).count(), checks)

    def test_normal_diagnostic_commits_evidence_summary_and_audit_for_both_entries(self):
        for index, entry in enumerate(('api', 'page'), 1):
            with self.subTest(entry=entry):
                response = self.diagnose(entry)
                self.assertEqual(response.status_code, 200 if entry == 'api' else 302)
                self.integration.refresh_from_db()
                self.assertFalse(self.integration.enabled)
                self.assertEqual(self.integration.config_json.get('existing'), 'keep')
                self.assertEqual(self.integration.config_json.get('last_diagnostic'), DIAGNOSTIC)
                self.assertEqual(IntegrationCheck.objects.filter(integration=self.integration).count(), index)
                self.assertEqual(AuditLog.objects.filter(action='marketing_integration_diagnosed', entity_id=self.integration.pk).count(), index)


@skipUnless(connection.vendor == 'postgresql', 'Requires real autocommit diagnostic boundary')
@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class IntegrationDiagnosticReadBoundaryTests(DiagnosticFixture, TransactionTestCase):
    def _fixture_teardown(self):
        # Run this single test in its own fresh full-SQL disposable database.
        return None

    def test_external_read_runs_without_an_open_database_transaction(self):
        observations = []
        def observe(_integration):
            observations.append(connection.in_atomic_block)
            return DIAGNOSTIC
        for entry in ('api', 'page'):
            response = self.diagnose(entry, observe)
            self.assertEqual(response.status_code, 200 if entry == 'api' else 302)
        self.assertEqual(observations, [False, False], 'Do not hold DB transactions during external diagnostic reads')
