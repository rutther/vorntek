from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Iterable
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.urls import reverse
from django.utils import timezone

from leads.models import Activity, SalesTeam, Task

from .access import (
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    activity_queryset_for_user,
    assignee_queryset_for_user,
    task_queryset_for_user,
    user_role_keys,
)
from .capabilities import SalesCapability, can_sales
from .task_versions import task_version_token


PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 25
SAFE_TOKEN = re.compile(r'^[0-9]{1,20}$')
ACTIONABLE = ('open', 'in_progress')
STATUS_OPTIONS = {
    'actionable': ('可执行', 'azure'),
    'open': ('待处理', 'orange'),
    'in_progress': ('进行中', 'azure'),
    'completed': ('已完成', 'green'),
    'canceled': ('已取消', 'secondary'),
    'all': ('全部状态', 'secondary'),
}
TASK_TYPES = {
    'follow_up': '跟进', 'call': '电话', 'email': '邮件', 'whatsapp': 'WhatsApp',
    'meeting': '会议', 'quote': '报价', 'review': '内部评审', 'other': '其他',
}
PRIORITIES = {
    'urgent': ('紧急', 'red'), 'high': ('高', 'orange'),
    'normal': ('普通', 'azure'), 'low': ('低', 'secondary'),
}
DUE_OPTIONS = {
    'all': '全部时间', 'overdue': '已逾期', 'today': '今天', 'tomorrow': '明天',
    'next_7_days': '未来 7 天', 'later': '更晚',
}
SORT_OPTIONS = {
    'attention': '优先处理', 'due': '到期时间', 'priority': '优先级', 'updated': '最近更新',
}
ACTIVITY_TYPES = {
    'note': '跟进记录', 'call': '电话', 'email': '邮件', 'whatsapp': 'WhatsApp',
    'meeting': '会议', 'site_visit': '现场拜访', 'assignment': '分配',
    'conversion': '转换', 'task': '任务事件', 'system': '系统事件',
    'stage_change': '阶段变更', 'file': '文件',
}
ACTIVITY_DIRECTIONS = {'inbound': '入站', 'outbound': '出站', 'internal': '内部'}
SAFE_METADATA_LABELS = {
    'event': '事件', 'task_id': '任务 ID', 'previous_task_id': '上一任务 ID',
    'next_task_id': '下一任务 ID', 'previous_owner_user_id': '原负责人 ID',
    'owner_user_id': '负责人 ID', 'stage': '阶段', 'from_stage': '原阶段',
    'to_stage': '新阶段', 'correction_of_activity_id': '更正原活动 ID',
}


