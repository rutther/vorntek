from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Iterable
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, F, OuterRef, Q, Subquery, Sum
from django.urls import reverse
from django.utils import timezone

from leads.crm_relationships import (
    all_linked_targets_in_scope_q,
    linked_targets_match_record_site_q,
)
from leads.models import (
    Activity,
    Company,
    Contact,
    CrmAttachment,
    LeadConversion,
    LeadSubmission,
    Opportunity,
    SalesTeam,
    Task,
)

from .access import (
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    activity_queryset_for_user,
    assignee_queryset_for_user,
    contact_queryset_for_user,
    crm_owned_queryset_for_user,
    lead_queryset_for_user,
    opportunity_queryset_for_user,
    task_queryset_for_user,
    user_role_keys,
)
from .account_versions import account_version_token
from .capabilities import SalesCapability, can_sales


PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 25
SAFE_TOKEN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
COMPANY_STATUSES = {
    'prospect': ('潜在客户', 'azure'),
    'customer': ('客户', 'green'),
    'inactive': ('停用', 'secondary'),
}
CONTACT_STATUSES = {
    'active': ('有效', 'green'),
    'inactive': ('停用', 'secondary'),
}
COMPANY_SORTS = {
    'name': '企业名称',
    'recent': '最近活动',
    'opportunities': '开放机会',
    'updated': '最近更新',
}
CONTACT_SORTS = {
    'name': '联系人姓名',
    'recent': '最近活动',
    'opportunities': '开放机会',
    'updated': '最近更新',
}
OPEN_OPPORTUNITY_STAGES = (
    'qualification', 'discovery', 'solution', 'quotation', 'negotiation', 'on_hold',
)


