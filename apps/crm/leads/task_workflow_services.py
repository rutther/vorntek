from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .crm_relationships import validate_crm_relationship_targets
from .crm_services import (
    ACTIONABLE_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    append_activity,
    create_task,
    synchronize_submission_next_follow_up,
    _set_stage_audit_context,
)
from .models import Activity, Company, CrmMutationReceipt, Opportunity, SalesTeamMember, Task


TASK_TYPES = frozenset({'follow_up', 'call', 'email', 'whatsapp', 'meeting', 'quote', 'review', 'other'})
TASK_PRIORITIES = frozenset({'low', 'normal', 'high', 'urgent'})
MANUAL_ACTIVITY_TYPES = frozenset({'note', 'call', 'email', 'whatsapp', 'meeting', 'site_visit'})
ACTIVITY_DIRECTIONS = frozenset({'inbound', 'outbound', 'internal'})
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9._:-]{16,200}$')


class TaskWorkflowError(ValidationError):
    def __init__(self, message: str, *, code: str = 'task_invalid'):
        super().__init__(message, code=code)
        self.workflow_code = code


class TaskOptimisticLockError(TaskWorkflowError):
    def __init__(self, current_updated_at):
        super().__init__('任务已经被其他操作更新，请刷新后核对再提交。', code='task_version_conflict')
        self.current_updated_at = current_updated_at


class TaskIdempotencyConflict(TaskWorkflowError):
    def __init__(self):
        super().__init__('同一请求令牌已用于不同内容，请刷新表单后重试。', code='idempotency_conflict')


@dataclass(frozen=True, slots=True)
class TaskMutationResult:
    task: Task
    changed: bool
    replayed: bool = False
    next_task: Task | None = None


@dataclass(frozen=True, slots=True)
class ActivityMutationResult:
    activity: Activity
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class OpportunityMutationResult:
    opportunity: Opportunity
    replayed: bool = False


def _clean_text(value: Any, *, field: str, maximum: int, required: bool = False) -> str:
    resolved = str(value or '').strip()
    if required and not resolved:
        raise TaskWorkflowError(f'{field}不能为空。', code=f'{field}_required')
    if len(resolved) > maximum:
        raise TaskWorkflowError(f'{field}不能超过 {maximum} 个字符。', code=f'{field}_too_long')
    return resolved


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _token_hash(token: str) -> str:
    resolved = str(token or '').strip()
    if not TOKEN_PATTERN.fullmatch(resolved):
        raise TaskWorkflowError('请求令牌无效，请刷新表单后重试。', code='idempotency_token_invalid')
    return hashlib.sha256(resolved.encode('utf-8')).hexdigest()


def _receipt(
    *,
    site,
    actor,
    mutation_scope: str,
    idempotency_token: str,
    fingerprint_payload: dict[str, Any],
) -> tuple[CrmMutationReceipt, bool]:
    token_hash = _token_hash(idempotency_token)
    fingerprint_hash = _hash_payload(fingerprint_payload)
    try:
        receipt, created = CrmMutationReceipt.objects.get_or_create(
            site=site,
            actor_user=actor,
            mutation_scope=mutation_scope,
            token_hash=token_hash,
            defaults={
                'fingerprint_hash': fingerprint_hash,
                'status': 'processing',
            },
        )
    except IntegrityError:
        receipt = CrmMutationReceipt.objects.select_for_update().get(
            site=site,
            actor_user=actor,
            mutation_scope=mutation_scope,
            token_hash=token_hash,
        )
        created = False
    if not created:
        receipt = CrmMutationReceipt.objects.select_for_update().get(pk=receipt.pk)
        if receipt.expires_at <= timezone.now():
            receipt.delete()
            receipt = CrmMutationReceipt.objects.create(
                site=site,
                actor_user=actor,
                mutation_scope=mutation_scope,
                token_hash=token_hash,
                fingerprint_hash=fingerprint_hash,
                status='processing',
            )
            return receipt, False
        if receipt.fingerprint_hash != fingerprint_hash:
            raise TaskIdempotencyConflict()
        if receipt.status != 'succeeded' or receipt.entity_id is None:
            raise TaskWorkflowError('相同请求正在处理中，请稍后安全重试。', code='idempotency_in_progress')
        return receipt, True
    return receipt, False


