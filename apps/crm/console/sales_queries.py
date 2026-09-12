from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max, Q
from django.utils import timezone

from leads.models import Activity, Company, LeadEventOutbox, LeadInboundEvent, LeadSubmission, Opportunity, Task

from .access import (
    ROLE_MARKETING_OPS,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    activity_queryset_for_user,
    crm_owned_queryset_for_user,
    lead_queryset_for_user,
    task_queryset_for_user,
    user_role_keys,
)


PIPELINE_STAGES: tuple[tuple[str, str], ...] = (
    ('qualification', '资格确认'),
    ('discovery', '需求澄清'),
    ('solution', '方案设计'),
    ('quotation', '报价'),
    ('negotiation', '商务谈判'),
    ('on_hold', '暂缓'),
    ('won', '已成交'),
    ('lost', '已丢单'),
)


@dataclass(frozen=True)
class WorkItem:
    kind: str
    entity_id: int
    title: str
    company: str
    country: str
    project: str
    urgency: str
    due_at: object | None
    next_action: str
    owner: str
    latest_activity_at: object | None
    score: tuple


def _owner_label(user) -> str:
    if user is None:
        return '未分配'
    full_name = user.get_full_name().strip()
    return full_name or user.get_username()


def _lead_next_action(lead: LeadSubmission) -> str:
    if lead.assignee_id is None:
        return '分配负责人'
    if lead.stage == 'new':
        return '首次联系'
    if lead.stage == 'contacted':
        return '确认项目范围'
    if lead.stage == 'qualified':
        return '建立或更新销售项目'
    return '更新跟进记录'


def pipeline_value_summary(rows) -> list[dict[str, object]]:
    totals: dict[str, Decimal] = {}
    for item in rows:
        if item.value_amount is None:
            continue
        currency = (item.currency or 'USD').strip().upper() or 'USD'
        totals[currency] = totals.get(currency, Decimal('0')) + item.value_amount
    return [
        {'currency': currency, 'amount': amount}
        for currency, amount in sorted(totals.items())
    ]


def _urgency_for_time(due_at, *, now) -> tuple[str, int, float]:
    if due_at is None:
        return '未安排', 4, 0.0
    seconds = (due_at - now).total_seconds()
    if seconds < 0:
        return '已逾期', 0, seconds
    if due_at.date() == now.date():
        return '今天到期', 1, seconds
    if seconds <= 3 * 86400:
        return '三天内', 2, seconds
    return '即将到期', 3, seconds