def _positive_int(value, default: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return resolved if resolved > 0 else default


def _clean_search(value) -> str:
    return ' '.join(str(value or '').split())[:160]


def _choice(value, choices: Iterable[str], default: str) -> str:
    resolved = str(value or '').strip().lower()
    return resolved if resolved in set(choices) else default


def _token_choice(value, choices: Iterable[str], default: str) -> str:
    resolved = str(value or '').strip()
    if not SAFE_TOKEN.fullmatch(resolved):
        return default
    return resolved if resolved in set(choices) else default


def _user_label(user) -> str:
    if user is None:
        return '未分配'
    return (user.get_full_name() or user.get_username()).strip()


def _format_datetime(value) -> str:
    if value is None:
        return ''
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')


def _format_money(amount: Decimal | None, currency: str) -> str:
    if amount is None:
        return ''
    code = (currency or 'USD').strip().upper() or 'USD'
    return f'{code} {amount:,.2f}'


@dataclass(frozen=True, slots=True)
class AccountListFilters:
    query: str = ''
    status: str = 'all'
    owner: str = 'all'
    team: str = 'all'
    company: str = 'all'
    sort: str = 'name'
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @classmethod
    def from_querydict(
        cls,
        querydict,
        *,
        status_keys: Iterable[str],
        sort_keys: Iterable[str],
        owner_ids: Iterable[int],
        team_ids: Iterable[int],
        company_ids: Iterable[int] = (),
        can_filter_unassigned: bool,
    ) -> 'AccountListFilters':
        owner_choices = {'all', 'mine', *(str(item) for item in owner_ids)}
        if can_filter_unassigned:
            owner_choices.add('unassigned')
        size = _positive_int(querydict.get('page_size'), DEFAULT_PAGE_SIZE)
        if size not in PAGE_SIZES:
            size = DEFAULT_PAGE_SIZE
        return cls(
            query=_clean_search(querydict.get('q')),
            status=_choice(querydict.get('status'), ('all', *status_keys), 'all'),
            owner=_token_choice(querydict.get('owner'), owner_choices, 'all'),
            team=_token_choice(
                querydict.get('team'), {'all', *(str(item) for item in team_ids)}, 'all'
            ),
            company=_token_choice(
                querydict.get('company'), {'all', *(str(item) for item in company_ids)}, 'all'
            ),
            sort=_choice(querydict.get('sort'), sort_keys, 'name'),
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
        if self.status != 'all':
            params['status'] = self.status
        if self.owner != 'all':
            params['owner'] = self.owner
        if self.team != 'all':
            params['team'] = self.team
        if self.company != 'all':
            params['company'] = self.company
        return params


def _url(route_name: str, filters: AccountListFilters, *, args=(), **changes) -> str:
    resolved = replace(filters, **changes) if changes else filters
    return f'{reverse(route_name, args=args)}?{urlencode(resolved.query_params())}'


def _visible_owner_choices(*, site, user, scoped) -> list[dict[str, object]]:
    candidates = list(assignee_queryset_for_user(user, site=site))
    candidate_ids = {item.pk for item in candidates}
    historical_ids = set(
        scoped.exclude(owner_user_id__isnull=True)
        .order_by().values_list('owner_user_id', flat=True).distinct()
    )
    historical = list(
        get_user_model().objects.filter(pk__in=historical_ids - candidate_ids).order_by('username')
    )
    return [
        {'value': str(item.pk), 'label': _user_label(item), 'active': item.is_active, 'historical': False}
        for item in candidates
    ] + [
        {
            'value': str(item.pk),
            'label': f'{_user_label(item)}（历史负责人）',
            'active': item.is_active,
            'historical': True,
        }
        for item in historical
    ]


def _visible_team_choices(*, site, scoped) -> list[dict[str, object]]:
    team_ids = list(
        scoped.exclude(team_id__isnull=True).order_by().values_list('team_id', flat=True).distinct()
    )
    return [
        {'value': str(item.pk), 'label': item.name, 'enabled': item.enabled}
        for item in SalesTeam.objects.filter(site=site, pk__in=team_ids).order_by('name', 'id')
    ]


def _apply_common_filters(queryset, filters: AccountListFilters, *, user):
    if filters.status != 'all':
        queryset = queryset.filter(status=filters.status)
    if filters.owner == 'mine':
        queryset = queryset.filter(owner_user=user)
    elif filters.owner == 'unassigned':
        queryset = queryset.filter(owner_user__isnull=True)
    elif filters.owner.isdigit():
        queryset = queryset.filter(owner_user_id=int(filters.owner))
    if filters.team.isdigit():
        queryset = queryset.filter(team_id=int(filters.team))
    return queryset


def _selected_company_filter(querydict, visible_companies) -> dict[str, object] | None:
    """Resolve one relation deep-link without materializing an account directory."""

    raw = str(querydict.get('company') or '').strip()
    if not raw.isdigit():
        return None
    return visible_companies.filter(pk=int(raw)).values('id', 'name').first()


def _activity_scope(*, site, user):
    return activity_queryset_for_user(Activity.objects.filter(site=site), user)


def _attachment_queryset_for_user(*, site, user):
    return CrmAttachment.objects.filter(site=site).filter(
        linked_targets_match_record_site_q(),
        all_linked_targets_in_scope_q(
            submission_ids=lead_queryset_for_user(
                LeadSubmission.objects.filter(site=site), user
            ).values('id'),
            company_ids=crm_owned_queryset_for_user(
                Company.objects.filter(site=site), user
            ).values('id'),
            contact_ids=contact_queryset_for_user(
                Contact.objects.filter(site=site), user
            ).values('id'),
            opportunity_ids=opportunity_queryset_for_user(
                Opportunity.objects.filter(site=site), user
            ).values('id'),
        ),
    )


def _active_filter_chips(
    *, route_name: str, filters: AccountListFilters,
    status_labels, owner_labels, team_labels, company_labels,
) -> list[dict[str, str]]:
    definitions = (
        ('query', filters.query, f'搜索：{filters.query}', ''),
        ('status', filters.status, f'状态：{status_labels.get(filters.status, (filters.status, ""))[0]}', 'all'),
        ('owner', filters.owner, f'负责人：{owner_labels.get(filters.owner, filters.owner)}', 'all'),
        ('team', filters.team, f'团队：{team_labels.get(filters.team, filters.team)}', 'all'),
        ('company', filters.company, f'企业：{company_labels.get(filters.company, filters.company)}', 'all'),
    )
    chips = []
    for field, value, label, default in definitions:
        if value and value != default:
            chips.append({
                'label': label,
                'remove_url': _url(route_name, filters, **{field: default, 'page': 1}),
            })
    return chips


def _pagination(*, route_name: str, filters: AccountListFilters, page_obj) -> dict[str, object]:
    return {
        'current': page_obj.number,
        'total_pages': page_obj.paginator.num_pages,
        'total_items': page_obj.paginator.count,
        'start': page_obj.start_index() if page_obj.paginator.count else 0,
        'end': page_obj.end_index() if page_obj.paginator.count else 0,
        'previous_url': _url(route_name, filters, page=page_obj.previous_page_number()) if page_obj.has_previous() else '',
        'next_url': _url(route_name, filters, page=page_obj.next_page_number()) if page_obj.has_next() else '',
    }


def _record_owner_choices(*, site, user, record) -> list[dict[str, object]]:
    can_assign = bool(user_role_keys(user).intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN}))
    if not can_assign or record.team_id is None:
        candidates = get_user_model().objects.filter(pk=record.owner_user_id)
    else:
        candidates = assignee_queryset_for_user(user, site=site).filter(
            sales_team_memberships__team_id=record.team_id,
            sales_team_memberships__team__enabled=True,
        ).distinct()
        if record.owner_user_id and not candidates.filter(pk=record.owner_user_id).exists():
            candidates = get_user_model().objects.filter(
                Q(pk__in=candidates.values('pk')) | Q(pk=record.owner_user_id)
            )
    return [
        {
            'value': str(item.pk),
            'label': _user_label(item) + ('' if item.is_active else '（已停用，仅保留）'),
            'selected': item.pk == record.owner_user_id,
            'disabled': not item.is_active and item.pk != record.owner_user_id,
            'is_active': bool(item.is_active),
        }
        for item in candidates.order_by('username')
    ]


