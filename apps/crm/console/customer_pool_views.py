from __future__ import annotations

import uuid
from io import BytesIO

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.utils.http import url_has_allowed_host_and_scheme
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from leads.customer_pool_services import (
    archive_reviewed_customer,
    assign_customer,
    claim_customer,
    claim_customers_bulk,
    create_manual_customer,
    publish_reviewed_customer,
    public_pool_queryset,
    release_customer,
    restore_archived_customer,
)
from leads.customer_import_services import (
    MAX_IMPORT_BYTES,
    MAX_RESEARCH_IMPORT_BYTES,
    RESEARCH_SNAPSHOT_PROFILES,
    commit_customer_import,
    preview_customer_import,
    preview_research_snapshot_import,
)
from leads.models import (
    Company,
    CompanyPoolState,
    CustomerExportJob,
    CustomerImportBatch,
    SalesTeam,
    SalesTeamMember,
)

from .access import crm_owned_queryset_for_user, primary_role_label
from .capabilities import DataScope, SalesCapability, can_sales, require_sales, sales_capability_scope
from .customer_pool_exports import EXPORT_FIELD_LABELS
from .customer_pool_search import resolve_sensitive_search, store_sensitive_search
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale


SOURCE_LABELS = {
    'manual': '手动录入',
    'research': '研究开发',
    'website_form': '网站表单',
    'meta_native': 'Meta 原生表单',
    'other': '其他来源',
}
INTAKE_LABELS = {
    'manual_create': '手动创建',
    'file_import': '文件导入',
    'automatic_receive': '自动接收',
}
PAGE_SIZES = (10, 25, 50)


def _context(request, *, workspace, locale, active_key='customer_pool'):
    return {
        'workspace': workspace,
        'nav_items': build_navigation(active_key, request.user),
        'console_role_label': primary_role_label(request.user),
        'preview_site_url': f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
        'create_actions': [],
    }