def _positive_int(value, default: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return resolved if resolved > 0 else default


def _clean_query(value) -> str:
    resolved = ' '.join(str(value or '').split())[:100]
    return resolved if len(resolved) >= 2 else ''


def _choice(value, choices: Iterable[str], default: str) -> str:
    resolved = str(value or '').strip().lower()
    return resolved if resolved in set(choices) else default


def _id_choice(value, allowed_ids: Iterable[int], default: str = 'all') -> str:
    resolved = str(value or '').strip()
    if not SAFE_TOKEN.fullmatch(resolved):
        return default
    return resolved if int(resolved) in set(allowed_ids) else default


def _user_label(user) -> str:
    if user is None:
        return '未知账号'
    return (user.get_full_name() or user.get_username()).strip()


def _format_datetime(value) -> str:
    if value is None:
        return ''
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')


def _local_day_bounds():
    local_now = timezone.localtime()
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = start + timedelta(days=1)
    return local_now, start, tomorrow


def _due_label(value, *, now=None) -> tuple[str, str]:
    if value is None:
        return '未安排', 'secondary'
    now = now or timezone.now()
    local_due = timezone.localtime(value)
    local_now = timezone.localtime(now)
    due_date = local_due.date()
    today = local_now.date()
    if value < now:
        seconds = max(1, int((now - value).total_seconds()))
        days = max(1, (seconds + 86399) // 86400)
        return f'{_format_datetime(value)} · 逾期 {days} 天', 'danger'
    if due_date == today:
        return f'{_format_datetime(value)} · 今天', 'warning'
    if due_date == today + timedelta(days=1):
        return f'{_format_datetime(value)} · 明天', 'azure'
    return _format_datetime(value), 'secondary'


@dataclass(frozen=True, slots=True)
class TaskListFilters:
    query: str = ''
    status: str = 'actionable'
    owner: str = 'all'
    team: str = 'all'
    due: str = 'all'
    priority: str = 'all'
    task_type: str = 'all'
    sort: str = 'attention'
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @classmethod
    def from_querydict(cls, querydict, *, owner_ids, team_ids, allow_team: bool):
        size = _positive_int(querydict.get('page_size'), DEFAULT_PAGE_SIZE)
        if size not in PAGE_SIZES:
            size = DEFAULT_PAGE_SIZE
        return cls(
            query=_clean_query(querydict.get('q')),
            status=_choice(querydict.get('status'), STATUS_OPTIONS, 'actionable'),
            owner=_id_choice(querydict.get('owner'), owner_ids),
            team=(
                _id_choice(querydict.get('team'), team_ids)
                if allow_team else 'all'
            ),
            due=_choice(querydict.get('due'), DUE_OPTIONS, 'all'),
            priority=_choice(querydict.get('priority'), ('all', *PRIORITIES), 'all'),
            task_type=_choice(querydict.get('type'), ('all', *TASK_TYPES), 'all'),
            sort=_choice(querydict.get('sort'), SORT_OPTIONS, 'attention'),
            page=_positive_int(querydict.get('page'), 1),
            page_size=size,
        )

    def query_params(self, *, page: int | None = None) -> dict[str, object]:
        params: dict[str, object] = {
            'sort': self.sort,
            'page': page if page is not None else self.page,
            'page_size': self.page_size,
        }
        if self.query:
            params['q'] = self.query
        if self.status != 'actionable':
            params['status'] = self.status
        if self.owner != 'all':
            params['owner'] = self.owner
        if self.team != 'all':
            params['team'] = self.team
        if self.due != 'all':
            params['due'] = self.due
        if self.priority != 'all':
            params['priority'] = self.priority
        if self.task_type != 'all':
            params['type'] = self.task_type
        return params


def _url(route_name: str, filters: TaskListFilters, *, args=(), **changes) -> str:
    resolved = replace(filters, **changes) if changes else filters
    return f'{reverse(route_name, args=args)}?{urlencode(resolved.query_params())}'


def _task_detail_url(filters: TaskListFilters, *, task_id: int, activity_page: int = 1) -> str:
    params = filters.query_params()
    if activity_page > 1:
        params['activity_page'] = activity_page
    return f'{reverse("console:task_workspace_detail_v2", args=(task_id,))}?{urlencode(params)}'


def _visible_choices(*, site, user, scoped):
    roles = user_role_keys(user)
    candidate_users = list(assignee_queryset_for_user(user, site=site))
    candidate_ids = {item.pk for item in candidate_users}
    historical_ids = set(
        scoped.order_by().values_list('owner_user_id', flat=True).distinct()
    )
    historical = list(
        get_user_model().objects.filter(pk__in=historical_ids - candidate_ids).order_by('username')
    )
    owners = [
        {'value': str(item.pk), 'label': _user_label(item), 'historical': False}
        for item in candidate_users
    ] + [
        {'value': str(item.pk), 'label': f'{_user_label(item)}（历史）', 'historical': True}
        for item in historical
    ]
    team_ids = list(
        scoped.exclude(team_id__isnull=True).order_by().values_list('team_id', flat=True).distinct()
    )
    teams = [
        {'value': str(item.pk), 'label': item.name}
        for item in SalesTeam.objects.filter(site=site, pk__in=team_ids).order_by('name', 'id')
    ]
    return owners, teams, bool(roles.intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN}))


