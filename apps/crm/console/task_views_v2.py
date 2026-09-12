from __future__ import annotations

import json
from datetime import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.db.models import Q

from leads.models import Activity, Company, Contact, LeadSubmission, Opportunity, Task
from leads.task_workflow_services import (
    TaskIdempotencyConflict,
    TaskOptimisticLockError,
    TaskWorkflowError,
    append_manual_activity_workflow,
    close_task_workflow,
    create_task_workflow,
    create_opportunity_workflow,
    reassign_task_workflow,
    start_task_workflow,
    update_task_workflow,
)

from .access import (
    activity_queryset_for_user,
    assignee_queryset_for_user,
    contact_queryset_for_user,
    crm_owned_queryset_for_user,
    lead_queryset_for_user,
    opportunity_queryset_for_user,
    primary_role_label,
    task_queryset_for_user,
)
from .capabilities import SalesCapability, can_sales, require_sales
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale
from .task_forms_v2 import (
    ActivityCorrectionV2Form,
    ActivityCreateV2Form,
    TaskCancelV2Form,
    TaskCompleteV2Form,
    TaskCreateV2Form,
    OpportunityCreateV2Form,
    TaskMutationForm,
    TaskStartV2Form,
)
from .task_versions import task_version_token, task_version_value
from .task_workspace_v2 import (
    build_activity_detail_workspace_v2,
    build_task_detail_workspace_v2,
    build_task_error_workspace,
    build_task_list_workspace_v2,
)


def _page_context(request, *, locale, workspace, active_key='tasks'):
    return {
        'workspace': workspace,
        'nav_items': build_navigation(active_key, request.user),
        'console_role_label': primary_role_label(request.user),
        'preview_site_url': f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
        'create_actions': [],
    }


def _errors(form):
    return {
        field: [item['message'] for item in items]
        for field, items in form.errors.get_json_data(escape_html=True).items()
    }


def _error_response(exc, *, status=400):
    if isinstance(exc, TaskIdempotencyConflict):
        status = 409
    if isinstance(exc, TaskOptimisticLockError):
        return JsonResponse({
            'ok': False,
            'code': exc.workflow_code,
            'message': str(exc.message),
            'current_version': task_version_token(exc.current_updated_at),
        }, status=409)
    message = getattr(exc, 'message', None) or '; '.join(getattr(exc, 'messages', [str(exc)]))
    return JsonResponse({
        'ok': False,
        'code': getattr(exc, 'workflow_code', getattr(exc, 'code', 'validation_error')),
        'message': message,
        'errors': {'__all__': [message]},
    }, status=status)


def _parse_version(value: str):
    return task_version_value(value)


def _scoped_target(*, site, user, target_type: str, target_id: int):
    if target_type == 'lead':
        value = lead_queryset_for_user(
            LeadSubmission.objects.filter(site=site), user
        ).select_related('team').filter(pk=target_id).first()
        key = 'submission'
    elif target_type == 'company':
        value = crm_owned_queryset_for_user(
            Company.objects.filter(site=site), user
        ).select_related('team').filter(pk=target_id).first()
        key = 'company'
    elif target_type == 'contact':
        value = contact_queryset_for_user(
            Contact.objects.filter(site=site), user
        ).select_related('team', 'company').filter(pk=target_id).first()
        key = 'contact'
    elif target_type == 'opportunity':
        value = opportunity_queryset_for_user(
            Opportunity.objects.filter(site=site), user
        ).select_related('team', 'company', 'primary_contact', 'source_submission').filter(pk=target_id).first()
        key = 'opportunity'
    else:
        raise Http404('销售关联对象无效。')
    if value is None:
        raise Http404('当前数据范围中没有该关联对象。')
    require_sales(user, SalesCapability.WRITE, record=value)
    return {key: value, 'team': value.team}


