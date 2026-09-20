from __future__ import annotations

from django.db.models import Count, Q
from django.urls import reverse

from leads.models import LeadEventOutbox, LeadFormDefinition, LeadSubmission

from .error_redaction import redact_event_error
from .payloads import (
    admin_locale_label,
    badge,
    build_row,
    default_site_locale,
    format_admin_datetime,
    link_action,
    locale_query_href,
    make_page,
    post_action,
    status_label,
    status_tone,
)


def lead_stage_label(value: str) -> str:
    return {
        'new': '新提交',
        'contacted': '已联系',
        'qualified': '高质量',
        'won': '已成交',
        'lost': '已流失',
        'spam': '垃圾线索',
    }.get(value, value)


def lead_stage_tone(value: str) -> str:
    if value == 'won':
        return 'green'
    if value in {'contacted', 'qualified'}:
        return 'blue'
    if value in {'lost', 'spam'}:
        return 'red'
    return 'amber'


def locale_filter_options() -> list[dict[str, str]]:
    return [
        {'key': 'all', 'label': '全部'},
        {'key': 'en', 'label': '英语'},
        {'key': 'ar', 'label': '阿拉伯语'},
        {'key': 'fr', 'label': '法语'},
        {'key': 'es', 'label': '西班牙语'},
        {'key': 'ru', 'label': '俄语'},
        {'key': 'shared', 'label': '未绑定/共享'},
    ]


