"""Capability and data-scope policy for sales operations.

The legacy access layer answers whether a role may enter a page.  This module
answers the narrower question a write endpoint must ask: which sales action is
allowed, and over which records?  Keep authorization checks on the server even
when the matching control is hidden in the UI.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from django.core.exceptions import PermissionDenied

from leads.models import SalesTeamMember

from .access import (
    ROLE_CONTENT_OPS,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    user_role_keys,
)


class SalesCapability(StrEnum):
    READ = 'sales.read'
    WRITE = 'sales.write'
    ASSIGN = 'sales.assign'
    CONVERT = 'sales.convert'
    ATTRIBUTION_READ = 'sales.attribution.read'
    POOL_READ = 'sales.pool.read'
    POOL_CREATE = 'sales.pool.create'
    POOL_CLAIM = 'sales.pool.claim'
    POOL_RELEASE = 'sales.pool.release'
    POOL_IMPORT = 'sales.pool.import'
    POOL_EXPORT = 'sales.pool.export'
    POOL_REVIEW = 'sales.pool.review'


class DataScope(StrEnum):
    NONE = 'none'
    OWN = 'own'
    TEAM = 'team'
    ALL = 'all'


_SYSTEM_ADMIN_CAPABILITIES = {
    capability: DataScope.ALL for capability in SalesCapability
}

# Public, immutable policy table: role -> capability -> data scope.
ROLE_CAPABILITY_SCOPES: Mapping[str, Mapping[SalesCapability, DataScope]] = MappingProxyType({
    ROLE_SALES: MappingProxyType({
        SalesCapability.READ: DataScope.OWN,
        SalesCapability.WRITE: DataScope.OWN,
        SalesCapability.CONVERT: DataScope.OWN,
        SalesCapability.ATTRIBUTION_READ: DataScope.OWN,
        SalesCapability.POOL_READ: DataScope.OWN,
        SalesCapability.POOL_CREATE: DataScope.OWN,
        SalesCapability.POOL_CLAIM: DataScope.OWN,
        SalesCapability.POOL_RELEASE: DataScope.OWN,
        SalesCapability.POOL_EXPORT: DataScope.OWN,
    }),
    ROLE_SALES_MANAGER: MappingProxyType({
        SalesCapability.READ: DataScope.TEAM,
        SalesCapability.WRITE: DataScope.TEAM,
        SalesCapability.ASSIGN: DataScope.TEAM,
        SalesCapability.CONVERT: DataScope.TEAM,
        SalesCapability.ATTRIBUTION_READ: DataScope.TEAM,
        SalesCapability.POOL_READ: DataScope.TEAM,
        SalesCapability.POOL_CREATE: DataScope.TEAM,
        SalesCapability.POOL_CLAIM: DataScope.TEAM,
        SalesCapability.POOL_RELEASE: DataScope.TEAM,
        SalesCapability.POOL_EXPORT: DataScope.TEAM,
        SalesCapability.POOL_REVIEW: DataScope.TEAM,
    }),
    ROLE_MARKETING_OPS: MappingProxyType({
        SalesCapability.ATTRIBUTION_READ: DataScope.ALL,
    }),
    ROLE_CONTENT_OPS: MappingProxyType({}),
    ROLE_SYSTEM_ADMIN: MappingProxyType(_SYSTEM_ADMIN_CAPABILITIES),
})

_SCOPE_RANK = {
    DataScope.NONE: 0,
    DataScope.OWN: 1,
    DataScope.TEAM: 2,
    DataScope.ALL: 3,
}
_UNSET = object()


def _capability(value: SalesCapability | str) -> SalesCapability | None:
    try:
        return SalesCapability(value)
    except (TypeError, ValueError):
        return None


def capability_scope_for_roles(
    roles: Iterable[str],
    capability: SalesCapability | str,
) -> DataScope:
    """Return the strongest scope granted by a user's complete role set.

    Unknown roles and capability names fail closed.  Supporting multiple roles
    matters during migrations, when an account can briefly belong to more than
    one console group.
    """

    capability_key = _capability(capability)
    if capability_key is None:
        return DataScope.NONE

    result = DataScope.NONE
    for role in roles:
        scope = ROLE_CAPABILITY_SCOPES.get(role, {}).get(
            capability_key,
            DataScope.NONE,
        )
        if _SCOPE_RANK[scope] > _SCOPE_RANK[result]:
            result = scope
    return result


def sales_capability_scope(user, capability: SalesCapability | str) -> DataScope:
    """Resolve a user's data scope for one sales capability."""

    return capability_scope_for_roles(user_role_keys(user), capability)


def _value(record: Any, field_name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(field_name, _UNSET)
    return getattr(record, field_name, _UNSET)


def _record_owner_id(record: Any) -> Any:
    for field_name in ('assignee_id', 'owner_user_id', 'owner_id'):
        value = _value(record, field_name)
        if value is not _UNSET:
            return value
    return _UNSET


def _record_team_id(record: Any) -> Any:
    return _value(record, 'team_id')


def _managed_team_ids_for_user(user) -> set[Any]:
    return set(
        SalesTeamMember.objects.filter(
            user=user,
            membership_role='manager',
            team__enabled=True,
        ).values_list('team_id', flat=True)
    )


def can_sales(
    user,
    capability: SalesCapability | str,
    *,
    record: Any | None = None,
    owner_id: Any = _UNSET,
    team_id: Any = _UNSET,
    managed_team_ids: Iterable[Any] | None = None,
) -> bool:
    """Return whether ``user`` may perform ``capability`` in this data scope.

    With no record or explicit IDs this is a capability-level check suitable for
    deciding whether to render an action.  Passing a record (or IDs) makes it a
    record-level authorization check.  ``LeadSubmission.assignee_id`` and CRM
    ``owner_user_id`` records are both supported.

    ``managed_team_ids`` may be supplied by callers that already loaded team
    scope.  Otherwise manager team IDs are resolved from ``SalesTeamMember``.
    """

    scope = sales_capability_scope(user, capability)
    if scope is DataScope.NONE:
        return False

    scope_target_supplied = (
        record is not None or owner_id is not _UNSET or team_id is not _UNSET
    )
    if not scope_target_supplied:
        return True
    if scope is DataScope.ALL:
        return True

    if record is not None:
        if owner_id is _UNSET:
            owner_id = _record_owner_id(record)
        if team_id is _UNSET:
            team_id = _record_team_id(record)

    if scope is DataScope.OWN:
        user_id = getattr(user, 'pk', getattr(user, 'id', None))
        return owner_id is not _UNSET and owner_id is not None and owner_id == user_id

    if scope is DataScope.TEAM:
        if team_id is _UNSET or team_id is None:
            return False
        allowed_team_ids = (
            set(managed_team_ids)
            if managed_team_ids is not None
            else _managed_team_ids_for_user(user)
        )
        return team_id in allowed_team_ids

    return False


def require_sales(
    user,
    capability: SalesCapability | str,
    *,
    record: Any | None = None,
    owner_id: Any = _UNSET,
    team_id: Any = _UNSET,
    managed_team_ids: Iterable[Any] | None = None,
    message: str | None = None,
) -> bool:
    """Return ``True`` when authorized, otherwise raise ``PermissionDenied``."""

    if can_sales(
        user,
        capability,
        record=record,
        owner_id=owner_id,
        team_id=team_id,
        managed_team_ids=managed_team_ids,
    ):
        return True
    raise PermissionDenied(message or '当前账号没有执行该销售操作或访问该数据范围的权限。')
