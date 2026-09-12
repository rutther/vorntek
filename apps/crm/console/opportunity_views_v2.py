from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from .access import primary_role_label
from .capabilities import SalesCapability, require_sales
from .navigation import build_navigation
from .opportunity_workspace_v2 import (
    build_opportunity_detail_workspace_v2,
    build_opportunity_list_workspace_v2,
)
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale


def _page_context(request, *, site, locale, workspace) -> dict[str, object]:
    return {
        'workspace': workspace,
        'nav_items': build_navigation('opportunities', request.user),
        'console_role_label': primary_role_label(request.user),
        'preview_site_url': f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
        'create_actions': [],
    }


def _canonical_redirect_required(request, canonical_url: str) -> bool:
    # Comparing the path plus raw query is intentional: it strips unknown and
    # repeated keys, restores defaults, and gives copied URLs one stable order.
    return request.get_full_path() != canonical_url


@login_required
@require_GET
def opportunity_workspace_v2(request):
    require_sales(request.user, SalesCapability.READ)
    # Opportunities are site-scoped. Resolve the enabled default locale only
    # for the public-preview action; locale is not opportunity filter state.
    site, locale = default_site_locale(None)
    workspace = build_opportunity_list_workspace_v2(
        request=request,
        site=site,
    )
    if _canonical_redirect_required(request, workspace['canonical_url']):
        return redirect(workspace['canonical_url'])
    return render(
        request,
        'console/v2/pages/opportunities.html',
        _page_context(request, site=site, locale=locale, workspace=workspace),
    )


@login_required
@require_GET
def opportunity_workspace_detail_v2(request, opportunity_id: int):
    require_sales(request.user, SalesCapability.READ)
    site, locale = default_site_locale(None)
    workspace = build_opportunity_detail_workspace_v2(
        request=request,
        site=site,
        opportunity_id=opportunity_id,
    )
    if workspace.get('missing'):
        raise Http404('当前数据范围中没有这条销售机会。')
    if _canonical_redirect_required(request, workspace['canonical_url']):
        return redirect(workspace['canonical_url'])
    return render(
        request,
        'console/v2/pages/opportunity_detail.html',
        _page_context(request, site=site, locale=locale, workspace=workspace),
    )