def leads_payload(locale_code: str | None = None) -> dict:
    site, _locale = default_site_locale(locale_code)
    forms_qs = LeadFormDefinition.objects.filter(site=site).select_related('locale').order_by('category', 'code')[:120]
    submissions_qs = (
        LeadSubmission.objects.filter(site=site)
        .select_related('form', 'locale', 'route', 'cta')
        .order_by('-submitted_at', '-id')[:200]
    )
    outbox_qs = (
        LeadEventOutbox.objects.filter(submission__site=site)
        .select_related('submission', 'submission__form', 'integration', 'integration__provider')
        .order_by('-created_at', '-id')[:200]
    )

    stage_counts = LeadSubmission.objects.filter(site=site).aggregate(
        total=Count('id'),
        new=Count('id', filter=Q(stage='new')),
        contacted=Count('id', filter=Q(stage='contacted')),
        qualified=Count('id', filter=Q(stage='qualified')),
        won=Count('id', filter=Q(stage='won')),
        lost=Count('id', filter=Q(stage='lost')),
    )
    outbox_counts = LeadEventOutbox.objects.filter(submission__site=site).aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status='pending')),
        failed=Count('id', filter=Q(status='failed')),
        sent=Count('id', filter=Q(status='sent')),
    )

    submission_rows = []
    for row in submissions_qs:
        row_locale_code = row.locale.locale_code if row.locale else 'shared'
        row_locale_label = admin_locale_label(row.locale) if row.locale else '共享'
        title = row.full_name or row.company or row.email or row.phone or f'线索 {row.id}'
        subtitle = ' / '.join(part for part in [row.company, row.email or row.phone] if part)
        submission_rows.append(
            build_row(
                row_id=f'lead-{row.id}',
                tab_key='submissions',
                filter_keys=[row.stage, row_locale_code],
                search=' '.join([title, row.form.name, row.email, row.phone, row.company, row.country, row.source_url, row.message]),
                title=title,
                subtitle=subtitle or row.form.name,
                cells={
                    'locale': row_locale_label,
                    'form': row.form.name,
                    'stage': badge(lead_stage_label(row.stage), lead_stage_tone(row.stage)),
                    'contact': row.email or row.phone or '未填联系方式',
                    'updatedAt': format_admin_datetime(row.submitted_at),
                },
                details=[
                    {'label': '表单', 'value': row.form.name},
                    {'label': '语言', 'value': row_locale_label},
                    {'label': '姓名', 'value': row.full_name or '未填写'},
                    {'label': '公司', 'value': row.company or '未填写'},
                    {'label': '邮箱', 'value': row.email or '未填写'},
                    {'label': '电话', 'value': row.phone or '未填写'},
                    {'label': '国家', 'value': row.country or '未填写'},
                    {'label': '提交页面', 'value': row.source_url or '未记录'},
                    {'label': '留言', 'value': row.message or '未填写'},
                ],
                actions=[
                    link_action('编辑线索', locale_query_href(reverse('console:lead_submission_edit', args=[row.id]), row.locale.locale_code if row.locale else 'en')),
                ],
            )
        )

    form_rows = []
    for row in forms_qs:
        row_locale_code = row.locale.locale_code if row.locale else 'shared'
        row_locale_label = admin_locale_label(row.locale) if row.locale else '全站共享'
        form_rows.append(
            build_row(
                row_id=f'lead-form-{row.id}',
                tab_key='forms',
                filter_keys=[row.status, row.channel, row_locale_code],
                search=' '.join([row.code, row.name, row.category, row.scope_type, row.scope_value]),
                title=row.name,
                subtitle=f'{row.code} / {row.category}',
                cells={
                    'locale': row_locale_label,
                    'channel': row.channel,
                    'scope': f'{row.scope_type}:{row.scope_value}',
                    'status': badge(status_label(row.status), status_tone(row.status)),
                    'capi': badge('启用' if row.capi_enabled else '停用', 'green' if row.capi_enabled else 'amber'),
                    'updatedAt': format_admin_datetime(row.updated_at),
                },
                details=[
                    {'label': '代码', 'value': row.code},
                    {'label': '语言', 'value': row_locale_label},
                    {'label': '收集渠道', 'value': row.channel},
                    {'label': '作用域', 'value': f'{row.scope_type}:{row.scope_value}'},
                    {'label': '提交事件', 'value': row.submission_event_name},
                    {'label': '已联系事件', 'value': row.contacted_event_name},
                    {'label': '合格事件', 'value': row.qualified_event_name},
                    {'label': '成交事件', 'value': row.won_event_name},
                ],
                actions=[
                    link_action('编辑表单', locale_query_href(reverse('console:lead_form_edit', args=[row.id]), row.locale.locale_code if row.locale else 'en')),
                    link_action('查看提交接口', f'/api/leads/forms/{row.code}/submit/'),
                ],
            )
        )

    outbox_rows = []
    for row in outbox_qs:
        submission = row.submission
        provider_code = getattr(row.integration.provider, 'code', 'meta')
        provider_name = getattr(row.integration.provider, 'name', 'Meta')
        safe_error = redact_event_error(row.last_error) if row.last_error else ''
        outbox_rows.append(
            build_row(
                row_id=f'lead-outbox-{row.id}',
                tab_key='outbox',
                filter_keys=[row.status, provider_code],
                search=' '.join([row.event_name, row.integration.name, row.status, safe_error]),
                title=f'{row.event_name} / {submission.form.code}',
                subtitle=f'线索 #{submission.id} / {row.integration.name}',
                cells={
                    'provider': provider_name,
                    'stage': lead_stage_label(row.stage_key),
                    'status': badge(status_label(row.status), status_tone(row.status)),
                    'attempts': str(row.attempts),
                    'updatedAt': format_admin_datetime(row.updated_at),
                },
                details=[
                    {'label': '事件名', 'value': row.event_name},
                    {'label': '事件 ID', 'value': row.event_id},
                    {'label': '动作来源', 'value': row.action_source},
                    {'label': '最后错误', 'value': safe_error or '无'},
                ],
                actions=[
                    post_action('重试回传', reverse('console:lead_outbox_dispatch', args=[row.id])),
                    link_action('查看线索', locale_query_href(reverse('console:lead_submission_edit', args=[submission.id]), submission.locale.locale_code if submission.locale else 'en')),
                ],
            )
        )

    payload = make_page(
        section_key='leads',
        title='线索管理',
        description='这里管理网站表单、线索提交、成交阶段和 Meta CAPI 回传队列。',
        page_type='list',
        workspace_label='线索管理',
        workspace_meta='先收线索，再推进阶段，最后看回传结果。',
        search_placeholder='搜索姓名、邮箱、电话、公司、表单代码或事件状态',
    )
    payload['tabs'] = [
        {'key': 'submissions', 'label': '线索'},
        {'key': 'forms', 'label': '表单'},
        {'key': 'outbox', 'label': '回传'},
    ]
    payload['actions'] = [
        link_action('新建表单', locale_query_href(reverse('console:lead_form_create'), 'en'), tone='primary'),
        post_action('重试待处理回传', reverse('console:lead_outbox_dispatch_pending')),
    ]
    payload['scopeActions'] = []
    payload['views'] = {
        'submissions': {
            'searchPlaceholder': '搜索姓名、邮箱、电话、公司或留言',
            'workspaceMeta': '线索列表是运营主工作面。先筛、再看、再推进阶段。',
            'filterGroups': [
                {
                    'key': 'stage',
                    'label': '阶段',
                    'options': [
                        {'key': 'all', 'label': '全部'},
                        {'key': 'new', 'label': f'新提交 {stage_counts["new"] or 0}'},
                        {'key': 'contacted', 'label': f'已联系 {stage_counts["contacted"] or 0}'},
                        {'key': 'qualified', 'label': f'高质量 {stage_counts["qualified"] or 0}'},
                        {'key': 'won', 'label': f'已成交 {stage_counts["won"] or 0}'},
                        {'key': 'lost', 'label': f'已流失 {stage_counts["lost"] or 0}'},
                    ],
                },
                {
                    'key': 'locale',
                    'label': '语言',
                    'options': locale_filter_options(),
                },
            ],
            'summary': [
                {'label': '线索总数', 'value': stage_counts['total'] or 0, 'meta': '最近 200 条'},
                {'label': '已成交', 'value': stage_counts['won'] or 0, 'meta': '可回传 converted'},
            ],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '线索', 'type': 'title'},
                    {'key': 'locale', 'label': '语言', 'type': 'text'},
                    {'key': 'form', 'label': '表单', 'type': 'text'},
                    {'key': 'stage', 'label': '阶段', 'type': 'badge'},
                    {'key': 'contact', 'label': '联系方式', 'type': 'text'},
                    {'key': 'updatedAt', 'label': '提交时间', 'type': 'text'},
                ],
                'columnWidths': {
                    'title': 320,
                    'locale': 92,
                    'form': 148,
                    'stage': 92,
                    'contact': 220,
                    'updatedAt': 152,
                },
                'actionWidth': 116,
                'rowsPerPage': 20,
                'rows': submission_rows,
                'emptyMessage': '当前没有线索提交。',
            },
        },
        'forms': {
            'searchPlaceholder': '搜索表单名称、代码、分类或作用域',
            'workspaceMeta': '表单定义控制网站收集入口、公开接口和回传事件名。',
            'filterGroups': [
                {
                    'key': 'status',
                    'label': '状态',
                    'options': [
                        {'key': 'all', 'label': '全部'},
                        {'key': 'active', 'label': '启用'},
                        {'key': 'disabled', 'label': '停用'},
                        {'key': 'archived', 'label': '归档'},
                    ],
                },
                {
                    'key': 'channel',
                    'label': '渠道',
                    'options': [
                        {'key': 'all', 'label': '全部'},
                        {'key': 'website', 'label': '网站表单'},
                        {'key': 'whatsapp', 'label': 'WhatsApp'},
                        {'key': 'messenger', 'label': 'Messenger'},
                        {'key': 'phone', 'label': '电话'},
                        {'key': 'email', 'label': '邮箱'},
                    ],
                },
                {
                    'key': 'locale',
                    'label': '语言',
                    'options': locale_filter_options(),
                },
            ],
            'summary': [{'label': '表单数量', 'value': len(form_rows), 'meta': '最近 120 条'}],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '表单', 'type': 'title'},
                    {'key': 'locale', 'label': '语言', 'type': 'text'},
                    {'key': 'channel', 'label': '渠道', 'type': 'text'},
                    {'key': 'scope', 'label': '作用域', 'type': 'text'},
                    {'key': 'status', 'label': '状态', 'type': 'badge'},
                    {'key': 'capi', 'label': 'CAPI', 'type': 'badge'},
                    {'key': 'updatedAt', 'label': '更新时间', 'type': 'text'},
                ],
                'columnWidths': {
                    'title': 300,
                    'locale': 92,
                    'channel': 112,
                    'scope': 196,
                    'status': 92,
                    'capi': 92,
                    'updatedAt': 152,
                },
                'actionWidth': 176,
                'rowsPerPage': 20,
                'rows': form_rows,
                'emptyMessage': '当前没有线索表单。',
            },
        },
        'outbox': {
            'searchPlaceholder': '搜索事件名、状态、错误信息或接入名称',
            'workspaceMeta': '这里只看服务端回传队列。失败后可重试。',
            'filterGroups': [
                {
                    'key': 'status',
                    'label': '状态',
                    'options': [
                        {'key': 'all', 'label': '全部'},
                        {'key': 'pending', 'label': f'待发送 {outbox_counts["pending"] or 0}'},
                        {'key': 'failed', 'label': f'失败 {outbox_counts["failed"] or 0}'},
                        {'key': 'sent', 'label': f'已发送 {outbox_counts["sent"] or 0}'},
                    ],
                },
                {
                    'key': 'provider',
                    'label': '平台',
                    'options': [
                        {'key': 'all', 'label': '全部'},
                        {'key': 'meta', 'label': 'Meta'},
                    ],
                },
            ],
            'summary': [{'label': '队列总数', 'value': outbox_counts['total'] or 0, 'meta': '最近 200 条'}],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '事件', 'type': 'title'},
                    {'key': 'provider', 'label': '平台', 'type': 'text'},
                    {'key': 'stage', 'label': '阶段', 'type': 'text'},
                    {'key': 'status', 'label': '状态', 'type': 'badge'},
                    {'key': 'attempts', 'label': '次数', 'type': 'text'},
                    {'key': 'updatedAt', 'label': '更新时间', 'type': 'text'},
                ],
                'columnWidths': {
                    'title': 300,
                    'provider': 112,
                    'stage': 96,
                    'status': 92,
                    'attempts': 72,
                    'updatedAt': 152,
                },
                'actionWidth': 176,
                'rowsPerPage': 20,
                'rows': outbox_rows,
                'emptyMessage': '当前没有待发送或已发送的回传事件。',
            },
        },
    }
    payload['summary'] = [
        {'label': '待发送', 'value': outbox_counts['pending'] or 0, 'meta': '失败可重试'},
        {'label': '已成交', 'value': stage_counts['won'] or 0, 'meta': '可回传 converted'},
    ]
    payload['notes'] = [
        {
            'title': '商业闭环',
            'body': '线索管理不是 SEO 页面维护。它要把网站表单、成交阶段和 Meta CAPI 回传串成一个闭环。',
        }
    ]
    return payload