def _locked_task_authorizer(*, site, user):
    def authorize(task):
        if task.site_id != site.pk or not task_queryset_for_user(
            Task.objects.filter(pk=task.pk, site=site), user
        ).exists():
            raise Http404('当前数据范围中没有这条任务。')
        require_sales(user, SalesCapability.WRITE, record=task)
    return authorize


def _owner_queryset(*, site, user, team):
    if team is None:
        return assignee_queryset_for_user(user, site=site).none()
    return assignee_queryset_for_user(user, site=site).filter(
        sales_team_memberships__team=team,
        sales_team_memberships__team__enabled=True,
    ).distinct()


@login_required
@require_GET
def task_target_search_v2(request):
    require_sales(request.user, SalesCapability.WRITE)
    site, _locale = default_site_locale(None)
    target_type = str(request.GET.get('type') or '').strip()
    query = ' '.join(str(request.GET.get('q') or '').split())[:100]
    if len(query) < 2:
        return JsonResponse({'ok': True, 'items': [], 'message': '请输入至少 2 个字符。'})
    if target_type == 'lead':
        queryset = lead_queryset_for_user(
            LeadSubmission.objects.filter(site=site), request.user
        ).filter(Q(full_name__icontains=query) | Q(company__icontains=query))
        rows = list(queryset.select_related('team').order_by('-submitted_at', '-id')[:21])
        label = lambda item: item.full_name or item.company or f'线索 #{item.pk}'
        meta = lambda item: f'线索 #{item.pk} · {item.company or "未填写企业"}'
    elif target_type == 'company':
        queryset = crm_owned_queryset_for_user(
            Company.objects.filter(site=site), request.user
        ).filter(Q(name__icontains=query) | Q(country__icontains=query) | Q(city__icontains=query))
        rows = list(queryset.select_related('team').order_by('name', 'id')[:21])
        label = lambda item: item.name
        meta = lambda item: ' / '.join(value for value in (item.country, item.city) if value) or '未填写地区'
    elif target_type == 'contact':
        queryset = contact_queryset_for_user(
            Contact.objects.filter(site=site), request.user
        ).filter(Q(full_name__icontains=query) | Q(company__name__icontains=query))
        rows = list(queryset.select_related('team', 'company').order_by('full_name', 'id')[:21])
        label = lambda item: item.full_name
        meta = lambda item: item.company.name if item.company else '未关联企业'
    elif target_type == 'opportunity':
        queryset = opportunity_queryset_for_user(
            Opportunity.objects.filter(site=site), request.user
        ).filter(Q(name__icontains=query) | Q(company__name__icontains=query))
        rows = list(queryset.select_related('team', 'company').order_by('-updated_at', '-id')[:21])
        label = lambda item: item.name
        meta = lambda item: item.company.name if item.company else '未关联企业'
    else:
        return JsonResponse({'ok': False, 'errors': {'type': ['目标类型无效。']}}, status=400)
    items = []
    for item in rows[:20]:
        owners = _owner_queryset(site=site, user=request.user, team=item.team)
        items.append({
            'id': item.pk,
            'label': label(item),
            'meta': meta(item),
            'team': item.team.name if item.team else '未分配团队',
            'owners': [
                {'id': owner.pk, 'label': (owner.get_full_name() or owner.get_username()).strip()}
                for owner in owners[:100]
            ],
        })
    return JsonResponse({
        'ok': True,
        'items': items,
        'has_more': len(rows) > 20,
        'message': '结果超过 20 条，请缩小搜索范围。' if len(rows) > 20 else '',
    })


@login_required
@require_GET
def task_workspace_v2(request):
    require_sales(request.user, SalesCapability.READ)
    site, locale = default_site_locale(None)
    try:
        workspace = build_task_list_workspace_v2(request=request, site=site)
    except DatabaseError:
        workspace = build_task_error_workspace()
        return render(
            request,
            'console/v2/pages/tasks.html',
            _page_context(request, locale=locale, workspace=workspace),
            status=503,
        )
    if request.get_full_path() != workspace['canonical_url']:
        return redirect(workspace['canonical_url'])
    return render(
        request,
        'console/v2/pages/tasks.html',
        _page_context(request, locale=locale, workspace=workspace),
    )