def _finish_receipt(receipt: CrmMutationReceipt, *, entity_table: str, entity_id: int, result: dict[str, Any]):
    receipt.status = 'succeeded'
    receipt.entity_table = entity_table
    receipt.entity_id = entity_id
    receipt.result_json = result
    receipt.save(update_fields=['status', 'entity_table', 'entity_id', 'result_json', 'updated_at'])


def _task_targets(task: Task) -> dict[str, Any]:
    return {
        'submission': task.submission,
        'company': task.company,
        'contact': task.contact,
        'opportunity': task.opportunity,
    }


def _target_ids(targets: dict[str, Any]) -> dict[str, int | None]:
    return {name: getattr(value, 'pk', None) if value is not None else None for name, value in targets.items()}


def _task_snapshot(task: Task) -> dict[str, Any]:
    return {
        'status': task.status,
        'owner_user_id': task.owner_user_id,
        'team_id': task.team_id,
        'title': task.title,
        'task_type': task.task_type,
        'priority': task.priority,
        'due_at': task.due_at.isoformat() if task.due_at else None,
        'completed_at': task.completed_at.isoformat() if task.completed_at else None,
        **{f'{name}_id': value for name, value in _target_ids(_task_targets(task)).items()},
    }


def _validate_owner(*, owner_user, site, team):
    if owner_user is None or not getattr(owner_user, 'is_active', False):
        raise TaskWorkflowError('任务负责人必须是有效账号。', code='owner_invalid')
    if team is None:
        raise TaskWorkflowError('任务关联对象必须有明确销售团队。', code='team_required')
    if team.site_id != site.pk or not team.enabled:
        raise TaskWorkflowError('任务销售团队不可用。', code='team_invalid')
    if not SalesTeamMember.objects.filter(
        team=team,
        user=owner_user,
        team__site=site,
        team__enabled=True,
    ).exists():
        raise TaskWorkflowError('任务负责人必须是关联对象所在团队的有效成员。', code='owner_team_mismatch')


def _validate_task_fields(*, title, description, task_type, priority, due_at):
    resolved_title = _clean_text(title, field='任务标题', maximum=240, required=True)
    resolved_description = _clean_text(description, field='任务说明', maximum=4000)
    if task_type not in TASK_TYPES:
        raise TaskWorkflowError('任务类型无效。', code='task_type_invalid')
    if priority not in TASK_PRIORITIES:
        raise TaskWorkflowError('任务优先级无效。', code='priority_invalid')
    if due_at is None:
        raise TaskWorkflowError('到期时间不能为空。', code='due_at_required')
    if timezone.is_naive(due_at):
        raise TaskWorkflowError('到期时间必须包含站点时区。', code='due_at_naive')
    return resolved_title, resolved_description


def _audit_recorder(value):
    if value is not None:
        return value
    from console.audit import record_audit
    return record_audit


