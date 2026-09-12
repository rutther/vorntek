from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from leads.models import (
    LeadConversion,
    Opportunity,
    SalesTeamMember,
    Task,
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppTemplate,
)

from .access import (
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    task_queryset_for_user,
    user_role_keys,
    whatsapp_conversation_queryset_for_user,
)
from .capabilities import SalesCapability, can_sales


STATUS_OPTIONS = (
    ('active', '进行中'),
    ('open', '仅开放'),
    ('pending', '等待中'),
    ('closed', '已关闭'),
    ('blocked', '已阻止'),
    ('all', '全部状态'),
)
OWNER_OPTIONS = (
    ('all', '全部可见'),
    ('mine', '我的会话'),
    ('unassigned', '未认领'),
)
SORT_OPTIONS = (
    ('priority', '优先处理'),
    ('recent', '最近消息'),
    ('oldest', '最早待处理'),
)
MESSAGE_STATUS = {
    'draft': ('草稿', 'muted'),
    'queued': ('排队中', 'blue'),
    'sending': ('发送中', 'blue'),
    'sent': ('已发送', 'muted'),
    'delivered': ('已送达', 'green'),
    'read': ('已读', 'green'),
    'received': ('已接收', 'green'),
    'recorded': ('内部记录', 'amber'),
    'failed': ('发送失败', 'red'),
    'canceled': ('已取消', 'muted'),
}


def _clean_choice(value: str, options, default: str) -> str:
    valid = {key for key, _label in options}
    normalized = str(value or '').strip().lower()
    return normalized if normalized in valid else default


def _time_label(value, *, now) -> str:
    if not value:
        return '暂无消息'
    local_value = timezone.localtime(value)
    local_now = timezone.localtime(now)
    if local_value.date() == local_now.date():
        return local_value.strftime('%H:%M')
    if local_value.date() == (local_now - timedelta(days=1)).date():
        return '昨天'
    if local_value.year == local_now.year:
        return local_value.strftime('%m-%d')
    return local_value.strftime('%Y-%m-%d')


def _query_string(filters: dict, *, page: int | None = None) -> str:
    values = {
        'q': filters['query'],
        'status': filters['status'],
        'owner': filters['owner'],
        'unread': '1' if filters['unread'] else '',
        'sort': filters['sort'],
    }
    if page is not None:
        values['page'] = page
    return urlencode({key: value for key, value in values.items() if value not in {'', None}})


def _detail_url(conversation_id: int, filters: dict, *, page: int) -> str:
    query = _query_string(filters, page=page)
    base = reverse('console:whatsapp_workspace_detail_v2', args=[conversation_id])
    return f'{base}?{query}' if query else base


def _conversation_queryset(*, site, user):
    return whatsapp_conversation_queryset_for_user(
        WhatsAppConversation.objects.filter(site=site).select_related(
            'integration', 'submission', 'owner_user', 'team', 'company', 'contact',
        ),
        user,
    )


def _apply_filters(queryset, *, request, user) -> tuple[object, dict]:
    filters = {
        'query': str(request.GET.get('q') or '').strip()[:200],
        'status': _clean_choice(request.GET.get('status'), STATUS_OPTIONS, 'active'),
        'owner': _clean_choice(request.GET.get('owner'), OWNER_OPTIONS, 'all'),
        'unread': str(request.GET.get('unread') or '').strip() == '1',
        'sort': _clean_choice(request.GET.get('sort'), SORT_OPTIONS, 'priority'),
    }
    if filters['query']:
        query = filters['query']
        queryset = queryset.filter(
            Q(display_name__icontains=query)
            | Q(external_contact_id__icontains=query)
            | Q(submission__full_name__icontains=query)
            | Q(submission__company__icontains=query)
            | Q(submission__email__icontains=query)
            | Q(contact__full_name__icontains=query)
            | Q(company__name__icontains=query)
            | Q(last_message_preview__icontains=query)
        )
    if filters['status'] == 'active':
        queryset = queryset.filter(status__in=['open', 'pending'])
    elif filters['status'] != 'all':
        queryset = queryset.filter(status=filters['status'])
    if filters['owner'] == 'mine':
        queryset = queryset.filter(owner_user=user)
    elif filters['owner'] == 'unassigned':
        queryset = queryset.filter(owner_user__isnull=True)
    if filters['unread']:
        queryset = queryset.filter(unread_count__gt=0)
    if filters['sort'] == 'recent':
        queryset = queryset.order_by('-last_message_at', '-updated_at', '-id')
    elif filters['sort'] == 'oldest':
        queryset = queryset.order_by('last_message_at', 'id')
    else:
        queryset = queryset.order_by('-unread_count', 'last_message_at', 'id')
    return queryset, filters