def _error_text(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return '；'.join(error.messages)
    return str(error)


def _safe_next(request) -> str:
    candidate = str(request.POST.get('next') or '')
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse('console:customer_pool')


def _safe_website(value: str) -> str:
    website = str(value or '').strip()
    return website if website.lower().startswith(('https://', 'http://')) else ''


def _single_active_team_for_actor(actor):
    memberships = list(
        SalesTeamMember.objects.select_related('team')
        .filter(user=actor, team__enabled=True)
        .order_by('team_id')[:2]
    )
    return memberships[0].team if len(memberships) == 1 else None


def _pool_queryset(*, request, site, scope: str):
    if scope == 'mine':
        return (
            Company.objects.filter(site=site, pool_state__state='owned', owner_user=request.user)
            .select_related('pool_state', 'owner_user', 'team')
            .prefetch_related('customer_sources', 'contact_points')
        )
    if scope == 'team':
        return (
            crm_owned_queryset_for_user(
                Company.objects.filter(site=site, pool_state__state='owned'), request.user
            )
            .select_related('pool_state', 'owner_user', 'team')
            .prefetch_related('customer_sources', 'contact_points')
        )
    if scope in {'review', 'archived'}:
        if not can_sales(request.user, SalesCapability.POOL_REVIEW):
            return Company.objects.none()
        return (
            crm_owned_queryset_for_user(
                Company.objects.filter(site=site, pool_state__state=scope), request.user
            )
            .select_related('pool_state', 'owner_user', 'team')
            .prefetch_related('customer_sources', 'contact_points')
        )
    return public_pool_queryset(site=site, actor=request.user).select_related('owner_user', 'team')


def _review_teams_for_actor(*, actor, site):
    teams = SalesTeam.objects.filter(site=site, enabled=True)
    if sales_capability_scope(actor, SalesCapability.POOL_REVIEW) is DataScope.ALL:
        return teams.order_by('name', 'id')
    return teams.filter(
        memberships__user=actor,
        memberships__membership_role='manager',
    ).distinct().order_by('name', 'id')


def _assignment_candidates_for_company(*, actor, company):
    if (
        company.team_id is None
        or company.pool_state.state not in {'available', 'owned'}
        or not can_sales(actor, SalesCapability.ASSIGN, record=company)
    ):
        return get_user_model().objects.none()
    return (
        get_user_model().objects.filter(
            is_active=True,
            sales_team_memberships__team_id=company.team_id,
            sales_team_memberships__team__enabled=True,
        )
        .exclude(pk=company.owner_user_id)
        .distinct()
        .order_by('username', 'id')
    )


def _query_url(request, **updates) -> str:
    params = request.GET.copy()
    for key, value in updates.items():
        if value in ('', None, 'all'):
            params.pop(key, None)
        else:
            params[key] = value
    query = params.urlencode()
    return f'{reverse("console:customer_pool")}{"?" + query if query else ""}'


@login_required
@require_GET
def customer_pool(request):
    require_sales(request.user, SalesCapability.POOL_READ)
    site, locale = default_site_locale(None)
    create_draft = request.session.pop('customer_pool_create_draft', {})
    bulk_claim_result = request.session.pop('customer_pool_bulk_claim_result', None)
    scope = str(request.GET.get('scope') or 'available')
    can_review = can_sales(request.user, SalesCapability.POOL_REVIEW)
    if scope not in {'available', 'mine', 'team', 'review', 'archived'}:
        scope = 'available'
    if scope in {'review', 'archived'} and not can_review:
        scope = 'available'
    query = ' '.join(str(request.GET.get('q') or '').split())[:120]
    contact_search_token = str(request.GET.get('contact_search') or '')[:80]
    contact_search = resolve_sensitive_search(request, contact_search_token)
    source_type = str(request.GET.get('source') or 'all')
    intake_method = str(request.GET.get('intake') or 'all')
    country = ' '.join(str(request.GET.get('country') or '').split())[:120]
    evidence_status = str(request.GET.get('evidence') or 'all')
    owner_id = int(request.GET.get('owner')) if str(request.GET.get('owner') or '').isdigit() else 0
    team_id = int(request.GET.get('team')) if str(request.GET.get('team') or '').isdigit() else 0
    batch_id = int(request.GET.get('batch')) if str(request.GET.get('batch') or '').isdigit() else 0
    try:
        claim_team = _single_active_team_for_actor(request.user)
        can_export_capability = can_sales(request.user, SalesCapability.POOL_EXPORT)
        queryset = _pool_queryset(request=request, site=site, scope=scope)
        owner_options = list(
            queryset.exclude(owner_user__isnull=True)
            .values('owner_user_id', 'owner_user__username')
            .distinct()
            .order_by('owner_user__username', 'owner_user_id')
        )
        team_options = list(
            queryset.exclude(team__isnull=True)
            .values('team_id', 'team__name')
            .distinct()
            .order_by('team__name', 'team_id')
        )
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(website__icontains=query)
                | Q(industry__icontains=query)
                | Q(customer_sources__source_detail__icontains=query)
            )
        if contact_search:
            queryset = queryset.filter(contact_points__raw_value__icontains=contact_search)
        if source_type in SOURCE_LABELS:
            queryset = queryset.filter(customer_sources__source_type=source_type)
        if intake_method in INTAKE_LABELS:
            queryset = queryset.filter(customer_sources__intake_method=intake_method)
        if country:
            queryset = queryset.filter(country__icontains=country)
        if evidence_status in {'pending', 'declared', 'verified', 'rejected'}:
            queryset = queryset.filter(pool_state__evidence_status=evidence_status)
        if owner_id:
            queryset = queryset.filter(owner_user_id=owner_id)
        if team_id:
            queryset = queryset.filter(team_id=team_id)
        if batch_id:
            queryset = queryset.filter(customer_sources__evidence_json__batch_id=batch_id)
        queryset = queryset.distinct().order_by('-pool_state__published_at', '-id')
        try:
            page_size = int(request.GET.get('page_size') or 25)
        except (TypeError, ValueError):
            page_size = 25
        if page_size not in PAGE_SIZES:
            page_size = 25
        paginator = Paginator(queryset, page_size)
        page = paginator.get_page(request.GET.get('page'))
        rows = []
        for company in page.object_list:
            sources = list(company.customer_sources.all())
            contact_points = list(company.contact_points.all())
            first_source = sources[0] if sources else None
            rows.append({
                'company': company,
                'state': company.pool_state,
                'source_count': len(sources),
                'source_label': SOURCE_LABELS.get(getattr(first_source, 'source_type', ''), '来源待补充'),
                'intake_label': INTAKE_LABELS.get(getattr(first_source, 'intake_method', ''), '进入方式待补充'),
                'source_detail': getattr(first_source, 'source_detail', '') or '—',
                'contact_point': contact_points[0] if contact_points else None,
                'detail_url': reverse('console:customer_pool_detail', args=[company.pk]),
                'claim_url': reverse('console:customer_pool_claim', args=[company.pk]),
                'release_url': reverse('console:customer_pool_release', args=[company.pk]),
                'claim_token': f'claim-{uuid.uuid4().hex}',
            })
        export_jobs = []
        has_active_exports = False
        export_status_snapshot = ''
        if can_export_capability:
            now = timezone.now()
            for job in CustomerExportJob.objects.filter(
                site=site,
                created_by=request.user,
            ).order_by('-created_at', '-id')[:10]:
                effective_status = (
                    'expired'
                    if job.status == 'ready' and job.expires_at and job.expires_at <= now
                    else job.status
                )
                has_active_exports = has_active_exports or effective_status in {'pending', 'processing'}
                export_jobs.append({
                    'record': job,
                    'status': effective_status,
                    'field_labels': '、'.join(
                        EXPORT_FIELD_LABELS.get(field, field)
                        for field in (job.fields_json or [])
                    ),
                    'format_label': str(job.format or '').upper(),
                    'download_url': reverse(
                        'console:customer_pool_export_download', args=[job.pk]
                    ),
                    'retry_url': reverse(
                        'console:customer_pool_export_retry', args=[job.pk]
                    ),
                    'cancel_url': reverse(
                        'console:customer_pool_export_cancel', args=[job.pk]
                    ),
                })
            export_status_snapshot = '|'.join(
                f'{item["record"].pk}:{item["status"]}:{item["record"].updated_at.isoformat()}'
                for item in export_jobs
            )
        workspace = {
            'error': False,
            'rows': rows,
            'page': page,
            'previous_url': _query_url(request, page=page.previous_page_number()) if page.has_previous() else '',
            'next_url': _query_url(request, page=page.next_page_number()) if page.has_next() else '',
            'scope': scope,
            'query': query,
            'contact_search_token': contact_search_token if contact_search else '',
            'contact_search_active': bool(contact_search),
            'clear_contact_search_url': _query_url(request, contact_search=None, page=None),
            'contact_search_url': reverse('console:customer_pool_contact_search'),
            'page_size': page_size,
            'page_size_options': PAGE_SIZES,
            'source_type': source_type,
            'intake_method': intake_method,
            'country': country,
            'evidence_status': evidence_status,
            'evidence_options': (
                ('pending', '待核验'),
                ('declared', '已声明'),
                ('verified', '已核验'),
                ('rejected', '已拒绝'),
            ),
            'owner_id': owner_id,
            'owner_options': owner_options,
            'team_id': team_id,
            'team_options': team_options,
            'batch_id': batch_id,
            'source_options': SOURCE_LABELS.items(),
            'intake_options': INTAKE_LABELS.items(),
            'clear_url': reverse('console:customer_pool'),
            'available_url': _query_url(request, scope='available', page=None),
            'mine_url': _query_url(request, scope='mine', page=None),
            'team_url': _query_url(request, scope='team', page=None),
            'review_url': _query_url(request, scope='review', page=None),
            'archived_url': _query_url(request, scope='archived', page=None),
            'can_create': can_sales(request.user, SalesCapability.POOL_CREATE),
            'can_claim': claim_team is not None and can_sales(request.user, SalesCapability.POOL_CLAIM),
            'can_bulk_claim': scope == 'available' and claim_team is not None and can_sales(
                request.user, SalesCapability.POOL_CLAIM
            ),
            'can_assign_self': claim_team is not None,
            'can_review': can_review,
            'can_import': can_sales(request.user, SalesCapability.POOL_IMPORT),
            'can_export': scope != 'available' and can_export_capability,
            'can_view_export_history': can_export_capability,
            'export_jobs': export_jobs,
            'has_active_exports': has_active_exports,
            'export_status_snapshot': export_status_snapshot,
            'export_status_url': reverse('console:customer_pool_export_status'),
            'template_url': reverse('console:customer_pool_template'),
            'import_url': reverse('console:customer_pool_import'),
            'export_url': reverse('console:customer_pool_export'),
            'export_standard21_url': reverse('console:customer_pool_export_standard21'),
            'bulk_claim_url': reverse('console:customer_pool_bulk_claim'),
            'bulk_claim_token': f'bulk-claim-{uuid.uuid4().hex}',
            'bulk_claim_result': bulk_claim_result,
            'manual_create_url': reverse('console:customer_pool_manual_create'),
            'manual_create_token': f'manual-create-{uuid.uuid4().hex}',
            'create_draft': create_draft,
            'reopen_create_modal': bool(create_draft),
        }
    except DatabaseError:
        workspace = {'error': True, 'clear_url': reverse('console:customer_pool')}
        return render(request, 'console/v2/pages/customer_pool.html', _context(request, workspace=workspace, locale=locale), status=503)
    return render(request, 'console/v2/pages/customer_pool.html', _context(request, workspace=workspace, locale=locale))


