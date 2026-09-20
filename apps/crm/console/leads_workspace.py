from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from leads.google_data_manager import google_response_warnings
from leads.models import LeadEventOutbox, LeadFormDefinition, LeadSubmission
from leads.notifications import email_delivery_configured
from sitecore.models import SiteLocale

from .payloads import admin_locale_label, default_site_locale, format_admin_datetime
from .error_redaction import redact_event_error
from .access import (
    MARKETING_ROLES,
    SALES_ROLES,
    lead_attribution_assignee_queryset_for_user,
    lead_attribution_queryset_for_user,
    user_has_any_role,
)


TAB_KEYS = ('submissions', 'forms', 'outbox')
DEFAULT_PAGE_SIZE = {
    'submissions': 50,
    'forms': 20,
    'outbox': 50,
}
PAGE_SIZE_OPTIONS = (20, 50, 100)


def build_leads_workspace(request, locale_code: str | None = None) -> dict:
    site, current_locale = default_site_locale(locale_code)
    can_manage_marketing = user_has_any_role(request.user, MARKETING_ROLES)
    can_operate_sales = user_has_any_role(request.user, SALES_ROLES)
    allowed_tabs = TAB_KEYS if can_manage_marketing else ('submissions',)
    active_tab = request.GET.get('tab', 'submissions')
    if active_tab not in allowed_tabs:
        active_tab = 'submissions'

    locale_options = [
        {'value': 'all', 'label': '全部语言'},
        {'value': 'shared', 'label': '全站共享'},
    ]
    locale_options.extend(
        {
            'value': locale.locale_code,
            'label': admin_locale_label(locale),
        }
        for locale in SiteLocale.objects.filter(site=site, enabled=True).order_by('sort_order', 'locale_code')
    )

    context = {
        'title': '线索收件箱',
        'description': '集中处理网站、Meta、WhatsApp 与搜索广告带来的新线索。',
        'current_locale': current_locale,
        'active_tab': active_tab,
        'tabs': _build_tabs(site, request, locale_code, allowed_tabs),
        'panel_links': {
            'marketing': _build_href(reverse('console:marketing'), locale_code=locale_code) if can_manage_marketing else '',
            'smtp': _build_href(reverse('console:smtp_settings'), locale_code=locale_code) if can_manage_marketing else '',
            'forms': _build_href(reverse('console:leads'), locale_code=locale_code, tab='forms') if can_manage_marketing else '',
            'new_form': _build_href(reverse('console:lead_form_create'), locale_code=locale_code) if can_manage_marketing else '',
            'retry_pending': reverse('console:lead_outbox_dispatch_pending') if can_manage_marketing else '',
            'export_csv': _build_href(reverse('console:lead_submission_export_csv'), locale_code=locale_code),
            'export_xlsx': _build_href(reverse('console:lead_submission_export_xlsx'), locale_code=locale_code),
        },
        'table': {},
        'filters': {},
        'summary': [],
        'alerts': [],
        'context_note': '',
        'can_operate_sales': can_operate_sales,
    }

    if active_tab == 'submissions':
        context.update(_build_submissions_context(site, request, locale_code, locale_options))
    elif active_tab == 'forms':
        context.update(_build_forms_context(site, request, locale_code, locale_options))
    else:
        context.update(_build_outbox_context(site, request, locale_code))

    return context


def _build_tabs(site, request, locale_code: str | None, allowed_tabs: tuple[str, ...]) -> list[dict]:
    submissions = lead_attribution_queryset_for_user(
        LeadSubmission.objects.filter(site=site), request.user
    )
    totals = submissions.aggregate(
        submissions=Count('id'),
        won=Count('id', filter=Q(stage='won')),
    )
    forms_total = LeadFormDefinition.objects.filter(site=site).count() if 'forms' in allowed_tabs else 0
    outbox_total = LeadEventOutbox.objects.filter(submission__site=site).count() if 'outbox' in allowed_tabs else 0
    counts = {
        'submissions': totals['submissions'] or 0,
        'forms': forms_total,
        'outbox': outbox_total,
    }
    labels = {
        'submissions': '线索收件箱',
        'forms': '表单配置',
        'outbox': '回传队列',
    }
    return [
        {
            'key': key,
            'label': labels[key],
            'count': counts[key],
            'href': _build_href(reverse('console:leads'), locale_code=locale_code, tab=key),
            'active': request.GET.get('tab', 'submissions') == key,
        }
        for key in allowed_tabs
    ]