@login_required
@require_GET
def task_new_v2(request):
    require_sales(request.user, SalesCapability.WRITE)
    site, locale = default_site_locale(None)
    try:
        workspace = build_task_list_workspace_v2(request=request, site=site)
    except DatabaseError:
        workspace = build_task_error_workspace()
        return render(
            request, 'console/v2/pages/tasks.html',
            _page_context(request, locale=locale, workspace=workspace), status=503,
        )
    workspace['auto_open_create'] = True
    return render(
        request, 'console/v2/pages/tasks.html',
        _page_context(request, locale=locale, workspace=workspace),
    )


@login_required
@require_GET
def task_workspace_detail_v2(request, task_id: int):
    require_sales(request.user, SalesCapability.READ)
    site, locale = default_site_locale(None)
    try:
        workspace = build_task_detail_workspace_v2(
            request=request, site=site, task_id=task_id
        )
    except DatabaseError:
        workspace = build_task_error_workspace(detail=True)
        return render(
            request,
            'console/v2/pages/task_detail.html',
            _page_context(request, locale=locale, workspace=workspace),
            status=503,
        )
    if workspace.get('missing'):
        raise Http404('当前数据范围中没有这条任务。')
    if request.get_full_path() != workspace['canonical_url']:
        return redirect(workspace['canonical_url'])
    return render(
        request,
        'console/v2/pages/task_detail.html',
        _page_context(request, locale=locale, workspace=workspace),
    )


@login_required
@require_GET
def activity_workspace_detail_v2(request, activity_id: int):
    require_sales(request.user, SalesCapability.READ)
    site, locale = default_site_locale(None)
    workspace = build_activity_detail_workspace_v2(
        request=request, site=site, activity_id=activity_id
    )
    if workspace.get('missing'):
        raise Http404('当前数据范围中没有这条活动。')
    if request.get_full_path() != workspace['canonical_url']:
        return redirect(workspace['canonical_url'])
    return render(
        request,
        'console/v2/pages/activity_detail.html',
        _page_context(request, locale=locale, workspace=workspace),
    )


@login_required
@require_POST
def task_create_v2(request):
    require_sales(request.user, SalesCapability.WRITE)
    site, _locale = default_site_locale(None)
    target_type = str(request.POST.get('target_type') or '').strip()
    target_id = str(request.POST.get('target_id') or '').strip()
    if not target_id.isdigit():
        return JsonResponse({'ok': False, 'errors': {'target_id': ['关联对象无效。']}}, status=400)
    target = _scoped_target(
        site=site, user=request.user, target_type=target_type, target_id=int(target_id)
    )
    owners = _owner_queryset(site=site, user=request.user, team=target['team'])
    form = TaskCreateV2Form(
        request.POST,
        site=site,
        target_team=target['team'],
        owner_queryset=owners,
    )
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    try:
        result = create_task_workflow(
            site=site,
            actor=request.user,
            owner_user=form.cleaned_data['owner_user'],
            title=form.cleaned_data['title'],
            description=form.cleaned_data['description'],
            task_type=form.cleaned_data['task_type'],
            priority=form.cleaned_data['priority'],
            due_at=form.cleaned_data['due_at'],
            idempotency_token=form.cleaned_data['idempotency_token'],
            request=request,
            **{key: value for key, value in target.items() if key != 'team'},
        )
    except TaskWorkflowError as exc:
        return _error_response(exc)
    return JsonResponse({
        'ok': True,
        'task_id': result.task.pk,
        'changed': result.changed,
        'replayed': result.replayed,
        'version': task_version_token(result.task.updated_at),
        'detail_url': reverse('console:task_workspace_detail_v2', args=(result.task.pk,)),
    })


