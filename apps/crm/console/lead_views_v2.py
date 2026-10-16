from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from leads.lead_workflow_services import (
    UNCHANGED,
    ActivityInput,
    LeadWorkflowError,
    LeadOptimisticLockError,
    NextTaskInput,
    QualificationInput,
    assign_lead_owner,
    dispose_lead,
    move_lead_to_trash,
    restore_lead_from_trash,
)
from leads.models import LeadSubmission, SavedView
from leads.crm_services import validate_conversion_identity

from .access import (
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    active_lead_queryset,
    assignee_queryset_for_user,
    assignable_assignee_queryset_for_lead,
    lead_queryset_for_user,
    primary_role_label,
    safe_next_url,
    user_has_any_role,
)
from .capabilities import SalesCapability, can_sales, require_sales
from .commit_authorization import CommitAuthorizationError,current_actor_for_commit
from .lead_workspace_v2 import (
    QUERY_KEYS,
    build_lead_workspace_v2,
    lead_saved_view_payload,
)
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale


def _requested_locale_code(request) -> str | None:
    value = (request.GET.get('locale') or request.POST.get('locale') or '').strip().lower()
    return value or None


@dataclass(frozen=True, slots=True)
class _AssigneeChoice:
    """Template-compatible owner option with an explicit legacy-state label."""

    id: int
    username: str
    label: str
    is_active: bool
    current_only: bool = False

    def get_full_name(self) -> str:
        return self.label

    def get_username(self) -> str:
        return self.username


def _user_choice(user, *, current_only: bool = False) -> _AssigneeChoice:
    label = (user.get_full_name() or user.get_username()).strip()
    if current_only:
        state = '已停用，仅保留' if not user.is_active else '当前负责人，仅保留'
        label = f'{label}（{state}）'
    return _AssigneeChoice(
        id=user.id,
        username=user.get_username(),
        label=label,
        is_active=user.is_active,
        current_only=current_only,
    )


def _assignment_choices(user, lead) -> list[_AssigneeChoice]:
    candidates = list(assignable_assignee_queryset_for_lead(user, lead))
    choices = [_user_choice(candidate) for candidate in candidates]
    candidate_ids = {candidate.id for candidate in candidates}
    if lead.assignee_id and lead.assignee_id not in candidate_ids:
        # Historical records may point at a disabled user or at a user whose
        # team membership was removed.  Keeping that selected option prevents
        # an unrelated save from silently clearing the assignment.
        choices.append(_user_choice(lead.assignee, current_only=True))
    return choices