def build_workbench_context(*, site, user, now=None, limit: int = 40) -> dict:
    now = now or timezone.now()
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    week_end = now + timedelta(days=7)
    stale_lead_cutoff = now - timedelta(hours=4)

    leads = lead_queryset_for_user(
        LeadSubmission.objects.filter(site=site).select_related(
            'assignee', 'team', 'locale', 'conversion__opportunity'
        ),
        user,
    )
    tasks = task_queryset_for_user(
        Task.objects.filter(site=site).select_related(
            'owner_user', 'company', 'contact', 'opportunity', 'submission'
        ),
        user,
    )
    opportunities = crm_owned_queryset_for_user(
        Opportunity.objects.filter(site=site).select_related(
            'owner_user', 'company', 'primary_contact'
        ),
        user,
    )

    open_tasks = tasks.filter(status__in=['open', 'in_progress'])
    active_opportunities = opportunities.exclude(stage__in=['won', 'lost'])
    active_leads = leads.exclude(stage__in=['won', 'lost', 'spam'])

    counts = {
        'new_leads': active_leads.filter(stage='new').count(),
        'today_tasks': open_tasks.filter(due_at__gte=now, due_at__lte=today_end).count(),
        'due_soon': open_tasks.filter(due_at__gt=today_end, due_at__lte=week_end).count()
        + active_opportunities.filter(
            next_follow_up_at__gt=today_end,
            next_follow_up_at__lte=week_end,
        ).count(),
        'overdue': open_tasks.filter(due_at__lt=now).count()
        + active_opportunities.filter(next_follow_up_at__lt=now).count()
        + active_leads.filter(stage='new', contacted_at__isnull=True, submitted_at__lt=stale_lead_cutoff).count(),
        'unassigned': active_leads.filter(assignee__isnull=True).count(),
    }

    items: list[WorkItem] = []
    task_rows = open_tasks.filter(due_at__lte=week_end).order_by('due_at', '-priority')[:limit]
    for task in task_rows:
        urgency, band, seconds = _urgency_for_time(task.due_at, now=now)
        related_company = task.company or getattr(task.opportunity, 'company', None)
        related_contact = task.contact or getattr(task.opportunity, 'primary_contact', None)
        items.append(
            WorkItem(
                kind='task',
                entity_id=task.id,
                title=related_contact.full_name if related_contact else task.title,
                company=related_company.name if related_company else '',
                country=related_company.country if related_company else '',
                project=task.opportunity.name if task.opportunity else '',
                urgency=urgency,
                due_at=task.due_at,
                next_action=task.title,
                owner=_owner_label(task.owner_user),
                latest_activity_at=None,
                score=(band, seconds, 0, task.id),
            )
        )

    lead_rows = active_leads.filter(
        Q(stage='new') | Q(next_follow_up_at__lte=week_end),
        conversion__isnull=True,
    ).order_by('submitted_at')[:limit]
    lead_activity = {
        row['submission_id']: row['latest']
        for row in activity_queryset_for_user(
            Activity.objects.filter(
                site=site,
                submission_id__in=[lead.id for lead in lead_rows],
            ),
            user,
        )
        .values('submission_id')
        .annotate(latest=Max('occurred_at'))
    }
    for lead in lead_rows:
        due_at = lead.next_follow_up_at
        if lead.stage == 'new' and lead.contacted_at is None:
            due_at = min(filter(None, [due_at, lead.submitted_at + timedelta(hours=4)]), default=None)
        urgency, band, seconds = _urgency_for_time(due_at, now=now)
        project = str((lead.payload_json or {}).get('product_category') or '').strip()
        capacity = str((lead.payload_json or {}).get('capacity') or '').strip()
        if project and capacity:
            project = f'{project} / {capacity}'
        elif capacity:
            project = capacity
        items.append(
            WorkItem(
                kind='lead',
                entity_id=lead.id,
                title=lead.full_name or lead.company or f'线索 {lead.id}',
                company=lead.company,
                country=lead.country,
                project=project,
                urgency=urgency,
                due_at=due_at,
                next_action=_lead_next_action(lead),
                owner=_owner_label(lead.assignee),
                latest_activity_at=lead_activity.get(lead.id),
                score=(band, seconds, 1, lead.id),
            )
        )

    opportunity_rows = active_opportunities.filter(
        next_follow_up_at__isnull=False,
        next_follow_up_at__lte=week_end,
    ).exclude(
        tasks__status__in=['open', 'in_progress'],
    ).distinct().order_by('next_follow_up_at')[:limit]
    opportunity_activity = {
        row['opportunity_id']: row['latest']
        for row in activity_queryset_for_user(
            Activity.objects.filter(
                site=site,
                opportunity_id__in=[item.id for item in opportunity_rows],
            ),
            user,
        )
        .values('opportunity_id')
        .annotate(latest=Max('occurred_at'))
    }
    for opportunity in opportunity_rows:
        urgency, band, seconds = _urgency_for_time(opportunity.next_follow_up_at, now=now)
        items.append(
            WorkItem(
                kind='opportunity',
                entity_id=opportunity.id,
                title=(opportunity.primary_contact.full_name if opportunity.primary_contact else opportunity.name),
                company=opportunity.company.name if opportunity.company else '',
                country=opportunity.company.country if opportunity.company else '',
                project=' / '.join(filter(None, [opportunity.product_scope, opportunity.capacity_target])),
                urgency=urgency,
                due_at=opportunity.next_follow_up_at,
                next_action=opportunity.next_step or '确认下一步',
                owner=_owner_label(opportunity.owner_user),
                latest_activity_at=opportunity_activity.get(opportunity.id),
                score=(band, seconds, 2, opportunity.id),
            )
        )

    action_queue = sorted(items, key=lambda item: item.score)[:limit]
    roles = user_role_keys(user)
    show_system_exceptions = bool(
        roles.intersection({ROLE_SYSTEM_ADMIN, ROLE_MARKETING_OPS, ROLE_SALES_MANAGER})
    )
    exceptions = []
    if show_system_exceptions:
        failed_outbox = LeadEventOutbox.objects.filter(
            submission__site=site,
            status='failed',
        ).count()
        failed_inbound = LeadInboundEvent.objects.filter(
            status='failed',
        ).filter(Q(submission__site=site) | Q(submission__isnull=True)).count()
        failed_notifications = LeadSubmission.objects.filter(
            site=site,
            notification_status='failed',
        ).count()
        if failed_outbox:
            exceptions.append({'kind': 'outbox', 'label': '营销事件发送失败', 'count': failed_outbox})
        if failed_inbound:
            exceptions.append({'kind': 'inbound', 'label': '平台线索接收失败', 'count': failed_inbound})
        if failed_notifications:
            exceptions.append({'kind': 'notification', 'label': '线索通知发送失败', 'count': failed_notifications})

    return {
        'now': now,
        'counts': counts,
        'action_queue': action_queue,
        'system_exceptions': exceptions,
        'show_system_exceptions': show_system_exceptions,
    }


def build_pipeline_context(
    *,
    site,
    user,
    filters: dict | None = None,
    include_closed: bool = False,
) -> dict:
    filters = filters or {}
    queryset = crm_owned_queryset_for_user(
        Opportunity.objects.filter(site=site).select_related(
            'company', 'primary_contact', 'owner_user', 'team'
        ),
        user,
    )
    if not include_closed:
        queryset = queryset.exclude(stage__in=['won', 'lost'])
    stage = str(filters.get('stage') or '').strip()
    owner_id = str(filters.get('owner') or '').strip()
    query = str(filters.get('q') or '').strip()
    source_channel = str(filters.get('source_channel') or '').strip()
    if stage:
        queryset = queryset.filter(stage=stage)
    if owner_id.isdigit():
        queryset = queryset.filter(owner_user_id=int(owner_id))
    if source_channel:
        queryset = queryset.filter(source_channel=source_channel)
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(company__name__icontains=query)
            | Q(primary_contact__full_name__icontains=query)
            | Q(product_scope__icontains=query)
            | Q(capacity_target__icontains=query)
        )

    opportunities = list(queryset.order_by('stage', 'next_follow_up_at', '-updated_at'))
    columns = []
    for stage_key, stage_label in PIPELINE_STAGES:
        stage_rows = [item for item in opportunities if item.stage == stage_key]
        value_by_currency = pipeline_value_summary(stage_rows)
        columns.append(
            {
                'key': stage_key,
                'label': stage_label,
                'count': len(stage_rows),
                'value_amount': value_by_currency[0]['amount'] if len(value_by_currency) == 1 else None,
                'value_currency': value_by_currency[0]['currency'] if len(value_by_currency) == 1 else '',
                'value_by_currency': value_by_currency,
                'items': stage_rows,
            }
        )
    return {
        'filters': filters,
        'columns': columns,
        'rows': opportunities,
        'count': len(opportunities),
        'stage_choices': PIPELINE_STAGES,
    }
