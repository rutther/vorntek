from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Iterable
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Case, F, IntegerField, Q, QuerySet, TextField, Value, When
from django.db.models.functions import Coalesce, Trim
from django.urls import reverse
from django.utils import timezone

from leads.crm_services import OPPORTUNITY_STAGE_TRANSITIONS
from leads.models import (
    Activity,
    CrmAttachment,
    LeadSubmission,
    Opportunity,
    OpportunityStageHistory,
    Task,
)

from .access import (
    activity_queryset_for_user,
    assignee_queryset_for_user,
    lead_queryset_for_user,
    opportunity_queryset_for_user,
    task_queryset_for_user,
)
from .capabilities import SalesCapability, can_sales
from .opportunity_versions import opportunity_version_token
from .sales_queries import PIPELINE_STAGES, pipeline_value_summary


ACTIVE_STAGES = ('qualification', 'discovery', 'solution', 'quotation', 'negotiation', 'on_hold')
TERMINAL_STAGES = frozenset({'won', 'lost'})
STAGE_LABELS = dict(PIPELINE_STAGES)
STAGE_TONES = {
    'qualification': 'azure',
    'discovery': 'blue',
    'solution': 'indigo',
    'quotation': 'purple',
    'negotiation': 'orange',
    'on_hold': 'secondary',
    'won': 'green',
    'lost': 'red',
}
SOURCE_LABELS = {
    'website': '网站',
    'meta_ads': 'Meta 广告',
    'paid_search': '付费搜索',
    'organic': '自然搜索',
    'referral': '外部推荐',
    'direct': '直接访问',
    'whatsapp': 'WhatsApp',
    'email': '邮件',
    'other': '其他',
}
ACTIVITY_TYPE_LABELS = {
    'note': '跟进记录',
    'call': '电话',
    'email': '邮件',
    'whatsapp': 'WhatsApp',
    'meeting': '会议',
    'site_visit': '现场拜访',
    'system': '系统记录',
}
TASK_STATUS_LABELS = {
    'open': '待处理',
    'in_progress': '处理中',
    'completed': '已完成',
    'canceled': '已取消',
}
QUERY_KEYS = frozenset({
    'q',
    'stage',
    'owner',
    'source',
    'due',
    'include_closed',
    'sort',
    'page',
    'page_size',
    'view',
})
PAGE_SIZES = (20, 25, 50, 100)
DEFAULT_PAGE_SIZE = 25
SORT_KEYS = ('attention', 'follow_up', 'close_date', 'amount_desc', 'updated')
VIEW_KEYS = ('list', 'board')
DUE_KEYS = (
    'all',
    'overdue',
    'today',
    'next_7_days',
    'unplanned',
    'close_7_days',
    'missing_next_step',
)
SAFE_TOKEN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def _clean_search(value) -> str:
    return ' '.join(str(value or '').split())[:160]


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _choice(value, choices: Iterable[str], default: str) -> str:
    resolved = str(value or '').strip().lower()
    return resolved if resolved in set(choices) else default


def _token_choice(value, choices: Iterable[str], default: str) -> str:
    resolved = str(value or '').strip()
    if not SAFE_TOKEN.fullmatch(resolved):
        return default
    return resolved if resolved in set(choices) else default


def _truthy(value) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _user_label(user) -> str:
    if user is None:
        return '未分配'
    return (user.get_full_name() or user.get_username()).strip()


def _format_money(amount: Decimal | None, currency: str) -> str:
    if amount is None:
        return '未填写'
    resolved_currency = (currency or 'USD').strip().upper() or 'USD'
    return f'{resolved_currency} {amount:,.2f}'


def _format_datetime(value) -> str:
    if value is None:
        return ''
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')


def _format_datetime_input(value) -> str:
    if value is None:
        return ''
    return timezone.localtime(value).strftime('%Y-%m-%dT%H:%M')