def _apply_filters(queryset, filters: TaskListFilters):
    if filters.status == 'actionable':
        queryset = queryset.filter(status__in=ACTIONABLE)
    elif filters.status != 'all':
        queryset = queryset.filter(status=filters.status)
    if filters.owner.isdigit():
        queryset = queryset.filter(owner_user_id=int(filters.owner))
    if filters.team.isdigit():
        queryset = queryset.filter(team_id=int(filters.team))
    if filters.priority != 'all':
        queryset = queryset.filter(priority=filters.priority)
    if filters.task_type != 'all':
        queryset = queryset.filter(task_type=filters.task_type)
    now, today_start, tomorrow = _local_day_bounds()
    if filters.due == 'overdue':
        queryset = queryset.filter(status__in=ACTIONABLE, due_at__lt=now)
    elif filters.due == 'today':
        queryset = queryset.filter(due_at__gte=today_start, due_at__lt=tomorrow)
    elif filters.due == 'tomorrow':
        queryset = queryset.filter(due_at__gte=tomorrow, due_at__lt=tomorrow + timedelta(days=1))
    elif filters.due == 'next_7_days':
        queryset = queryset.filter(due_at__gte=today_start, due_at__lt=tomorrow + timedelta(days=7))
    elif filters.due == 'later':
        queryset = queryset.filter(due_at__gte=tomorrow + timedelta(days=7))
    if filters.query:
        queryset = queryset.filter(
            Q(title__icontains=filters.query)
            | Q(description__icontains=filters.query)
            | Q(submission__full_name__icontains=filters.query)
            | Q(submission__company__icontains=filters.query)
            | Q(company__name__icontains=filters.query)
            | Q(contact__full_name__icontains=filters.query)
            | Q(opportunity__name__icontains=filters.query)
        )
    return queryset


def _ordered(queryset, filters: TaskListFilters):
    priority_rank = Case(
        When(priority='urgent', then=Value(4)),
        When(priority='high', then=Value(3)),
        When(priority='normal', then=Value(2)),
        default=Value(1), output_field=IntegerField(),
    )
    if filters.sort == 'updated':
        return queryset.order_by('-updated_at', '-id')
    if filters.sort == 'priority':
        return queryset.annotate(_priority_rank=priority_rank).order_by('-_priority_rank', 'due_at', 'id')
    if filters.sort == 'due':
        return queryset.annotate(_priority_rank=priority_rank).order_by('due_at', '-_priority_rank', 'id')
    terminal_rank = Case(
        When(status__in=ACTIONABLE, then=Value(0)), default=Value(1), output_field=IntegerField(),
    )
    overdue_rank = Case(
        When(status__in=ACTIONABLE, due_at__lt=timezone.now(), then=Value(0)),
        default=Value(1), output_field=IntegerField(),
    )
    return queryset.annotate(
        _terminal_rank=terminal_rank,
        _overdue_rank=overdue_rank,
        _priority_rank=priority_rank,
    ).order_by('_terminal_rank', '_overdue_rank', 'due_at', '-_priority_rank', 'id')


def _target_links(item) -> list[dict[str, str]]:
    links = []
    if item.submission_id:
        links.append({'kind': '线索', 'label': f'线索 #{item.submission_id}', 'href': reverse('console:lead_workspace_detail_v2', args=(item.submission_id,))})
    if item.company_id:
        links.append({'kind': '企业', 'label': item.company.name, 'href': reverse('console:company_workspace_detail_v2', args=(item.company_id,))})
    if item.contact_id:
        links.append({'kind': '联系人', 'label': item.contact.full_name, 'href': reverse('console:contact_workspace_detail_v2', args=(item.contact_id,))})
    if item.opportunity_id:
        links.append({'kind': '销售机会', 'label': item.opportunity.name, 'href': reverse('console:opportunity_workspace_detail_v2', args=(item.opportunity_id,))})
    return links


def _metadata_rows(metadata) -> list[dict[str, str]]:
    if not isinstance(metadata, dict):
        return []
    rows = []
    for key, label in SAFE_METADATA_LABELS.items():
        value = metadata.get(key)
        if value in (None, '', [], {}):
            continue
        if isinstance(value, (str, int, float, bool)):
            rows.append({'label': label, 'value': str(value)[:240]})
    return rows