def _build_submissions_context(site, request, locale_code: str | None, locale_options: list[dict]) -> dict:
    can_operate_sales = user_has_any_role(request.user, SALES_ROLES)
    stage = request.GET.get('stage', 'all')
    lead_locale = request.GET.get('lead_locale', 'all')
    source_channel = request.GET.get('source_channel', 'all')
    assignee = request.GET.get('assignee', 'all')
    follow_up = request.GET.get('follow_up', 'all')
    query = (request.GET.get('q') or '').strip()
    page_size = _requested_page_size(request, 'submissions')
    now = timezone.now()
    overdue_cutoff = now - timedelta(hours=settings.SITEOS_LEAD_REMINDER_HOURS)

    qs = lead_attribution_queryset_for_user(
        LeadSubmission.objects.filter(site=site)
        .select_related('form', 'locale', 'assignee', 'team', 'duplicate_of'),
        request.user,
    ).order_by('-submitted_at', '-id')
    if stage != 'all':
        qs = qs.filter(stage=stage)
    if lead_locale == 'shared':
        qs = qs.filter(locale__isnull=True)
    elif lead_locale != 'all':
        qs = qs.filter(locale__locale_code=lead_locale)
    if source_channel != 'all':
        qs = qs.filter(source_channel=source_channel)
    if assignee == 'unassigned':
        qs = qs.filter(assignee__isnull=True)
    elif assignee.isdigit():
        qs = qs.filter(assignee_id=int(assignee))
    if follow_up == 'overdue':
        qs = qs.filter(stage='new', submitted_at__lte=overdue_cutoff)
    elif follow_up == 'due':
        qs = qs.filter(stage__in=['new', 'contacted', 'qualified'], next_follow_up_at__lte=now)
    elif follow_up == 'unassigned':
        qs = qs.filter(assignee__isnull=True, stage__in=['new', 'contacted', 'qualified'])
    elif follow_up == 'notification_failed':
        qs = qs.filter(notification_status='failed')
    if query:
        qs = qs.filter(
            Q(full_name__icontains=query)
            | Q(company__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
            | Q(message__icontains=query)
            | Q(follow_up_notes__icontains=query)
            | Q(source_detail__icontains=query)
            | Q(form__name__icontains=query)
            | Q(form__code__icontains=query)
        )

    page_obj = Paginator(qs, page_size).get_page(request.GET.get('page') or 1)

    rows = []
    for submission in page_obj.object_list:
        line_two = ' / '.join(
            part
            for part in [
                submission.company,
                submission.country,
                str((submission.payload_json or {}).get('product_category') or ''),
                str((submission.payload_json or {}).get('capacity') or ''),
            ]
            if part
        )
        next_follow_up = format_admin_datetime(submission.next_follow_up_at) if submission.next_follow_up_at else '未安排'
        is_overdue = submission.stage == 'new' and submission.submitted_at <= overdue_cutoff
        if submission.next_follow_up_at and submission.next_follow_up_at <= now and submission.stage not in {'won', 'lost', 'spam'}:
            is_overdue = True
        rows.append(
            {
                'id': submission.id,
                'title': submission.full_name or submission.company or submission.email or submission.phone or f'线索 #{submission.id}',
                'subtitle': line_two or submission.form.name,
                'locale': admin_locale_label(submission.locale) if submission.locale else '全站共享',
                'form': submission.form.name,
                'stage': _stage_meta(submission.stage),
                'source': _source_meta(submission.source_channel),
                'source_detail': submission.source_detail,
                'assignee': submission.assignee.get_username() if submission.assignee else '未分配',
                'contact': submission.email or submission.phone or '未填写',
                'next_follow_up': next_follow_up,
                'overdue': is_overdue,
                'notification': _status_meta(submission.notification_status, active_label='已通知', inactive_label='未通知'),
                'updated_at': format_admin_datetime(submission.submitted_at),
                'actions': [
                    {
                        'label': '跟进',
                        'href': _build_href(
                            reverse('console:lead_submission_edit', args=[submission.id]),
                            locale_code=(submission.locale.locale_code if submission.locale else locale_code or 'en'),
                        ),
                    }
                ] if can_operate_sales else [],
            }
        )

    counts = lead_attribution_queryset_for_user(
        LeadSubmission.objects.filter(site=site),
        request.user,
    ).aggregate(
        total=Count('id'),
        new=Count('id', filter=Q(stage='new')),
        contacted=Count('id', filter=Q(stage='contacted')),
        qualified=Count('id', filter=Q(stage='qualified')),
        won=Count('id', filter=Q(stage='won')),
        lost=Count('id', filter=Q(stage='lost')),
        overdue=Count('id', filter=Q(stage='new', submitted_at__lte=overdue_cutoff)),
        unassigned=Count('id', filter=Q(stage__in=['new', 'contacted', 'qualified'], assignee__isnull=True)),
        notification_failed=Count('id', filter=Q(notification_status='failed')),
    )

    active_filters = {
        'tab': 'submissions',
        'stage': stage,
        'lead_locale': lead_locale,
        'source_channel': source_channel,
        'assignee': assignee,
        'follow_up': follow_up,
        'q': query,
        'page_size': page_size,
    }
    assignee_options = [{'value': 'all', 'label': '全部'}, {'value': 'unassigned', 'label': '未分配'}]
    assignee_options.extend(
        {'value': str(user.id), 'label': user.get_username()}
        for user in lead_attribution_assignee_queryset_for_user(request.user, site=site)
    )
    alerts = []
    smtp_ready = email_delivery_configured()
    if not smtp_ready:
        alerts.append({'tone': 'warning', 'text': '新线索邮件通知尚未配置 SMTP 账号和授权密码。线索仍会入库，但邮件不会送达。'})
    if counts['overdue']:
        alerts.append({'tone': 'warning', 'text': f'{counts["overdue"]} 条新线索已超过 {settings.SITEOS_LEAD_REMINDER_HOURS} 小时未处理。'})
    if counts['notification_failed']:
        alerts.append({'tone': 'danger', 'text': f'{counts["notification_failed"]} 条线索邮件通知失败，请检查 SMTP 配置。'})

    return {
        'context_note': (
            f'当前结果 {page_obj.paginator.count} 条。这里先筛，再批量推进，再进入单条跟进。'
            if can_operate_sales
            else f'当前结果 {page_obj.paginator.count} 条。这是来源与归因只读视图，销售处置动作未开放。'
        ),
        'alerts': alerts,
        'export_links': {
            'csv': _build_href(reverse('console:lead_submission_export_csv'), locale_code=locale_code, **active_filters),
            'xlsx': _build_href(reverse('console:lead_submission_export_xlsx'), locale_code=locale_code, **active_filters),
        },
        'summary': [
            {
                'label': '全部线索',
                'value': counts['total'] or 0,
                'href': _build_href(reverse('console:leads'), locale_code=locale_code, tab='submissions', page_size=page_size),
            },
            {
                'label': '待跟进',
                'value': counts['new'] or 0,
                'href': _build_href(reverse('console:leads'), locale_code=locale_code, tab='submissions', stage='new', page_size=page_size),
            },
            {
                'label': '超时未处理',
                'value': counts['overdue'] or 0,
                'tone': 'danger',
                'href': _build_href(reverse('console:leads'), locale_code=locale_code, tab='submissions', follow_up='overdue', page_size=page_size),
            },
            {
                'label': '未分配',
                'value': counts['unassigned'] or 0,
                'href': _build_href(reverse('console:leads'), locale_code=locale_code, tab='submissions', follow_up='unassigned', page_size=page_size),
            },
        ],
        'filters': {
            'search': query,
            'clear_href': _build_href(reverse('console:leads'), locale_code=locale_code, tab='submissions'),
            'fields': [
                {
                    'name': 'stage',
                    'label': '阶段',
                    'value': stage,
                    'options': [
                        {'value': 'all', 'label': '全部'},
                        {'value': 'new', 'label': '新提交'},
                        {'value': 'contacted', 'label': '已联系'},
                        {'value': 'qualified', 'label': '高质量'},
                        {'value': 'won', 'label': '已成交'},
                        {'value': 'lost', 'label': '已流失'},
                    ],
                },
                {
                    'name': 'lead_locale',
                    'label': '语言',
                    'value': lead_locale,
                    'options': locale_options,
                },
                {
                    'name': 'source_channel',
                    'label': '来源',
                    'value': source_channel,
                    'options': [
                        {'value': 'all', 'label': '全部'},
                        {'value': 'meta_ads', 'label': 'Meta 广告'},
                        {'value': 'paid_search', 'label': '付费搜索'},
                        {'value': 'organic', 'label': '自然搜索'},
                        {'value': 'referral', 'label': '外部推荐'},
                        {'value': 'direct', 'label': '直接访问'},
                        {'value': 'website', 'label': '网站其他入口'},
                        {'value': 'whatsapp', 'label': 'WhatsApp'},
                        {'value': 'email', 'label': '邮件'},
                        {'value': 'other', 'label': '其他'},
                    ],
                },
                {
                    'name': 'assignee',
                    'label': '负责人',
                    'value': assignee,
                    'options': assignee_options,
                },
                {
                    'name': 'follow_up',
                    'label': '跟进提醒',
                    'value': follow_up,
                    'options': [
                        {'value': 'all', 'label': '全部'},
                        {'value': 'overdue', 'label': '超时未处理'},
                        {'value': 'due', 'label': '跟进已到期'},
                        {'value': 'unassigned', 'label': '未分配'},
                        {'value': 'notification_failed', 'label': '通知失败'},
                    ],
                },
            ],
        },
        'table': {
            'kind': 'submissions',
            'empty': '当前筛选条件下没有线索提交。',
            'columns': [
                '线索', '来源', '负责人', '阶段', '联系方式', '下次跟进', '提交时间',
                *(['操作'] if can_operate_sales else []),
            ],
            'rows': rows,
            'page': _page_meta(page_obj, locale_code, extra=active_filters),
        },
        'bulk': {
            'action': reverse('console:lead_submission_bulk_stage'),
            'stage_options': [
                {'value': 'contacted', 'label': '批量改为已联系'},
                {'value': 'lost', 'label': '批量改为已流失'},
                {'value': 'new', 'label': '批量恢复为新提交'},
            ],
            'next_href': _build_href(
                reverse('console:leads'),
                locale_code=locale_code,
                tab='submissions',
                stage=stage,
                lead_locale=lead_locale,
                source_channel=source_channel,
                assignee=assignee,
                follow_up=follow_up,
                q=query,
                page_size=page_size,
                page=page_obj.number,
            ),
        } if can_operate_sales else None,
    }


def _build_forms_context(site, request, locale_code: str | None, locale_options: list[dict]) -> dict:
    status = request.GET.get('status', 'all')
    channel = request.GET.get('channel', 'all')
    lead_locale = request.GET.get('lead_locale', 'all')
    query = (request.GET.get('q') or '').strip()
    page_size = _requested_page_size(request, 'forms')

    qs = LeadFormDefinition.objects.filter(site=site).select_related('locale').order_by('category', 'code')
    if status != 'all':
        qs = qs.filter(status=status)
    if channel != 'all':
        qs = qs.filter(channel=channel)
    if lead_locale == 'shared':
        qs = qs.filter(locale__isnull=True)
    elif lead_locale != 'all':
        qs = qs.filter(locale__locale_code=lead_locale)
    if query:
        qs = qs.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(category__icontains=query)
            | Q(scope_value__icontains=query)
        )

    page_obj = Paginator(qs, page_size).get_page(request.GET.get('page') or 1)
    rows = []
    for lead_form in page_obj.object_list:
        rows.append(
            {
                'title': lead_form.name,
                'subtitle': f'{lead_form.code} / {lead_form.category}',
                'locale': admin_locale_label(lead_form.locale) if lead_form.locale else '全站共享',
                'channel': lead_form.channel,
                'scope': f'{lead_form.scope_type}:{lead_form.scope_value}',
                'status': _status_meta(lead_form.status),
                'capi': _status_meta('active' if lead_form.capi_enabled else 'disabled', active_label='启用', inactive_label='停用'),
                'updated_at': format_admin_datetime(lead_form.updated_at),
                'actions': [
                    {
                        'label': '编辑',
                        'href': _build_href(
                            reverse('console:lead_form_edit', args=[lead_form.id]),
                            locale_code=(lead_form.locale.locale_code if lead_form.locale else locale_code or 'en'),
                        ),
                    },
                    {
                        'label': '接口',
                        'href': f'/api/leads/forms/{lead_form.code}/submit/',
                        'external': True,
                    },
                ],
            }
        )

    return {
        'context_note': f'当前结果 {page_obj.paginator.count} 条。这里维护公开表单定义，不在前台页面里硬编码字段。',
        'summary': [
            {'label': '表单数', 'value': LeadFormDefinition.objects.filter(site=site).count()},
            {'label': 'CAPI 已启用', 'value': LeadFormDefinition.objects.filter(site=site, capi_enabled=True).count()},
        ],
        'filters': {
            'search': query,
            'clear_href': _build_href(reverse('console:leads'), locale_code=locale_code, tab='forms'),
            'fields': [
                {
                    'name': 'status',
                    'label': '状态',
                    'value': status,
                    'options': [
                        {'value': 'all', 'label': '全部'},
                        {'value': 'active', 'label': '启用'},
                        {'value': 'disabled', 'label': '停用'},
                        {'value': 'archived', 'label': '归档'},
                    ],
                },
                {
                    'name': 'channel',
                    'label': '渠道',
                    'value': channel,
                    'options': [
                        {'value': 'all', 'label': '全部'},
                        {'value': 'website', 'label': '网站表单'},
                        {'value': 'whatsapp', 'label': 'WhatsApp'},
                        {'value': 'messenger', 'label': 'Messenger'},
                        {'value': 'phone', 'label': '电话'},
                        {'value': 'email', 'label': '邮箱'},
                        {'value': 'custom', 'label': '其他'},
                    ],
                },
                {
                    'name': 'lead_locale',
                    'label': '语言',
                    'value': lead_locale,
                    'options': locale_options,
                },
            ],
        },
        'table': {
            'kind': 'forms',
            'empty': '当前筛选条件下没有表单定义。',
            'columns': ['表单', '语言', '渠道', '作用域', 'CAPI', '状态', '更新', '操作'],
            'rows': rows,
            'page': _page_meta(page_obj, locale_code, extra={'tab': 'forms', 'status': status, 'channel': channel, 'lead_locale': lead_locale, 'q': query, 'page_size': page_size}),
        },
    }