@dataclass(frozen=True, slots=True)
class OpportunityWorkspaceV2Filters:
    query: str = ''
    stage: str = 'all'
    owner: str = 'all'
    source: str = 'all'
    due: str = 'all'
    include_closed: bool = False
    sort: str = 'attention'
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    view: str = 'list'

    @classmethod
    def from_querydict(
        cls,
        querydict,
        *,
        allowed_owner_ids: Iterable[int] = (),
        allowed_sources: Iterable[str] = (),
        can_filter_unassigned: bool = False,
    ) -> 'OpportunityWorkspaceV2Filters':
        stage = _choice(
            querydict.get('stage'),
            ('all', *STAGE_LABELS.keys()),
            'all',
        )
        owner_choices = {'all', 'mine', *(str(value) for value in allowed_owner_ids)}
        if can_filter_unassigned:
            owner_choices.add('unassigned')
        page_size = _positive_int(querydict.get('page_size'), DEFAULT_PAGE_SIZE)
        if page_size not in PAGE_SIZES:
            page_size = DEFAULT_PAGE_SIZE
        include_closed = _truthy(querydict.get('include_closed'))
        if stage in TERMINAL_STAGES:
            include_closed = True
        return cls(
            query=_clean_search(querydict.get('q')),
            stage=stage,
            owner=_token_choice(querydict.get('owner'), owner_choices, 'all'),
            source=_token_choice(
                querydict.get('source'),
                {'all', *allowed_sources},
                'all',
            ),
            due=_choice(querydict.get('due'), DUE_KEYS, 'all'),
            include_closed=include_closed,
            sort=_choice(querydict.get('sort'), SORT_KEYS, 'attention'),
            page=_positive_int(querydict.get('page'), 1),
            page_size=page_size,
            view=_choice(querydict.get('view'), VIEW_KEYS, 'list'),
        )

    def query_params(self, *, page: int | None = None, view: str | None = None) -> dict[str, object]:
        params: dict[str, object] = {
            'sort': self.sort,
            'page': page if page is not None else self.page,
            'page_size': self.page_size,
            'view': view if view is not None else self.view,
        }
        if self.query:
            params['q'] = self.query
        if self.stage != 'all':
            params['stage'] = self.stage
        if self.owner != 'all':
            params['owner'] = self.owner
        if self.source != 'all':
            params['source'] = self.source
        if self.due != 'all':
            params['due'] = self.due
        if self.include_closed:
            params['include_closed'] = '1'
        return params


def _url(route_name: str, filters: OpportunityWorkspaceV2Filters, *, args=(), **changes) -> str:
    resolved = replace(filters, **changes) if changes else filters
    base = reverse(route_name, args=args)
    return f'{base}?{urlencode(resolved.query_params())}'


def _visible_owner_choices(*, site, user, scoped: QuerySet[Opportunity]) -> list[dict[str, object]]:
    active_candidates = list(assignee_queryset_for_user(user, site=site))
    candidate_ids = {candidate.id for candidate in active_candidates}
    historical_ids = set(
        scoped.exclude(owner_user_id__isnull=True)
        .order_by()
        .values_list('owner_user_id', flat=True)
        .distinct()
    )
    historical_users = list(
        get_user_model().objects.filter(id__in=historical_ids - candidate_ids).order_by('username')
    )
    return [
        {
            'value': str(candidate.id),
            'label': _user_label(candidate),
            'is_active': bool(candidate.is_active),
            'current_only': False,
        }
        for candidate in active_candidates
    ] + [
        {
            'value': str(candidate.id),
            'label': f'{_user_label(candidate)}（历史负责人，仅保留）',
            'is_active': bool(candidate.is_active),
            'current_only': True,
        }
        for candidate in historical_users
    ]


def _visible_source_values(scoped: QuerySet[Opportunity]) -> list[str]:
    """Return only values that are safe to round-trip through canonical URLs."""

    return [
        value
        for value in (
            scoped.exclude(source_channel='')
            .order_by()
            .values_list('source_channel', flat=True)
            .distinct()
        )
        if value and SAFE_TOKEN.fullmatch(value)
    ]