def _row(item, *, filters: TaskListFilters, now) -> dict[str, object]:
    status_label, status_tone = STATUS_OPTIONS.get(item.status, (item.status, 'secondary'))
    priority_label, priority_tone = PRIORITIES.get(item.priority, (item.priority, 'secondary'))
    due_label, due_tone = _due_label(item.due_at, now=now)
    return {
        'id': item.pk,
        'title': item.title,
        'description': item.description,
        'task_type_label': TASK_TYPES.get(item.task_type, item.task_type),
        'priority_label': priority_label,
        'priority_tone': priority_tone,
        'status_label': status_label,
        'status_tone': status_tone,
        'due_at': item.due_at,
        'due_label': due_label,
        'due_tone': due_tone,
        'owner': _user_label(item.owner_user),
        'team': item.team.name if item.team else '未分配团队',
        'targets': _target_links(item),
        'detail_url': _url('console:task_workspace_detail_v2', filters, args=(item.pk,)),
        'version': task_version_token(item.updated_at),
        'can_start': item.status == 'open',
        'can_close': item.status in ACTIONABLE,
    }


def _pagination(filters, page_obj):
    return {
        'current': page_obj.number,
        'total_pages': page_obj.paginator.num_pages,
        'total_items': page_obj.paginator.count,
        'start': page_obj.start_index() if page_obj.paginator.count else 0,
        'end': page_obj.end_index() if page_obj.paginator.count else 0,
        'previous_url': _url('console:task_workspace_v2', filters, page=page_obj.previous_page_number()) if page_obj.has_previous() else '',
        'next_url': _url('console:task_workspace_v2', filters, page=page_obj.next_page_number()) if page_obj.has_next() else '',
    }


def build_task_list_workspace_v2(*, request, site) -> dict[str, object]:
    user = request.user
    base = task_queryset_for_user(Task.objects.filter(site=site), user)
    owners, teams, allow_team = _visible_choices(site=site, user=user, scoped=base)
    filters = TaskListFilters.from_querydict(
        request.GET,
        owner_ids=(int(item['value']) for item in owners),
        team_ids=(int(item['value']) for item in teams),
        allow_team=allow_team,
    )
    queryset = _apply_filters(base, filters).select_related(
        'owner_user', 'team', 'submission', 'company', 'contact', 'opportunity'
    ).distinct()
    queryset = _ordered(queryset, filters)
    page_obj = Paginator(queryset, filters.page_size).get_page(filters.page)
    if page_obj.number != filters.page:
        filters = replace(filters, page=page_obj.number)
    now, today_start, tomorrow = _local_day_bounds()
    actionable = base.filter(status__in=ACTIONABLE)
    high_priority = actionable.filter(priority__in=('high', 'urgent')).count()
    metrics = [
        {'label': '逾期', 'value': actionable.filter(due_at__lt=now).count(), 'href': _url('console:task_workspace_v2', TaskListFilters(due='overdue')), 'tone': 'danger', 'icon': 'alarm-clock'},
        {'label': '今天', 'value': actionable.filter(due_at__gte=today_start, due_at__lt=tomorrow).count(), 'href': _url('console:task_workspace_v2', TaskListFilters(due='today')), 'tone': 'warning', 'icon': 'calendar-check'},
        {'label': '未来 7 天', 'value': actionable.filter(due_at__gte=tomorrow, due_at__lt=tomorrow + timedelta(days=7)).count(), 'href': _url('console:task_workspace_v2', TaskListFilters(due='next_7_days')), 'tone': 'azure', 'icon': 'calendar-range'},
        {'label': '高 / 紧急', 'value': high_priority, 'href': _url('console:task_workspace_v2', TaskListFilters(priority='high')), 'tone': 'orange', 'icon': 'flag'},
    ]
    has_filters = bool(
        filters.query or filters.status != 'actionable' or filters.owner != 'all'
        or filters.team != 'all' or filters.due != 'all' or filters.priority != 'all'
        or filters.task_type != 'all'
    )
    return {
        'title': '任务',
        'description': '把未来承诺按逾期、负责人和客户上下文排成可执行队列；完成和取消必须留下真实结果。',
        'filters': filters,
        'rows': [_row(item, filters=filters, now=now) for item in page_obj.object_list],
        'metrics': metrics,
        'owners': owners,
        'assign_candidates': [item for item in owners if not item['historical']],
        'teams': teams,
        'allow_team_filter': allow_team,
        'status_options': [{'value': key, 'label': value[0]} for key, value in STATUS_OPTIONS.items()],
        'due_options': [{'value': key, 'label': value} for key, value in DUE_OPTIONS.items()],
        'priority_options': [{'value': key, 'label': value[0]} for key, value in PRIORITIES.items()],
        'type_options': [{'value': key, 'label': value} for key, value in TASK_TYPES.items()],
        'sort_options': [{'value': key, 'label': value} for key, value in SORT_OPTIONS.items()],
        'pagination': _pagination(filters, page_obj),
        'canonical_url': _url('console:task_workspace_v2', filters),
        'clear_url': _url('console:task_workspace_v2', TaskListFilters()),
        'has_filters': has_filters,
        'can_create': can_sales(user, SalesCapability.WRITE),
        'can_bulk_assign': can_sales(user, SalesCapability.ASSIGN),
        'error': False,
    }