def _company_row(item: Company, *, filters: AccountListFilters) -> dict[str, object]:
    latest = getattr(item, 'latest_visible_activity_at', None)
    opportunity_query = {
        'q': item.name, 'sort': 'attention', 'page': 1, 'page_size': 25, 'view': 'list',
    }
    return {
        'id': item.pk,
        'name': item.name,
        'detail_url': _url('console:company_workspace_detail_v2', filters, args=(item.pk,)),
        'location': ' / '.join(value for value in (item.country, item.city) if value) or '未填写地区',
        'industry': (item.industry or '').strip() or '未填写行业',
        'contact_count': int(getattr(item, 'visible_contact_count', 0) or 0),
        'contacts_url': _url('console:contact_workspace_v2', AccountListFilters(company=str(item.pk))),
        'open_opportunity_count': int(getattr(item, 'open_opportunity_count', 0) or 0),
        'opportunities_url': f'{reverse("console:opportunity_workspace_v2")}?{urlencode(opportunity_query)}',
        'latest_activity_at': latest,
        'latest_activity_label': _format_datetime(latest) or '暂无活动',
        'owner': _user_label(item.owner_user),
        'team': item.team.name if item.team else '未分配团队',
        'status_label': COMPANY_STATUSES.get(item.status, (item.status or '未知', 'secondary'))[0],
        'status_tone': COMPANY_STATUSES.get(item.status, ('', 'secondary'))[1],
    }


def _contact_channels(item: Contact) -> list[dict[str, str]]:
    channels = []
    if (item.email or '').strip():
        channels.append({'kind': '邮箱', 'value': item.email.strip(), 'icon': 'mail'})
    if (item.phone or '').strip():
        channels.append({'kind': '电话', 'value': item.phone.strip(), 'icon': 'phone'})
    if (item.whatsapp_phone or '').strip():
        channels.append({'kind': 'WhatsApp', 'value': item.whatsapp_phone.strip(), 'icon': 'message-circle'})
    return channels


def _contact_row(item: Contact, *, filters: AccountListFilters) -> dict[str, object]:
    latest = getattr(item, 'latest_visible_activity_at', None)
    opportunity_query = {
        'q': item.full_name, 'sort': 'attention', 'page': 1, 'page_size': 25, 'view': 'list',
    }
    return {
        'id': item.pk,
        'full_name': item.full_name,
        'detail_url': _url('console:contact_workspace_detail_v2', filters, args=(item.pk,)),
        'job_title': (item.job_title or '').strip() or '未填写职位',
        'company': item.company.name if item.company else '未关联企业',
        'company_url': (
            _url('console:company_workspace_detail_v2', AccountListFilters(), args=(item.company_id,))
            if item.company_id else ''
        ),
        'channels': _contact_channels(item),
        'country_language': ' / '.join(
            value for value in ((item.country or '').strip(), (item.preferred_language or '').strip()) if value
        ) or '未填写',
        'open_opportunity_count': int(getattr(item, 'open_opportunity_count', 0) or 0),
        'opportunities_url': f'{reverse("console:opportunity_workspace_v2")}?{urlencode(opportunity_query)}',
        'latest_activity_at': latest,
        'latest_activity_label': _format_datetime(latest) or '暂无活动',
        'owner': _user_label(item.owner_user),
        'team': item.team.name if item.team else '未分配团队',
        'status_label': CONTACT_STATUSES.get(item.status, (item.status or '未知', 'secondary'))[0],
        'status_tone': CONTACT_STATUSES.get(item.status, ('', 'secondary'))[1],
    }


