from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any, Callable, Mapping

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .crm_services import append_activity, create_task
from .models import LeadEventOutbox, LeadSubmission, SalesTeam, SalesTeamMember, Task
from .services import dispatch_outbox_event, queue_submission_event


logger = logging.getLogger(__name__)

LEAD_STAGES = frozenset({'new', 'contacted', 'qualified', 'won', 'lost', 'spam'})
ACTIVITY_TYPES = frozenset({'note', 'call', 'email', 'whatsapp', 'meeting', 'site_visit'})
ACTIVITY_DIRECTIONS = frozenset({'outbound', 'inbound', 'internal'})
TASK_TYPES = frozenset({'follow_up', 'call', 'email', 'whatsapp', 'meeting', 'quote', 'review', 'other'})
TASK_PRIORITIES = frozenset({'low', 'normal', 'high', 'urgent'})
ACTIONABLE_TASK_STATUSES = ('open', 'in_progress')

# Domain limits intentionally mirror the existing Django form contracts.  The
# workflow service is also called by JSON/HTTP endpoints, so forms cannot be the
# only place that protects PostgreSQL and the audit/activity tables from
# unbounded crafted values.
ACTIVITY_SUBJECT_MAX_LENGTH = 240
TASK_TITLE_MAX_LENGTH = 240
FOLLOW_UP_NOTES_MAX_LENGTH = 8000
QUALIFICATION_NOTES_MAX_LENGTH = 4000
REQUIREMENT_VALUE_MAX_LENGTH = 500
REQUIREMENT_FIELDS = frozenset(
    {
        'country',
        'company',
        'product_category',
        'capacity',
        'packaging_format',
        'project_type',
        'purchase_timeline',
        'contact_role',
    }
)

IDEMPOTENCY_NONE = 'none'
IDEMPOTENCY_DATABASE_ONLY = 'database_write_deduplication_only'
_IDEMPOTENCY_CONFIG_KEY = '_lead_workflow_v2'
_IDEMPOTENCY_LEDGER_LIMIT = 20
_TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9._:-]{8,128}$')
_CURRENCY_PATTERN = re.compile(r'^[A-Z]{3,8}$')


class _Unchanged:
    def __repr__(self) -> str:
        return 'UNCHANGED'


UNCHANGED = _Unchanged()


class LeadWorkflowError(ValidationError):
    """Base class for domain failures that must roll back the disposition."""


class LeadOptimisticLockError(LeadWorkflowError):
    def __init__(self, current_updated_at: datetime | None):
        self.current_updated_at = current_updated_at
        super().__init__(
            '这条线索已被其他人修改，请重新载入最新内容后再提交。',
            code='lead_concurrent_update',
            params={
                'current_updated_at': (
                    current_updated_at.isoformat() if current_updated_at else ''
                ),
            },
        )


class LeadIdempotencyConflictError(LeadWorkflowError):
    def __init__(self):
        super().__init__(
            '该幂等令牌已经用于不同的线索处置内容，请生成新令牌后重试。',
            code='lead_idempotency_conflict',
        )


@dataclass(frozen=True)
class QualificationInput:
    contactable: bool = False
    company_verified: bool = False
    project_confirmed: bool = False
    technical_fit: bool = False
    next_step_confirmed: bool = False
    overridden: bool = False
    notes: str = ''

    @classmethod
    def from_value(cls, value: QualificationInput | Mapping[str, Any]) -> QualificationInput:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise LeadWorkflowError('合格判定数据无效。', code='qualification_invalid')
        return cls(
            contactable=bool(value.get('contactable')),
            company_verified=bool(value.get('company_verified')),
            project_confirmed=bool(value.get('project_confirmed')),
            technical_fit=bool(value.get('technical_fit')),
            next_step_confirmed=bool(value.get('next_step_confirmed')),
            overridden=bool(value.get('overridden')),
            notes=str(value.get('notes') or '').strip(),
        )

    @property
    def score(self) -> int:
        return 20 * sum(
            (
                self.contactable,
                self.company_verified,
                self.project_confirmed,
                self.technical_fit,
                self.next_step_confirmed,
            )
        )

    def as_json(self) -> dict[str, bool]:
        return {
            'contactable': self.contactable,
            'company_verified': self.company_verified,
            'project_confirmed': self.project_confirmed,
            'technical_fit': self.technical_fit,
            'next_step_confirmed': self.next_step_confirmed,
        }


@dataclass(frozen=True)
class ActivityInput:
    activity_type: str
    direction: str
    subject: str
    body: str
    occurred_at: datetime | None = None
    metadata: Mapping[str, Any] | None = None

    @classmethod
    def from_value(cls, value: ActivityInput | Mapping[str, Any]) -> ActivityInput:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise LeadWorkflowError('沟通记录数据无效。', code='activity_invalid')
        return cls(
            activity_type=str(value.get('activity_type') or '').strip(),
            direction=str(value.get('direction') or '').strip(),
            subject=str(value.get('subject') or '').strip(),
            body=str(value.get('body') or '').strip(),
            occurred_at=value.get('occurred_at'),
            metadata=value.get('metadata'),
        )