def _detail_filters(*, request, site, user, scoped):
    owners, teams, allow_team = _visible_choices(site=site, user=user, scoped=scoped)
    return TaskListFilters.from_querydict(
        request.GET,
        owner_ids=(int(item['value']) for item in owners),
        team_ids=(int(item['value']) for item in teams),
        allow_team=allow_team,
    )


def build_task_detail_workspace_v2(*, request, site, task_id: int) -> dict[str, object]:
    user = request.user
    scoped = task_queryset_for_user(Task.objects.filter(site=site), user)
    filters = _detail_filters(request=request, site=site, user=user, scoped=scoped)
    task = scoped.select_related(
        'owner_user', 'team', 'created_by_user', 'submission', 'company', 'contact', 'opportunity'
    ).filter(pk=task_id).first()
    if task is None:
        return {'missing': True}
    activity_queryset = activity_queryset_for_user(
        Activity.objects.filter(site=site).annotate(
            _task_id_text=KeyTextTransform('task_id', 'metadata_json')
        ).filter(_task_id_text=str(task.pk)), user
    ).select_related('actor_user').order_by('-occurred_at', '-id')
    requested_activity_page = _positive_int(request.GET.get('activity_page'), 1)
    activity_page = Paginator(activity_queryset, 25).get_page(requested_activity_page)
    status_label, status_tone = STATUS_OPTIONS.get(task.status, (task.status, 'secondary'))
    priority_label, priority_tone = PRIORITIES.get(task.priority, (task.priority, 'secondary'))
    due_label, due_tone = _due_label(task.due_at)
    can_write = can_sales(user, SalesCapability.WRITE, record=task)
    owner_candidates = []
    if task.team_id:
        users = assignee_queryset_for_user(user, site=site).filter(
            sales_team_memberships__team_id=task.team_id,
            sales_team_memberships__team__enabled=True,
        ).distinct().order_by('username')
        if task.owner_user_id and not users.filter(pk=task.owner_user_id).exists():
            users = get_user_model().objects.filter(Q(pk__in=users.values('pk')) | Q(pk=task.owner_user_id)).order_by('username')
        owner_candidates = [
            {'value': str(item.pk), 'label': _user_label(item), 'selected': item.pk == task.owner_user_id, 'disabled': not item.is_active}
            for item in users
        ]
    return {
        'title': task.title,
        'record': {
            'id': task.pk,
            'title': task.title,
            'description': task.description,
            'task_type': task.task_type,
            'task_type_label': TASK_TYPES.get(task.task_type, task.task_type),
            'priority': task.priority,
            'priority_label': priority_label,
            'priority_tone': priority_tone,
            'status': task.status,
            'status_label': status_label,
            'status_tone': status_tone,
            'due_at': task.due_at,
            'due_input': timezone.localtime(task.due_at).strftime('%Y-%m-%dT%H:%M'),
            'due_label': due_label,
            'due_tone': due_tone,
            'owner': _user_label(task.owner_user),
            'owner_id': task.owner_user_id,
            'team': task.team.name if task.team else '未分配团队',
            'created_by': _user_label(task.created_by_user),
            'created_at_label': _format_datetime(task.created_at),
            'updated_at_label': _format_datetime(task.updated_at),
            'ended_at_label': _format_datetime(task.completed_at),
            'outcome': task.outcome,
            'reminder_label': _format_datetime(task.reminder_at),
            'targets': _target_links(task),
            'version': task_version_token(task.updated_at),
        },
        'activities': [
            {
                'id': item.pk,
                'subject': item.subject or '未命名活动',
                'body': item.body,
                'type': item.activity_type,
                'direction': item.direction,
                'actor': _user_label(item.actor_user),
                'occurred_at': item.occurred_at,
                'occurred_label': _format_datetime(item.occurred_at),
                'detail_url': reverse('console:activity_workspace_detail_v2', args=(item.pk,)),
            }
            for item in activity_page.object_list
        ],
        'activity_total': activity_page.paginator.count,
        'activity_pagination': {
            'current': activity_page.number,
            'total_pages': activity_page.paginator.num_pages,
            'previous_url': (
                _task_detail_url(filters, task_id=task.pk, activity_page=activity_page.previous_page_number())
                if activity_page.has_previous() else ''
            ),
            'next_url': (
                _task_detail_url(filters, task_id=task.pk, activity_page=activity_page.next_page_number())
                if activity_page.has_next() else ''
            ),
        },
        'owner_candidates': owner_candidates,
        'can_assign': can_sales(user, SalesCapability.ASSIGN, record=task),
        'can_write': can_write,
        'can_edit': can_write and task.status in ACTIONABLE,
        'can_start': can_write and task.status == 'open',
        'can_close': can_write and task.status in ACTIONABLE,
        'list_url': _url('console:task_workspace_v2', filters),
        'canonical_url': _task_detail_url(
            filters, task_id=task.pk, activity_page=activity_page.number
        ),
        'type_options': [{'value': key, 'label': value} for key, value in TASK_TYPES.items()],
        'priority_options': [{'value': key, 'label': value[0]} for key, value in PRIORITIES.items()],
        'error': False,
    }