@login_required
@require_GET
def customer_pool_detail(request, company_id: int):
    require_sales(request.user, SalesCapability.POOL_READ)
    site, locale = default_site_locale(None)
    available = public_pool_queryset(site=site, actor=request.user)
    owned = crm_owned_queryset_for_user(Company.objects.filter(site=site), request.user)
    reviewable = crm_owned_queryset_for_user(
        Company.objects.filter(site=site, pool_state__state__in={'review', 'archived'}),
        request.user,
    ) if can_sales(request.user, SalesCapability.POOL_REVIEW) else Company.objects.none()
    company = (
        Company.objects.filter(
            Q(pk__in=available.values('pk'))
            | Q(pk__in=owned.values('pk'))
            | Q(pk__in=reviewable.values('pk')),
            pk=company_id,
        )
        .select_related('pool_state', 'owner_user', 'team')
        .prefetch_related('customer_sources', 'contact_points', 'contacts')
        .first()
    )
    if company is None:
        raise Http404('当前数据范围中没有这条客户记录。')
    claim_team = _single_active_team_for_actor(request.user)
    can_review_record = can_sales(request.user, SalesCapability.POOL_REVIEW, record=company)
    assignment_candidates = list(
        _assignment_candidates_for_company(actor=request.user, company=company)
    )
    can_assign_record = bool(assignment_candidates)
    workspace = {
        'company': company,
        'state': company.pool_state,
        'sources': [
            {
                'record': source,
                'source_label': SOURCE_LABELS.get(source.source_type, source.source_type),
                'intake_label': INTAKE_LABELS.get(source.intake_method, source.intake_method),
            }
            for source in company.customer_sources.all()
        ],
        'contact_points': company.contact_points.all(),
        'claim_url': reverse('console:customer_pool_claim', args=[company.pk]),
        'release_url': reverse('console:customer_pool_release', args=[company.pk]),
        'claim_token': f'claim-{uuid.uuid4().hex}',
        'can_claim': claim_team is not None and can_sales(request.user, SalesCapability.POOL_CLAIM),
        'can_assign': can_assign_record,
        'assignment_candidates': assignment_candidates,
        'assignment_url': reverse('console:customer_pool_assign', args=[company.pk]),
        'assignment_token': f'assign-{uuid.uuid4().hex}',
        'can_release': can_sales(request.user, SalesCapability.POOL_RELEASE, record=company),
        'can_review': can_review_record,
        'review_teams': _review_teams_for_actor(actor=request.user, site=site) if can_review_record else [],
        'review_url': reverse('console:customer_pool_review', args=[company.pk]),
        'review_token': f'review-{uuid.uuid4().hex}',
        'archive_url': reverse('console:customer_pool_archive', args=[company.pk]),
        'archive_token': f'archive-{uuid.uuid4().hex}',
        'restore_url': reverse('console:customer_pool_restore', args=[company.pk]),
        'restore_token': f'restore-{uuid.uuid4().hex}',
        'release_token': f'release-{uuid.uuid4().hex}',
        'website_url': _safe_website(company.website),
        'company_workspace_url': (
            reverse('console:company_workspace_detail_v2', args=[company.pk])
            if company.pool_state.state == 'owned'
            and can_sales(request.user, SalesCapability.READ, record=company)
            else ''
        ),
        'back_url': reverse('console:customer_pool'),
    }
    return render(request, 'console/v2/pages/customer_pool_detail.html', _context(request, workspace=workspace, locale=locale))


