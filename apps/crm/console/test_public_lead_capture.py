from __future__ import annotations

import copy
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.access import GROUP_BY_ROLE, ROLE_SALES, ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN
from console.models import AuditLog
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
from leads.services import clean_submission_input, record_initial_consent_snapshot
from marketing.models import CanonicalEvent, MarketingIntegration, MarketingProvider
from sitecore.models import Cta, PageRoute, Site, SiteLocale


class PublicLeadCaptureVerticalTests(TestCase):
    """Isolated browser-contract-to-CRM tests with all external delivery disabled."""

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
        self.now = timezone.now().replace(microsecond=0)
        sales_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES])
        manager_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES_MANAGER])
        admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])

        self.sales = get_user_model().objects.create_user(
            username='vertical-sales',
            email='vertical-sales@example.test',
            password='test-password',
        )
        self.sales.groups.add(sales_group)
        self.manager = get_user_model().objects.create_user(
            username='vertical-manager',
            email='vertical-manager@example.test',
            password='test-password',
        )
        self.manager.groups.add(manager_group)
        self.admin = get_user_model().objects.create_user(
            username='vertical-admin',
            email='vertical-admin@example.test',
            password='test-password',
        )
        self.admin.groups.add(admin_group)

        self.site = Site.objects.create(
            code='siteos_demo',
            name='SiteOS Demo',
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
        SalesTeamMember.objects.create(team=self.team, user=self.sales, membership_role='member')
        SalesTeamMember.objects.create(team=self.team, user=self.manager, membership_role='manager')
        self.form = LeadFormDefinition.objects.create(
            site=self.site,
            locale=None,
            code='project-inquiry',
            name='Project inquiry',
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

    def valid_payload(self, *, suffix='1', marketing=False):
        return {
            'full_name': f'Vertical Tester {suffix}',
            'email': f'vertical-{suffix}@example.test',
            'phone': '',
            'company': f'Vertical Test Company {suffix}',
            'country': 'United Arab Emirates',
            'message': f'Isolated integration inquiry {suffix}',
            'source_url': 'https://example.test/?utm_source=isolated&utm_campaign=never-send',
            'referrer_url': 'https://referrer.test/path?fbclid=never-send',
            'route_path': '/',
            'client_event_id': f'isolated-event-{suffix}',
            'fbp': 'fb.1.never-send',
            'fbc': 'fb.1.never-send-click',
            'form_started_at': (timezone.now() - timedelta(seconds=5)).isoformat(),
            'website': '',
            'utm': {
                'utm_source': 'isolated',
                'utm_medium': 'test',
                'utm_campaign': 'never-send',
            },
            'consent': {
                'contact': True,
                'privacy_notice': True,
                'marketing': marketing,
                'policy_version': 'test-only',
            },
            'extra_fields': {
                'inquiry_type': 'new_line',
                'product_category': 'water',
                'capacity': '12000 BPH',
                'site_language': 'en',
            },
        }

    def post_payload(self, payload, *, remote_addr='127.0.0.1'):
        return self.client.post(
            self.submit_url,
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR=remote_addr,
            HTTP_USER_AGENT='NewCrown isolated vertical test',
        )

    def test_valid_marketing_false_submission_persists_and_is_visible_to_manager(self):
        payload = self.valid_payload(marketing=False)
        with (
            patch('leads.notifications.send_submission_notification', return_value=False) as notify,
            patch('leads.services.dispatch_outbox_event') as dispatch,
        ):
            response = self.post_payload(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertFalse(body['duplicate'])
        self.assertFalse(body['notification_sent'])
        self.assertEqual(body['queued_events'], 0)
        self.assertEqual(body['dispatch_attempted'], 0)
        self.assertEqual(body['dispatch_succeeded'], 0)
        notify.assert_called_once()
        dispatch.assert_not_called()

        submission = LeadSubmission.objects.get()
        self.assertEqual(body['submission_id'], submission.id)
        self.assertEqual(body['submission_key'], str(submission.submission_key))
        self.assertEqual(submission.stage, 'new')
        self.assertEqual(submission.source_channel, 'referral')
        self.assertEqual(submission.team, self.team)
        self.assertIsNone(submission.assignee)
        self.assertEqual(submission.payload_json['product_category'], 'water')
        self.assertEqual(submission.payload_json['capacity'], '12000 BPH')
        self.assertEqual(submission.utm_json, {})
        self.assertEqual(submission.identifiers_json['fbp'], '')
        self.assertEqual(submission.identifiers_json['fbc'], '')
        self.assertEqual(submission.identifiers_json['client_event_id'], 'isolated-event-1')
        self.assertNotIn('utm_', submission.source_url)
        self.assertNotIn('fbclid', submission.referrer_url)
        self.assertEqual(LeadEventOutbox.objects.count(), 0)

        consent = {
            record.purpose: record.decision
            for record in ConsentRecord.objects.filter(submission=submission)
        }
        self.assertEqual(
            consent,
            {'contact': 'granted', 'privacy_notice': 'granted', 'marketing': 'denied'},
        )

        self.client.force_login(self.manager)
        detail = self.client.get(reverse('console:lead_workspace_detail_v2', args=[submission.id]))
        listing = self.client.get(reverse('console:lead_workspace_v2'), follow=True)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(listing.status_code, 200)
        self.assertContains(detail, submission.full_name)
        self.assertContains(detail, submission.company)
        self.assertContains(detail, submission.country)
        self.assertContains(listing, submission.company)

        self.client.force_login(self.sales)
        out_of_scope = self.client.get(reverse('console:lead_workspace_detail_v2', args=[submission.id]))
        self.assertEqual(out_of_scope.status_code, 404)

    def test_duplicate_returns_original_identity_without_second_business_effect(self):
        payload = self.valid_payload(marketing=False)
        with (
            patch('leads.notifications.send_submission_notification', return_value=False) as notify,
            patch('leads.services.dispatch_outbox_event') as dispatch,
        ):
            first = self.post_payload(payload)
            second = self.post_payload(payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_body = first.json()
        second_body = second.json()
        self.assertFalse(first_body['duplicate'])
        self.assertTrue(second_body['duplicate'])
        self.assertEqual(second_body['submission_id'], first_body['submission_id'])
        self.assertEqual(second_body['submission_key'], first_body['submission_key'])
        self.assertEqual(LeadSubmission.objects.count(), 1)
        self.assertEqual(ConsentRecord.objects.count(), 3)
        self.assertEqual(LeadEventOutbox.objects.count(), 0)
        notify.assert_called_once()
        dispatch.assert_not_called()

    def test_invalid_payload_matrix_fails_closed_without_persistence(self):
        invalid_payloads = {}

        missing_contact = self.valid_payload(suffix='missing-contact')
        missing_contact['email'] = ''
        missing_contact['phone'] = ''
        invalid_payloads['email-and-phone-empty'] = missing_contact

        missing_name = self.valid_payload(suffix='missing-name')
        missing_name['full_name'] = ''
        invalid_payloads['missing-name'] = missing_name

        missing_company = self.valid_payload(suffix='missing-company')
        missing_company['company'] = ''
        invalid_payloads['missing-company'] = missing_company

        missing_country = self.valid_payload(suffix='missing-country')
        missing_country['country'] = ''
        invalid_payloads['missing-country'] = missing_country

        missing_product = self.valid_payload(suffix='missing-product')
        missing_product['extra_fields'].pop('product_category')
        invalid_payloads['missing-product'] = missing_product

        missing_capacity = self.valid_payload(suffix='missing-capacity')
        missing_capacity['extra_fields'].pop('capacity')
        invalid_payloads['missing-capacity'] = missing_capacity

        bad_email = self.valid_payload(suffix='bad-email')
        bad_email['email'] = 'not-an-email'
        invalid_payloads['bad-email'] = bad_email

        bad_phone = self.valid_payload(suffix='bad-phone')
        bad_phone['email'] = ''
        bad_phone['phone'] = '123'
        invalid_payloads['bad-phone'] = bad_phone

        honeypot = self.valid_payload(suffix='honeypot')
        honeypot['website'] = 'bot-filled.example'
        invalid_payloads['honeypot'] = honeypot

        too_fast = self.valid_payload(suffix='too-fast')
        too_fast['form_started_at'] = timezone.now().isoformat()
        invalid_payloads['too-fast'] = too_fast

        too_many_links = self.valid_payload(suffix='links')
        too_many_links['message'] = 'https://one.test https://two.test https://three.test'
        invalid_payloads['too-many-links'] = too_many_links

        without_contact_consent = self.valid_payload(suffix='consent')
        without_contact_consent['consent']['contact'] = False
        invalid_payloads['contact-consent-false'] = without_contact_consent

        with (
            patch('leads.notifications.send_submission_notification') as notify,
            patch('leads.services.dispatch_outbox_event') as dispatch,
        ):
            for label, payload in invalid_payloads.items():
                with self.subTest(label=label):
                    response = self.post_payload(copy.deepcopy(payload))
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(response.json()['ok'])

            invalid_json = self.client.post(
                self.submit_url,
                data='{',
                content_type='application/json',
                REMOTE_ADDR='127.0.0.1',
            )
            non_object_json = self.client.post(
                self.submit_url,
                data='[]',
                content_type='application/json',
                REMOTE_ADDR='127.0.0.1',
            )

        self.assertEqual(invalid_json.status_code, 400)
        self.assertEqual(non_object_json.status_code, 400)
        self.assertEqual(LeadSubmission.objects.count(), 0)
        self.assertEqual(ConsentRecord.objects.count(), 0)
        self.assertEqual(LeadEventOutbox.objects.count(), 0)
        notify.assert_not_called()
        dispatch.assert_not_called()


    @override_settings(SITEOS_LEAD_RATE_LIMIT_COUNT=2, SITEOS_LEAD_RATE_LIMIT_MINUTES=10)
    def test_rate_limit_returns_429_and_does_not_create_rejected_row(self):
        with (
            patch('leads.notifications.send_submission_notification', return_value=False) as notify,
            patch('leads.services.dispatch_outbox_event') as dispatch,
        ):
            first = self.post_payload(self.valid_payload(suffix='rate-1'))
            second = self.post_payload(self.valid_payload(suffix='rate-2'))
            rejected = self.post_payload(self.valid_payload(suffix='rate-3'))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(rejected['Retry-After'], '600')
        self.assertEqual(rejected.json()['code'], 'rate_limited')
        self.assertEqual(LeadSubmission.objects.count(), 2)
        self.assertEqual(ConsentRecord.objects.count(), 6)
        self.assertEqual(LeadEventOutbox.objects.count(), 0)
        self.assertEqual(notify.call_count, 2)
        dispatch.assert_not_called()

    def test_service_failure_rolls_back_submission_and_consent(self):
        self.client.raise_request_exception = False
        with (
            patch(
                'leads.services.record_initial_consent_snapshot',
                side_effect=RuntimeError('isolated transactional failure'),
            ),
            patch('leads.notifications.send_submission_notification') as notify,
            patch('leads.services.dispatch_outbox_event') as dispatch,
        ):
            response = self.post_payload(self.valid_payload(suffix='rollback'))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(LeadSubmission.objects.count(), 0)
        self.assertEqual(ConsentRecord.objects.count(), 0)
        self.assertEqual(LeadEventOutbox.objects.count(), 0)
        notify.assert_not_called()
        dispatch.assert_not_called()

    def test_locked_recheck_collapses_competing_insert_to_one_submission(self):
        """Deterministically inject a competing commit between the fast check and locked recheck."""
        payload = self.valid_payload(suffix='race', marketing=False)
        cleaned = clean_submission_input(payload, form_definition=self.form)
        competing = {}

        def insert_competing_submission(dedupe_key):
            created = LeadSubmission.objects.create(
                form=self.form,
                site=self.site,
                locale=self.locale,
                team=self.team,
                stage='new',
                full_name=cleaned['full_name'],
                email=cleaned['email'],
                phone=cleaned['phone'],
                company=cleaned['company'],
                country=cleaned['country'],
                message=cleaned['message'],
                source_url=cleaned['source_url'],
                referrer_url=cleaned['referrer_url'],
                source_channel=cleaned['source_channel'],
                source_detail=cleaned['source_detail'],
                client_ip='127.0.0.1',
                user_agent='competing isolated request',
                buyer_value=cleaned['buyer_value'],
                buyer_currency=cleaned['buyer_currency'],
                payload_json=cleaned['payload_json'],
                identifiers_json=cleaned['identifiers_json'],
                utm_json=cleaned['utm_json'],
                consent_json=cleaned['consent_json'],
                config_json=cleaned['config_json'],
                contact_key=cleaned['contact_key'],
                dedupe_key=dedupe_key,
                notification_status='pending',
                submitted_at=timezone.now(),
                stage_updated_at=timezone.now(),
            )
            record_initial_consent_snapshot(created, source='website_form')
            competing['submission'] = created

        with (
            patch('leads.services._acquire_dedupe_lock', side_effect=insert_competing_submission),
            patch('leads.notifications.send_submission_notification') as notify,
            patch('leads.services.dispatch_outbox_event') as dispatch,
        ):
            response = self.post_payload(payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['duplicate'])
        self.assertEqual(body['submission_id'], competing['submission'].id)
        self.assertEqual(body['submission_key'], str(competing['submission'].submission_key))
        self.assertEqual(LeadSubmission.objects.count(), 1)
        self.assertEqual(ConsentRecord.objects.count(), 3)
        self.assertEqual(LeadEventOutbox.objects.count(), 0)
        notify.assert_not_called()
        dispatch.assert_not_called()