def _outcome_hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@transaction.atomic
def create_opportunity_workflow(
    *,
    site,
    actor,
    company: Company,
    owner_user,
    name,
    idempotency_token: str,
    value_amount=None,
    currency: str = 'USD',
    probability: int = 10,
    expected_close_date=None,
    product_scope: str = '',
    capacity_target: str = '',
    packaging_format: str = '',
    next_step: str = '',
    next_follow_up_at=None,
    locked_company_authorizer=None,
    request=None,
    audit_recorder: Callable[..., Any] | None = None,
) -> OpportunityMutationResult:
    resolved_name = _clean_text(name, field='项目名称', maximum=240, required=True)
    resolved_currency = str(currency or '').strip().upper()
    if len(resolved_currency) != 3 or not resolved_currency.isalpha():
        raise TaskWorkflowError('币种必须使用三个英文字母，例如 USD。', code='currency_invalid')
    if probability is None or not 0 <= int(probability) <= 100:
        raise TaskWorkflowError('成交概率必须在 0 到 100 之间。', code='probability_invalid')

    # Keep the row-lock query on the company table itself. ``team`` is nullable,
    # so select_related() would produce an outer join and PostgreSQL rejects
    # FOR UPDATE on the nullable side of that join. Accessing ``team`` below
    # performs a separate read while the company row remains locked.
    locked_company = Company.objects.select_for_update().get(pk=company.pk, site=site)
    if locked_company_authorizer is not None:
        locked_company_authorizer(locked_company)
    if locked_company.owner_user_id is None or locked_company.team_id is None:
        raise TaskWorkflowError('企业必须先领取或分配负责人，才能新建销售机会。', code='company_unassigned')
    _validate_owner(owner_user=owner_user, site=site, team=locked_company.team)

    payload = {
        'company_id': locked_company.pk,
        'owner_user_id': owner_user.pk,
        'name': resolved_name,
        'value_amount': str(value_amount) if value_amount is not None else None,
        'currency': resolved_currency,
        'probability': int(probability),
        'expected_close_date': expected_close_date.isoformat() if expected_close_date else None,
        'product_scope': str(product_scope or '').strip(),
        'capacity_target': str(capacity_target or '').strip(),
        'packaging_format': str(packaging_format or '').strip(),
        'next_step': str(next_step or '').strip(),
        'next_follow_up_at': next_follow_up_at.isoformat() if next_follow_up_at else None,
    }
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        mutation_scope='opportunity.create',
        idempotency_token=idempotency_token,
        fingerprint_payload=payload,
    )
    if replayed:
        return OpportunityMutationResult(
            opportunity=Opportunity.objects.select_related('company', 'owner_user', 'team').get(
                pk=receipt.entity_id
            ),
            replayed=True,
        )

    _set_stage_audit_context(actor, '客户详情手动创建销售机会')
    opportunity = Opportunity.objects.create(
        site=site,
        company=locked_company,
        owner_user=owner_user,
        team=locked_company.team,
        stage='qualification',
        source_channel=locked_company.source_channel or 'manual',
        source_detail='客户详情手动创建',
        **{key: value for key, value in payload.items() if key not in {'company_id', 'owner_user_id'}},
    )
    activity = append_activity(
        site=site,
        actor=actor,
        activity_type='system',
        subject=f'创建销售机会：{opportunity.name}',
        company=locked_company,
        opportunity=opportunity,
        metadata={'event': 'created', 'opportunity_id': opportunity.pk},
    )
    _audit_recorder(audit_recorder)(
        actor=actor,
        action='crm_opportunity_created',
        entity_table='crm_opportunity',
        entity_id=opportunity.pk,
        after={**payload, 'stage': opportunity.stage, 'team_id': opportunity.team_id},
        request=request,
        metadata={'activity_id': activity.pk},
    )
    _finish_receipt(
        receipt,
        entity_table='crm_opportunity',
        entity_id=opportunity.pk,
        result={'opportunity_id': opportunity.pk, 'activity_id': activity.pk},
    )
    return OpportunityMutationResult(opportunity=opportunity)


@transaction.atomic
def create_task_workflow(
    *,
    site,
    actor,
    owner_user,
    title,
    due_at,
    idempotency_token: str,
    submission=None,
    company=None,
    contact=None,
    opportunity=None,
    description: str = '',
    task_type: str = 'follow_up',
    priority: str = 'normal',
    request=None,
    audit_recorder: Callable[..., Any] | None = None,
) -> TaskMutationResult:
    title, description = _validate_task_fields(
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        due_at=due_at,
    )
    scope = validate_crm_relationship_targets(
        site=site,
        submission=submission,
        company=company,
        contact=contact,
        opportunity=opportunity,
    )
    team = next(
        (target.team for target in (submission, company, contact, opportunity) if target is not None and target.team_id == scope.team_id),
        None,
    )
    _validate_owner(owner_user=owner_user, site=site, team=team)
    targets = {'submission': submission, 'company': company, 'contact': contact, 'opportunity': opportunity}
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        mutation_scope='task.create',
        idempotency_token=idempotency_token,
        fingerprint_payload={
            'owner_user_id': owner_user.pk,
            'title': title,
            'description': description,
            'task_type': task_type,
            'priority': priority,
            'due_at': due_at.isoformat(),
            'targets': _target_ids(targets),
        },
    )
    if replayed:
        return TaskMutationResult(
            task=Task.objects.select_related('site', 'owner_user', 'team').get(pk=receipt.entity_id),
            changed=False,
            replayed=True,
        )
    task = create_task(
        site=site,
        owner_user=owner_user,
        team=team,
        created_by_user=actor,
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        due_at=due_at,
        **targets,
    )
    synchronize_submission_next_follow_up(submission_id=task.submission_id)
    activity = append_activity(
        site=site,
        actor=actor,
        activity_type='task',
        subject=f'创建任务：{task.title}',
        metadata={'task_id': task.id, 'event': 'created', 'due_at': task.due_at.isoformat()},
        **targets,
    )
    recorder = _audit_recorder(audit_recorder)
    recorder(
        actor=actor,
        action='crm_task_created',
        entity_table='crm_task',
        entity_id=task.id,
        after=_task_snapshot(task),
        request=request,
        metadata={'activity_id': activity.id},
    )
    _finish_receipt(
        receipt,
        entity_table='crm_task',
        entity_id=task.id,
        result={'task_id': task.id, 'activity_id': activity.id},
    )
    return TaskMutationResult(task=task, changed=True)


