from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import DatabaseError, connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from console.access import (
    GROUP_BY_ROLE,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SYSTEM_ADMIN,
)
from console.models import AuditLog
from leads.inbound import (
    process_claimed_inbound_receipt,
    retry_inbound_events,
    retry_inbound_receipt_by_id,
)
from leads.models import (
    ConsentRecord,
    LeadEventOutbox,
    LeadFormDefinition,
    LeadInboundEvent,
    LeadSubmission,
    SalesTeam,
)
from marketing.models import (
    CanonicalEvent,
    IntegrationCheck,
    MarketingIntegration,
    MarketingProvider,
)
from sitecore.models import Cta, PageRoute, Site, SiteLocale


class MarketingEventWorkspaceTests(TestCase):
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
        MarketingProvider,
        MarketingIntegration,
        IntegrationCheck,
        LeadFormDefinition,
        SalesTeam,
        LeadSubmission,
        ConsentRecord,
        LeadEventOutbox,
        LeadInboundEvent,
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
        self.now = timezone.now().replace(microsecond=0)
        self.groups = {
            role: Group.objects.create(name=GROUP_BY_ROLE[role])
            for role in (ROLE_MARKETING_OPS, ROLE_SALES, ROLE_SYSTEM_ADMIN)
        }
        self.marketing = self._user('marketing-events', ROLE_MARKETING_OPS)
        self.admin = self._user('marketing-admin', ROLE_SYSTEM_ADMIN)
        self.sales = self._user('marketing-sales', ROLE_SALES)
        self.site, self.locale, self.form = self._site_bundle('siteos_demo')
        self.other_site, self.other_locale, self.other_form = self._site_bundle('other')
        self.meta = MarketingProvider.objects.create(
            code='meta', name='Meta', enabled=True, capabilities={},
        )
        self.google = MarketingProvider.objects.create(
            code='google', name='Google', enabled=True, capabilities={},
        )
        self.outbound = self._integration(
            site=self.site,
            provider=self.meta,
            name='Meta CAPI',
            integration_type='pixel',
            public_id='123456',
        )
        self.inbound = self._integration(
            site=self.site,
            provider=self.meta,
            name='Meta Lead Ads',
            integration_type='leadgen',
            public_id='page-123',
        )
        self.other_outbound = self._integration(
            site=self.other_site,
            provider=self.google,
            name='Other Google',
            integration_type='data_manager',
            public_id='other-account',
        )
        self.lead = self._lead(
            form=self.form,
            site=self.site,
            locale=self.locale,
            name='Private Person',
        )
        self.other_lead = self._lead(
            form=self.other_form,
            site=self.other_site,
            locale=self.other_locale,
            name='Other Private Person',
        )

    def _user(self, username, role):
        user = get_user_model().objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password='test-password',
        )
        user.groups.add(self.groups[role])
        return user

    @staticmethod
    def _site_bundle(code):
        site = Site.objects.create(
            code=code,
            name=f'{code} site',
            base_url=f'https://{code}.example.com',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        locale = SiteLocale.objects.create(
            site=site,
            locale_code='en',
            label='English',
            direction='ltr',
            is_default=True,
            enabled=True,
            sort_order=10,
        )
        form = LeadFormDefinition.objects.create(
            site=site,
            locale=locale,
            code=f'{code}-form',
            name=f'{code} form',
            status='active',
            capi_enabled=True,
            form_schema={},
            config_json={},
        )
        return site, locale, form

    @staticmethod
    def _integration(*, site, provider, name, integration_type, public_id, enabled=True):
        return MarketingIntegration.objects.create(
            site=site,
            provider=provider,
            name=name,
            integration_type=integration_type,
            public_id=public_id,
            secret_ref='vault:test-reference',
            enabled=enabled,
            config_json={},
        )

    def _lead(self, *, form, site, locale, name):
        return LeadSubmission.objects.create(
            form=form,
            site=site,
            locale=locale,
            stage='new',
            full_name=name,
            email=f'{name.lower().replace(" ", ".")}@example.com',
            phone='+15550000000',
            company='Confidential Company',
            country='US',
            source_channel='website',
            payload_json={},
            identifiers_json={},
            utm_json={},
            consent_json={'marketing': True},
            config_json={},
            qualification_json={},
            submitted_at=self.now,
            stage_updated_at=self.now,
        )

    def _outbox(
        self,
        *,
        integration=None,
        lead=None,
        status='failed',
        suffix='1',
        next_attempt_at=None,
        attempts=1,
        last_error='provider failed',
    ):
        return LeadEventOutbox.objects.create(
            submission=lead or self.lead,
            integration=integration or self.outbound,
            stage_key='qualified',
            event_name=f'Qualified {suffix}',
            event_id=f'event-{suffix}',
            payload_json={},
            status=status,
            attempts=attempts,
            last_error=last_error,
            response_json={},
            next_attempt_at=next_attempt_at,
        )

    def _inbound(
        self,
        *,
        integration=None,
        status='failed',
        suffix='1',
        submission=None,
        attempts=1,
        payload=None,
    ):
        integration = integration or self.inbound
        return LeadInboundEvent.objects.create(
            integration=integration,
            submission=submission,
            provider_code=integration.provider.code,
            event_type='leadgen',
            external_event_id=f'receipt-{suffix}',
            payload_json=payload if payload is not None else {
                'leadgen_id': f'leadgen-{suffix}',
                'page_id': integration.public_id,
            },
            status=status,
            attempts=attempts,
            last_error='webhook processing failed' if status == 'failed' else '',
            received_at=self.now - timedelta(minutes=20),
        )

    def _get(self, params=None, *, user=None):
        self.client.force_login(user or self.marketing)
        return self.client.get(
            reverse('console:marketing_events'),
            params or {},
            follow=True,
        )

    def test_marketing_and_admin_can_open_but_sales_is_forbidden(self):
        for user in (self.marketing, self.admin):
            with self.subTest(user=user.username):
                response = self._get(user=user)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '<h1 class="page-title">事件运营</h1>', html=True)
        response = self._get(user=self.sales)
        self.assertEqual(response.status_code, 403)

    def test_unified_queue_is_site_scoped_and_does_not_expose_sales_pii(self):
        due = self._outbox(suffix='due')
        inbound = self._inbound(suffix='inbound')
        self._outbox(
            integration=self.other_outbound,
            lead=self.other_lead,
            suffix='cross-site',
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        workspace = response.context['workspace']
        self.assertEqual({row['key'] for row in workspace['rows']}, {
            f'outbound-{due.id}', f'inbound-{inbound.id}',
        })
        body = response.content.decode('utf-8')
        self.assertNotIn(self.lead.full_name, body)
        self.assertNotIn(self.lead.email, body)
        self.assertNotIn(self.other_lead.full_name, body)
        self.assertIn(f'线索 #{self.lead.id}', body)
        self.assertNotIn('查看线索', body)

    def test_filters_and_pagination_are_stable_get_contracts(self):
        for index in range(27):
            self._outbox(suffix=f'meta-{index:02d}')
        self._inbound(suffix='only-inbound')
        response = self._get({
            'direction': 'outbound',
            'provider': 'meta',
            'status': 'failed',
            'q': 'Qualified meta',
            'page': '2',
            'page_size': '25',
        })
        workspace = response.context['workspace']
        self.assertEqual(workspace['total'], 27)
        self.assertEqual(len(workspace['rows']), 2)
        self.assertEqual(workspace['pagination']['current_page'], 2)
        self.assertIn('direction=outbound', workspace['pagination']['previous_url'])
        self.assertIn('provider=meta', workspace['pagination']['previous_url'])
        self.assertIn('status=failed', workspace['pagination']['previous_url'])
        self.assertIn('q=Qualified+meta', workspace['pagination']['previous_url'])
        self.assertNotIn('locale=', workspace['pagination']['previous_url'])

    def test_search_does_not_allow_pii_probing_through_raw_errors(self):
        outbound = self._outbox(
            suffix='safe-search-target',
            last_error='Delivery failed for customer@example.com at +65 9123 4567',
        )
        inbound = self._inbound(suffix='safe-inbound-target')
        inbound.last_error = 'Rejected payload for another@example.com at +1 415 555 0100'
        inbound.save(update_fields=['last_error'])

        for probe in (
            'customer@example.com',
            '+65 9123 4567',
            'another@example.com',
            '+1 415 555 0100',
        ):
            with self.subTest(probe=probe):
                response = self._get({'status': 'all', 'q': probe})
                self.assertEqual(response.context['workspace']['total'], 0)

        by_event_id = self._get({'status': 'all', 'q': outbound.event_id})
        self.assertEqual(
            {row['key'] for row in by_event_id.context['workspace']['rows']},
            {f'outbound-{outbound.id}'},
        )
        by_platform = self._get({'status': 'all', 'q': self.outbound.name})
        self.assertEqual(by_platform.context['workspace']['total'], 1)

    def test_integration_evidence_api_redacts_error_credentials_and_pii(self):
        outbound = self._outbox(
            suffix='safe-evidence-outbound',
            last_error=(
                'Authorization: Bearer outbound-secret-token; app_secret="abc def"; '
                "client_secret='xyz qrs'; api_key=api-secret; "
                'customer@example.com; +65 9123 4567'
            ),
        )
        inbound = self._inbound(suffix='safe-evidence-inbound')
        inbound.last_error = (
            'access_token=inbound-secret-token; refresh_token="refresh secret"; '
            'another@example.com; +1 415 555 0100'
        )
        inbound.save(update_fields=['last_error'])

        self.client.force_login(self.marketing)
        outbound_response = self.client.get(
            reverse('console:integration_evidence_data', args=[self.outbound.id]),
        )
        inbound_response = self.client.get(
            reverse('console:integration_evidence_data', args=[self.inbound.id]),
        )

        self.assertEqual(outbound_response.status_code, 200)
        self.assertEqual(inbound_response.status_code, 200)
        outbound_error = next(
            row['last_error']
            for row in outbound_response.json()['outbound_events']
            if row['id'] == outbound.id
        )
        inbound_error = next(
            row['last_error']
            for row in inbound_response.json()['inbound_events']
            if row['id'] == inbound.id
        )
        rendered = f'{outbound_error} {inbound_error}'
        for secret in (
            'outbound-secret-token',
            'inbound-secret-token',
            'abc def',
            'xyz qrs',
            'api-secret',
            'refresh secret',
            'customer@example.com',
            'another@example.com',
            '+65 9123 4567',
            '+1 415 555 0100',
        ):
            self.assertNotIn(secret, rendered)
        self.assertIn('[已隐藏]', rendered)
        self.assertIn('[邮箱已隐藏]', rendered)
        self.assertIn('[电话已隐藏]', rendered)

    def test_unknown_and_invalid_get_parameters_redirect_to_one_safe_canonical_url(self):
        self.client.force_login(self.marketing)
        endpoint = reverse('console:marketing_events')
        response = self.client.get(
            f'{endpoint}?direction=OUTBOUND&provider=Meta&q=%20pump%20'
            '&status=bogus&page=0&page_size=7&next=https%3A%2F%2Fevil.example&debug=1'
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers['Location'],
            f'{endpoint}?q=pump&direction=outbound&provider=meta',
        )
        self.assertEqual(self.client.get(response.headers['Location']).status_code, 200)

        legal = self.client.get(
            f'{endpoint}?status=failed&direction=inbound&page=2&page_size=50&debug=1'
        )
        self.assertEqual(legal.status_code, 302)
        self.assertEqual(
            legal.headers['Location'],
            f'{endpoint}?direction=inbound&status=failed&page=2&page_size=50',
        )

    def test_due_retry_summary_and_row_actions_share_null_past_future_contract(self):
        null_due = self._outbox(suffix='null', next_attempt_at=None)
        past_due = self._outbox(
            suffix='past', next_attempt_at=self.now - timedelta(minutes=1),
        )
        future = self._outbox(
            suffix='future', next_attempt_at=self.now + timedelta(hours=1),
        )
        maxed = self._outbox(suffix='maxed', next_attempt_at=None, attempts=8)
        disabled_integration = self._integration(
            site=self.site,
            provider=self.google,
            name='Disabled summary',
            integration_type='data_manager',
            public_id='disabled-summary',
            enabled=False,
        )
        disabled = self._outbox(
            integration=disabled_integration,
            suffix='disabled-summary',
            next_attempt_at=None,
        )
        response = self._get()
        workspace = response.context['workspace']
        retry_metric = next(item for item in workspace['summary'] if item['label'] == '可安全重试')
        self.assertEqual(retry_metric['value'], 2)
        by_id = {row['id']: row for row in workspace['rows'] if row['direction'] == 'outbound'}
        self.assertEqual(by_id[null_due.id]['action']['kind'], 'retry')
        self.assertEqual(by_id[past_due.id]['action']['kind'], 'retry')
        self.assertEqual(by_id[future.id]['action']['kind'], 'manual')
        self.assertEqual(by_id[maxed.id]['action']['kind'], 'manual')
        self.assertEqual(by_id[disabled.id]['action']['kind'], 'manual')
        self.assertEqual(
            next(item for item in workspace['summary'] if item['label'] == '需要处理')['href'],
            reverse('console:marketing_events'),
        )
        self.assertEqual(
            retry_metric['href'],
            f'{reverse("console:marketing_events")}?retry=due',
        )

        due_only = self._get({'retry': 'due'})
        self.assertEqual(
            {row['id'] for row in due_only.context['workspace']['rows']},
            {null_due.id, past_due.id},
        )

    def test_exact_outbound_retry_claims_only_requested_due_id(self):
        requested = self._outbox(suffix='requested')
        untouched = self._outbox(suffix='untouched')

        def complete(item):
            item.status = 'sent'
            item.attempts += 1
            item.last_error = ''
            item.next_attempt_at = None
            item.dispatched_at = self.now
            item.save(update_fields=[
                'status', 'attempts', 'last_error', 'next_attempt_at', 'dispatched_at',
            ])
            return True

        self.client.force_login(self.marketing)
        with patch(
            'console.marketing_views.dispatch_claimed_outbox_event',
            side_effect=complete,
        ) as dispatch:
            response = self.client.post(
                reverse('console:marketing_outbox_retry', args=[requested.id]),
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(dispatch.call_args.args[0].id, requested.id)
        requested.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual(requested.status, 'sent')
        self.assertEqual(untouched.status, 'failed')
        self.assertTrue(AuditLog.objects.filter(
            action='outbox_retry_completed', entity_id=requested.id,
        ).exists())

    def test_outbound_future_disabled_and_cross_site_retries_fail_closed(self):
        future = self._outbox(
            suffix='future-api', next_attempt_at=self.now + timedelta(hours=1),
        )
        disabled_integration = self._integration(
            site=self.site,
            provider=self.google,
            name='Disabled Google',
            integration_type='data_manager',
            public_id='disabled',
            enabled=False,
        )
        disabled = self._outbox(integration=disabled_integration, suffix='disabled')
        cross_site = self._outbox(
            integration=self.other_outbound,
            lead=self.other_lead,
            suffix='cross-api',
        )
        self.client.force_login(self.marketing)
        with patch('console.marketing_views.dispatch_claimed_outbox_event') as dispatch:
            self.assertEqual(self.client.post(
                reverse('console:marketing_outbox_retry', args=[future.id]),
            ).status_code, 409)
            self.assertEqual(self.client.post(
                reverse('console:marketing_outbox_retry', args=[disabled.id]),
            ).status_code, 409)
            self.assertEqual(self.client.post(
                reverse('console:marketing_outbox_retry', args=[cross_site.id]),
            ).status_code, 404)
        dispatch.assert_not_called()

    def test_legacy_outbox_mutations_are_not_a_marketing_ops_bypass(self):
        due = self._outbox(suffix='legacy-role-gate')
        self.client.force_login(self.marketing)

        self.assertEqual(
            self.client.post(
                reverse('console:lead_outbox_dispatch', args=[due.id]),
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse('console:lead_outbox_dispatch_pending'),
            ).status_code,
            403,
        )

    def test_legacy_single_alias_uses_the_strict_exact_retry_contract(self):
        requested = self._outbox(suffix='legacy-due')
        future = self._outbox(
            suffix='legacy-future', next_attempt_at=self.now + timedelta(hours=1),
        )
        maxed = self._outbox(suffix='legacy-maxed', attempts=8)
        disabled_integration = self._integration(
            site=self.site,
            provider=self.google,
            name='Legacy disabled integration',
            integration_type='data_manager',
            public_id='legacy-disabled',
            enabled=False,
        )
        disabled = self._outbox(
            integration=disabled_integration,
            suffix='legacy-disabled',
        )

        def complete(item):
            item.status = 'sent'
            item.attempts += 1
            item.last_error = ''
            item.next_attempt_at = None
            item.dispatched_at = self.now
            item.save(update_fields=[
                'status', 'attempts', 'last_error', 'next_attempt_at', 'dispatched_at',
            ])
            return True

        self.client.force_login(self.admin)
        with patch(
            'console.marketing_views.dispatch_claimed_outbox_event',
            side_effect=complete,
        ) as dispatch:
            response = self.client.post(
                reverse('console:lead_outbox_dispatch', args=[requested.id]),
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['ok'])
            for blocked in (future, maxed, disabled):
                blocked_response = self.client.post(
                    reverse('console:lead_outbox_dispatch', args=[blocked.id]),
                )
                self.assertEqual(blocked_response.status_code, 409)

        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(dispatch.call_args.args[0].id, requested.id)

    def test_legacy_single_alias_redacts_errors_and_batch_alias_is_gone(self):
        outbox = self._outbox(
            suffix='legacy-redaction',
            last_error=(
                'token=old-secret customer@example.com +65 9123 4567'
            ),
        )

        def fail(item):
            item.status = 'failed'
            item.attempts += 1
            item.last_error = (
                'Authorization: Bearer new-secret payload='
                '{"email":"another@example.com","phone":"+1 415 555 0100"}'
            )
            item.next_attempt_at = self.now + timedelta(minutes=2)
            item.save(update_fields=[
                'status', 'attempts', 'last_error', 'next_attempt_at',
            ])
            return False

        self.client.force_login(self.admin)
        with patch(
            'console.marketing_views.dispatch_claimed_outbox_event',
            side_effect=fail,
        ):
            response = self.client.post(
                reverse('console:lead_outbox_dispatch', args=[outbox.id]),
            )
        self.assertEqual(response.status_code, 502)
        rendered = response.content.decode('utf-8')
        audit_text = str(list(
            AuditLog.objects.filter(entity_id=outbox.id).values(
                'before_json', 'after_json', 'metadata_json',
            )
        ))
        for secret in (
            'old-secret', 'new-secret', 'customer@example.com',
            'another@example.com', '+65 9123 4567', '+1 415 555 0100',
        ):
            self.assertNotIn(secret, rendered)
            self.assertNotIn(secret, audit_text)

        with patch('leads.services.dispatch_pending_outbox') as dispatch_pending:
            retired = self.client.post(
                reverse('console:lead_outbox_dispatch_pending'),
            )
        self.assertEqual(retired.status_code, 410)
        self.assertEqual(retired.json()['code'], 'legacy_batch_dispatch_retired')
        dispatch_pending.assert_not_called()

    def test_exact_inbound_retry_is_idempotent_and_second_claim_conflicts(self):
        receipt = self._inbound(suffix='idempotent', submission=self.lead, payload={})
        lead_count = LeadSubmission.objects.count()
        self.client.force_login(self.marketing)
        endpoint = reverse('console:marketing_inbound_retry', args=[receipt.id])
        first = self.client.post(endpoint)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()['ok'])
        receipt.refresh_from_db()
        self.assertEqual(receipt.status, 'processed')
        self.assertEqual(receipt.submission_id, self.lead.id)
        self.assertEqual(LeadSubmission.objects.count(), lead_count)
        self.assertEqual(self.client.post(endpoint).status_code, 409)
        self.assertEqual(LeadSubmission.objects.count(), lead_count)

    def test_meta_retry_reuses_durable_submission_after_pre_link_crash(self):
        self.inbound.config_json = {
            'form_code': self.form.code,
            'contact_consent_confirmed': True,
            'marketing_consent_confirmed': True,
            'queue_initial_crm_event': True,
        }
        self.inbound.save(update_fields=['config_json'])
        receipt = self._inbound(suffix='crash-window', payload={
            'leadgen_id': 'leadgen-crash-window',
            'page_id': self.inbound.public_id,
        })
        LeadInboundEvent.objects.filter(pk=receipt.pk).update(
            status='processing', attempts=2, updated_at=self.now,
        )
        receipt.refresh_from_db()
        provider_payload = {
            'id': 'leadgen-crash-window',
            'created_time': self.now.isoformat(),
            'field_data': [
                {'name': 'full_name', 'values': ['Crash Window Lead']},
                {'name': 'email', 'values': ['crash-window@example.com']},
            ],
        }
        original_save = LeadInboundEvent.save

        def crash_before_link(instance, *args, **kwargs):
            if instance.pk == receipt.pk and instance.status == 'processed':
                raise RuntimeError('simulated crash before receipt link')
            return original_save(instance, *args, **kwargs)

        with (
            patch('leads.inbound.fetch_meta_lead', return_value=provider_payload),
            patch.object(LeadInboundEvent, 'save', new=crash_before_link),
            self.assertRaisesRegex(RuntimeError, 'simulated crash'),
        ):
            process_claimed_inbound_receipt(receipt)

        dedupe_key = LeadSubmission.objects.get(
            full_name='Crash Window Lead'
        ).dedupe_key
        self.assertEqual(
            LeadSubmission.objects.filter(dedupe_key=dedupe_key).count(), 1
        )
        receipt.refresh_from_db()
        self.assertIsNone(receipt.submission_id)
        self.assertEqual(receipt.status, 'processing')

        LeadInboundEvent.objects.filter(pk=receipt.pk).update(
            status='failed', updated_at=self.now - timedelta(minutes=20),
        )
        with (
            patch('leads.inbound.fetch_meta_lead', return_value=provider_payload),
            patch('leads.inbound.send_submission_notification') as notify,
            self.captureOnCommitCallbacks(execute=True),
        ):
            retried, succeeded = retry_inbound_receipt_by_id(
                receipt_id=receipt.pk,
                site_id=self.site.pk,
            )

        self.assertTrue(succeeded)
        retried.refresh_from_db()
        self.assertEqual(retried.status, 'processed')
        self.assertIsNotNone(retried.submission_id)
        self.assertEqual(
            LeadSubmission.objects.filter(dedupe_key=dedupe_key).count(), 1
        )
        self.assertEqual(
            LeadEventOutbox.objects.filter(submission_id=retried.submission_id).count(), 1
        )
        notify.assert_called_once()

    def test_only_stale_inbound_processing_claims_can_be_recovered(self):
        stale = self._inbound(
            suffix='stale-processing',
            status='processing',
            submission=self.lead,
            payload={},
        )
        recent = self._inbound(
            suffix='recent-processing',
            status='processing',
            submission=self.lead,
            payload={},
        )
        stale_updated_at = self.now - timedelta(minutes=16)
        LeadInboundEvent.objects.filter(id=stale.id).update(
            updated_at=stale_updated_at,
        )

        workspace = self._get().context['workspace']
        stale_row = next(
            row for row in workspace['rows'] if row['key'] == f'inbound-{stale.id}'
        )
        self.assertEqual(stale_row['action']['kind'], 'retry')
        self.assertNotIn(
            f'inbound-{recent.id}',
            {row['key'] for row in workspace['rows']},
        )

        self.client.force_login(self.marketing)
        recovered = self.client.post(
            reverse('console:marketing_inbound_retry', args=[stale.id]),
        )
        self.assertEqual(recovered.status_code, 200)
        stale.refresh_from_db()
        self.assertEqual(stale.status, 'processed')
        self.assertEqual(stale.attempts, 2)

        still_owned = self.client.post(
            reverse('console:marketing_inbound_retry', args=[recent.id]),
        )
        self.assertEqual(still_owned.status_code, 409)
        recent.refresh_from_db()
        self.assertEqual((recent.status, recent.attempts), ('processing', 1))

    def test_worker_uses_the_same_stale_inbound_claim_timeout(self):
        stale = self._inbound(
            suffix='worker-stale',
            status='processing',
            submission=self.lead,
            payload={},
        )
        recent = self._inbound(
            suffix='worker-recent',
            status='processing',
            submission=self.lead,
            payload={},
        )
        disabled_integration = self._integration(
            site=self.site,
            provider=self.meta,
            name='Disabled inbound worker',
            integration_type='leadgen',
            public_id='disabled-worker-page',
            enabled=False,
        )
        disabled_stale = self._inbound(
            integration=disabled_integration,
            suffix='worker-disabled-stale',
            status='processing',
            submission=self.lead,
            payload={},
        )
        LeadInboundEvent.objects.filter(id__in=[stale.id, disabled_stale.id]).update(
            updated_at=self.now - timedelta(minutes=16),
        )

        attempted, succeeded = retry_inbound_events(limit=100, max_attempts=8)

        self.assertEqual((attempted, succeeded), (1, 1))
        stale.refresh_from_db()
        recent.refresh_from_db()
        disabled_stale.refresh_from_db()
        self.assertEqual((stale.status, stale.attempts), ('processed', 2))
        self.assertEqual((recent.status, recent.attempts), ('processing', 1))
        self.assertEqual((disabled_stale.status, disabled_stale.attempts), ('failed', 1))

    def test_inbound_claimed_and_cross_site_receipts_fail_closed(self):
        claimed = self._inbound(suffix='claimed', status='processing')
        cross_site = LeadInboundEvent.objects.create(
            integration=self.other_outbound,
            provider_code='google',
            event_type='leadgen',
            external_event_id='cross-site-receipt',
            payload_json={},
            status='failed',
            attempts=1,
            last_error='failed',
            received_at=self.now,
        )
        self.client.force_login(self.marketing)
        self.assertEqual(self.client.post(
            reverse('console:marketing_inbound_retry', args=[claimed.id]),
        ).status_code, 409)
        self.assertEqual(self.client.post(
            reverse('console:marketing_inbound_retry', args=[cross_site.id]),
        ).status_code, 404)

    def test_inbound_retry_rejects_a_receipt_bound_to_the_wrong_adapter(self):
        receipt = self._inbound(
            suffix='wrong-adapter', submission=self.lead, payload={},
        )
        self.inbound.integration_type = 'pixel'
        self.inbound.save(update_fields=['integration_type'])
        self.client.force_login(self.marketing)

        with patch('leads.inbound.process_claimed_inbound_receipt') as process:
            response = self.client.post(
                reverse('console:marketing_inbound_retry', args=[receipt.id]),
            )

        self.assertEqual(response.status_code, 409)
        receipt.refresh_from_db()
        self.assertEqual((receipt.status, receipt.attempts), ('failed', 1))
        process.assert_not_called()

    def test_retry_claim_rolls_back_when_audit_intent_cannot_be_written(self):
        inbound = self._inbound(suffix='audit-intent', submission=self.lead, payload={})
        outbound = self._outbox(suffix='audit-intent')
        self.client.force_login(self.marketing)

        with (
            patch(
                'console.marketing_event_views.record_audit',
                side_effect=RuntimeError('audit unavailable'),
            ),
            patch('leads.inbound.process_claimed_inbound_receipt') as process_inbound,
            self.assertRaises(RuntimeError),
        ):
            self.client.post(
                reverse('console:marketing_inbound_retry', args=[inbound.id]),
            )
        inbound.refresh_from_db()
        self.assertEqual((inbound.status, inbound.attempts), ('failed', 1))
        process_inbound.assert_not_called()

        with (
            patch(
                'console.marketing_views.record_audit',
                side_effect=RuntimeError('audit unavailable'),
            ),
            patch('console.marketing_views.dispatch_claimed_outbox_event') as dispatch,
            self.assertRaises(RuntimeError),
        ):
            self.client.post(
                reverse('console:marketing_outbox_retry', args=[outbound.id]),
            )
        outbound.refresh_from_db()
        self.assertEqual((outbound.status, outbound.attempts), ('failed', 1))
        dispatch.assert_not_called()

        stale = self._inbound(
            suffix='stale-audit-intent',
            status='processing',
            submission=self.lead,
            payload={},
        )
        original_updated_at = self.now - timedelta(minutes=16)
        LeadInboundEvent.objects.filter(id=stale.id).update(
            updated_at=original_updated_at,
        )
        with (
            patch(
                'console.marketing_event_views.record_audit',
                side_effect=RuntimeError('audit unavailable'),
            ),
            patch('leads.inbound.process_claimed_inbound_receipt') as process_inbound,
            self.assertRaises(RuntimeError),
        ):
            self.client.post(
                reverse('console:marketing_inbound_retry', args=[stale.id]),
            )
        stale.refresh_from_db()
        self.assertEqual((stale.status, stale.attempts), ('processing', 1))
        self.assertEqual(stale.updated_at, original_updated_at)
        process_inbound.assert_not_called()

    def test_initial_attention_filtered_and_load_error_states_are_distinct(self):
        initial = self._get()
        self.assertContains(initial, '接入尚未产生事件')

        self._outbox(status='sent', suffix='healthy')
        attention_clear = self._get()
        self.assertContains(attention_clear, '当前没有需要处理的事件')
        filtered = self._get({'status': 'all', 'q': 'does-not-exist'})
        self.assertContains(filtered, '没有匹配的事件')
        self.assertContains(filtered, '清除筛选')

        with patch(
            'console.marketing_event_views.build_marketing_event_workspace',
            side_effect=DatabaseError('database offline'),
        ):
            failed = self._get()
        self.assertEqual(failed.status_code, 503)
        self.assertContains(failed, '事件队列暂时无法读取', status_code=503)
        self.assertContains(failed, '重新载入', status_code=503)

    def test_template_has_named_table_controls_and_redacts_sensitive_errors(self):
        self._outbox(
            suffix='secret',
            last_error=(
                'Authorization: Bearer super-secret token=abc123 '
                'customer@example.com +65 9123 4567 '
                'payload={"full_name":"Alice Doe"}'
            ),
        )
        response = self._get()
        body = response.content.decode('utf-8')
        self.assertIn('<caption class="visually-hidden">', body)
        self.assertIn('scope="col"', body)
        self.assertIn('placeholder="事件名、精确 ID 或平台接入"', body)
        self.assertIn('aria-label="精确重试 Outbox #', body)
        self.assertIn('已隐藏', body)
        self.assertNotIn('super-secret', body)
        self.assertNotIn('abc123', body)
        self.assertNotIn('customer@example.com', body)
        self.assertNotIn('+65 9123 4567', body)
        self.assertNotIn('Alice Doe', body)
        self.assertNotIn('查看线索', body)
