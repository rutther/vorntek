from __future__ import annotations

from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.urls import reverse

from leads.crm_relationships import all_linked_targets_in_scope_q
from leads.models import Activity, CrmAttachment, PrivacyRequest, RetentionPolicy, Task

from .marketing_services import (
    privacy_subject_relationship_ids,
    privacy_subject_submissions,
)
from .models import AuditLog
from .payloads import format_admin_datetime
from .privacy_retention import retention_preview
from .privacy_versions import retention_policy_version_token


PRIVACY_TYPE_LABELS = {
    'export': '导出个人数据',
    'withdraw_consent': '撤回营销同意',
    'restrict': '限制继续处理',
    'delete': '删除或匿名化',
}

PRIVACY_STATUS_LABELS = {
    'pending': '待核验',
    'verified': '已核验',
    'processing': '处理中',
    'completed': '已完成',
    'rejected': '已拒绝',
}

PRIVACY_AUDIT_ACTIONS = frozenset({
    'privacy_request_created',
    'privacy_request_status_changed',
    'privacy_request_exported',
    'privacy_request_executed',
    'privacy_retention_policy_updated',
    'privacy_retention_policy_executed',
})

AUDIT_ACTION_LABELS = {
    'privacy_request_created': '登记隐私请求',
    'privacy_request_status_changed': '更新隐私请求状态',
    'privacy_request_exported': '下载数据导出包',
    'privacy_request_executed': '执行隐私动作',
    'privacy_retention_policy_updated': '更新保留策略',
    'privacy_retention_policy_executed': '执行保留策略',
}


def _queue_url(*, page: int, filters: dict[str, str]) -> str:
    query = {key: value for key, value in filters.items() if value}
    if page > 1:
        query['page'] = str(page)
    suffix = urlencode(query)
    endpoint = reverse('console:marketing_privacy')
    return f'{endpoint}?{suffix}' if suffix else endpoint


def _next_action(item: PrivacyRequest, *, system_admin: bool) -> str:
    if item.status == 'pending':
        return '核验身份'
    if item.status == 'verified':
        return '开始处理'
    if item.status == 'processing':
        if system_admin:
            return '下载并交付' if item.request_type == 'export' else '复核并执行'
        return '等待系统管理员' if item.request_type != 'export' else '查看处理进度'
    return '查看记录'


def _request_row(
    item: PrivacyRequest,
    *,
    system_admin: bool,
    return_context: str = '',
) -> dict:
    manage_url = reverse('console:privacy_request_manage', args=[item.id])
    if return_context == 'governance':
        manage_url = f'{manage_url}?return=governance'
    return {
        'id': item.id,
        'requester_name': item.requester_name or '身份信息已清理',
        'requester_email': item.requester_email,
        'requester_phone': item.requester_phone,
        'request_type': item.request_type,
        'request_type_label': PRIVACY_TYPE_LABELS.get(item.request_type, item.request_type),
        'status': item.status,
        'status_label': PRIVACY_STATUS_LABELS.get(item.status, item.status),
        'submission_id': item.submission_id,
        'handled_by': (
            item.handled_by_user.get_full_name()
            or item.handled_by_user.username
            if item.handled_by_user
            else '未分配'
        ),
        'requested_at': format_admin_datetime(item.requested_at),
        'updated_at': format_admin_datetime(item.updated_at),
        'next_action': _next_action(item, system_admin=system_admin),
        'manage_url': manage_url,
    }


