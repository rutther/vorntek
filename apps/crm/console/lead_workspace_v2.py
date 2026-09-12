from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Iterable
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.paginator import Paginator
from django.db.models import (
    Case,
    Count,
    DateTimeField,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    TextField,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Lower, NullIf
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from leads.models import Activity, ConsentRecord, LeadEventOutbox, LeadSubmission, SavedView, Task
from sitecore.models import SiteLocale

from .access import (
    activity_queryset_for_user,
    assignee_queryset_for_user,
    lead_queryset_for_user,
    saved_view_queryset_for_user,
    shareable_sales_team_queryset_for_user,
    task_queryset_for_user,
)
from .leads_payload import lead_stage_label, lead_stage_tone
from .payloads import admin_locale_label, format_admin_datetime


QUERY_KEYS = frozenset({
    'q',
    'stage',
    'language',
    'source',
    'owner',
    'sla',
    'sort',
    'page',
    'page_size',
})
STAGES = ('all', 'new', 'contacted', 'qualified', 'won', 'lost', 'spam')
ACTIVE_STAGES = ('new', 'contacted', 'qualified')
ACTIONABLE_TASK_STATUSES = ('open', 'in_progress')
SLA_FILTERS = ('all', 'overdue', 'due_soon', 'scheduled', 'unplanned')
PAGE_SIZES = (20, 25, 50, 100)
DEFAULT_PAGE_SIZE = 25
SORT_KEYS = ('sla', 'newest', 'oldest', 'updated', 'name')
SAVED_FILTER_KEYS = frozenset({'q', 'stage', 'language', 'source', 'owner', 'sla', 'page_size'})
SAFE_TOKEN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

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
SOURCE_TONES = {
    'meta_ads': 'blue',
    'paid_search': 'blue',
    'organic': 'green',
    'whatsapp': 'green',
}
STAGE_OPTIONS = tuple(
    {'value': value, 'label': '全部阶段' if value == 'all' else lead_stage_label(value)}
    for value in STAGES
)
SLA_OPTIONS = (
    {'value': 'all', 'label': '全部 SLA'},
    {'value': 'overdue', 'label': '已超时'},
    {'value': 'due_soon', 'label': '24 小时内'},
    {'value': 'scheduled', 'label': '已安排'},
    {'value': 'unplanned', 'label': '未安排下一步'},
)
SORT_OPTIONS = (
    {'value': 'sla', 'label': 'SLA 优先'},
    {'value': 'newest', 'label': '最新提交'},
    {'value': 'oldest', 'label': '最早提交'},
    {'value': 'updated', 'label': '最近更新'},
    {'value': 'name', 'label': '姓名 A–Z'},
)


@dataclass(frozen=True, slots=True)
class LeadWorkspaceV2Filters:
    query: str = ''
    stage: str = 'all'
    language: str = 'all'
    source: str = 'all'
    owner: str = 'all'
    sla: str = 'all'
    sort: str = 'sla'
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @classmethod
    def from_querydict(
        cls,
        querydict,
        *,
        allowed_languages: Iterable[str] = (),
        allowed_sources: Iterable[str] = (),
        allowed_owner_ids: Iterable[int] = (),
    ) -> 'LeadWorkspaceV2Filters':
        """Parse only documented query keys and reject unrecognised values.

        The method deliberately reads each key by name instead of copying the
        QueryDict. This keeps unrelated or attacker-controlled GET parameters
        out of return links, pagination and record-to-record navigation.
        """

        language_values = {'all', 'shared', *allowed_languages}
        source_values = {'all', *SOURCE_LABELS, *allowed_sources}
        owner_values = {'all', 'unassigned', 'mine', *(str(value) for value in allowed_owner_ids)}

        query = _clean_search(querydict.get('q'))
        stage = _choice(querydict.get('stage'), STAGES, 'all')
        language = _token_choice(querydict.get('language'), language_values, 'all')
        source = _token_choice(querydict.get('source'), source_values, 'all')
        owner = _token_choice(querydict.get('owner'), owner_values, 'all')
        sla = _choice(querydict.get('sla'), SLA_FILTERS, 'all')
        sort = _choice(querydict.get('sort'), SORT_KEYS, 'sla')
        page = _positive_int(querydict.get('page'), 1)
        page_size = _positive_int(querydict.get('page_size'), DEFAULT_PAGE_SIZE)
        if page_size not in PAGE_SIZES:
            page_size = DEFAULT_PAGE_SIZE
        return cls(
            query=query,
            stage=stage,
            language=language,
            source=source,
            owner=owner,
            sla=sla,
            sort=sort,
            page=page,
            page_size=page_size,
        )

    def query_params(self, *, page: int | None = None) -> dict[str, str | int]:
        """Return the canonical, complete list/detail navigation state."""

        params: dict[str, str | int] = {
            'sort': self.sort,
            'page': page if page is not None else self.page,
            'page_size': self.page_size,
        }
        if self.query:
            params['q'] = self.query
        if self.stage != 'all':
            params['stage'] = self.stage
        if self.language != 'all':
            params['language'] = self.language
        if self.source != 'all':
            params['source'] = self.source
        if self.owner != 'all':
            params['owner'] = self.owner
        if self.sla != 'all':
            params['sla'] = self.sla
        return params


@dataclass(frozen=True, slots=True)
class _SortSpec:
    key: str
    fields: tuple[tuple[str, str], ...]


SORT_SPECS = {
    'sla': _SortSpec(
        key='sla',
        fields=(
            ('_v2_sla_bucket', 'asc'),
            ('_v2_sla_deadline', 'asc'),
            ('submitted_at', 'desc'),
            ('id', 'desc'),
        ),
    ),
    'newest': _SortSpec('newest', (('submitted_at', 'desc'), ('id', 'desc'))),
    'oldest': _SortSpec('oldest', (('submitted_at', 'asc'), ('id', 'asc'))),
    'updated': _SortSpec('updated', (('updated_at', 'desc'), ('id', 'desc'))),
    'name': _SortSpec('name', (('_v2_sort_name', 'asc'), ('id', 'asc'))),
}


def normalize_lead_saved_view_state(
    filters_value,
    sort_value=None,
    *,
    site,
    user,
    scoped: QuerySet[LeadSubmission] | None = None,
    locales=None,
    owners=None,
    source_values=None,
) -> dict:
    """Normalize persisted lead view state through the routed v2 parser.

    Saved JSON is data, never a URL fragment.  Only scalar values under the
    documented keys reach :class:`LeadWorkspaceV2Filters`; unknown keys are
    ignored and invalid values fall back to the parser defaults.  A known key
    containing a nested/list value is rejected because stringifying attacker-
    controlled structures would turn them into surprising search text.
    """

    if not isinstance(filters_value, dict):
        raise ValidationError('线索保存视图的筛选条件必须是对象。', code='filters_invalid')
    for key in SAVED_FILTER_KEYS | {'sort'}:
        if key in filters_value and not isinstance(filters_value[key], (str, int, float, bool, type(None))):
            raise ValidationError(
                f'线索保存视图字段 {key} 必须是单值。',
                code='filter_value_invalid',
            )

    scoped = scoped if scoped is not None else lead_queryset_for_user(
        LeadSubmission.objects.filter(site=site),
        user,
    )
    locales = list(locales) if locales is not None else list(
        SiteLocale.objects.filter(site=site, enabled=True).order_by('sort_order', 'locale_code')
    )
    owners = list(owners) if owners is not None else list(assignee_queryset_for_user(user, site=site))
    source_values = list(source_values) if source_values is not None else list(
        scoped.exclude(source_channel='')
        .order_by()
        .values_list('source_channel', flat=True)
        .distinct()
    )

    sort_candidate = filters_value.get('sort')
    if isinstance(sort_value, list) and sort_value:
        first = sort_value[0]
        sort_candidate = first.get('field') if isinstance(first, dict) else first
    parser_input = {
        key: filters_value.get(key)
        for key in SAVED_FILTER_KEYS
        if key in filters_value
    }
    parser_input['sort'] = sort_candidate
    parser_input['page'] = 1
    parsed = LeadWorkspaceV2Filters.from_querydict(
        parser_input,
        allowed_languages=(locale.locale_code for locale in locales),
        allowed_sources=source_values,
        allowed_owner_ids=(owner.id for owner in owners),
    )
    canonical_params = parsed.query_params(page=1)
    saved_filters = {
        key: value
        for key, value in canonical_params.items()
        if key in SAVED_FILTER_KEYS
        and not (key in {'stage', 'language', 'source', 'owner', 'sla'} and value == 'all')
    }
    return {
        'filters': saved_filters,
        'sort': [parsed.sort],
        'query_params': canonical_params,
    }


def lead_saved_view_payload(
    saved_view: SavedView,
    *,
    site,
    user,
    list_url: str,
    scoped: QuerySet[LeadSubmission] | None = None,
    locales=None,
    owners=None,
    source_values=None,
) -> dict:
    """Return a safe, directly applicable payload for one persisted view."""

    state = normalize_lead_saved_view_state(
        saved_view.filters_json,
        saved_view.sort_json,
        site=site,
        user=user,
        scoped=scoped,
        locales=locales,
        owners=owners,
        source_values=source_values,
    )
    return {
        'id': saved_view.id,
        'name': saved_view.name,
        'apply_url': _url_with_query(list_url, state['query_params']),
        'filters': state['filters'],
        'sort': state['sort'],
        'columns': saved_view.columns_json if isinstance(saved_view.columns_json, list) else [],
        'is_default': bool(saved_view.is_default and saved_view.user_id == user.pk),
        'is_shared': saved_view.is_shared,
        'team': (
            {'id': saved_view.team_id, 'label': saved_view.team.name}
            if saved_view.team_id and saved_view.team
            else None
        ),
    }


def build_lead_workspace_v2(
    request,
    *,
    site,
    lead_id: int | None = None,
    list_url: str | None = None,
    detail_url_name: str | None = 'console:lead_workspace_detail_v2',
    now=None,
) -> dict:
    """Build the first-render context for the routed v2 lead workspace.

    Every object query begins from ``lead_queryset_for_user``. The selected
    record is resolved from the same filtered and sorted queryset as the list,
    so a crafted URL cannot open a record outside the user's scope or silently
    jump outside the active queue.
    """

    now = now or timezone.now()
    reminder_hours = int(getattr(settings, 'SITEOS_LEAD_REMINDER_HOURS', 24))
    reminder_delta = timedelta(hours=reminder_hours)
    overdue_cutoff = now - reminder_delta
    due_soon_cutoff = now + timedelta(hours=24)

    scoped_base = lead_queryset_for_user(
        LeadSubmission.objects.filter(site=site).select_related(
            'form',
            'locale',
            'assignee',
            'team',
            'duplicate_of',
            'conversion__company',
            'conversion__contact',
            'conversion__opportunity',
        ),
        request.user,
    )
    actionable_task = task_queryset_for_user(
        Task.objects.filter(
            site=site,
            submission_id=OuterRef('pk'),
            status__in=ACTIONABLE_TASK_STATUSES,
        ),
        request.user,
    ).order_by('due_at', 'id')
    scoped = scoped_base.annotate(
        _v2_open_task_title=Subquery(
            actionable_task.values('title')[:1],
            output_field=TextField(),
        ),
        _v2_open_task_due_at=Subquery(
            actionable_task.values('due_at')[:1],
            output_field=DateTimeField(),
        ),
    )

    locales = list(
        SiteLocale.objects.filter(site=site, enabled=True).order_by('sort_order', 'locale_code')
    )
    owners = list(assignee_queryset_for_user(request.user, site=site))
    source_values = list(
        scoped.exclude(source_channel='')
        .order_by()
        .values_list('source_channel', flat=True)
        .distinct()
    )
    filters = LeadWorkspaceV2Filters.from_querydict(
        request.GET,
        allowed_languages=(locale.locale_code for locale in locales),
        allowed_sources=source_values,
        allowed_owner_ids=(owner.id for owner in owners),
    )

    filtered = _apply_filters(
        scoped,
        filters,
        request_user_id=request.user.id,
        now=now,
        overdue_cutoff=overdue_cutoff,
        due_soon_cutoff=due_soon_cutoff,
    )
    ordered, sort_spec = _apply_sort(
        filtered,
        filters.sort,
        now=now,
        reminder_delta=reminder_delta,
        overdue_cutoff=overdue_cutoff,
        due_soon_cutoff=due_soon_cutoff,
    )

    paginator = Paginator(ordered, filters.page_size)
    page_obj = paginator.get_page(filters.page)
    filters = replace(filters, page=page_obj.number)
    list_url = list_url or _default_list_url(request)

    rows = [
        _lead_row(
            lead,
            filters=filters,
            list_url=list_url,
            detail_url_name=detail_url_name,
            now=now,
            reminder_delta=reminder_delta,
        )
        for lead in page_obj.object_list
    ]

    selected_record = ordered.filter(pk=lead_id).first() if lead_id is not None else None
    selected = None
    previous_url = ''
    next_url = ''
    selected_page = None
    activities: list[dict] = []
    tasks: list[dict] = []
    task_history: list[dict] = []
    activity_total = 0
    task_total = 0
    task_history_total = 0

    if selected_record is not None:
        selected_page = _page_for_record(ordered, selected_record, sort_spec, filters.page_size)
        selected = _lead_detail(selected_record, now=now, reminder_delta=reminder_delta)
        previous_record = _neighbour(ordered, selected_record, sort_spec, after=False)
        next_record = _neighbour(ordered, selected_record, sort_spec, after=True)
        if previous_record is not None:
            previous_page = _page_for_record(ordered, previous_record, sort_spec, filters.page_size)
            previous_url = _detail_url(
                previous_record.id,
                filters=filters,
                page=previous_page,
                list_url=list_url,
                detail_url_name=detail_url_name,
            )
        if next_record is not None:
            next_page = _page_for_record(ordered, next_record, sort_spec, filters.page_size)
            next_url = _detail_url(
                next_record.id,
                filters=filters,
                page=next_page,
                list_url=list_url,
                detail_url_name=detail_url_name,
            )

        activity_qs = activity_queryset_for_user(
            Activity.objects.filter(site=site, submission=selected_record).select_related('actor_user'),
            request.user,
        ).order_by('-occurred_at', '-id')
        activity_total = activity_qs.count()
        activities = [_activity_item(item) for item in activity_qs[:20]]

        task_history_qs = task_queryset_for_user(
            Task.objects.filter(site=site, submission=selected_record).select_related(
                'owner_user', 'created_by_user'
            ),
            request.user,
        ).order_by('-updated_at', '-id')
        task_history_total = task_history_qs.count()
        task_history = [_task_item(item, now=now) for item in task_history_qs[:20]]
        task_qs = task_history_qs.filter(
            status__in=ACTIONABLE_TASK_STATUSES,
        ).order_by('due_at', '-priority', 'id')
        task_total = task_qs.count()
        tasks = [_task_item(item, now=now) for item in task_qs[:20]]

    stats = _stats(
        scoped_base,
        filtered_total=paginator.count,
        request_user_id=request.user.id,
        now=now,
        overdue_cutoff=overdue_cutoff,
        due_soon_cutoff=due_soon_cutoff,
    )
    pagination = _pagination_context(page_obj, filters=filters, list_url=list_url)
    language_options = [
        {'value': 'all', 'label': '全部语言'},
        {'value': 'shared', 'label': '未绑定 / 全站共享'},
        *(
            {
                'value': locale.locale_code,
                'label': f'{admin_locale_label(locale)} ({locale.locale_code})',
            }
            for locale in locales
        ),
    ]
    source_options = [
        {'value': 'all', 'label': '全部来源'},
        *(
            {'value': value, 'label': SOURCE_LABELS.get(value, value)}
            for value in sorted(set(source_values) | set(SOURCE_LABELS))
        ),
    ]
    owner_options = [
        {'value': 'all', 'label': '全部负责人'},
        {'value': 'unassigned', 'label': '未分配'},
        {'value': 'mine', 'label': '我负责的'},
        *(
            {'value': str(owner.id), 'label': _user_label(owner)}
            for owner in owners
        ),
    ]
    visible_saved_views = (
        saved_view_queryset_for_user(SavedView.objects.all(), request.user, site=site)
        .filter(scope='leads')
        .select_related('user', 'team')
        .order_by('name', 'id')
    )
    saved_views = []
    for saved_view in visible_saved_views:
        try:
            saved_views.append(
                lead_saved_view_payload(
                    saved_view,
                    site=site,
                    user=request.user,
                    list_url=list_url,
                    scoped=scoped,
                    locales=locales,
                    owners=owners,
                    source_values=source_values,
                )
            )
        except ValidationError:
            # Historical rows may predate the v2 whitelist.  Keep the name
            # visible for cleanup, but never manufacture an apply URL from
            # malformed JSON.
            saved_views.append({
                'id': saved_view.id,
                'name': saved_view.name,
                'apply_url': '',
                'filters': {},
                'sort': ['sla'],
                'columns': [],
                'is_default': bool(
                    saved_view.is_default and saved_view.user_id == request.user.pk
                ),
                'is_shared': saved_view.is_shared,
                'team': (
                    {'id': saved_view.team_id, 'label': saved_view.team.name}
                    if saved_view.team_id and saved_view.team
                    else None
                ),
                'invalid': True,
            })
    saved_views.sort(
        key=lambda item: (
            not item['is_default'],
            str(item['name']).casefold(),
            item['id'],
        )
    )
    shareable_teams = list(shareable_sales_team_queryset_for_user(request.user, site=site))

    return {
        'title': '线索工作区',
        'description': '按 SLA 分诊、分配并连续处置销售线索。',
        'query_keys': tuple(sorted(QUERY_KEYS)),
        'filters': filters,
        'filter_values': filters.query_params(),
        'quick_views': {
            'all_active': (
                not filters.query
                and filters.stage == 'all'
                and filters.language == 'all'
                and filters.source == 'all'
                and filters.owner == 'all'
                and filters.sla == 'all'
            ),
        },
        'active_filter_chips': _active_filter_chips(
            filters,
            list_url=list_url,
            locales=locales,
            owners=owners,
        ),
        'options': {
            'stages': list(STAGE_OPTIONS),
            'languages': language_options,
            'sources': source_options,
            'owners': owner_options,
            'sla': list(SLA_OPTIONS),
            'sort': list(SORT_OPTIONS),
            'page_sizes': list(PAGE_SIZES),
        },
        'stats': stats,
        'rows': rows,
        'page_obj': page_obj,
        'pagination': pagination,
        'list_url': _url_with_query(list_url, filters.query_params(page=page_obj.number)),
        'clear_filters_url': _url_with_query(
            list_url,
            {'sort': 'sla', 'page': 1, 'page_size': filters.page_size},
        ),
        'selected': selected,
        'selected_missing': lead_id is not None and selected_record is None,
        'selected_page': selected_page,
        'previous_url': previous_url,
        'next_url': next_url,
        'activities': activities,
        'activity_total': activity_total,
        'tasks': tasks,
        'task_total': task_total,
        'task_history': task_history,
        'task_history_total': task_history_total,
        'saved_views': saved_views,
        'saved_view': {
            'save_url': reverse('console:saved_view_save'),
            'shareable_teams': [
                {'id': team.id, 'label': team.name}
                for team in shareable_teams
            ],
            'allowed_filter_fields': sorted(SAVED_FILTER_KEYS),
        },
    }


def _apply_filters(
    queryset: QuerySet[LeadSubmission],
    filters: LeadWorkspaceV2Filters,
    *,
    request_user_id: int,
    now,
    overdue_cutoff,
    due_soon_cutoff,
) -> QuerySet[LeadSubmission]:
    if filters.stage != 'all':
        queryset = queryset.filter(stage=filters.stage)
    if filters.language == 'shared':
        queryset = queryset.filter(locale__isnull=True)
    elif filters.language != 'all':
        queryset = queryset.filter(locale__locale_code=filters.language)
    if filters.source != 'all':
        queryset = queryset.filter(source_channel=filters.source)
    if filters.owner == 'unassigned':
        queryset = queryset.filter(assignee__isnull=True)
        if filters.stage == 'all':
            queryset = queryset.filter(stage__in=ACTIVE_STAGES)
    elif filters.owner == 'mine':
        queryset = queryset.filter(assignee_id=request_user_id)
        if filters.stage == 'all':
            queryset = queryset.filter(stage__in=ACTIVE_STAGES)
    elif filters.owner.isdigit():
        queryset = queryset.filter(assignee_id=int(filters.owner))
    if filters.sla == 'overdue':
        queryset = queryset.filter(_overdue_q(now=now, overdue_cutoff=overdue_cutoff))
    elif filters.sla == 'due_soon':
        queryset = queryset.filter(
            stage__in=ACTIVE_STAGES,
            next_follow_up_at__gt=now,
            next_follow_up_at__lte=due_soon_cutoff,
        )
    elif filters.sla == 'scheduled':
        queryset = queryset.filter(
            stage__in=ACTIVE_STAGES,
            next_follow_up_at__gt=due_soon_cutoff,
        )
    elif filters.sla == 'unplanned':
        queryset = queryset.filter(stage__in=ACTIVE_STAGES, next_follow_up_at__isnull=True).exclude(
            stage='new', submitted_at__lte=overdue_cutoff
        )
    if filters.query:
        query = filters.query
        queryset = queryset.filter(
            Q(full_name__icontains=query)
            | Q(company__icontains=query)
            | Q(country__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
            | Q(message__icontains=query)
            | Q(follow_up_notes__icontains=query)
            | Q(source_detail__icontains=query)
            | Q(form__name__icontains=query)
            | Q(form__code__icontains=query)
            | Q(payload_json__product_category__icontains=query)
            | Q(payload_json__product__icontains=query)
            | Q(payload_json__product_scope__icontains=query)
            | Q(payload_json__capacity__icontains=query)
            | Q(payload_json__capacity_target__icontains=query)
        )
    return queryset


def _apply_sort(
    queryset: QuerySet[LeadSubmission],
    sort_key: str,
    *,
    now,
    reminder_delta,
    overdue_cutoff,
    due_soon_cutoff,
) -> tuple[QuerySet[LeadSubmission], _SortSpec]:
    future = now + timedelta(days=36500)
    first_response_deadline = ExpressionWrapper(
        F('submitted_at') + reminder_delta,
        output_field=DateTimeField(),
    )
    queryset = queryset.annotate(
        _v2_sla_deadline=Case(
            When(next_follow_up_at__isnull=False, then=F('next_follow_up_at')),
            When(stage='new', then=first_response_deadline),
            default=Value(future),
            output_field=DateTimeField(),
        ),
        _v2_sla_bucket=Case(
            When(stage__in=('won', 'lost', 'spam'), then=Value(5)),
            When(_overdue_q(now=now, overdue_cutoff=overdue_cutoff), then=Value(0)),
            When(
                stage__in=ACTIVE_STAGES,
                next_follow_up_at__gt=now,
                next_follow_up_at__lte=due_soon_cutoff,
                then=Value(1),
            ),
            When(stage='new', next_follow_up_at__isnull=True, then=Value(2)),
            When(stage__in=ACTIVE_STAGES, next_follow_up_at__isnull=True, then=Value(3)),
            default=Value(4),
        ),
        _v2_sort_name=Lower(
            Coalesce(
                NullIf(F('full_name'), Value('')),
                NullIf(F('company'), Value('')),
                NullIf(F('email'), Value('')),
                Value(''),
                output_field=TextField(),
            )
        ),
    )
    spec = SORT_SPECS.get(sort_key, SORT_SPECS['sla'])
    return queryset.order_by(*_order_terms(spec)), spec


def _stats(
    scoped: QuerySet[LeadSubmission],
    *,
    filtered_total: int,
    request_user_id: int,
    now,
    overdue_cutoff,
    due_soon_cutoff,
) -> dict[str, int]:
    values = scoped.aggregate(
        total=Count('id'),
        new=Count('id', filter=Q(stage='new')),
        active=Count('id', filter=Q(stage__in=ACTIVE_STAGES)),
        mine=Count(
            'id',
            filter=Q(stage__in=ACTIVE_STAGES, assignee_id=request_user_id),
        ),
        overdue=Count('id', filter=_overdue_q(now=now, overdue_cutoff=overdue_cutoff)),
        due_soon=Count(
            'id',
            filter=Q(
                stage__in=ACTIVE_STAGES,
                next_follow_up_at__gt=now,
                next_follow_up_at__lte=due_soon_cutoff,
            ),
        ),
        unassigned=Count(
            'id',
            filter=Q(stage__in=ACTIVE_STAGES, assignee__isnull=True),
        ),
        converted=Count('id', filter=Q(conversion__isnull=False)),
    )
    return {key: int(value or 0) for key, value in values.items()} | {
        'filtered': int(filtered_total),
    }


def _lead_row(
    lead: LeadSubmission,
    *,
    filters: LeadWorkspaceV2Filters,
    list_url: str,
    detail_url_name: str | None,
    now,
    reminder_delta,
) -> dict:
    payload = lead.payload_json or {}
    product = _payload_text(payload, 'product_category', 'product', 'product_scope')
    industrial = bool(payload.get('business_line'))
    capacity = _payload_text(payload, 'application_context') if industrial else _payload_text(payload, 'capacity', 'capacity_target', 'production_capacity')
    return {
        'id': lead.id,
        'title': _lead_title(lead),
        'company': lead.company or '未填写企业',
        'country': lead.country or '未填写国家',
        'product': product or '未填写产品',
        'capacity': capacity or ('未填写应用背景' if industrial else '未填写产能'),
        'context_label': '应用背景' if industrial else '产能目标',
        'language': _language_meta(lead),
        'stage': {
            'value': lead.stage,
            'label': lead_stage_label(lead.stage),
            'tone': lead_stage_tone(lead.stage),
        },
        'source': _source_meta(lead.source_channel, lead.source_detail),
        'owner': _user_label(lead.assignee) if lead.assignee else '未分配',
        'contact': lead.email or lead.phone or '未填写联系方式',
        'sla': _sla_meta(lead, now=now, reminder_delta=reminder_delta),
        'next_step': _next_step_meta(lead)['text'],
        'submitted_at': lead.submitted_at,
        'submitted_at_label': format_admin_datetime(lead.submitted_at),
        'duplicate': bool(lead.duplicate_of_id),
        'spam_risk': int(lead.spam_score or 0),
        'detail_url': _detail_url(
            lead.id,
            filters=filters,
            page=filters.page,
            list_url=list_url,
            detail_url_name=detail_url_name,
        ),
    }


def _lead_detail(lead: LeadSubmission, *, now, reminder_delta) -> dict:
    payload = lead.payload_json or {}
    consent_snapshot = lead.consent_json or {}
    attribution = lead.utm_json or {}
    qualification = lead.qualification_json or {}
    try:
        conversion = lead.conversion
    except ObjectDoesNotExist:
        conversion = None
    conversion_context = None
    if conversion is not None:
        conversion_context = {
            'converted_at': conversion.converted_at,
            'converted_at_label': format_admin_datetime(conversion.converted_at),
            'company': {
                'id': conversion.company_id,
                'name': conversion.company.name,
            } if conversion.company else None,
            'contact': {
                'id': conversion.contact_id,
                'name': conversion.contact.full_name,
            } if conversion.contact else None,
            'opportunity': {
                'id': conversion.opportunity_id,
                'name': conversion.opportunity.name,
                'stage': conversion.opportunity.stage,
            } if conversion.opportunity else None,
            'metadata': conversion.metadata_json or {},
        }
    consent_records = [
        {
            'purpose': record.purpose,
            'decision': record.decision,
            'source': record.source,
            'policy_version': record.policy_version or '未记录',
            'captured_at': record.captured_at,
            'captured_at_label': format_admin_datetime(record.captured_at),
        }
        for record in ConsentRecord.objects.filter(submission=lead).order_by('-captured_at', '-id')[:20]
    ]
    event_deliveries = [
        {
            'event_name': event.event_name,
            'stage_key': event.stage_key,
            'event_id': event.event_id,
            'status': event.status,
            'delivery_mode': event.delivery_mode,
            'attempts': event.attempts,
            'match_status': event.match_status,
            'provider_request_id': event.provider_request_id or '未返回',
            'last_error': event.last_error,
            'updated_at_label': format_admin_datetime(event.updated_at),
        }
        for event in LeadEventOutbox.objects.filter(submission=lead).order_by('-created_at', '-id')[:20]
    ]
    return {
        'id': lead.id,
        'title': _lead_title(lead),
        'company': lead.company or '未填写企业',
        'country': lead.country or '未填写国家',
        'language': _language_meta(lead),
        'source': _source_meta(lead.source_channel, lead.source_detail),
        'product': _payload_text(payload, 'product_category', 'product', 'product_scope') or '未填写',
        'capacity': (_payload_text(payload, 'application_context') if payload.get('business_line') else _payload_text(payload, 'capacity', 'capacity_target', 'production_capacity')) or '未填写',
        'context_label': '应用背景' if payload.get('business_line') else '产能目标',
        'industrial': bool(payload.get('business_line')),
        'project': {
            'business_line': _payload_text(payload, 'business_line'),
            'application_context': _payload_text(payload, 'application_context') or '未填写',
            'inquiry_type': _payload_text(payload, 'inquiry_type', 'project_type') or '未填写',
            'product': _payload_text(payload, 'product_category', 'product', 'product_scope') or '未填写',
            'container': _payload_text(payload, 'container_package', 'package', 'container') or '未填写',
            'capacity': _payload_text(payload, 'capacity', 'capacity_target', 'production_capacity') or '未填写',
            'timeline': _payload_text(payload, 'timeline', 'purchase_timeline') or '未填写',
            'condition': _payload_text(payload, 'project_condition', 'factory_condition') or '未填写',
        },
        'stage': {
            'value': lead.stage,
            'label': lead_stage_label(lead.stage),
            'tone': lead_stage_tone(lead.stage),
        },
        'owner': {
            'id': lead.assignee_id,
            'label': _user_label(lead.assignee) if lead.assignee else '未分配',
        },
        'team': {
            'id': lead.team_id,
            'label': lead.team.name,
        } if lead.team else None,
        'sla': _sla_meta(lead, now=now, reminder_delta=reminder_delta),
        'next_step': _next_step_meta(lead),
        'follow_up_notes': lead.follow_up_notes,
        'qualification': {
            'score': int(lead.qualification_score or 0),
            'notes': lead.qualification_notes or '暂无资格判断说明',
            'overridden': bool(lead.qualification_overridden),
            'items': _qualification_items(qualification),
            'raw': qualification,
        },
        'conversion': conversion_context,
        'email': lead.email or '未填写',
        'phone': lead.phone or '未填写',
        'message': lead.message or '未填写留言',
        'form': {
            'id': lead.form_id,
            'name': lead.form.name,
            'code': lead.form.code,
        },
        'submitted_at': lead.submitted_at,
        'submitted_at_label': format_admin_datetime(lead.submitted_at),
        'updated_at': lead.updated_at,
        'updated_at_label': format_admin_datetime(lead.updated_at),
        'updated_at_iso': lead.updated_at.isoformat(),
        'source_url': lead.source_url,
        'referrer_url': lead.referrer_url,
        'attribution': [
            {'key': str(key), 'value': str(value)}
            for key, value in sorted(attribution.items())
            if value not in (None, '', [], {})
        ],
        'consent': {
            'snapshot': [
                {'purpose': str(key), 'decision': 'granted' if bool(value) else 'denied'}
                for key, value in sorted(consent_snapshot.items())
                if isinstance(value, bool)
            ],
            'records': consent_records,
        },
        'technical': {
            'submission_key': str(lead.submission_key),
            'notification_status': lead.notification_status,
            'notification_error': lead.notification_error,
            'duplicate_of_id': lead.duplicate_of_id,
            'spam_score': int(lead.spam_score or 0),
            'spam_reason': lead.spam_reason,
            'event_deliveries': event_deliveries,
        },
        'duplicate_of_id': lead.duplicate_of_id,
        'spam_score': int(lead.spam_score or 0),
        'spam_reason': lead.spam_reason,
        'payload': payload,
    }


def _activity_item(item: Activity) -> dict:
    labels = {
        'call': '电话',
        'email': '邮件',
        'whatsapp': 'WhatsApp',
        'meeting': '会议',
        'note': '内部备注',
        'site_visit': '现场拜访',
        'task': '任务',
        'system': '系统记录',
        'stage_change': '阶段变更',
        'assignment': '负责人分配',
        'conversion': '线索转换',
        'file': '文件',
    }
    direction_labels = {
        'internal': '内部',
        'outbound': '外呼',
        'inbound': '客户发起',
    }
    return {
        'id': item.id,
        'type': item.activity_type,
        'type_label': labels.get(item.activity_type, item.activity_type),
        'direction_code': item.direction,
        'direction': direction_labels.get(item.direction, '其他'),
        'subject': item.subject or labels.get(item.activity_type, '活动记录'),
        'body': item.body,
        'actor': _user_label(item.actor_user) if item.actor_user else '系统',
        'occurred_at': item.occurred_at,
        'occurred_at_label': format_admin_datetime(item.occurred_at),
    }


def _task_item(item: Task, *, now) -> dict:
    return {
        'id': item.id,
        'title': item.title,
        'description': item.description,
        'type': item.task_type,
        'priority': item.priority,
        'status': item.status,
        'owner': _user_label(item.owner_user),
        'due_at': item.due_at,
        'due_at_label': format_admin_datetime(item.due_at),
        'overdue': item.status in ACTIONABLE_TASK_STATUSES and item.due_at <= now,
        'completed_at': item.completed_at,
        'outcome': item.outcome,
    }


def _pagination_context(page_obj, *, filters: LeadWorkspaceV2Filters, list_url: str) -> dict:
    pages = []
    for page_number in page_obj.paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1):
        if page_number == page_obj.paginator.ELLIPSIS:
            pages.append({'label': str(page_number), 'ellipsis': True})
        else:
            pages.append({
                'label': str(page_number),
                'number': page_number,
                'active': page_number == page_obj.number,
                'url': _url_with_query(list_url, filters.query_params(page=page_number)),
            })
    return {
        'number': page_obj.number,
        'num_pages': page_obj.paginator.num_pages,
        'current_page': page_obj.number,
        'total_pages': page_obj.paginator.num_pages,
        'total': page_obj.paginator.count,
        'start_index': page_obj.start_index() if page_obj.paginator.count else 0,
        'end_index': page_obj.end_index() if page_obj.paginator.count else 0,
        'has_previous': page_obj.has_previous(),
        'has_next': page_obj.has_next(),
        'previous_url': (
            _url_with_query(list_url, filters.query_params(page=page_obj.previous_page_number()))
            if page_obj.has_previous()
            else ''
        ),
        'next_url': (
            _url_with_query(list_url, filters.query_params(page=page_obj.next_page_number()))
            if page_obj.has_next()
            else ''
        ),
        'pages': pages,
    }


def _active_filter_chips(
    filters: LeadWorkspaceV2Filters,
    *,
    list_url: str,
    locales,
    owners,
) -> list[dict[str, str]]:
    locale_labels = {
        locale.locale_code: f'{admin_locale_label(locale)} ({locale.locale_code})'
        for locale in locales
    }
    locale_labels['shared'] = '未绑定 / 全站共享'
    owner_labels = {str(owner.id): _user_label(owner) for owner in owners}
    owner_labels |= {'unassigned': '未分配', 'mine': '我负责的'}
    stage_labels = {item['value']: item['label'] for item in STAGE_OPTIONS}
    sla_labels = {item['value']: item['label'] for item in SLA_OPTIONS}
    chips: list[dict[str, str]] = []
    definitions = (
        ('query', 'q', f'搜索：“{filters.query}”' if filters.query else '', ''),
        ('stage', 'stage', f'阶段：{stage_labels.get(filters.stage, filters.stage)}', 'all'),
        ('language', 'language', f'语言：{locale_labels.get(filters.language, filters.language)}', 'all'),
        ('source', 'source', f'来源：{SOURCE_LABELS.get(filters.source, filters.source)}', 'all'),
        ('owner', 'owner', f'负责人：{owner_labels.get(filters.owner, filters.owner)}', 'all'),
        ('sla', 'sla', f'SLA：{sla_labels.get(filters.sla, filters.sla)}', 'all'),
    )
    for field_name, query_name, label, default in definitions:
        current = getattr(filters, field_name)
        if current == default:
            continue
        updated = replace(filters, **{field_name: default, 'page': 1})
        chips.append({
            'key': query_name,
            'label': label,
            'remove_url': _url_with_query(list_url, updated.query_params(page=1)),
        })
    return chips


def _neighbour(
    queryset: QuerySet[LeadSubmission],
    current: LeadSubmission,
    spec: _SortSpec,
    *,
    after: bool,
):
    condition = _lexicographic_condition(current, spec, after=after)
    return queryset.filter(condition).order_by(*_order_terms(spec, reverse=not after)).first()


def _page_for_record(
    queryset: QuerySet[LeadSubmission],
    record: LeadSubmission,
    spec: _SortSpec,
    page_size: int,
) -> int:
    records_before = queryset.filter(_lexicographic_condition(record, spec, after=False)).count()
    return (records_before // page_size) + 1


def _lexicographic_condition(current: LeadSubmission, spec: _SortSpec, *, after: bool) -> Q:
    result = Q(pk__in=[])
    equal_prefix = Q()
    for field_name, direction in spec.fields:
        value = getattr(current, field_name)
        comparison = 'gt' if (direction == 'asc') == after else 'lt'
        result |= equal_prefix & Q(**{f'{field_name}__{comparison}': value})
        equal_prefix &= Q(**{field_name: value})
    return result


def _order_terms(spec: _SortSpec, *, reverse: bool = False) -> list:
    terms = []
    for field_name, direction in spec.fields:
        ascending = direction == 'asc'
        if reverse:
            ascending = not ascending
        terms.append(F(field_name).asc() if ascending else F(field_name).desc())
    return terms


def _detail_url(
    lead_id: int,
    *,
    filters: LeadWorkspaceV2Filters,
    page: int,
    list_url: str,
    detail_url_name: str | None,
) -> str:
    base = ''
    if detail_url_name:
        try:
            base = reverse(detail_url_name, args=[lead_id])
        except NoReverseMatch:
            base = ''
    params = filters.query_params(page=page)
    if not base:
        base = list_url
        params = {'lead_id': lead_id, **params}
    return _url_with_query(base, params)


def _default_list_url(request) -> str:
    try:
        return reverse('console:lead_workspace_v2')
    except NoReverseMatch:
        return request.path


def _url_with_query(base_url: str, params: dict[str, str | int]) -> str:
    separator = '&' if '?' in base_url else '?'
    return f'{base_url}{separator}{urlencode(params)}' if params else base_url


def _overdue_q(*, now, overdue_cutoff) -> Q:
    return Q(stage__in=ACTIVE_STAGES) & (
        Q(next_follow_up_at__lte=now)
        | Q(stage='new', next_follow_up_at__isnull=True, submitted_at__lte=overdue_cutoff)
    )


def _sla_meta(lead: LeadSubmission, *, now, reminder_delta) -> dict:
    if lead.stage in {'won', 'lost', 'spam'}:
        return {'code': 'closed', 'label': '已完成', 'tone': 'slate', 'due_at': None, 'due_at_label': '不适用'}
    due_at = lead.next_follow_up_at
    if due_at is None and lead.stage == 'new':
        due_at = lead.submitted_at + reminder_delta
    if due_at is None:
        return {'code': 'unplanned', 'label': '未安排下一步', 'tone': 'amber', 'due_at': None, 'due_at_label': '未安排'}
    if due_at <= now:
        return {
            'code': 'overdue',
            'label': '已超时',
            'tone': 'red',
            'due_at': due_at,
            'due_at_label': format_admin_datetime(due_at),
        }
    if due_at <= now + timedelta(hours=24):
        return {
            'code': 'due_soon',
            'label': '24 小时内',
            'tone': 'amber',
            'due_at': due_at,
            'due_at_label': format_admin_datetime(due_at),
        }
    return {
        'code': 'scheduled',
        'label': '已安排',
        'tone': 'green',
        'due_at': due_at,
        'due_at_label': format_admin_datetime(due_at),
    }


def _lead_title(lead: LeadSubmission) -> str:
    return lead.full_name or lead.company or lead.email or lead.phone or f'线索 #{lead.id}'


def _language_meta(lead: LeadSubmission) -> dict[str, str]:
    if not lead.locale:
        return {'code': 'shared', 'label': '未绑定 / 全站共享', 'direction': 'ltr'}
    return {
        'code': lead.locale.locale_code,
        'label': f'{admin_locale_label(lead.locale)} ({lead.locale.locale_code})',
        'direction': lead.locale.direction,
    }


def _source_meta(value: str, detail: str = '') -> dict[str, str]:
    return {
        'value': value or 'website',
        'label': SOURCE_LABELS.get(value, value or '网站'),
        'tone': SOURCE_TONES.get(value, 'slate'),
        'detail': detail or '未记录来源说明',
    }


def _next_step_meta(lead: LeadSubmission) -> dict:
    task_title = str(getattr(lead, '_v2_open_task_title', '') or '').strip()
    task_due_at = getattr(lead, '_v2_open_task_due_at', None)
    if task_title:
        due_label = format_admin_datetime(task_due_at) if task_due_at else '未安排'
        return {
            'text': f'{task_title} · {due_label}',
            'title': task_title,
            'at': task_due_at,
            'at_label': due_label,
            'source': 'task',
        }
    payload = lead.payload_json or {}
    qualification = lead.qualification_json or {}
    text = (
        _payload_text(payload, 'next_step')
        or _payload_text(qualification, 'next_step')
        or (lead.follow_up_notes or '').strip()
        or '尚未记录下一步'
    )
    return {
        'text': text,
        'title': '',
        'at': lead.next_follow_up_at,
        'at_label': (
            format_admin_datetime(lead.next_follow_up_at)
            if lead.next_follow_up_at
            else '未安排'
        ),
        'source': 'lead',
    }


def _qualification_items(values: dict) -> list[dict[str, str]]:
    labels = {
        'budget': '预算',
        'authority': '决策权',
        'need': '需求',
        'timeline': '时间表',
        'product_fit': '产品匹配',
        'capacity_fit': '产能匹配',
        'contactable': '可联系',
        'complete': '资料完整',
    }
    return [
        {
            'key': str(key),
            'label': labels.get(str(key), str(key).replace('_', ' ')),
            'value': _display_value(value),
        }
        for key, value in list(values.items())[:12]
    ]


def _display_value(value) -> str:
    if value is True:
        return '是'
    if value is False:
        return '否'
    if value is None or value == '':
        return '未设置'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(', ', ': '))
    return str(value)


def _payload_text(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ''


def _user_label(user) -> str:
    full_name = user.get_full_name().strip() if hasattr(user, 'get_full_name') else ''
    return full_name or user.get_username()


def _clean_search(value) -> str:
    return ' '.join(str(value or '').strip().split())[:120]


def _choice(value, allowed: Iterable[str], default: str) -> str:
    candidate = str(value or '').strip()
    return candidate if candidate in allowed else default


def _token_choice(value, allowed: Iterable[str], default: str) -> str:
    candidate = str(value or '').strip()
    if not SAFE_TOKEN.fullmatch(candidate):
        return default
    return candidate if candidate in allowed else default


def _positive_int(value, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default