@login_required
@require_POST
def opportunity_create_v2(request):
    require_sales(request.user, SalesCapability.WRITE)
    site, _locale = default_site_locale(None)
    company_id = str(request.POST.get('company_id') or '').strip()
    if not company_id.isdigit():
        return JsonResponse({'ok': False, 'errors': {'company_id': ['企业关联无效。']}}, status=400)
    company = crm_owned_queryset_for_user(
        Company.objects.filter(site=site), request.user
    ).select_related('team').filter(pk=int(company_id)).first()
    if company is None:
        raise Http404('当前数据范围中没有该企业。')
    require_sales(request.user, SalesCapability.WRITE, record=company)
    owners = _owner_queryset(site=site, user=request.user, team=company.team)
    form = OpportunityCreateV2Form(
        request.POST, site=site, company=company, owner_queryset=owners
    )
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)

    def authorize(locked_company):
        require_sales(request.user, SalesCapability.WRITE, record=locked_company)

    try:
        result = create_opportunity_workflow(
            site=site,
            actor=request.user,
            company=company,
            owner_user=form.cleaned_data['owner_user'],
            name=form.cleaned_data['name'],
            value_amount=form.cleaned_data['value_amount'],
            currency=form.cleaned_data['currency'],
            probability=form.cleaned_data['probability'],
            expected_close_date=form.cleaned_data['expected_close_date'],
            product_scope=form.cleaned_data['product_scope'],
            capacity_target=form.cleaned_data['capacity_target'],
            packaging_format=form.cleaned_data['packaging_format'],
            next_step=form.cleaned_data['next_step'],
            next_follow_up_at=form.cleaned_data['next_follow_up_at'],
            idempotency_token=form.cleaned_data['idempotency_token'],
            locked_company_authorizer=authorize,
            request=request,
        )
    except TaskWorkflowError as exc:
        return _error_response(exc)
    return JsonResponse({
        'ok': True,
        'opportunity_id': result.opportunity.pk,
        'replayed': result.replayed,
        'detail_url': reverse(
            'console:opportunity_workspace_detail_v2', args=(result.opportunity.pk,)
        ),
    })


def _scoped_task_for_form(*, site, user, task_id: int):
    task = task_queryset_for_user(
        Task.objects.filter(site=site), user
    ).select_related('owner_user', 'team').filter(pk=task_id).first()
    if task is None:
        raise Http404('当前数据范围中没有这条任务。')
    require_sales(user, SalesCapability.WRITE, record=task)
    return task


@login_required
@require_POST
def task_update_v2(request, task_id: int):
    site, _locale = default_site_locale(None)
    task = _scoped_task_for_form(site=site, user=request.user, task_id=task_id)
    allow_assign = can_sales(request.user, SalesCapability.ASSIGN, record=task)
    form = TaskMutationForm(
        request.POST,
        task=task,
        site=site,
        user=request.user,
        allow_assign=allow_assign,
    )
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    try:
        result = update_task_workflow(
            task_id=task.pk,
            actor=request.user,
            expected_updated_at=_parse_version(form.cleaned_data['version']),
            title=form.cleaned_data['title'],
            description=form.cleaned_data['description'],
            task_type=form.cleaned_data['task_type'],
            priority=form.cleaned_data['priority'],
            due_at=form.cleaned_data['due_at'],
            owner_user=form.cleaned_data['owner_user'],
            locked_task_authorizer=_locked_task_authorizer(site=site, user=request.user),
            request=request,
        )
    except (TaskWorkflowError, Http404) as exc:
        if isinstance(exc, Http404):
            raise
        return _error_response(exc)
    return JsonResponse({'ok': True, 'task_id': task.pk, 'changed': result.changed, 'version': task_version_token(result.task.updated_at)})


@login_required
@require_POST
def task_start_v2(request, task_id: int):
    site, _locale = default_site_locale(None)
    task = _scoped_task_for_form(site=site, user=request.user, task_id=task_id)
    form = TaskStartV2Form(request.POST)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    try:
        result = start_task_workflow(
            task_id=task.pk,
            actor=request.user,
            expected_updated_at=_parse_version(form.cleaned_data['version']),
            locked_task_authorizer=_locked_task_authorizer(site=site, user=request.user),
            request=request,
        )
    except TaskWorkflowError as exc:
        return _error_response(exc)
    return JsonResponse({'ok': True, 'task_id': task.pk, 'changed': result.changed, 'status': result.task.status, 'version': task_version_token(result.task.updated_at)})


