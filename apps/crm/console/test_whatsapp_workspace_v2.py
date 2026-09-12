from __future__ import annotations

import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.access import GROUP_BY_ROLE, ROLE_MARKETING_OPS, ROLE_SALES
from console.models import AuditLog, ContentAccessGrant
from leads.models import (
    Activity,
    Company,
    Contact,
    LeadConversion,
    LeadEventOutbox,
    LeadFormDefinition,
    LeadInboundEvent,
    LeadSubmission,
    Opportunity,
    SalesTeam,
    SalesTeamMember,
    Task,
    WhatsAppConversation,
    WhatsAppDeliveryEvent,
    WhatsAppMedia,
    WhatsAppMessage,
    WhatsAppTemplate,
)
from marketing.models import CanonicalEvent, MarketingIntegration, MarketingProvider
from sitecore.models import Cta, PageRoute, Site, SiteLocale


class WhatsAppWorkspaceV2Tests(TestCase):
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        ContentAccessGrant,
        CanonicalEvent,
        Cta,
        MarketingProvider,
        MarketingIntegration,
        LeadFormDefinition,
        SalesTeam,
        SalesTeamMember,
        LeadSubmission,
        LeadInboundEvent,
        LeadEventOutbox,
        Activity,
        Company,
        Contact,
        Opportunity,
        LeadConversion,
        Task,
        WhatsAppTemplate,
        WhatsAppConversation,
        WhatsAppMessage,
        WhatsAppMedia,
        WhatsAppDeliveryEvent,
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
        self.runtime = Path(settings.BASE_DIR) / '.runtime' / 'test-wa-media' / uuid.uuid4().hex
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.runtime, True)
        self.settings_override = override_settings(
            SITEOS_WHATSAPP_MEDIA_ROOT=self.runtime,
            SITEOS_WHATSAPP_INLINE_MOCK_DISPATCH=True,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        now = timezone.now()
        self.sales_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES])
        self.marketing_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_MARKETING_OPS])
        User = get_user_model()
        self.sales = User.objects.create_user(username='wa-sales', password='test-password')
        self.sales.groups.add(self.sales_group)
        self.marketing = User.objects.create_user(username='wa-marketing', password='test-password')
        self.marketing.groups.add(self.marketing_group)
        self.site = Site.objects.create(
            code='siteos_demo', name='Demo', default_locale='en', enabled=True, config_json={},
        )
        self.locale = SiteLocale.objects.create(
            site=self.site, locale_code='en', label='English', direction='ltr',
            is_default=True, enabled=True, sort_order=10,
        )
        self.provider = MarketingProvider.objects.create(
            code='whatsapp', name='WhatsApp', enabled=True, capabilities={},
        )
        self.integration = MarketingIntegration.objects.create(
            site=self.site,
            provider=self.provider,
            name='WhatsApp local mock',
            integration_type='cloud_api',
            public_id='15550000000',
            consent_category='service',
            enabled=True,
            config_json={'delivery_mode': 'mock'},
        )
        self.form = LeadFormDefinition.objects.create(
            site=self.site,
            locale=self.locale,
            code='wa-inbound',
            name='WhatsApp inbound',
            category='sales',
            channel='whatsapp',
            form_schema={},
            config_json={},
        )
        self.team = SalesTeam.objects.create(site=self.site, code='sales', name='Sales', enabled=True)
        SalesTeamMember.objects.create(team=self.team, user=self.sales, membership_role='member')
        self.lead = LeadSubmission.objects.create(
            form=self.form,
            site=self.site,
            locale=self.locale,
            assignee=self.sales,
            team=self.team,
            full_name='Omar Al-Farsi',
            phone='+971500000001',
            company='Al Noor Drinks',
            country='Saudi Arabia',
            source_channel='whatsapp',
            payload_json={}, identifiers_json={}, utm_json={}, consent_json={}, config_json={},
            submitted_at=now - timedelta(hours=1),
            stage_updated_at=now - timedelta(hours=1),
        )
        self.conversation = WhatsAppConversation.objects.create(
            integration=self.integration,
            site=self.site,
            submission=self.lead,
            owner_user=self.sales,
            team=self.team,
            external_contact_id='971500000001',
            display_name='Omar Al-Farsi',
            status='open',
            unread_count=1,
            last_message_preview='Need a beverage line',
            last_message_at=now - timedelta(minutes=5),
            last_inbound_at=now - timedelta(minutes=5),
            service_window_expires_at=now + timedelta(hours=23),
            metadata_json={},
        )
        self.inbound = WhatsAppMessage.objects.create(
            conversation=self.conversation,
            direction='inbound',
            message_type='text',
            external_message_id='wamid.test.inbound',
            sender_id='971500000001',
            recipient_id='15550000000',
            body='Need a beverage line',
            status='received',
            payload_json={},
            provider_timestamp=now - timedelta(minutes=5),
        )
        self.template = WhatsAppTemplate.objects.create(
            integration=self.integration,
            name='project_follow_up',
            language='en',
            category='utility',
            status='approved',
            components_json=[],
        )
        self.client.force_login(self.sales)

    def test_workspace_renders_normalized_timeline_and_explicit_mock_state(self):
        response = self.client.get(
            reverse('console:whatsapp_workspace_detail_v2', args=[self.conversation.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Omar Al-Farsi')
        self.assertContains(response, 'Need a beverage line')
        self.assertContains(response, '模拟接入')
        self.assertContains(response, '模拟发送')
        self.assertContains(response, 'data-wa-send-form')
        self.assertContains(response, 'data-wa-disposition-form')

    def test_quick_disposition_updates_requirements_and_creates_next_step_atomically(self):
        response = self.client.post(
            reverse('console:lead_disposition_v2', args=[self.lead.id]),
            {
                'quick_action': 'qualified',
                'expected_updated_at': self.lead.updated_at.isoformat(),
                'idempotency_token': 'wa-disposition-qualified-0001',
                'decision_notes': 'Confirmed project, technical fit and purchasing timeline in WhatsApp.',
                'country': 'Saudi Arabia',
                'company': 'Al Noor Drinks',
                'product_category': 'Beverage filling line',
                'capacity': '36,000 bottles/hour',
                'packaging_format': '500 ml PET bottle',
                'project_type': 'New line',
                'purchase_timeline': 'Within 3 months',
                'contact_role': 'Project decision maker',
                'task_due_at': (timezone.now() + timedelta(days=1)).isoformat(),
                'task_title': 'Confirm technical layout',
                'task_priority': 'high',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.stage, 'qualified')
        self.assertEqual(self.lead.payload_json['capacity'], '36,000 bottles/hour')
        self.assertEqual(self.lead.payload_json['purchase_timeline'], 'Within 3 months')
        self.assertTrue(Task.objects.filter(
            submission=self.lead,
            title='Confirm technical layout',
            priority='high',
        ).exists())
        self.assertTrue(Activity.objects.filter(
            submission=self.lead,
            activity_type='whatsapp',
            direction='internal',
        ).exists())

    def test_quick_won_disposition_requires_real_buyer_value(self):
        response = self.client.post(
            reverse('console:lead_disposition_v2', args=[self.lead.id]),
            {
                'quick_action': 'won',
                'expected_updated_at': self.lead.updated_at.isoformat(),
                'idempotency_token': 'wa-disposition-won-value-0001',
                'decision_notes': 'Customer confirmed the order.',
                'country': 'Saudi Arabia',
                'company': 'Al Noor Drinks',
                'product_category': 'Beverage filling line',
                'capacity': '36,000 bottles/hour',
                'packaging_format': '500 ml PET bottle',
                'project_type': 'New line',
                'purchase_timeline': 'This month',
                'contact_role': 'Decision maker',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'quick_buyer_value_required')
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.stage, 'new')

    def test_ctwa_qualified_disposition_queues_one_meta_lead_submitted_event(self):
        meta_provider = MarketingProvider.objects.create(
            code='meta', name='Meta', enabled=True, capabilities={},
        )
        meta_integration = MarketingIntegration.objects.create(
            site=self.site,
            provider=meta_provider,
            name='Meta dataset mock',
            integration_type='pixel',
            public_id='dataset-123',
            consent_category='analytics',
            enabled=True,
            config_json={'validate_only': True},
        )
        LeadSubmission.objects.filter(pk=self.lead.pk).update(
            identifiers_json={
                'whatsapp_id': '971500000001',
                'ctwa_clid': 'ctwa-click-qualified-1',
                'whatsapp_business_account_id': 'waba-123',
                'ctwa_identity_mode': 'waba',
            },
            consent_json={'contact': True, 'marketing': True},
        )
        self.lead.refresh_from_db()
        response = self.client.post(
            reverse('console:lead_disposition_v2', args=[self.lead.id]),
            {
                'quick_action': 'qualified',
                'expected_updated_at': self.lead.updated_at.isoformat(),
                'idempotency_token': 'wa-disposition-capi-0001',
                'decision_notes': 'Confirmed active project and purchase timeline.',
                'country': 'Saudi Arabia',
                'company': 'Al Noor Drinks',
                'product_category': 'Beverage filling line',
                'capacity': '36,000 bottles/hour',
                'packaging_format': '500 ml PET bottle',
                'project_type': 'New line',
                'purchase_timeline': 'Within 3 months',
                'contact_role': 'Project decision maker',
                'task_due_at': (timezone.now() + timedelta(days=1)).isoformat(),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        events = LeadEventOutbox.objects.filter(
            submission=self.lead,
            integration=meta_integration,
        )
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.stage_key, 'qualified')
        self.assertEqual(event.event_name, 'LeadSubmitted')
        self.assertEqual(event.action_source, 'business_messaging')
        self.assertEqual(event.payload_json['event_name'], 'LeadSubmitted')
        self.assertEqual(event.payload_json['user_data'], {
            'ctwa_clid': 'ctwa-click-qualified-1',
            'whatsapp_business_account_id': 'waba-123',
        })

    def test_quick_qualified_disposition_rejects_incomplete_project_contract(self):
        response = self.client.post(
            reverse('console:lead_disposition_v2', args=[self.lead.id]),
            {
                'quick_action': 'qualified',
                'expected_updated_at': self.lead.updated_at.isoformat(),
                'idempotency_token': 'wa-disposition-incomplete-0001',
                'decision_notes': 'Project discussed but packaging is still missing.',
                'country': 'Saudi Arabia',
                'company': 'Al Noor Drinks',
                'product_category': 'Beverage filling line',
                'capacity': '36,000 bottles/hour',
                'packaging_format': '',
                'project_type': 'New line',
                'purchase_timeline': 'Within 3 months',
                'contact_role': 'Project decision maker',
                'task_due_at': (timezone.now() + timedelta(days=1)).isoformat(),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'quick_requirements_incomplete')
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.stage, 'new')

    def test_send_route_is_idempotent_and_mock_dispatches_without_network(self):
        url = reverse('console:whatsapp_send_v2', args=[self.conversation.id])
        data = {
            'mode': 'text',
            'body': 'Thanks. We will review the capacity.',
            'idempotency_token': 'route-test-token-0001',
        }
        first = self.client.post(url, data)
        second = self.client.post(url, data)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.json()['sent'])
        self.assertFalse(second.json()['created'])
        stored = WhatsAppMessage.objects.filter(
            conversation=self.conversation,
            direction='outbound',
            body=data['body'],
        )
        self.assertEqual(stored.count(), 1)
        self.assertEqual(stored.get().status, 'sent')

    def test_closed_window_blocks_freeform_but_allows_approved_utility_template(self):
        WhatsAppConversation.objects.filter(pk=self.conversation.pk).update(
            service_window_expires_at=timezone.now() - timedelta(minutes=1)
        )
        url = reverse('console:whatsapp_send_v2', args=[self.conversation.id])
        blocked = self.client.post(url, {
            'mode': 'text', 'body': 'Hello', 'idempotency_token': 'window-text-token-0001',
        })
        allowed = self.client.post(url, {
            'mode': 'template',
            'template_id': str(self.template.id),
            'idempotency_token': 'window-template-token-001',
        })
        self.assertEqual(blocked.status_code, 400)
        self.assertIn('24', blocked.json()['error'])
        self.assertEqual(allowed.status_code, 201)
        self.assertEqual(allowed.json()['message']['status'], 'sent')

    def test_marketing_role_cannot_access_sales_conversations(self):
        self.client.force_login(self.marketing)
        response = self.client.get(reverse('console:whatsapp_workspace_v2'))
        self.assertEqual(response.status_code, 403)

    def test_private_media_download_is_scoped_and_audited(self):
        path = self.runtime / 'inbound' / 'drawing.pdf'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'%PDF-1.4 test')
        media = WhatsAppMedia.objects.create(
            message=self.inbound,
            external_media_id='media-test-1',
            media_type='document',
            mime_type='application/pdf',
            original_name='drawing.pdf',
            file_size_bytes=path.stat().st_size,
            sha256='a' * 64,
            storage_path=str(path),
            status='ready',
            downloaded_at=timezone.now(),
        )
        correct = reverse(
            'console:whatsapp_media_download_v2',
            args=[self.conversation.id, self.inbound.id, media.id],
        )
        wrong = reverse(
            'console:whatsapp_media_download_v2',
            args=[self.conversation.id, self.inbound.id + 999, media.id],
        )
        response = self.client.get(correct)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(self.client.get(wrong).status_code, 404)
        self.assertTrue(AuditLog.objects.filter(
            action='whatsapp_media_downloaded', entity_id=media.id,
        ).exists())