def build_company_list_workspace_v2(*, request, site) -> dict[str, object]:
    user = request.user
    show_unassigned = bool(
        user_role_keys(user).intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN})
    )
    scoped = crm_owned_queryset_for_user(Company.objects.filter(site=site), user)
    owner_choices = _visible_owner_choices(site=site, user=user, scoped=scoped)
    team_choices = _visible_team_choices(site=site, scoped=scoped)
    filters = AccountListFilters.from_querydict(
        request.GET,
        status_keys=COMPANY_STATUSES,
        sort_keys=COMPANY_SORTS,
        owner_ids=(int(item['value']) for item in owner_choices),
        team_ids=(int(item['value']) for item in team_choices),
        can_filter_unassigned=show_unassigned,
    )
    total_visible = scoped.count()
    queryset = _apply_common_filters(scoped, filters, user=user)
    if filters.query:
        queryset = queryset.filter(
            Q(name__icontains=filters.query)
            | Q(industry__icontains=filters.query)
            | Q(country__icontains=filters.query)
            | Q(city__icontains=filters.query)
            | Q(website__icontains=filters.query)
        )

    visible_contacts = contact_queryset_for_user(Contact.objects.filter(site=site), user)
    visible_opportunities = opportunity_queryset_for_user(
        Opportunity.objects.filter(site=site), user
    )
    activity_scope = _activity_scope(site=site, user=user)
    queryset = queryset.select_related('owner_user', 'team').annotate(
        visible_contact_count=Count(
            'contacts',
            filter=Q(contacts__id__in=visible_contacts.values('id')),
            distinct=True,
        ),
        open_opportunity_count=Count(
            'opportunities',
            filter=Q(
                opportunities__id__in=visible_opportunities.values('id'),
                opportunities__stage__in=OPEN_OPPORTUNITY_STAGES,
            ),
            distinct=True,
        ),
        latest_visible_activity_at=Subquery(
            activity_scope.filter(company_id=OuterRef('pk'))
            .order_by('-occurred_at', '-id').values('occurred_at')[:1]
        ),
    )
    if filters.sort == 'recent':
        queryset = queryset.order_by(
            F('latest_visible_activity_at').desc(nulls_last=True), 'name', 'id'
        )
    elif filters.sort == 'opportunities':
        queryset = queryset.order_by('-open_opportunity_count', 'name', 'id')
    elif filters.sort == 'updated':
        queryset = queryset.order_by('-updated_at', '-id')
    else:
        queryset = queryset.order_by('name', 'id')

    page_obj = Paginator(queryset, filters.page_size).get_page(filters.page)
    if filters.page != page_obj.number:
        filters = replace(filters, page=page_obj.number)
    owner_labels = {'all': '全部负责人', 'mine': '我负责的', 'unassigned': '未分配'}
    owner_labels.update({item['value']: item['label'] for item in owner_choices})
    team_labels = {'all': '全部团队', **{item['value']: item['label'] for item in team_choices}}
    return {
        'kind': 'companies',
        'eyebrow': '销售 · 客户账户',
        'title': '企业',
        'description': '从客户组织进入联系人、销售机会和跟进证据，不再在孤立表格之间重复搜索。',
        'filters': filters,
        'status_options': [
            {'value': key, 'label': value[0]} for key, value in COMPANY_STATUSES.items()
        ],
        'sort_options': [
            {'value': key, 'label': label} for key, label in COMPANY_SORTS.items()
        ],
        'owner_choices': owner_choices,
        'team_choices': team_choices,
        'show_unassigned': show_unassigned,
        'rows': [_company_row(item, filters=filters) for item in page_obj.object_list],
        'total_visible': total_visible,
        'filtered_total': page_obj.paginator.count,
        'active_filters': _active_filter_chips(
            route_name='console:company_workspace_v2',
            filters=filters,
            status_labels=COMPANY_STATUSES,
            owner_labels=owner_labels,
            team_labels=team_labels,
            company_labels={},
        ),
        'has_filters': bool(
            filters.query or filters.status != 'all'
            or filters.owner != 'all' or filters.team != 'all'
        ),
        'clear_url': _url('console:company_workspace_v2', AccountListFilters()),
        'canonical_url': _url('console:company_workspace_v2', filters),
        'pagination': _pagination(
            route_name='console:company_workspace_v2', filters=filters, page_obj=page_obj
        ),
        'error': False,
    }


