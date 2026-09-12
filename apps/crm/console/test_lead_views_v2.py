from __future__ import annotations

import json
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from console.access import (
    GROUP_BY_ROLE,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    assignable_assignee_queryset_for_lead,
)
from console.models import AuditLog
from console.task_versions import task_version_token
from leads.crm_services import cancel_task
from leads.lead_workflow_services import (
    ACTIVITY_SUBJECT_MAX_LENGTH,
    FOLLOW_UP_NOTES_MAX_LENGTH,
    QUALIFICATION_NOTES_MAX_LENGTH,
    TASK_TITLE_MAX_LENGTH,
)
from leads.models import (
    Activity,
    Company,
    CompanyContactPoint,
    CompanyPoolState,
    ConsentRecord,
    Contact,
    CustomerSource,
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


class _DocumentContractParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.h1_count = 0
        self.stylesheets: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'h1':
            self.h1_count += 1
        elif tag == 'link' and values.get('rel') == 'stylesheet':
            self.stylesheets.append(values.get('href', ''))
        elif tag == 'script' and values.get('src'):
            self.scripts.append(values['src'])


class LeadViewsV2ContractTests(TestCase):
    """HTTP, authorization and rendered-shell contract for the v2 lead slice."""

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
        CompanyPoolState,
        CompanyContactPoint,
        CustomerSource,
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
        marketing_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_MARKETING_OPS])
        manager_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SALES_MANAGER])
        admin_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        self.sales = get_user_model().objects.create_user(
            username='lead-v2-sales',
            email='lead-v2-sales@example.com',
            password='test-password',
        )
        self.sales.groups.add(sales_group)
        self.other_sales = get_user_model().objects.create_user(
            username='lead-v2-other-sales',
            email='lead-v2-other-sales@example.com',
            password='test-password',
        )
        self.other_sales.groups.add(sales_group)
        self.marketing = get_user_model().objects.create_user(
            username='lead-v2-marketing',
            email='lead-v2-marketing@example.com',
            password='test-password',
        )
        self.marketing.groups.add(marketing_group)
        self.manager = get_user_model().objects.create_user(
            username='lead-v2-manager',
            email='lead-v2-manager@example.com',
            password='test-password',
        )
        self.manager.groups.add(manager_group)
        self.admin = get_user_model().objects.create_user(
            username='lead-v2-admin',
            email='lead-v2-admin@example.com',
            password='test-password',
        )
        self.admin.groups.add(admin_group)

        self.site = Site.objects.create(
            code='siteos_demo',
            name='SiteOS Demo',
            base_url='https://example.com',
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
        self.form = LeadFormDefinition.objects.create(
            site=self.site,
            locale=self.locale,
            code='sales-inquiry',
            name='Sales inquiry',
            category='sales',
            channel='website',
            scope_type='site',
            scope_value='*',
            status='active',
            contacted_event_name='contacted_lead',
            qualified_event_name='qualified_lead',
            won_event_name='converted',
            capi_enabled=True,
            form_schema={},
            config_json={},
        )
        self.team = SalesTeam.objects.create(
            site=self.site,
            code='primary-sales',
            name='Primary sales',
            enabled=True,
        )
        SalesTeamMember.objects.create(
            team=self.team,
            user=self.sales,
            membership_role='member',
        )
        SalesTeamMember.objects.create(
            team=self.team,
            user=self.other_sales,
            membership_role='member',
        )
        SalesTeamMember.objects.create(
            team=self.team,
            user=self.manager,
            membership_role='manager',
        )
        self.owned_lead = self._lead(
            full_name='Amina Hassan',
            assignee=self.sales,
        )
        self.other_lead = self._lead(
            full_name='Omar Farooq',
            assignee=self.other_sales,
        )

        provider = MarketingProvider.objects.create(
            code='meta',
            name='Meta',
            enabled=True,
            capabilities={},
        )
        MarketingIntegration.objects.create(
            site=self.site,
            provider=provider,
            name='Candidate validation queue',
            integration_type='pixel',
            public_id='1234567890',
            enabled=True,
            consent_category='marketing',
            config_json={'validate_only': True},
        )

    def _lead(self, *, full_name, assignee):
        return LeadSubmission.objects.create(
            form=self.form,
            site=self.site,
            locale=self.locale,
            stage='new',
            assignee=assignee,
            team=self.team,
            full_name=full_name,
            email=f'{full_name.split()[0].lower()}@example.com',
            phone='+971500000000',
            company='Beverage Projects',
            country='AE',
            message='Need a 36,000 BPH line.',
            source_url='https://example.com/contact',
            source_channel='meta_ads',
            source_detail='Candidate campaign',
            client_ip='203.0.113.10',
            user_agent='Candidate test browser',
            buyer_currency='USD',
            payload_json={
                'product_category': 'Beverage production line',
                'capacity': '36,000 BPH',
            },
            identifiers_json={},
            utm_json={},
            consent_json={'marketing': True},
            config_json={},
            follow_up_notes='',
            qualification_score=0,
            qualification_json={},
            qualification_notes='',
            notification_status='sent',
            submitted_at=self.now - timedelta(hours=2),
            stage_updated_at=self.now - timedelta(hours=2),
        )

    def _post_data(self, lead, **overrides):
        lead.refresh_from_db()
        values = {
            'expected_updated_at': lead.updated_at.isoformat(),
            'idempotency_token': f'dispose-{lead.pk}-candidate',
            'continue_action': 'stay',
            'current_url': reverse('console:lead_workspace_detail_v2', args=[lead.pk]),
            'next_url': reverse('console:lead_workspace_v2'),
            'stage': 'contacted',
            'qualification_contactable': '1',
            'qualification_company_verified': '1',
            'qualification_project_confirmed': '1',
            'qualification_notes': 'Validated during the first response.',
            'activity_type': 'call',
            'activity_direction': 'outbound',
            'activity_subject': 'First qualification call',
            'activity_body': 'Confirmed project scope and the next action.',
            'follow_up_notes': 'Send the technical questionnaire.',
        }
        values.update(overrides)
        return values

    def _post_json(self, lead, **overrides):
        return self.client.post(
            reverse('console:lead_disposition_v2', args=[lead.pk]),
            self._post_data(lead, **overrides),
            HTTP_ACCEPT='application/json',
        )

    def _conversion_counts(self, lead):
        return {
            'companies': Company.objects.filter(site=self.site).count(),
            'contacts': Contact.objects.filter(site=self.site).count(),
            'opportunities': Opportunity.objects.filter(source_submission=lead).count(),
            'conversions': LeadConversion.objects.filter(submission=lead).count(),
            'activities': Activity.objects.filter(submission=lead).count(),
            'tasks': Task.objects.filter(submission=lead).count(),
            'audits': AuditLog.objects.filter(
                entity_table='lead_submission',
                entity_id=lead.pk,
                action='lead_converted',
            ).count(),
        }

    def _prepare_conversion(self, lead, *, due_at=None):
        LeadSubmission.objects.filter(pk=lead.pk).update(
            stage='qualified',
            next_follow_up_at=due_at,
        )
        lead.refresh_from_db()

    def test_sales_role_can_open_scoped_list_and_detail(self):
        self.client.force_login(self.sales)

        listing = self.client.get(
            reverse('console:lead_workspace_v2'),
            {'q': 'Amina', 'sort': 'name', 'page_size': '20'},
        )
        redirected = urlsplit(listing.url)
        redirected_query = parse_qs(redirected.query)
        detail = self.client.get(
            reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk])
        )
        out_of_scope = self.client.get(
            reverse('console:lead_workspace_detail_v2', args=[self.other_lead.pk])
        )

        self.assertEqual(listing.status_code, 302)
        self.assertEqual(
            redirected.path,
            reverse(
                'console:lead_workspace_detail_v2',
                args=[self.owned_lead.pk],
            ),
        )
        self.assertEqual(redirected_query['q'], ['Amina'])
        self.assertEqual(redirected_query['sort'], ['name'])
        self.assertEqual(redirected_query['page_size'], ['20'])
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Amina Hassan')
        self.assertNotContains(detail, 'Omar Farooq')
        self.assertEqual(out_of_scope.status_code, 404)

    def test_legacy_get_entry_redirects_only_safe_queue_state_to_v2(self):
        self.client.force_login(self.sales)

        response = self.client.get(
            reverse('console:leads'),
            {
                'tab': 'submissions',
                'q': '  Amina   Hassan  ',
                'stage': 'qualified',
                'lead_locale': 'en',
                'source_channel': 'meta_ads',
                'assignee': str(self.sales.pk),
                'follow_up': 'overdue',
                'page': '2',
                'page_size': '20',
                'next': 'https://attacker.invalid/collect',
                'debug': '1',
            },
        )

        self.assertEqual(response.status_code, 302)
        destination = urlsplit(response.url)
        query = parse_qs(destination.query)
        self.assertEqual(destination.path, reverse('console:lead_workspace_v2'))
        self.assertEqual(query['q'], ['Amina Hassan'])
        self.assertEqual(query['stage'], ['qualified'])
        self.assertEqual(query['language'], ['en'])
        self.assertEqual(query['source'], ['meta_ads'])
        self.assertEqual(query['owner'], [str(self.sales.pk)])
        self.assertEqual(query['sla'], ['overdue'])
        self.assertEqual(query['page'], ['2'])
        self.assertEqual(query['page_size'], ['20'])
        self.assertNotIn('next', query)
        self.assertNotIn('debug', query)

        unrelated_tab = self.client.get(
            reverse('console:leads'),
            {'tab': 'forms', 'q': 'must-not-leak'},
        )
        self.assertEqual(unrelated_tab.status_code, 302)
        self.assertEqual(unrelated_tab.url, reverse('console:lead_workspace_v2'))

        head = self.client.head(reverse('console:leads'))
        self.assertEqual(head.status_code, 302)
        self.assertEqual(head.url, reverse('console:lead_workspace_v2'))

    def test_legacy_non_get_renderer_remains_available_for_compatibility(self):
        self.client.force_login(self.sales)

        response = self.client.post(reverse('console:leads'), {})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'console/_leads_workspace.html')

    def test_conversion_cta_only_renders_for_an_eligible_unconverted_lead(self):
        self.client.force_login(self.sales)

        not_ready = self.client.get(
            reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk])
        )
        self.assertFalse(not_ready.context['workspace']['can_convert'])
        self.assertNotContains(not_ready, 'data-nc-open-conversion')
        self.assertNotContains(not_ready, 'data-nc-conversion-form')

        self._prepare_conversion(self.owned_lead)
        ready = self.client.get(
            reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk])
        )
        self.assertTrue(ready.context['workspace']['can_convert'])
        self.assertContains(ready, 'data-nc-open-conversion')
        self.assertContains(ready, 'data-nc-conversion-form')
        self.assertContains(
            ready,
            reverse('console:lead_convert', args=[self.owned_lead.pk]),
        )
        self.assertContains(ready, 'Beverage Projects - Beverage production line')

        converted = self.client.post(
            reverse('console:lead_convert', args=[self.owned_lead.pk]),
            {'opportunity_name': 'Amina bottling project'},
        )
        self.assertEqual(converted.status_code, 200)
        source = CustomerSource.objects.get(submission=self.owned_lead)
        self.assertEqual(source.source_type, 'website_form')
        self.assertEqual(source.intake_method, 'automatic_receive')
        self.assertEqual(source.attribution_json['source_channel'], 'meta_ads')
        after = self.client.get(
            reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk])
        )
        self.assertFalse(after.context['workspace']['can_convert'])
        self.assertNotContains(after, 'data-nc-open-conversion')
        self.assertContains(after, 'Amina bottling project')

    def test_legacy_conversion_uses_one_active_owner_in_the_lead_team(self):
        due_at = self.now + timedelta(days=1)
        self._prepare_conversion(self.owned_lead, due_at=due_at)
        self.client.force_login(self.sales)

        response = self.client.post(
            reverse('console:lead_convert', args=[self.owned_lead.pk]),
            {'opportunity_name': 'Amina bottling project'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['created'])
        company = Company.objects.get(pk=payload['company_id'])
        contact = Contact.objects.get(pk=payload['contact_id'])
        opportunity = Opportunity.objects.get(pk=payload['opportunity_id'])
        task = Task.objects.get(submission=self.owned_lead)
        for record in (company, contact, opportunity, task):
            self.assertEqual(record.owner_user_id, self.sales.pk)
            self.assertEqual(record.team_id, self.team.pk)
        self.assertEqual(task.due_at, due_at)
        self.assertTrue(
            Activity.objects.filter(
                submission=self.owned_lead,
                activity_type='conversion',
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                entity_table='lead_submission',
                entity_id=self.owned_lead.pk,
                action='lead_converted',
            ).exists()
        )

    def test_meta_instant_form_conversion_is_not_mislabeled_as_website_form(self):
        lead = self._lead(full_name='Meta Native Lead', assignee=self.sales)
        lead.payload_json = {
            **lead.payload_json,
            'origin': 'meta_instant_form',
        }
        lead.source_detail = 'Meta Instant Form'
        lead.save(update_fields=['payload_json', 'source_detail', 'updated_at'])
        self._prepare_conversion(lead)
        self.client.force_login(self.sales)

        response = self.client.post(
            reverse('console:lead_convert', args=[lead.pk]),
            {'opportunity_name': 'Meta native project'},
        )

        self.assertEqual(response.status_code, 200)
        source = CustomerSource.objects.get(submission=lead)
        self.assertEqual(source.source_type, 'meta_native')
        self.assertEqual(source.intake_method, 'automatic_receive')

    def test_legacy_conversion_rejects_inactive_historical_owner_without_any_writes(self):
        inactive_owner = get_user_model().objects.create_user(
            username='inactive-conversion-owner',
            password='test-password',
            is_active=False,
        )
        SalesTeamMember.objects.create(
            team=self.team,
            user=inactive_owner,
            membership_role='member',
        )
        lead = self._lead(full_name='Inactive Conversion Owner', assignee=inactive_owner)
        self._prepare_conversion(lead)
        before = self._conversion_counts(lead)
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('console:lead_convert', args=[lead.pk]),
            {'opportunity_name': 'Must not exist'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('请先重新分配', response.json()['errors']['__all__'][0])
        self.assertEqual(self._conversion_counts(lead), before)

    def test_legacy_conversion_rejects_unassigned_team_lead_instead_of_using_admin(self):
        lead = self._lead(full_name='Unassigned Conversion Lead', assignee=None)
        self._prepare_conversion(lead, due_at=self.now + timedelta(days=1))
        before = self._conversion_counts(lead)
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('console:lead_convert', args=[lead.pk]),
            {'opportunity_name': 'Must not use the administrator'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('负责人不可用', response.json()['errors']['__all__'][0])
        self.assertEqual(self._conversion_counts(lead), before)

    def test_legacy_conversion_rejects_owner_outside_the_lead_team(self):
        secondary_team = SalesTeam.objects.create(
            site=self.site,
            code='conversion-secondary',
            name='Conversion secondary team',
            enabled=True,
        )
        outsider = get_user_model().objects.create_user(
            username='conversion-team-outsider',
            password='test-password',
        )
        SalesTeamMember.objects.create(
            team=secondary_team,
            user=outsider,
            membership_role='member',
        )
        lead = self._lead(full_name='Cross Team Conversion Lead', assignee=outsider)
        self._prepare_conversion(lead)
        before = self._conversion_counts(lead)
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('console:lead_convert', args=[lead.pk]),
            {'opportunity_name': 'Must not cross teams'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('所属销售团队', response.json()['errors']['__all__'][0])
        self.assertEqual(self._conversion_counts(lead), before)

    def test_legacy_conversion_reauthorizes_scope_after_lock(self):
        secondary_team = SalesTeam.objects.create(
            site=self.site,
            code='conversion-race-team',
            name='Conversion race team',
            enabled=True,
        )
        self._prepare_conversion(self.owned_lead)
        before = self._conversion_counts(self.owned_lead)
        self.client.force_login(self.manager)
        manager = LeadSubmission._default_manager
        real_select_for_update = manager.select_for_update
        transfer_attempted = False

        def transfer_before_lock(*args, **kwargs):
            nonlocal transfer_attempted
            transfer_attempted = True
            LeadSubmission.objects.filter(pk=self.owned_lead.pk).update(team=secondary_team)
            return real_select_for_update(*args, **kwargs)

        with patch.object(
            manager,
            'select_for_update',
            side_effect=transfer_before_lock,
        ):
            response = self.client.post(
                reverse('console:lead_convert', args=[self.owned_lead.pk]),
                {'opportunity_name': 'Race must be hidden'},
            )

        self.assertTrue(transfer_attempted)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._conversion_counts(self.owned_lead), before)

    def test_legacy_conversion_audit_failure_rolls_back_every_domain_write(self):
        self._prepare_conversion(
            self.owned_lead,
            due_at=self.now + timedelta(days=1),
        )
        before = self._conversion_counts(self.owned_lead)
        self.client.force_login(self.sales)
        self.client.raise_request_exception = False

        with patch(
            'console.sales_views.record_audit',
            side_effect=RuntimeError('simulated conversion audit failure'),
        ):
            response = self.client.post(
                reverse('console:lead_convert', args=[self.owned_lead.pk]),
                {'opportunity_name': 'Atomic conversion'},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._conversion_counts(self.owned_lead), before)

    def test_marketing_ops_is_rejected_by_middleware_for_every_v2_route(self):
        self.client.force_login(self.marketing)

        responses = (
            self.client.get(reverse('console:lead_workspace_v2')),
            self.client.get(
                reverse(
                    'console:lead_workspace_detail_v2',
                    args=[self.owned_lead.pk],
                )
            ),
            self.client.post(
                reverse('console:lead_disposition_v2', args=[self.owned_lead.pk]),
                {},
            ),
            self.client.post(reverse('console:lead_bulk_assign_v2'), {}),
        )

        self.assertEqual([response.status_code for response in responses], [403, 403, 403, 403])

    def test_sales_cannot_dispose_an_out_of_scope_record(self):
        self.client.force_login(self.sales)

        response = self._post_json(self.other_lead)

        self.assertEqual(response.status_code, 404)
        self.other_lead.refresh_from_db()
        self.assertEqual(self.other_lead.stage, 'new')
        self.assertFalse(Activity.objects.filter(submission=self.other_lead).exists())
        self.assertFalse(Task.objects.filter(submission=self.other_lead).exists())

    def test_success_json_atomically_writes_activity_qualification_task_and_real_queue(self):
        self.client.force_login(self.sales)
        due_at = self.now + timedelta(days=1)

        with self.captureOnCommitCallbacks(execute=True):
            with patch(
                'leads.lead_workflow_services.dispatch_outbox_event',
                return_value=True,
            ) as dispatcher:
                response = self._post_json(
                    self.owned_lead,
                    create_task='1',
                    task_title='Send technical questionnaire',
                    task_due_at=due_at.isoformat(),
                    task_type='follow_up',
                    task_priority='high',
                    task_description='Confirm utility and footprint requirements.',
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['current_updated_at'])
        self.assertEqual(payload['dispatch_scheduled'], 1)
        self.assertEqual(payload['idempotency_mode'], 'database_write_deduplication_only')
        self.assertIn('进入发送队列', payload['message'])

        self.owned_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.stage, 'contacted')
        self.assertEqual(self.owned_lead.qualification_score, 60)
        self.assertEqual(
            self.owned_lead.qualification_json,
            {
                'contactable': True,
                'company_verified': True,
                'project_confirmed': True,
                'technical_fit': False,
                'next_step_confirmed': False,
            },
        )
        activity = Activity.objects.get(submission=self.owned_lead)
        self.assertEqual(activity.subject, 'First qualification call')
        self.assertEqual(activity.metadata_json['workflow'], 'lead_disposition_v2')
        task = Task.objects.get(submission=self.owned_lead)
        self.assertEqual(task.title, 'Send technical questionnaire')
        self.assertEqual(task.status, 'open')
        self.assertEqual(task.owner_user_id, self.sales.pk)
        outbox = LeadEventOutbox.objects.get(submission=self.owned_lead)
        self.assertEqual(outbox.status, 'pending')
        self.assertEqual(outbox.stage_key, 'contacted')
        self.assertEqual(outbox.delivery_mode, 'validation')
        self.assertEqual(AuditLog.objects.filter(action='lead_disposed_v2').count(), 1)
        dispatcher.assert_called_once()

        rendered = self.client.get(
            reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk])
        )
        self.assertEqual(rendered.status_code, 200)
        workspace = rendered.context['workspace']
        self.assertEqual(workspace['task_total'], 1)
        self.assertEqual(workspace['selected']['next_step']['source'], 'task')
        self.assertEqual(
            workspace['selected']['next_step']['title'],
            'Send technical questionnaire',
        )
        self.assertIn(
            'Send technical questionnaire',
            workspace['selected']['next_step']['text'],
        )

    def test_stale_optimistic_lock_returns_409_without_writes(self):
        self.client.force_login(self.sales)
        stale = self.owned_lead.updated_at - timedelta(seconds=1)

        response = self._post_json(
            self.owned_lead,
            expected_updated_at=stale.isoformat(),
        )

        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['code'], 'lead_concurrent_update')
        self.assertTrue(payload['current_updated_at'])
        self.owned_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.stage, 'new')
        self.assertFalse(Activity.objects.filter(submission=self.owned_lead).exists())
        self.assertFalse(Task.objects.filter(submission=self.owned_lead).exists())
        self.assertFalse(LeadEventOutbox.objects.filter(submission=self.owned_lead).exists())

    def test_assignment_change_requires_assign_capability(self):
        self.client.force_login(self.sales)

        response = self._post_json(
            self.owned_lead,
            assignee_id=str(self.other_sales.pk),
        )

        self.assertEqual(response.status_code, 403)
        self.owned_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.assignee_id, self.sales.pk)
        self.assertFalse(Activity.objects.filter(submission=self.owned_lead).exists())

    def test_malformed_assignment_id_is_a_400_contract_error_not_a_500(self):
        self.client.force_login(self.sales)

        response = self._post_json(self.owned_lead, assignee_id='not-an-integer')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_id')
        self.owned_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.assignee_id, self.sales.pk)
        self.assertFalse(Activity.objects.filter(submission=self.owned_lead).exists())

    def test_tampered_buyer_value_and_currency_return_400_without_database_writes(self):
        self.client.force_login(self.sales)
        invalid_cases = (
            ({'buyer_value': '12345678901.23'}, 'buyer_value_precision_invalid'),
            ({'buyer_value': '1.234'}, 'buyer_value_precision_invalid'),
            ({'buyer_value': '-1'}, 'buyer_value_negative'),
            ({'buyer_value': 'Infinity'}, 'buyer_value_not_finite'),
            ({'buyer_value': 'NaN'}, 'buyer_value_not_finite'),
            ({'buyer_currency': 'ABCDEFGHI'}, 'buyer_currency_invalid'),
            ({'buyer_currency': 'USD/JPY'}, 'buyer_currency_invalid'),
            ({'buyer_currency': 'US1'}, 'buyer_currency_invalid'),
        )

        for overrides, expected_code in invalid_cases:
            with self.subTest(overrides=overrides):
                response = self._post_json(self.owned_lead, **overrides)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['code'], expected_code)
                self.owned_lead.refresh_from_db()
                self.assertEqual(self.owned_lead.stage, 'new')
                self.assertIsNone(self.owned_lead.buyer_value)
                self.assertEqual(self.owned_lead.buyer_currency, 'USD')
                self.assertFalse(Activity.objects.filter(submission=self.owned_lead).exists())
                self.assertFalse(Task.objects.filter(submission=self.owned_lead).exists())
                self.assertFalse(
                    AuditLog.objects.filter(
                        entity_table='lead_submission',
                        entity_id=self.owned_lead.pk,
                    ).exists()
                )

    def test_crafted_text_overflow_returns_400_and_rolls_back_the_whole_disposition(self):
        self.client.force_login(self.sales)
        self.owned_lead.refresh_from_db()
        original_updated_at = self.owned_lead.updated_at
        invalid_cases = (
            (
                {'activity_subject': 'x' * (ACTIVITY_SUBJECT_MAX_LENGTH + 1)},
                'activity_subject_too_long',
            ),
            (
                {
                    'create_task': '1',
                    'task_title': 'x' * (TASK_TITLE_MAX_LENGTH + 1),
                    'task_due_at': (self.now + timedelta(days=1)).isoformat(),
                },
                'task_title_too_long',
            ),
            (
                {'follow_up_notes': 'x' * (FOLLOW_UP_NOTES_MAX_LENGTH + 1)},
                'follow_up_notes_too_long',
            ),
            (
                {'qualification_notes': 'x' * (QUALIFICATION_NOTES_MAX_LENGTH + 1)},
                'qualification_notes_too_long',
            ),
        )

        for overrides, expected_code in invalid_cases:
            with self.subTest(expected_code=expected_code):
                response = self._post_json(self.owned_lead, **overrides)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['code'], expected_code)
                self.owned_lead.refresh_from_db()
                self.assertEqual(self.owned_lead.stage, 'new')
                self.assertEqual(self.owned_lead.updated_at, original_updated_at)
                self.assertFalse(Activity.objects.filter(submission=self.owned_lead).exists())
                self.assertFalse(Task.objects.filter(submission=self.owned_lead).exists())
                self.assertFalse(
                    AuditLog.objects.filter(
                        entity_table='lead_submission',
                        entity_id=self.owned_lead.pk,
                    ).exists()
                )

    def test_inactive_historical_owner_cannot_receive_a_new_open_task(self):
        inactive_owner = get_user_model().objects.create_user(
            username='inactive-task-owner',
            password='test-password',
            is_active=False,
        )
        SalesTeamMember.objects.create(
            team=self.team,
            user=inactive_owner,
            membership_role='member',
        )
        legacy_lead = self._lead(full_name='Inactive Task Owner Lead', assignee=inactive_owner)
        self.client.force_login(self.admin)
        response = self._post_json(
            legacy_lead,
            assignee_id=str(inactive_owner.pk),
            create_task='1',
            task_title='Follow up with inactive owner',
            task_due_at=(self.now + timedelta(days=1)).isoformat(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()['code'],
            'task_owner_inactive_requires_reassignment',
        )
        self.assertIn('先重新分配负责人', response.json()['message'])
        legacy_lead.refresh_from_db()
        self.assertEqual(legacy_lead.stage, 'new')
        self.assertEqual(legacy_lead.assignee_id, inactive_owner.pk)
        self.assertFalse(Activity.objects.filter(submission=legacy_lead).exists())
        self.assertFalse(Task.objects.filter(submission=legacy_lead).exists())
        self.assertFalse(
            AuditLog.objects.filter(entity_table='lead_submission', entity_id=legacy_lead.pk).exists()
        )

    def test_task_completion_and_cancellation_recompute_the_lead_follow_up_deadline(self):
        first_due = self.now + timedelta(hours=1)
        second_due = self.now + timedelta(hours=2)
        first = Task.objects.create(
            site=self.site,
            submission=self.owned_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.sales,
            title='First actionable task',
            status='open',
            due_at=first_due,
        )
        second = Task.objects.create(
            site=self.site,
            submission=self.owned_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.sales,
            title='Second actionable task',
            status='in_progress',
            due_at=second_due,
        )
        LeadSubmission.objects.filter(pk=self.owned_lead.pk).update(
            next_follow_up_at=first_due,
        )
        self.client.force_login(self.sales)

        response = self.client.post(
            reverse('console:task_complete', args=[first.pk]),
            {'outcome': 'Done', 'version': task_version_token(first.updated_at)},
        )
        self.assertEqual(response.status_code, 200)
        self.owned_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.next_follow_up_at, second_due)

        response = self.client.post(
            reverse('console:task_complete', args=[second.pk]),
            {'outcome': 'Done', 'version': task_version_token(second.updated_at)},
        )
        self.assertEqual(response.status_code, 200)
        self.owned_lead.refresh_from_db()
        self.assertIsNone(self.owned_lead.next_follow_up_at)

        cancelled_due = self.now + timedelta(hours=3)
        cancelled = Task.objects.create(
            site=self.site,
            submission=self.owned_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.sales,
            title='Cancelled actionable task',
            status='open',
            due_at=cancelled_due,
        )
        LeadSubmission.objects.filter(pk=self.owned_lead.pk).update(
            next_follow_up_at=cancelled_due,
        )
        cancel_task(task_id=cancelled.pk, actor=self.sales, outcome='No longer needed')
        cancelled.refresh_from_db()
        self.owned_lead.refresh_from_db()
        self.assertEqual(cancelled.status, 'canceled')
        self.assertIsNone(self.owned_lead.next_follow_up_at)

    def test_terminal_task_states_cannot_be_rewritten(self):
        canceled_at = self.now - timedelta(hours=2)
        canceled = Task.objects.create(
            site=self.site,
            submission=self.owned_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.sales,
            title='Canceled terminal task',
            status='canceled',
            due_at=self.now + timedelta(hours=1),
            completed_at=canceled_at,
            outcome='Customer withdrew the request',
        )
        self.client.force_login(self.sales)
        before_activity_count = Activity.objects.filter(submission=self.owned_lead).count()
        before_audit_count = AuditLog.objects.filter(
            entity_table='crm_task',
            entity_id=canceled.pk,
        ).count()

        response = self.client.post(
            reverse('console:task_complete', args=[canceled.pk]),
            {
                'outcome': 'Must not reopen the terminal decision',
                'version': task_version_token(canceled.updated_at),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('不能改写历史结果', response.json()['errors']['__all__'][0])
        canceled.refresh_from_db()
        self.assertEqual(canceled.status, 'canceled')
        self.assertEqual(canceled.completed_at, canceled_at)
        self.assertEqual(canceled.outcome, 'Customer withdrew the request')
        self.assertEqual(
            Activity.objects.filter(submission=self.owned_lead).count(),
            before_activity_count,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                entity_table='crm_task',
                entity_id=canceled.pk,
            ).count(),
            before_audit_count,
        )

        completed_at = self.now - timedelta(hours=1)
        completed = Task.objects.create(
            site=self.site,
            submission=self.owned_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.sales,
            title='Completed terminal task',
            status='completed',
            due_at=self.now + timedelta(hours=2),
            completed_at=completed_at,
            outcome='Delivered the quotation',
        )
        with self.assertRaisesMessage(ValidationError, '不能改写为另一终态'):
            cancel_task(
                task_id=completed.pk,
                actor=self.sales,
                outcome='Must not cancel completed work',
            )
        completed.refresh_from_db()
        self.assertEqual(completed.status, 'completed')
        self.assertEqual(completed.completed_at, completed_at)
        self.assertEqual(completed.outcome, 'Delivered the quotation')

    def test_repeating_the_same_terminal_task_request_is_an_audit_free_noop(self):
        completed_at = self.now - timedelta(hours=1)
        task = Task.objects.create(
            site=self.site,
            submission=self.owned_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.sales,
            title='Already completed task',
            status='completed',
            due_at=self.now + timedelta(hours=1),
            completed_at=completed_at,
            outcome='Original immutable outcome',
        )
        self.client.force_login(self.sales)

        response = self.client.post(
            reverse('console:task_complete', args=[task.pk]),
            {
                'outcome': 'Late retry must not overwrite',
                'version': task_version_token(task.updated_at),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['changed'])
        task.refresh_from_db()
        self.assertEqual(task.completed_at, completed_at)
        self.assertEqual(task.outcome, 'Original immutable outcome')
        self.assertFalse(
            Activity.objects.filter(
                submission=self.owned_lead,
                activity_type='task',
                subject__contains='Already completed task',
            ).exists()
        )
        self.assertFalse(
            AuditLog.objects.filter(
                entity_table='crm_task',
                entity_id=task.pk,
                action='crm_task_completed',
            ).exists()
        )

    def test_new_task_keeps_an_existing_earlier_actionable_deadline(self):
        earlier_due = self.now + timedelta(days=1)
        later_due = self.now + timedelta(days=7)
        Task.objects.create(
            site=self.site,
            submission=self.owned_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.sales,
            title='Existing urgent follow-up',
            status='in_progress',
            due_at=earlier_due,
        )
        LeadSubmission.objects.filter(pk=self.owned_lead.pk).update(
            next_follow_up_at=earlier_due,
        )
        self.client.force_login(self.sales)

        response = self._post_json(
            self.owned_lead,
            create_task='1',
            task_title='Later technical follow-up',
            task_due_at=later_due.isoformat(),
        )

        self.assertEqual(response.status_code, 200)
        self.owned_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.next_follow_up_at, earlier_due)
        self.assertEqual(
            list(
                Task.objects.filter(
                    submission=self.owned_lead,
                    status__in=('open', 'in_progress'),
                ).values_list('due_at', flat=True)
            ),
            [earlier_due, later_due],
        )

    def test_task_create_rejects_owner_from_another_managed_team(self):
        secondary_team = SalesTeam.objects.create(
            site=self.site,
            code='secondary-sales',
            name='Secondary sales',
            enabled=True,
        )
        SalesTeamMember.objects.create(
            team=secondary_team,
            user=self.manager,
            membership_role='manager',
        )
        secondary_sales = get_user_model().objects.create_user(
            username='secondary-team-sales',
            email='secondary-team-sales@example.com',
            password='test-password',
        )
        secondary_sales.groups.add(
            Group.objects.get(name=GROUP_BY_ROLE[ROLE_SALES])
        )
        SalesTeamMember.objects.create(
            team=secondary_team,
            user=secondary_sales,
            membership_role='member',
        )
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse('console:task_create'),
            {
                'target_type': 'lead',
                'target_id': str(self.owned_lead.pk),
                'title': 'Cross-team task must be rejected',
                'task_type': 'follow_up',
                'priority': 'normal',
                'owner_user': str(secondary_sales.pk),
                'due_at': (self.now + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M'),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('owner_user', response.json()['errors'])
        self.assertFalse(
            Task.objects.filter(
                submission=self.owned_lead,
                title='Cross-team task must be rejected',
            ).exists()
        )

    def test_task_complete_rolls_back_task_activity_sla_and_audit_together(self):
        due_at = self.now + timedelta(hours=1)
        task = Task.objects.create(
            site=self.site,
            submission=self.owned_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.sales,
            title='Atomic completion task',
            status='open',
            due_at=due_at,
        )
        LeadSubmission.objects.filter(pk=self.owned_lead.pk).update(
            next_follow_up_at=due_at,
        )
        self.client.force_login(self.sales)
        self.client.raise_request_exception = False

        with patch(
            'console.audit.record_audit',
            side_effect=RuntimeError('simulated audit failure'),
        ):
            response = self.client.post(
                reverse('console:task_complete', args=[task.pk]),
                {'outcome': 'Done', 'version': task_version_token(task.updated_at)},
            )

        self.assertEqual(response.status_code, 500)
        task.refresh_from_db()
        self.owned_lead.refresh_from_db()
        self.assertEqual(task.status, 'open')
        self.assertIsNone(task.completed_at)
        self.assertEqual(task.outcome, '')
        self.assertEqual(self.owned_lead.next_follow_up_at, due_at)
        self.assertFalse(
            Activity.objects.filter(
                submission=self.owned_lead,
                activity_type='task',
                subject__contains='Atomic completion task',
            ).exists()
        )
        self.assertFalse(
            AuditLog.objects.filter(
                entity_table='crm_task',
                entity_id=task.pk,
                action='crm_task_completed',
            ).exists()
        )

    def test_task_complete_reauthorizes_the_current_owner_after_lock(self):
        task = Task.objects.create(
            site=self.site,
            submission=self.owned_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.sales,
            title='Ownership race task',
            status='open',
            due_at=self.now + timedelta(hours=2),
        )
        self.client.force_login(self.sales)
        manager = Task._default_manager
        real_select_for_update = manager.select_for_update
        transfer_attempted = False

        def transfer_before_lock(*args, **kwargs):
            nonlocal transfer_attempted
            transfer_attempted = True
            Task.objects.filter(pk=task.pk).update(owner_user=self.other_sales)
            return real_select_for_update(*args, **kwargs)

        with patch.object(
            manager,
            'select_for_update',
            side_effect=transfer_before_lock,
        ):
            response = self.client.post(
                reverse('console:task_complete', args=[task.pk]),
                {
                    'outcome': 'Must not complete after reassignment',
                    'version': task_version_token(task.updated_at),
                },
            )

        self.assertTrue(transfer_attempted)
        self.assertEqual(response.status_code, 404)
        task.refresh_from_db()
        self.assertEqual(task.status, 'open')
        self.assertIsNone(task.completed_at)
        self.assertFalse(
            Activity.objects.filter(
                submission=self.owned_lead,
                activity_type='task',
                subject__contains='Ownership race task',
            ).exists()
        )
        self.assertFalse(
            AuditLog.objects.filter(
                entity_table='crm_task',
                entity_id=task.pk,
                action='crm_task_completed',
            ).exists()
        )

    def test_external_redirect_candidates_are_replaced_by_canonical_detail_url(self):
        self.client.force_login(self.sales)

        with patch(
            'leads.lead_workflow_services.dispatch_outbox_event',
            return_value=True,
        ):
            response = self._post_json(
                self.owned_lead,
                current_url='https://attacker.invalid/collect',
                next_url='//attacker.invalid/next',
                continue_action='next',
            )

        self.assertEqual(response.status_code, 200)
        redirect_url = response.json()['redirect_url']
        self.assertEqual(
            redirect_url,
            reverse(
                'console:lead_workspace_detail_v2',
                args=[self.owned_lead.pk],
            ),
        )
        self.assertNotIn('attacker.invalid', redirect_url)

    def test_next_without_a_next_url_uses_stable_current_detail_and_drops_default_noise(self):
        self.client.force_login(self.sales)
        data = self._post_data(
            self.owned_lead,
            continue_action='next',
            current_url=(
                f'{reverse("console:lead_workspace_detail_v2", args=[self.owned_lead.pk])}'
                '?stage=new&language=all&source=all&owner=all&sla=all'
            ),
            q='Amina',
            stage_filter='all',
            language='all',
            source='all',
            owner='all',
            sla='all',
            sort='sla',
            page='1',
            page_size='25',
        )
        data.pop('next_url')

        with patch(
            'leads.lead_workflow_services.dispatch_outbox_event',
            return_value=True,
        ):
            response = self.client.post(
                reverse('console:lead_disposition_v2', args=[self.owned_lead.pk]),
                data,
                HTTP_ACCEPT='application/json',
            )

        self.assertEqual(response.status_code, 200)
        redirected = urlsplit(response.json()['redirect_url'])
        self.assertEqual(
            redirected.path,
            reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk]),
        )
        self.assertEqual(
            parse_qs(redirected.query),
            {
                'sort': ['sla'],
                'page': ['1'],
                'page_size': ['25'],
            },
        )
        self.assertEqual(self.client.get(response.json()['redirect_url']).status_code, 200)

    def test_assignment_choices_match_team_inference_and_exclude_non_members(self):
        second_team = SalesTeam.objects.create(
            site=self.site,
            code='secondary-sales',
            name='Secondary sales',
            enabled=True,
        )
        SalesTeamMember.objects.create(
            team=second_team,
            user=self.manager,
            membership_role='manager',
        )
        SalesTeamMember.objects.create(
            team=second_team,
            user=self.other_sales,
            membership_role='member',
        )
        secondary_only = get_user_model().objects.create_user(
            username='secondary-only',
            password='test-password',
        )
        SalesTeamMember.objects.create(
            team=second_team,
            user=secondary_only,
            membership_role='member',
        )

        team_bound = self.owned_lead
        admin_team_bound_ids = set(
            assignable_assignee_queryset_for_lead(self.admin, team_bound)
            .values_list('id', flat=True)
        )
        self.assertIn(self.sales.id, admin_team_bound_ids)
        self.assertIn(self.other_sales.id, admin_team_bound_ids)
        self.assertNotIn(secondary_only.id, admin_team_bound_ids)
        self.assertNotIn(self.admin.id, admin_team_bound_ids)
        self.assertNotIn(self.marketing.id, admin_team_bound_ids)

        unteamed = self._lead(full_name='No Team Lead', assignee=self.manager)
        unteamed.team = None
        unteamed.save(update_fields=['team'])
        admin_unteamed_ids = set(
            assignable_assignee_queryset_for_lead(self.admin, unteamed)
            .values_list('id', flat=True)
        )
        manager_unteamed_ids = set(
            assignable_assignee_queryset_for_lead(self.manager, unteamed)
            .values_list('id', flat=True)
        )
        self.assertIn(self.sales.id, admin_unteamed_ids)
        self.assertIn(secondary_only.id, admin_unteamed_ids)
        self.assertNotIn(self.other_sales.id, admin_unteamed_ids)
        self.assertNotIn(self.manager.id, admin_unteamed_ids)
        self.assertEqual(manager_unteamed_ids, admin_unteamed_ids)

    def test_inactive_historical_owner_is_marked_and_preserved_on_unrelated_save(self):
        inactive_owner = get_user_model().objects.create_user(
            username='former-sales-owner',
            password='test-password',
            is_active=False,
        )
        SalesTeamMember.objects.create(
            team=self.team,
            user=inactive_owner,
            membership_role='member',
        )
        legacy_lead = self._lead(full_name='Legacy Owner Lead', assignee=inactive_owner)
        self.client.force_login(self.admin)

        detail = self.client.get(
            reverse('console:lead_workspace_detail_v2', args=[legacy_lead.pk])
        )
        self.assertEqual(detail.status_code, 200)
        selected_owner = detail.context['workspace']['selected_owner']
        self.assertEqual(selected_owner.id, inactive_owner.id)
        self.assertTrue(selected_owner.current_only)
        self.assertIn('已停用，仅保留', selected_owner.label)

        with patch(
            'leads.lead_workflow_services.dispatch_outbox_event',
            return_value=True,
        ):
            response = self._post_json(
                legacy_lead,
                assignee_id=str(inactive_owner.id),
            )
        self.assertEqual(response.status_code, 200)
        legacy_lead.refresh_from_db()
        self.assertEqual(legacy_lead.assignee_id, inactive_owner.id)

    def test_stay_redirect_drops_filters_invalidated_by_the_saved_stage(self):
        self.client.force_login(self.sales)
        lead = self._lead(full_name='Daniel Rivera', assignee=self.sales)
        detail_path = reverse(
            'console:lead_workspace_detail_v2',
            args=[lead.pk],
        )
        current_url = (
            f'{detail_path}?stage=new&owner=mine&sla=overdue&sort=sla'
            '&page=2&page_size=25&q=Daniel'
        )

        with patch(
            'leads.lead_workflow_services.dispatch_outbox_event',
            return_value=True,
        ):
            response = self._post_json(
                lead,
                current_url=current_url,
                continue_action='stay',
                stage='contacted',
                stage_filter='new',
                owner='mine',
                sla='overdue',
                sort='sla',
                page='2',
                page_size='25',
                q='Daniel',
            )

        self.assertEqual(response.status_code, 200)
        redirect_url = response.json()['redirect_url']
        redirected = urlsplit(redirect_url)
        query = parse_qs(redirected.query)
        self.assertEqual(redirected.path, detail_path)
        self.assertEqual(
            query,
            {
                'sort': ['sla'],
                'page': ['2'],
                'page_size': ['25'],
            },
        )
        self.assertNotIn('stage', query)
        self.assertNotIn('owner', query)
        self.assertNotIn('sla', query)
        self.assertNotIn('q', query)

        destination = self.client.get(redirect_url)
        self.assertEqual(destination.status_code, 200)
        self.assertContains(destination, 'Daniel Rivera')

    def test_success_redirect_drops_query_that_only_matched_mutated_notes(self):
        self.client.force_login(self.sales)
        self.owned_lead.follow_up_notes = 'mutable-only-search-token'
        self.owned_lead.save(update_fields=['follow_up_notes'])
        detail_path = reverse(
            'console:lead_workspace_detail_v2',
            args=[self.owned_lead.pk],
        )
        current_url = f'{detail_path}?q=mutable-only-search-token&sort=sla&page=1&page_size=25'

        with patch(
            'leads.lead_workflow_services.dispatch_outbox_event',
            return_value=True,
        ):
            response = self._post_json(
                self.owned_lead,
                current_url=current_url,
                q='mutable-only-search-token',
                sort='sla',
                page='1',
                page_size='25',
                follow_up_notes='replacement text no longer matching',
            )

        self.assertEqual(response.status_code, 200)
        redirect_url = response.json()['redirect_url']
        self.assertNotIn('q=', redirect_url)
        self.assertEqual(self.client.get(redirect_url).status_code, 200)

    def test_list_and_detail_each_have_one_h1_and_only_the_v2_shell_assets(self):
        self.client.force_login(self.sales)
        responses = (
            self.client.get(
                reverse('console:lead_workspace_v2'),
                {'q': 'definitely-no-result'},
            ),
            self.client.get(
                reverse(
                    'console:lead_workspace_detail_v2',
                    args=[self.owned_lead.pk],
                )
            ),
        )
        expected_stylesheets = [
            '/static/console/vendor/tabler/1.4.0/tabler.min.css',
            '/static/console/v2/vorntek-theme.css',
            '/static/console/v2/vorntek-v2.css?v=20260830-6',
            '/static/console/vorntek-brand.css',
        ]
        expected_scripts = [
            '/static/console/vendor/tabler/1.4.0/tabler.min.js',
            '/static/console/lucide.min.js',
            '/static/console/v2/shell.js',
            '/static/console/v2/leads.js',
        ]

        for response in responses:
            with self.subTest(path=response.request['PATH_INFO']):
                self.assertEqual(response.status_code, 200)
                parser = _DocumentContractParser()
                parser.feed(response.content.decode())
                self.assertEqual(parser.h1_count, 1)
                self.assertEqual(parser.stylesheets, expected_stylesheets)
                self.assertEqual(parser.scripts, expected_scripts)
                rendered = response.content.decode()
                self.assertNotIn('admin-shell.css', rendered)
                self.assertNotIn('admin-shell.js', rendered)

    def test_v2_template_exposes_saved_views_and_role_gates_assignment_controls(self):
        detail_url = reverse(
            'console:lead_workspace_detail_v2',
            args=[self.owned_lead.pk],
        )
        self.client.force_login(self.sales)
        sales_response = self.client.get(detail_url)
        self.assertEqual(sales_response.status_code, 200)
        sales_rendered = sales_response.content.decode()
        self.assertIn('data-nc-save-view-form', sales_rendered)
        self.assertIn('data-nc-saved-view-list', sales_rendered)
        self.assertIn('data-nc-draft-alert', sales_rendered)
        self.assertIn('data-nc-draft-restore', sales_rendered)
        self.assertIn('data-nc-draft-discard', sales_rendered)
        self.assertIn('data-nc-conflict-panel', sales_rendered)
        self.assertIn('data-nc-conflict-reload', sales_rendered)
        self.assertIn('data-nc-conflict-discard', sales_rendered)
        self.assertIn(f'data-nc-user-id="{self.sales.pk}"', sales_rendered)
        self.assertIn(f'data-nc-site-id="{self.site.pk}"', sales_rendered)
        self.assertIn('data-nc-list-url=', sales_rendered)
        self.assertIn('for="lead-follow-up-notes"', sales_rendered)
        self.assertIn('id="lead-follow-up-notes"', sales_rendered)
        self.assertNotIn('<time>', sales_rendered)
        self.assertIn('data-nc-logout-form', sales_rendered)
        self.assertNotIn('data-nc-bulk-bar', sales_rendered)
        self.assertNotIn('data-nc-update-owner', sales_rendered)
        self.assertNotIn('nc-create-button', sales_rendered)
        self.assertNotIn(reverse('console:lead_form_create'), sales_rendered)
        self.assertNotIn(reverse('console:article_create'), sales_rendered)
        self.assertNotIn(reverse('console:asset_upload'), sales_rendered)

        self.client.force_login(self.manager)
        manager_response = self.client.get(detail_url)
        self.assertEqual(manager_response.status_code, 200)
        manager_rendered = manager_response.content.decode()
        self.assertIn('data-nc-select-page', manager_rendered)
        self.assertIn('name="record_ids"', manager_rendered)
        self.assertIn('data-nc-bulk-bar', manager_rendered)
        self.assertIn('data-nc-bulk-failures', manager_rendered)
        self.assertIn('data-nc-bulk-failure-count', manager_rendered)
        self.assertIn('data-nc-bulk-failure-list', manager_rendered)
        self.assertIn('data-nc-update-owner', manager_rendered)
        self.assertIn('data-nc-original-assignee', manager_rendered)
        self.assertNotIn('name="assignee_id" aria-label="负责人"', manager_rendered)
        self.assertNotIn('nc-create-button', manager_rendered)

    def test_industrial_inquiry_has_application_context_in_rendered_crm(self):
        self.owned_lead.payload_json = {
            'business_line': 'gasSensors', 'product_category': 'Gas sensors',
            'application_context': 'Synthetic industrial monitoring',
        }
        self.owned_lead.save(update_fields=['payload_json', 'updated_at'])
        self.client.force_login(self.admin)
        response = self.client.get(reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk]))
        self.assertContains(response, 'Gas sensors')
        self.assertContains(response, 'gasSensors')
        self.assertContains(response, 'Synthetic industrial monitoring')
        self.assertContains(response, '应用背景')
        self.assertNotContains(response, '容器 / 包装')
        self.assertEqual(response.context['workspace']['selected']['context_label'], '应用背景')

    def test_detail_exposes_project_consent_trace_and_warns_when_assignment_has_no_candidates(self):
        self.owned_lead.payload_json = {
            'inquiry_type': 'New complete line',
            'product_category': 'Bottled water',
            'container_package': '500 ml PET',
            'capacity': '12,000 BPH',
            'timeline': '3-6 months',
            'project_condition': 'New factory / new line',
        }
        self.owned_lead.consent_json = {
            'contact': True,
            'privacy_notice': True,
            'marketing': False,
        }
        self.owned_lead.save(update_fields=['payload_json', 'consent_json', 'updated_at'])
        ConsentRecord.objects.create(
            submission=self.owned_lead,
            purpose='contact',
            decision='granted',
            source='website_form',
            policy_version='2026-09-04',
            evidence_json={},
        )

        self.client.force_login(self.admin)
        detail_url = reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk])
        populated = self.client.get(detail_url)
        self.assertContains(populated, '项目需求')
        self.assertContains(populated, 'New complete line')
        self.assertContains(populated, '500 ml PET')
        self.assertContains(populated, '权限与同意')
        self.assertContains(populated, '2026-09-04')
        self.assertContains(populated, '技术追踪')
        self.assertContains(populated, 'value="call" checked')
        self.assertContains(populated, 'value="首次联系"')

        SalesTeamMember.objects.all().delete()
        blocked = self.client.get(detail_url)
        self.assertContains(blocked, 'data-nc-assignment-readiness')
        self.assertContains(blocked, '负责人配置未就绪')
        self.assertContains(blocked, reverse('console:system_teams'))

    def test_v2_selection_restore_rejects_non_array_session_data(self):
        source = (
            Path(__file__).resolve().parent
            / 'static'
            / 'console'
            / 'v2'
            / 'leads.js'
        ).read_text(encoding='utf-8')
        self.assertIn('Array.isArray(storedSelection)', source)
        self.assertIn('/^\\d+$/.test(id) && currentIds.has(id)', source)

    def test_v2_disposition_draft_source_contract_guards_user_input(self):
        source = (
            Path(__file__).resolve().parent
            / 'static'
            / 'console'
            / 'v2'
            / 'leads.js'
        ).read_text(encoding='utf-8')
        self.assertIn("const draftPrefix = sessionPrefix('draft');", source)
        self.assertIn('const draftKey = draftPrefix ? draftPrefix + recordId :', source)
        self.assertIn("const legacyDraft = /^nc:leads:v2:draft:\\d+$/;", source)
        self.assertIn('purgeLegacyLeadSessionState();', source)
        self.assertIn('initLogoutSessionCleanup();', source)
        self.assertIn("'nc:leads:v2:' + kind + ':' + identity.userId + ':' + identity.siteId + ':'", source)
        dirty_logout = source.index("const dirty = disposition && disposition.dataset.ncDirty === 'true';")
        unresolved_logout = source.index("disposition.dataset.ncUnresolvedDraft === 'true'", dirty_logout)
        confirm_logout = source.index('if ((dirty || unresolved) && !window.confirm(', unresolved_logout)
        cancel_logout = source.index('event.preventDefault();', confirm_logout)
        cleanup_logout = source.index("document.dispatchEvent(new CustomEvent('nc:logout-start'))", cancel_logout)
        self.assertLess(dirty_logout, confirm_logout)
        self.assertLess(unresolved_logout, confirm_logout)
        self.assertLess(confirm_logout, cancel_logout)
        self.assertLess(cancel_logout, cleanup_logout)
        self.assertIn('退出登录将舍弃这条线索尚未保存或未处理的草稿', source)
        self.assertIn("window.addEventListener('beforeunload'", source)
        self.assertIn('serializeEditableDisposition(form)', source)
        self.assertIn('restoreEditableDisposition(form, draft.fields)', source)
        self.assertIn("document.dispatchEvent(new CustomEvent('nc:draft-restored'", source)
        self.assertIn('draftManager.handleConflict(message)', source)
        self.assertIn('draftManager.markSaved(payload.current_updated_at, true)', source)
        self.assertIn('value.version === 1 || value.version === 2', source)
        self.assertIn("submissionState: 'ambiguous'", source)
        self.assertIn('idempotencyToken: idempotencyToken', source)
        self.assertIn('draftManager.beginSubmission(requestFields)', source)
        self.assertIn('draftManager.handleAmbiguousFailure()', source)
        self.assertIn('lateChangedFields(requestFields, currentFields)', source)
        self.assertIn("['draft', 'selection', 'scroll', 'assignment-retry']", source)
        for protected_name in (
            'csrfmiddlewaretoken',
            'expected_updated_at',
            'idempotency_token',
            'continue_action',
            'current_url',
            'next_url',
        ):
            self.assertIn(f"'{protected_name}'", source)

    def test_v2_disposition_submit_freezes_after_snapshot_and_guards_late_changes(self):
        source = (
            Path(__file__).resolve().parent
            / 'static'
            / 'console'
            / 'v2'
            / 'leads.js'
        ).read_text(encoding='utf-8')
        request_body = source.index('const requestBody = new FormData(form);')
        request_fields = source.index('const requestFields = serializeEditableDisposition(form);', request_body)
        request_snapshot = source.index(
            'const requestSnapshot = JSON.stringify(requestFields);',
            request_fields,
        )
        freeze = source.index('setBusy(form, true);', request_snapshot)
        late_change_guard = source.index(
            'if (currentSnapshot !== requestSnapshot)',
            freeze,
        )
        clear_draft = source.index('draftManager.markSaved(payload.current_updated_at', late_change_guard)
        self.assertLess(request_body, request_snapshot)
        self.assertLess(request_body, request_fields)
        self.assertLess(request_fields, request_snapshot)
        self.assertLess(request_snapshot, freeze)
        self.assertLess(freeze, late_change_guard)
        self.assertLess(late_change_guard, clear_draft)
        self.assertIn('body: requestBody', source)
        self.assertNotIn('body: new FormData(form)', source)
        self.assertIn("field.dataset.ncBusyControl === 'true'", source)
        self.assertIn("control.dataset.ncBusyControl = 'true'", source)
        self.assertIn("control.dataset.ncBusyWasDisabled = String(control.disabled)", source)
        self.assertIn("link.dataset.ncBusyLink = 'true'", source)
        self.assertIn("const assignmentControls = qa('[data-nc-select-page]", source)
        self.assertIn("q('[data-nc-bulk-assign][aria-busy=\"true\"]')", source)
        self.assertIn('draftManager.handlePostSubmitChanges(', source)
        self.assertIn('if (!keepLockedForNavigation)', source)

    def test_v2_async_ui_contract_freezes_inputs_and_blocks_navigation_races(self):
        source = (
            Path(__file__).resolve().parent
            / 'static'
            / 'console'
            / 'v2'
            / 'leads.js'
        ).read_text(encoding='utf-8')

        saved_body = source.index('const body = new FormData(form);')
        saved_controls = source.index("const busyControls = qa('input, select, textarea, button', form);", saved_body)
        saved_freeze = source.index('setAsyncControlsBusy(busyControls, true, submit', saved_controls)
        saved_restore = source.index('setAsyncControlsBusy(busyControls, false, submit)', saved_freeze)
        self.assertLess(saved_body, saved_controls)
        self.assertLess(saved_controls, saved_freeze)
        self.assertLess(saved_freeze, saved_restore)

        self.assertIn('const capturedIds = new Set(ids);', source)
        self.assertIn('const assigneeId = String(owner.value);', source)
        self.assertIn('controls.concat([pageToggle, owner, clear, assign])', source)
        self.assertIn('.filter((item) => capturedIds.has(String(item.id)))', source)
        self.assertIn("writeSessionJson(retryKey, { ownerId: assigneeId, failures: failures })", source)
        self.assertIn('reloadCanonicalLeadQueue(workspace)', source)
        self.assertIn('const busyControls = [select, button];', source)

        self.assertIn("document.addEventListener('click', (event) => {", source)
        self.assertIn("document.addEventListener('submit', (event) => {", source)
        self.assertIn("String(link.target || '').toLowerCase() === '_blank'", source)
        self.assertIn("document.documentElement.dataset.ncDispositionBusy !== 'true'", source)
        self.assertIn('control.form.requestSubmit();', source)
        self.assertNotIn('control.form.submit()', source)

    def test_v2_template_uses_neutral_skip_conversion_and_live_score_semantics(self):
        template_source = (
            Path(__file__).resolve().parent
            / 'templates'
            / 'console'
            / 'v2'
            / 'pages'
            / 'leads.html'
        ).read_text(encoding='utf-8')
        script_source = (
            Path(__file__).resolve().parent
            / 'static'
            / 'console'
            / 'v2'
            / 'leads.js'
        ).read_text(encoding='utf-8')
        self.assertIn('跳过并查看下一条', template_source)
        self.assertNotIn('稍后处理', template_source)
        self.assertNotIn('将创建', template_source)
        self.assertEqual(template_source.count('data-nc-qualification-score'), 2)
        self.assertIn("qa('[data-nc-qualification-score]')", script_source)
        self.assertIn("qa('[data-nc-qualification-progress]')", script_source)
        self.assertIn('showFailures(failures)', script_source)
        self.assertIn("lead.textContent = '线索 #' + id", script_source)

    def test_v2_conversion_modal_and_async_feedback_contract(self):
        template_source = (
            Path(__file__).resolve().parent
            / 'templates'
            / 'console'
            / 'v2'
            / 'pages'
            / 'leads.html'
        ).read_text(encoding='utf-8')
        script_source = (
            Path(__file__).resolve().parent
            / 'static'
            / 'console'
            / 'v2'
            / 'leads.js'
        ).read_text(encoding='utf-8')

        self.assertIn('{% if workspace.can_convert %}', template_source)
        self.assertIn('data-nc-open-conversion', template_source)
        self.assertIn('data-nc-conversion-form', template_source)
        self.assertIn('aria-describedby="lead-conversion-consequence"', template_source)
        self.assertIn('name="opportunity_name"', template_source)
        self.assertIn('data-nc-conversion-feedback', template_source)
        self.assertIn("'X-CSRFToken': csrfToken(form)", script_source)
        self.assertIn('function conversionFailureMessage(payload, status)', script_source)
        self.assertIn('if (status === 409)', script_source)
        self.assertIn("submitLabel.textContent = '重新尝试'", script_source)
        self.assertIn('window.location.reload()', script_source)
        self.assertIn('initLeadConversion();', script_source)
        conversion_source = script_source.split('function initLeadConversion()', 1)[1].split(
            'function initActivityDefaults()',
            1,
        )[0]
        self.assertNotIn('announce(', conversion_source)

    def test_personal_lead_saved_view_is_canonical_and_directly_applicable(self):
        self.client.force_login(self.sales)
        response = self.client.post(
            reverse('console:saved_view_save'),
            {
                'scope': 'leads',
                'name': 'My new Meta leads',
                'filters_json': json.dumps({
                    'q': '  Amina   Hassan ',
                    'stage': 'new',
                    'owner': 'mine',
                    'source': 'meta_ads',
                    'page_size': 20,
                    'redirect': 'https://attacker.invalid/collect',
                }),
                'columns_json': json.dumps(['identity', 'stage', 'owner']),
                'sort_json': json.dumps(['newest']),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        saved = SavedView.objects.get(pk=payload['saved_view_id'])
        self.assertEqual(saved.user_id, self.sales.pk)
        self.assertEqual(saved.filters_json, {
            'q': 'Amina Hassan',
            'stage': 'new',
            'source': 'meta_ads',
            'owner': 'mine',
            'page_size': 20,
        })
        self.assertEqual(saved.sort_json, ['newest'])
        apply_url = urlsplit(payload['saved_view']['apply_url'])
        self.assertEqual(apply_url.path, reverse('console:lead_workspace_v2'))
        self.assertNotIn('attacker.invalid', apply_url.query)
        self.assertNotIn('redirect', parse_qs(apply_url.query))
        self.assertEqual(parse_qs(apply_url.query)['sort'], ['newest'])

        detail = self.client.get(
            reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk])
        )
        self.assertEqual(detail.status_code, 200)
        workspace_view = detail.context['workspace']['saved_views'][0]
        self.assertEqual(workspace_view['name'], 'My new Meta leads')
        self.assertEqual(workspace_view['apply_url'], payload['saved_view']['apply_url'])
        self.assertEqual(detail.context['workspace']['saved_view']['shareable_teams'], [])

    def test_saved_view_audit_failure_rolls_back_the_view_write(self):
        self.client.force_login(self.sales)
        with (
            patch(
                'console.sales_views.record_audit',
                side_effect=RuntimeError('audit insert failed'),
            ),
            self.assertRaisesRegex(RuntimeError, 'audit insert failed'),
        ):
            self.client.post(
                reverse('console:saved_view_save'),
                {
                    'scope': 'leads',
                    'name': 'Must roll back',
                    'filters_json': json.dumps({'stage': 'new'}),
                    'columns_json': json.dumps(['identity', 'stage']),
                    'sort_json': json.dumps(['sla']),
                },
            )

        self.assertFalse(
            SavedView.objects.filter(
                site=self.site,
                user=self.sales,
                scope='leads',
                name='Must roll back',
            ).exists()
        )

    def test_default_saved_view_conflict_returns_business_error_and_restores_previous_default(self):
        previous = SavedView.objects.create(
            site=self.site,
            user=self.sales,
            scope='leads',
            name='Previous default',
            filters_json={'stage': 'new'},
            columns_json=[],
            sort_json=['sla'],
            is_default=True,
            is_shared=False,
        )
        self.client.force_login(self.sales)

        with patch(
            'console.sales_forms.SavedView.objects.update_or_create',
            side_effect=IntegrityError('simulated partial unique race'),
        ):
            response = self.client.post(
                reverse('console:saved_view_save'),
                {
                    'scope': 'leads',
                    'name': 'Racing default',
                    'filters_json': json.dumps({'stage': 'qualified'}),
                    'columns_json': '[]',
                    'sort_json': json.dumps(['sla']),
                    'is_default': 'on',
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn('并发冲突', response.json()['errors']['__all__'][0])
        previous.refresh_from_db()
        self.assertTrue(previous.is_default)
        self.assertFalse(
            SavedView.objects.filter(
                site=self.site,
                user=self.sales,
                scope='leads',
                name='Racing default',
            ).exists()
        )

    def test_personal_default_saved_view_applies_once_but_explicit_query_wins(self):
        SavedView.objects.create(
            site=self.site,
            user=self.sales,
            scope='leads',
            name='Default new queue',
            filters_json={'q': 'Amina', 'stage': 'new', 'page_size': 20},
            columns_json=[],
            sort_json=['newest'],
            is_default=True,
            is_shared=False,
        )
        self.client.force_login(self.sales)

        default_response = self.client.get(reverse('console:lead_workspace_v2'))
        self.assertEqual(default_response.status_code, 302)
        default_destination = urlsplit(default_response.url)
        self.assertEqual(default_destination.path, reverse('console:lead_workspace_v2'))
        self.assertEqual(parse_qs(default_destination.query), {
            'sort': ['newest'],
            'page': ['1'],
            'page_size': ['20'],
            'q': ['Amina'],
            'stage': ['new'],
        })
        self.assertEqual(self.client.get(default_response.url).status_code, 302)

        explicit = self.client.get(
            reverse('console:lead_workspace_v2'),
            {'sort': 'name', 'page_size': '25'},
        )
        self.assertEqual(explicit.status_code, 302)
        explicit_query = parse_qs(urlsplit(explicit.url).query)
        self.assertEqual(explicit_query['sort'], ['name'])
        self.assertNotIn('q', explicit_query)

    def test_invalid_default_saved_view_falls_back_without_redirect_loop(self):
        SavedView.objects.create(
            site=self.site,
            user=self.sales,
            scope='leads',
            name='Legacy invalid default',
            filters_json={'q': {'nested': '//attacker.invalid'}},
            columns_json=[],
            sort_json=[],
            is_default=True,
            is_shared=False,
        )
        self.client.force_login(self.sales)

        response = self.client.get(reverse('console:lead_workspace_v2'))
        self.assertEqual(response.status_code, 302)
        destination = urlsplit(response.url)
        self.assertEqual(
            destination.path,
            reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk]),
        )
        self.assertNotIn('attacker.invalid', response.url)

    def test_team_shared_saved_view_visibility_matches_form_permissions(self):
        self.client.force_login(self.manager)
        created = self.client.post(
            reverse('console:saved_view_save'),
            {
                'scope': 'leads',
                'name': 'Primary team queue',
                'filters_json': json.dumps({'owner': 'unassigned', 'sla': 'overdue'}),
                'columns_json': '[]',
                'sort_json': json.dumps([{'field': 'sla'}]),
                'is_default': 'on',
                'is_shared': 'on',
                'team': str(self.team.pk),
            },
        )
        self.assertEqual(created.status_code, 200)
        self.assertTrue(created.json()['saved_view']['is_shared'])
        self.assertTrue(created.json()['saved_view']['is_default'])
        self.assertEqual(created.json()['saved_view']['team']['id'], self.team.pk)

        self.client.force_login(self.sales)
        visible = self.client.get(
            reverse('console:saved_view_save'),
            {'scope': 'leads'},
        )
        self.assertEqual(visible.status_code, 200)
        self.assertEqual(
            [item['name'] for item in visible.json()['saved_views']],
            ['Primary team queue'],
        )
        self.assertTrue(visible.json()['saved_views'][0]['apply_url'])
        self.assertFalse(visible.json()['saved_views'][0]['is_default'])
        self.assertTrue(visible.json()['saved_views'][0]['is_shared'])
        detail = self.client.get(
            reverse('console:lead_workspace_detail_v2', args=[self.owned_lead.pk])
        )
        shared_context = detail.context['workspace']['saved_views'][0]
        self.assertFalse(shared_context['is_default'])
        self.assertTrue(shared_context['is_shared'])
        self.assertEqual(shared_context['team']['label'], self.team.name)

        self.client.force_login(self.marketing)
        self.assertEqual(
            self.client.get(reverse('console:saved_view_save'), {'scope': 'leads'}).status_code,
            403,
        )

    def test_malicious_saved_filters_are_rejected_or_removed_from_apply_url(self):
        self.client.force_login(self.sales)
        nested = self.client.post(
            reverse('console:saved_view_save'),
            {
                'scope': 'leads',
                'name': 'Nested attack',
                'filters_json': json.dumps({'q': {'$where': 'steal()'}}),
                'columns_json': '[]',
                'sort_json': '[]',
            },
        )
        self.assertEqual(nested.status_code, 400)
        self.assertFalse(SavedView.objects.filter(name='Nested attack').exists())

        normalized = self.client.post(
            reverse('console:saved_view_save'),
            {
                'scope': 'leads',
                'name': 'Unknown keys',
                'filters_json': json.dumps({
                    'stage': 'drop-table',
                    'next': '//attacker.invalid',
                    'owner': '999999999',
                }),
                'columns_json': '[]',
                'sort_json': json.dumps(['not-a-sort']),
            },
        )
        self.assertEqual(normalized.status_code, 200)
        saved_payload = normalized.json()['saved_view']
        self.assertNotIn('attacker.invalid', saved_payload['apply_url'])
        query = parse_qs(urlsplit(saved_payload['apply_url']).query)
        self.assertEqual(query['sort'], ['sla'])
        self.assertNotIn('stage', query)
        self.assertNotIn('owner', query)
        self.assertNotIn('next', query)

    def test_sales_cannot_use_dedicated_assignment_endpoint(self):
        self.client.force_login(self.sales)
        response = self.client.post(
            reverse('console:lead_bulk_assign_v2'),
            {'record_ids': [str(self.owned_lead.pk)], 'assignee_id': str(self.other_sales.pk)},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['updated'], 0)
        self.owned_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.assignee_id, self.sales.pk)
        self.assertFalse(Activity.objects.filter(submission=self.owned_lead).exists())

    def test_manager_assignment_writes_only_owner_team_activity_and_audit(self):
        self.client.force_login(self.manager)
        original_stage = self.owned_lead.stage
        original_notes = self.owned_lead.follow_up_notes
        response = self.client.post(
            reverse('console:lead_bulk_assign_v2'),
            {
                'record_ids': [str(self.owned_lead.pk), str(self.owned_lead.pk)],
                'assignee_id': str(self.other_sales.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['updated'], 1)
        self.assertEqual(len(payload['records']), 1)
        self.assertTrue(payload['records'][0]['current_updated_at'])
        self.assertEqual(payload['records'][0]['assignee']['id'], self.other_sales.pk)
        self.assertEqual(payload['records'][0]['team']['id'], self.team.pk)
        self.owned_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.assignee_id, self.other_sales.pk)
        self.assertEqual(self.owned_lead.team_id, self.team.pk)
        self.assertEqual(self.owned_lead.stage, original_stage)
        self.assertEqual(self.owned_lead.follow_up_notes, original_notes)
        activities = Activity.objects.filter(submission=self.owned_lead)
        self.assertEqual(activities.count(), 1)
        activity = activities.get()
        self.assertEqual(activity.activity_type, 'assignment')
        self.assertEqual(activity.direction, 'internal')
        self.assertEqual(activity.body, '')
        audit = AuditLog.objects.get(action='lead_assignment_v2')
        self.assertEqual(audit.entity_id, self.owned_lead.pk)
        self.assertEqual(audit.before_json['assignee_id'], self.sales.pk)
        self.assertEqual(audit.after_json['assignee_id'], self.other_sales.pk)

        retry = self.client.post(
            reverse('console:lead_bulk_assign_v2'),
            {
                'record_ids': [str(self.owned_lead.pk)],
                'assignee_id': str(self.other_sales.pk),
            },
        )
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json()['ok'])
        self.assertEqual(retry.json()['updated'], 0)
        self.assertFalse(retry.json()['records'][0]['changed'])
        self.assertIn('无需更新', retry.json()['message'])
        self.assertEqual(
            retry.json()['records'][0]['current_updated_at'],
            payload['records'][0]['current_updated_at'],
        )
        self.assertEqual(Activity.objects.filter(submission=self.owned_lead).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='lead_assignment_v2').count(), 1)

    def test_assignment_scope_hides_missing_and_cross_team_records(self):
        secondary_team = SalesTeam.objects.create(
            site=self.site,
            code='secondary-private',
            name='Secondary private',
            enabled=True,
        )
        secondary_sales = get_user_model().objects.create_user(
            username='secondary-private-sales',
            password='test-password',
        )
        SalesTeamMember.objects.create(
            team=secondary_team,
            user=secondary_sales,
            membership_role='member',
        )
        private_lead = self._lead(full_name='Private team lead', assignee=secondary_sales)
        private_lead.team = secondary_team
        private_lead.save(update_fields=['team'])
        fake_id = private_lead.pk + 100000
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse('console:lead_bulk_assign_v2'),
            {
                'record_ids': [str(private_lead.pk), str(fake_id)],
                'assignee_id': str(self.sales.pk),
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['updated'], 0)
        self.assertEqual(
            [failure['code'] for failure in response.json()['failures']],
            ['not_found', 'not_found'],
        )
        self.assertEqual(
            response.json()['failures'][0]['message'],
            response.json()['failures'][1]['message'],
        )

    def test_bulk_assignment_returns_real_partial_success(self):
        secondary_team = SalesTeam.objects.create(
            site=self.site,
            code='secondary-partial',
            name='Secondary partial',
            enabled=True,
        )
        secondary_sales = get_user_model().objects.create_user(
            username='secondary-partial-sales',
            password='test-password',
        )
        SalesTeamMember.objects.create(
            team=secondary_team,
            user=secondary_sales,
            membership_role='member',
        )
        private_lead = self._lead(full_name='Partial private lead', assignee=secondary_sales)
        private_lead.team = secondary_team
        private_lead.save(update_fields=['team'])
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse('console:lead_bulk_assign_v2'),
            {
                'record_ids': [str(self.owned_lead.pk), str(private_lead.pk)],
                'assignee_id': str(self.other_sales.pk),
            },
        )
        self.assertEqual(response.status_code, 207)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['updated'], 1)
        self.assertEqual([record['id'] for record in payload['records']], [self.owned_lead.pk])
        failure = payload['failures'][0]
        self.assertEqual(set(failure), {'id', 'message', 'code'})
        self.assertEqual(failure['id'], private_lead.pk)
        self.assertEqual(failure['code'], 'not_found')
        self.assertTrue(failure['message'])
        self.owned_lead.refresh_from_db()
        private_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.assignee_id, self.other_sales.pk)
        self.assertEqual(private_lead.assignee_id, secondary_sales.pk)

    def test_inactive_owner_cannot_be_new_assignment_target(self):
        inactive = get_user_model().objects.create_user(
            username='inactive-assignment-target',
            password='test-password',
            is_active=False,
        )
        SalesTeamMember.objects.create(team=self.team, user=inactive, membership_role='member')
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('console:lead_bulk_assign_v2'),
            {'record_ids': [str(self.owned_lead.pk)], 'assignee_id': str(inactive.pk)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['failures'][0]['code'], 'assignee_unavailable')
        self.owned_lead.refresh_from_db()
        self.assertEqual(self.owned_lead.assignee_id, self.sales.pk)

    def test_pagination_contract_and_active_owner_quick_queue_semantics(self):
        for index in range(26):
            self._lead(full_name=f'Paged Lead {index:02d}', assignee=self.sales)
        self.client.force_login(self.sales)
        response = self.client.get(
            reverse('console:lead_workspace_v2'),
            {'q': 'Paged Lead', 'page_size': '25'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        pagination = response.context['workspace']['pagination']
        self.assertEqual(pagination['current_page'], 1)
        self.assertEqual(pagination['total_pages'], 2)
        self.assertContains(response, '1 / 2')

        closed = self._lead(full_name='Closed owned lead', assignee=self.sales)
        closed.stage = 'won'
        closed.save(update_fields=['stage'])
        active_queue = self.client.get(
            reverse('console:lead_workspace_v2'),
            {'owner': 'mine', 'sort': 'sla'},
            follow=True,
        )
        self.assertEqual(active_queue.status_code, 200)
        self.assertNotContains(active_queue, 'Closed owned lead')

    def test_detail_get_canonicalizes_updated_sort_when_the_selected_record_moves_pages(self):
        for index in range(20):
            item = self._lead(
                full_name=f'Queue{index:02d} Lead',
                assignee=self.sales,
            )
            LeadSubmission.objects.filter(pk=item.pk).update(
                updated_at=self.now + timedelta(minutes=index),
            )
        LeadSubmission.objects.filter(pk=self.owned_lead.pk).update(
            updated_at=self.now - timedelta(days=1),
        )
        self.client.force_login(self.sales)
        detail_url = reverse(
            'console:lead_workspace_detail_v2',
            args=[self.owned_lead.pk],
        )
        filters = {
            'q': 'Beverage',
            'stage': 'new',
            'language': 'en',
            'source': 'meta_ads',
            'owner': str(self.sales.pk),
            'sla': 'unplanned',
            'sort': 'updated',
            'page': '1',
            'page_size': '20',
            'attacker_key': 'must-not-survive',
        }

        moved_to_second_page = self.client.get(detail_url, filters)
        self.assertEqual(moved_to_second_page.status_code, 302)
        canonical = urlsplit(moved_to_second_page.url)
        canonical_query = parse_qs(canonical.query)
        self.assertEqual(canonical.path, detail_url)
        self.assertEqual(canonical_query['page'], ['2'])
        for key in ('q', 'stage', 'language', 'source', 'owner', 'sla', 'sort', 'page_size'):
            self.assertEqual(canonical_query[key], [filters[key]])
        self.assertNotIn('attacker_key', canonical_query)
        self.assertEqual(self.client.get(moved_to_second_page.url).status_code, 200)

        LeadSubmission.objects.filter(pk=self.owned_lead.pk).update(
            updated_at=self.now + timedelta(days=1),
        )
        moved_back_to_first_page = self.client.get(
            detail_url,
            {**filters, 'page': '2'},
        )
        self.assertEqual(moved_back_to_first_page.status_code, 302)
        self.assertEqual(
            parse_qs(urlsplit(moved_back_to_first_page.url).query)['page'],
            ['1'],
        )
        self.assertEqual(self.client.get(moved_back_to_first_page.url).status_code, 200)

    def test_detail_get_canonicalizes_sla_sort_after_follow_up_deadline_changes(self):
        for index in range(20):
            item = self._lead(
                full_name=f'SlaQueue{index:02d} Lead',
                assignee=self.sales,
            )
            LeadSubmission.objects.filter(pk=item.pk).update(
                next_follow_up_at=self.now + timedelta(days=2, hours=index),
            )
        LeadSubmission.objects.filter(pk=self.owned_lead.pk).update(
            next_follow_up_at=self.now + timedelta(days=100),
        )
        self.client.force_login(self.sales)
        detail_url = reverse(
            'console:lead_workspace_detail_v2',
            args=[self.owned_lead.pk],
        )

        moved_to_second_page = self.client.get(
            detail_url,
            {'sort': 'sla', 'page': '1', 'page_size': '20'},
        )
        self.assertEqual(moved_to_second_page.status_code, 302)
        self.assertEqual(
            parse_qs(urlsplit(moved_to_second_page.url).query)['page'],
            ['2'],
        )
        self.assertEqual(self.client.get(moved_to_second_page.url).status_code, 200)

        LeadSubmission.objects.filter(pk=self.owned_lead.pk).update(
            next_follow_up_at=self.now - timedelta(hours=1),
        )
        moved_back_to_first_page = self.client.get(
            detail_url,
            {'sort': 'sla', 'page': '2', 'page_size': '20'},
        )
        self.assertEqual(moved_back_to_first_page.status_code, 302)
        self.assertEqual(
            parse_qs(urlsplit(moved_back_to_first_page.url).query)['page'],
            ['1'],
        )
        self.assertEqual(self.client.get(moved_back_to_first_page.url).status_code, 200)