def _page_context(request, *, lead_id: int | None = None) -> dict:
    require_sales(request.user, SalesCapability.READ)
    site, locale = default_site_locale(_requested_locale_code(request))
    list_url = reverse('console:lead_workspace_v2')
    workspace = build_lead_workspace_v2(
        request,
        site=site,
        lead_id=lead_id,
        list_url=list_url,
        detail_url_name='console:lead_workspace_detail_v2',
        canonicalize=True,
    )
    if workspace.get('redirect_url'):
        return {'workspace': workspace}
    if workspace['selected_missing']:
        raise Http404('当前队列中没有这条线索。')

    selected_record = None
    if workspace['selected']:
        selected_record = (
            LeadSubmission.objects.filter(site=site, pk=workspace['selected']['id'])
            .select_related('assignee', 'team', 'form')
            .first()
        )

    assignment_choices = _assignment_choices(request.user, selected_record) if selected_record else []
    selected_owner = next(
        (choice for choice in assignment_choices if choice.id == selected_record.assignee_id),
        None,
    ) if selected_record else None
    is_trash_view = workspace['filters'].view == 'trash'
    can_bulk_assign = bool(
        not is_trash_view
        and can_sales(request.user, SalesCapability.ASSIGN)
    )
    can_bulk_trash = can_sales(request.user, SalesCapability.TRASH)
    bulk_owner_records = (
        list(assignee_queryset_for_user(request.user, site=site))
        if can_bulk_assign
        else []
    )
    selected_conversion = workspace['selected'].get('conversion') if workspace['selected'] else None
    conversion_eligible = bool(
        selected_record
        and selected_record.stage in {'qualified', 'won'}
        and selected_conversion is None
        and selected_record.assignee_id
        and selected_record.team_id
    )
    if conversion_eligible:
        try:
            conversion_fields = validate_conversion_identity(selected_record)
        except ValidationError:
            conversion_eligible = False
    can_convert = bool(
        not is_trash_view
        and conversion_eligible
        and can_sales(request.user, SalesCapability.CONVERT, record=selected_record)
    )
    conversion_opportunity_name = ''
    if can_convert:
        product_scope = workspace['selected']['product']
        if conversion_fields is not None:
            raw_scope = (selected_record.payload_json or {}).get('product_category')
            product_scope = '' if raw_scope is None else str(raw_scope).strip()
            if not conversion_fields.get('product_category', {}).get('enabled'):
                product_scope = ''
        if not product_scope or product_scope == '未填写':
            product_scope = '咨询项目'
        identity = (selected_record.company.strip() or selected_record.full_name.strip()
                    or selected_record.email or selected_record.phone or f'询盘 #{selected_record.pk}')
        conversion_opportunity_name = f'{identity} - {product_scope}'
    workspace.update(
        {
            'site': site,
            'locale': locale,
            'owners': assignment_choices,
            'selected_owner': selected_owner,
            'is_trash_view': is_trash_view,
            'can_write': bool(
                not is_trash_view
                and selected_record
                and can_sales(request.user, SalesCapability.WRITE, record=selected_record)
            ),
            'can_assign': bool(
                not is_trash_view
                and selected_record
                and can_sales(request.user, SalesCapability.ASSIGN, record=selected_record)
            ),
            # This flag deliberately combines authorization and domain
            # readiness.  A converted or not-yet-qualified lead must not
            # expose an action that the conversion service will reject.
            'can_convert': can_convert,
            'conversion_url': (
                reverse('console:lead_convert', args=[selected_record.pk])
                if can_convert
                else ''
            ),
            'conversion_opportunity_name': conversion_opportunity_name,
            'disposition_url': (
                reverse('console:lead_disposition_v2', args=[selected_record.pk])
                if selected_record
                else ''
            ),
            'bulk': {
                'can_select': can_bulk_trash or can_bulk_assign,
                'can_assign': can_bulk_assign,
                'url': reverse('console:lead_bulk_assign_v2'),
                'can_trash': bool(can_bulk_trash and not is_trash_view),
                'can_restore': bool(can_bulk_trash and is_trash_view),
                'trash_url': reverse('console:lead_bulk_trash_v2'),
                'restore_url': reverse('console:lead_bulk_restore_v2'),
                'max_records': 100,
                'owners': [
                    {'id': owner.id, 'label': owner.get_full_name() or owner.get_username()}
                    for owner in bulk_owner_records
                ],
            },
            'assignment_readiness': {
                'blocked': bool(can_bulk_assign and not bulk_owner_records),
                'message': (
                    '当前站点没有可分配的启用销售成员。线索会保留在“未分配”队列，但无法进入明确负责人跟进。'
                    if can_bulk_assign and not bulk_owner_records
                    else ''
                ),
                'manage_url': (
                    reverse('console:system_teams')
                    if can_bulk_assign
                    and not bulk_owner_records
                    and user_has_any_role(request.user, {ROLE_SYSTEM_ADMIN})
                    else ''
                ),
            },
        }
    )
    return {
        'workspace': workspace,
        'nav_items': build_navigation('leads', request.user),
        'console_role_label': primary_role_label(request.user),
        'preview_site_url': f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
    }


@login_required
@require_GET
def lead_workspace_v2(request):
    require_sales(request.user, SalesCapability.READ)
    site, _locale = default_site_locale(_requested_locale_code(request))
    if not any(key in request.GET for key in QUERY_KEYS):
        default_view = (
            SavedView.objects.filter(
                site=site,
                user=request.user,
                scope='leads',
                is_default=True,
            )
            .select_related('team')
            .order_by('id')
            .first()
        )
        if default_view is not None:
            try:
                payload = lead_saved_view_payload(
                    default_view,
                    site=site,
                    user=request.user,
                    list_url=reverse('console:lead_workspace_v2'),
                )
            except ValidationError:
                payload = None
            if payload and payload['apply_url']:
                # The canonical URL always contains page/sort/page_size, so
                # the next request cannot re-enter this default-view branch.
                return redirect(payload['apply_url'])
    target = build_lead_workspace_v2(request, site=site, list_url=reverse('console:lead_workspace_v2'),
        redirect_only=True)['redirect_url']
    if target:
        return redirect(target)
    context = _page_context(request)
    return render(request, 'console/v2/pages/leads.html', context)