@login_required
@require_POST
def customer_pool_manual_create(request):
    site, _locale = default_site_locale(None)
    try:
        result = create_manual_customer(
            site=site,
            actor=request.user,
            idempotency_token=str(request.POST.get('idempotency_token') or ''),
            company_name=str(request.POST.get('company_name') or ''),
            country=str(request.POST.get('country') or ''),
            city=str(request.POST.get('city') or ''),
            industry=str(request.POST.get('industry') or ''),
            website=str(request.POST.get('website') or ''),
            contact_name=str(request.POST.get('contact_name') or ''),
            email=str(request.POST.get('email') or ''),
            phone=str(request.POST.get('phone') or ''),
            notes=str(request.POST.get('notes') or ''),
            source_type=str(request.POST.get('source_type') or 'manual'),
            source_detail=str(request.POST.get('source_detail') or 'CRM 手动创建'),
            assignment_mode=str(request.POST.get('assignment_mode') or 'review'),
        )
    except (ValidationError, PermissionDenied) as error:
        draft_limits = {
            'company_name': 300,
            'country': 120,
            'city': 120,
            'industry': 160,
            'website': 500,
            'contact_name': 200,
            'email': 320,
            'phone': 120,
            'source_type': 32,
            'source_detail': 1000,
            'notes': 4000,
            'assignment_mode': 24,
        }
        request.session['customer_pool_create_draft'] = {
            key: str(request.POST.get(key) or '')[:maximum]
            for key, maximum in draft_limits.items()
        }
        messages.error(request, _error_text(error))
        return redirect('console:customer_pool')
    messages.success(request, f'已创建客户“{result.company.name}”。')
    return redirect('console:customer_pool_detail', company_id=result.company.pk)