def _apply_filters(queryset: QuerySet[Opportunity], filters: OpportunityWorkspaceV2Filters, *, user, now):
    local_today = timezone.localdate(now)
    local_week = local_today + timedelta(days=7)
    if not filters.include_closed:
        queryset = queryset.exclude(stage__in=TERMINAL_STAGES)
    if filters.stage != 'all':
        queryset = queryset.filter(stage=filters.stage)
    if filters.owner == 'mine':
        queryset = queryset.filter(owner_user=user)
    elif filters.owner == 'unassigned':
        queryset = queryset.filter(owner_user__isnull=True)
    elif filters.owner.isdigit():
        queryset = queryset.filter(owner_user_id=int(filters.owner))
    if filters.source != 'all':
        queryset = queryset.filter(source_channel=filters.source)
    if filters.query:
        queryset = queryset.filter(
            Q(name__icontains=filters.query)
            | Q(company__name__icontains=filters.query)
            | Q(primary_contact__full_name__icontains=filters.query)
            | Q(product_scope__icontains=filters.query)
            | Q(capacity_target__icontains=filters.query)
            | Q(packaging_format__icontains=filters.query)
        )
    if filters.due == 'overdue':
        queryset = queryset.filter(next_follow_up_at__lt=now)
    elif filters.due == 'today':
        queryset = queryset.filter(next_follow_up_at__date=local_today)
    elif filters.due == 'next_7_days':
        queryset = queryset.filter(next_follow_up_at__gte=now, next_follow_up_at__date__lte=local_week)
    elif filters.due == 'unplanned':
        queryset = queryset.filter(next_follow_up_at__isnull=True)
    elif filters.due == 'close_7_days':
        queryset = queryset.filter(
            expected_close_date__gte=local_today,
            expected_close_date__lte=local_week,
        )
    elif filters.due == 'missing_next_step':
        queryset = queryset.annotate(
            _v2_next_step_clean=Trim(
                Coalesce('next_step', Value(''), output_field=TextField()),
            ),
        ).filter(_v2_next_step_clean='')
    return queryset


