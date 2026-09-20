"""Role-scoped query assembly for the canonical console workbench.

The public entry point is intentionally fail closed.  Role-specific builders
below this boundary may add data only after an authenticated console role has
been derived from server-side group membership; request parameters never
select or widen a role.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.db.models import Case, Count, DateTimeField, ExpressionWrapper, F, Q, When
from django.db.models.functions import Least
from django.urls import reverse
from django.utils import timezone

from leads.models import (
    LeadEventOutbox,
    LeadInboundEvent,
    LeadSubmission,
    SalesTeamMember,
    Task,
)
from leads.readiness import EXPECTED_INTEGRATIONS, inspect_marketing_integration
from marketing.models import MarketingIntegration
from sitecore.models import Article, MediaAsset, ReleaseBuild

from . import access
from .content_access import (
    ASSETS_READ,
    CONTENT_READ,
    CONTENT_SET_PUBLISHED,
    CONTENT_WRITE,
    RELEASES_READ,
    effective_content_capabilities,
)
from .error_redaction import redact_event_error
from .marketing_queries import retryable_outbox_q


ROLE_CONTENT_OPS = access.ROLE_CONTENT_OPS

ACTIVE_LEAD_STAGES = frozenset({'new', 'contacted', 'qualified', 'on_hold'})
ACTIONABLE_TASK_STATUSES = frozenset({'open', 'in_progress'})
MAX_ITEMS = 50
DEFAULT_ITEMS = 8

LEAD_STAGE_META = {
    'new': ('新线索', 'azure'),
    'contacted': ('已联系', 'warning'),
    'qualified': ('已合格', 'success'),
    'on_hold': ('暂缓', 'secondary'),
}


def _role_keys(user) -> set[str] | frozenset[str]:
    """Use the access layer's request-cached, fail-closed role resolver."""

    return access.user_role_keys(user)


def _item_limit(params: Mapping[str, Any] | None) -> int:
    raw_value = params.get('limit') if params is not None and hasattr(params, 'get') else None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_ITEMS
    return max(1, min(value, MAX_ITEMS))


def _action(label: str, href: str, *, icon: str | None = None) -> dict[str, str]:
    result = {'label': label, 'href': href}
    if icon:
        result['icon'] = icon
    return result


def _metric(
    label: str,
    value: int,
    *,
    description: str,
    href: str | None,
    tone: str,
    icon: str,
) -> dict[str, Any]:
    return {
        'label': label,
        'value': int(value),
        'description': description,
        'href': href,
        'tone': tone,
        'icon': icon,
    }


def _item(
    *,
    kind: str,
    item_id: int | str,
    title: str,
    meta: str = '',
    detail: str = '',
    status_label: str = '',
    status_tone: str = 'secondary',
    href: str | None = None,
    due_label: str = '',
    icon: str = 'circle-dot',
) -> dict[str, Any]:
    return {
        'kind': kind,
        'id': item_id,
        'title': title,
        'meta': meta,
        'detail': detail,
        'status_label': status_label,
        'status_tone': status_tone,
        'href': href,
        'due_label': due_label,
        'icon': icon,
    }


def _section(
    *,
    key: str,
    title: str,
    description: str,
    count: int,
    items: list[dict[str, Any]],
    empty_title: str,
    empty_description: str,
    action: dict[str, str] | None,
) -> dict[str, Any]:
    return {
        'key': key,
        'title': title,
        'description': description,
        'count': int(count),
        'items': items,
        'empty_title': empty_title,
        'empty_description': empty_description,
        'action': action,
    }


def _empty_state(title: str, description: str) -> dict[str, Any]:
    return {'title': title, 'description': description, 'action': None}


def _text(value: Any, *, fallback: str = '', limit: int = 180) -> str:
    normalized = ' '.join(str(value or '').split())
    if not normalized:
        return fallback
    if len(normalized) <= limit:
        return normalized
    return f'{normalized[: limit - 1]}…'