@dataclass(frozen=True)
class NextTaskInput:
    title: str
    due_at: datetime
    task_type: str = 'follow_up'
    priority: str = 'normal'
    owner_user_id: int | None = None
    reminder_at: datetime | None = None
    description: str = ''

    @classmethod
    def from_value(cls, value: NextTaskInput | Mapping[str, Any] | None) -> NextTaskInput | None:
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise LeadWorkflowError('下一任务数据无效。', code='task_invalid')
        return cls(
            title=str(value.get('title') or '').strip(),
            due_at=value.get('due_at'),
            task_type=str(value.get('task_type') or 'follow_up').strip(),
            priority=str(value.get('priority') or 'normal').strip(),
            owner_user_id=value.get('owner_user_id'),
            reminder_at=value.get('reminder_at'),
            description=str(value.get('description') or '').strip(),
        )


@dataclass(frozen=True)
class LeadDispositionResult:
    submission: LeadSubmission
    activity_ids: tuple[int, ...]
    task_id: int | None
    outbox_ids: tuple[int, ...]
    dispatch_scheduled: int
    replayed: bool
    idempotency_mode: str


@dataclass(frozen=True)
class LeadAssignmentResult:
    """The committed result of one dedicated owner-assignment operation."""

    submission: LeadSubmission
    changed: bool
    activity_id: int | None
    audit_id: int | None


def _coerce_datetime(value: Any, *, field_name: str) -> datetime:
    resolved = parse_datetime(value) if isinstance(value, str) else value
    if not isinstance(resolved, datetime):
        raise LeadWorkflowError(
            f'{field_name}不是有效时间。',
            code='datetime_invalid',
            params={'field': field_name},
        )
    if timezone.is_naive(resolved):
        resolved = timezone.make_aware(resolved, timezone.get_current_timezone())
    return resolved


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return (
        _coerce_datetime(left, field_name='expected_updated_at').astimezone(datetime_timezone.utc)
        == _coerce_datetime(right, field_name='updated_at').astimezone(datetime_timezone.utc)
    )


def _json_value(value: Any) -> Any:
    if value is UNCHANGED:
        return '__unchanged__'
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return _coerce_datetime(value, field_name='datetime').isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _token_hash(token: str) -> str:
    resolved = (token or '').strip()
    if not resolved:
        return ''
    if not _TOKEN_PATTERN.fullmatch(resolved):
        raise LeadWorkflowError(
            '幂等令牌必须为 8–128 位，仅包含字母、数字、点、下划线、冒号或短横线。',
            code='idempotency_token_invalid',
        )
    return hashlib.sha256(resolved.encode('utf-8')).hexdigest()