def _build_outbox_context(site, request, locale_code: str | None) -> dict:
    status = request.GET.get('status', 'all')
    provider = request.GET.get('provider', 'all')
    query = (request.GET.get('q') or '').strip()
    page_size = _requested_page_size(request, 'outbox')

    qs = (
        LeadEventOutbox.objects.filter(submission__site=site)
        .select_related('submission', 'submission__form', 'integration', 'integration__provider')
        .order_by('-created_at', '-id')
    )
    if status != 'all':
        qs = qs.filter(status=status)
    if provider != 'all':
        qs = qs.filter(integration__provider__code=provider)
    if query:
        qs = qs.filter(
            Q(event_name__icontains=query)
            | Q(integration__name__icontains=query)
            | Q(last_error__icontains=query)
            | Q(submission__form__code__icontains=query)
        )

    page_obj = Paginator(qs, page_size).get_page(request.GET.get('page') or 1)
    rows = []
    for item in page_obj.object_list:
        submission = item.submission
        warnings = google_response_warnings(item.response_json)
        note = _outbox_lifecycle_note(item)
        if warnings:
            warning_note = f'平台警告：{"；".join(warnings[:2])}'
            note = f'{note} {warning_note}'.strip()
        actions = []
        if item.status in {'pending', 'failed', 'processing'}:
            actions.append(
                {'label': '重试' if item.status == 'failed' else '立即处理', 'post_href': reverse('console:lead_outbox_dispatch', args=[item.id])}
            )
        actions.append(
            {
                'label': '查看线索',
                'href': _build_href(
                    reverse('console:lead_submission_edit', args=[submission.id]),
                    locale_code=(submission.locale.locale_code if submission.locale else locale_code or 'en'),
                ),
            }
        )
        rows.append(
            {
                'title': item.event_name,
                'subtitle': f'线索 #{submission.id} / {submission.form.name}',
                'provider': getattr(item.integration.provider, 'name', 'Meta'),
                'stage': _stage_meta(item.stage_key),
                'status': _status_meta(item.status),
                'attempts': item.attempts,
                'updated_at': format_admin_datetime(item.updated_at),
                'error': redact_event_error(item.last_error) if item.last_error else '',
                'note': note,
                'actions': actions,
            }
        )

    counts = LeadEventOutbox.objects.filter(submission__site=site).aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status='pending')),
        sending=Count('id', filter=Q(status='sending')),
        processing=Count('id', filter=Q(status='processing')),
        partial=Count('id', filter=Q(status='partial')),
        failed=Count('id', filter=Q(status='failed')),
        validated=Count('id', filter=Q(status='validated')),
        sent=Count('id', filter=Q(status='sent')),
        skipped=Count('id', filter=Q(status='skipped')),
    )

    return {
        'context_note': f'当前结果 {page_obj.paginator.count} 条。这里先看失败，再重试，不需要在营销页来回跳。',
        'summary': [
            {'label': '内部已创建', 'value': counts['total'] or 0},
            {'label': '等待发送', 'value': counts['pending'] or 0},
            {'label': '我方发送中', 'value': counts['sending'] or 0},
            {'label': '平台处理中', 'value': counts['processing'] or 0},
            {'label': '部分成功', 'value': counts['partial'] or 0},
            {'label': '失败', 'value': counts['failed'] or 0},
            {'label': '仅校验', 'value': counts['validated'] or 0},
            {'label': '平台已接收', 'value': counts['sent'] or 0},
            {'label': '未发送', 'value': counts['skipped'] or 0},
        ],
        'filters': {
            'search': query,
            'clear_href': _build_href(reverse('console:leads'), locale_code=locale_code, tab='outbox'),
            'fields': [
                {
                    'name': 'status',
                    'label': '状态',
                    'value': status,
                    'options': [
                        {'value': 'all', 'label': '全部'},
                        {'value': 'pending', 'label': '等待发送'},
                        {'value': 'sending', 'label': '我方发送中'},
                        {'value': 'processing', 'label': '平台处理中'},
                        {'value': 'partial', 'label': '部分成功'},
                        {'value': 'failed', 'label': '失败'},
                        {'value': 'validated', 'label': '仅校验'},
                        {'value': 'sent', 'label': '平台已接收'},
                        {'value': 'skipped', 'label': '未发送'},
                    ],
                },
                {
                    'name': 'provider',
                    'label': '平台',
                    'value': provider,
                    'options': [
                        {'value': 'all', 'label': '全部'},
                        {'value': 'meta', 'label': 'Meta'},
                        {'value': 'google', 'label': 'Google'},
                    ],
                },
            ],
        },
        'table': {
            'kind': 'outbox',
            'empty': '当前筛选条件下没有回传队列记录。',
            'columns': ['事件', '平台', '阶段', '状态', '次数', '更新时间', '操作'],
            'rows': rows,
            'page': _page_meta(page_obj, locale_code, extra={'tab': 'outbox', 'status': status, 'provider': provider, 'q': query, 'page_size': page_size}),
        },
    }