@login_required
@require_GET
def lead_workspace_detail_v2(request, submission_id: int):
    context = _page_context(request, lead_id=submission_id)
    workspace = context['workspace']
    if workspace.get('redirect_url'):
        return redirect(workspace['redirect_url'])
    return render(
        request,
        'console/v2/pages/leads.html',
        context,
    )


def _truthy(request, key: str) -> bool:
    return (request.POST.get(key) or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _optional_id(value: str | None, *, field_label: str):
    if value is None:
        return UNCHANGED
    resolved = value.strip()
    if not resolved:
        return None
    try:
        return int(resolved)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f'{field_label}无效。', code='invalid_id') from exc


def _optional_money(value: str | None):
    if value is None:
        return UNCHANGED
    resolved = value.strip()
    if not resolved:
        return None
    try:
        return Decimal(resolved)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError('预计金额无效。', code='invalid_money') from exc


def _validation_error(exc: ValidationError) -> tuple[str, str]:
    message = exc.messages[0] if exc.messages else '提交内容无效。'
    code = getattr(exc, 'code', None) or 'validation_error'
    return str(message), str(code)


def _wants_json(request) -> bool:
    return (
        'application/json' in (request.headers.get('Accept') or '')
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


@login_required
@require_POST
def lead_disposition_v2(request, submission_id: int):
    site, _locale = default_site_locale(_requested_locale_code(request))
    lead = (
        lead_queryset_for_user(
            LeadSubmission.objects.filter(site=site).select_related('assignee', 'team'),
            request.user,
        )
        .filter(pk=submission_id)
        .select_related('assignee', 'team')
        .first()
    )
    if lead is None:
        raise Http404('线索不存在。')
    require_sales(request.user, SalesCapability.WRITE, record=lead)

    current_fallback = reverse('console:lead_workspace_detail_v2', args=[lead.pk])
    canonical_query = {}
    for post_key, query_key in (
        ('q', 'q'),
        ('stage_filter', 'stage'),
        ('language', 'language'),
        ('source', 'source'),
        ('owner', 'owner'),
        ('sla', 'sla'),
        ('sort', 'sort'),
        ('page', 'page'),
        ('page_size', 'page_size'),
    ):
        value = (request.POST.get(post_key) or '').strip()
        if not value or (query_key in {'stage', 'language', 'source', 'owner', 'sla'} and value == 'all'):
            continue
        canonical_query[query_key] = value
    if canonical_query:
        current_fallback = f'{current_fallback}?{urlencode(canonical_query)}'
    stable_query = {
        key: value
        for key, value in canonical_query.items()
        if key not in {'q', 'stage', 'owner', 'sla'}
    }
    stable_current_url = reverse('console:lead_workspace_detail_v2', args=[lead.pk])
    if stable_query:
        stable_current_url = f'{stable_current_url}?{urlencode(stable_query)}'
    current_url = safe_next_url(request, request.POST.get('current_url'), current_fallback)
    next_candidate = (request.POST.get('next_url') or '').strip()
    next_url = (
        safe_next_url(request, next_candidate, stable_current_url)
        if next_candidate
        else ''
    )

    try:
        assignee_id = _optional_id(request.POST.get('assignee_id'), field_label='负责人')

        def authorize_locked_submission(locked_lead):
            require_sales(request.user, SalesCapability.WRITE, record=locked_lead)
            if assignee_id is not UNCHANGED and assignee_id != locked_lead.assignee_id:
                require_sales(request.user, SalesCapability.ASSIGN, record=locked_lead)

        quick_action = (request.POST.get('quick_action') or '').strip()
        quick_stage_map = {
            'contacted': 'contacted',
            'qualified': 'qualified',
            'quotation': 'qualified',
            'won': 'won',
            'lost': 'lost',
        }
        if quick_action and quick_action not in quick_stage_map:
            raise ValidationError('销售处置动作无效。', code='quick_action_invalid')
        requirement_updates = {
            key: request.POST.get(key, '')
            for key in (
                'country', 'company', 'product_category', 'capacity',
                'packaging_format', 'project_type', 'purchase_timeline', 'contact_role',
            )
            if key in request.POST
        }
        if quick_action in {'qualified', 'quotation', 'won'}:
            required_keys = {
                'country', 'company', 'product_category', 'capacity',
                'packaging_format', 'project_type', 'purchase_timeline', 'contact_role',
            }
            missing = sorted(
                key for key in required_keys
                if not str(requirement_updates.get(key) or '').strip()
            )
            if missing:
                raise ValidationError(
                    '标记合格、报价或成交前，请补齐国家、公司、产品、目标产能、包装、项目类型、采购时间和联系人角色。',
                    code='quick_requirements_incomplete',
                )

        next_task = None
        if quick_action in {'contacted', 'qualified', 'quotation'}:
            due_at = (request.POST.get('task_due_at') or '').strip()
            if not due_at:
                raise ValidationError('请安排明确的下一步时间。', code='quick_next_step_required')
            next_task = NextTaskInput(
                title=(request.POST.get('task_title') or '').strip() or (
                    '准备并发送报价' if quick_action == 'quotation' else '继续跟进 WhatsApp 线索'
                ),
                due_at=due_at,
                task_type='quote' if quick_action == 'quotation' else 'whatsapp',
                priority=(request.POST.get('task_priority') or 'normal').strip(),
                owner_user_id=(None if assignee_id is UNCHANGED else assignee_id),
                description=(request.POST.get('task_description') or '').strip(),
            )
        elif _truthy(request, 'create_task'):
            next_task = NextTaskInput(
                title=(request.POST.get('task_title') or '').strip(),
                due_at=(request.POST.get('task_due_at') or '').strip(),
                task_type=(request.POST.get('task_type') or 'follow_up').strip(),
                priority=(request.POST.get('task_priority') or 'normal').strip(),
                owner_user_id=(None if assignee_id is UNCHANGED else assignee_id),
                description=(request.POST.get('task_description') or '').strip(),
            )

        quick_is_positive = quick_action in {'qualified', 'quotation', 'won'}
        quick_notes = (request.POST.get('decision_notes') or '').strip()
        if quick_action and not quick_notes:
            raise ValidationError('请记录这次判断的依据或结果。', code='quick_notes_required')
        buyer_value = _optional_money(request.POST.get('buyer_value'))
        if quick_action == 'won' and (
            buyer_value is UNCHANGED or buyer_value is None or buyer_value <= 0
        ):
            raise ValidationError('确认成交时必须填写大于 0 的真实成交金额。', code='quick_buyer_value_required')

        result = dispose_lead(
            submission_id=lead.pk,
            actor=request.user,
            expected_updated_at=(request.POST.get('expected_updated_at') or '').strip(),
            stage=(quick_stage_map[quick_action] if quick_action else (request.POST.get('stage') or '').strip()),
            qualification=(
                QualificationInput(
                    contactable=quick_action != 'lost',
                    company_verified=quick_is_positive,
                    project_confirmed=quick_is_positive,
                    technical_fit=quick_is_positive,
                    next_step_confirmed=quick_action in {'qualified', 'quotation', 'won'},
                    notes=quick_notes,
                )
                if quick_action
                else QualificationInput(
                    contactable=_truthy(request, 'qualification_contactable'),
                    company_verified=_truthy(request, 'qualification_company_verified'),
                    project_confirmed=_truthy(request, 'qualification_project_confirmed'),
                    technical_fit=_truthy(request, 'qualification_technical_fit'),
                    next_step_confirmed=_truthy(request, 'qualification_next_step_confirmed'),
                    overridden=_truthy(request, 'qualification_overridden'),
                    notes=(request.POST.get('qualification_notes') or '').strip(),
                )
            ),
            activity=(
                ActivityInput(
                    activity_type='whatsapp',
                    direction='internal',
                    subject={
                        'contacted': 'WhatsApp 线索已联系',
                        'qualified': 'WhatsApp 线索判定为合格',
                        'quotation': 'WhatsApp 线索进入报价',
                        'won': 'WhatsApp 线索确认成交',
                        'lost': 'WhatsApp 线索判定为失败',
                    }[quick_action],
                    body=quick_notes,
                    metadata={'workflow': 'whatsapp_quick_disposition', 'quick_action': quick_action},
                )
                if quick_action
                else ActivityInput(
                    activity_type=(request.POST.get('activity_type') or '').strip(),
                    direction=(request.POST.get('activity_direction') or 'outbound').strip(),
                    subject=(request.POST.get('activity_subject') or '').strip(),
                    body=(request.POST.get('activity_body') or '').strip(),
                )
            ),
            next_task=next_task,
            assignee_id=assignee_id,
            follow_up_notes=(request.POST.get('follow_up_notes') or '').strip(),
            buyer_value=buyer_value,
            buyer_currency=(
                UNCHANGED
                if 'buyer_currency' not in request.POST
                else (request.POST.get('buyer_currency') or 'USD').strip()
            ),
            requirement_updates=requirement_updates,
            idempotency_token=(request.POST.get('idempotency_token') or '').strip(),
            request=request,
            locked_submission_authorizer=authorize_locked_submission,
        )
    except ValidationError as exc:
        message, code = _validation_error(exc)
        status = 409 if isinstance(exc, LeadOptimisticLockError) else 400
        if _wants_json(request):
            payload = {'ok': False, 'message': message, 'code': code}
            if isinstance(exc, LeadOptimisticLockError):
                payload['current_updated_at'] = (
                    exc.current_updated_at.isoformat() if exc.current_updated_at else ''
                )
            return JsonResponse(payload, status=status)
        messages.error(request, message)
        return redirect(current_url)

    destination = (
        next_url
        if request.POST.get('continue_action') == 'next' and next_url
        else stable_current_url
    )
    notice = '线索处置已保存。'
    if result.replayed:
        notice = '这次提交已保存，无需重复写入。'
    elif result.dispatch_scheduled:
        notice = f'线索处置已保存，{result.dispatch_scheduled} 条营销回传已进入发送队列。'

    if _wants_json(request):
        return JsonResponse(
            {
                'ok': True,
                'message': notice,
                'redirect_url': destination,
                'replayed': result.replayed,
                'current_updated_at': (
                    result.submission.updated_at.isoformat()
                    if result.submission.updated_at else ''
                ),
                'dispatch_scheduled': result.dispatch_scheduled,
                'idempotency_mode': result.idempotency_mode,
            }
        )
    messages.success(request, notice)
    return redirect(destination)


def _assignment_record_payload(result) -> dict:
    submission = result.submission
    assignee = submission.assignee
    team = submission.team
    return {
        'id': submission.pk,
        'changed': result.changed,
        'current_updated_at': (
            submission.updated_at.isoformat() if submission.updated_at else ''
        ),
        'assignee': {
            'id': assignee.pk,
            'label': assignee.get_full_name().strip() or assignee.get_username(),
        },
        'team': (
            {'id': team.pk, 'label': team.name}
            if team is not None
            else None
        ),
    }


@login_required
@require_POST
def lead_bulk_assign_v2(request):
    """Assign one or many scoped leads with explicit per-record outcomes."""

    if not can_sales(request.user, SalesCapability.ASSIGN):
        return JsonResponse(
            {
                'ok': False,
                'updated': 0,
                'records': [],
                'failures': [],
                'message': '当前账号没有负责人分配权限。',
            },
            status=403,
        )

    raw_ids = request.POST.getlist('record_ids')
    if not raw_ids:
        return JsonResponse(
            {
                'ok': False,
                'updated': 0,
                'records': [],
                'failures': [],
                'message': '请至少选择一条线索。',
            },
            status=400,
        )
    record_ids: list[int] = []
    seen_ids: set[int] = set()
    for raw_id in raw_ids:
        value = str(raw_id or '').strip()
        if not value.isdigit() or int(value) <= 0:
            return JsonResponse(
                {
                    'ok': False,
                    'updated': 0,
                    'records': [],
                    'failures': [],
                    'message': '线索编号格式无效。',
                },
                status=400,
            )
        record_id = int(value)
        if record_id not in seen_ids:
            seen_ids.add(record_id)
            record_ids.append(record_id)
    if len(record_ids) > 100:
        return JsonResponse(
            {
                'ok': False,
                'updated': 0,
                'records': [],
                'failures': [],
                'message': '一次最多分配 100 条线索。',
            },
            status=400,
        )

    raw_assignee_id = str(request.POST.get('assignee_id') or '').strip()
    if not raw_assignee_id.isdigit() or int(raw_assignee_id) <= 0:
        return JsonResponse(
            {
                'ok': False,
                'updated': 0,
                'records': [],
                'failures': [],
                'message': '请选择有效负责人。',
            },
            status=400,
        )
    assignee_id = int(raw_assignee_id)
    site, _locale = default_site_locale(_requested_locale_code(request))
    scoped_ids = set(
        active_lead_queryset(
            lead_queryset_for_user(
                LeadSubmission.objects.filter(site=site, pk__in=record_ids),
                request.user,
            )
        ).values_list('pk', flat=True)
    )

    records = []
    failures = []
    for index,record_id in enumerate(record_ids):
        if record_id not in scoped_ids:
            failures.append({
                'id': record_id,
                'message': '线索不存在或不在可分配范围内。',
                'code': 'not_found',
            })
            continue

        def authorize_locked_submission(locked_lead):
            if locked_lead.trashed_at is not None or locked_lead.site_id != site.id or not can_sales(
                actor,
                SalesCapability.ASSIGN,
                record=locked_lead,
            ):
                raise PermissionDenied

        def authorize_locked_assignee(locked_lead, target_assignee_id):
            if not assignable_assignee_queryset_for_lead(
                actor,
                locked_lead,
            ).filter(pk=target_assignee_id).exists():
                raise LeadWorkflowError(
                    '负责人不可用于该线索，可能已停用或不属于当前销售团队。',
                    code='assignee_unavailable',
                )

        try:
            with transaction.atomic():
                actor=current_actor_for_commit(request.user.pk,request.session.session_key,
                    {ROLE_SALES_MANAGER,ROLE_SYSTEM_ADMIN}, related_user_ids=(assignee_id,))
                result = assign_lead_owner(
                    submission_id=record_id,
                    actor=actor,
                    assignee_id=assignee_id,
                    request=request,
                    locked_submission_authorizer=authorize_locked_submission,
                    locked_assignee_authorizer=authorize_locked_assignee,
                )
        except CommitAuthorizationError as exc:
            failures.extend({'id':pending_id,'message':str(exc),'code':'authorization_changed'}
                for pending_id in record_ids[index:])
            break
        except (LeadSubmission.DoesNotExist, PermissionDenied):
            failures.append({
                'id': record_id,
                'message': '线索不存在或不在可分配范围内。',
                'code': 'not_found',
            })
        except ValidationError as exc:
            message, code = _validation_error(exc)
            failures.append({'id': record_id, 'message': message, 'code': code})
        else:
            records.append(_assignment_record_payload(result))

    updated = sum(1 for record in records if record['changed'])
    if records and failures:
        status = 207
        message = f'已更新 {updated} 条，{len(failures)} 条失败。'
        if not updated:
            message = f'可访问记录的负责人未变化，另有 {len(failures)} 条失败。'
    elif records:
        status = 200
        message = (
            f'已更新 {updated} 条线索的负责人。'
            if updated
            else '所选线索的负责人未变化，无需更新。'
        )
    else:
        status = 403 if failures and failures[0]['code']=='authorization_changed' else 404 if failures and all(
            failure['code'] == 'not_found' for failure in failures
        ) else 400
        message = failures[0]['message'] if failures else '没有线索被更新。'
    return JsonResponse(
        {
            'ok': bool(records),
            'updated': updated,
            'records': records,
            'failures': failures,
            'message': message,
        },
        status=status,
    )


def _trash_record_payload(result) -> dict:
    submission = result.submission
    return {
        'id': submission.pk,
        'changed': result.changed,
        'trashed_at': submission.trashed_at.isoformat() if submission.trashed_at else '',
        'restored_at': submission.restored_at.isoformat() if submission.restored_at else '',
        'current_updated_at': (
            submission.updated_at.isoformat() if submission.updated_at else ''
        ),
    }


def _bulk_trash_transition(request, *, restore: bool) -> JsonResponse:
    action_label = '恢复' if restore else '移入回收站'
    if not can_sales(request.user, SalesCapability.TRASH):
        return JsonResponse(
            {
                'ok': False,
                'updated': 0,
                'records': [],
                'failures': [],
                'message': f'当前账号没有线索{action_label}权限。',
            },
            status=403,
        )

    raw_ids = request.POST.getlist('record_ids')
    if not raw_ids:
        return JsonResponse(
            {
                'ok': False,
                'updated': 0,
                'records': [],
                'failures': [],
                'message': '请至少选择一条线索。',
            },
            status=400,
        )
    record_ids: list[int] = []
    seen_ids: set[int] = set()
    for raw_id in raw_ids:
        value = str(raw_id or '').strip()
        if not value.isdigit() or int(value) <= 0:
            return JsonResponse(
                {
                    'ok': False,
                    'updated': 0,
                    'records': [],
                    'failures': [],
                    'message': '线索编号格式无效。',
                },
                status=400,
            )
        record_id = int(value)
        if record_id not in seen_ids:
            seen_ids.add(record_id)
            record_ids.append(record_id)
    if len(record_ids) > 100:
        return JsonResponse(
            {
                'ok': False,
                'updated': 0,
                'records': [],
                'failures': [],
                'message': f'一次最多{action_label} 100 条线索。',
            },
            status=400,
        )

    site, _locale = default_site_locale(_requested_locale_code(request))
    scoped_ids = set(
        lead_queryset_for_user(
            LeadSubmission.objects.filter(site=site, pk__in=record_ids),
            request.user,
        ).values_list('pk', flat=True)
    )
    records = []
    failures = []
    command = restore_lead_from_trash if restore else move_lead_to_trash
    reason = str(request.POST.get('reason') or '').strip()

    for index,record_id in enumerate(record_ids):
        if record_id not in scoped_ids:
            failures.append({
                'id': record_id,
                'message': f'线索不存在或不在可{action_label}范围内。',
                'code': 'not_found',
            })
            continue

        def authorize_locked_submission(locked_lead):
            if locked_lead.site_id != site.id or not can_sales(
                actor,
                SalesCapability.TRASH,
                record=locked_lead,
            ):
                raise PermissionDenied

        command_kwargs = {
            'submission_id': record_id,
            'actor': request.user,
            'request': request,
            'locked_submission_authorizer': authorize_locked_submission,
        }
        if not restore:
            command_kwargs['reason'] = reason
        try:
            with transaction.atomic():
                actor=current_actor_for_commit(request.user.pk,request.session.session_key,
                    {ROLE_SALES_MANAGER,ROLE_SYSTEM_ADMIN})
                command_kwargs['actor']=actor
                result = command(**command_kwargs)
        except CommitAuthorizationError as exc:
            failures.extend({'id':pending_id,'message':str(exc),'code':'authorization_changed'}
                for pending_id in record_ids[index:])
            break
        except (LeadSubmission.DoesNotExist, PermissionDenied):
            failures.append({
                'id': record_id,
                'message': f'线索不存在或不在可{action_label}范围内。',
                'code': 'not_found',
            })
        except ValidationError as exc:
            message, code = _validation_error(exc)
            failures.append({'id': record_id, 'message': message, 'code': code})
        else:
            records.append(_trash_record_payload(result))

    updated = sum(1 for record in records if record['changed'])
    if records and failures:
        status = 207
        message = f'已{action_label} {updated} 条，{len(failures)} 条失败。'
    elif records:
        status = 200
        message = (
            f'已{action_label} {updated} 条线索。'
            if updated
            else f'所选线索状态未变化，无需再次{action_label}。'
        )
    else:
        status = 403 if failures and failures[0]['code']=='authorization_changed' else 404 if failures and all(
            failure['code'] == 'not_found' for failure in failures
        ) else 400
        message = failures[0]['message'] if failures else f'没有线索被{action_label}。'
    return JsonResponse(
        {
            'ok': bool(records),
            'updated': updated,
            'records': records,
            'failures': failures,
            'message': message,
        },
        status=status,
    )


@login_required
@require_POST
def lead_bulk_trash_v2(request):
    return _bulk_trash_transition(request, restore=False)


@login_required
@require_POST
def lead_bulk_restore_v2(request):
    return _bulk_trash_transition(request, restore=True)