@login_required
@require_POST
def customer_pool_contact_search(request):
    require_sales(request.user, SalesCapability.POOL_READ)
    query = ' '.join(str(request.POST.get('contact_query') or '').split())[:120]
    next_url = _safe_next(request)
    if not query:
        messages.error(request, '请输入要查找的电话或邮箱。')
        return redirect(next_url)
    token = store_sensitive_search(request, query)
    separator = '&' if '?' in next_url else '?'
    return redirect(f'{next_url}{separator}contact_search={token}')


@login_required
@require_POST
def customer_pool_claim(request, company_id: int):
    site, _locale = default_site_locale(None)
    try:
        result = claim_customer(
            company_id=company_id,
            site=site,
            actor=request.user,
            expected_version=int(request.POST.get('expected_version') or 0),
            idempotency_token=str(request.POST.get('idempotency_token') or ''),
        )
    except (ValueError, ValidationError, PermissionDenied, Company.DoesNotExist, CompanyPoolState.DoesNotExist) as error:
        messages.error(request, _error_text(error))
    else:
        messages.success(request, f'已领取“{result.company.name}”。')
    return redirect(_safe_next(request))


@login_required
@require_POST
def customer_pool_bulk_claim(request):
    site, _locale = default_site_locale(None)
    try:
        selections = []
        for raw in request.POST.getlist('items'):
            company_id, expected_version = str(raw).split(':', 1)
            selections.append((int(company_id), int(expected_version)))
        result = claim_customers_bulk(
            site=site,
            actor=request.user,
            selections=selections,
            idempotency_token=str(request.POST.get('idempotency_token') or ''),
        )
    except (ValueError, ValidationError, PermissionDenied) as error:
        messages.error(request, _error_text(error))
    else:
        request.session['customer_pool_bulk_claim_result'] = {
            'succeeded': result.succeeded,
            'failed': result.failed,
            'items': [
                {
                    'position': index,
                    'company_id': item.company_id,
                    'status': item.status,
                    'message': item.message,
                    'workflow_code': item.workflow_code,
                }
                for index, item in enumerate(result.items, start=1)
            ],
        }
        if result.failed:
            messages.warning(
                request,
                f'批量领取完成：{result.succeeded} 家成功，{result.failed} 家未领取。',
            )
        else:
            messages.success(request, f'已领取 {result.succeeded} 家客户。')
    return redirect(_safe_next(request))