def _requested_page_size(request, tab: str) -> int:
    try:
        requested = int(request.GET.get('page_size') or DEFAULT_PAGE_SIZE[tab])
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE[tab]
    return requested if requested in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE[tab]


def _page_meta(page_obj, locale_code: str | None, *, extra: dict[str, object]) -> dict:
    current = page_obj.number
    total = page_obj.paginator.num_pages
    params = {key: value for key, value in extra.items() if value and value != 'all'}
    prev_href = next_href = None
    if page_obj.has_previous():
        prev_href = _build_href(reverse('console:leads'), locale_code=locale_code, page=page_obj.previous_page_number(), **params)
    if page_obj.has_next():
        next_href = _build_href(reverse('console:leads'), locale_code=locale_code, page=page_obj.next_page_number(), **params)
    pages = []
    for page_number in page_obj.paginator.get_elided_page_range(current, on_each_side=2, on_ends=1):
        if page_number == page_obj.paginator.ELLIPSIS:
            pages.append({'label': '...', 'ellipsis': True})
        else:
            pages.append(
                {
                    'label': str(page_number),
                    'active': page_number == current,
                    'href': _build_href(reverse('console:leads'), locale_code=locale_code, page=page_number, **params),
                }
            )

    preserved_params = []
    if locale_code:
        preserved_params.append({'name': 'locale', 'value': locale_code})
    preserved_params.extend(
        {'name': key, 'value': value}
        for key, value in params.items()
        if key != 'page_size'
    )

    return {
        'label': f'共 {page_obj.paginator.count} 条',
        'range_label': f'{page_obj.start_index()}-{page_obj.end_index()} / {page_obj.paginator.count}',
        'prev_href': prev_href,
        'next_href': next_href,
        'pages': pages,
        'page_size': page_obj.paginator.per_page,
        'page_size_options': [
            {'value': option, 'selected': option == page_obj.paginator.per_page}
            for option in PAGE_SIZE_OPTIONS
        ],
        'preserved_params': preserved_params,
    }


