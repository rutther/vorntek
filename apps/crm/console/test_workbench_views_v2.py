from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from console.access import (
    GROUP_BY_ROLE,
    ROLE_CONTENT_OPS,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SYSTEM_ADMIN,
)


def workbench_payload():
    return {
        'variant': 'sales',
        'title': '我的工作',
        'description': '先处理逾期，再推进今天到期的客户动作。',
        'metrics': [
            {
                'label': '逾期待处理',
                'value': 2,
                'description': '需要立即响应',
                'tone': 'danger',
                'icon': 'clock-alert',
            },
            {
                'label': '今天到期',
                'value': 3,
                'tone': 'warning',
                'icon': 'calendar-check-2',
            },
        ],
        'sections': [
            {
                'key': 'priority',
                'title': '优先队列',
                'description': '按响应时限和下一跟进时间排序。',
                'action': {
                    'label': '查看全部线索',
                    'href': '/admin/sales/leads/',
                },
                'items': [
                    {
                        'kind': 'lead',
                        'id': 7,
                        'title': 'Amina Hassan',
                        'meta': 'Gulf Bottling · 沙特阿拉伯',
                        'detail': '首次 WhatsApp 联系',
                        'status_label': '已逾期',
                        'status_tone': 'danger',
                        'due_label': '逾期 2 小时',
                        'icon': 'user-plus',
                        'href': '/admin/sales/leads/7/',
                    }
                ],
            }
        ],
        'primary_action': {
            'label': '打开线索收件箱',
            'href': '/admin/sales/leads/',
            'icon': 'inbox',
        },
    }


