"""Site- and locale-scoped authorization for content operations.

Page visibility is not authorization.  Content, asset, and release endpoints
must resolve this policy on the server before reading or mutating records.

Query budget
------------
``effective_content_capabilities`` performs no grant query for anonymous,
unknown-role, or system-administrator accounts.  For a content operator it
performs one grant query.  Resolving an uncached console role can add one auth
group query; ``ConsoleAccessMiddleware`` supplies that immutable request cache
during normal requests.  Callers that need several decisions should resolve
the returned ``frozenset`` once and test membership rather than repeatedly
calling the convenience helper.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .access import ROLE_CONTENT_OPS, ROLE_SYSTEM_ADMIN, user_role_keys


CONTENT_READ = 'content.read'
CONTENT_WRITE = 'content.write'
CONTENT_SET_PUBLISHED = 'content.set_published'
CONTENT_LOCALE_MANAGE = 'content.locale.manage'
ASSETS_READ = 'assets.read'
ASSETS_WRITE = 'assets.write'
ASSETS_IMPORT_LOCAL = 'assets.import_local'
RELEASES_READ = 'releases.read'
RELEASES_PREVIEW_BUILD = 'releases.preview_build'

ALL_CONTENT_CAPABILITIES = frozenset({
    CONTENT_READ,
    CONTENT_WRITE,
    CONTENT_SET_PUBLISHED,
    CONTENT_LOCALE_MANAGE,
    ASSETS_READ,
    ASSETS_WRITE,
    ASSETS_IMPORT_LOCAL,
    RELEASES_READ,
    RELEASES_PREVIEW_BUILD,
})

# These capabilities are deliberately named as a separate policy fact.  They
# are still resolved exactly like every other capability: no read/write grant
# implies either high-risk operation.
HIGH_RISK_CONTENT_CAPABILITIES = frozenset({
    ASSETS_IMPORT_LOCAL,
    RELEASES_PREVIEW_BUILD,
})

_NO_CONTENT_CAPABILITIES: frozenset[str] = frozenset()


def _primary_key(value: Any) -> Any | None:
    """Return a model instance or scalar primary key without issuing queries."""

    if value is None or isinstance(value, bool):
        return None
    primary_key = getattr(value, 'pk', value)
    # SiteOS uses BigAutoField for the three identities resolved here (user,
    # site, and locale).  Reject malformed scalar input instead of letting a
    # field coercion error turn an authorization denial into a 500 response.
    if (
        not isinstance(primary_key, int)
        or isinstance(primary_key, bool)
        or primary_key <= 0
    ):
        return None
    return primary_key


def _validated_scope_ids(*, site: Any, locale: Any | None) -> tuple[Any, Any | None] | None:
    """Validate an in-memory ``Site`` / ``SiteLocale`` scope.

    A locale-specific authorization decision must receive a locale model (or
    model-like object) carrying ``site_id``.  Accepting a bare locale ID would
    make it impossible to reject a cross-site pair without an extra database
    query, so it fails closed.  A site can be passed as an instance or scalar
    primary key.
    """

    site_id = _primary_key(site)
    if site_id is None:
        return None
    if locale is None:
        return site_id, None

    locale_id = _primary_key(locale)
    locale_site_id = getattr(locale, 'site_id', None)
    if locale_site_id is None:
        locale_site = getattr(locale, 'site', None)
        locale_site_id = _primary_key(locale_site)
    if locale_id is None or locale_site_id is None or locale_site_id != site_id:
        return None
    return site_id, locale_id


def effective_content_capabilities(
    user,
    *,
    site,
    locale=None,
) -> frozenset[str]:
    """Return the immutable exact capabilities effective in one data scope.

    ``system_admin`` retains every capability for a valid site/locale pair.
    ``content_ops`` receives only enabled direct-user or group grants for the
    requested site.  A locale request accepts either its matching locale grant
    or a site-wide grant whose locale is null.  A site-level request accepts
    only site-wide grants.  Other roles and invalid scopes fail closed.

    Capabilities never imply one another.  In particular, write access does
    not imply publishing, local-file import, or preview-build access.
    """

    scope_ids = _validated_scope_ids(site=site, locale=locale)
    if scope_ids is None or not getattr(user, 'is_authenticated', False):
        return _NO_CONTENT_CAPABILITIES
    user_id = _primary_key(getattr(user, 'pk', None))
    if user_id is None:
        return _NO_CONTENT_CAPABILITIES

    roles = user_role_keys(user)
    if ROLE_SYSTEM_ADMIN in roles:
        return ALL_CONTENT_CAPABILITIES
    if ROLE_CONTENT_OPS not in roles:
        return _NO_CONTENT_CAPABILITIES

    site_id, locale_id = scope_ids

    # Import lazily so policy constants remain usable while schema tooling is
    # loading models, and to avoid any authorization-time model import cycle.
    from .models import ContentAccessGrant

    group_ids = user.groups.values('id')
    grants = ContentAccessGrant.objects.filter(
        enabled=True,
        site_id=site_id,
    ).filter(
        Q(user_id=user_id) | Q(group_id__in=group_ids),
    )
    if locale_id is None:
        grants = grants.filter(locale_id__isnull=True)
    else:
        grants = grants.filter(Q(locale_id__isnull=True) | Q(locale_id=locale_id))

    # ``distinct`` keeps a direct grant plus an equivalent group grant from
    # leaking duplicate mutable state to callers.  The Python whitelist is a
    # second fail-closed layer if a legacy database predates the SQL CHECK.
    resolved = {
        capability
        for capability in grants.values_list('capability', flat=True).distinct()
        if capability in ALL_CONTENT_CAPABILITIES
    }
    return frozenset(resolved)


def has_content_capability(
    user,
    capability: str,
    *,
    site,
    locale=None,
) -> bool:
    """Check one exact content capability in the requested server-side scope."""

    if capability not in ALL_CONTENT_CAPABILITIES:
        return False
    return capability in effective_content_capabilities(
        user,
        site=site,
        locale=locale,
    )


def require_content_capability(
    user,
    capability: str,
    *,
    site,
    locale=None,
) -> bool:
    """Return ``True`` when authorized, otherwise raise ``PermissionDenied``."""

    if has_content_capability(
        user,
        capability,
        site=site,
        locale=locale,
    ):
        return True
    raise PermissionDenied('当前账号没有在此站点执行该内容操作的权限。')