def build_activity_detail_workspace_v2(*, request, site, activity_id: int) -> dict[str, object]:
    activity = activity_queryset_for_user(
        Activity.objects.filter(site=site), request.user
    ).select_related(
        'actor_user', 'submission', 'company', 'contact', 'opportunity'
    ).filter(pk=activity_id).first()
    if activity is None:
        return {'missing': True}
    corrections = list(
        activity_queryset_for_user(
            Activity.objects.filter(site=site, metadata_json__correction_of_activity_id=activity.pk), request.user
        ).select_related('actor_user').order_by('occurred_at', 'id')[:100]
    )
    corrected_activity_id = (activity.metadata_json or {}).get('correction_of_activity_id')
    return {
        'title': activity.subject or f'活动 #{activity.pk}',
        'record': {
            'id': activity.pk,
            'subject': activity.subject or '未命名活动',
            'body': activity.body,
            'activity_type': activity.activity_type,
            'activity_type_label': ACTIVITY_TYPES.get(activity.activity_type, activity.activity_type),
            'direction': activity.direction,
            'direction_label': ACTIVITY_DIRECTIONS.get(activity.direction, activity.direction),
            'actor': _user_label(activity.actor_user),
            'occurred_label': _format_datetime(activity.occurred_at),
            'created_label': _format_datetime(activity.created_at),
            'targets': _target_links(activity),
            'metadata_rows': _metadata_rows(activity.metadata_json),
            'corrects_url': (
                reverse('console:activity_workspace_detail_v2', args=(corrected_activity_id,))
                if str(corrected_activity_id or '').isdigit() else ''
            ),
        },
        'corrections': [
            {
                'id': item.pk,
                'subject': item.subject,
                'body': item.body,
                'actor': _user_label(item.actor_user),
                'occurred_label': _format_datetime(item.occurred_at),
                'detail_url': reverse('console:activity_workspace_detail_v2', args=(item.pk,)),
            }
            for item in corrections
        ],
        'can_correct': can_sales(request.user, SalesCapability.WRITE),
        'canonical_url': reverse('console:activity_workspace_detail_v2', args=(activity.pk,)),
        'error': False,
    }


def build_task_error_workspace(*, detail=False):
    return {
        'title': '任务暂时不可用',
        'description': '读取任务时遇到数据库错误，请稍后重试。',
        'rows': [], 'metrics': [], 'error': True, 'detail': detail,
        'canonical_url': reverse('console:task_workspace_v2'),
    }