def _sla(conversation: WhatsAppConversation, *, now) -> dict:
    config = dict(conversation.integration.config_json or {})
    minutes = max(int(config.get('reply_sla_minutes') or 120), 15)
    if conversation.unread_count <= 0 or not conversation.last_inbound_at:
        return {'label': '已响应', 'tone': 'muted', 'overdue': False}
    due_at = conversation.last_inbound_at + timedelta(minutes=minutes)
    if due_at <= now:
        return {'label': '回复超时', 'tone': 'red', 'overdue': True}
    remaining = max(int((due_at - now).total_seconds() // 60), 1)
    return {'label': f'{remaining} 分钟内回复', 'tone': 'amber', 'overdue': False}


def _row(conversation: WhatsAppConversation, *, now, filters: dict, page: int) -> dict:
    title = (
        conversation.display_name
        or (conversation.submission.full_name if conversation.submission_id else '')
        or conversation.external_contact_id
    )
    company = (
        conversation.company.name
        if conversation.company_id
        else conversation.submission.company
        if conversation.submission_id
        else ''
    )
    return {
        'id': conversation.id,
        'title': title,
        'initials': ''.join(part[0] for part in title.split()[:2]).upper() or 'WA',
        'company': company or '未关联企业',
        'preview': conversation.last_message_preview or '尚无消息摘要',
        'time_label': _time_label(conversation.last_message_at, now=now),
        'unread_count': conversation.unread_count,
        'owner': (
            conversation.owner_user.get_full_name() or conversation.owner_user.get_username()
            if conversation.owner_user_id else '未认领'
        ),
        'status': conversation.status,
        'sla': _sla(conversation, now=now),
        'detail_url': _detail_url(conversation.id, filters, page=page),
    }


def _message(message: WhatsAppMessage) -> dict:
    status_label, status_tone = MESSAGE_STATUS.get(message.status, (message.status, 'muted'))
    return {
        'id': message.id,
        'direction': message.direction,
        'type': message.message_type,
        'body': message.body or f'[{message.message_type}]',
        'time': timezone.localtime(
            message.provider_timestamp or message.created_at
        ).strftime('%H:%M'),
        'date': timezone.localtime(
            message.provider_timestamp or message.created_at
        ).date().isoformat(),
        'date_label': timezone.localtime(
            message.provider_timestamp or message.created_at
        ).strftime('%Y年%m月%d日'),
        'status': message.status,
        'status_label': status_label,
        'status_tone': status_tone,
        'actor': (
            message.actor_user.get_full_name() or message.actor_user.get_username()
            if message.actor_user_id else ''
        ),
        'error': message.error_message,
        'retryable': message.retryable,
        'media': [
            {
                'id': media.id,
                'type': media.media_type,
                'name': media.original_name or media.media_type,
                'status': media.status,
                'error': media.error_message,
                'size': media.file_size_bytes,
                'download_url': (
                    reverse(
                        'console:whatsapp_media_download_v2',
                        args=[message.conversation_id, message.id, media.id],
                    )
                    if media.status == 'ready' else ''
                ),
            }
            for media in message.media_items.all()
        ],
    }


def _message_groups(conversation: WhatsAppConversation) -> list[dict]:
    messages = list(
        conversation.messages.select_related('actor_user', 'template')
        .prefetch_related('media_items')
        .order_by('provider_timestamp', 'created_at', 'id')[:300]
    )
    groups = []
    for record in messages:
        item = _message(record)
        if not groups or groups[-1]['date'] != item['date']:
            groups.append({
                'date': item['date'],
                'label': item['date_label'],
                'messages': [],
            })
        groups[-1]['messages'].append(item)
    return groups


def _related_context(conversation: WhatsAppConversation, user) -> dict:
    conversion = None
    if conversation.submission_id:
        conversion = (
            LeadConversion.objects.select_related('company', 'contact', 'opportunity')
            .filter(submission_id=conversation.submission_id)
            .first()
        )
    opportunity = conversion.opportunity if conversion else None
    if opportunity is None and conversation.company_id:
        opportunity = (
            Opportunity.objects.filter(company_id=conversation.company_id)
            .order_by('-updated_at', '-id')
            .first()
        )
    target_q = Q()
    has_target = False
    for field_name, value in (
        ('submission_id', conversation.submission_id),
        ('company_id', conversation.company_id),
        ('contact_id', conversation.contact_id),
        ('opportunity_id', opportunity.id if opportunity else None),
    ):
        if value:
            target_q |= Q(**{field_name: value})
            has_target = True
    tasks = []
    if has_target:
        tasks = list(
            task_queryset_for_user(
                Task.objects.filter(target_q).select_related('owner_user'),
                user,
            )
            .filter(status__in=['open', 'in_progress'])
            .order_by('due_at', '-priority', 'id')[:5]
        )
    return {
        'submission': conversation.submission,
        'company': conversation.company or (conversion.company if conversion else None),
        'contact': conversation.contact or (conversion.contact if conversion else None),
        'opportunity': opportunity,
        'tasks': tasks,
    }


def _assignment_options(conversation: WhatsAppConversation, user) -> list[dict]:
    if not conversation.team_id:
        return []
    roles = user_role_keys(user)
    if ROLE_SYSTEM_ADMIN not in roles and ROLE_SALES_MANAGER not in roles:
        return []
    members = SalesTeamMember.objects.filter(
        team_id=conversation.team_id,
        user__is_active=True,
    ).select_related('user').order_by('user__username')
    return [
        {
            'id': member.user_id,
            'label': member.user.get_full_name() or member.user.get_username(),
        }
        for member in members
    ]


def build_whatsapp_workspace_v2(*, request, site, conversation_id: int | None = None) -> dict:
    now = timezone.now()
    base_queryset = _conversation_queryset(site=site, user=request.user)
    total = base_queryset.count()
    unread = base_queryset.filter(unread_count__gt=0).count()
    unassigned = base_queryset.filter(owner_user__isnull=True, status__in=['open', 'pending']).count()
    filtered_queryset, filters = _apply_filters(
        base_queryset,
        request=request,
        user=request.user,
    )
    paginator = Paginator(filtered_queryset, 50)
    page = paginator.get_page(request.GET.get('page') or 1)
    rows = [_row(item, now=now, filters=filters, page=page.number) for item in page.object_list]
    selected = None
    if conversation_id is not None:
        selected = base_queryset.filter(pk=conversation_id).first()

    detail = None
    if selected is not None:
        roles = user_role_keys(request.user)
        can_write = can_sales(request.user, SalesCapability.WRITE, record=selected)
        is_unassigned_member = bool(
            selected.owner_user_id is None
            and selected.team_id
            and ROLE_SALES in roles
            and SalesTeamMember.objects.filter(
                team_id=selected.team_id,
                user=request.user,
                team__enabled=True,
            ).exists()
        )
        can_claim = is_unassigned_member or can_sales(
            request.user,
            SalesCapability.ASSIGN,
            record=selected,
        )
        approved_templates = list(
            WhatsAppTemplate.objects.filter(
                integration=selected.integration,
                status='approved',
            ).order_by('category', 'name', 'language', '-version')
        )
        window_open = bool(
            selected.service_window_expires_at
            and selected.service_window_expires_at > now
        )
        integration_mode = str(
            (selected.integration.config_json or {}).get('delivery_mode') or 'mock'
        ).strip().lower()
        send_block_reason = ''
        if not can_write:
            send_block_reason = '请先认领会话，或联系团队负责人转交。'
        elif not selected.integration.enabled:
            send_block_reason = 'WhatsApp 接入尚未启用，当前只能查看历史与测试数据。'
        elif selected.status == 'blocked' or selected.opted_out_at:
            send_block_reason = '客户已停止联系，发送已被后端策略阻止。'
        elif selected.status == 'closed':
            send_block_reason = '会话已关闭，请先重新打开再发送。'
        elif not window_open and not approved_templates:
            send_block_reason = '24 小时窗口已关闭，且没有可用的已批准模板。'
        detail = {
            'record': selected,
            'message_groups': _message_groups(selected),
            'related': _related_context(selected, request.user),
            'can_write': can_write,
            'can_claim': can_claim,
            'can_assign': can_sales(request.user, SalesCapability.ASSIGN, record=selected),
            'assignment_options': _assignment_options(selected, request.user),
            'window_open': window_open,
            'window_label': (
                f'客服窗口至 {timezone.localtime(selected.service_window_expires_at).strftime("%m-%d %H:%M")}'
                if window_open else '24 小时客服窗口已关闭'
            ),
            'approved_templates': approved_templates,
            'send_block_reason': send_block_reason,
            'delivery_mode': integration_mode,
            'urls': {
                'send': reverse('console:whatsapp_send_v2', args=[selected.id]),
                'note': reverse('console:whatsapp_note_v2', args=[selected.id]),
                'assign': reverse('console:whatsapp_assign_v2', args=[selected.id]),
                'state': reverse('console:whatsapp_state_v2', args=[selected.id]),
                'read': reverse('console:whatsapp_mark_read_v2', args=[selected.id]),
                'disposition': (
                    reverse('console:lead_disposition_v2', args=[selected.submission_id])
                    if selected.submission_id else ''
                ),
                'retry_base': reverse('console:whatsapp_retry_v2', args=[selected.id, 0]).rsplit('/0/', 1)[0],
            },
        }
    return {
        'site': site,
        'filters': filters,
        'rows': rows,
        'selected': detail,
        'selected_missing': conversation_id is not None and selected is None,
        'stats': {
            'total': total,
            'unread': unread,
            'unassigned': unassigned,
            'filtered': paginator.count,
        },
        'options': {
            'status': STATUS_OPTIONS,
            'owner': OWNER_OPTIONS,
            'sort': SORT_OPTIONS,
        },
        'page': page,
        'list_url': reverse('console:whatsapp_workspace_v2'),
        'clear_url': reverse('console:whatsapp_workspace_v2'),
        'canonical_query': _query_string(filters, page=page.number),
    }