def build_contact_list_workspace_v2(*, request, site) -> dict[str, object]:
    user = request.user
    show_unassigned = bool(
        user_role_keys(user).intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN})
    )
    scoped = contact_queryset_for_user(Contact.objects.filter(site=site), user)
    visible_companies = crm_owned_queryset_for_user(Company.objects.filter(site=site), user)
    owner_choices = _visible_owner_choices(site=site, user=user, scoped=scoped)
    team_choices = _visible_team_choices(site=site, scoped=scoped)
    selected_company = _selected_company_filter(request.GET, visible_companies)
    filters = AccountListFilters.from_querydict(
        request.GET,
        status_keys=CONTACT_STATUSES,
        sort_keys=CONTACT_SORTS,
        owner_ids=(int(item['value']) for item in owner_choices),
        team_ids=(int(item['value']) for item in team_choices),
        company_ids=([selected_company['id']] if selected_company else ()),
        can_filter_unassigned=show_unassigned,
    )
    total_visible = scoped.count()
    queryset = _apply_common_filters(scoped, filters, user=user)
    if filters.company.isdigit():
        queryset = queryset.filter(company_id=int(filters.company))
    if filters.query:
        queryset = queryset.filter(
            Q(full_name__icontains=filters.query)
            | Q(job_title__icontains=filters.query)
            | Q(company__name__icontains=filters.query)
            | Q(email__icontains=filters.query)
            | Q(phone__icontains=filters.query)
            | Q(whatsapp_phone__icontains=filters.query)
        )

    visible_opportunities = opportunity_queryset_for_user(
        Opportunity.objects.filter(site=site), user
    )
    activity_scope = _activity_scope(site=site, user=user)
    queryset = queryset.select_related('company', 'owner_user', 'team').annotate(
        open_opportunity_count=Count(
            'primary_opportunities',
            filter=Q(
                primary_opportunities__id__in=visible_opportunities.values('id'),
                primary_opportunities__stage__in=OPEN_OPPORTUNITY_STAGES,
            ),
            distinct=True,
        ),
        latest_visible_activity_at=Subquery(
            activity_scope.filter(contact_id=OuterRef('pk'))
            .order_by('-occurred_at', '-id').values('occurred_at')[:1]
        ),
    )
    if filters.sort == 'recent':
        queryset = queryset.order_by(
            F('latest_visible_activity_at').desc(nulls_last=True), 'full_name', 'id'
        )
    elif filters.sort == 'opportunities':
        queryset = queryset.order_by('-open_opportunity_count', 'full_name', 'id')
    elif filters.sort == 'updated':
        queryset = queryset.order_by('-updated_at', '-id')
    else:
        queryset = queryset.order_by('full_name', 'id')

    page_obj = Paginator(queryset, filters.page_size).get_page(filters.page)
    if filters.page != page_obj.number:
        filters = replace(filters, page=page_obj.number)
    owner_labels = {'all': '全部负责人', 'mine': '我负责的', 'unassigned': '未分配'}
    owner_labels.update({item['value']: item['label'] for item in owner_choices})
    team_labels = {'all': '全部团队', **{item['value']: item['label'] for item in team_choices}}
    company_labels = {'all': '全部企业'}
    if selected_company:
        company_labels[str(selected_company['id'])] = selected_company['name']
    return {
        'kind': 'contacts',
        'eyebrow': '销售 · 客户关系',
        'title': '联系人',
        'description': '只在授权销售范围内呈现真实联系方式，并把人员、企业、机会和跟进证据连在一起。',
        'filters': filters,
        'status_options': [
            {'value': key, 'label': value[0]} for key, value in CONTACT_STATUSES.items()
        ],
        'sort_options': [
            {'value': key, 'label': label} for key, label in CONTACT_SORTS.items()
        ],
        'owner_choices': owner_choices,
        'team_choices': team_choices,
        'show_unassigned': show_unassigned,
        'selected_company': (
            {'value': str(selected_company['id']), 'label': selected_company['name']}
            if selected_company else None
        ),
        'rows': [_contact_row(item, filters=filters) for item in page_obj.object_list],
        'total_visible': total_visible,
        'filtered_total': page_obj.paginator.count,
        'active_filters': _active_filter_chips(
            route_name='console:contact_workspace_v2',
            filters=filters,
            status_labels=CONTACT_STATUSES,
            owner_labels=owner_labels,
            team_labels=team_labels,
            company_labels=company_labels,
        ),
        'has_filters': bool(
            filters.query or filters.status != 'all' or filters.owner != 'all'
            or filters.team != 'all' or filters.company != 'all'
        ),
        'clear_url': _url('console:contact_workspace_v2', AccountListFilters()),
        'canonical_url': _url('console:contact_workspace_v2', filters),
        'pagination': _pagination(
            route_name='console:contact_workspace_v2', filters=filters, page_obj=page_obj
        ),
        'error': False,
    }