def _locked_task(task_id: int) -> Task:
    return (
        Task.objects.select_for_update(of=('self',))
        .select_related(
            'site', 'owner_user', 'team', 'created_by_user',
            'submission', 'company', 'contact', 'opportunity',
        )
        .get(pk=task_id)
    )


def _authorize_locked(task: Task, authorizer):
    if authorizer is not None:
        authorizer(task)


def _require_version(task: Task, expected_updated_at):
    if expected_updated_at is None or task.updated_at != expected_updated_at:
        raise TaskOptimisticLockError(task.updated_at)


@transaction.atomic
def update_task_workflow(
    *,
    task_id: int,
    actor,
    expected_updated_at,
    title,
    description,
    task_type,
    priority,
    due_at,
    owner_user=None,
    locked_task_authorizer=None,
    request=None,
    audit_recorder=None,
) -> TaskMutationResult:
    task = _locked_task(task_id)
    _authorize_locked(task, locked_task_authorizer)
    _require_version(task, expected_updated_at)
    if task.status not in ACTIONABLE_TASK_STATUSES:
        raise TaskWorkflowError('终态任务不能编辑或改期。', code='task_terminal')
    title, description = _validate_task_fields(
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        due_at=due_at,
    )
    resolved_owner = owner_user or task.owner_user
    _validate_owner(owner_user=resolved_owner, site=task.site, team=task.team)
    before = _task_snapshot(task)
    owner_changed = resolved_owner.pk != task.owner_user_id
    task.title = title
    task.description = description
    task.task_type = task_type
    task.priority = priority
    task.due_at = due_at
    task.owner_user = resolved_owner
    task.save(update_fields=['title', 'description', 'task_type', 'priority', 'due_at', 'owner_user', 'updated_at'])
    synchronize_submission_next_follow_up(submission_id=task.submission_id)
    activity = append_activity(
        site=task.site,
        actor=actor,
        activity_type='task',
        subject=('重新分配任务' if owner_changed else '更新任务') + f'：{task.title}',
        metadata={
            'task_id': task.id,
            'event': 'reassigned' if owner_changed else 'updated',
            'previous_owner_user_id': before['owner_user_id'],
            'owner_user_id': task.owner_user_id,
        },
        **_task_targets(task),
    )
    recorder = _audit_recorder(audit_recorder)
    recorder(
        actor=actor,
        action='crm_task_reassigned' if owner_changed else 'crm_task_updated',
        entity_table='crm_task',
        entity_id=task.id,
        before=before,
        after=_task_snapshot(task),
        request=request,
        metadata={'activity_id': activity.id},
    )
    return TaskMutationResult(task=task, changed=True)