class WorkbenchV2ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.users = {}
        for role in (
            ROLE_SALES,
            ROLE_MARKETING_OPS,
            ROLE_CONTENT_OPS,
            ROLE_SYSTEM_ADMIN,
        ):
            group = Group.objects.create(name=GROUP_BY_ROLE[role])
            user = User.objects.create_user(
                username=f'test-{role}',
                password='LocalOnly!234',
            )
            user.groups.add(group)
            cls.users[role] = user

    def _get_workbench(self, role=ROLE_SALES, payload=None):
        self.client.force_login(self.users[role])
        site = SimpleNamespace(pk=11, id=11, code='new-crown', name='New Crown')
        locale = SimpleNamespace(pk=21, id=21, locale_code='zh')
        content_navigation = (
            patch('console.navigation.content_navigation_entries', return_value=[])
            if role == ROLE_CONTENT_OPS else nullcontext()
        )
        with (
            patch(
                'console.workbench_views.default_site_locale',
                return_value=(site, locale),
            ),
            patch(
                'console.workbench_views._build_role_workbench',
                return_value=payload or workbench_payload(),
            ) as builder,
            content_navigation,
        ):
            response = self.client.get('/admin/workbench/?locale=zh')
        return response, builder, site, locale

    def test_workbench_renders_the_v2_shell_and_query_contract(self):
        response, builder, site, locale = self._get_workbench()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'console/v2/pages/workbench.html')
        self.assertContains(response, '我的工作')
        self.assertContains(response, 'Amina Hassan')
        self.assertContains(response, '逾期 2 小时')
        self.assertContains(response, '/admin/sales/leads/7/')
        self.assertContains(response, '/static/console/vendor/tabler/1.4.0/tabler.min.css')
        self.assertContains(response, '/static/console/v2/vorntek-v2.css?v=20260830-6')
        self.assertContains(response, '/static/console/v2/shell.js')
        self.assertNotContains(response, '/static/console/v2/leads.js')
        self.assertNotContains(response, '/static/console/admin-shell.css')
        self.assertNotContains(response, '/static/console/admin-shell.js')
        self.assertEqual(response.content.count(b'<h1'), 1)
        builder.assert_called_once()
        kwargs = builder.call_args.kwargs
        self.assertEqual(kwargs['site'], site)
        self.assertEqual(kwargs['locale'], locale)
        self.assertEqual(kwargs['user'], self.users[ROLE_SALES])
        self.assertEqual(kwargs['params'].get('locale'), 'zh')

    def test_empty_workbench_section_uses_compact_status_row(self):
        payload = workbench_payload()
        payload['sections'][0]['items'] = []
        payload['sections'][0]['empty_title'] = '没有逾期线索'
        payload['sections'][0]['empty_description'] = '当前队列已经清空。'

        response, _, _, _ = self._get_workbench(payload=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="nc-workbench-empty"')
        self.assertContains(response, '没有逾期线索')
        self.assertContains(response, '当前队列已经清空。')
        self.assertNotContains(response, 'class="empty py-5"')

    def test_full_workbench_request_resolves_role_groups_once(self):
        # Exercise middleware, the real role-aware builder, navigation and
        # role-label rendering together.  This focused shell test stubs the
        # content capability queries because unmanaged grant tables are
        # covered by the dedicated navigation and route-permission suites.
        self.client.force_login(self.users[ROLE_CONTENT_OPS])
        site = SimpleNamespace(pk=11, id=11, code='new-crown', name='New Crown')
        locale = SimpleNamespace(pk=21, id=21, locale_code='zh')
        with (
            patch(
                'console.workbench_views.default_site_locale',
                return_value=(site, locale),
            ),
            patch('console.navigation.content_navigation_entries', return_value=[]),
            patch(
                'console.workbench_queries.effective_content_capabilities',
                return_value=frozenset(),
            ),
            CaptureQueriesContext(connection) as captured,
        ):
            response = self.client.get('/admin/workbench/?locale=zh')

        role_group_queries = [
            query['sql']
            for query in captured.captured_queries
            if 'auth_group' in query['sql'].lower()
        ]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['workbench']['variant'], 'content_ops')
        self.assertEqual(len(role_group_queries), 1, role_group_queries)

    def test_workbench_navigation_is_role_specific_and_content_fails_closed(self):
        expectations = {
            ROLE_SALES: ('销售', '营销', '网站内容', '系统'),
            ROLE_MARKETING_OPS: ('营销', '销售', '网站内容', '系统'),
            ROLE_CONTENT_OPS: (None, '销售', '营销', '网站内容'),
            ROLE_SYSTEM_ADMIN: ('网站内容', None, None, None),
        }
        for role, (present, *absent) in expectations.items():
            with self.subTest(role=role):
                response, _builder, _site, _locale = self._get_workbench(role)
                self.assertContains(response, '>工作台<')
                if present:
                    self.assertContains(response, f'>{present}<')
                for label in absent:
                    if label:
                        self.assertNotContains(response, f'>{label}<')

    def test_empty_workbench_has_a_persistent_non_toast_result(self):
        payload = {
            'variant': 'content',
            'title': '内容待办',
            'description': '完成内容与预览闭环。',
            'metrics': [],
            'sections': [],
            'empty_state': {
                'title': '当前没有内容待办',
                'description': '新的草稿或构建异常会显示在这里。',
            },
        }
        response, _builder, _site, _locale = self._get_workbench(
            ROLE_CONTENT_OPS,
            payload,
        )

        self.assertContains(response, '当前没有内容待办')
        self.assertContains(response, '新的草稿或构建异常会显示在这里。')

    def test_admin_root_redirects_to_workbench_and_preserves_query(self):
        self.client.force_login(self.users[ROLE_SALES])

        response = self.client.get('/admin/?q=pump&locale=zh')

        self.assertRedirects(
            response,
            '/admin/workbench/?q=pump&locale=zh',
            fetch_redirect_response=False,
        )

    def test_legacy_sales_workbench_redirect_preserves_query_and_drops_view(self):
        self.client.force_login(self.users[ROLE_SALES])

        response = self.client.get(
            '/admin/sales/?view=workbench&locale=zh&q=Saudi&page=2'
        )

        self.assertRedirects(
            response,
            '/admin/workbench/?locale=zh&q=Saudi&page=2',
            fetch_redirect_response=False,
        )

    def test_legacy_sales_default_and_invalid_views_canonicalize_to_workbench(self):
        self.client.force_login(self.users[ROLE_SALES])

        default_response = self.client.get('/admin/sales/?locale=zh')
        invalid_response = self.client.get('/admin/sales/?view=unknown&locale=zh')

        self.assertEqual(default_response.url, '/admin/workbench/?locale=zh')
        self.assertEqual(invalid_response.url, '/admin/workbench/?locale=zh')

    def test_legacy_get_redirects_never_redirect_post_bodies(self):
        self.client.force_login(self.users[ROLE_SALES])

        home_response = self.client.post('/admin/', {'payload': 'must-not-move'})
        sales_response = self.client.post(
            '/admin/sales/?view=workbench',
            {'payload': 'must-not-move'},
        )

        self.assertEqual(home_response.status_code, 405)
        self.assertEqual(sales_response.status_code, 405)
        self.assertNotIn(home_response.status_code, {301, 302, 307, 308})
        self.assertNotIn(sales_response.status_code, {301, 302, 307, 308})

    def test_workbench_is_get_only(self):
        self.client.force_login(self.users[ROLE_SALES])

        response = self.client.post('/admin/workbench/', {'unsafe': 'write'})

        self.assertEqual(response.status_code, 405)

    def test_canonical_landing_aliases_are_get_only(self):
        self.client.force_login(self.users[ROLE_SYSTEM_ADMIN])

        marketing_response = self.client.post(
            '/admin/marketing/attribution/',
            {'payload': 'must-not-move'},
        )
        content_response = self.client.post(
            '/admin/content/articles/',
            {'payload': 'must-not-move'},
        )

        self.assertEqual(marketing_response.status_code, 405)
        self.assertEqual(content_response.status_code, 405)

    def test_content_canonical_alias_remains_fail_closed_for_content_ops(self):
        self.client.force_login(self.users[ROLE_CONTENT_OPS])

        site = SimpleNamespace(pk=11, id=11, code='new-crown', enabled=True)
        locale = SimpleNamespace(pk=21, id=21, site_id=11, locale_code='en', enabled=True)
        with (
            patch('console.views._resolved_content_site_locale', return_value=(site, locale)),
            patch('console.views.effective_content_capabilities', return_value=frozenset()),
        ):
            response = self.client.get('/admin/content/articles/')

        self.assertEqual(response.status_code, 403)