def _build_href(base: str, locale_code: str | None = None, **params) -> str:
    query = {}
    if locale_code:
        query['locale'] = locale_code
    for key, value in params.items():
        if value not in (None, '', 'all'):
            query[key] = value
    if not query:
        return base
    return f'{base}?{urlencode(query)}'


def _compact_message(value: str | None) -> str:
    if not value:
        return ''
    text = ' '.join(str(value).split())
    return text[:56] + ('…' if len(text) > 56 else '')


def _outbox_lifecycle_note(item) -> str:
    created = format_admin_datetime(item.created_at)
    if item.status == 'pending':
        return f'内部已创建于 {created}，正在等待发送。'
    if item.status == 'sending':
        return f'内部已创建于 {created}，我方正在发送。'
    if item.status == 'processing':
        sent_at = format_admin_datetime(item.dispatched_at) if item.dispatched_at else '时间未记录'
        return f'我方已于 {sent_at} 提交，平台仍在处理；匹配状态未知。'
    if item.status == 'validated':
        return '仅完成 API 格式校验，未正式写入广告平台。'
    if item.status == 'partial':
        return '平台只处理了部分记录；为避免重复回传，系统不会自动重试，需人工核对失败记录。'
    if item.status == 'sent':
        sent_at = format_admin_datetime(item.dispatched_at) if item.dispatched_at else '时间未记录'
        return f'我方已于 {sent_at} 发送，平台 API 已接收；逐条匹配状态未知。'
    if item.status == 'failed':
        return f'内部已创建于 {created}，发送失败，可在排除错误后重试。'
    if item.status == 'skipped':
        return '事件未发送：当前数据或同意条件不足；平台匹配不适用。'
    return f'内部记录状态：{item.status}。'


