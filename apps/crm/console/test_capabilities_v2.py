from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase

from console.access import (
    ALL_CONSOLE_ROLES,
    GROUP_BY_ROLE,
    ROLE_CONTENT_OPS,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
)
from console.capabilities import (
    DataScope,
    SalesCapability,
    can_sales,
    capability_scope_for_roles,
    require_sales,
    sales_capability_scope,
)


def role_user(*roles: str, superuser: bool = False, user_id: int = 7):
    user = Mock()
    user.is_authenticated = True
    user.is_superuser = superuser
    user.pk = user_id
    user.id = user_id
    user.groups.values_list.return_value = [
        GROUP_BY_ROLE[role] for role in roles if role in GROUP_BY_ROLE
    ]
    return user


class SalesCapabilityPolicyTests(SimpleTestCase):
    def test_role_matrix_exposes_the_frozen_data_scopes(self):
        expected = {
            ROLE_SALES: {
                SalesCapability.READ: DataScope.OWN,
                SalesCapability.WRITE: DataScope.OWN,
                SalesCapability.ASSIGN: DataScope.NONE,
                SalesCapability.CONVERT: DataScope.OWN,
                SalesCapability.ATTRIBUTION_READ: DataScope.OWN,
            },
            ROLE_SALES_MANAGER: {
                capability: (
                    DataScope.NONE
                    if capability is SalesCapability.POOL_IMPORT
                    else DataScope.TEAM
                )
                for capability in SalesCapability
            },
            ROLE_MARKETING_OPS: {
                SalesCapability.READ: DataScope.NONE,
                SalesCapability.WRITE: DataScope.NONE,
                SalesCapability.ASSIGN: DataScope.NONE,
                SalesCapability.CONVERT: DataScope.NONE,
                SalesCapability.ATTRIBUTION_READ: DataScope.ALL,
            },
            ROLE_SYSTEM_ADMIN: {
                capability: DataScope.ALL for capability in SalesCapability
            },
            ROLE_CONTENT_OPS: {
                capability: DataScope.NONE for capability in SalesCapability
            },
        }

        self.assertEqual(set(expected), set(ALL_CONSOLE_ROLES))

        for role, capabilities in expected.items():
            for capability, scope in capabilities.items():
                with self.subTest(role=role, capability=capability):
                    self.assertEqual(
                        capability_scope_for_roles({role}, capability),
                        scope,
                    )

    def test_sales_may_read_write_and_convert_only_owned_records(self):
        user = role_user(ROLE_SALES, user_id=7)
        own_lead = SimpleNamespace(assignee_id=7, team_id=10)
        another_lead = SimpleNamespace(assignee_id=8, team_id=10)

        for capability in (
            SalesCapability.READ,
            SalesCapability.WRITE,
            SalesCapability.CONVERT,
            SalesCapability.ATTRIBUTION_READ,
        ):
            with self.subTest(capability=capability, record='own'):
                self.assertTrue(can_sales(user, capability, record=own_lead))
            with self.subTest(capability=capability, record='another'):
                self.assertFalse(can_sales(user, capability, record=another_lead))

        self.assertFalse(can_sales(user, SalesCapability.ASSIGN))
        self.assertFalse(can_sales(user, SalesCapability.ASSIGN, record=own_lead))

    def test_sales_scope_supports_crm_owner_field_and_mapping_records(self):
        user = role_user(ROLE_SALES, user_id=7)

        self.assertTrue(
            can_sales(
                user,
                SalesCapability.WRITE,
                record=SimpleNamespace(owner_user_id=7, team_id=20),
            )
        )
        self.assertTrue(
            can_sales(
                user,
                SalesCapability.READ,
                record={'owner_user_id': 7, 'team_id': 20},
            )
        )
        self.assertFalse(
            can_sales(user, SalesCapability.READ, record={'team_id': 20})
        )

    def test_sales_manager_actions_are_limited_to_managed_teams(self):
        user = role_user(ROLE_SALES_MANAGER)
        managed = SimpleNamespace(owner_user_id=18, team_id=20)
        outside = SimpleNamespace(owner_user_id=18, team_id=21)

        for capability in SalesCapability:
            if capability is SalesCapability.POOL_IMPORT:
                self.assertFalse(can_sales(user, capability))
                continue
            with self.subTest(capability=capability, record='managed'):
                self.assertTrue(
                    can_sales(
                        user,
                        capability,
                        record=managed,
                        managed_team_ids={20},
                    )
                )
            with self.subTest(capability=capability, record='outside'):
                self.assertFalse(
                    can_sales(
                        user,
                        capability,
                        record=outside,
                        managed_team_ids={20},
                    )
                )

    @patch('console.capabilities._managed_team_ids_for_user', return_value={20})
    def test_manager_scope_can_resolve_team_membership_for_views(self, managed_ids):
        user = role_user(ROLE_SALES_MANAGER)

        self.assertTrue(
            can_sales(
                user,
                SalesCapability.ASSIGN,
                team_id=20,
            )
        )
        managed_ids.assert_called_once_with(user)

    def test_marketing_ops_has_only_full_site_attribution_read(self):
        user = role_user(ROLE_MARKETING_OPS)
        any_record = SimpleNamespace(assignee_id=999, team_id=999)

        self.assertTrue(
            can_sales(user, SalesCapability.ATTRIBUTION_READ, record=any_record)
        )
        for capability in (
            SalesCapability.READ,
            SalesCapability.WRITE,
            SalesCapability.ASSIGN,
            SalesCapability.CONVERT,
        ):
            with self.subTest(capability=capability):
                self.assertFalse(can_sales(user, capability))
                self.assertFalse(can_sales(user, capability, record=any_record))

    def test_system_admin_and_superuser_have_all_capabilities_for_all_records(self):
        users = (
            role_user(ROLE_SYSTEM_ADMIN),
            role_user(superuser=True),
        )
        record = SimpleNamespace(assignee_id=999, team_id=999)

        for user in users:
            for capability in SalesCapability:
                with self.subTest(user=user, capability=capability):
                    self.assertEqual(
                        sales_capability_scope(user, capability),
                        DataScope.ALL,
                    )
                    self.assertTrue(can_sales(user, capability, record=record))

    def test_content_unknown_and_unauthenticated_users_fail_closed(self):
        content_user = role_user(ROLE_CONTENT_OPS)
        unknown_user = role_user('unregistered_role')
        anonymous = SimpleNamespace(is_authenticated=False, is_superuser=False)

        for user in (content_user, unknown_user, anonymous):
            for capability in SalesCapability:
                with self.subTest(user=user, capability=capability):
                    self.assertFalse(can_sales(user, capability))

        self.assertEqual(
            capability_scope_for_roles({ROLE_SALES}, 'sales.misspelled'),
            DataScope.NONE,
        )

    def test_multiple_roles_merge_to_the_strongest_scope_per_capability(self):
        user = role_user(ROLE_SALES, ROLE_MARKETING_OPS)

        self.assertEqual(
            sales_capability_scope(user, SalesCapability.READ),
            DataScope.OWN,
        )
        self.assertEqual(
            sales_capability_scope(user, SalesCapability.ATTRIBUTION_READ),
            DataScope.ALL,
        )
        self.assertEqual(
            sales_capability_scope(user, SalesCapability.ASSIGN),
            DataScope.NONE,
        )

    def test_require_sales_returns_true_or_raises_permission_denied(self):
        user = role_user(ROLE_SALES, user_id=7)

        self.assertTrue(
            require_sales(
                user,
                SalesCapability.WRITE,
                owner_id=7,
            )
        )
        with self.assertRaises(PermissionDenied):
            require_sales(
                user,
                SalesCapability.WRITE,
                owner_id=8,
            )
        with self.assertRaisesMessage(PermissionDenied, '禁止转换'):
            require_sales(
                user,
                SalesCapability.CONVERT,
                owner_id=8,
                message='禁止转换',
            )
