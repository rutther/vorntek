from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from console.access import (
    GROUP_BY_ROLE,
    ROLE_CONTENT_OPS,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
)
from console.content_access import (
    ASSETS_READ,
    CONTENT_READ,
    CONTENT_SET_PUBLISHED,
    CONTENT_WRITE,
    RELEASES_READ,
)
from console.models import ContentAccessGrant
from leads.models import (
    Company,
    Contact,
    LeadConversion,
    LeadEventOutbox,
    LeadFormDefinition,
    LeadInboundEvent,
    LeadSubmission,
    Opportunity,
    PrivacyRequest,
    SalesTeam,
    SalesTeamMember,
    Task,
)
from marketing.models import CanonicalEvent, MarketingIntegration, MarketingProvider
from sitecore.models import (
    Article,
    Category,
    Cta,
    MediaAsset,
    PageRoute,
    Release,
    ReleaseBuild,
    Site,
    SiteLocale,
)

from .workbench_queries import build_role_workbench


class RoleWorkbenchQueryTests(TestCase):
    """Database-backed scope, ordering and stable-payload contract tests."""

    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        Category,
        Article,
        MediaAsset,
        CanonicalEvent,
        Cta,
        MarketingProvider,
        MarketingIntegration,
        LeadFormDefinition,
        SalesTeam,
        SalesTeamMember,
        LeadSubmission,
        PrivacyRequest,
        Company,
        Contact,
        Opportunity,
        LeadConversion,
        Task,
        LeadEventOutbox,
        LeadInboundEvent,
        Release,
        ReleaseBuild,
        ContentAccessGrant,
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
            role: Group.objects.create(name=group_name)
            for role, group_name in GROUP_BY_ROLE.items()
        }
        self.sales = self._user('sales', ROLE_SALES)
        self.other_sales = self._user('other-sales', ROLE_SALES)
        self.manager = self._user('manager', ROLE_SALES_MANAGER)
        self.other_manager = self._user('other-manager', ROLE_SALES_MANAGER)
        self.marketing = self._user('marketing', ROLE_MARKETING_OPS)
        self.admin = self._user('admin', ROLE_SYSTEM_ADMIN)
        self.content = self._user('content', ROLE_CONTENT_OPS)
        self.unassigned = self._user('unassigned')
        self.inactive_unassigned = self._user('inactive-unassigned', active=False)

        self.site, self.locale, self.form = self._site_bundle('primary')
        self.other_site, self.other_locale, self.other_form = self._site_bundle('other')

        self.team = SalesTeam.objects.create(
            site=self.site,
            code='managed',
            name='Managed team',
            enabled=True,
        )
        self.other_team = SalesTeam.objects.create(
            site=self.site,
            code='other',
            name='Other team',
            enabled=True,
        )
        self.disabled_team = SalesTeam.objects.create(
            site=self.site,
            code='disabled',
            name='Disabled team',
            enabled=False,
        )
        self.cross_site_team = SalesTeam.objects.create(
            site=self.other_site,
            code='cross-site',
            name='Cross-site team',
            enabled=True,
        )
        for team, user, role in (
            (self.team, self.sales, 'member'),
            (self.team, self.manager, 'manager'),
            (self.other_team, self.other_sales, 'member'),
            (self.other_team, self.other_manager, 'manager'),
            (self.disabled_team, self.manager, 'manager'),
            (self.cross_site_team, self.manager, 'manager'),
        ):
            SalesTeamMember.objects.create(
                team=team,
                user=user,
                membership_role=role,
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
            name='Primary Meta CAPI',
            integration_type='pixel',
            public_id='1234567890',
            secret_ref='vault:meta-token',
            enabled=True,
            config_json={},
        )
        self.other_integration = MarketingIntegration.objects.create(
            site=self.other_site,
            provider=self.provider,
            name='Other Meta CAPI',
            integration_type='pixel',
            public_id='9988776655',
            secret_ref='vault:other-meta-token',
            enabled=True,
            config_json={},
        )

    def _user(self, username, role=None, *, active=True):
        user = get_user_model().objects.create_user(
            username=username,
            email=f'{username}@example.com',
            password='test-password',
            is_active=active,
        )
        if role:
            user.groups.add(self.groups[role])
        return user

    def _site_bundle(self, code):
        site = Site.objects.create(
            code=code,
            name=f'{code.title()} Site',
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
            code=f'{code}-inquiry',
            name=f'{code.title()} inquiry',
            category='sales',
            channel='website',
            status='active',
            capi_enabled=True,
            form_schema={},
            config_json={},
        )
        return site, locale, form

    def _lead(
        self,
        *,
        name,
        assignee,
        team,
        site=None,
        stage='new',
        submitted_delta=timedelta(hours=2),
        next_follow_up_at=None,
        notification_status='sent',
        notification_error='',
    ):
        selected_site = site or self.site
        form = self.form if selected_site == self.site else self.other_form
        locale = self.locale if selected_site == self.site else self.other_locale
        return LeadSubmission.objects.create(
            form=form,
            site=selected_site,
            locale=locale,
            stage=stage,
            assignee=assignee,
            team=team,
            full_name=name,
            email=f'{name.lower().replace(" ", ".")}@example.com',
            company=f'{name} Company',
            country='AE',
            source_url='https://example.com/contact',
            source_channel='website',
            buyer_currency='USD',
            payload_json={},
            identifiers_json={},
            utm_json={},
            consent_json={},
            config_json={},
            qualification_json={},
            notification_status=notification_status,
            notification_error=notification_error,
            submitted_at=self.now - submitted_delta,
            stage_updated_at=self.now - submitted_delta,
            next_follow_up_at=next_follow_up_at,
        )

    def _task(self, *, title, owner, team, due_delta, site=None, status='open', lead=None):
        return Task.objects.create(
            site=site or self.site,
            submission=lead,
            owner_user=owner,
            team=team,
            created_by_user=self.manager,
            title=title,
            status=status,
            due_at=self.now + due_delta,
        )

    def _outbox(
        self,
        *,
        integration,
        lead,
        status,
        event_name,
        next_attempt_at=None,
        last_error='provider error',
    ):
        return LeadEventOutbox.objects.create(
            submission=lead,
            integration=integration,
            stage_key='qualified',
            event_name=event_name,
            event_id=f'evt-{lead.id}-{event_name}',
            payload_json={},
            status=status,
            attempts=1,
            last_error=last_error,
            response_json={},
            next_attempt_at=next_attempt_at,
        )

    def _inbound(self, *, integration, status='failed', suffix='1'):
        return LeadInboundEvent.objects.create(
            integration=integration,
            provider_code=integration.provider.code,
            event_type='leadgen',
            external_event_id=f'inbound-{integration.id}-{suffix}',
            payload_json={},
            status=status,
            attempts=1,
            last_error='webhook parse failed' if status == 'failed' else '',
            received_at=self.now - timedelta(minutes=15),
        )

    def _build(self, user, *, site=None, locale=None, params=None, now=None):
        selected_site = site or self.site
        selected_locale = locale or (
            self.locale if selected_site == self.site else self.other_locale
        )
        return build_role_workbench(
            site=selected_site,
            locale=selected_locale,
            user=user,
            params={} if params is None else params,
            now=now or self.now,
        )

    @staticmethod
    def _sections(payload):
        return {section['key']: section for section in payload['sections']}

    def test_sales_rep_sees_only_owned_actionable_tasks_and_leads_in_stable_order(self):
        owned_late = self._lead(
            name='Owned late',
            assignee=self.sales,
            team=self.team,
            submitted_delta=timedelta(hours=1),
        )
        owned_early = self._lead(
            name='Owned early',
            assignee=self.sales,
            team=self.team,
            submitted_delta=timedelta(hours=8),
        )
        other_lead = self._lead(
            name='Other rep',
            assignee=self.other_sales,
            team=self.other_team,
        )
        closed_lead = self._lead(
            name='Closed',
            assignee=self.sales,
            team=self.team,
            stage='won',
        )
        later_task = self._task(
            title='Later own task', owner=self.sales, team=self.team,
            due_delta=timedelta(hours=3), lead=owned_late,
        )
        earlier_task = self._task(
            title='Earlier own task', owner=self.sales, team=self.team,
            due_delta=timedelta(hours=-2), lead=owned_early,
        )
        self._task(
            title='Other owner task', owner=self.other_sales, team=self.team,
            due_delta=timedelta(hours=-4), lead=other_lead,
        )
        self._task(
            title='Completed own task', owner=self.sales, team=self.team,
            due_delta=timedelta(hours=-8), status='completed', lead=closed_lead,
        )

        payload = self._build(
            self.sales,
            params={'role': ROLE_SYSTEM_ADMIN, 'limit': '20'},
        )
        sections = self._sections(payload)

        self.assertEqual(payload['variant'], 'sales_rep')
        self.assertEqual(
            [item['id'] for item in sections['tasks']['items']],
            [earlier_task.id, later_task.id],
        )
        self.assertEqual(
            [item['id'] for item in sections['leads']['items']],
            [owned_early.id, owned_late.id],
        )
        self.assertNotIn(other_lead.id, [item['id'] for item in sections['leads']['items']])
        self.assertEqual(sections['tasks']['count'], 2)
        self.assertEqual(sections['leads']['count'], 2)
        self.assertTrue(all(item['href'] for item in sections['tasks']['items']))
        self.assertTrue(all(item['href'] for item in sections['leads']['items']))

    def test_sales_manager_scope_is_only_managed_enabled_teams_for_selected_site(self):
        managed_lead = self._lead(
            name='Managed', assignee=self.sales, team=self.team,
        )
        other_team_lead = self._lead(
            name='Other team', assignee=self.other_sales, team=self.other_team,
        )
        disabled_lead = self._lead(
            name='Disabled team', assignee=self.sales, team=self.disabled_team,
        )
        cross_site_lead = self._lead(
            name='Cross site', assignee=self.sales, team=self.cross_site_team,
            site=self.other_site,
        )
        managed_task = self._task(
            title='Managed task', owner=self.other_sales, team=self.team,
            due_delta=timedelta(hours=1), lead=managed_lead,
        )
        self._task(
            title='Other task', owner=self.sales, team=self.other_team,
            due_delta=timedelta(hours=1), lead=other_team_lead,
        )
        self._task(
            title='Disabled task', owner=self.sales, team=self.disabled_team,
            due_delta=timedelta(hours=1), lead=disabled_lead,
        )
        self._task(
            title='Cross-site task', owner=self.sales, team=self.cross_site_team,
            due_delta=timedelta(hours=1), lead=cross_site_lead,
            site=self.other_site,
        )

        payload = self._build(self.manager)
        sections = self._sections(payload)

        self.assertEqual(payload['variant'], 'sales_manager')
        self.assertEqual([item['id'] for item in sections['leads']['items']], [managed_lead.id])
        self.assertEqual([item['id'] for item in sections['tasks']['items']], [managed_task.id])

    def test_task_does_not_leak_a_related_lead_outside_the_task_scope(self):
        outside_lead = self._lead(
            name='Must stay hidden',
            assignee=self.other_sales,
            team=self.other_team,
        )
        task = self._task(
            title='Authorized task title',
            owner=self.sales,
            team=self.team,
            due_delta=timedelta(hours=1),
            lead=outside_lead,
        )

        for user in (self.sales, self.manager):
            with self.subTest(user=user.username):
                sections = self._sections(self._build(user))
                self.assertNotIn(
                    task.id,
                    [row['id'] for row in sections['tasks']['items']],
                )

    def test_sales_empty_state_and_query_budget_are_stable(self):
        with CaptureQueriesContext(connection) as captured:
            payload = self._build(self.sales)

        self.assertEqual(payload['variant'], 'sales_rep')
        self.assertIsNotNone(payload['empty_state'])
        self.assertEqual([section['count'] for section in payload['sections']], [0, 0])
        self.assertLessEqual(len(captured), 5)

    def test_today_metric_uses_local_day_boundary_for_aware_and_naive_now(self):
        # 14:00 UTC is 22:00 in Asia/Shanghai.  The first task is therefore
        # still today at 23:30 local; the second is tomorrow at 00:30 local.
        aware_now = datetime(2026, 8, 30, 14, 0, tzinfo=UTC)
        visible_lead = self._lead(
            name='Local boundary lead',
            assignee=self.sales,
            team=self.team,
        )
        same_day_task = Task.objects.create(
            site=self.site,
            submission=visible_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.manager,
            title='Local 23:30',
            status='open',
            due_at=aware_now + timedelta(hours=1, minutes=30),
        )
        next_day_task = Task.objects.create(
            site=self.site,
            submission=visible_lead,
            owner_user=self.sales,
            team=self.team,
            created_by_user=self.manager,
            title='Local next day 00:30',
            status='open',
            due_at=aware_now + timedelta(hours=2, minutes=30),
        )

        with timezone.override('Asia/Shanghai'):
            aware_payload = self._build(self.sales, now=aware_now)
            naive_payload = self._build(
                self.sales,
                now=datetime(2026, 8, 30, 22, 0),
            )

        for payload in (aware_payload, naive_payload):
            with self.subTest(now_kind='aware' if payload is aware_payload else 'naive'):
                today_metric = next(
                    metric for metric in payload['metrics']
                    if metric['label'] == '今日任务'
                )
                self.assertEqual(today_metric['value'], 1)
                task_ids = [
                    item['id']
                    for item in self._sections(payload)['tasks']['items']
                ]
                self.assertEqual(task_ids, [same_day_task.id, next_day_task.id])

    def test_marketing_queue_covers_failure_partial_retry_inbound_and_is_site_scoped(self):
        lead = self._lead(name='Marketing lead', assignee=self.sales, team=self.team)
        other_lead = self._lead(
            name='Other marketing lead', assignee=self.sales,
            team=self.cross_site_team, site=self.other_site,
        )
        due = self._outbox(
            integration=self.integration,
            lead=lead,
            status='failed',
            event_name='due_failure',
            next_attempt_at=self.now - timedelta(minutes=1),
            last_error='Failed for private@example.com at +65 9123 4567',
        )
        future = self._outbox(
            integration=self.integration,
            lead=lead,
            status='failed',
            event_name='future_failure',
            next_attempt_at=self.now + timedelta(hours=1),
        )
        unscheduled = self._outbox(
            integration=self.integration,
            lead=lead,
            status='failed',
            event_name='unscheduled_failure',
            next_attempt_at=None,
        )
        partial = self._outbox(
            integration=self.integration,
            lead=lead,
            status='partial',
            event_name='partial_delivery',
        )
        other = self._outbox(
            integration=self.other_integration,
            lead=other_lead,
            status='failed',
            event_name='other_site_failure',
            next_attempt_at=self.now - timedelta(hours=1),
        )
        inbound = self._inbound(integration=self.integration)
        inbound.last_error = 'Rejected another@example.com at +1 415 555 0100'
        inbound.save(update_fields=['last_error'])
        self._inbound(integration=self.other_integration, suffix='other')

        payload = self._build(self.marketing, params={'limit': '10'})
        sections = self._sections(payload)

        self.assertEqual(payload['variant'], 'marketing_ops')
        self.assertEqual(
            {item['id'] for item in sections['delivery_failures']['items']},
            {due.id, future.id, unscheduled.id},
        )
        self.assertEqual(
            [item['id'] for item in sections['partial_deliveries']['items']],
            [partial.id],
        )
        self.assertEqual(
            {item['id'] for item in sections['retry_due']['items']},
            {due.id, unscheduled.id},
        )
        self.assertEqual(sections['retry_due']['count'], 2)
        self.assertEqual(
            [item['id'] for item in sections['inbound_failures']['items']],
            [inbound.id],
        )
        all_ids = {
            item['id']
            for section in payload['sections']
            for item in section['items']
            if isinstance(item['id'], int)
        }
        self.assertNotIn(other.id, all_ids)
        self.assertEqual(
            [item['id'] for item in sections['integration_blockers']['items']],
            ['meta.leadgen', 'whatsapp.cloud_api', 'google.data_manager'],
        )
        self.assertTrue(
            all(
                item['href'].startswith('/admin/marketing')
                for section in payload['sections']
                for item in section['items']
            )
        )
        rendered_payload = repr(payload)
        self.assertNotIn('private@example.com', rendered_payload)
        self.assertNotIn('+65 9123 4567', rendered_payload)
        self.assertNotIn('another@example.com', rendered_payload)
        self.assertNotIn('+1 415 555 0100', rendered_payload)
        self.assertIn('[邮箱已隐藏]', rendered_payload)

    def test_marketing_overview_counts_unscheduled_failed_event_as_due_retry(self):
        from .marketing_queries import build_marketing_overview

        lead = self._lead(name='Retry contract', assignee=self.sales, team=self.team)
        self._outbox(
            integration=self.integration,
            lead=lead,
            status='failed',
            event_name='past_retry',
            next_attempt_at=self.now - timedelta(minutes=1),
        )
        self._outbox(
            integration=self.integration,
            lead=lead,
            status='failed',
            event_name='unscheduled_retry',
            next_attempt_at=None,
        )
        self._outbox(
            integration=self.integration,
            lead=lead,
            status='failed',
            event_name='future_retry',
            next_attempt_at=self.now + timedelta(hours=1),
        )
        maxed = self._outbox(
            integration=self.integration,
            lead=lead,
            status='failed',
            event_name='max_attempts',
            next_attempt_at=None,
        )
        LeadEventOutbox.objects.filter(id=maxed.id).update(attempts=8)
        disabled_integration = MarketingIntegration.objects.create(
            site=self.site,
            provider=self.provider,
            name='Disabled retry contract',
            integration_type='pixel',
            public_id='777777',
            secret_ref='vault:disabled-retry',
            enabled=False,
            config_json={},
        )
        self._outbox(
            integration=disabled_integration,
            lead=lead,
            status='failed',
            event_name='disabled_integration',
            next_attempt_at=None,
        )

        with patch('console.marketing_queries._integration_state', return_value={}):
            overview = build_marketing_overview(site=self.site)

        self.assertEqual(overview['event_health']['failed'], 5)
        self.assertEqual(overview['event_health']['due_retries'], 2)

        workbench = self._build(self.marketing)
        retry_metric = next(
            item for item in workbench['metrics'] if item['label'] == '重试到期'
        )
        self.assertEqual(retry_metric['value'], 2)
        retry_section = self._sections(workbench)['retry_due']
        self.assertEqual(retry_section['count'], 2)
        self.assertEqual(len(retry_section['items']), 2)

    def test_marketing_query_budget_is_bounded_not_per_item(self):
        lead = self._lead(name='Many failures', assignee=self.sales, team=self.team)
        for index in range(12):
            self._outbox(
                integration=self.integration,
                lead=lead,
                status='failed',
                event_name=f'failure_{index}',
                next_attempt_at=self.now - timedelta(minutes=index + 1),
            )

        with CaptureQueriesContext(connection) as captured:
            payload = self._build(self.marketing, params={'limit': '5'})

        sections = self._sections(payload)
        self.assertEqual(sections['delivery_failures']['count'], 12)
        self.assertEqual(len(sections['delivery_failures']['items']), 5)
        self.assertLessEqual(len(captured), 8)

    def test_system_admin_sees_only_system_anomalies_and_selected_site_failures(self):
        lead = self._lead(
            name='Notification failure', assignee=self.sales, team=self.team,
            notification_status='failed', notification_error='SMTP rejected',
        )
        normal_sales_lead = self._lead(
            name='Normal sales lead', assignee=self.sales, team=self.team,
        )
        self._task(
            title='Sales task must not appear', owner=self.sales, team=self.team,
            due_delta=timedelta(hours=-1), lead=normal_sales_lead,
        )
        other_notification = self._lead(
            name='Other notification', assignee=self.sales,
            team=self.cross_site_team, site=self.other_site,
            notification_status='failed', notification_error='Other site',
        )
        outbox = self._outbox(
            integration=self.integration, lead=lead, status='failed',
            event_name='system_outbox',
        )
        other_outbox = self._outbox(
            integration=self.other_integration, lead=other_notification,
            status='failed', event_name='other_system_outbox',
        )
        inbound = self._inbound(integration=self.integration, suffix='system')
        release = Release.objects.create(
            site=self.site,
            release_key='primary-release',
            status='draft',
            snapshot_manifest={},
        )
        failed_build = ReleaseBuild.objects.create(
            release=release,
            build_key='primary-build',
            status='failed',
            log_excerpt='compiler failure',
            config_json={},
        )
        other_release = Release.objects.create(
            site=self.other_site,
            release_key='other-release',
            status='draft',
            snapshot_manifest={},
        )
        other_build = ReleaseBuild.objects.create(
            release=other_release,
            build_key='other-build',
            status='failed',
            log_excerpt='other failure',
            config_json={},
        )

        payload = self._build(self.admin)
        sections = self._sections(payload)

        self.assertEqual(payload['variant'], 'system_admin')
        self.assertEqual(payload['title'], '系统运行概览')
        self.assertEqual(
            [item['id'] for item in sections['access_anomalies']['items']],
            [self.unassigned.id],
        )
        self.assertEqual(
            [item['id'] for item in sections['release_failures']['items']],
            [failed_build.id],
        )
        delivery_ids = {item['id'] for item in sections['delivery_anomalies']['items']}
        self.assertEqual(delivery_ids, {outbox.id, inbound.id})
        self.assertTrue(all(
            item['href'].startswith(reverse('console:marketing_events'))
            for item in sections['delivery_anomalies']['items']
        ))
        self.assertEqual(
            sections['delivery_anomalies']['action']['href'],
            f'{reverse("console:marketing_events")}?status=needs_attention',
        )
        self.assertNotIn(other_outbox.id, delivery_ids)
        self.assertNotIn(other_build.id, [item['id'] for item in sections['release_failures']['items']])
        self.assertEqual(
            [item['id'] for item in sections['notification_failures']['items']],
            [lead.id],
        )
        self.assertEqual(
            sections['notification_failures']['action']['href'],
            reverse('console:marketing_forms'),
        )
        self.assertNotIn('tasks', sections)
        self.assertNotIn('leads', sections)

    def test_content_ops_without_grants_is_explicit_zero_data_zero_action(self):
        self._lead(name='Hidden from content', assignee=self.sales, team=self.team)
        payload = self._build(
            self.content,
            params={'role': ROLE_SYSTEM_ADMIN, 'limit': '50'},
        )

        self.assertEqual(payload['variant'], 'content_ops')
        self.assertEqual(payload['metrics'], [])
        self.assertEqual(payload['sections'], [])
        self.assertIsNone(payload['primary_action'])
        self.assertIsNotNone(payload['empty_state'])
        self.assertIsNone(payload['empty_state']['action'])
        self.assertIn('核对站点、语言和具体能力授权', payload['empty_state']['description'])

    def test_locale_only_asset_and_release_grants_do_not_leak_site_wide_workbench_data(self):
        for capability in (ASSETS_READ, RELEASES_READ):
            ContentAccessGrant.objects.create(
                user=self.content,
                site=self.site,
                locale=self.locale,
                capability=capability,
            )
        MediaAsset.objects.create(
            site=self.site,
            asset_type='image',
            original_name='locale-only-secret.png',
            title='Locale-only secret asset',
            mime_type='image/png',
            file_ext='.png',
            file_size_bytes=17,
            sha256='c' * 64,
            storage_path='storage/assets/images/locale-only-secret.png',
            public_path='/assets/images/locale-only-secret.png',
            status='active',
        )
        release = Release.objects.create(
            site=self.site,
            release_key='locale-only-secret-release',
            status='failed',
            snapshot_manifest={},
        )
        ReleaseBuild.objects.create(
            release=release,
            build_key='locale-only-secret-build',
            status='failed',
            config_json={},
        )

        locale_only = self._build(self.content)

        self.assertEqual(locale_only['metrics'], [])
        self.assertEqual(locale_only['sections'], [])
        self.assertIsNone(locale_only['primary_action'])
        self.assertNotIn('Locale-only secret asset', repr(locale_only))
        self.assertNotIn('/assets/images/locale-only-secret.png', repr(locale_only))
        self.assertNotIn('locale-only-secret-release', repr(locale_only))
        self.assertNotIn('locale-only-secret-build', repr(locale_only))

        for capability in (ASSETS_READ, RELEASES_READ):
            ContentAccessGrant.objects.create(
                user=self.content,
                site=self.site,
                capability=capability,
            )

        site_wide = self._build(self.content)
        sections = self._sections(site_wide)
        self.assertEqual(
            [item['title'] for item in sections['recent_assets']['items']],
            ['Locale-only secret asset'],
        )
        self.assertEqual(
            [item['title'] for item in sections['preview_failures']['items']],
            ['locale-only-secret-build'],
        )

    def test_content_ops_workbench_uses_exact_granted_scope_and_hides_other_site(self):
        for capability in (CONTENT_READ, CONTENT_WRITE):
            ContentAccessGrant.objects.create(
                user=self.content,
                site=self.site,
                locale=self.locale,
                capability=capability,
            )
        for capability in (ASSETS_READ, RELEASES_READ):
            ContentAccessGrant.objects.create(
                user=self.content,
                site=self.site,
                capability=capability,
            )

        category = Category.objects.create(
            site=self.site,
            locale=self.locale,
            code='news',
            name='News',
            slug='news',
            status='published',
        )
        route = PageRoute.objects.create(
            site=self.site,
            locale=self.locale,
            path='/en/articles/granted/',
            route_type='article',
            status='draft',
            page_title='Granted article',
            meta_description='',
        )
        article = Article.objects.create(
            route=route,
            category=category,
            title='Granted article',
            slug='granted',
            status='draft',
        )
        other_route = PageRoute.objects.create(
            site=self.other_site,
            locale=self.other_locale,
            path='/en/articles/hidden/',
            route_type='article',
            status='draft',
            page_title='Hidden article',
            meta_description='',
        )
        Article.objects.create(
            route=other_route,
            title='Hidden article',
            slug='hidden',
            status='draft',
        )
        MediaAsset.objects.create(
            site=self.site,
            asset_type='image',
            original_name='granted.png',
            title='Granted asset',
            mime_type='image/png',
            file_ext='.png',
            file_size_bytes=8,
            sha256='a' * 64,
            storage_path='storage/assets/images/granted.png',
            public_path='/assets/images/granted.png',
            status='active',
        )
        MediaAsset.objects.create(
            site=self.other_site,
            asset_type='image',
            original_name='hidden.png',
            title='Hidden asset',
            mime_type='image/png',
            file_ext='.png',
            file_size_bytes=8,
            sha256='b' * 64,
            storage_path='storage/assets/images/hidden.png',
            public_path='/assets/images/hidden.png',
            status='active',
        )
        release = Release.objects.create(
            site=self.site,
            release_key='content-primary-release',
            status='failed',
            snapshot_manifest={},
        )
        failed_build = ReleaseBuild.objects.create(
            release=release,
            build_key='content-primary-build',
            status='failed',
            log_excerpt='host path must not surface',
            config_json={},
        )
        other_release = Release.objects.create(
            site=self.other_site,
            release_key='content-other-release',
            status='failed',
            snapshot_manifest={},
        )
        ReleaseBuild.objects.create(
            release=other_release,
            build_key='content-other-build',
            status='failed',
            config_json={},
        )

        payload = self._build(self.content)
        sections = self._sections(payload)

        self.assertEqual(payload['variant'], 'content_ops')
        self.assertEqual(
            [item['id'] for item in sections['content_attention']['items']],
            [article.id],
        )
        self.assertEqual(
            [item['title'] for item in sections['recent_assets']['items']],
            ['Granted asset'],
        )
        self.assertEqual(
            [item['id'] for item in sections['preview_failures']['items']],
            [failed_build.id],
        )
        self.assertNotIn('Hidden', repr(payload))
        self.assertNotIn('host path must not surface', repr(payload))

    def test_content_workbench_does_not_link_to_forbidden_published_article_editor(self):
        for capability in (CONTENT_READ, CONTENT_WRITE):
            ContentAccessGrant.objects.create(
                user=self.content,
                site=self.site,
                locale=self.locale,
                capability=capability,
            )
        route = PageRoute.objects.create(
            site=self.site,
            locale=self.locale,
            path='/en/articles/published-seo-attention/',
            route_type='article',
            status='published',
            page_title='Published SEO attention',
            meta_description='',
        )
        article = Article.objects.create(
            route=route,
            title='Published SEO attention',
            slug='published_seo_attention',
            status='published',
        )

        restricted = self._build(self.content)
        restricted_item = next(
            item
            for item in self._sections(restricted)['content_attention']['items']
            if item['id'] == article.id
        )
        self.assertEqual(
            restricted_item['href'],
            f'{reverse("console:content_articles")}?locale={self.locale.locale_code}',
        )

        ContentAccessGrant.objects.create(
            user=self.content,
            site=self.site,
            locale=self.locale,
            capability=CONTENT_SET_PUBLISHED,
        )
        authorized = self._build(self.content)
        authorized_item = next(
            item
            for item in self._sections(authorized)['content_attention']['items']
            if item['id'] == article.id
        )
        self.assertIn(
            reverse('console:article_edit', args=[article.id]),
            authorized_item['href'],
        )

    def test_unassigned_and_multi_role_accounts_fail_closed_or_use_fixed_precedence(self):
        restricted = self._build(self.unassigned, params={'role': ROLE_SYSTEM_ADMIN})
        self.assertEqual(restricted['variant'], 'restricted')
        self.assertEqual(restricted['sections'], [])
        self.assertIsNone(restricted['primary_action'])

        self.sales.groups.add(self.groups[ROLE_MARKETING_OPS])
        multi_role = self._build(self.sales)
        self.assertEqual(multi_role['variant'], 'marketing_ops')

    def test_role_resolution_delegates_to_access_and_preserves_cached_type(self):
        from .workbench_queries import _role_keys

        cached_roles = frozenset({ROLE_SALES})
        with patch(
            'console.workbench_queries.access.user_role_keys',
            return_value=cached_roles,
        ) as resolver:
            result = _role_keys(self.sales)

        self.assertIs(result, cached_roles)
        resolver.assert_called_once_with(self.sales)

    def test_payload_has_stable_template_keys_and_missing_due_is_safe(self):
        from .workbench_queries import _due_meta

        payload = self._build(self.content)
        self.assertEqual(
            set(payload),
            {
                'variant',
                'title',
                'description',
                'metrics',
                'sections',
                'primary_action',
                'empty_state',
            },
        )
        self.assertEqual(_due_meta(None, now=self.now), ('', 'secondary'))
