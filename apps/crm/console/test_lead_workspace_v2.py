from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import NoReverseMatch
from django.utils import timezone

from marketing.models import CanonicalEvent, MarketingIntegration, MarketingProvider
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
from sitecore.models import Cta, PageRoute, Site, SiteLocale

from .lead_workspace_v2 import (
    QUERY_KEYS,
    SORT_SPECS,
    LeadWorkspaceV2Filters,
    _active_filter_chips,
    _activity_item,
    _detail_url,
    _sla_meta,
    build_lead_workspace_v2,
)


class LeadWorkspaceV2FilterTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_only_documented_query_keys_and_values_survive(self):
        request = self.factory.get(
            '/admin/sales/leads/',
            {
                'q': '  Amina   Hassan  ',
                'stage': 'qualified',
                'language': 'en',
                'source': 'meta_ads',
                'owner': '17',
                'sla': 'overdue',
                'sort': 'updated',
                'page': '3',
                'page_size': '50',
                'debug': '1',
                'next': 'https://attacker.invalid/',
            },
        )

        filters = LeadWorkspaceV2Filters.from_querydict(
            request.GET,
            allowed_languages=['en'],
            allowed_sources=['meta_ads'],
            allowed_owner_ids=[17],
        )

        self.assertEqual(filters.query, 'Amina Hassan')
        self.assertEqual(filters.stage, 'qualified')
        self.assertEqual(filters.language, 'en')
        self.assertEqual(filters.source, 'meta_ads')
        self.assertEqual(filters.owner, '17')
        self.assertEqual(filters.sla, 'overdue')
        self.assertEqual(filters.sort, 'updated')
        self.assertEqual(filters.page, 3)
        self.assertEqual(filters.page_size, 50)
        self.assertEqual(set(filters.query_params()), QUERY_KEYS)
        self.assertNotIn('debug', filters.query_params())
        self.assertNotIn('next', filters.query_params())

    def test_invalid_enumerations_and_page_values_fall_back(self):
        request = self.factory.get(
            '/admin/sales/leads/',
            {
                'stage': 'DROP TABLE',
                'language': '../en',
                'source': 'unknown-provider',
                'owner': '999',
                'sla': 'later',
                'sort': '-password',
                'page': '-7',
                'page_size': '9999',
            },
        )

        filters = LeadWorkspaceV2Filters.from_querydict(
            request.GET,
            allowed_languages=['en'],
            allowed_sources=['meta_ads'],
            allowed_owner_ids=[17],
        )

        self.assertEqual(
            filters,
            LeadWorkspaceV2Filters(),
        )

    def test_every_sort_has_an_explicit_id_tie_breaker(self):
        for key, spec in SORT_SPECS.items():
            with self.subTest(key=key):
                self.assertEqual(spec.fields[-1][0], 'id')
                self.assertIn(spec.fields[-1][1], {'asc', 'desc'})

    def test_detail_url_preserves_full_canonical_query_and_ignores_unknowns(self):
        filters = LeadWorkspaceV2Filters(
            query='Amina Hassan',
            stage='new',
            language='en',
            source='meta_ads',
            owner='unassigned',
            sla='overdue',
            sort='sla',
            page=2,
            page_size=50,
        )
        with patch('console.lead_workspace_v2.reverse', side_effect=NoReverseMatch):
            url = _detail_url(
                42,
                filters=filters,
                page=2,
                list_url='/admin/sales/leads/',
                detail_url_name='console:missing',
            )

        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['lead_id'], ['42'])
        self.assertEqual(query['q'], ['Amina Hassan'])
        self.assertEqual(query['stage'], ['new'])
        self.assertEqual(query['language'], ['en'])
        self.assertEqual(query['source'], ['meta_ads'])
        self.assertEqual(query['owner'], ['unassigned'])
        self.assertEqual(query['sla'], ['overdue'])
        self.assertEqual(query['sort'], ['sla'])
        self.assertEqual(query['page'], ['2'])
        self.assertEqual(query['page_size'], ['50'])

    def test_active_chip_removes_only_itself_and_resets_page(self):
        filters = LeadWorkspaceV2Filters(
            query='Amina',
            stage='new',
            language='en',
            source='meta_ads',
            owner='unassigned',
            sla='overdue',
            sort='updated',
            page=4,
            page_size=50,
        )
        locale = SimpleNamespace(locale_code='en', label='English')
        owner = SimpleNamespace(id=7, get_full_name=lambda: '', get_username=lambda: 'owner-7')

        chips = _active_filter_chips(
            filters,
            list_url='/admin/sales/leads/',
            locales=[locale],
            owners=[owner],
        )
        stage_chip = next(item for item in chips if item['key'] == 'stage')
        query = parse_qs(urlsplit(stage_chip['remove_url']).query)

        self.assertNotIn('stage', query)
        self.assertEqual(query['q'], ['Amina'])
        self.assertEqual(query['language'], ['en'])
        self.assertEqual(query['source'], ['meta_ads'])
        self.assertEqual(query['owner'], ['unassigned'])
        self.assertEqual(query['sla'], ['overdue'])
        self.assertEqual(query['sort'], ['updated'])
        self.assertEqual(query['page'], ['1'])
        self.assertEqual(query['page_size'], ['50'])

    def test_sla_meta_distinguishes_overdue_due_soon_scheduled_and_unplanned(self):
        now = timezone.now()
        base = {
            'stage': 'contacted',
            'submitted_at': now - timedelta(hours=2),
            'next_follow_up_at': None,
        }

        self.assertEqual(_sla_meta(SimpleNamespace(**base), now=now, reminder_delta=timedelta(hours=24))['code'], 'unplanned')
        self.assertEqual(
            _sla_meta(
                SimpleNamespace(**(base | {'next_follow_up_at': now - timedelta(minutes=1)})),
                now=now,
                reminder_delta=timedelta(hours=24),
            )['code'],
            'overdue',
        )
        self.assertEqual(
            _sla_meta(
                SimpleNamespace(**(base | {'next_follow_up_at': now + timedelta(hours=2)})),
                now=now,
                reminder_delta=timedelta(hours=24),
            )['code'],
            'due_soon',
        )
        self.assertEqual(
            _sla_meta(
                SimpleNamespace(**(base | {'next_follow_up_at': now + timedelta(days=2)})),
                now=now,
                reminder_delta=timedelta(hours=24),
            )['code'],
            'scheduled',
        )

    def test_every_frozen_activity_type_and_direction_has_a_chinese_label(self):
        expected_types = {
            'note': '内部备注',
            'call': '电话',
            'email': '邮件',
            'whatsapp': 'WhatsApp',
            'meeting': '会议',
            'site_visit': '现场拜访',
            'task': '任务',
            'system': '系统记录',
            'stage_change': '阶段变更',
            'assignment': '负责人分配',
            'conversion': '线索转换',
            'file': '文件',
        }
        for activity_type, label in expected_types.items():
            with self.subTest(activity_type=activity_type):
                item = SimpleNamespace(
                    id=1,
                    activity_type=activity_type,
                    direction='inbound',
                    subject='',
                    body='',
                    actor_user=None,
                    occurred_at=timezone.now(),
                )
                payload = _activity_item(item)
                self.assertEqual(payload['type_label'], label)
                self.assertEqual(payload['subject'], label)
                self.assertEqual(payload['direction'], '客户发起')