@transaction.atomic
def reassign_task_workflow(
    *,
    task_id: int,
    actor,
    owner_user,
    expected_updated_at,
    locked_task_authorizer=None,
    request=None,
    audit_recorder=None,
) -> TaskMutationResult:
    """Reassign one actionable task without ever changing its target team."""

    task = _locked_task(task_id)
    _authorize_locked(task, locked_task_authorizer)
    _require_version(task, expected_updated_at)
    if task.status not in ACTIONABLE_TASK_STATUSES:
        raise TaskWorkflowError('终态任务不能重新分配。', code='task_terminal')
    _validate_owner(owner_user=owner_user, site=task.site, team=task.team)
    if task.owner_user_id == owner_user.pk:
        return TaskMutationResult(task=task, changed=False)
    before = _task_snapshot(task)
    task.owner_user = owner_user
    task.save(update_fields=['owner_user', 'updated_at'])
    activity = append_activity(
        site=task.site,
        actor=actor,
        activity_type='task',
        subject=f'重新分配任务：{task.title}',
        metadata={
            'task_id': task.id,
            'event': 'reassigned',
            'previous_owner_user_id': before['owner_user_id'],
            'owner_user_id': task.owner_user_id,
        },
        **_task_targets(task),
    )
    _audit_recorder(audit_recorder)(
        actor=actor,
        action='crm_task_reassigned',
        entity_table='crm_task',
        entity_id=task.id,
        before=before,
        after=_task_snapshot(task),
        request=request,
        metadata={'activity_id': activity.id},
    )
    return TaskMutationResult(task=task, changed=True)


@transaction.atomic
def start_task_workflow(
    *, task_id: int, actor, expected_updated_at, locked_task_authorizer=None,
    request=None, audit_recorder=None,
) -> TaskMutationResult:
    task = _locked_task(task_id)
    _authorize_locked(task, locked_task_authorizer)
    if task.status == 'in_progress':
        return TaskMutationResult(task=task, changed=False)
    if task.status in TERMINAL_TASK_STATUSES:
        raise TaskWorkflowError('已结束任务不能开始。', code='task_terminal')
    _require_version(task, expected_updated_at)
    before = _task_snapshot(task)
    task.status = 'in_progress'
    task.save(update_fields=['status', 'updated_at'])
    activity = append_activity(
        site=task.site,
        actor=actor,
        activity_type='task',
        subject=f'开始任务：{task.title}',
        metadata={'task_id': task.id, 'event': 'started'},
        **_task_targets(task),
    )
    _audit_recorder(audit_recorder)(
        actor=actor, action='crm_task_started', entity_table='crm_task', entity_id=task.id,
        before=before, after=_task_snapshot(task), request=request, metadata={'activity_id': activity.id},
    )
    return TaskMutationResult(task=task, changed=True)


@transaction.atomic
def close_task_workflow(
    *,
    task_id: int,
    actor,
    target_status: str,
    outcome: str,
    expected_updated_at,
    next_task: dict[str, Any] | None = None,
    locked_task_authorizer=None,
    request=None,
    audit_recorder=None,
) -> TaskMutationResult:
    if target_status not in TERMINAL_TASK_STATUSES:
        raise TaskWorkflowError('任务终态无效。', code='task_status_invalid')
    resolved_outcome = _clean_text(
        outcome,
        field='完成结果' if target_status == 'completed' else '取消原因',
        maximum=2000,
        required=True,
    )
    task = _locked_task(task_id)
    _authorize_locked(task, locked_task_authorizer)
    if task.status == target_status:
        return TaskMutationResult(task=task, changed=False)
    if task.status in TERMINAL_TASK_STATUSES:
        raise TaskWorkflowError('任务已经处于另一终态，不能改写历史结果。', code='task_terminal_conflict')
    _require_version(task, expected_updated_at)
    if target_status == 'canceled' and next_task:
        raise TaskWorkflowError('取消任务时不能同时创建下一任务。', code='next_task_not_allowed')
    before = _task_snapshot(task)
    now = timezone.now()
    task.status = target_status
    task.completed_at = now
    task.outcome = resolved_outcome
    task.save(update_fields=['status', 'completed_at', 'outcome', 'updated_at'])
    created_next_task = None
    if next_task:
        next_title, next_description = _validate_task_fields(
            title=next_task.get('title'),
            description=next_task.get('description', ''),
            task_type=next_task.get('task_type', 'follow_up'),
            priority=next_task.get('priority', 'normal'),
            due_at=next_task.get('due_at'),
        )
        next_owner = next_task.get('owner_user') or task.owner_user
        _validate_owner(owner_user=next_owner, site=task.site, team=task.team)
        created_next_task = create_task(
            site=task.site,
            owner_user=next_owner,
            team=task.team,
            created_by_user=actor,
            title=next_title,
            description=next_description,
            task_type=next_task.get('task_type', 'follow_up'),
            priority=next_task.get('priority', 'normal'),
            due_at=next_task['due_at'],
            **_task_targets(task),
        )
        append_activity(
            site=task.site,
            actor=actor,
            activity_type='task',
            subject=f'创建下一任务：{created_next_task.title}',
            metadata={
                'task_id': created_next_task.id,
                'event': 'created',
                'previous_task_id': task.id,
            },
            **_task_targets(task),
        )
    synchronize_submission_next_follow_up(submission_id=task.submission_id)
    action_label = '完成' if target_status == 'completed' else '取消'
    activity = append_activity(
        site=task.site,
        actor=actor,
        activity_type='task',
        subject=f'{action_label}任务：{task.title}',
        body=resolved_outcome,
        metadata={
            'task_id': task.id,
            'event': target_status,
            'next_task_id': getattr(created_next_task, 'id', None),
        },
        **_task_targets(task),
    )
    _audit_recorder(audit_recorder)(
        actor=actor,
        action='crm_task_completed' if target_status == 'completed' else 'crm_task_canceled',
        entity_table='crm_task',
        entity_id=task.id,
        before=before,
        after=_task_snapshot(task),
        request=request,
        metadata={
            'activity_id': activity.id,
            'outcome_sha256': _outcome_hash(resolved_outcome),
            'next_task_id': getattr(created_next_task, 'id', None),
        },
    )
    return TaskMutationResult(task=task, changed=True, next_task=created_next_task)