@login_required
@require_POST
def customer_pool_assign(request, company_id: int):
    site, _locale = default_site_locale(None)
    target_user = get_user_model().objects.filter(
        pk=int(request.POST.get('target_user_id') or 0)
    ).first() if str(request.POST.get('target_user_id') or '').isdigit() else None
    try:
        result = assign_customer(
            company_id=company_id,
            site=site,
            actor=request.user,
            target_user=target_user,
            expected_version=int(request.POST.get('expected_version') or 0),
            idempotency_token=str(request.POST.get('idempotency_token') or ''),
            reason=str(request.POST.get('reason') or ''),
        )
    except (
        ValueError,
        ValidationError,
        PermissionDenied,
        Company.DoesNotExist,
        CompanyPoolState.DoesNotExist,
    ) as error:
        messages.error(request, _error_text(error))
    else:
        action = '分配' if result.replayed is False else '确认'
        messages.success(
            request,
            f'已{action}“{result.company.name}”给 {result.company.owner_user.get_username()}。',
        )
    return redirect('console:customer_pool_detail', company_id=company_id)


@login_required
@require_POST
def customer_pool_review(request, company_id: int):
    site, _locale = default_site_locale(None)
    try:
        team = SalesTeam.objects.get(
            pk=int(request.POST.get('team_id') or 0),
            site=site,
            enabled=True,
        )
        result = publish_reviewed_customer(
            company_id=company_id,
            site=site,
            actor=request.user,
            expected_version=int(request.POST.get('expected_version') or 0),
            idempotency_token=str(request.POST.get('idempotency_token') or ''),
            contact_point_id=int(request.POST.get('contact_point_id') or 0),
            team=team,
        )
    except (
        ValueError,
        ValidationError,
        PermissionDenied,
        Company.DoesNotExist,
        CompanyPoolState.DoesNotExist,
        SalesTeam.DoesNotExist,
    ) as error:
        messages.error(request, _error_text(error))
    else:
        messages.success(request, f'已核验“{result.company.name}”并发布到客户公海。')
    return redirect('console:customer_pool_detail', company_id=company_id)


@login_required
@require_POST
def customer_pool_archive(request, company_id: int):
    site, _locale = default_site_locale(None)
    try:
        result = archive_reviewed_customer(
            company_id=company_id,
            site=site,
            actor=request.user,
            expected_version=int(request.POST.get('expected_version') or 0),
            idempotency_token=str(request.POST.get('idempotency_token') or ''),
            reason=str(request.POST.get('reason') or ''),
        )
    except (
        ValueError,
        ValidationError,
        PermissionDenied,
        Company.DoesNotExist,
        CompanyPoolState.DoesNotExist,
    ) as error:
        messages.error(request, _error_text(error))
    else:
        messages.success(request, f'已归档“{result.company.name}”。')
    return redirect('console:customer_pool_detail', company_id=company_id)


@login_required
@require_POST
def customer_pool_restore(request, company_id: int):
    site, _locale = default_site_locale(None)
    try:
        result = restore_archived_customer(
            company_id=company_id,
            site=site,
            actor=request.user,
            expected_version=int(request.POST.get('expected_version') or 0),
            idempotency_token=str(request.POST.get('idempotency_token') or ''),
            reason=str(request.POST.get('reason') or ''),
        )
    except (
        ValueError,
        ValidationError,
        PermissionDenied,
        Company.DoesNotExist,
        CompanyPoolState.DoesNotExist,
    ) as error:
        messages.error(request, _error_text(error))
    else:
        messages.success(request, f'已将“{result.company.name}”恢复到待核验。')
    return redirect('console:customer_pool_detail', company_id=company_id)