def _order_queryset(queryset: QuerySet[Opportunity], sort: str, *, now):
    if sort == 'follow_up':
        return queryset.order_by(F('next_follow_up_at').asc(nulls_last=True), '-updated_at', '-id')
    if sort == 'close_date':
        return queryset.order_by(F('expected_close_date').asc(nulls_last=True), '-updated_at', '-id')
    if sort == 'amount_desc':
        return queryset.order_by(F('value_amount').desc(nulls_last=True), '-updated_at', '-id')
    if sort == 'updated':
        return queryset.order_by('-updated_at', '-id')
    return queryset.annotate(
        _v2_attention_bucket=Case(
            When(next_follow_up_at__lt=now, then=Value(0)),
            When(Q(next_step='') | Q(next_step__isnull=True), then=Value(1)),
            When(expected_close_date__lt=timezone.localdate(now), then=Value(2)),
            When(owner_user__isnull=True, then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        ),
    ).order_by(
        '_v2_attention_bucket',
        F('next_follow_up_at').asc(nulls_last=True),
        F('expected_close_date').asc(nulls_last=True),
        '-updated_at',
        '-id',
    )


def _row_payload(item: Opportunity, *, filters: OpportunityWorkspaceV2Filters, now) -> dict[str, object]:
    is_terminal = item.stage in TERMINAL_STAGES
    local_today = timezone.localdate(now)
    owner = _user_label(item.owner_user)
    detail_url = _url(
        'console:opportunity_workspace_detail_v2',
        filters,
        args=(item.id,),
    )
    return {
        'id': item.id,
        'name': item.name,
        'detail_url': detail_url,
        'company': item.company.name if item.company else '未关联企业',
        'contact': item.primary_contact.full_name if item.primary_contact else '未关联联系人',
        'stage': {
            'value': item.stage,
            'label': STAGE_LABELS.get(item.stage, item.stage or '未知阶段'),
            'tone': STAGE_TONES.get(item.stage, 'secondary'),
            'terminal': is_terminal,
            'reason_complete': bool(
                (item.won_reason or '').strip() if item.stage == 'won'
                else (item.lost_reason or '').strip() if item.stage == 'lost'
                else True
            ),
        },
        'amount_label': _format_money(item.value_amount, item.currency),
        'probability': item.probability,
        'next_step': (item.next_step or '').strip() or '未填写下一步',
        'follow_up_at': item.next_follow_up_at,
        'follow_up_label': _format_datetime(item.next_follow_up_at) or '未安排跟进',
        'follow_up_overdue': bool(item.next_follow_up_at and item.next_follow_up_at < now and not is_terminal),
        'owner': owner,
        'team': item.team.name if item.team else '未分配团队',
        'close_date': item.expected_close_date,
        'close_label': item.expected_close_date.isoformat() if item.expected_close_date else '未填写',
        'close_overdue': bool(item.expected_close_date and item.expected_close_date < local_today and not is_terminal),
        'product_scope': (item.product_scope or '').strip(),
        'capacity_target': (item.capacity_target or '').strip(),
    }


def _metric_url(*, due: str = 'all', owner: str = 'all') -> str:
    filters = OpportunityWorkspaceV2Filters(due=due, owner=owner)
    return _url('console:opportunity_workspace_v2', filters)


def _active_filter_chips(filters: OpportunityWorkspaceV2Filters, owner_labels: dict[str, str]) -> list[dict[str, str]]:
    chips: list[dict[str, str]] = []
    definitions = [
        ('query', filters.query, f'搜索：{filters.query}', ''),
        ('stage', filters.stage, f'阶段：{STAGE_LABELS.get(filters.stage, filters.stage)}', 'all'),
        ('owner', filters.owner, f'负责人：{owner_labels.get(filters.owner, filters.owner)}', 'all'),
        ('source', filters.source, f'来源：{SOURCE_LABELS.get(filters.source, filters.source)}', 'all'),
        ('due', filters.due, f'时间：{dict(DUE_OPTIONS).get(filters.due, filters.due)}', 'all'),
    ]
    for field, value, label, default in definitions:
        if value and value != default:
            chips.append({
                'label': label,
                'remove_url': _url(
                    'console:opportunity_workspace_v2',
                    filters,
                    **{field: default, 'page': 1},
                ),
            })
    if filters.include_closed:
        chips.append({
            'label': '包含已关闭',
            'remove_url': _url(
                'console:opportunity_workspace_v2',
                filters,
                include_closed=False,
                stage='all' if filters.stage in TERMINAL_STAGES else filters.stage,
                page=1,
            ),
        })
    return chips


STAGE_OPTIONS = (
    {'value': 'all', 'label': '全部阶段'},
    *({'value': key, 'label': label} for key, label in PIPELINE_STAGES),
)
DUE_OPTIONS = (
    ('all', '全部时间'),
    ('overdue', '跟进已逾期'),
    ('today', '今天跟进'),
    ('next_7_days', '7 天内跟进'),
    ('unplanned', '未安排跟进'),
    ('close_7_days', '7 天内预计成交'),
    ('missing_next_step', '下一步缺失'),
)
SORT_OPTIONS = (
    ('attention', '优先处理'),
    ('follow_up', '跟进时间'),
    ('close_date', '预计成交'),
    ('amount_desc', '金额从高到低'),
    ('updated', '最近更新'),
)


def build_opportunity_list_workspace_v2(*, request, site, now=None) -> dict[str, object]:
    now = now or timezone.now()
    scoped = opportunity_queryset_for_user(
        Opportunity.objects.filter(site=site).select_related(
            'company', 'primary_contact', 'owner_user', 'team', 'source_submission'
        ),
        request.user,
    )
    owner_choices = _visible_owner_choices(site=site, user=request.user, scoped=scoped)
    owner_ids = [int(choice['value']) for choice in owner_choices]
    source_values = _visible_source_values(scoped)
    can_assign = can_sales(request.user, SalesCapability.ASSIGN)
    filters = OpportunityWorkspaceV2Filters.from_querydict(
        request.GET,
        allowed_owner_ids=owner_ids,
        allowed_sources=source_values,
        can_filter_unassigned=can_assign,
    )
    filtered = _apply_filters(scoped, filters, user=request.user, now=now)
    filtered_count = filtered.count()
    ordered = _order_queryset(filtered, filters.sort, now=now)
    paginator = Paginator(ordered, filters.page_size)
    page_obj = paginator.get_page(filters.page)
    if page_obj.number != filters.page:
        filters = replace(filters, page=page_obj.number)

    rows = [
        _row_payload(item, filters=filters, now=now)
        for item in page_obj.object_list
    ]
    active_scope = scoped.exclude(stage__in=TERMINAL_STAGES)
    local_today = timezone.localdate(now)
    local_week = local_today + timedelta(days=7)
    missing_next_step = active_scope.annotate(
        _v2_next_step_clean=Trim(
            Coalesce('next_step', Value(''), output_field=TextField()),
        ),
    ).filter(_v2_next_step_clean='').count()
    metrics = [
        {
            'key': 'overdue',
            'label': '已逾期跟进',
            'value': active_scope.filter(next_follow_up_at__lt=now).count(),
            'description': '当前权限范围 · 站点时区',
            'tone': 'danger',
            'icon': 'alarm-clock',
            'href': _metric_url(due='overdue'),
        },
        {
            'key': 'close_7_days',
            'label': '7 天内预计成交',
            'value': active_scope.filter(
                expected_close_date__gte=local_today,
                expected_close_date__lte=local_week,
            ).count(),
            'description': '当前权限范围 · 含今天',
            'tone': 'warning',
            'icon': 'calendar-range',
            'href': _metric_url(due='close_7_days'),
        },
        {
            'key': 'missing_next_step',
            'label': '下一步缺失',
            'value': missing_next_step,
            'description': '当前权限范围 · 仅进行中',
            'tone': 'orange',
            'icon': 'circle-help',
            'href': _metric_url(due='missing_next_step'),
        },
    ]
    if can_assign:
        metrics.append({
            'key': 'unassigned',
            'label': '未分配',
            'value': active_scope.filter(owner_user__isnull=True).count(),
            'description': '当前权限范围 · 仅进行中',
            'tone': 'secondary',
            'icon': 'user-round-x',
            'href': _metric_url(owner='unassigned'),
        })
    for metric in metrics:
        if not metric['value']:
            metric['href'] = ''

    owner_options = [
        {'value': 'all', 'label': '全部负责人'},
        {'value': 'mine', 'label': '我负责的'},
    ]
    if can_assign:
        owner_options.append({'value': 'unassigned', 'label': '未分配'})
    owner_options.extend(owner_choices)
    owner_labels = {option['value']: option['label'] for option in owner_options}

    board_rows = list(
        _order_queryset(filtered, 'follow_up', now=now)[:200]
    ) if filters.view == 'board' else []
    board_columns = []
    for stage_value, stage_label in PIPELINE_STAGES:
        stage_rows = [item for item in board_rows if item.stage == stage_value]
        if not stage_rows:
            continue
        board_columns.append({
            'value': stage_value,
            'label': stage_label,
            'tone': STAGE_TONES.get(stage_value, 'secondary'),
            'count': len(stage_rows),
            'amounts': pipeline_value_summary(stage_rows),
            'rows': [
                _row_payload(item, filters=filters, now=now)
                for item in stage_rows
            ],
        })

    canonical_url = _url('console:opportunity_workspace_v2', filters)
    clear_url = _url(
        'console:opportunity_workspace_v2',
        OpportunityWorkspaceV2Filters(view=filters.view, page_size=filters.page_size),
    )
    return {
        'site': site,
        'filters': filters,
        'canonical_url': canonical_url,
        'clear_url': clear_url,
        'list_view_url': _url('console:opportunity_workspace_v2', filters, view='list', page=1),
        'board_view_url': _url('console:opportunity_workspace_v2', filters, view='board', page=1),
        'rows': rows,
        'board': {
            'columns': board_columns,
            'truncated': filtered_count > 200,
            'hidden_stages': [
                {'value': value, 'label': label}
                for value, label in PIPELINE_STAGES
                if not any(column['value'] == value for column in board_columns)
            ],
        },
        'stats': {
            'scope_total': scoped.count(),
            'filtered': filtered_count,
        },
        'metrics': metrics,
        'active_filter_chips': _active_filter_chips(filters, owner_labels),
        'options': {
            'stages': STAGE_OPTIONS,
            'owners': owner_options,
            'sources': (
                {'value': 'all', 'label': '全部来源'},
                *(
                    {'value': value, 'label': SOURCE_LABELS.get(value, value)}
                    for value in source_values
                ),
            ),
            'due': tuple({'value': value, 'label': label} for value, label in DUE_OPTIONS),
            'sort': tuple({'value': value, 'label': label} for value, label in SORT_OPTIONS),
            'page_sizes': PAGE_SIZES,
        },
        'pagination': {
            'current': page_obj.number,
            'total_pages': paginator.num_pages,
            'total_items': paginator.count,
            'previous_url': (
                _url('console:opportunity_workspace_v2', filters, page=page_obj.previous_page_number())
                if page_obj.has_previous() else ''
            ),
            'next_url': (
                _url('console:opportunity_workspace_v2', filters, page=page_obj.next_page_number())
                if page_obj.has_next() else ''
            ),
        },
        'sort_urls': {
            'amount': _url('console:opportunity_workspace_v2', filters, sort='amount_desc', page=1),
            'follow_up': _url('console:opportunity_workspace_v2', filters, sort='follow_up', page=1),
            'close_date': _url('console:opportunity_workspace_v2', filters, sort='close_date', page=1),
        },
        'can_convert': can_sales(request.user, SalesCapability.CONVERT),
        'lead_workspace_url': reverse('console:lead_workspace_v2'),
    }


def _attachment_queryset_for_opportunity(*, site, user, opportunity: Opportunity):
    # Keep the list boundary identical to the existing CRM download/archive
    # endpoints: a routed detail never advertises a file those endpoints would
    # reject for the current user.
    from .sales_views import _attachment_queryset_for_user

    return _attachment_queryset_for_user(site=site, user=user).filter(
        opportunity=opportunity,
        status='active',
    )


def build_opportunity_detail_workspace_v2(*, request, site, opportunity_id: int, now=None) -> dict[str, object]:
    now = now or timezone.now()
    scoped = opportunity_queryset_for_user(
        Opportunity.objects.filter(site=site).select_related(
            'company', 'primary_contact', 'owner_user', 'team', 'source_submission'
        ),
        request.user,
    )
    opportunity = scoped.filter(pk=opportunity_id).first()
    if opportunity is None:
        return {'missing': True}

    owner_choices = _visible_owner_choices(site=site, user=request.user, scoped=scoped)
    source_values = _visible_source_values(scoped)
    filters = OpportunityWorkspaceV2Filters.from_querydict(
        request.GET,
        allowed_owner_ids=(int(choice['value']) for choice in owner_choices),
        allowed_sources=source_values,
        can_filter_unassigned=can_sales(request.user, SalesCapability.ASSIGN),
    )
    canonical_url = _url(
        'console:opportunity_workspace_detail_v2',
        filters,
        args=(opportunity.id,),
    )
    back_url = _url('console:opportunity_workspace_v2', filters)

    activities = list(
        activity_queryset_for_user(
            Activity.objects.filter(site=site, opportunity=opportunity).select_related('actor_user'),
            request.user,
        ).order_by('-occurred_at', '-id')[:100]
    )
    tasks = list(
        task_queryset_for_user(
            Task.objects.filter(site=site, opportunity=opportunity).select_related('owner_user', 'team'),
            request.user,
        ).order_by('status', 'due_at', 'id')[:100]
    )
    attachments = list(
        _attachment_queryset_for_opportunity(
            site=site,
            user=request.user,
            opportunity=opportunity,
        ).select_related('asset', 'uploaded_by_user').order_by('-created_at', '-id')[:100]
    )
    history = list(
        OpportunityStageHistory.objects.filter(opportunity=opportunity)
        .select_related('changed_by_user')
        .order_by('-changed_at', '-id')[:100]
    )
    source_lead_url = ''
    if opportunity.source_submission_id and lead_queryset_for_user(
        LeadSubmission.objects.filter(site=site, pk=opportunity.source_submission_id),
        request.user,
    ).exists():
        source_lead_url = reverse(
            'console:lead_workspace_detail_v2',
            args=[opportunity.source_submission_id],
        )

    can_write = can_sales(request.user, SalesCapability.WRITE, record=opportunity)
    can_assign = can_sales(request.user, SalesCapability.ASSIGN, record=opportunity)
    for choice in owner_choices:
        choice['selected'] = str(choice['value']) == str(opportunity.owner_user_id or '')
        choice['disabled'] = bool(choice['current_only'] and not choice['selected'])
    stage_targets = [
        {
            'value': target,
            'label': STAGE_LABELS.get(target, target),
            'requires_reason': target in TERMINAL_STAGES or opportunity.stage in TERMINAL_STAGES,
        }
        for target in OPPORTUNITY_STAGE_TRANSITIONS.get(opportunity.stage, frozenset())
    ]
    stage_order = {value: index for index, (value, _label) in enumerate(PIPELINE_STAGES)}
    stage_targets.sort(key=lambda item: stage_order.get(item['value'], 999))
    row = _row_payload(opportunity, filters=filters, now=now)
    return {
        'missing': False,
        'site': site,
        'filters': filters,
        'canonical_url': canonical_url,
        'back_url': back_url,
        'record': {
            **row,
            'source_channel': SOURCE_LABELS.get(opportunity.source_channel, opportunity.source_channel or '未填写'),
            'source_detail': (opportunity.source_detail or '').strip() or '未填写',
            'source_lead_id': opportunity.source_submission_id,
            'source_lead_url': source_lead_url,
            'owner_id': opportunity.owner_user_id,
            'value_amount': opportunity.value_amount,
            'currency': (opportunity.currency or 'USD').upper(),
            'expected_close_input': opportunity.expected_close_date.isoformat() if opportunity.expected_close_date else '',
            'next_follow_up_input': _format_datetime_input(opportunity.next_follow_up_at),
            'product_scope': (opportunity.product_scope or '').strip() or '未填写',
            'product_scope_input': (opportunity.product_scope or '').strip(),
            'capacity_target': (opportunity.capacity_target or '').strip() or '未填写',
            'capacity_target_input': (opportunity.capacity_target or '').strip(),
            'packaging_format': (opportunity.packaging_format or '').strip() or '未填写',
            'packaging_format_input': (opportunity.packaging_format or '').strip(),
            'next_step_input': (opportunity.next_step or '').strip(),
            'competitor': (opportunity.competitor or '').strip() or '未填写',
            'won_reason': (opportunity.won_reason or '').strip(),
            'lost_reason': (opportunity.lost_reason or '').strip(),
            'closed_at': opportunity.closed_at,
            'updated_at': opportunity.updated_at,
            'updated_at_version': opportunity_version_token(opportunity.updated_at),
        },
        'can_write': can_write,
        'can_assign': can_assign,
        'owner_choices': owner_choices,
        'stage_targets': stage_targets,
        'edit_url': reverse('console:opportunity_update', args=[opportunity.id]) if can_write else '',
        'stage_url': reverse('console:opportunity_stage', args=[opportunity.id]) if can_write else '',
        'activities': [
            {
                'id': item.id,
                'type_label': ACTIVITY_TYPE_LABELS.get(item.activity_type, item.activity_type),
                'subject': (item.subject or '').strip() or ACTIVITY_TYPE_LABELS.get(item.activity_type, '活动记录'),
                'body': (item.body or '').strip(),
                'actor': _user_label(item.actor_user),
                'occurred_at': item.occurred_at,
                'occurred_label': _format_datetime(item.occurred_at),
                'detail_url': reverse('console:activity_workspace_detail_v2', args=(item.pk,)),
            }
            for item in activities
        ],
        'tasks': [
            {
                'id': item.id,
                'title': item.title,
                'description': item.description,
                'status': item.status,
                'status_label': TASK_STATUS_LABELS.get(item.status, item.status),
                'status_tone': (
                    'green' if item.status == 'completed'
                    else 'secondary' if item.status == 'canceled'
                    else 'red' if item.due_at < now
                    else 'azure'
                ),
                'owner': _user_label(item.owner_user),
                'due_at': item.due_at,
                'due_label': _format_datetime(item.due_at),
                'outcome': (item.outcome or '').strip(),
                'detail_url': reverse('console:task_workspace_detail_v2', args=(item.pk,)),
            }
            for item in tasks
        ],
        'attachments': [
            {
                'id': item.id,
                'title': item.title or item.original_name or f'附件 #{item.id}',
                'original_name': item.original_name,
                'mime_type': item.mime_type,
                'file_size_bytes': item.file_size_bytes,
                'uploaded_by': _user_label(item.uploaded_by_user),
                'created_at': item.created_at,
                'download_url': reverse('console:crm_attachment_download', args=[item.id]),
            }
            for item in attachments
        ],
        'stage_history': [
            {
                'id': item.id,
                'from_label': STAGE_LABELS.get(item.from_stage, item.from_stage or '创建'),
                'to_label': STAGE_LABELS.get(item.to_stage, item.to_stage),
                'reason': (item.reason or '').strip() or '未记录说明',
                'actor': _user_label(item.changed_by_user),
                'changed_at': item.changed_at,
                'changed_label': _format_datetime(item.changed_at),
            }
            for item in history
        ],
    }
