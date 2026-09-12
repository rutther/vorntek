from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from console.access import (
    GROUP_BY_ROLE,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
)
from console.models import AuditLog
from console.task_versions import task_version_token, task_version_value
from leads.crm_services import append_activity, create_task
from leads.models import (
    Activity,
    Company,
    Contact,
    CrmMutationReceipt,
    LeadFormDefinition,
    LeadSubmission,
    Opportunity,
    SalesTeam,
    SalesTeamMember,
    Task,
)
from leads.task_workflow_services import (
    TaskOptimisticLockError,
    append_manual_activity_workflow,
    create_task_workflow,
    update_task_workflow,
)
from marketing.models import CanonicalEvent
from sitecore.models import Cta, PageRoute, Site, SiteLocale


class TaskViewsV2Tests(TestCase):
    unmanaged_models = (
        Site,
        SiteLocale,
        PageRoute,
        CanonicalEvent,
        Cta,
        LeadFormDefinition,
        SalesTeam,
        SalesTeamMember,
        LeadSubmission,
        Company,
        Contact,
        Opportunity,
        Activity,
        Task,
        CrmMutationReceipt,
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
        groups = {
            role: Group.objects.create(name=GROUP_BY_ROLE[role])
            for role in (ROLE_SALES, ROLE_SALES_MANAGER, ROLE_MARKETING_OPS, ROLE_SYSTEM_ADMIN)
        }
        User = get_user_model()
        self.sales = User.objects.create_user(username='task-sales', password='password')
        self.other_sales = User.objects.create_user(username='task-other', password='password')
        self.manager = User.objects.create_user(username='task-manager', password='password')
        self.marketing = User.objects.create_user(username='task-marketing', password='password')
        self.admin = User.objects.create_user(username='task-admin', password='password')
        self.sales.groups.add(groups[ROLE_SALES])
        self.other_sales.groups.add(groups[ROLE_SALES])
        self.manager.groups.add(groups[ROLE_SALES_MANAGER])
        self.marketing.groups.add(groups[ROLE_MARKETING_OPS])
        self.admin.groups.add(groups[ROLE_SYSTEM_ADMIN])

        self.site = Site.objects.create(
            code='siteos_demo', name='SiteOS Demo', base_url='https://example.com',
            default_locale='en', enabled=True, config_json={},
        )
        self.locale = SiteLocale.objects.create(
            site=self.site, locale_code='en', label='English', direction='ltr',
            is_default=True, enabled=True, sort_order=10,
        )
        self.team = SalesTeam.objects.create(
            site=self.site, code='team-a', name='Team A', enabled=True
        )
        self.other_team = SalesTeam.objects.create(
            site=self.site, code='team-b', name='Team B', enabled=True
        )
        SalesTeamMember.objects.create(team=self.team, user=self.sales, membership_role='member')
        SalesTeamMember.objects.create(team=self.team, user=self.manager, membership_role='manager')
        SalesTeamMember.objects.create(team=self.team, user=self.admin, membership_role='member')
        SalesTeamMember.objects.create(team=self.other_team, user=self.other_sales, membership_role='member')
        self.company = Company.objects.create(
            site=self.site, owner_user=self.sales, team=self.team,
            name='Alpha Bottling', normalized_name='alpha bottling', country='AE',
        )
        self.other_company = Company.objects.create(
            site=self.site, owner_user=self.other_sales, team=self.other_team,
            name='Beta Beverage', normalized_name='beta beverage', country='SA',
        )
        self.now = timezone.now().replace(second=0, microsecond=0)
        self.task = create_task(
            site=self.site, company=self.company, owner_user=self.sales, team=self.team,
            created_by_user=self.sales, title='确认灌装线技术参数',
            description='与客户核对瓶型和目标产能。', due_at=self.now + timedelta(hours=4),
            task_type='call', priority='high',
        )

    def _login(self, user=None):
        self.client.force_login(user or self.sales)

    def _due_input(self, *, days=1):
        return timezone.localtime(self.now + timedelta(days=days)).strftime('%Y-%m-%dT%H:%M')

    def test_version_token_is_opaque_and_tamper_evident(self):
        token = task_version_token(self.task.updated_at)
        self.assertNotIn(self.task.updated_at.isoformat(), token)
        self.assertEqual(task_version_value(token), self.task.updated_at)
        self.assertIsNone(task_version_value(f'{token}x'))

    def test_list_defaults_to_actionable_and_renders_one_h1(self):
        create_task(
            site=self.site, company=self.company, owner_user=self.sales, team=self.team,
            created_by_user=self.sales, title='历史已完成任务', due_at=self.now,
            status='completed', completed_at=self.now, outcome='已确认',
        )
        self._login()
        response = self.client.get(reverse('console:task_workspace_v2'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '确认灌装线技术参数')
        self.assertNotContains(response, '历史已完成任务')
        self.assertEqual(response.content.decode().count('<h1'), 1)
        self.assertContains(response, '相关对象')

    def test_invalid_query_redirects_to_one_canonical_url(self):
        self._login()
        response = self.client.get(reverse('console:task_workspace_v2'), {'status': 'garbage'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('sort=attention', response['Location'])
        self.assertNotIn('garbage', response['Location'])

    def test_sales_cannot_see_other_sales_task_and_manager_sees_managed_team(self):
        other_task = create_task(
            site=self.site, company=self.other_company, owner_user=self.other_sales,
            team=self.other_team, created_by_user=self.other_sales, title='其他团队任务',
            due_at=self.now + timedelta(days=1),
        )
        self._login()
        response = self.client.get(reverse('console:task_workspace_detail_v2', args=(other_task.pk,)))
        self.assertEqual(response.status_code, 404)
        self._login(self.manager)
        response = self.client.get(reverse('console:task_workspace_detail_v2', args=(self.task.pk,)), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.task.title)

    def test_marketing_role_is_forbidden_not_empty(self):
        self._login(self.marketing)
        response = self.client.get(reverse('console:task_workspace_v2'))
        self.assertEqual(response.status_code, 403)

    def test_target_search_is_scoped_and_bounded(self):
        self._login()
        response = self.client.get(
            reverse('console:task_target_search_v2'), {'type': 'company', 'q': 'Bottling'}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item['id'] for item in payload['items']], [self.company.pk])
        response = self.client.get(
            reverse('console:task_target_search_v2'), {'type': 'company', 'q': 'Beta'}
        )
        self.assertEqual(response.json()['items'], [])

    def test_create_is_durable_idempotent_and_conflicting_reuse_is_409(self):
        self._login()
        payload = {
            'idempotency_token': 'task-create-token-0001', 'target_type': 'company',
            'target_id': self.company.pk, 'title': '发送更新版报价', 'description': '',
            'task_type': 'quote', 'priority': 'urgent', 'due_at': self._due_input(),
            'owner_user': self.sales.pk,
        }
        first = self.client.post(reverse('console:task_create'), payload)
        self.assertEqual(first.status_code, 200)
        second = self.client.post(reverse('console:task_create'), payload)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()['replayed'])
        self.assertEqual(Task.objects.filter(title='发送更新版报价').count(), 1)
        self.assertEqual(CrmMutationReceipt.objects.filter(mutation_scope='task.create').count(), 1)
        conflict = self.client.post(
            reverse('console:task_create'), {**payload, 'title': '使用相同令牌的不同内容'}
        )
        self.assertEqual(conflict.status_code, 409)

    def _opportunity_payload(self, *, token='opportunity-create-token-0001', **overrides):
        payload = {
            'idempotency_token': token,
            'company_id': self.company.pk,
            'name': 'Alpha aseptic filling line',
            'owner_user': self.sales.pk,
            'value_amount': '125000.00',
            'currency': 'usd',
            'probability': '15',
            'expected_close_date': '',
            'product_scope': 'Aseptic filling line',
            'capacity_target': '12000 BPH',
            'packaging_format': 'PET bottle',
            'next_step': 'Confirm bottle samples.',
            'next_follow_up_at': self._due_input(),
        }
        payload.update(overrides)
        return payload

    def test_opportunity_create_is_scoped_durable_and_idempotent(self):
        self._login()
        url = reverse('console:opportunity_create_v2')
        payload = self._opportunity_payload()
        first = self.client.post(url, payload)
        self.assertEqual(first.status_code, 200)
        opportunity = Opportunity.objects.get(pk=first.json()['opportunity_id'])
        self.assertEqual(opportunity.company_id, self.company.pk)
        self.assertEqual(opportunity.owner_user_id, self.sales.pk)
        self.assertEqual(opportunity.team_id, self.team.pk)
        self.assertEqual(opportunity.stage, 'qualification')
        self.assertEqual(opportunity.value_amount, Decimal('125000.00'))
        self.assertEqual(opportunity.currency, 'USD')
        self.assertEqual(opportunity.source_detail, '客户详情手动创建')
        self.assertEqual(
            Activity.objects.filter(opportunity=opportunity, activity_type='system').count(), 1
        )
        self.assertEqual(
            AuditLog.objects.filter(action='crm_opportunity_created', entity_id=opportunity.pk).count(), 1
        )

        replay = self.client.post(url, payload)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()['replayed'])
        self.assertEqual(Opportunity.objects.filter(name=payload['name']).count(), 1)
        self.assertEqual(
            CrmMutationReceipt.objects.filter(mutation_scope='opportunity.create').count(), 1
        )
        conflict = self.client.post(url, {**payload, 'name': 'Different project name'})
        self.assertEqual(conflict.status_code, 409)

    def test_opportunity_create_hides_other_team_and_forbids_marketing(self):
        url = reverse('console:opportunity_create_v2')
        self._login()
        response = self.client.post(
            url,
            self._opportunity_payload(
                token='opportunity-other-team-0001',
                company_id=self.other_company.pk,
                owner_user=self.other_sales.pk,
            ),
        )
        self.assertEqual(response.status_code, 404)
        self._login(self.marketing)
        self.assertEqual(self.client.post(url, self._opportunity_payload()).status_code, 403)

    def test_opportunity_create_rolls_back_when_audit_fails(self):
        self._login()
        with patch('console.audit.record_audit', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse('console:opportunity_create_v2'),
                    self._opportunity_payload(token='opportunity-audit-failure-0001'),
                )
        self.assertFalse(Opportunity.objects.filter(name='Alpha aseptic filling line').exists())
        self.assertFalse(CrmMutationReceipt.objects.filter(mutation_scope='opportunity.create').exists())
        self.assertFalse(Activity.objects.filter(subject__startswith='创建销售机会：').exists())

    def test_start_is_idempotent_without_duplicate_evidence(self):
        self._login()
        url = reverse('console:task_start_v2', args=(self.task.pk,))
        version = task_version_token(self.task.updated_at)
        first = self.client.post(url, {'version': version})
        self.assertEqual(first.status_code, 200)
        second = self.client.post(url, {'version': version})
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()['changed'])
        self.assertEqual(Activity.objects.filter(metadata_json__event='started').count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='crm_task_started').count(), 1)

    def test_complete_requires_real_outcome_and_can_create_next_task_atomically(self):
        self._login()
        url = reverse('console:task_complete', args=(self.task.pk,))
        version = task_version_token(self.task.updated_at)
        invalid = self.client.post(url, {'version': version, 'outcome': '   '})
        self.assertEqual(invalid.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'open')
        valid = self.client.post(url, {
            'version': version, 'outcome': '客户确认技术参数，等待正式 PI。',
            'create_next_task': '1', 'next_title': '发送正式 PI',
            'next_task_type': 'quote', 'next_priority': 'high',
            'next_due_at': self._due_input(days=2), 'next_owner_user': self.sales.pk,
            'next_description': '按确认配置出具。',
        })
        self.assertEqual(valid.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'completed')
        self.assertEqual(self.task.outcome, '客户确认技术参数，等待正式 PI。')
        next_task = Task.objects.get(pk=valid.json()['next_task_id'])
        self.assertEqual(next_task.team_id, self.task.team_id)
        self.assertEqual(next_task.company_id, self.task.company_id)

    def test_stale_opaque_version_returns_409_and_preserves_current_record(self):
        self._login()
        stale = task_version_token(self.task.updated_at)
        Task.objects.filter(pk=self.task.pk).update(title='并发更新后的标题', updated_at=self.now + timedelta(minutes=2))
        response = self.client.post(reverse('console:task_update_v2', args=(self.task.pk,)), {
            'version': stale, 'title': '客户端旧标题', 'description': '',
            'task_type': 'call', 'priority': 'high', 'due_at': self._due_input(),
            'owner_user': self.sales.pk,
        })
        self.assertEqual(response.status_code, 409)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, '并发更新后的标题')
        self.assertNotEqual(response.json()['current_version'], stale)

    def test_cancel_requires_reason_and_is_terminal_read_only(self):
        self._login()
        url = reverse('console:task_cancel_v2', args=(self.task.pk,))
        version = task_version_token(self.task.updated_at)
        self.assertEqual(self.client.post(url, {'version': version, 'reason': ''}).status_code, 400)
        response = self.client.post(url, {'version': version, 'reason': '客户项目暂停。'})
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'canceled')
        detail = self.client.get(reverse('console:task_workspace_detail_v2', args=(self.task.pk,)), follow=True)
        self.assertNotContains(detail, '编辑 / 改期')
        self.assertNotContains(detail, '完成</button>')
        self.assertContains(detail, '客户项目暂停。')

    def test_activity_correction_appends_and_does_not_mutate_original(self):
        original = append_activity(
            site=self.site, actor=self.sales, company=self.company, activity_type='call',
            direction='outbound', subject='客户电话', body='原始记录内容。', occurred_at=self.now,
        )
        self._login()
        response = self.client.post(
            reverse('console:activity_correction_v2', args=(original.pk,)),
            {'idempotency_token': 'activity-correction-0001', 'subject': '更正：客户电话', 'body': '实际为客户主动来电。'},
        )
        self.assertEqual(response.status_code, 200)
        original.refresh_from_db()
        self.assertEqual(original.body, '原始记录内容。')
        correction = Activity.objects.get(pk=response.json()['activity_id'])
        self.assertEqual(correction.direction, 'internal')
        self.assertEqual(correction.metadata_json['correction_of_activity_id'], original.pk)

    def test_activity_and_receipt_roll_back_when_audit_fails(self):
        before_activity = Activity.objects.count()
        before_receipt = CrmMutationReceipt.objects.count()

        def fail_audit(**_kwargs):
            raise RuntimeError('audit unavailable')

        with self.assertRaises(RuntimeError):
            append_manual_activity_workflow(
                site=self.site, actor=self.sales, company=self.company,
                activity_type='meeting', direction='outbound', subject='方案会议',
                body='讨论技术方案。', occurred_at=self.now,
                idempotency_token='activity-create-token-0001', audit_recorder=fail_audit,
            )
        self.assertEqual(Activity.objects.count(), before_activity)
        self.assertEqual(CrmMutationReceipt.objects.count(), before_receipt)

    def test_task_and_receipt_roll_back_when_audit_fails(self):
        before_task = Task.objects.count()
        before_activity = Activity.objects.count()

        def fail_audit(**_kwargs):
            raise RuntimeError('audit unavailable')

        with self.assertRaises(RuntimeError):
            create_task_workflow(
                site=self.site, actor=self.sales, owner_user=self.sales,
                company=self.company, title='事务回滚任务', due_at=self.now + timedelta(days=1),
                idempotency_token='task-rollback-token-0001', audit_recorder=fail_audit,
            )
        self.assertEqual(Task.objects.count(), before_task)
        self.assertEqual(Activity.objects.count(), before_activity)
        self.assertFalse(CrmMutationReceipt.objects.filter(mutation_scope='task.create').exists())

    def test_service_rejects_stale_version_before_mutating(self):
        stale = self.task.updated_at
        Task.objects.filter(pk=self.task.pk).update(updated_at=self.now + timedelta(minutes=5))
        with self.assertRaises(TaskOptimisticLockError):
            update_task_workflow(
                task_id=self.task.pk, actor=self.sales, expected_updated_at=stale,
                title='旧版本编辑', description='', task_type='call', priority='normal',
                due_at=self.now + timedelta(days=1), owner_user=self.sales,
            )
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, '确认灌装线技术参数')

    def test_bulk_assign_never_changes_team_and_reports_partial_results(self):
        terminal = create_task(
            site=self.site, company=self.company, owner_user=self.sales, team=self.team,
            created_by_user=self.sales, title='不可分配的历史任务', due_at=self.now,
            status='completed', completed_at=self.now, outcome='已完成',
        )
        self._login(self.manager)
        response = self.client.post(reverse('console:task_bulk_assign_v2'), {
            'owner_user': self.manager.pk,
            'items_json': json.dumps([
                {'id': self.task.pk, 'version': task_version_token(self.task.updated_at)},
                {'id': terminal.pk, 'version': task_version_token(terminal.updated_at)},
            ]),
        })
        self.assertEqual(response.status_code, 207)
        payload = response.json()
        self.assertEqual(payload['succeeded'], 1)
        self.assertEqual([item['status'] for item in payload['results']], ['succeeded', 'invalid'])
        self.task.refresh_from_db()
        self.assertEqual(self.task.owner_user_id, self.manager.pk)
        self.assertEqual(self.task.team_id, self.team.pk)
        self.assertEqual(Activity.objects.filter(metadata_json__event='reassigned').count(), 1)

    def test_bulk_assign_rejects_more_than_one_hundred_items(self):
        self._login(self.manager)
        response = self.client.post(reverse('console:task_bulk_assign_v2'), {
            'owner_user': self.manager.pk,
            'items_json': json.dumps([
                {'id': index + 1, 'version': 'invalid'} for index in range(101)
            ]),
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('items_json', response.json()['errors'])