def _activity_items(queryset) -> list[dict[str, object]]:
    return [
        {
            'kind': 'activity',
            'icon': 'message-square-text',
            'tone': 'azure',
            'title': item.subject or '未命名活动',
            'body': item.body,
            'meta': f'{item.activity_type} · {_user_label(item.actor_user)}',
            'timestamp': item.occurred_at,
            'time_label': _format_datetime(item.occurred_at),
            'detail_url': reverse('console:activity_workspace_detail_v2', args=(item.pk,)),
        }
        for item in queryset
    ]


def _task_items(queryset) -> list[dict[str, object]]:
    status_labels = {'open': '待处理', 'completed': '已完成', 'canceled': '已取消'}
    return [
        {
            'kind': 'task',
            'icon': 'list-checks',
            'tone': (
                'green' if item.status == 'completed'
                else 'secondary' if item.status == 'canceled' else 'orange'
            ),
            'title': item.title,
            'body': item.outcome or item.description,
            'meta': (
                f'{status_labels.get(item.status, item.status)} · '
                f'{_user_label(item.owner_user)} · 到期 {_format_datetime(item.due_at)}'
            ),
            'timestamp': item.updated_at,
            'time_label': _format_datetime(item.updated_at),
            'detail_url': reverse('console:task_workspace_detail_v2', args=(item.pk,)),
        }
        for item in queryset
    ]


def _attachment_items(queryset) -> list[dict[str, object]]:
    return [
        {
            'kind': 'attachment',
            'icon': 'paperclip',
            'tone': 'secondary',
            'title': item.title or item.original_name or '未命名附件',
            'body': item.original_name,
            'meta': f'{item.file_size_bytes or 0} bytes · {_user_label(item.uploaded_by_user)}',
            'timestamp': item.created_at,
            'time_label': _format_datetime(item.created_at),
            'download_url': reverse('console:crm_attachment_download', args=(item.pk,)),
        }
        for item in queryset
    ]


def _timeline(*, activities, tasks, attachments) -> list[dict[str, object]]:
    items = _activity_items(activities) + _task_items(tasks) + _attachment_items(attachments)
    items.sort(key=lambda item: item['timestamp'], reverse=True)
    return items[:100]


def _company_record_payload(item: Company) -> dict[str, object]:
    status = COMPANY_STATUSES.get(item.status, (item.status or '未知', 'secondary'))
    return {
        'id': item.pk,
        'name': item.name,
        'website': item.website,
        'industry': item.industry,
        'country': item.country,
        'city': item.city,
        'location': ' / '.join(value for value in (item.country, item.city) if value) or '未填写',
        'status': item.status,
        'status_label': status[0],
        'status_tone': status[1],
        'source_channel': item.source_channel or '未记录',
        'notes': item.notes,
        'owner': _user_label(item.owner_user),
        'owner_id': item.owner_user_id,
        'team': item.team.name if item.team else '未分配团队',
        'team_id': item.team_id,
        'updated_at': item.updated_at,
        'version': account_version_token(item.updated_at),
    }


def _contact_record_payload(item: Contact) -> dict[str, object]:
    status = CONTACT_STATUSES.get(item.status, (item.status or '未知', 'secondary'))
    return {
        'id': item.pk,
        'full_name': item.full_name,
        'job_title': item.job_title,
        'email': item.email,
        'phone': item.phone,
        'whatsapp_phone': item.whatsapp_phone,
        'channels': _contact_channels(item),
        'country': item.country,
        'preferred_language': item.preferred_language,
        'status': item.status,
        'status_label': status[0],
        'status_tone': status[1],
        'notes': item.notes,
        'owner': _user_label(item.owner_user),
        'owner_id': item.owner_user_id,
        'team': item.team.name if item.team else '未分配团队',
        'team_id': item.team_id,
        'company': item.company.name if item.company else '未关联企业',
        'company_id': item.company_id,
        'updated_at': item.updated_at,
        'version': account_version_token(item.updated_at),
    }


def _company_detail_filters(*, request, site, user, scoped):
    owners = _visible_owner_choices(site=site, user=user, scoped=scoped)
    teams = _visible_team_choices(site=site, scoped=scoped)
    return AccountListFilters.from_querydict(
        request.GET,
        status_keys=COMPANY_STATUSES,
        sort_keys=COMPANY_SORTS,
        owner_ids=(int(item['value']) for item in owners),
        team_ids=(int(item['value']) for item in teams),
        can_filter_unassigned=bool(
            user_role_keys(user).intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN})
        ),
    )