def build_privacy_queue_workspace(*, site, request, system_admin: bool) -> dict:
    query = ' '.join((request.GET.get('q') or '').split())[:120]
    status = str(request.GET.get('status') or '').strip().lower()
    request_type = str(request.GET.get('type') or '').strip().lower()
    if status not in {'', *PRIVACY_STATUS_LABELS}:
        status = ''
    if request_type not in {'', *PRIVACY_TYPE_LABELS}:
        request_type = ''

    base = PrivacyRequest.objects.filter(site=site)
    stats = base.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status='pending')),
        verified=Count('id', filter=Q(status='verified')),
        processing=Count('id', filter=Q(status='processing')),
        closed=Count('id', filter=Q(status__in=['completed', 'rejected'])),
        system_action=Count(
            'id',
            filter=(
                Q(status='processing')
                | Q(status='verified', request_type='export')
            ),
        ),
    )
    queryset = base.select_related('handled_by_user', 'submission')
    if query:
        queryset = queryset.filter(
            Q(requester_name__icontains=query)
            | Q(requester_email__icontains=query)
            | Q(requester_phone__icontains=query)
            | Q(resolution__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    if request_type:
        queryset = queryset.filter(request_type=request_type)
    queryset = queryset.order_by('-requested_at', '-id')

    page_obj = Paginator(queryset, 25).get_page(request.GET.get('page') or 1)
    filters = {'q': query, 'status': status, 'type': request_type}
    return {
        'rows': [
            _request_row(item, system_admin=system_admin)
            for item in page_obj.object_list
        ],
        'stats': stats,
        'filters': filters,
        'type_options': PRIVACY_TYPE_LABELS.items(),
        'status_options': PRIVACY_STATUS_LABELS.items(),
        'result_count': page_obj.paginator.count,
        'page': {
            'number': page_obj.number,
            'total': page_obj.paginator.num_pages,
            'has_previous': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'previous_url': (
                _queue_url(page=page_obj.previous_page_number(), filters=filters)
                if page_obj.has_previous() else ''
            ),
            'next_url': (
                _queue_url(page=page_obj.next_page_number(), filters=filters)
                if page_obj.has_next() else ''
            ),
        },
    }


def privacy_request_impact(item: PrivacyRequest) -> dict[str, int]:
    submission_ids = list(
        privacy_subject_submissions(item).values_list('id', flat=True)
    )
    relationships = privacy_subject_relationship_ids(
        site=item.site,
        submission_ids=submission_ids,
    )
    subject_scope = all_linked_targets_in_scope_q(**relationships)
    return {
        'submissions': len(submission_ids),
        'companies': len(relationships['company_ids']),
        'contacts': len(relationships['contact_ids']),
        'opportunities': len(relationships['opportunity_ids']),
        'tasks': Task.objects.filter(site=item.site).filter(subject_scope).count(),
        'activities': Activity.objects.filter(site=item.site).filter(subject_scope).count(),
        'attachments': CrmAttachment.objects.filter(
            site=item.site,
            status='active',
        ).filter(subject_scope).exclude(storage_path='').count(),
    }


def build_data_governance_workspace(*, site) -> dict:
    policy, _created = RetentionPolicy.objects.get_or_create(site=site)
    preview = retention_preview(policy)
    high_risk_queryset = (
        PrivacyRequest.objects.filter(site=site)
        .filter(Q(status='processing') | Q(status='verified', request_type='export'))
        .select_related('handled_by_user', 'submission')
        .order_by('requested_at', 'id')
    )
    high_risk_rows = [
        _request_row(item, system_admin=True, return_context='governance')
        for item in high_risk_queryset[:25]
    ]
    audit_rows = []
    privacy_request_ids = PrivacyRequest.objects.filter(site=site).values_list('id', flat=True)
    audits = (
        AuditLog.objects.filter(action__in=PRIVACY_AUDIT_ACTIONS)
        .filter(
            Q(entity_table='privacy_request', entity_id__in=privacy_request_ids)
            | Q(entity_table='privacy_retention_policy', entity_id=policy.id)
        )
        .select_related('actor_user')
        .order_by('-created_at', '-id')[:20]
    )
    for entry in audits:
        audit_rows.append({
            'id': entry.id,
            'action': entry.action,
            'action_label': AUDIT_ACTION_LABELS.get(entry.action, entry.action),
            'entity_table': entry.entity_table,
            'entity_id': entry.entity_id,
            'actor': (
                entry.actor_user.get_full_name()
                or entry.actor_user.username
                if entry.actor_user
                else entry.actor
            ),
            'created_at': format_admin_datetime(entry.created_at),
            'metadata': entry.metadata_json or {},
        })
    candidate_counts = preview['candidates']
    destructive_candidate_total = sum(
        candidate_counts[key]
        for key in (
            'lead_pii',
            'private_attachments',
            'inbound_payloads',
            'outbox_payloads',
            'privacy_request_identity',
        )
    )
    return {
        'policy': policy,
        'policy_version': retention_policy_version_token(policy),
        'preview': preview,
        'candidate_total': destructive_candidate_total,
        'high_risk_rows': high_risk_rows,
        'high_risk_count': high_risk_queryset.count(),
        'audit_rows': audit_rows,
    }