@login_required
@require_POST
def customer_pool_release(request, company_id: int):
    site, _locale = default_site_locale(None)
    try:
        result = release_customer(
            company_id=company_id,
            site=site,
            actor=request.user,
            expected_version=int(request.POST.get('expected_version') or 0),
            idempotency_token=str(request.POST.get('idempotency_token') or ''),
            reason=str(request.POST.get('reason') or ''),
        )
    except (ValueError, ValidationError, PermissionDenied, Company.DoesNotExist, CompanyPoolState.DoesNotExist) as error:
        messages.error(request, _error_text(error))
    else:
        messages.success(request, f'已将“{result.company.name}”释放到客户公海。')
    return redirect(_safe_next(request))


def _customer_template_workbook() -> Workbook:
    workbook = Workbook()
    instructions = workbook.active
    instructions.title = '填写说明'
    instructions.append(['Vorntek 客户导入模板', '版本 1'])
    instructions.append(['规则', '“企业”与“联系方式”通过企业外部键关联；手机号、邮箱、证件号按文本填写。'])
    instructions.append(['数据来源', 'manual / research / website_form / meta_native / other'])
    instructions.append(['禁止', '不要填写密码、Token、Cookie 或其他密钥。'])
    companies = workbook.create_sheet('企业')
    company_headers = ['企业外部键*', '企业名称*', '国家/地区', '城市', '行业', '网站', '数据来源*', '来源详情', '备注']
    companies.append(company_headers)
    companies.append(['example-company-001', 'Vorntek 虚构工业客户', '中国', '苏州', '循环水控制系统', 'https://example.invalid', 'research', '合成来源示例，非真实研究结果', '仅为格式示例，禁止联系'])
    contacts = workbook.create_sheet('联系方式')
    contact_headers = ['企业外部键*', '联系人姓名', '职位', '联系方式类型*', '联系方式值*', '分机', '用途', '使用状态', '证据说明']
    contacts.append(contact_headers)
    contacts.append(['example-company-001', '示例联系人', '采购经理', 'email', 'person@example.invalid', '', 'business', 'unknown', '仅为格式示例'])
    for sheet in workbook.worksheets:
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='1F5B99')
            cell.alignment = Alignment(vertical='center')
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(42, max(14, max(len(str(cell.value or '')) for cell in column) + 2))
    source_validation = DataValidation(type='list', formula1='"manual,research,website_form,meta_native,other"', allow_blank=False)
    companies.add_data_validation(source_validation)
    source_validation.add('G2:G1001')
    channel_validation = DataValidation(type='list', formula1='"email,phone,whatsapp,website,other"', allow_blank=False)
    contacts.add_data_validation(channel_validation)
    channel_validation.add('D2:D3001')
    for sheet in (companies, contacts):
        for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row):
            for cell in row:
                cell.number_format = '@'
    return workbook


@login_required
@require_GET
def customer_pool_template(request):
    require_sales(request.user, SalesCapability.POOL_IMPORT)
    output = BytesIO()
    _customer_template_workbook().save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="vorntek-customer-import-template-v1.xlsx"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