def _stage_meta(value: str) -> dict:
    mapping = {
        'new': ('新提交', 'amber'),
        'contacted': ('已联系', 'blue'),
        'qualified': ('高质量', 'blue'),
        'won': ('已成交', 'green'),
        'lost': ('已流失', 'red'),
        'spam': ('垃圾线索', 'red'),
    }
    label, tone = mapping.get(value, (value, 'slate'))
    return {'label': label, 'tone': tone}


def _source_meta(value: str) -> dict:
    mapping = {
        'meta_ads': ('Meta 广告', 'blue'),
        'paid_search': ('付费搜索', 'blue'),
        'organic': ('自然搜索', 'green'),
        'referral': ('外部推荐', 'slate'),
        'direct': ('直接访问', 'slate'),
        'website': ('网站', 'slate'),
        'whatsapp': ('WhatsApp', 'green'),
        'email': ('邮件', 'slate'),
        'other': ('其他', 'slate'),
    }
    label, tone = mapping.get(value, (value or '网站', 'slate'))
    return {'label': label, 'tone': tone}


def _status_meta(value: str, *, active_label: str = '启用', inactive_label: str = '停用') -> dict:
    mapping = {
        'active': (active_label, 'green'),
        'disabled': (inactive_label, 'amber'),
        'archived': ('归档', 'slate'),
        'pending': ('等待发送', 'amber'),
        'sending': ('我方发送中', 'blue'),
        'failed': ('失败', 'red'),
        'processing': ('平台处理中', 'blue'),
        'partial': ('部分成功', 'amber'),
        'validated': ('仅校验', 'blue'),
        'sent': ('平台已接收', 'green'),
        'skipped': ('未发送', 'slate'),
        'disabled': ('未配置', 'slate'),
    }
    label, tone = mapping.get(value, (value, 'slate'))
    return {'label': label, 'tone': tone}