def _contact_detail_filters(*, request, site, user, scoped, companies):
    owners = _visible_owner_choices(site=site, user=user, scoped=scoped)
    teams = _visible_team_choices(site=site, scoped=scoped)
    selected_company = _selected_company_filter(request.GET, companies)
    return AccountListFilters.from_querydict(
        request.GET,
        status_keys=CONTACT_STATUSES,
        sort_keys=CONTACT_SORTS,
        owner_ids=(int(item['value']) for item in owners),
        team_ids=(int(item['value']) for item in teams),
        company_ids=([selected_company['id']] if selected_company else ()),
        can_filter_unassigned=bool(
            user_role_keys(user).intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN})
        ),
    )


def build_company_detail_workspace_v2(*, request, site, company_id: int) -> dict[str, object]:
    user = request.user
    scoped = crm_owned_queryset_for_user(Company.objects.filter(site=site), user)
    filters = _company_detail_filters(
        request=request, site=site, user=user, scoped=scoped
    )
    company = scoped.select_related('owner_user', 'team').filter(pk=company_id).first()
    if company is None:
        return {'missing': True}

    contacts = list(
        contact_queryset_for_user(
            Contact.objects.filter(site=site, company=company).select_related('owner_user'), user
        ).order_by('full_name', 'id')[:100]
    )
    opportunities_qs = opportunity_queryset_for_user(
        Opportunity.objects.filter(site=site, company=company).select_related(
            'owner_user', 'primary_contact'
        ), user
    )
    opportunities = list(opportunities_qs.order_by('-updated_at', '-id')[:100])
    open_opportunities = opportunities_qs.filter(stage__in=OPEN_OPPORTUNITY_STAGES)
    amount_rows = list(
        open_opportunities.exclude(value_amount__isnull=True)
        .values('currency').annotate(total=Sum('value_amount')).order_by('currency')
    )
    activities = list(
        _activity_scope(site=site, user=user).filter(company=company)
        .select_related('actor_user').order_by('-occurred_at', '-id')[:50]
    )
    tasks = list(
        task_queryset_for_user(
            Task.objects.filter(site=site, company=company).select_related('owner_user'), user
        ).order_by('-updated_at', '-id')[:50]
    )
    attachments = list(
        _attachment_queryset_for_user(site=site, user=user)
        .filter(company=company, status='active').select_related('uploaded_by_user')
        .order_by('-created_at', '-id')[:50]
    )
    conversions = list(
        LeadConversion.objects.filter(
            company=company,
            submission_id__in=lead_queryset_for_user(
                LeadSubmission.objects.filter(site=site), user
            ).values('id'),
        ).select_related('submission').order_by('-converted_at', '-id')[:50]
    )
    next_task = min((item.due_at for item in tasks if item.status == 'open'), default=None)
    latest_activity = max((item.occurred_at for item in activities), default=None)
    opportunity_context = {'sort': 'attention', 'page': 1, 'page_size': 25, 'view': 'list'}
    return {
        'kind': 'company',
        'record': _company_record_payload(company),
        'back_url': _url('console:company_workspace_v2', filters),
        'canonical_url': _url(
            'console:company_workspace_detail_v2', filters, args=(company.pk,)
        ),
        'can_write': can_sales(user, SalesCapability.WRITE, record=company),
        'can_assign': bool(user_role_keys(user).intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN})),
        'edit_url': reverse('console:company_update', args=(company.pk,)),
        'opportunity_create_url': reverse('console:opportunity_create_v2'),
        'owner_choices': _record_owner_choices(site=site, user=user, record=company),
        'summary': {
            'open_opportunities': open_opportunities.count(),
            'amounts': [
                {
                    'currency': row['currency'] or 'USD',
                    'label': _format_money(row['total'], row['currency']),
                }
                for row in amount_rows
            ],
            'next_task_at': next_task,
            'next_task_label': _format_datetime(next_task) or '暂无开放任务',
            'latest_activity_at': latest_activity,
            'latest_activity_label': _format_datetime(latest_activity) or '暂无活动',
        },
        'contacts': [
            {
                'id': item.pk,
                'name': item.full_name,
                'job_title': item.job_title or '未填写职位',
                'owner': _user_label(item.owner_user),
                'url': _url(
                    'console:contact_workspace_detail_v2', AccountListFilters(), args=(item.pk,)
                ),
            }
            for item in contacts
        ],
        'opportunities': [
            {
                'id': item.pk,
                'name': item.name,
                'stage': item.stage,
                'amount': _format_money(item.value_amount, item.currency) or '未填写金额',
                'owner': _user_label(item.owner_user),
                'url': (
                    f'{reverse("console:opportunity_workspace_detail_v2", args=(item.pk,))}'
                    f'?{urlencode(opportunity_context)}'
                ),
            }
            for item in opportunities
        ],
        'conversions': [
            {
                'id': item.pk,
                'lead_id': item.submission_id,
                'lead_url': (
                    f'{reverse("console:lead_workspace_detail_v2", args=(item.submission_id,))}'
                    f'?{urlencode({"sort": "attention", "page": 1, "page_size": 25})}'
                ),
                'converted_at': item.converted_at,
            }
            for item in conversions
        ],
        'timeline': _timeline(
            activities=activities, tasks=tasks, attachments=attachments
        ),
        'error': False,
    }


