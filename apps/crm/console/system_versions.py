from __future__ import annotations

import hashlib
import json

from django.core import signing

from leads.models import SalesTeamMember

from .access import user_role_keys


USER_VERSION_SALT = 'console.system-user-version.v1'
TEAM_VERSION_SALT = 'console.sales-team-version.v1'


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _token(payload: dict[str, object], *, salt: str) -> str:
    return signing.dumps({'digest': _digest(payload)}, salt=salt, compress=True)


def _matches(token: str, payload: dict[str, object], *, salt: str) -> bool:
    try:
        decoded = signing.loads(str(token or ''), salt=salt)
    except signing.BadSignature:
        return False
    return isinstance(decoded, dict) and decoded.get('digest') == _digest(payload)


def system_user_version_payload(user) -> dict[str, object]:
    memberships = list(
        SalesTeamMember.objects.filter(user=user)
        .order_by('team_id', 'id')
        .values_list('team_id', 'membership_role')
    )
    return {
        'id': user.pk,
        'username': user.get_username(),
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'is_active': bool(user.is_active),
        'is_superuser': bool(user.is_superuser),
        'password_digest': hashlib.sha256(str(user.password).encode('utf-8')).hexdigest(),
        'roles': sorted(user_role_keys(user)),
        'memberships': memberships,
    }


def system_user_version_token(user) -> str:
    return _token(system_user_version_payload(user), salt=USER_VERSION_SALT)


def system_user_version_matches(token: str, user) -> bool:
    return _matches(token, system_user_version_payload(user), salt=USER_VERSION_SALT)


def sales_team_version_payload(team) -> dict[str, object]:
    return {
        'id': team.pk,
        'site_id': team.site_id,
        'code': team.code,
        'name': team.name,
        'enabled': bool(team.enabled),
    }


def sales_team_version_token(team) -> str:
    return _token(sales_team_version_payload(team), salt=TEAM_VERSION_SALT)


def sales_team_version_matches(token: str, team) -> bool:
    return _matches(token, sales_team_version_payload(team), salt=TEAM_VERSION_SALT)