def _local(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        return timezone.localtime(value)
    return value


def _normalized_now(value):
    """Interpret a naive clock value in Django's active local timezone."""

    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _end_of_local_day(value):
    """Return the precise local-day boundary for an aware or naive instant."""

    local_value = _local(_normalized_now(value))
    return local_value.replace(hour=23, minute=59, second=59, microsecond=999999)


def _timestamp_label(value) -> str:
    localized = _local(value)
    return localized.strftime('%m-%d %H:%M') if localized else ''


def _due_meta(value, *, now) -> tuple[str, str]:
    due = _local(value)
    local_now = _local(now)
    if due is None or local_now is None:
        return '', 'secondary'
    if due < local_now:
        seconds = max(0, int((local_now - due).total_seconds()))
        if seconds >= 86400:
            return f'逾期 {seconds // 86400} 天', 'danger'
        if seconds >= 3600:
            return f'逾期 {seconds // 3600} 小时', 'danger'
        return '刚刚逾期', 'danger'
    if due.date() == local_now.date():
        return f'今天 {due:%H:%M}', 'warning'
    if due.date() == (local_now + timedelta(days=1)).date():
        return f'明天 {due:%H:%M}', 'azure'
    return due.strftime('%m-%d %H:%M'), 'secondary'


def _lead_due_at(lead):
    candidate = lead.next_follow_up_at
    if lead.stage == 'new' and lead.contacted_at is None:
        first_contact_due = lead.submitted_at + timedelta(hours=4)
        return min(filter(None, (candidate, first_contact_due)), default=None)
    return candidate


def _lead_meta(lead) -> str:
    return ' · '.join(filter(None, (_text(lead.company, limit=70), _text(lead.country, limit=40))))


def _task_meta(task, *, viewer_id: int, variant: str) -> str:
    def related_record_is_visible(*, owner_id=None, team_id=None) -> bool:
        if variant == 'sales_manager':
            return bool(task.team_id and team_id == task.team_id)
        return owner_id == viewer_id

    if task.opportunity_id:
        if not related_record_is_visible(
            owner_id=task.opportunity.owner_user_id,
            team_id=task.opportunity.team_id,
        ):
            return '销售任务'
        return f'销售机会 · {_text(task.opportunity.name, fallback=f"#{task.opportunity_id}", limit=90)}'
    if task.submission_id:
        if not related_record_is_visible(
            owner_id=task.submission.assignee_id,
            team_id=task.submission.team_id,
        ):
            return '销售任务'
        lead_label = _text(
            task.submission.full_name or task.submission.company,
            fallback=f'#{task.submission_id}',
            limit=90,
        )
        return f'线索 · {lead_label}'
    if task.company_id:
        if not related_record_is_visible(
            owner_id=task.company.owner_user_id,
            team_id=task.company.team_id,
        ):
            return '销售任务'
        return f'企业 · {_text(task.company.name, fallback=f"#{task.company_id}", limit=90)}'
    return '销售任务'


def _build_sales_workbench(
    *,
    site,
    user,
    roles,
    variant: str,
    now,
    limit: int,
) -> dict[str, Any]:
    leads = LeadSubmission.objects.filter(
        site=site,
        stage__in=ACTIVE_LEAD_STAGES,
        conversion__isnull=True,
    )
    tasks = access.task_queryset_for_user(
        Task.objects.filter(
            site=site,
            status__in=ACTIONABLE_TASK_STATUSES,
        ),
        user,
        roles=roles,
    )
    if variant == 'sales_manager':
        managed_team_ids = SalesTeamMember.objects.filter(
            user=user,
            membership_role='manager',
            team__site=site,
            team__enabled=True,
        ).values('team_id')
        leads = leads.filter(team_id__in=managed_team_ids)
        tasks = tasks.filter(team_id__in=managed_team_ids)
        title = '团队队列'
        description = '仅显示你所管理且已启用销售团队的可执行任务与线索。'
    else:
        leads = leads.filter(assignee=user)
        tasks = tasks.filter(owner_user=user)
        title = '我的工作'
        description = '仅显示分配给你的可执行任务与线索。'

    today_end = _end_of_local_day(now)
    task_stats = tasks.aggregate(
        total=Count('id'),
        overdue=Count('id', filter=Q(due_at__lt=now)),
        today=Count('id', filter=Q(due_at__gte=now, due_at__lte=today_end)),
    )
    lead_stats = leads.aggregate(
        total=Count('id'),
        new=Count('id', filter=Q(stage='new')),
    )

    task_rows = list(
        tasks.select_related('submission', 'company', 'opportunity', 'team', 'owner_user')
        .order_by(F('due_at').asc(nulls_last=True), 'id')[:limit]
    )
    task_list_params = {'sort': 'attention', 'page': 1, 'page_size': 25}
    tasks_href = f"{reverse('console:task_workspace_v2')}?{urlencode(task_list_params)}"
    overdue_tasks_href = f"{reverse('console:task_workspace_v2')}?{urlencode({**task_list_params, 'due': 'overdue'})}"
    today_tasks_href = f"{reverse('console:task_workspace_v2')}?{urlencode({**task_list_params, 'due': 'today'})}"
    task_items = []
    for task in task_rows:
        due_label, tone = _due_meta(task.due_at, now=now)
        task_items.append(_item(
            kind='task',
            item_id=task.id,
            title=_text(task.title, fallback=f'任务 #{task.id}', limit=120),
            meta=_task_meta(task, viewer_id=user.id, variant=variant),
            detail=(
                f'负责人：{_text(task.owner_user.get_full_name() or task.owner_user.get_username(), limit=60)}'
                if variant == 'sales_manager' else ''
            ),
            status_label='逾期' if tone == 'danger' else ('今天到期' if tone == 'warning' else '待处理'),
            status_tone=tone,
            href=(
                f"{reverse('console:task_workspace_detail_v2', args=(task.id,))}"
                f"?{urlencode(task_list_params)}"
            ),
            due_label=due_label,
            icon='check-square',
        ))

    first_contact_due = ExpressionWrapper(
        F('submitted_at') + timedelta(hours=4),
        output_field=DateTimeField(),
    )
    lead_rows = list(
        leads.select_related('assignee', 'team')
        .annotate(
            workbench_due_at=Case(
                When(
                    stage='new',
                    contacted_at__isnull=True,
                    next_follow_up_at__isnull=False,
                    then=Least(F('next_follow_up_at'), first_contact_due),
                ),
                When(stage='new', contacted_at__isnull=True, then=first_contact_due),
                When(next_follow_up_at__isnull=False, then=F('next_follow_up_at')),
                default=F('submitted_at'),
                output_field=DateTimeField(),
            )
        )
        .order_by('workbench_due_at', 'id')[:limit]
    )
    lead_items = []
    for lead in lead_rows:
        stage_label, tone = LEAD_STAGE_META.get(
            lead.stage,
            (lead.stage or '待处理', 'secondary'),
        )
        due_label, due_tone = _due_meta(_lead_due_at(lead), now=now)
        if due_tone == 'danger':
            tone = 'danger'
        if variant == 'sales_manager':
            if lead.assignee_id:
                detail = f'负责人：{_text(lead.assignee.get_full_name() or lead.assignee.get_username(), limit=60)}'
            else:
                detail = '尚未分配负责人'
        else:
            detail = ''
        lead_items.append(_item(
            kind='lead',
            item_id=lead.id,
            title=_text(lead.full_name or lead.company, fallback=f'线索 #{lead.id}', limit=120),
            meta=_lead_meta(lead),
            detail=detail,
            status_label=stage_label,
            status_tone=tone,
            href=reverse('console:lead_workspace_detail_v2', args=[lead.id]),
            due_label=due_label,
            icon='user-round-search',
        ))

    lead_href = reverse('console:lead_workspace_v2')
    total = int(task_stats['total'] or 0) + int(lead_stats['total'] or 0)
    return {
        'variant': variant,
        'title': title,
        'description': description,
        'metrics': [
            _metric('逾期任务', task_stats['overdue'] or 0, description='需要优先处理', href=overdue_tasks_href, tone='danger', icon='alarm-clock'),
            _metric('今日任务', task_stats['today'] or 0, description='今天到期', href=today_tasks_href, tone='warning', icon='calendar-check'),
            _metric('可执行任务', task_stats['total'] or 0, description='开放或处理中', href=tasks_href, tone='azure', icon='list-checks'),
            _metric('可执行线索', lead_stats['total'] or 0, description=f"其中新线索 {int(lead_stats['new'] or 0)} 条", href=lead_href, tone='success', icon='users'),
        ],
        'sections': [
            _section(
                key='tasks',
                title='优先任务',
                description='按到期时间从早到晚排列。',
                count=task_stats['total'] or 0,
                items=task_items,
                empty_title='当前没有可执行任务',
                empty_description='已完成、已取消或不在你权限范围内的任务不会显示。',
                action=_action('查看全部任务', tasks_href),
            ),
            _section(
                key='leads',
                title='待推进线索',
                description='按下一次应处理时间排列。',
                count=lead_stats['total'] or 0,
                items=lead_items,
                empty_title='当前没有可执行线索',
                empty_description='关闭、垃圾、已转换或不在你权限范围内的线索不会显示。',
                action=_action('查看全部线索', lead_href),
            ),
        ],
        'primary_action': _action('打开销售线索', lead_href, icon='users'),
        'empty_state': (
            _empty_state('工作已清空', '当前没有需要你处理的任务或线索。')
            if total == 0 else None
        ),
    }


def _outbox_item(row, *, kind: str, status_label: str, tone: str, href: str) -> dict[str, Any]:
    provider = _text(
        row.integration.provider.name,
        fallback=row.integration.provider.code,
        limit=60,
    )
    return _item(
        kind=kind,
        item_id=row.id,
        title=_text(row.event_name, fallback=f'回传事件 #{row.id}', limit=100),
        meta=f'{provider} · 线索 #{row.submission_id}',
        detail=redact_event_error(row.last_error),
        status_label=status_label,
        status_tone=tone,
        href=href,
        due_label=_timestamp_label(row.next_attempt_at or row.updated_at),
        icon='send',
    )


def _build_marketing_workbench(*, site, now, limit: int) -> dict[str, Any]:
    marketing_href = reverse('console:marketing_events')
    outboxes = LeadEventOutbox.objects.filter(
        submission__site=site,
        integration__site=site,
    )
    outbox_stats = outboxes.aggregate(
        failed=Count('id', filter=Q(status='failed')),
        partial=Count('id', filter=Q(status='partial')),
        retry_due=Count('id', filter=retryable_outbox_q(now=now)),
    )
    outbox_rows = outboxes.select_related('integration__provider')
    failed_rows = list(
        outbox_rows.filter(status='failed').order_by('-updated_at', '-id')[:limit]
    )
    partial_rows = list(
        outbox_rows.filter(status='partial').order_by('-updated_at', '-id')[:limit]
    )
    retry_rows = list(
        outbox_rows.filter(retryable_outbox_q(now=now))
        .order_by('next_attempt_at', 'id')[:limit]
    )

    inbound = LeadInboundEvent.objects.filter(integration__site=site, status='failed')
    inbound_count = inbound.count()
    inbound_rows = list(
        inbound.select_related('integration__provider')
        .order_by('-received_at', '-id')[:limit]
    )
    inbound_items = [
        _item(
            kind='inbound_failure',
            item_id=row.id,
            title=f'{_text(row.integration.provider.name, fallback=row.provider_code, limit=60)} 入站事件',
            meta=_text(row.event_type, fallback=row.external_event_id, limit=100),
            detail=redact_event_error(row.last_error),
            status_label='处理失败',
            status_tone='danger',
            href=marketing_href,
            due_label=_timestamp_label(row.received_at),
            icon='webhook',
        )
        for row in inbound_rows
    ]

    integrations = list(
        MarketingIntegration.objects.select_related('provider')
        .filter(site=site)
        .order_by('provider__code', 'integration_type', 'id')
    )
    by_key = {
        (
            str(row.provider.code or '').strip().lower(),
            str(row.integration_type or '').strip().lower(),
        ): row
        for row in integrations
    }
    blocker_items = []
    for provider_code, integration_type in EXPECTED_INTEGRATIONS:
        key = (provider_code, integration_type)
        integration = by_key.get(key)
        if integration is None:
            blocker_items.append(_item(
                kind='integration_missing',
                item_id='.'.join(key),
                title=f'{provider_code}.{integration_type}',
                meta='预期营销接入尚未创建',
                detail='缺少接入记录，无法进行端到端验收。',
                status_label='阻断',
                status_tone='danger',
                href=marketing_href,
                icon='plug-zap',
            ))
            continue
        failing_checks = [
            check
            for check in inspect_marketing_integration(integration)
            if check.get('required') and check.get('status') == 'fail'
        ]
        if failing_checks:
            blocker_items.append(_item(
                kind='integration_blocker',
                item_id=integration.id,
                title=_text(integration.name, fallback='.'.join(key), limit=100),
                meta=' · '.join(check['label'] for check in failing_checks[:3]),
                detail=_text(
                    failing_checks[0].get('detail'),
                    fallback='必填接入配置不完整。',
                ),
                status_label='阻断',
                status_tone='danger',
                href=reverse('console:marketing_integration_edit', args=[integration.id]),
                icon='plug-zap',
            ))

    failed_items = [
        _outbox_item(
            row,
            kind='outbox_failure',
            status_label='发送失败',
            tone='danger',
            href=marketing_href,
        )
        for row in failed_rows
    ]
    partial_items = [
        _outbox_item(
            row,
            kind='outbox_partial',
            status_label='部分成功',
            tone='warning',
            href=marketing_href,
        )
        for row in partial_rows
    ]
    retry_items = [
        _outbox_item(
            row,
            kind='retry_due',
            status_label='应重试',
            tone='warning',
            href=marketing_href,
        )
        for row in retry_rows
    ]
    failed_count = int(outbox_stats['failed'] or 0)
    partial_count = int(outbox_stats['partial'] or 0)
    retry_count = int(outbox_stats['retry_due'] or 0)
    blockers_count = len(blocker_items)
    total_unique_attention = failed_count + partial_count + inbound_count + blockers_count
    section_action = _action('打开事件运营', marketing_href)
    return {
        'variant': 'marketing_ops',
        'title': '营销待办',
        'description': '仅显示当前站点的营销投递、入站处理与接入阻断。',
        'metrics': [
            _metric('发送失败', failed_count, description='服务器回传失败', href=marketing_href, tone='danger', icon='circle-x'),
            _metric('部分成功', partial_count, description='平台仅接受部分数据', href=marketing_href, tone='warning', icon='triangle-alert'),
            _metric('重试到期', retry_count, description='已到重试时间', href=marketing_href, tone='warning', icon='refresh-cw'),
            _metric('入站失败', inbound_count, description='Webhook 处理失败', href=marketing_href, tone='danger', icon='webhook'),
        ],
        'sections': [
            _section(key='delivery_failures', title='营销发送失败', description='最近失败的服务器回传事件。', count=failed_count, items=failed_items, empty_title='没有发送失败', empty_description='当前站点没有失败的营销回传事件。', action=section_action),
            _section(key='partial_deliveries', title='部分成功', description='平台只处理了部分数据的事件。', count=partial_count, items=partial_items, empty_title='没有部分成功事件', empty_description='当前站点没有需要人工核对的部分成功事件。', action=section_action),
            _section(key='retry_due', title='重试到期', description='失败且未安排等待或已到下次尝试时间的事件。', count=retry_count, items=retry_items, empty_title='没有到期重试', empty_description='当前没有需要立即重试的回传事件。', action=section_action),
            _section(key='inbound_failures', title='入站处理失败', description='Meta、WhatsApp 等平台线索的接收异常。', count=inbound_count, items=inbound_items, empty_title='没有入站失败', empty_description='当前站点的入站事件没有失败记录。', action=section_action),
            _section(key='integration_blockers', title='接入阻断', description='完成真实平台验收前必须解决的配置问题。', count=blockers_count, items=blocker_items[:limit], empty_title='没有接入阻断', empty_description='已配置接入未发现必填项阻断。', action=section_action),
        ],
        'primary_action': _action('打开事件运营', marketing_href, icon='workflow'),
        'empty_state': (
            _empty_state('营销队列已清空', '当前没有失败、部分成功、入站异常或接入阻断。')
            if total_unique_attention == 0 else None
        ),
    }


def _build_system_workbench(*, site, limit: int) -> dict[str, Any]:
    system_users_href = reverse('console:system_users')
    marketing_events_href = reverse('console:marketing_events')
    marketing_forms_href = reverse('console:marketing_forms')
    event_attention_href = f'{marketing_events_href}?status=needs_attention'
    releases_href = reverse('console:releases')

    recognized_group_names = tuple(access.GROUP_BY_ROLE.values())
    unassigned_users = (
        get_user_model().objects.filter(is_active=True, is_superuser=False)
        .exclude(groups__name__in=recognized_group_names)
        .distinct()
    )
    unassigned_count = unassigned_users.count()
    unassigned_rows = list(unassigned_users.order_by('username', 'id')[:limit])
    unassigned_items = [
        _item(
            kind='unassigned_user',
            item_id=row.id,
            title=_text(
                row.get_full_name() or row.get_username(),
                fallback=f'账号 #{row.id}',
                limit=100,
            ),
            meta=_text(row.email, fallback='未填写邮箱', limit=100),
            detail='启用账号尚未分配任何后台角色。',
            status_label='权限异常',
            status_tone='danger',
            href=reverse('console:system_user_edit', args=[row.id]),
            icon='user-x',
        )
        for row in unassigned_rows
    ]

    failed_builds = ReleaseBuild.objects.filter(release__site=site, status='failed')
    failed_build_count = failed_builds.count()
    failed_build_rows = list(
        failed_builds.select_related('release').order_by('-created_at', '-id')[:limit]
    )
    failed_build_items = [
        _item(
            kind='release_build_failure',
            item_id=row.id,
            title=_text(row.build_key, fallback=f'构建 #{row.id}', limit=100),
            meta=_text(row.release.release_key, fallback=f'发布 #{row.release_id}', limit=100),
            detail=_text(row.log_excerpt, fallback='构建失败，尚无日志摘要。'),
            status_label='构建失败',
            status_tone='danger',
            href=releases_href,
            due_label=_timestamp_label(row.finished_at or row.created_at),
            icon='package-x',
        )
        for row in failed_build_rows
    ]

    failed_notifications = LeadSubmission.objects.filter(
        site=site,
        notification_status='failed',
    )
    notification_count = failed_notifications.count()
    notification_rows = list(
        failed_notifications.order_by('-updated_at', '-id')[:limit]
    )
    notification_items = [
        _item(
            kind='notification_failure',
            item_id=row.id,
            title=_text(row.full_name or row.company, fallback=f'线索 #{row.id}', limit=100),
            meta=f'线索 #{row.id}',
            detail=_text(row.notification_error, fallback='通知失败，尚无错误摘要。'),
            status_label='通知失败',
            status_tone='danger',
            href=reverse('console:lead_workspace_detail_v2', args=[row.id]),
            due_label=_timestamp_label(row.updated_at),
            icon='mail-x',
        )
        for row in notification_rows
    ]

    failed_outboxes = LeadEventOutbox.objects.filter(
        submission__site=site,
        integration__site=site,
        status='failed',
    )
    failed_inbound = LeadInboundEvent.objects.filter(
        integration__site=site,
        status='failed',
    )
    outbox_count = failed_outboxes.count()
    inbound_count = failed_inbound.count()
    outbox_rows = list(
        failed_outboxes.select_related('integration__provider')
        .order_by('-updated_at', '-id')[:limit]
    )
    inbound_rows = list(
        failed_inbound.select_related('integration__provider')
        .order_by('-updated_at', '-id')[:limit]
    )
    delivery_entries = [
        (
            row.next_attempt_at or row.updated_at,
            _outbox_item(
                row,
                kind='outbox_failure',
                status_label='回传失败',
                tone='danger',
                href=f'{event_attention_href}&direction=outbound',
            ),
        )
        for row in outbox_rows
    ]
    delivery_entries.extend(
        (
            row.updated_at,
            _item(
                kind='inbound_failure',
                item_id=row.id,
                title=f'{_text(row.integration.provider.name, fallback=row.provider_code, limit=60)} 入站事件',
                meta=_text(row.event_type, fallback=row.external_event_id, limit=100),
                detail=redact_event_error(row.last_error),
                status_label='入站失败',
                status_tone='danger',
                href=f'{event_attention_href}&direction=inbound',
                due_label=_timestamp_label(row.updated_at),
                icon='webhook',
            ),
        )
        for row in inbound_rows
    )
    delivery_entries.sort(
        key=lambda entry: (
            entry[0],
            str(entry[1]['kind']),
            str(entry[1]['id']),
        ),
        reverse=True,
    )
    delivery_items = [entry[1] for entry in delivery_entries[:limit]]
    delivery_count = outbox_count + inbound_count
    total = unassigned_count + failed_build_count + notification_count + delivery_count
    return {
        'variant': 'system_admin',
        'title': '系统运行概览',
        'description': '聚合当前站点的发布、通知、事件队列健康，以及全局账号治理问题。',
        'metrics': [
            _metric('未分配角色', unassigned_count, description='启用但无后台角色', href=system_users_href, tone='danger', icon='user-x'),
            _metric('发布失败', failed_build_count, description='当前站点构建失败', href=releases_href, tone='danger', icon='package-x'),
            _metric('事件异常', delivery_count, description='回传或入站失败', href=event_attention_href, tone='danger', icon='server-off'),
            _metric('通知失败', notification_count, description='线索邮件通知失败', href=marketing_forms_href, tone='warning', icon='mail-x'),
        ],
        'sections': [
            _section(key='access_anomalies', title='账号治理', description='系统级账号角色异常。', count=unassigned_count, items=unassigned_items, empty_title='没有账号权限异常', empty_description='所有启用账号均已分配后台角色。', action=_action('管理账号', system_users_href)),
            _section(key='release_failures', title='发布构建失败', description='当前站点最近的失败构建。', count=failed_build_count, items=failed_build_items, empty_title='没有发布失败', empty_description='当前站点没有失败的发布构建。', action=_action('查看发布', releases_href)),
            _section(key='delivery_anomalies', title='事件队列异常', description='当前站点的营销回传与入站失败。', count=delivery_count, items=delivery_items, empty_title='没有事件队列异常', empty_description='当前站点没有失败的回传或入站事件。', action=_action('查看事件运营', event_attention_href)),
            _section(key='notification_failures', title='线索通知失败', description='当前站点未成功发送的线索通知。', count=notification_count, items=notification_items, empty_title='没有通知失败', empty_description='当前站点没有失败的线索通知。', action=_action('查看获客表单', marketing_forms_href)),
        ],
        'primary_action': _action('管理账号', system_users_href, icon='settings'),
        'empty_state': (
            _empty_state('系统运行正常', '当前未发现需要处理的系统异常。')
            if total == 0 else None
        ),
    }


def _build_content_workbench(*, site, locale, user, limit: int) -> dict[str, Any]:
    """Build only the content queues granted for this exact site/locale."""

    locale_capabilities = effective_content_capabilities(user, site=site, locale=locale)
    # Asset and release routes are site-wide.  A locale-scoped grant for one
    # of those capability names is deliberately not usable here, just as it is
    # not usable at the underlying route.  Keeping the two policy resolutions
    # separate prevents the workbench from becoming a read-side bypass.
    site_capabilities = effective_content_capabilities(user, site=site, locale=None)
    content_href = f"{reverse('console:content_articles')}?locale={locale.locale_code}"
    assets_href = reverse('console:assets')
    releases_href = reverse('console:releases')
    metrics: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    total_attention = 0
    primary_action = None

    if CONTENT_READ in locale_capabilities:
        articles = Article.objects.filter(route__site=site, route__locale=locale)
        article_stats = articles.aggregate(
            total=Count('id'),
            draft=Count('id', filter=Q(status='draft')),
            seo_pending=Count('id', filter=Q(route__meta_description='')),
        )
        draft_count = int(article_stats['draft'] or 0)
        seo_pending_count = int(article_stats['seo_pending'] or 0)
        article_rows = list(
            articles.select_related('route', 'category')
            .filter(Q(status='draft') | Q(route__meta_description=''))
            .order_by('-updated_at', '-id')[:limit]
        )
        article_items = [
            _item(
                kind='article_attention',
                item_id=row.id,
                title=_text(row.title, fallback=f'文章 #{row.id}', limit=120),
                meta=' · '.join(filter(None, (
                    _text(row.category.name if row.category else '未分类', limit=60),
                    _text(row.route.path, limit=90),
                ))),
                detail=(
                    '缺少 Meta 描述。' if not (row.route.meta_description or '').strip()
                    else '草稿尚未进入已发布状态。'
                ),
                status_label='SEO 待补' if not (row.route.meta_description or '').strip() else '草稿',
                status_tone='warning',
                href=(
                    f"{reverse('console:article_edit', args=[row.id])}?locale={locale.locale_code}"
                    if (
                        CONTENT_WRITE in locale_capabilities
                        and (
                            row.status != 'published'
                            or CONTENT_SET_PUBLISHED in locale_capabilities
                        )
                    )
                    else content_href
                ),
                due_label=_timestamp_label(row.updated_at),
                icon='file-text',
            )
            for row in article_rows
        ]
        article_attention = len({row.id for row in article_rows})
        total_attention += article_attention
        metrics.extend([
            _metric('文章总数', article_stats['total'] or 0, description=f'{locale.locale_code.upper()} 内容', href=content_href, tone='azure', icon='files'),
            _metric('草稿', draft_count, description='尚未发布', href=content_href, tone='warning', icon='file-clock'),
            _metric('SEO 待补', seo_pending_count, description='缺少 Meta 描述', href=content_href, tone='warning', icon='search-x'),
        ])
        sections.append(_section(
            key='content_attention',
            title='内容待完善',
            description='当前获授权语言中仍处于草稿或缺少基础 SEO 的文章。',
            count=article_attention,
            items=article_items,
            empty_title='当前语言内容已整理',
            empty_description='没有草稿或缺少 Meta 描述的文章。',
            action=_action('打开文章工作区', content_href),
        ))
        primary_action = _action('打开文章工作区', content_href, icon='file-text')

    if ASSETS_READ in site_capabilities:
        assets = MediaAsset.objects.filter(site=site, status='active')
        asset_count = assets.count()
        asset_rows = list(assets.order_by('-updated_at', '-id')[:limit])
        asset_items = [
            _item(
                kind='asset',
                item_id=row.id,
                title=_text(row.title or row.original_name, fallback=f'素材 #{row.id}', limit=120),
                meta=_text(row.public_path, fallback=row.asset_type, limit=120),
                detail=f'{row.asset_type} · {int(row.file_size_bytes or 0)} bytes',
                status_label='可用',
                status_tone='success',
                href=assets_href,
                due_label=_timestamp_label(row.updated_at),
                icon='image',
            )
            for row in asset_rows
        ]
        metrics.append(_metric('可用素材', asset_count, description='当前站点', href=assets_href, tone='success', icon='images'))
        sections.append(_section(
            key='recent_assets',
            title='最近更新素材',
            description='仅显示当前站点中最近更新的可用素材。',
            count=asset_count,
            items=asset_items,
            empty_title='当前没有可用素材',
            empty_description='素材上传后会出现在这里。',
            action=_action('打开素材库', assets_href),
        ))
        if primary_action is None:
            primary_action = _action('打开素材库', assets_href, icon='images')

    if RELEASES_READ in site_capabilities:
        failed_builds = ReleaseBuild.objects.filter(release__site=site, status='failed')
        failed_count = failed_builds.count()
        failed_rows = list(failed_builds.select_related('release').order_by('-created_at', '-id')[:limit])
        failed_items = [
            _item(
                kind='preview_failure',
                item_id=row.id,
                title=_text(row.build_key, fallback=f'构建 #{row.id}', limit=120),
                meta=_text(row.release.release_key, fallback='预览快照', limit=100),
                detail='预览构建失败；内部路径和原始日志不会在内容工作台中显示。',
                status_label='构建失败',
                status_tone='danger',
                href=releases_href,
                due_label=_timestamp_label(row.finished_at or row.created_at),
                icon='package-x',
            )
            for row in failed_rows
        ]
        total_attention += failed_count
        metrics.append(_metric('预览失败', failed_count, description='当前站点', href=releases_href, tone='danger', icon='package-x'))
        sections.append(_section(
            key='preview_failures',
            title='预览构建失败',
            description='当前站点最近失败的预览任务；这里只提供运营级状态。',
            count=failed_count,
            items=failed_items,
            empty_title='没有预览构建失败',
            empty_description='当前站点没有失败的预览任务。',
            action=_action('查看发布记录', releases_href),
        ))
        if primary_action is None:
            primary_action = _action('查看发布记录', releases_href, icon='package-check')

    has_visible_scope = (
        CONTENT_READ in locale_capabilities
        or ASSETS_READ in site_capabilities
        or RELEASES_READ in site_capabilities
    )
    if not has_visible_scope:
        return {
            'variant': 'content_ops',
            'title': '内容待办',
            'description': '当前站点与语言没有授予任何内容能力。',
            'metrics': [],
            'sections': [],
            'primary_action': None,
            'empty_state': _empty_state(
                '没有可见内容范围',
                '请联系系统管理员核对站点、语言和具体能力授权。',
            ),
        }

    return {
        'variant': 'content_ops',
        'title': '内容待办',
        'description': f'仅显示当前站点与 {locale.locale_code.upper()} 语言范围内已授权的内容、素材和预览状态。',
        'metrics': metrics[:4],
        'sections': sections,
        'primary_action': primary_action,
        'empty_state': (
            _empty_state('内容队列已清空', '当前授权范围内没有需要立即处理的问题。')
            if total_attention == 0 else None
        ),
    }


def _build_restricted_workbench() -> dict[str, Any]:
    return {
        'variant': 'restricted',
        'title': '暂无可用工作台',
        'description': '当前账号尚未获得可执行工作台的数据权限。',
        'metrics': [],
        'sections': [],
        'primary_action': None,
        'empty_state': _empty_state(
            '没有可执行内容',
            '请联系系统管理员核对账号角色和数据范围。',
        ),
    }


def build_role_workbench(
    *,
    site,
    locale,
    user,
    params: Mapping[str, Any] | None,
    now=None,
) -> dict[str, Any]:
    """Build a bounded, template-friendly workbench for one effective role.

    Role precedence for temporary multi-role accounts is deterministic:
    system admin, marketing ops, sales manager, sales rep, content ops.  The
    request parameter mapping is used only for the bounded display limit; it
    can never select a role, site or data scope.

    The result always contains ``variant``, ``title``, ``description``,
    ``metrics``, ``sections``, ``primary_action`` and ``empty_state``.  A
    section's server-computed ``count`` may exceed ``len(items)``.
    """

    roles = _role_keys(user)
    effective_now = _normalized_now(now or timezone.now())
    limit = _item_limit(params)
    if access.ROLE_SYSTEM_ADMIN in roles:
        return _build_system_workbench(site=site, limit=limit)
    if access.ROLE_MARKETING_OPS in roles:
        return _build_marketing_workbench(site=site, now=effective_now, limit=limit)
    if access.ROLE_SALES_MANAGER in roles:
        return _build_sales_workbench(
            site=site,
            user=user,
            roles=roles,
            variant='sales_manager',
            now=effective_now,
            limit=limit,
        )
    if access.ROLE_SALES in roles:
        return _build_sales_workbench(
            site=site,
            user=user,
            roles=roles,
            variant='sales_rep',
            now=effective_now,
            limit=limit,
        )
    if ROLE_CONTENT_OPS in roles:
        return _build_content_workbench(
            site=site,
            locale=locale,
            user=user,
            limit=limit,
        )
    return _build_restricted_workbench()