class LeadWorkspaceV2DatabaseTests(TestCase):
    """Exercise the real ORM against temporary copies of the unmanaged tables.

    ``TestCase`` keeps every example inside a savepoint and rolls it back.  That
    matters on PostgreSQL: ``TransactionTestCase`` finishes each example with a
    managed-table ``flush``, but the unmanaged ``lead_submission`` table has a
    real foreign key to ``auth_user`` and PostgreSQL correctly refuses to
    truncate only the referenced side.  Rollback isolation covers both managed
    and unmanaged writes without weakening the production model contract or
    requiring ``TRUNCATE ... CASCADE``.
    """
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
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
    )

    @classmethod
    def setUpClass(cls):
        # Create the unmanaged test schema before TestCase opens its class-level
        # atomic block.  This ordering is required by SQLite's schema editor and
        # also keeps PostgreSQL DDL out of the rollback used for fixture data.
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
            # Roll back all per-class/per-test data before removing the schema.
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)

    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user = get_user_model().objects.create_superuser(
            username='workspace-admin',
            email='workspace@example.com',
            password='test-password',
        )
        self.site = Site.objects.create(
            code='siteos_demo',
            name='SiteOS Demo',
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
            form_schema={},
            config_json={},
        )
        self.provider = MarketingProvider.objects.create(
            code='meta',
            name='Meta',
            enabled=True,
            capabilities={},
        )
        self.integration = MarketingIntegration.objects.create(
            site=self.site,
            provider=self.provider,
            name='Meta production dataset',
            integration_type='pixel',
            public_id='123456789012345',
            enabled=True,
            consent_category='marketing',
            config_json={},
        )
        self.lead_overdue = self._lead(
            name='Amina Hassan',
            stage='new',
            source='meta_ads',
            submitted_at=self.now - timedelta(days=2),
            next_follow_up_at=None,
            assignee=None,
        )
        self.lead_due = self._lead(
            name='Omar Farooq',
            stage='contacted',
            source='website',
            submitted_at=self.now - timedelta(hours=4),
            next_follow_up_at=self.now + timedelta(hours=2),
            assignee=self.user,
        )
        self.lead_scheduled = self._lead(
            name='Maya Santos',
            stage='qualified',
            source='email',
            submitted_at=self.now - timedelta(hours=1),
            next_follow_up_at=self.now + timedelta(days=2),
            assignee=self.user,
        )
        LeadConversion.objects.create(
            submission=self.lead_due,
            converted_by_user=self.user,
            metadata_json={'mode': 'test'},
        )
        Activity.objects.create(
            site=self.site,
            submission=self.lead_due,
            actor_user=self.user,
            activity_type='call',
            direction='outbound',
            subject='Discovery call',
            body='Confirmed product scope.',
            metadata_json={},
            occurred_at=self.now,
        )
        Activity.objects.create(
            site=self.site,
            submission=self.lead_due,
            actor_user=self.user,
            activity_type='assignment',
            direction='internal',
            subject='负责人分配已更新',
            body='',
            metadata_json={},
            occurred_at=self.now + timedelta(minutes=1),
        )
        Task.objects.create(
            site=self.site,
            submission=self.lead_due,
            owner_user=self.user,
            created_by_user=self.user,
            title='Confirm target capacity',
            description='',
            task_type='follow_up',
            priority='high',
            status='open',
            due_at=self.now + timedelta(hours=2),
        )
        Task.objects.create(
            site=self.site,
            submission=self.lead_due,
            owner_user=self.user,
            created_by_user=self.user,
            title='Historical completed task',
            description='',
            task_type='follow_up',
            priority='urgent',
            status='completed',
            due_at=self.now - timedelta(days=2),
            completed_at=self.now - timedelta(days=1),
        )
        Task.objects.create(
            site=self.site,
            submission=self.lead_due,
            owner_user=self.user,
            created_by_user=self.user,
            title='Historical cancelled task',
            description='',
            task_type='follow_up',
            priority='urgent',
            status='canceled',
            due_at=self.now - timedelta(days=1),
        )
        ConsentRecord.objects.create(
            submission=self.lead_due,
            purpose='contact',
            decision='granted',
            source='website_form',
            policy_version='2026-09-04',
            evidence_json={'form': 'sales-inquiry'},
        )
        LeadEventOutbox.objects.create(
            submission=self.lead_due,
            integration=self.integration,
            stage_key='lead',
            event_name='Lead',
            event_id='nc-lead-workspace-test',
            status='sent',
            delivery_mode='validation',
            attempts=1,
            match_status='received',
            last_error='token=lead-secret private@example.com +65 9123 4567',
            payload_json={},
        )
        self.factory = RequestFactory()

    def _lead(self, *, name, stage, source, submitted_at, next_follow_up_at, assignee):
        return LeadSubmission.objects.create(
            form=self.form,
            site=self.site,
            locale=self.locale,
            stage=stage,
            assignee=assignee,
            full_name=name,
            email=f'{name.split()[0].lower()}@example.com',
            phone='+10000000000',
            company='Beverage Projects',
            country='UAE',
            message='Need a beverage production line.',
            source_channel=source,
            source_detail='Campaign source',
            buyer_currency='USD',
            payload_json={
                'inquiry_type': 'New complete line',
                'product_category': 'Beverage production line',
                'container_package': '500 ml PET',
                'capacity': '36,000 BPH',
                'timeline': '3-6 months',
                'project_condition': 'New factory / new line',
                'next_step': 'Confirm scope',
            },
            identifiers_json={},
            utm_json={},
            consent_json={'contact': True, 'privacy_notice': True, 'marketing': False},
            config_json={},
            follow_up_notes='',
            next_follow_up_at=next_follow_up_at,
            qualification_score=70,
            qualification_json={'need': True, 'timeline': 'Q4'},
            qualification_notes='Product and timeline confirmed.',
            notification_status='sent',
            submitted_at=submitted_at,
            stage_updated_at=submitted_at,
        )

    def _request(self, params):
        request = self.factory.get('/admin/sales/leads/', params)
        request.user = self.user
        return request

    def test_real_queryset_orders_by_sla_and_builds_routed_detail_context(self):
        request = self._request(
            {
                'q': 'Beverage',
                'sort': 'sla',
                'page': '1',
                'page_size': '20',
                'debug': 'must-not-survive',
            }
        )

        context = build_lead_workspace_v2(
            request,
            site=self.site,
            lead_id=self.lead_due.id,
            list_url='/admin/sales/leads/',
            detail_url_name=None,
            now=self.now,
        )

        self.assertEqual(
            [row['id'] for row in context['rows']],
            [self.lead_overdue.id, self.lead_due.id, self.lead_scheduled.id],
        )
        self.assertEqual(context['stats']['filtered'], 3)
        self.assertEqual(context['stats']['mine'], 2)
        self.assertEqual(context['stats']['overdue'], 1)
        self.assertEqual(context['stats']['due_soon'], 1)
        self.assertFalse(context['quick_views']['all_active'])
        self.assertEqual(context['selected']['language']['code'], 'en')
        self.assertEqual(context['selected']['country'], 'UAE')
        self.assertEqual(context['selected']['product'], 'Beverage production line')
        self.assertEqual(context['selected']['capacity'], '36,000 BPH')
        self.assertEqual(context['selected']['project']['inquiry_type'], 'New complete line')
        self.assertEqual(context['selected']['project']['container'], '500 ml PET')
        self.assertEqual(context['selected']['project']['timeline'], '3-6 months')
        self.assertEqual(context['selected']['project']['condition'], 'New factory / new line')
        self.assertEqual(context['selected']['consent']['records'][0]['purpose'], 'contact')
        self.assertEqual(context['selected']['technical']['event_deliveries'][0]['event_name'], 'Lead')
        self.assertEqual(context['selected']['technical']['event_deliveries'][0]['status'], 'sent')
        event_error = context['selected']['technical']['event_deliveries'][0]['last_error']
        self.assertIn('[已隐藏]', event_error)
        self.assertIn('[邮箱已隐藏]', event_error)
        self.assertIn('[电话已隐藏]', event_error)
        self.assertNotIn('lead-secret', event_error)
        self.assertNotIn('private@example.com', event_error)
        self.assertNotIn('+65 9123 4567', event_error)
        self.assertEqual(context['selected']['sla']['code'], 'due_soon')
        self.assertEqual(context['selected']['next_step']['source'], 'task')
        self.assertEqual(context['selected']['next_step']['title'], 'Confirm target capacity')
        self.assertIn('Confirm target capacity', context['selected']['next_step']['text'])
        self.assertEqual(context['selected']['next_step']['at'], self.now + timedelta(hours=2))
        self.assertEqual(context['selected']['qualification']['score'], 70)
        self.assertIsNotNone(context['selected']['conversion'])
        self.assertEqual(context['selected']['updated_at'], self.lead_due.updated_at)
        self.assertEqual(context['selected']['updated_at_iso'], self.lead_due.updated_at.isoformat())
        self.assertEqual(context['activity_total'], 2)
        self.assertEqual(context['activities'][0]['type'], 'assignment')
        self.assertEqual(context['activities'][0]['type_label'], '负责人分配')
        self.assertEqual(context['activities'][0]['direction_code'], 'internal')
        self.assertEqual(context['activities'][0]['direction'], '内部')
        self.assertEqual(context['activities'][1]['subject'], 'Discovery call')
        self.assertEqual(context['activities'][1]['direction'], '外呼')
        self.assertEqual(context['task_total'], 1)
        self.assertEqual(context['task_history_total'], 3)
        self.assertEqual(len(context['tasks']), 1)
        self.assertEqual(context['tasks'][0]['title'], 'Confirm target capacity')
        self.assertEqual({item['status'] for item in context['task_history']}, {
            'open', 'completed', 'canceled',
        })
        selected_row = next(row for row in context['rows'] if row['id'] == self.lead_due.id)
        self.assertIn('Confirm target capacity', selected_row['next_step'])

        previous_query = parse_qs(urlsplit(context['previous_url']).query)
        next_query = parse_qs(urlsplit(context['next_url']).query)
        self.assertEqual(previous_query['lead_id'], [str(self.lead_overdue.id)])
        self.assertEqual(next_query['lead_id'], [str(self.lead_scheduled.id)])
        self.assertEqual(previous_query['q'], ['Beverage'])
        self.assertEqual(next_query['q'], ['Beverage'])
        self.assertEqual(previous_query['sort'], ['sla'])
        self.assertEqual(next_query['page_size'], ['20'])
        self.assertNotIn('debug', previous_query)
        self.assertNotIn('debug', next_query)

    def test_real_queryset_applies_language_source_owner_and_sla_filters(self):
        context = build_lead_workspace_v2(
            self._request(
                {
                    'language': 'en',
                    'source': 'website',
                    'owner': 'mine',
                    'sla': 'due_soon',
                    'sort': 'newest',
                }
            ),
            site=self.site,
            list_url='/admin/sales/leads/',
            detail_url_name=None,
            now=self.now,
        )

        self.assertEqual([row['id'] for row in context['rows']], [self.lead_due.id])
        self.assertEqual(context['filters'].language, 'en')
        self.assertEqual(context['filters'].source, 'website')
        self.assertEqual(context['filters'].owner, 'mine')
        self.assertEqual(context['filters'].sla, 'due_soon')
        self.assertEqual(
            {chip['key'] for chip in context['active_filter_chips']},
            {'language', 'source', 'owner', 'sla'},
        )
        self.assertFalse(context['quick_views']['all_active'])

    def test_all_leads_quick_view_is_active_only_without_record_filters(self):
        context = build_lead_workspace_v2(
            self._request({'sort': 'updated'}),
            site=self.site,
            list_url='/admin/sales/leads/',
            detail_url_name=None,
            now=self.now,
        )

        self.assertTrue(context['quick_views']['all_active'])