def build_contact_detail_workspace_v2(*, request, site, contact_id: int) -> dict[str, object]:
    user = request.user
    scoped = contact_queryset_for_user(Contact.objects.filter(site=site), user)
    visible_companies = crm_owned_queryset_for_user(Company.objects.filter(site=site), user)
    filters = _contact_detail_filters(
        request=request, site=site, user=user, scoped=scoped, companies=visible_companies
    )
    contact = scoped.select_related('company', 'owner_user', 'team').filter(pk=contact_id).first()
    if contact is None:
        return {'missing': True}

    opportunities = list(
        opportunity_queryset_for_user(
            Opportunity.objects.filter(site=site, primary_contact=contact).select_related('owner_user'),
            user,
        ).order_by('-updated_at', '-id')[:100]
    )
    activities = list(
        _activity_scope(site=site, user=user).filter(contact=contact)
        .select_related('actor_user').order_by('-occurred_at', '-id')[:50]
    )
    tasks = list(
        task_queryset_for_user(
            Task.objects.filter(site=site, contact=contact).select_related('owner_user'), user
        ).order_by('-updated_at', '-id')[:50]
    )
    attachments = list(
        _attachment_queryset_for_user(site=site, user=user)
        .filter(contact=contact, status='active').select_related('uploaded_by_user')
        .order_by('-created_at', '-id')[:50]
    )
    conversions = list(
        LeadConversion.objects.filter(
            contact=contact,
            submission_id__in=lead_queryset_for_user(
                LeadSubmission.objects.filter(site=site), user
            ).values('id'),
        ).select_related('submission').order_by('-converted_at', '-id')[:50]
    )
    opportunity_context = {'sort': 'attention', 'page': 1, 'page_size': 25, 'view': 'list'}
    return {
        'kind': 'contact',
        'record': _contact_record_payload(contact),
        'back_url': _url('console:contact_workspace_v2', filters),
        'canonical_url': _url(
            'console:contact_workspace_detail_v2', filters, args=(contact.pk,)
        ),
        'can_write': can_sales(user, SalesCapability.WRITE, record=contact),
        'can_assign': bool(user_role_keys(user).intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN})),
        'edit_url': reverse('console:contact_update', args=(contact.pk,)),
        'owner_choices': _record_owner_choices(site=site, user=user, record=contact),
        'company_search_url': reverse(
            'console:contact_company_options_v2', args=(contact.pk,)
        ),
        'company_url': (
            _url(
                'console:company_workspace_detail_v2', AccountListFilters(), args=(contact.company_id,)
            ) if contact.company_id else ''
        ),
        'opportunities': [
            {
                'id': item.pk,
                'name': item.name,
                'stage': item.stage,
                'amount': _format_money(item.value_amount, item.currency) or '未填写金额',
                'owner': _user_label(item.owner_user),
                'url': (
                    f'{reverse("console:opportunity_workspace_detail_v2", args=(item.pk,))}'
                    f'?{urlencode(opportunity_context)}'
                ),
            }
            for item in opportunities
        ],
        'conversions': [
            {
                'id': item.pk,
                'lead_id': item.submission_id,
                'lead_url': (
                    f'{reverse("console:lead_workspace_detail_v2", args=(item.submission_id,))}'
                    f'?{urlencode({"sort": "attention", "page": 1, "page_size": 25})}'
                ),
                'converted_at': item.converted_at,
            }
            for item in conversions
        ],
        'timeline': _timeline(
            activities=activities, tasks=tasks, attachments=attachments
        ),
        'error': False,
    }


def build_account_error_workspace(*, kind: str, detail: bool = False) -> dict[str, object]:
    label = '企业' if kind in {'company', 'companies'} else '联系人'
    route_name = (
        'console:company_workspace_v2' if label == '企业' else 'console:contact_workspace_v2'
    )
    return {
        'kind': kind,
        'title': label,
        'description': '客户关系数据暂时无法读取。',
        'error': True,
        'detail': detail,
        'rows': [],
        'canonical_url': reverse(route_name),
        'clear_url': reverse(route_name),
        'back_url': reverse(route_name),
    }