@login_required
@require_POST
def task_complete_v2(request, task_id: int):
    site, _locale = default_site_locale(None)
    task = _scoped_task_for_form(site=site, user=request.user, task_id=task_id)
    owners = _owner_queryset(site=site, user=request.user, team=task.team)
    form = TaskCompleteV2Form(
        request.POST, task=task, site=site, owner_queryset=owners
    )
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    next_task = None
    if form.cleaned_data['create_next_task']:
        next_task = {
            'title': form.cleaned_data['next_title'],
            'description': form.cleaned_data['next_description'],
            'task_type': form.cleaned_data['next_task_type'],
            'priority': form.cleaned_data['next_priority'],
            'due_at': form.cleaned_data['next_due_at'],
            'owner_user': form.cleaned_data['next_owner_user'],
        }
    try:
        result = close_task_workflow(
            task_id=task.pk,
            actor=request.user,
            target_status='completed',
            outcome=form.cleaned_data['outcome'],
            expected_updated_at=_parse_version(form.cleaned_data['version']),
            next_task=next_task,
            locked_task_authorizer=_locked_task_authorizer(site=site, user=request.user),
            request=request,
        )
    except TaskWorkflowError as exc:
        return _error_response(exc)
    return JsonResponse({
        'ok': True, 'task_id': task.pk, 'changed': result.changed,
        'status': result.task.status, 'version': task_version_token(result.task.updated_at),
        'next_task_id': getattr(result.next_task, 'pk', None),
    })


@login_required
@require_POST
def task_cancel_v2(request, task_id: int):
    site, _locale = default_site_locale(None)
    task = _scoped_task_for_form(site=site, user=request.user, task_id=task_id)
    form = TaskCancelV2Form(request.POST)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    try:
        result = close_task_workflow(
            task_id=task.pk,
            actor=request.user,
            target_status='canceled',
            outcome=form.cleaned_data['reason'],
            expected_updated_at=_parse_version(form.cleaned_data['version']),
            locked_task_authorizer=_locked_task_authorizer(site=site, user=request.user),
            request=request,
        )
    except TaskWorkflowError as exc:
        return _error_response(exc)
    return JsonResponse({'ok': True, 'task_id': task.pk, 'changed': result.changed, 'status': result.task.status, 'version': task_version_token(result.task.updated_at)})


