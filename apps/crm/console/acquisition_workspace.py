from __future__ import annotations

from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.urls import reverse

from leads.models import LeadFormDefinition
from leads.notifications import email_delivery_configured, form_notification_recipients

from .lead_forms import default_form_schema
from .payloads import admin_locale_label, format_admin_datetime


CHANNEL_LABELS = {
    'website': '网站表单',
    'whatsapp': 'WhatsApp',
    'messenger': 'Messenger',
    'phone': '电话',
    'email': '邮箱',
    'custom': '其他',
}

SCOPE_LABELS = {
    'site': '全站',
    'route': '指定路由',
    'cta': '指定 CTA',
    'page_type': '页面类型',
    'custom': '自定义',
}

STATUS_LABELS = {
    'active': '已启用',
    'disabled': '已停用',
    'archived': '已归档',
}


def _query_url(*, page: int, filters: dict[str, str]) -> str:
    query = {key: value for key, value in filters.items() if value}
    if page > 1:
        query['page'] = str(page)
    suffix = urlencode(query)
    endpoint = reverse('console:marketing_forms')
    return f'{endpoint}?{suffix}' if suffix else endpoint


def _field_count(form_definition: LeadFormDefinition) -> int:
    schema = form_definition.form_schema or default_form_schema()
    fields = schema.get('fields') or []
    return sum(
        1
        for field in fields
        if isinstance(field, dict) and field.get('enabled', True)
    )


def build_acquisition_forms_workspace(*, site, request) -> dict:
    query = ' '.join((request.GET.get('q') or '').split())[:120]
    status = (request.GET.get('status') or '').strip().lower()
    channel = (request.GET.get('channel') or '').strip().lower()
    locale_code = (request.GET.get('language') or '').strip().lower()
    if status not in {'', 'active', 'disabled', 'archived'}:
        status = ''
    if channel not in {'', *CHANNEL_LABELS}:
        channel = ''

    base = LeadFormDefinition.objects.filter(site=site)
    stats = base.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(status='active')),
        without_notification=Count(
            'id',
            filter=Q(status='active') & (Q(notify_emails='') | Q(notify_emails__isnull=True)),
        ),
        capi=Count('id', filter=Q(status='active', capi_enabled=True)),
    )

    queryset = (
        base.select_related('locale')
        .annotate(submission_count=Count('leadsubmission', distinct=True))
        .order_by('-updated_at', 'name', 'id')
    )
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(category__icontains=query)
            | Q(scope_value__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    if channel:
        queryset = queryset.filter(channel=channel)
    if locale_code == 'shared':
        queryset = queryset.filter(locale__isnull=True)
    elif locale_code:
        queryset = queryset.filter(locale__locale_code=locale_code)

    page_obj = Paginator(queryset, 25).get_page(request.GET.get('page') or 1)
    rows = []
    for form_definition in page_obj.object_list:
        recipients = form_notification_recipients(form_definition)
        rows.append({
            'id': form_definition.id,
            'name': form_definition.name,
            'code': form_definition.code,
            'category': form_definition.category,
            'locale': (
                admin_locale_label(form_definition.locale)
                if form_definition.locale
                else '全站共享'
            ),
            'locale_code': (
                form_definition.locale.locale_code
                if form_definition.locale
                else ''
            ),
            'channel': CHANNEL_LABELS.get(form_definition.channel, form_definition.channel),
            'scope': SCOPE_LABELS.get(form_definition.scope_type, form_definition.scope_type),
            'scope_value': form_definition.scope_value,
            'status': form_definition.status,
            'status_label': STATUS_LABELS.get(form_definition.status, form_definition.status),
            'capi_enabled': bool(form_definition.capi_enabled),
            'recipient_count': len(recipients),
            'recipient_summary': '、'.join(recipients[:2]),
            'field_count': _field_count(form_definition),
            'submission_count': form_definition.submission_count,
            'updated_at': format_admin_datetime(form_definition.updated_at),
            'edit_url': (
                f'{reverse("console:marketing_form_edit", args=[form_definition.id])}'
                f'?locale={form_definition.locale.locale_code}'
                if form_definition.locale
                else reverse('console:marketing_form_edit', args=[form_definition.id])
            ),
            'endpoint': f'/api/leads/forms/{form_definition.code}/submit/',
        })

    active_filters = {
        'q': query,
        'status': status,
        'channel': channel,
        'language': locale_code,
    }
    locales = list(
        site.locales.filter(enabled=True)
        .order_by('sort_order', 'locale_code')
        .values('locale_code', 'label')
    )
    return {
        'rows': rows,
        'stats': {
            **stats,
            'email_ready': email_delivery_configured(),
        },
        'filters': active_filters,
        'channel_options': CHANNEL_LABELS.items(),
        'locale_options': locales,
        'result_count': page_obj.paginator.count,
        'page': {
            'number': page_obj.number,
            'total': page_obj.paginator.num_pages,
            'has_previous': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'previous_url': (
                _query_url(page=page_obj.previous_page_number(), filters=active_filters)
                if page_obj.has_previous()
                else ''
            ),
            'next_url': (
                _query_url(page=page_obj.next_page_number(), filters=active_filters)
                if page_obj.has_next()
                else ''
            ),
        },
    }
