from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from .access import primary_role_label
from .access import crm_owned_queryset_for_user
from .account_workspace_v2 import (
    build_account_error_workspace,
    build_company_detail_workspace_v2,
    build_company_list_workspace_v2,
    build_contact_detail_workspace_v2,
    build_contact_list_workspace_v2,
)
from .capabilities import SalesCapability, require_sales
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale
from leads.models import Company, Contact


def _page_context(request, *, locale, workspace, active_key: str) -> dict[str, object]:
    return {
        'workspace': workspace,
        'nav_items': build_navigation(active_key, request.user),
        'console_role_label': primary_role_label(request.user),
        'preview_site_url': f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
        'create_actions': [],
    }


def _canonical_redirect_required(request, canonical_url: str) -> bool:
    return request.get_full_path() != canonical_url


def _render_list(request, *, kind: str):
    require_sales(request.user, SalesCapability.READ)
    site, locale = default_site_locale(None)
    builder = (
        build_company_list_workspace_v2
        if kind == 'companies'
        else build_contact_list_workspace_v2
    )
    active_key = kind
    try:
        workspace = builder(request=request, site=site)
    except DatabaseError:
        workspace = build_account_error_workspace(kind=kind)
        return render(
            request,
            'console/v2/pages/accounts.html',
            _page_context(request, locale=locale, workspace=workspace, active_key=active_key),
            status=503,
        )
    if _canonical_redirect_required(request, workspace['canonical_url']):
        return redirect(workspace['canonical_url'])
    return render(
        request,
        'console/v2/pages/accounts.html',
        _page_context(request, locale=locale, workspace=workspace, active_key=active_key),
    )


def _render_detail(request, *, kind: str, record_id: int):
    require_sales(request.user, SalesCapability.READ)
    site, locale = default_site_locale(None)
    if kind == 'company':
        builder = build_company_detail_workspace_v2
        builder_kwargs = {'company_id': record_id}
        active_key = 'companies'
    else:
        builder = build_contact_detail_workspace_v2
        builder_kwargs = {'contact_id': record_id}
        active_key = 'contacts'
    try:
        workspace = builder(request=request, site=site, **builder_kwargs)
    except DatabaseError:
        workspace = build_account_error_workspace(kind=kind, detail=True)
        return render(
            request,
            'console/v2/pages/account_detail.html',
            _page_context(request, locale=locale, workspace=workspace, active_key=active_key),
            status=503,
        )
    if workspace.get('missing'):
        raise Http404(f'当前数据范围中没有这条{workspace.get("title", "客户记录")}。')
    if _canonical_redirect_required(request, workspace['canonical_url']):
        return redirect(workspace['canonical_url'])
    return render(
        request,
        'console/v2/pages/account_detail.html',
        _page_context(request, locale=locale, workspace=workspace, active_key=active_key),
    )


@login_required
@require_GET
def company_workspace_v2(request):
    return _render_list(request, kind='companies')


@login_required
@require_GET
def company_workspace_detail_v2(request, company_id: int):
    return _render_detail(request, kind='company', record_id=company_id)


@login_required
@require_GET
def contact_workspace_v2(request):
    return _render_list(request, kind='contacts')


@login_required
@require_GET
def contact_workspace_detail_v2(request, contact_id: int):
    return _render_detail(request, kind='contact', record_id=contact_id)


@login_required
@require_GET
def contact_company_options_v2(request, contact_id: int):
    """Bounded, scoped company lookup for the contact edit dialog."""

    require_sales(request.user, SalesCapability.READ)
    site, _locale = default_site_locale(None)
    contact = crm_owned_queryset_for_user(
        Contact.objects.filter(site=site), request.user
    ).filter(pk=contact_id).first()
    if contact is None:
        raise Http404('当前数据范围中没有这条联系人。')
    query = ' '.join(str(request.GET.get('q') or '').split())[:120]
    if len(query) < 2:
        return JsonResponse({
            'ok': True,
            'items': [],
            'has_more': False,
            'message': '请输入至少 2 个字符搜索同团队企业。',
        })
    queryset = crm_owned_queryset_for_user(
        Company.objects.filter(site=site, team_id=contact.team_id), request.user
    ).filter(
        Q(name__icontains=query)
        | Q(country__icontains=query)
        | Q(city__icontains=query)
    ).order_by('name', 'id')
    rows = list(queryset.values('id', 'name', 'country', 'city')[:21])
    return JsonResponse({
        'ok': True,
        'items': [
            {
                'id': row['id'],
                'name': row['name'],
                'location': ' / '.join(
                    value for value in (row['country'], row['city']) if value
                ),
            }
            for row in rows[:20]
        ],
        'has_more': len(rows) > 20,
        'message': '结果超过 20 条，请继续缩小搜索范围。' if len(rows) > 20 else '',
    })