@login_required
@require_POST
def task_bulk_assign_v2(request):
    require_sales(request.user, SalesCapability.ASSIGN)
    site, _locale = default_site_locale(None)
    try:
        items = json.loads(request.POST.get('items_json') or '[]')
    except (TypeError, ValueError, json.JSONDecodeError):
        items = None
    owner_id = str(request.POST.get('owner_user') or '').strip()
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        return JsonResponse({
            'ok': False,
            'errors': {'items_json': ['请选择 1–100 条任务。']},
        }, status=400)
    if not owner_id.isdigit():
        return JsonResponse({'ok': False, 'errors': {'owner_user': ['负责人无效。']}}, status=400)
    owner = assignee_queryset_for_user(request.user, site=site).filter(
        pk=int(owner_id), is_active=True
    ).first()
    if owner is None:
        return JsonResponse({'ok': False, 'errors': {'owner_user': ['负责人不在可分配范围。']}}, status=400)

    results = []
    seen_ids = set()
    for item in items:
        task_id = str(item.get('id') if isinstance(item, dict) else '').strip()
        version = str(item.get('version') if isinstance(item, dict) else '').strip()
        if not task_id.isdigit() or int(task_id) in seen_ids:
            results.append({'id': task_id, 'status': 'invalid', 'message': '任务标识重复或无效。'})
            continue
        seen_ids.add(int(task_id))
        try:
            result = reassign_task_workflow(
                task_id=int(task_id),
                actor=request.user,
                owner_user=owner,
                expected_updated_at=_parse_version(version),
                locked_task_authorizer=_locked_task_authorizer(site=site, user=request.user),
                request=request,
            )
        except Http404:
            results.append({'id': int(task_id), 'status': 'hidden', 'message': '任务不存在或不在当前范围。'})
        except PermissionDenied:
            results.append({'id': int(task_id), 'status': 'hidden', 'message': '任务不存在或不在当前范围。'})
        except TaskOptimisticLockError as exc:
            results.append({
                'id': int(task_id), 'status': 'conflicted', 'message': str(exc.message),
                'current_version': task_version_token(exc.current_updated_at),
            })
        except TaskWorkflowError as exc:
            results.append({'id': int(task_id), 'status': 'invalid', 'message': str(exc.message)})
        else:
            results.append({
                'id': int(task_id), 'status': 'succeeded', 'changed': result.changed,
                'version': task_version_token(result.task.updated_at),
            })
    succeeded = sum(item['status'] == 'succeeded' for item in results)
    return JsonResponse({
        'ok': True,
        'partial': succeeded != len(results),
        'succeeded': succeeded,
        'total': len(results),
        'results': results,
    }, status=207 if succeeded != len(results) else 200)


@login_required
@require_POST
def activity_create_v2(request):
    require_sales(request.user, SalesCapability.WRITE)
    site, _locale = default_site_locale(None)
    form = ActivityCreateV2Form(request.POST)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    target = _scoped_target(
        site=site,
        user=request.user,
        target_type=form.cleaned_data['target_type'],
        target_id=form.cleaned_data['target_id'],
    )
    try:
        result = append_manual_activity_workflow(
            site=site,
            actor=request.user,
            activity_type=form.cleaned_data['activity_type'],
            direction=form.cleaned_data['direction'],
            subject=form.cleaned_data['subject'],
            body=form.cleaned_data['body'],
            occurred_at=form.cleaned_data['occurred_at'],
            idempotency_token=form.cleaned_data['idempotency_token'],
            request=request,
            **{key: value for key, value in target.items() if key != 'team'},
        )
    except TaskWorkflowError as exc:
        return _error_response(exc)
    return JsonResponse({
        'ok': True,
        'activity_id': result.activity.pk,
        'replayed': result.replayed,
        'detail_url': reverse('console:activity_workspace_detail_v2', args=(result.activity.pk,)),
    })


@login_required
@require_POST
def activity_correction_v2(request, activity_id: int):
    require_sales(request.user, SalesCapability.WRITE)
    site, _locale = default_site_locale(None)
    activity = activity_queryset_for_user(
        Activity.objects.filter(site=site), request.user
    ).select_related('submission', 'company', 'contact', 'opportunity').filter(pk=activity_id).first()
    if activity is None:
        raise Http404('当前数据范围中没有这条活动。')
    form = ActivityCorrectionV2Form(request.POST)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    try:
        result = append_manual_activity_workflow(
            site=site,
            actor=request.user,
            activity_type='note',
            direction='internal',
            subject=form.cleaned_data['subject'],
            body=form.cleaned_data['body'],
            occurred_at=datetime.now(tz=activity.occurred_at.tzinfo),
            idempotency_token=form.cleaned_data['idempotency_token'],
            submission=activity.submission,
            company=activity.company,
            contact=activity.contact,
            opportunity=activity.opportunity,
            metadata={'correction_of_activity_id': activity.pk},
            request=request,
            mutation_scope='activity.correction',
        )
    except TaskWorkflowError as exc:
        return _error_response(exc)
    return JsonResponse({
        'ok': True,
        'activity_id': result.activity.pk,
        'replayed': result.replayed,
        'detail_url': reverse('console:activity_workspace_detail_v2', args=(result.activity.pk,)),
    })