@transaction.atomic
def append_manual_activity_workflow(
    *,
    site,
    actor,
    activity_type: str,
    direction: str,
    subject: str,
    body: str,
    occurred_at,
    idempotency_token: str,
    submission=None,
    company=None,
    contact=None,
    opportunity=None,
    metadata: dict[str, Any] | None = None,
    request=None,
    audit_recorder=None,
    mutation_scope: str = 'activity.create',
) -> ActivityMutationResult:
    if activity_type not in MANUAL_ACTIVITY_TYPES:
        raise TaskWorkflowError('只能记录人工沟通活动。', code='activity_type_invalid')
    if direction not in ACTIVITY_DIRECTIONS:
        raise TaskWorkflowError('活动方向无效。', code='activity_direction_invalid')
    subject = _clean_text(subject, field='活动主题', maximum=240, required=True)
    body = _clean_text(body, field='活动内容', maximum=8000, required=True)
    if occurred_at is None or timezone.is_naive(occurred_at):
        raise TaskWorkflowError('发生时间无效。', code='occurred_at_invalid')
    if occurred_at > timezone.now() + timedelta(minutes=5):
        raise TaskWorkflowError('发生时间不能明显晚于当前时间。', code='occurred_at_future')
    targets = {'submission': submission, 'company': company, 'contact': contact, 'opportunity': opportunity}
    validate_crm_relationship_targets(site=site, **targets)
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        mutation_scope=mutation_scope,
        idempotency_token=idempotency_token,
        fingerprint_payload={
            'activity_type': activity_type,
            'direction': direction,
            'subject': subject,
            'body': body,
            'occurred_at': occurred_at.isoformat(),
            'targets': _target_ids(targets),
            'metadata': metadata or {},
        },
    )
    if replayed:
        return ActivityMutationResult(activity=Activity.objects.get(pk=receipt.entity_id), replayed=True)
    activity = append_activity(
        site=site,
        actor=actor,
        activity_type=activity_type,
        direction=direction,
        subject=subject,
        body=body,
        occurred_at=occurred_at,
        metadata=metadata or {},
        **targets,
    )
    _audit_recorder(audit_recorder)(
        actor=actor,
        action='crm_activity_correction_added' if mutation_scope == 'activity.correction' else 'crm_activity_created',
        entity_table='crm_activity',
        entity_id=activity.id,
        after={
            'activity_type': activity.activity_type,
            'direction': activity.direction,
            'occurred_at': activity.occurred_at.isoformat(),
            **{f'{name}_id': value for name, value in _target_ids(targets).items()},
        },
        request=request,
        metadata={'body_sha256': _outcome_hash(body), **(metadata or {})},
    )
    _finish_receipt(
        receipt,
        entity_table='crm_activity',
        entity_id=activity.id,
        result={'activity_id': activity.id},
    )
    return ActivityMutationResult(activity=activity)
