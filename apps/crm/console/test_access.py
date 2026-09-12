from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from console.access import (
    ALL_CONSOLE_ROLES,
    CONTENT_ROLES,
    CONTENT_VIEW_NAMES,
    ConsoleAccessMiddleware,
    GROUP_BY_ROLE,
    LEAD_ATTRIBUTION_ROLES,
    LEAD_ATTRIBUTION_VIEW_NAMES,
    LEAD_ROLES,
    MARKETING_VIEW_NAMES,
    PRIMARY_ROLE_PRIORITY,
    ROLE_CONTENT_OPS,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    SALES_ROLES,
    SALES_VIEW_NAMES,
    SYSTEM_VIEW_NAMES,
    activity_queryset_for_user,
    allowed_roles_for_view,
    crm_owned_queryset_for_user,
    default_console_route_name,
    lead_attribution_queryset_for_user,
    lead_queryset_for_user,
    primary_role_label,
    safe_next_url,
    user_role_keys,
)


def role_user(role: str, *, superuser: bool = False):
    user = Mock()
    user.is_authenticated = True
    user.is_superuser = superuser
    user.groups.values_list.return_value = [GROUP_BY_ROLE[role]] if role else []
    return user


class ConsoleAccessTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = ConsoleAccessMiddleware(lambda request: None)

    def request_for(self, view_name: str, user):
        request = self.factory.get(f'/admin/{view_name}/')
        request.user = user
        request.resolver_match = SimpleNamespace(namespace='console', url_name=view_name)
        return request

    def test_role_cache_is_request_scoped_and_immutable(self):
        user = role_user(ROLE_SALES)

        self.assertIsNone(
            self.middleware.process_view(
                self.request_for('workbench', user), None, (), {}
            )
        )
        cached_roles = user_role_keys(user)
        self.assertIsInstance(cached_roles, frozenset)
        self.assertEqual(cached_roles, {ROLE_SALES})
        self.assertEqual(primary_role_label(user), '销售')
        user.groups.values_list.assert_called_once_with('name', flat=True)

        # Reusing the model object for a new request must resolve current group
        # membership instead of carrying the previous request's cache forward.
        user.groups.values_list.return_value = [GROUP_BY_ROLE[ROLE_MARKETING_OPS]]
        self.assertIsNone(
            self.middleware.process_view(
                self.request_for('marketing', user), None, (), {}
            )
        )
        self.assertEqual(user_role_keys(user), {ROLE_MARKETING_OPS})
        self.assertEqual(user.groups.values_list.call_count, 2)

    def test_role_helpers_do_not_implicitly_cache_outside_middleware(self):
        user = role_user(ROLE_SALES)

        self.assertEqual(user_role_keys(user), {ROLE_SALES})
        user.groups.values_list.return_value = [GROUP_BY_ROLE[ROLE_MARKETING_OPS]]
        self.assertEqual(user_role_keys(user), {ROLE_MARKETING_OPS})
        self.assertEqual(user.groups.values_list.call_count, 2)

    def test_sales_can_open_leads_but_not_marketing(self):
        user = role_user(ROLE_SALES)

        self.assertIsNone(self.middleware.process_view(self.request_for('sales_workspace', user), None, (), {}))
        self.assertIsNone(self.middleware.process_view(self.request_for('leads', user), None, (), {}))
        with self.assertRaises(PermissionDenied):
            self.middleware.process_view(self.request_for('marketing', user), None, (), {})

    def test_role_sets_separate_sales_operations_from_attribution_reads(self):
        self.assertIn(ROLE_CONTENT_OPS, ALL_CONSOLE_ROLES)
        self.assertEqual(GROUP_BY_ROLE[ROLE_CONTENT_OPS], 'SiteOS Content Ops')
        self.assertIn(ROLE_CONTENT_OPS, PRIMARY_ROLE_PRIORITY)
        self.assertEqual(
            SALES_ROLES,
            frozenset({ROLE_SALES, ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN}),
        )
        self.assertEqual(LEAD_ROLES, SALES_ROLES)
        self.assertEqual(LEAD_ATTRIBUTION_ROLES, SALES_ROLES)
        self.assertFalse(LEAD_ATTRIBUTION_VIEW_NAMES)
        self.assertEqual(
            CONTENT_ROLES,
            frozenset({ROLE_CONTENT_OPS, ROLE_SYSTEM_ADMIN}),
        )

    def test_marketing_can_read_aggregate_attribution_but_not_raw_sales_records(self):
        user = role_user(ROLE_MARKETING_OPS)
        raw_lead_views = (
            'leads',
            'lead_submission_export_csv',
            'lead_submission_export_xlsx',
        )
        operational_views = (
            'sales_workspace',
            'lead_submission_edit',
            'lead_submission_bulk_stage',
            'workbench_data',
            'pipeline_data',
            'crm_collection_data',
            'crm_record_detail',
            'company_update',
            'contact_update',
            'opportunity_update',
            'crm_attachments',
            'crm_attachment_download',
            'crm_attachment_archive',
            'lead_convert',
            'activity_create',
            'task_create',
            'opportunity_create_v2',
            'task_complete',
            'opportunity_stage',
            'saved_view_save',
            'sales_bulk_assign',
            'lead_workspace_v2',
            'lead_workspace_detail_v2',
            'lead_disposition_v2',
            'lead_bulk_assign_v2',
        )

        self.assertIsNone(
            self.middleware.process_view(
                self.request_for('marketing_attribution', user), None, (), {}
            )
        )
        for view_name in raw_lead_views + operational_views:
            with self.subTest(view_name=view_name), self.assertRaises(PermissionDenied):
                self.middleware.process_view(
                    self.request_for(view_name, user), None, (), {}
                )

    def test_marketing_query_scope_never_returns_raw_leads(self):
        user = role_user(ROLE_MARKETING_OPS)
        lead_queryset = Mock()
        crm_queryset = Mock()
        activity_queryset = Mock()

        self.assertIs(
            lead_attribution_queryset_for_user(lead_queryset, user),
            lead_queryset.none.return_value,
        )
        operational_leads = Mock()
        self.assertIs(
            lead_queryset_for_user(operational_leads, user),
            operational_leads.none.return_value,
        )
        self.assertIs(
            crm_owned_queryset_for_user(crm_queryset, user),
            crm_queryset.none.return_value,
        )
        self.assertIs(
            activity_queryset_for_user(activity_queryset, user),
            activity_queryset.none.return_value,
        )
        crm_queryset.none.assert_called_once_with()
        activity_queryset.none.assert_called_once_with()

    def test_sales_roles_and_admin_keep_all_sales_operational_routes(self):
        operational_views = (
            'sales_workspace',
            'lead_submission_edit',
            'lead_submission_bulk_stage',
            'workbench_data',
            'pipeline_data',
            'crm_collection_data',
            'crm_record_detail',
            'company_update',
            'contact_update',
            'opportunity_update',
            'crm_attachments',
            'crm_attachment_download',
            'crm_attachment_archive',
            'lead_convert',
            'activity_create',
            'task_create',
            'opportunity_create_v2',
            'task_complete',
            'opportunity_stage',
            'saved_view_save',
            'sales_bulk_assign',
            'lead_workspace_v2',
            'lead_workspace_detail_v2',
            'lead_disposition_v2',
            'lead_bulk_assign_v2',
        )

        for role in (ROLE_SALES, ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN):
            user = role_user(role)
            for view_name in operational_views:
                with self.subTest(role=role, view_name=view_name):
                    self.assertIsNone(
                        self.middleware.process_view(
                            self.request_for(view_name, user), None, (), {}
                        )
                    )

    def test_marketing_and_admin_keep_marketing_form_routes(self):
        marketing_views = (
            'marketing',
            'marketing_forms',
            'marketing_privacy',
            'marketing_form_create',
            'marketing_form_edit',
            'lead_form_create',
            'lead_form_edit',
        )

        for role in (ROLE_MARKETING_OPS, ROLE_SYSTEM_ADMIN):
            user = role_user(role)
            for view_name in marketing_views:
                with self.subTest(role=role, view_name=view_name):
                    self.assertIsNone(
                        self.middleware.process_view(
                            self.request_for(view_name, user), None, (), {}
                        )
                    )

    def test_smtp_configuration_is_system_admin_only(self):
        marketing = role_user(ROLE_MARKETING_OPS)
        admin = role_user(ROLE_SYSTEM_ADMIN)

        for view_name in ('system_email', 'smtp_settings'):
            with self.subTest(view_name=view_name, role='marketing'), self.assertRaises(PermissionDenied):
                self.middleware.process_view(
                    self.request_for(view_name, marketing), None, (), {}
                )
            self.assertIsNone(
                self.middleware.process_view(
                    self.request_for(view_name, admin), None, (), {}
                )
            )

    def test_retired_legacy_outbox_mutations_are_system_admin_only(self):
        legacy_mutations = (
            'lead_outbox_dispatch',
            'lead_outbox_dispatch_pending',
        )
        marketing = role_user(ROLE_MARKETING_OPS)
        admin = role_user(ROLE_SYSTEM_ADMIN)

        for view_name in legacy_mutations:
            with self.subTest(view_name=view_name, role=ROLE_MARKETING_OPS):
                with self.assertRaises(PermissionDenied):
                    self.middleware.process_view(
                        self.request_for(view_name, marketing), None, (), {}
                    )
            with self.subTest(view_name=view_name, role=ROLE_SYSTEM_ADMIN):
                self.assertIsNone(
                    self.middleware.process_view(
                        self.request_for(view_name, admin), None, (), {}
                    )
                )

    def test_home_and_workbench_remain_available_to_every_registered_console_role(self):
        for view_name in ('home', 'workbench'):
            self.assertEqual(allowed_roles_for_view(view_name), ALL_CONSOLE_ROLES)
            for role in ALL_CONSOLE_ROLES:
                with self.subTest(view_name=view_name, role=role):
                    self.assertIsNone(
                        self.middleware.process_view(
                            self.request_for(view_name, role_user(role)), None, (), {}
                        )
                    )

    def test_every_registered_role_starts_in_the_neutral_workbench(self):
        for role in ALL_CONSOLE_ROLES:
            with self.subTest(role=role):
                self.assertEqual(
                    default_console_route_name(role_user(role)),
                    'console:workbench',
                )

        # A role label is not a content grant.  In particular, the default
        # landing must never send an ungranted operator to content_articles.
        self.assertNotEqual(
            default_console_route_name(role_user(ROLE_CONTENT_OPS)),
            'console:content_articles',
        )

    def test_content_ops_passes_only_the_coarse_content_domain_gate(self):
        user = role_user(ROLE_CONTENT_OPS)
        self.assertEqual(primary_role_label(user), '内容运营')
        for view_name in CONTENT_VIEW_NAMES:
            with self.subTest(view_name=view_name):
                self.assertEqual(allowed_roles_for_view(view_name), CONTENT_ROLES)
                self.assertIsNone(
                    self.middleware.process_view(
                        self.request_for(view_name, user), None, (), {}
                    )
                )

        protected_views = (
            MARKETING_VIEW_NAMES
            | LEAD_ATTRIBUTION_VIEW_NAMES
            | SALES_VIEW_NAMES
            | SYSTEM_VIEW_NAMES
        )
        for view_name in protected_views:
            with self.subTest(view_name=view_name), self.assertRaises(PermissionDenied):
                self.middleware.process_view(
                    self.request_for(view_name, user), None, (), {}
                )

    def test_marketing_no_longer_inherits_legacy_site_wide_content_writes(self):
        marketing_user = role_user(ROLE_MARKETING_OPS)
        system_user = role_user(ROLE_SYSTEM_ADMIN)

        for view_name in CONTENT_VIEW_NAMES:
            with self.subTest(view_name=view_name, role='marketing'), self.assertRaises(PermissionDenied):
                self.middleware.process_view(
                    self.request_for(view_name, marketing_user), None, (), {}
                )
            self.assertIsNone(
                self.middleware.process_view(
                    self.request_for(view_name, system_user), None, (), {}
                )
            )

    def test_new_marketing_attribution_entry_is_marketing_and_admin_only(self):
        for role in (ROLE_MARKETING_OPS, ROLE_SYSTEM_ADMIN):
            self.assertIsNone(
                self.middleware.process_view(
                    self.request_for('marketing_attribution', role_user(role)), None, (), {}
                )
            )
        for role in (ROLE_CONTENT_OPS, ROLE_SALES, ROLE_SALES_MANAGER):
            with self.subTest(role=role), self.assertRaises(PermissionDenied):
                self.middleware.process_view(
                    self.request_for('marketing_attribution', role_user(role)), None, (), {}
                )

    def test_superuser_is_treated_as_system_admin(self):
        user = role_user(ROLE_SYSTEM_ADMIN, superuser=True)

        self.assertIsNone(self.middleware.process_view(self.request_for('marketing', user), None, (), {}))
        self.assertIsNone(self.middleware.process_view(self.request_for('articles', user), None, (), {}))

    def test_sales_cannot_call_marketing_or_privacy_apis_directly(self):
        user = role_user(ROLE_SALES)
        protected_views = (
            'marketing_overview_data',
            'integration_evidence_data',
            'integration_diagnose',
            'marketing_outbox_retry',
            'privacy_requests_data',
            'privacy_request_create',
            'privacy_request_update',
            'privacy_request_register',
            'privacy_request_manage',
            'privacy_request_export',
            'privacy_request_execute',
        )

        for view_name in protected_views:
            with self.subTest(view_name=view_name), self.assertRaises(PermissionDenied):
                self.middleware.process_view(self.request_for(view_name, user), None, (), {})

    def test_sensitive_privacy_actions_remain_system_admin_only(self):
        marketing_user = role_user(ROLE_MARKETING_OPS)
        system_user = role_user(ROLE_SYSTEM_ADMIN)
        for view_name in (
            'marketing_privacy',
            'privacy_request_register',
            'privacy_request_manage',
        ):
            self.assertIsNone(
                self.middleware.process_view(self.request_for(view_name, marketing_user), None, (), {})
            )
            self.assertIsNone(
                self.middleware.process_view(self.request_for(view_name, system_user), None, (), {})
            )

    def test_user_and_team_management_remain_system_admin_only(self):
        sales_user = role_user(ROLE_SALES)
        marketing_user = role_user(ROLE_MARKETING_OPS)
        system_user = role_user(ROLE_SYSTEM_ADMIN)
        system_views = (
            'system_users',
            'system_user_create',
            'system_user_edit',
            'system_teams',
            'system_team_create',
            'system_team_edit',
            'system_email',
            'smtp_settings',
            'system_data_governance',
        )

        for view_name in system_views:
            with self.subTest(view_name=view_name, role='sales'), self.assertRaises(PermissionDenied):
                self.middleware.process_view(self.request_for(view_name, sales_user), None, (), {})
            with self.subTest(view_name=view_name, role='marketing'), self.assertRaises(PermissionDenied):
                self.middleware.process_view(self.request_for(view_name, marketing_user), None, (), {})
            self.assertIsNone(
                self.middleware.process_view(self.request_for(view_name, system_user), None, (), {})
            )
        system_views = (
            'privacy_request_export',
            'privacy_request_execute',
            'retention_policy_data',
            'retention_policy_update',
            'retention_policy_execute',
        )

        for view_name in system_views:
            with self.subTest(view_name=view_name), self.assertRaises(PermissionDenied):
                self.middleware.process_view(self.request_for(view_name, marketing_user), None, (), {})
            self.assertIsNone(
                self.middleware.process_view(self.request_for(view_name, system_user), None, (), {})
            )

    def test_anonymous_console_request_redirects_to_login(self):
        response = self.middleware.process_view(
            self.request_for('leads', AnonymousUser()),
            None,
            (),
            {},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_safe_next_url_rejects_external_host(self):
        request = self.factory.post('/admin/leads/', secure=True, HTTP_HOST='vorntek.example')

        self.assertEqual(
            safe_next_url(request, 'https://attacker.example/path', '/admin/leads/'),
            '/admin/leads/',
        )
        self.assertEqual(
            safe_next_url(request, '/admin/leads/?stage=new', '/admin/leads/'),
            '/admin/leads/?stage=new',
        )


class RawLeadRouteAuthorizationTests(TestCase):
    """Exercise the real URL middleware boundary, not only helper sets."""

    def setUp(self):
        marketing_group = Group.objects.create(name=GROUP_BY_ROLE[ROLE_MARKETING_OPS])
        self.marketing_user = get_user_model().objects.create_user(
            username='raw-lead-marketing',
            password='test-password',
        )
        self.marketing_user.groups.add(marketing_group)
        self.client.force_login(self.marketing_user)

    def test_marketing_operator_receives_403_for_raw_lead_routes(self):
        for route_name in (
            'console:leads',
            'console:lead_submission_export_csv',
            'console:lead_submission_export_xlsx',
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 403)