def customer_pool_import(request):
    require_sales(request.user, SalesCapability.POOL_IMPORT)
    site, locale = default_site_locale(None)
    selected_batch = None
    selected_import_profile = str(request.POST.get('import_profile') or 'template')
    if selected_import_profile not in {'template', 'standard21', *RESEARCH_SNAPSHOT_PROFILES}:
        selected_import_profile = 'template'
    if request.method == 'POST':
        try:
            if request.POST.get('action') == 'commit':
                selected_batch = commit_customer_import(
                    batch_id=int(request.POST.get('batch_id') or 0),
                    site=site,
                    actor=request.user,
                )
                messages.success(request, '导入批次已执行；逐行结果已保留。')
            else:
                upload = request.FILES.get('file')
                max_bytes = (
                    MAX_IMPORT_BYTES
                    if selected_import_profile == 'template'
                    else MAX_RESEARCH_IMPORT_BYTES
                )
                if upload and upload.size > max_bytes:
                    raise ValidationError('文件超过所选导入类型的大小限制。')
                file_bytes = upload.read(max_bytes + 1) if upload else b''
                original_name = upload.name if upload else ''
                if selected_import_profile == 'standard21':
                    from leads.customer_standard21_import import preview_standard21_import

                    result = preview_standard21_import(
                        site=site,
                        actor=request.user,
                        file_bytes=file_bytes,
                        original_name=original_name,
                    )
                elif selected_import_profile == 'template':
                    result = preview_customer_import(
                        site=site,
                        actor=request.user,
                        file_bytes=file_bytes,
                        original_name=original_name,
                    )
                else:
                    result = preview_research_snapshot_import(
                        site=site,
                        actor=request.user,
                        file_bytes=file_bytes,
                        original_name=original_name,
                        profile_code=selected_import_profile,
                    )
                selected_batch = result.batch
                messages.info(request, '该文件已存在，显示原预览结果。' if result.replayed else '预览完成，尚未写入客户主数据。')
        except (ValueError, ValidationError, PermissionDenied, CustomerImportBatch.DoesNotExist) as error:
            messages.error(request, _error_text(error))
    elif request.GET.get('batch'):
        selected_batch = CustomerImportBatch.objects.filter(
            pk=request.GET.get('batch'), site=site, created_by=request.user
        ).first()
    count_labels = {
        'source_rows': '判定行',
        'company_rows': '企业源行',
        'contact_rows': '联系方式源行',
        'accounts': '账户数',
        'source_file_rows': '源文件行数',
        'pool_rows': '公海行',
        'new': '新增',
        'supplement': '补充',
        'unchanged': '不变',
        'quarantined': '待人工核验',
        'rejected': '拒绝',
        'imported': '已写入',
        'failed': '失败',
    }
    status_labels = {
        'preview': '待确认',
        'processing': '处理中',
        'partial': '部分完成',
        'succeeded': '已完成',
        'new': '新增',
        'supplement': '补充',
        'unchanged': '不变',
        'quarantined': '待人工核验',
        'rejected': '拒绝',
        'imported': '已写入',
        'failed': '失败',
    }
    batch_queryset = CustomerImportBatch.objects.filter(site=site).select_related('created_by').order_by('-created_at')[:20]
    batches = [
        {
            'id': batch.id,
            'original_name': batch.original_name,
            'created_by': batch.created_by,
            'created_at': batch.created_at,
            'status_label': status_labels.get(batch.status, batch.status),
        }
        for batch in batch_queryset
    ]
    selected_rows = []
    selected_summary = []
    selected_batch_status_label = ''
    if selected_batch:
        selected_rows = [
            {
                'row_number': row.row_number,
                'payload_json': row.payload_json,
                'status': row.status,
                'status_label': status_labels.get(row.status, row.status),
                'message': row.message,
            }
            for row in selected_batch.rows.order_by('row_number', 'id')[:500]
        ]
        selected_summary = [
            {'label': count_labels.get(key, key), 'value': value}
            for key, value in selected_batch.counts_json.items()
        ]
        selected_batch_status_label = status_labels.get(selected_batch.status, selected_batch.status)
    workspace = {
        'batches': batches,
        'selected_batch': selected_batch,
        'selected_batch_status_label': selected_batch_status_label,
        'selected_summary': selected_summary,
        'selected_rows': selected_rows,
        'import_profiles': [
            {'code': profile.code, 'label': profile.label}
            for profile in RESEARCH_SNAPSHOT_PROFILES.values()
        ] + [
            {'code': 'standard21', 'label': '标准 21 列客户池（CSV，10 MB 以内）'},
        ],
        'selected_import_profile': selected_import_profile,
        'template_url': reverse('console:customer_pool_template'),
        'pool_url': reverse('console:customer_pool'),
    }
    return render(request, 'console/v2/pages/customer_pool_import.html', _context(request, workspace=workspace, locale=locale))