def _request_fingerprint(
    *,
    stage: str,
    qualification: QualificationInput,
    activity: ActivityInput,
    next_task: NextTaskInput | None,
    assignee_id: Any,
    team_id: Any,
    follow_up_notes: Any,
    buyer_value: Any,
    buyer_currency: Any,
    requirement_updates: Mapping[str, str],
) -> str:
    payload = {
        'stage': stage,
        'qualification': asdict(qualification),
        'activity': asdict(activity),
        'next_task': asdict(next_task) if next_task else None,
        'assignee_id': assignee_id,
        'team_id': team_id,
        'follow_up_notes': follow_up_notes,
        'buyer_value': buyer_value,
        'buyer_currency': buyer_currency,
        'requirement_updates': dict(requirement_updates),
    }
    encoded = json.dumps(
        _json_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _workflow_config(submission: LeadSubmission) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = dict(submission.config_json or {})
    namespace = config.get(_IDEMPOTENCY_CONFIG_KEY)
    if not isinstance(namespace, dict):
        namespace = {}
    entries = namespace.get('idempotency')
    if not isinstance(entries, list):
        entries = []
    return config, [entry for entry in entries if isinstance(entry, dict)]


def _replayed_result(
    submission: LeadSubmission,
    *,
    token_hash: str,
    request_hash: str,
    actor_id: int,
) -> LeadDispositionResult | None:
    if not token_hash:
        return None
    _, entries = _workflow_config(submission)
    entry = next(
        (item for item in reversed(entries) if item.get('token_hash') == token_hash),
        None,
    )
    if entry is None:
        return None
    if entry.get('request_hash') != request_hash or int(entry.get('actor_id') or 0) != actor_id:
        raise LeadIdempotencyConflictError()
    return LeadDispositionResult(
        submission=submission,
        activity_ids=tuple(int(value) for value in entry.get('activity_ids') or ()),
        task_id=int(entry['task_id']) if entry.get('task_id') else None,
        outbox_ids=tuple(int(value) for value in entry.get('outbox_ids') or ()),
        dispatch_scheduled=0,
        replayed=True,
        idempotency_mode=IDEMPOTENCY_DATABASE_ONLY,
    )


def _store_idempotency_result(
    submission: LeadSubmission,
    *,
    token_hash: str,
    request_hash: str,
    actor_id: int,
    activity_ids: tuple[int, ...],
    task_id: int | None,
    outbox_ids: tuple[int, ...],
    completed_at: datetime,
) -> None:
    if not token_hash:
        return
    config, entries = _workflow_config(submission)
    namespace = config.get(_IDEMPOTENCY_CONFIG_KEY)
    if not isinstance(namespace, dict):
        namespace = {}
    entries.append(
        {
            'token_hash': token_hash,
            'request_hash': request_hash,
            'actor_id': actor_id,
            'completed_at': completed_at.isoformat(),
            'activity_ids': list(activity_ids),
            'task_id': task_id,
            'outbox_ids': list(outbox_ids),
        }
    )
    namespace['idempotency'] = entries[-_IDEMPOTENCY_LEDGER_LIMIT:]
    # This is deliberately described as database-only de-duplication. It is not
    # a durable HTTP response cache and cannot guarantee exactly-once delivery
    # to an external marketing platform.
    namespace['guarantee'] = IDEMPOTENCY_DATABASE_ONLY
    config[_IDEMPOTENCY_CONFIG_KEY] = namespace
    submission.config_json = config
    submission.updated_at = completed_at
    submission.save(update_fields=['config_json', 'updated_at'])


def _lead_snapshot(submission: LeadSubmission) -> dict[str, Any]:
    payload = dict(getattr(submission, 'payload_json', None) or {})
    return {
        'stage': submission.stage,
        'team_id': submission.team_id,
        'assignee_id': submission.assignee_id,
        'source_channel': submission.source_channel,
        'source_detail': submission.source_detail,
        'next_follow_up_at': _json_value(submission.next_follow_up_at),
        'follow_up_notes': submission.follow_up_notes,
        'qualification_score': submission.qualification_score,
        'qualification_json': submission.qualification_json or {},
        'qualification_notes': submission.qualification_notes,
        'qualification_overridden': submission.qualification_overridden,
        'buyer_value': _json_value(submission.buyer_value),
        'buyer_currency': submission.buyer_currency,
        'country': getattr(submission, 'country', ''),
        'company': getattr(submission, 'company', ''),
        'requirements': {
            key: payload.get(key, '')
            for key in sorted(REQUIREMENT_FIELDS - {'country', 'company'})
        },
        'contacted_at': _json_value(submission.contacted_at),
        'qualified_at': _json_value(submission.qualified_at),
        'won_at': _json_value(submission.won_at),
        'lost_at': _json_value(submission.lost_at),
        'stage_updated_at': _json_value(submission.stage_updated_at),
        'updated_at': _json_value(submission.updated_at),
    }


def _lock_submission(submission_id: int) -> LeadSubmission:
    return (
        LeadSubmission.objects.select_for_update(of=('self',))
        .select_related('form', 'site', 'assignee', 'team')
        .get(pk=submission_id)
    )


def _active_user(user_id: int):
    try:
        return get_user_model().objects.get(pk=user_id, is_active=True)
    except get_user_model().DoesNotExist as exc:
        raise LeadWorkflowError('负责人不存在或已停用。', code='assignee_unavailable') from exc


def _team_for_submission(submission: LeadSubmission, team_id: int) -> SalesTeam:
    try:
        return SalesTeam.objects.get(pk=team_id, site_id=submission.site_id, enabled=True)
    except SalesTeam.DoesNotExist as exc:
        raise LeadWorkflowError('销售团队不存在、已停用或不属于当前站点。', code='team_unavailable') from exc


def _resolve_assignment(
    submission: LeadSubmission,
    *,
    assignee_id: int | None | _Unchanged,
    team_id: int | None | _Unchanged,
):
    # Treat an explicitly re-posted current value as unchanged *after* the row
    # lock is acquired.  This preserves inactive historical owners and avoids
    # basing assignment semantics on the stale pre-lock object from the view.
    if assignee_id is not UNCHANGED and assignee_id == submission.assignee_id:
        assignee_id = UNCHANGED
    if team_id is not UNCHANGED and team_id == submission.team_id:
        team_id = UNCHANGED
    if assignee_id is UNCHANGED and team_id is UNCHANGED:
        return submission.assignee, submission.team

    assignee = submission.assignee if assignee_id is UNCHANGED else (
        _active_user(int(assignee_id)) if assignee_id is not None else None
    )
    team = submission.team if team_id is UNCHANGED else (
        _team_for_submission(submission, int(team_id)) if team_id is not None else None
    )

    # When a new assignee is posted against an existing team, validate the
    # inherited team too.  Unchanged legacy assignments remain readable, but a
    # disabled or cross-site team cannot be used for a new assignment.
    if team is not None and team_id is UNCHANGED:
        team = _team_for_submission(submission, team.pk)

    if assignee is None:
        return assignee, team

    if team is None:
        memberships = list(
            SalesTeamMember.objects.filter(
                user_id=assignee.pk,
                team__site_id=submission.site_id,
                team__enabled=True,
            )
            .values_list('team_id', flat=True)
            .distinct()[:2]
        )
        if len(memberships) != 1:
            raise LeadWorkflowError(
                '负责人没有唯一可确定的销售团队，请同时选择团队。',
                code='team_required',
            )
        team = _team_for_submission(submission, memberships[0])

    if not SalesTeamMember.objects.filter(team_id=team.pk, user_id=assignee.pk).exists():
        raise LeadWorkflowError(
            '负责人不属于所选销售团队。',
            code='assignee_team_mismatch',
        )
    return assignee, team


def assign_lead_owner(
    *,
    submission_id: int,
    actor,
    assignee_id: int,
    request=None,
    locked_submission_authorizer: Callable[[LeadSubmission], Any] | None = None,
    locked_assignee_authorizer: Callable[[LeadSubmission, int], Any] | None = None,
    activity_appender: Callable[..., Any] | None = None,
    audit_recorder: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> LeadAssignmentResult:
    """Assign exactly one lead without fabricating a communication activity.

    The row is locked before both authorization callbacks and the team
    resolution run.  Callers can therefore perform a cheap scoped preflight
    for a batch and still re-authorize the current record state here.  Each
    invocation owns its transaction deliberately, which lets the HTTP batch
    endpoint report real partial success instead of rolling back unrelated
    records.
    """

    actor_id = int(getattr(actor, 'pk', 0) or 0)
    if not actor_id or not getattr(actor, 'is_authenticated', True):
        raise LeadWorkflowError('负责人分配必须有已登录的操作者。', code='actor_required')
    try:
        resolved_assignee_id = int(assignee_id)
    except (TypeError, ValueError) as exc:
        raise LeadWorkflowError('负责人无效。', code='assignee_invalid') from exc
    if resolved_assignee_id <= 0:
        raise LeadWorkflowError('负责人无效。', code='assignee_invalid')

    activity_appender = activity_appender or append_activity
    clock = clock or timezone.now
    if audit_recorder is None:
        from console.audit import record_audit

        audit_recorder = record_audit

    with transaction.atomic():
        submission = _lock_submission(submission_id)
        if locked_submission_authorizer is not None:
            locked_submission_authorizer(submission)
        if locked_assignee_authorizer is not None:
            locked_assignee_authorizer(submission, resolved_assignee_id)

        before_assignee_id = submission.assignee_id
        before_team_id = submission.team_id
        assignee, team = _resolve_assignment(
            submission,
            assignee_id=resolved_assignee_id,
            team_id=UNCHANGED,
        )
        resolved_team_id = team.pk if team else None
        if submission.assignee_id == assignee.pk and submission.team_id == resolved_team_id:
            return LeadAssignmentResult(
                submission=submission,
                changed=False,
                activity_id=None,
                audit_id=None,
            )
        now = _coerce_datetime(clock(), field_name='当前时间')
        submission.assignee = assignee
        submission.team = team
        submission.updated_at = now
        submission.save(update_fields=['assignee', 'team', 'updated_at'])

        assignee_label = assignee.get_full_name().strip() or assignee.get_username()
        activity = activity_appender(
            site=submission.site,
            actor=actor,
            activity_type='assignment',
            direction='internal',
            subject=f'负责人调整为 {assignee_label}',
            body='',
            submission=submission,
            metadata={
                'workflow': 'lead_assignment_v2',
                'from_assignee_id': before_assignee_id,
                'to_assignee_id': assignee.pk,
                'from_team_id': before_team_id,
                'to_team_id': team.pk if team else None,
            },
            occurred_at=now,
        )
        audit = audit_recorder(
            actor=actor,
            action='lead_assignment_v2',
            entity_table='lead_submission',
            entity_id=submission.pk,
            before={
                'assignee_id': before_assignee_id,
                'team_id': before_team_id,
            },
            after={
                'assignee_id': assignee.pk,
                'team_id': resolved_team_id,
                'updated_at': submission.updated_at,
            },
            request=request,
        )
        return LeadAssignmentResult(
            submission=submission,
            changed=True,
            activity_id=int(activity.pk),
            audit_id=int(audit.pk),
        )


def _validate_qualification(stage: str, qualification: QualificationInput) -> None:
    if len(qualification.notes) > QUALIFICATION_NOTES_MAX_LENGTH:
        raise LeadWorkflowError(
            f'合格判定说明不能超过 {QUALIFICATION_NOTES_MAX_LENGTH} 个字符。',
            code='qualification_notes_too_long',
        )
    if qualification.overridden and not qualification.notes:
        raise LeadWorkflowError(
            '人工覆盖评分时必须填写原因。',
            code='qualification_override_reason_required',
        )
    if stage in {'qualified', 'won'} and qualification.score < 80 and not qualification.overridden:
        raise LeadWorkflowError(
            '标记为高质量或成交前，必须满足五项合格判定中的至少四项；特殊情况需人工覆盖并说明原因。',
            code='qualification_required',
        )


def _validate_activity(activity: ActivityInput) -> ActivityInput:
    if activity.activity_type not in ACTIVITY_TYPES:
        raise LeadWorkflowError('活动类型无效。', code='activity_type_invalid')
    if activity.direction not in ACTIVITY_DIRECTIONS:
        raise LeadWorkflowError('沟通方向无效。', code='activity_direction_invalid')
    if not activity.subject.strip():
        raise LeadWorkflowError('沟通主题不能为空。', code='activity_subject_required')
    if len(activity.subject.strip()) > ACTIVITY_SUBJECT_MAX_LENGTH:
        raise LeadWorkflowError(
            f'沟通主题不能超过 {ACTIVITY_SUBJECT_MAX_LENGTH} 个字符。',
            code='activity_subject_too_long',
        )
    if not activity.body.strip():
        raise LeadWorkflowError('沟通记录不能为空。', code='activity_body_required')
    if activity.occurred_at is not None:
        _coerce_datetime(activity.occurred_at, field_name='发生时间')
    if activity.metadata is not None and not isinstance(activity.metadata, Mapping):
        raise LeadWorkflowError('活动附加信息必须是对象。', code='activity_metadata_invalid')
    return activity


def _validate_task(task: NextTaskInput | None) -> NextTaskInput | None:
    if task is None:
        return None
    if not task.title.strip():
        raise LeadWorkflowError('下一任务标题不能为空。', code='task_title_required')
    if len(task.title.strip()) > TASK_TITLE_MAX_LENGTH:
        raise LeadWorkflowError(
            f'下一任务标题不能超过 {TASK_TITLE_MAX_LENGTH} 个字符。',
            code='task_title_too_long',
        )
    if task.task_type not in TASK_TYPES:
        raise LeadWorkflowError('下一任务类型无效。', code='task_type_invalid')
    if task.priority not in TASK_PRIORITIES:
        raise LeadWorkflowError('下一任务优先级无效。', code='task_priority_invalid')
    due_at = _coerce_datetime(task.due_at, field_name='到期时间')
    reminder_at = (
        _coerce_datetime(task.reminder_at, field_name='提醒时间')
        if task.reminder_at is not None
        else None
    )
    if reminder_at and reminder_at > due_at:
        raise LeadWorkflowError('提醒时间不能晚于到期时间。', code='task_reminder_invalid')
    return NextTaskInput(
        title=task.title.strip(),
        due_at=due_at,
        task_type=task.task_type,
        priority=task.priority,
        owner_user_id=task.owner_user_id,
        reminder_at=reminder_at,
        description=task.description.strip(),
    )


def _validate_buyer_value(value: Decimal | None | _Unchanged):
    if value is UNCHANGED or value is None:
        return value
    try:
        resolved = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LeadWorkflowError('预计金额无效。', code='buyer_value_invalid') from exc
    if not resolved.is_finite():
        raise LeadWorkflowError('预计金额必须是有限数值。', code='buyer_value_not_finite')
    if resolved < 0:
        raise LeadWorkflowError('预计金额不能为负数。', code='buyer_value_negative')
    field = LeadSubmission._meta.get_field('buyer_value')
    try:
        field.run_validators(resolved)
    except ValidationError as exc:
        raise LeadWorkflowError(
            '预计金额最多 12 位数字，且最多保留 2 位小数。',
            code='buyer_value_precision_invalid',
        ) from exc
    return resolved


def _validate_buyer_currency(value: str | _Unchanged):
    if value is UNCHANGED:
        return value
    resolved = str(value or 'USD').strip().upper() or 'USD'
    if not _CURRENCY_PATTERN.fullmatch(resolved):
        raise LeadWorkflowError(
            '币种必须为 3–8 位大写英文字母。',
            code='buyer_currency_invalid',
        )
    return resolved


def _validate_requirement_updates(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise LeadWorkflowError('项目需求字段无效。', code='requirements_invalid')
    unknown = sorted(set(str(key) for key in value) - REQUIREMENT_FIELDS)
    if unknown:
        raise LeadWorkflowError(
            f'不支持的项目需求字段：{", ".join(unknown)}。',
            code='requirements_unknown_fields',
        )
    result: dict[str, str] = {}
    for key, raw in value.items():
        text = str(raw or '').strip()
        if len(text) > REQUIREMENT_VALUE_MAX_LENGTH:
            raise LeadWorkflowError(
                f'{key} 不能超过 {REQUIREMENT_VALUE_MAX_LENGTH} 个字符。',
                code='requirement_value_too_long',
            )
        result[str(key)] = text
    return result


def _validate_follow_up_notes(value: str | _Unchanged):
    if value is UNCHANGED:
        return value
    resolved = str(value or '').strip()
    if len(resolved) > FOLLOW_UP_NOTES_MAX_LENGTH:
        raise LeadWorkflowError(
            f'跟进备注不能超过 {FOLLOW_UP_NOTES_MAX_LENGTH} 个字符。',
            code='follow_up_notes_too_long',
        )
    return resolved


def _stage_event_specs(
    submission: LeadSubmission,
    *,
    previous_stage: str,
    contacted_was_missing: bool,
    qualified_was_missing: bool,
) -> list[tuple[str, str]]:
    if previous_stage == submission.stage or submission.stage not in {'contacted', 'qualified', 'won'}:
        return []
    specs: list[tuple[str, str]] = []
    if submission.stage == 'contacted' or contacted_was_missing:
        specs.append(('contacted', submission.form.contacted_event_name or 'contacted_lead'))
    if submission.stage == 'qualified' or (submission.stage == 'won' and qualified_was_missing):
        specs.append(('qualified', submission.form.qualified_event_name or 'qualified_lead'))
    if submission.stage == 'won':
        specs.append(('won', submission.form.won_event_name or 'converted'))
    return specs


def _model_id(value: Any) -> int | None:
    identifier = getattr(value, 'pk', None) or getattr(value, 'id', None)
    return int(identifier) if identifier is not None else None


def _dispatch_after_commit(outbox: LeadEventOutbox, dispatcher: Callable[[LeadEventOutbox], bool]) -> None:
    try:
        dispatcher(outbox)
    except Exception:  # pragma: no cover - robust on_commit also logs callback failures
        logger.exception('线索处置提交后派发 outbox 失败：outbox_id=%s', _model_id(outbox))


def dispose_lead(
    *,
    submission_id: int,
    actor,
    expected_updated_at: datetime | str,
    stage: str,
    qualification: QualificationInput | Mapping[str, Any],
    activity: ActivityInput | Mapping[str, Any],
    next_task: NextTaskInput | Mapping[str, Any] | None = None,
    assignee_id: int | None | _Unchanged = UNCHANGED,
    team_id: int | None | _Unchanged = UNCHANGED,
    follow_up_notes: str | _Unchanged = UNCHANGED,
    buyer_value: Decimal | None | _Unchanged = UNCHANGED,
    buyer_currency: str | _Unchanged = UNCHANGED,
    requirement_updates: Mapping[str, Any] | None = None,
    idempotency_token: str = '',
    request=None,
    outbox_dispatcher: Callable[[LeadEventOutbox], bool] | None = None,
    activity_appender: Callable[..., Any] | None = None,
    task_creator: Callable[..., Any] | None = None,
    outbox_queuer: Callable[..., list[LeadEventOutbox]] | None = None,
    audit_recorder: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] | None = None,
    locked_submission_authorizer: Callable[[LeadSubmission], Any] | None = None,
) -> LeadDispositionResult:
    """Atomically save one complete lead-disposition decision.

    All database changes are made under a row lock and one transaction. External
    outbox delivery is only attempted from ``transaction.on_commit``. A supplied
    idempotency token prevents duplicate database writes while its bounded ledger
    entry remains in ``LeadSubmission.config_json``; it is intentionally *not*
    advertised as strong end-to-end exactly-once idempotency.
    """

    actor_id = int(getattr(actor, 'pk', 0) or 0)
    if not actor_id or not getattr(actor, 'is_authenticated', True):
        raise LeadWorkflowError('线索处置必须有已登录的操作者。', code='actor_required')

    resolved_stage = (stage or '').strip()
    if resolved_stage not in LEAD_STAGES:
        raise LeadWorkflowError('线索阶段无效。', code='stage_invalid')
    resolved_qualification = QualificationInput.from_value(qualification)
    resolved_activity = _validate_activity(ActivityInput.from_value(activity))
    resolved_task = _validate_task(NextTaskInput.from_value(next_task))
    resolved_follow_up_notes = _validate_follow_up_notes(follow_up_notes)
    resolved_buyer_value = _validate_buyer_value(buyer_value)
    resolved_buyer_currency = _validate_buyer_currency(buyer_currency)
    resolved_requirement_updates = _validate_requirement_updates(requirement_updates)
    _validate_qualification(resolved_stage, resolved_qualification)
    expected = _coerce_datetime(expected_updated_at, field_name='expected_updated_at')
    resolved_token_hash = _token_hash(idempotency_token)
    request_hash = _request_fingerprint(
        stage=resolved_stage,
        qualification=resolved_qualification,
        activity=resolved_activity,
        next_task=resolved_task,
        assignee_id=assignee_id,
        team_id=team_id,
        follow_up_notes=resolved_follow_up_notes,
        buyer_value=resolved_buyer_value,
        buyer_currency=resolved_buyer_currency,
        requirement_updates=resolved_requirement_updates,
    )

    activity_appender = activity_appender or append_activity
    task_creator = task_creator or create_task
    outbox_queuer = outbox_queuer or queue_submission_event
    outbox_dispatcher = outbox_dispatcher or dispatch_outbox_event
    clock = clock or timezone.now
    if audit_recorder is None:
        from console.audit import record_audit

        audit_recorder = record_audit

    with transaction.atomic():
        submission = _lock_submission(submission_id)
        if locked_submission_authorizer is not None:
            locked_submission_authorizer(submission)
        replay = _replayed_result(
            submission,
            token_hash=resolved_token_hash,
            request_hash=request_hash,
            actor_id=actor_id,
        )
        if replay is not None:
            return replay
        if not _same_instant(expected, submission.updated_at):
            raise LeadOptimisticLockError(submission.updated_at)

        resolved_assignee, resolved_team = _resolve_assignment(
            submission,
            assignee_id=assignee_id,
            team_id=team_id,
        )
        task_owner = None
        if resolved_task is not None:
            if resolved_task.owner_user_id is not None:
                try:
                    requested_task_owner_id = int(resolved_task.owner_user_id)
                except (TypeError, ValueError) as exc:
                    raise LeadWorkflowError(
                        '下一任务负责人无效。',
                        code='task_owner_invalid',
                    ) from exc
                if (
                    resolved_assignee is not None
                    and requested_task_owner_id == resolved_assignee.pk
                    and not getattr(resolved_assignee, 'is_active', False)
                ):
                    raise LeadWorkflowError(
                        '当前负责人已停用，请先重新分配负责人再创建任务。',
                        code='task_owner_inactive_requires_reassignment',
                    )
                task_owner = _active_user(requested_task_owner_id)
            elif resolved_assignee is not None:
                if not getattr(resolved_assignee, 'is_active', False):
                    raise LeadWorkflowError(
                        '当前负责人已停用，请先重新分配负责人再创建任务。',
                        code='task_owner_inactive_requires_reassignment',
                    )
                task_owner = resolved_assignee
            else:
                if not getattr(actor, 'is_active', True):
                    raise LeadWorkflowError(
                        '任务负责人不可用，请先分配有效负责人。',
                        code='task_owner_unavailable',
                    )
                task_owner = actor
            if resolved_team is not None and not SalesTeamMember.objects.filter(
                team_id=resolved_team.pk,
                user_id=task_owner.pk,
            ).exists():
                raise LeadWorkflowError(
                    '下一任务负责人不属于所选销售团队。',
                    code='task_owner_team_mismatch',
                )
        now = _coerce_datetime(clock(), field_name='当前时间')
        before = _lead_snapshot(submission)
        previous_stage = submission.stage
        previous_assignee_id = submission.assignee_id
        previous_team_id = submission.team_id
        resolved_assignee_id = _model_id(resolved_assignee)
        resolved_team_id = _model_id(resolved_team)
        contacted_was_missing = submission.contacted_at is None
        qualified_was_missing = submission.qualified_at is None

        submission.assignee = resolved_assignee
        submission.team = resolved_team
        submission.stage = resolved_stage
        if previous_stage != resolved_stage:
            submission.stage_updated_at = now
        if resolved_stage in {'contacted', 'qualified', 'won'} and submission.contacted_at is None:
            submission.contacted_at = now
        if resolved_stage in {'qualified', 'won'} and submission.qualified_at is None:
            submission.qualified_at = now
        if resolved_stage == 'won' and submission.won_at is None:
            submission.won_at = now
        if resolved_stage == 'lost' and submission.lost_at is None:
            submission.lost_at = now
        submission.qualification_score = resolved_qualification.score
        submission.qualification_json = resolved_qualification.as_json()
        submission.qualification_notes = resolved_qualification.notes
        submission.qualification_overridden = resolved_qualification.overridden
        if resolved_follow_up_notes is not UNCHANGED:
            submission.follow_up_notes = resolved_follow_up_notes
        if resolved_buyer_value is not UNCHANGED:
            submission.buyer_value = resolved_buyer_value
        if resolved_buyer_currency is not UNCHANGED:
            submission.buyer_currency = resolved_buyer_currency
        if 'country' in resolved_requirement_updates:
            submission.country = resolved_requirement_updates['country']
        if 'company' in resolved_requirement_updates:
            submission.company = resolved_requirement_updates['company']
        if resolved_requirement_updates:
            payload = dict(submission.payload_json or {})
            for key, value in resolved_requirement_updates.items():
                if key not in {'country', 'company'}:
                    payload[key] = value
            submission.payload_json = payload

        # Create the task before deriving the denormalized SLA deadline so the
        # query sees the complete actionable set in this transaction.  The
        # fallback keeps injected test/adapter creators honest even when they
        # do not persist a Task row themselves.
        created_task = None
        if resolved_task is not None:
            created_task = task_creator(
                site=submission.site,
                submission=submission,
                owner_user=task_owner,
                team=resolved_team,
                created_by_user=actor,
                title=resolved_task.title,
                description=resolved_task.description,
                task_type=resolved_task.task_type,
                priority=resolved_task.priority,
                status='open',
                due_at=resolved_task.due_at,
                reminder_at=resolved_task.reminder_at,
            )
            persisted_next_due = (
                Task.objects.filter(
                    submission_id=submission.pk,
                    status__in=ACTIONABLE_TASK_STATUSES,
                )
                .order_by('due_at', 'id')
                .values_list('due_at', flat=True)
                .first()
            )
            submission.next_follow_up_at = min(
                due_at
                for due_at in (persisted_next_due, resolved_task.due_at)
                if due_at is not None
            )
        submission.updated_at = now
        submission.save(
            update_fields=[
                'assignee',
                'team',
                'stage',
                'stage_updated_at',
                'contacted_at',
                'qualified_at',
                'won_at',
                'lost_at',
                'qualification_score',
                'qualification_json',
                'qualification_notes',
                'qualification_overridden',
                'follow_up_notes',
                'buyer_value',
                'buyer_currency',
                'country',
                'company',
                'payload_json',
                'next_follow_up_at',
                'updated_at',
            ]
        )

        activities: list[Any] = []
        if previous_assignee_id != resolved_assignee_id or previous_team_id != resolved_team_id:
            activities.append(
                activity_appender(
                    site=submission.site,
                    actor=actor,
                    activity_type='assignment',
                    direction='internal',
                    subject='负责人分配已更新',
                    body='',
                    submission=submission,
                    metadata={
                        'from_assignee_id': previous_assignee_id,
                        'to_assignee_id': resolved_assignee_id,
                        'from_team_id': previous_team_id,
                        'to_team_id': resolved_team_id,
                    },
                    occurred_at=now,
                )
            )
        activity_metadata = dict(resolved_activity.metadata or {})
        activity_metadata.update(
            {
                'workflow': 'lead_disposition_v2',
                'from_stage': previous_stage,
                'to_stage': resolved_stage,
            }
        )
        activities.append(
            activity_appender(
                site=submission.site,
                actor=actor,
                activity_type=resolved_activity.activity_type,
                direction=resolved_activity.direction,
                subject=resolved_activity.subject.strip(),
                body=resolved_activity.body.strip(),
                submission=submission,
                metadata=activity_metadata,
                occurred_at=(
                    _coerce_datetime(resolved_activity.occurred_at, field_name='发生时间')
                    if resolved_activity.occurred_at is not None
                    else now
                ),
            )
        )

        queued_outboxes: list[LeadEventOutbox] = []
        if submission.form.capi_enabled and bool((submission.consent_json or {}).get('marketing')):
            for stage_key, event_name in _stage_event_specs(
                submission,
                previous_stage=previous_stage,
                contacted_was_missing=contacted_was_missing,
                qualified_was_missing=qualified_was_missing,
            ):
                queued_outboxes.extend(
                    outbox_queuer(
                        submission,
                        stage_key=stage_key,
                        event_name=event_name,
                    )
                )

        activity_ids = tuple(
            identifier for identifier in (_model_id(item) for item in activities) if identifier is not None
        )
        task_id = _model_id(created_task)
        outbox_ids = tuple(
            identifier
            for identifier in (_model_id(item) for item in queued_outboxes)
            if identifier is not None
        )
        _store_idempotency_result(
            submission,
            token_hash=resolved_token_hash,
            request_hash=request_hash,
            actor_id=actor_id,
            activity_ids=activity_ids,
            task_id=task_id,
            outbox_ids=outbox_ids,
            completed_at=now,
        )
        audit_recorder(
            actor=actor,
            action='lead_disposed_v2',
            entity_table='lead_submission',
            entity_id=submission.id,
            before=before,
            after=_lead_snapshot(submission),
            request=request,
            metadata={
                'workflow': 'lead_disposition_v2',
                'expected_updated_at': expected.isoformat(),
                'idempotency_token_hash': resolved_token_hash,
                'idempotency_mode': (
                    IDEMPOTENCY_DATABASE_ONLY if resolved_token_hash else IDEMPOTENCY_NONE
                ),
                'activity_ids': list(activity_ids),
                'task_id': task_id,
                'outbox_ids': list(outbox_ids),
                'dispatch_scheduled': len(queued_outboxes),
            },
        )
        for outbox in queued_outboxes:
            transaction.on_commit(
                partial(_dispatch_after_commit, outbox, outbox_dispatcher),
                robust=True,
            )

        return LeadDispositionResult(
            submission=submission,
            activity_ids=activity_ids,
            task_id=task_id,
            outbox_ids=outbox_ids,
            dispatch_scheduled=len(queued_outboxes),
            replayed=False,
            idempotency_mode=(
                IDEMPOTENCY_DATABASE_ONLY if resolved_token_hash else IDEMPOTENCY_NONE
            ),
        )
