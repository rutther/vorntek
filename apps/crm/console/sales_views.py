from __future__ import annotations

from dataclasses import asdict

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from leads.crm_services import (
    append_activity,
    change_opportunity_stage,
    complete_task,
    convert_qualified_lead,
    create_task,
    synchronize_submission_next_follow_up,
)
from leads.crm_relationships import (
    all_linked_targets_in_scope_q,
    linked_targets_match_record_site_q,
)
from leads.models import (
    Activity,
    Company,
    Contact,
    CrmAttachment,
    LeadSubmission,
    Opportunity,
    OpportunityStageHistory,
    SalesTeamMember,
    SavedView,
    Task,
)

from .access import (
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    activity_queryset_for_user,
    assignee_queryset_for_user,
    crm_owned_queryset_for_user,
    lead_queryset_for_user,
    saved_view_queryset_for_user,
    task_queryset_for_user,
    user_role_keys,
)
from .audit import record_audit
from .capabilities import SalesCapability, require_sales
from .crm_files import resolve_private_attachment_path, store_crm_attachment
from .forms import resolve_storage_path as resolve_public_asset_path
from .payloads import default_site_locale
from .lead_workspace_v2 import (
    lead_saved_view_payload,
    normalize_lead_saved_view_state,
)
from .opportunity_versions import opportunity_version_matches, opportunity_version_token
from .account_versions import account_version_matches, account_version_token
from .sales_forms import (
    ActivityEditorForm,
    CompanyEditorForm,
    ContactEditorForm,
    CrmAttachmentUploadForm,
    LeadConversionForm,
    OpportunityEditorForm,
    OpportunityStageForm,
    SavedViewForm,
    TaskEditorForm,
)
from .sales_queries import build_pipeline_context, build_workbench_context


def _errors(form) -> dict:
    return {field: [str(item) for item in errors] for field, errors in form.errors.items()}


def _validation_response(exc: ValidationError, *, status: int = 400) -> JsonResponse:
    if hasattr(exc, 'message_dict'):
        errors = exc.message_dict
    else:
        errors = {'__all__': exc.messages}
    return JsonResponse({'ok': False, 'errors': errors}, status=status)


def _opportunity_conflict_response(opportunity: Opportunity) -> JsonResponse:
    return JsonResponse(
        {
            'ok': False,
            'code': 'opportunity_version_conflict',
            'errors': {
                '__all__': [
                    '这条销售机会已被其他操作更新。请刷新并核对最新内容后重试。'
                ],
            },
            'current_version': opportunity_version_token(opportunity.updated_at),
        },
        status=409,
    )


def _account_conflict_response(record, *, kind: str) -> JsonResponse:
    label = '企业' if kind == 'company' else '联系人'
    return JsonResponse(
        {
            'ok': False,
            'code': f'{kind}_version_conflict',
            'errors': {
                '__all__': [
                    f'这条{label}资料已被其他操作更新。请刷新并核对最新内容后重试。'
                ],
            },
            'current_version': account_version_token(record.updated_at),
        },
        status=409,
    )


def _requested_locale(request) -> str | None:
    return (request.GET.get('locale') or request.POST.get('locale') or '').strip().lower() or None


def _work_item_payload(item) -> dict:
    payload = asdict(item)
    payload.pop('score', None)
    return payload


def _opportunity_payload(item: Opportunity) -> dict:
    owner_label = '未分配'
    if item.owner_user:
        owner_label = item.owner_user.get_full_name().strip() or item.owner_user.get_username()
    return {
        'id': item.id,
        'name': item.name,
        'stage': item.stage,
        'company': item.company.name if item.company else '',
        'country': item.company.country if item.company else '',
        'contact': item.primary_contact.full_name if item.primary_contact else '',
        'owner': owner_label,
        'product_scope': item.product_scope,
        'capacity_target': item.capacity_target,
        'value_amount': item.value_amount,
        'currency': item.currency,
        'probability': item.probability,
        'next_step': item.next_step,
        'next_follow_up_at': item.next_follow_up_at,
        'updated_at': item.updated_at,
        'version': opportunity_version_token(item.updated_at),
    }


def _user_label(user) -> str:
    if user is None:
        return '未分配'
    return user.get_full_name().strip() or user.get_username()


def _attachment_queryset_for_user(*, site, user):
    leads = lead_queryset_for_user(
        LeadSubmission.objects.filter(site=site),
        user,
    ).values('id')
    companies = crm_owned_queryset_for_user(
        Company.objects.filter(site=site),
        user,
    ).values('id')
    contacts = crm_owned_queryset_for_user(
        Contact.objects.filter(site=site),
        user,
    ).values('id')
    opportunities = crm_owned_queryset_for_user(
        Opportunity.objects.filter(site=site),
        user,
    ).values('id')
    return CrmAttachment.objects.filter(site=site).filter(
        linked_targets_match_record_site_q(),
        all_linked_targets_in_scope_q(
            submission_ids=leads,
            company_ids=companies,
            contact_ids=contacts,
            opportunity_ids=opportunities,
        ),
    )


def _attachment_payload(item: CrmAttachment) -> dict:
    original_name = item.original_name
    mime_type = item.mime_type
    file_size_bytes = item.file_size_bytes
    if item.asset_id and item.asset:
        original_name = original_name or item.asset.original_name
        mime_type = mime_type or item.asset.mime_type
        file_size_bytes = file_size_bytes or item.asset.file_size_bytes
    return {
        'id': item.id,
        'title': item.title or original_name,
        'original_name': original_name,
        'mime_type': mime_type,
        'file_size_bytes': file_size_bytes,
        'status': item.status,
        'uploaded_by': _user_label(item.uploaded_by_user),
        'created_at': item.created_at,
        'download_url': reverse('console:crm_attachment_download', args=[item.id]),
    }


def _target_key_and_record(target: dict) -> tuple[str, object]:
    for key in ('submission', 'company', 'contact', 'opportunity'):
        if target.get(key) is not None:
            return key, target[key]
    raise Http404('Sales target is missing.')


def _target_query(key: str, record_id: int) -> Q:
    return Q(**{f'{key}_id': record_id})


def _business_record_payload(key: str, item, *, user=None) -> dict:
    if key == 'submission':
        conversion = getattr(item, 'conversion', None)
        return {
            'type': 'lead',
            'id': item.id,
            'full_name': item.full_name,
            'company': item.company,
            'country': item.country,
            'email': item.email,
            'phone': item.phone,
            'stage': item.stage,
            'owner': _user_label(item.assignee),
            'source_channel': item.source_channel,
            'source_detail': item.source_detail,
            'message': item.message,
            'next_follow_up_at': item.next_follow_up_at,
            'submitted_at': item.submitted_at,
            'conversion': {
                'company_id': conversion.company_id,
                'contact_id': conversion.contact_id,
                'opportunity_id': conversion.opportunity_id,
            } if conversion else None,
        }
    if key == 'company':
        contacts = item.contacts.all()
        opportunities = item.opportunities.all()
        if user is not None:
            contacts = crm_owned_queryset_for_user(contacts, user)
            opportunities = crm_owned_queryset_for_user(opportunities, user)
        return {
            'type': 'company',
            'id': item.id,
            'name': item.name,
            'website': item.website,
            'industry': item.industry,
            'country': item.country,
            'city': item.city,
            'status': item.status,
            'owner': _user_label(item.owner_user),
            'notes': item.notes,
            'version': account_version_token(item.updated_at),
            'contact_count': contacts.count(),
            'opportunity_count': opportunities.count(),
        }
    if key == 'contact':
        return {
            'type': 'contact',
            'id': item.id,
            'full_name': item.full_name,
            'job_title': item.job_title,
            'email': item.email,
            'phone': item.phone,
            'whatsapp_phone': item.whatsapp_phone,
            'country': item.country,
            'preferred_language': item.preferred_language,
            'status': item.status,
            'owner': _user_label(item.owner_user),
            'company': {'id': item.company_id, 'name': item.company.name} if item.company else None,
            'notes': item.notes,
            'version': account_version_token(item.updated_at),
        }
    return {
        **_opportunity_payload(item),
        'type': 'opportunity',
        'source_submission_id': item.source_submission_id,
        'expected_close_date': item.expected_close_date,
        'won_reason': item.won_reason,
        'lost_reason': item.lost_reason,
    }


def _activity_payload(item: Activity) -> dict:
    return {
        'id': item.id,
        'activity_type': item.activity_type,
        'direction': item.direction,
        'subject': item.subject,
        'body': item.body,
        'actor': _user_label(item.actor_user),
        'occurred_at': item.occurred_at,
        'created_at': item.created_at,
    }


def _task_payload(item: Task) -> dict:
    return {
        'id': item.id,
        'title': item.title,
        'description': item.description,
        'task_type': item.task_type,
        'priority': item.priority,
        'status': item.status,
        'owner': _user_label(item.owner_user),
        'due_at': item.due_at,
        'reminder_at': item.reminder_at,
        'completed_at': item.completed_at,
        'outcome': item.outcome,
        'detail_url': reverse('console:task_workspace_detail_v2', args=(item.id,)),
    }


@login_required
@require_GET
def workbench_data(request):
    site, _locale = default_site_locale(_requested_locale(request))
    context = build_workbench_context(site=site, user=request.user)
    return JsonResponse(
        {
            'ok': True,
            'counts': context['counts'],
            'action_queue': [_work_item_payload(item) for item in context['action_queue']],
            'system_exceptions': context['system_exceptions'],
            'show_system_exceptions': context['show_system_exceptions'],
        }
    )


@login_required
@require_GET
def pipeline_data(request):
    site, _locale = default_site_locale(_requested_locale(request))
    filters = {
        'stage': request.GET.get('stage', ''),
        'owner': request.GET.get('owner', ''),
        'source_channel': request.GET.get('source_channel', ''),
        'q': request.GET.get('q', ''),
    }
    context = build_pipeline_context(
        site=site,
        user=request.user,
        filters=filters,
        include_closed=request.GET.get('include_closed') in {'1', 'true'},
    )
    return JsonResponse(
        {
            'ok': True,
            'count': context['count'],
            'stages': [
                {
                    'key': column['key'],
                    'label': column['label'],
                    'count': column['count'],
                    'value_amount': column['value_amount'],
                    'value_currency': column['value_currency'],
                    'value_by_currency': column['value_by_currency'],
                    'items': [_opportunity_payload(item) for item in column['items']],
                }
                for column in context['columns']
            ],
            'rows': [_opportunity_payload(item) for item in context['rows']],
        }
    )


@login_required
@require_GET
def crm_collection_data(request, collection: str):
    site, _locale = default_site_locale(_requested_locale(request))
    collection = str(collection or '').strip().lower()
    query = (request.GET.get('q') or '').strip()
    status = (request.GET.get('status') or '').strip()
    try:
        page = max(int(request.GET.get('page') or 1), 1)
        page_size = min(max(int(request.GET.get('page_size') or 50), 1), 100)
    except ValueError:
        return JsonResponse({'ok': False, 'errors': {'page': ['分页参数无效。']}}, status=400)

    if collection == 'companies':
        queryset = crm_owned_queryset_for_user(
            Company.objects.filter(site=site).select_related('owner_user', 'team'),
            request.user,
        )
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(country__icontains=query)
                | Q(industry__icontains=query)
            )
        if status:
            queryset = queryset.filter(status=status)
        queryset = queryset.order_by('name', 'id')
        serializer = lambda item: _business_record_payload('company', item, user=request.user)
    elif collection == 'contacts':
        queryset = crm_owned_queryset_for_user(
            Contact.objects.filter(site=site).select_related('company', 'owner_user', 'team'),
            request.user,
        )
        if query:
            queryset = queryset.filter(
                Q(full_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
                | Q(company__name__icontains=query)
            )
        if status:
            queryset = queryset.filter(status=status)
        queryset = queryset.order_by('full_name', 'id')
        serializer = lambda item: _business_record_payload('contact', item, user=request.user)
    elif collection == 'opportunities':
        queryset = crm_owned_queryset_for_user(
            Opportunity.objects.filter(site=site).select_related(
                'company', 'primary_contact', 'owner_user', 'team'
            ),
            request.user,
        )
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(company__name__icontains=query)
                | Q(product_scope__icontains=query)
            )
        if status:
            queryset = queryset.filter(stage=status)
        queryset = queryset.order_by('stage', 'next_follow_up_at', '-updated_at')
        serializer = lambda item: _business_record_payload('opportunity', item, user=request.user)
    elif collection == 'tasks':
        queryset = task_queryset_for_user(
            Task.objects.filter(site=site).select_related('owner_user', 'team'),
            request.user,
        )
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) | Q(description__icontains=query)
            )
        if status:
            queryset = queryset.filter(status=status)
        queryset = queryset.order_by('due_at', '-priority', 'id')
        serializer = _task_payload
    else:
        raise Http404('Unknown CRM collection.')

    total = queryset.count()
    start = (page - 1) * page_size
    items = list(queryset[start:start + page_size])
    pages = max((total + page_size - 1) // page_size, 1)
    return JsonResponse(
        {
            'ok': True,
            'collection': collection,
            'count': total,
            'page': page,
            'page_size': page_size,
            'pages': pages,
            'items': [serializer(item) for item in items],
        }
    )


@login_required
@require_GET
def crm_record_detail(request, target_type: str, target_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    normalized_type = str(target_type or '').strip().lower()
    target = _scoped_target(
        site=site,
        user=request.user,
        target_type=normalized_type,
        target_id=target_id,
    )
    key, record = _target_key_and_record(target)
    target_query = _target_query(key, record.id)
    activities = activity_queryset_for_user(
        Activity.objects.filter(site=site).filter(target_query).select_related('actor_user'),
        request.user,
    ).order_by('-occurred_at', '-id')[:100]
    tasks = task_queryset_for_user(
        Task.objects.filter(site=site).filter(target_query).select_related('owner_user'),
        request.user,
    ).order_by('status', 'due_at', 'id')[:100]
    attachments = _attachment_queryset_for_user(site=site, user=request.user).filter(
        target_query,
        status='active',
    ).select_related('asset', 'uploaded_by_user').order_by('-created_at', '-id')[:100]
    stage_history = []
    if key == 'opportunity':
        stage_history = list(
            OpportunityStageHistory.objects.filter(opportunity=record)
            .select_related('changed_by_user')
            .order_by('-changed_at', '-id')
            .values(
                'id', 'from_stage', 'to_stage', 'reason', 'changed_at',
                'changed_by_user__username',
            )[:100]
        )
    return JsonResponse(
        {
            'ok': True,
            'record': _business_record_payload(key, record, user=request.user),
            'activities': [_activity_payload(item) for item in activities],
            'tasks': [_task_payload(item) for item in tasks],
            'attachments': [_attachment_payload(item) for item in attachments],
            'stage_history': stage_history,
        }
    )


@login_required
@require_POST
def company_update(request, company_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    with transaction.atomic():
        company = get_object_or_404(
            crm_owned_queryset_for_user(
                Company.objects.select_for_update().filter(site=site), request.user
            ),
            id=company_id,
        )
        require_sales(request.user, SalesCapability.WRITE, record=company)
        submitted_version = (request.POST.get('version') or '').strip()
        if not account_version_matches(company.updated_at, submitted_version):
            return _account_conflict_response(company, kind='company')
        form = CompanyEditorForm(
            request.POST, company=company, site=site, user=request.user
        )
        if not form.is_valid():
            return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
        before = _business_record_payload('company', company, user=request.user)
        company = form.save()
        after = _business_record_payload('company', company, user=request.user)
        append_activity(
            site=site,
            actor=request.user,
            activity_type='system',
            subject='更新企业资料',
            company=company,
        )
        record_audit(
            actor=request.user,
            action='crm_company_updated',
            entity_table='crm_company',
            entity_id=company.id,
            before=before,
            after=after,
            request=request,
        )
    return JsonResponse({
        'ok': True,
        'record': after,
        'version': account_version_token(company.updated_at),
    })


@login_required
@require_POST
def contact_update(request, contact_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    with transaction.atomic():
        # Keep the lock query on crm_contact itself. PostgreSQL rejects FOR UPDATE
        # when select_related() introduces the nullable company side as an outer join.
        contact = get_object_or_404(
            crm_owned_queryset_for_user(
                Contact.objects.select_for_update().filter(site=site), request.user
            ),
            id=contact_id,
        )
        require_sales(request.user, SalesCapability.WRITE, record=contact)
        submitted_version = (request.POST.get('version') or '').strip()
        if not account_version_matches(contact.updated_at, submitted_version):
            return _account_conflict_response(contact, kind='contact')
        form = ContactEditorForm(
            request.POST, contact=contact, site=site, user=request.user
        )
        if not form.is_valid():
            return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
        before = _business_record_payload('contact', contact, user=request.user)
        contact = form.save()
        after = _business_record_payload('contact', contact, user=request.user)
        append_activity(
            site=site,
            actor=request.user,
            activity_type='system',
            subject='更新联系人资料',
            company=contact.company,
            contact=contact,
        )
        record_audit(
            actor=request.user,
            action='crm_contact_updated',
            entity_table='crm_contact',
            entity_id=contact.id,
            before=before,
            after=after,
            request=request,
        )
    return JsonResponse({
        'ok': True,
        'record': after,
        'version': account_version_token(contact.updated_at),
    })


@login_required
@require_POST
def opportunity_update(request, opportunity_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    with transaction.atomic():
        opportunity = get_object_or_404(
            crm_owned_queryset_for_user(
                Opportunity.objects.select_for_update().filter(site=site),
                request.user,
            ),
            id=opportunity_id,
        )
        require_sales(request.user, SalesCapability.WRITE, record=opportunity)
        submitted_version = (request.POST.get('version') or '').strip()
        if not opportunity_version_matches(
            opportunity.updated_at,
            submitted_version,
        ):
            return _opportunity_conflict_response(opportunity)
        form = OpportunityEditorForm(
            request.POST,
            site=site,
            user=request.user,
            opportunity=opportunity,
        )
        if not form.is_valid():
            return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
        before = _business_record_payload('opportunity', opportunity, user=request.user)
        opportunity = form.save()
        after = _business_record_payload('opportunity', opportunity, user=request.user)
        append_activity(
            site=site,
            actor=request.user,
            activity_type='system',
            subject='更新项目资料与下一步',
            submission=opportunity.source_submission,
            company=opportunity.company,
            contact=opportunity.primary_contact,
            opportunity=opportunity,
        )
        record_audit(
            actor=request.user,
            action='crm_opportunity_updated',
            entity_table='crm_opportunity',
            entity_id=opportunity.id,
            before=before,
            after=after,
            request=request,
        )
    return JsonResponse({
        'ok': True,
        'record': after,
        'version': opportunity_version_token(opportunity.updated_at),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def crm_attachments(request):
    site, _locale = default_site_locale(_requested_locale(request))
    target_type = (request.GET.get('target_type') or request.POST.get('target_type') or '').strip()
    target_id = request.GET.get('target_id') or request.POST.get('target_id')
    if not str(target_id).isdigit():
        return JsonResponse({'ok': False, 'errors': {'target_id': ['关联对象无效。']}}, status=400)
    target = _scoped_target(
        site=site,
        user=request.user,
        target_type=target_type,
        target_id=int(target_id),
    )
    key, record = _target_key_and_record(target)
    if request.method == 'GET':
        attachments = _attachment_queryset_for_user(site=site, user=request.user).filter(
            _target_query(key, record.id),
            status='active',
        ).select_related('asset', 'uploaded_by_user').order_by('-created_at', '-id')
        return JsonResponse(
            {'ok': True, 'attachments': [_attachment_payload(item) for item in attachments]}
        )

    form = CrmAttachmentUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    target_values = {key: record}
    try:
        attachment = store_crm_attachment(
            site=site,
            uploaded_file=form.cleaned_data['file'],
            title=form.cleaned_data['title'],
            uploaded_by=request.user,
            target=target_values,
        )
    except ValidationError as exc:
        return _validation_response(exc)
    append_activity(
        site=site,
        actor=request.user,
        activity_type='file',
        subject=f'上传附件：{attachment.title or attachment.original_name}',
        metadata={'attachment_id': attachment.id},
        **target_values,
    )
    record_audit(
        actor=request.user,
        action='crm_attachment_uploaded',
        entity_table='crm_attachment',
        entity_id=attachment.id,
        after={
            'target_type': target_type,
            'target_id': record.id,
            'original_name': attachment.original_name,
            'file_size_bytes': attachment.file_size_bytes,
            'sha256': attachment.sha256,
        },
        request=request,
    )
    return JsonResponse({'ok': True, 'attachment': _attachment_payload(attachment)}, status=201)


@login_required
@require_GET
def crm_attachment_download(request, attachment_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    attachment = get_object_or_404(
        _attachment_queryset_for_user(site=site, user=request.user).select_related('asset'),
        id=attachment_id,
        status='active',
    )
    if attachment.storage_path:
        try:
            path = resolve_private_attachment_path(attachment.storage_path)
        except ValidationError as exc:
            raise Http404('Attachment path is invalid.') from exc
        filename = attachment.original_name or path.name
        content_type = attachment.mime_type or 'application/octet-stream'
    elif attachment.asset_id and attachment.asset:
        path = resolve_public_asset_path(attachment.asset.storage_path)
        filename = attachment.asset.original_name or path.name
        content_type = attachment.asset.mime_type or 'application/octet-stream'
    else:
        raise Http404('Attachment file is missing.')
    if not path.exists() or not path.is_file():
        raise Http404('Attachment file is missing.')
    response = FileResponse(
        path.open('rb'),
        as_attachment=True,
        filename=filename,
        content_type=content_type,
    )
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def crm_attachment_archive(request, attachment_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    attachment = get_object_or_404(
        _attachment_queryset_for_user(site=site, user=request.user).select_related(
            'submission', 'company', 'contact', 'opportunity'
        ),
        id=attachment_id,
        status='active',
    )
    attachment.status = 'archived'
    attachment.save(update_fields=['status'])
    append_activity(
        site=site,
        actor=request.user,
        activity_type='file',
        subject=f'归档附件：{attachment.title or attachment.original_name}',
        metadata={'attachment_id': attachment.id},
        submission=attachment.submission,
        company=attachment.company,
        contact=attachment.contact,
        opportunity=attachment.opportunity,
    )
    record_audit(
        actor=request.user,
        action='crm_attachment_archived',
        entity_table='crm_attachment',
        entity_id=attachment.id,
        before={'status': 'active'},
        after={'status': 'archived'},
        request=request,
    )
    return JsonResponse({'ok': True, 'attachment_id': attachment.id, 'status': 'archived'})


@login_required
@require_POST
@transaction.atomic
def lead_convert(request, submission_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    submission = get_object_or_404(
        lead_queryset_for_user(LeadSubmission.objects.filter(site=site), request.user),
        id=submission_id,
    )
    form = LeadConversionForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)

    def authorize_locked_submission(locked_submission: LeadSubmission) -> None:
        # The list-scope lookup above is only an early non-disclosure check.
        # Re-evaluate scope after the service has locked the row so an owner or
        # team change between those two points cannot authorize a conversion.
        if not lead_queryset_for_user(
            LeadSubmission.objects.filter(
                site=site,
                id=locked_submission.id,
            ),
            request.user,
        ).exists():
            raise Http404('Lead not found.')

    try:
        result = convert_qualified_lead(
            submission_id=submission.id,
            actor=request.user,
            opportunity_name=form.cleaned_data['opportunity_name'],
            locked_submission_authorizer=authorize_locked_submission,
        )
    except ValidationError as exc:
        return _validation_response(exc)
    record_audit(
        actor=request.user,
        action='lead_converted',
        entity_table='lead_submission',
        entity_id=submission.id,
        after={
            'company_id': result.company.id,
            'contact_id': result.contact.id,
            'opportunity_id': result.opportunity.id,
            'created': result.created,
        },
        request=request,
    )
    return JsonResponse(
        {
            'ok': True,
            'created': result.created,
            'company_id': result.company.id,
            'contact_id': result.contact.id,
            'opportunity_id': result.opportunity.id,
        }
    )


def _scoped_target(*, site, user, target_type: str, target_id: int) -> dict:
    if target_type == 'lead':
        target = get_object_or_404(
            lead_queryset_for_user(LeadSubmission.objects.filter(site=site), user),
            id=target_id,
        )
        return {'submission': target, 'team': target.team}
    model_map = {
        'company': (Company, 'company'),
        'contact': (Contact, 'contact'),
        'opportunity': (Opportunity, 'opportunity'),
    }
    if target_type not in model_map:
        raise Http404('Unknown sales target.')
    model, key = model_map[target_type]
    target = get_object_or_404(
        crm_owned_queryset_for_user(model.objects.filter(site=site), user),
        id=target_id,
    )
    return {key: target, 'team': target.team}


@login_required
@require_POST
def activity_create(request):
    site, _locale = default_site_locale(_requested_locale(request))
    target_type = (request.POST.get('target_type') or '').strip()
    target_id = request.POST.get('target_id')
    if not str(target_id).isdigit():
        return JsonResponse({'ok': False, 'errors': {'target_id': ['关联对象无效。']}}, status=400)
    target = _scoped_target(site=site, user=request.user, target_type=target_type, target_id=int(target_id))
    form = ActivityEditorForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    activity = append_activity(
        site=site,
        actor=request.user,
        activity_type=form.cleaned_data['activity_type'],
        direction=form.cleaned_data['direction'],
        subject=form.cleaned_data['subject'],
        body=form.cleaned_data['body'],
        occurred_at=form.cleaned_data['occurred_at'],
        submission=target.get('submission'),
        company=target.get('company'),
        contact=target.get('contact'),
        opportunity=target.get('opportunity'),
    )
    record_audit(
        actor=request.user,
        action='crm_activity_created',
        entity_table='crm_activity',
        entity_id=activity.id,
        after={'target_type': target_type, 'target_id': int(target_id), 'activity_type': activity.activity_type},
        request=request,
    )
    return JsonResponse({'ok': True, 'activity_id': activity.id})


@login_required
@require_POST
@transaction.atomic
def task_create(request):
    site, _locale = default_site_locale(_requested_locale(request))
    target_type = (request.POST.get('target_type') or '').strip()
    target_id = request.POST.get('target_id')
    if not str(target_id).isdigit():
        return JsonResponse({'ok': False, 'errors': {'target_id': ['关联对象无效。']}}, status=400)
    target = _scoped_target(site=site, user=request.user, target_type=target_type, target_id=int(target_id))
    form = TaskEditorForm(
        request.POST,
        site=site,
        user=request.user,
        target_team=target.get('team'),
    )
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    task = create_task(
        site=site,
        submission=target.get('submission'),
        company=target.get('company'),
        contact=target.get('contact'),
        opportunity=target.get('opportunity'),
        owner_user=form.cleaned_data['owner_user'],
        team=target.get('team'),
        created_by_user=request.user,
        title=form.cleaned_data['title'],
        description=form.cleaned_data['description'],
        task_type=form.cleaned_data['task_type'],
        priority=form.cleaned_data['priority'],
        due_at=form.cleaned_data['due_at'],
        reminder_at=form.cleaned_data['reminder_at'],
    )
    synchronize_submission_next_follow_up(submission_id=task.submission_id)
    append_activity(
        site=site,
        actor=request.user,
        activity_type='task',
        subject=f'创建任务：{task.title}',
        submission=task.submission,
        company=task.company,
        contact=task.contact,
        opportunity=task.opportunity,
        metadata={'task_id': task.id, 'due_at': task.due_at.isoformat()},
    )
    record_audit(
        actor=request.user,
        action='crm_task_created',
        entity_table='crm_task',
        entity_id=task.id,
        after={'owner_user_id': task.owner_user_id, 'due_at': task.due_at.isoformat()},
        request=request,
    )
    return JsonResponse({'ok': True, 'task_id': task.id})


@login_required
@require_POST
@transaction.atomic
def task_complete(request, task_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    task = get_object_or_404(
        task_queryset_for_user(
            Task.objects.select_for_update().filter(site=site),
            request.user,
        ),
        id=task_id,
    )
    before = {'status': task.status, 'completed_at': task.completed_at}
    try:
        task = complete_task(
            task_id=task.id,
            actor=request.user,
            outcome=request.POST.get('outcome', ''),
        )
    except ValidationError as exc:
        return _validation_response(exc)
    changed = before['status'] != task.status
    if changed:
        record_audit(
            actor=request.user,
            action='crm_task_completed',
            entity_table='crm_task',
            entity_id=task.id,
            before=before,
            after={'status': task.status, 'completed_at': task.completed_at, 'outcome': task.outcome},
            request=request,
        )
    return JsonResponse({
        'ok': True,
        'task_id': task.id,
        'status': task.status,
        'changed': changed,
    })


@login_required
@require_POST
def opportunity_stage(request, opportunity_id: int):
    site, _locale = default_site_locale(_requested_locale(request))
    with transaction.atomic():
        opportunity = get_object_or_404(
            crm_owned_queryset_for_user(
                Opportunity.objects.select_for_update().filter(site=site),
                request.user,
            ),
            id=opportunity_id,
        )
        require_sales(request.user, SalesCapability.WRITE, record=opportunity)
        submitted_version = (request.POST.get('version') or '').strip()
        if not opportunity_version_matches(
            opportunity.updated_at,
            submitted_version,
        ):
            return _opportunity_conflict_response(opportunity)
        form = OpportunityStageForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
        before = {'stage': opportunity.stage, 'probability': opportunity.probability}
        try:
            opportunity = change_opportunity_stage(
                opportunity_id=opportunity.id,
                stage=form.cleaned_data['stage'],
                actor=request.user,
                reason=form.cleaned_data['reason'],
            )
        except ValidationError as exc:
            return _validation_response(exc)
        record_audit(
            actor=request.user,
            action='crm_opportunity_stage_changed',
            entity_table='crm_opportunity',
            entity_id=opportunity.id,
            before=before,
            after={'stage': opportunity.stage, 'probability': opportunity.probability},
            request=request,
            metadata={'reason': form.cleaned_data['reason']},
        )
    return JsonResponse({
        'ok': True,
        'opportunity_id': opportunity.id,
        'stage': opportunity.stage,
        'version': opportunity_version_token(opportunity.updated_at),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def saved_view_save(request):
    site, _locale = default_site_locale(_requested_locale(request))
    if request.method == 'GET':
        queryset = saved_view_queryset_for_user(
            SavedView.objects.all(),
            request.user,
            site=site,
        )
        scope = (request.GET.get('scope') or '').strip()
        if scope:
            queryset = queryset.filter(scope=scope)
        items = queryset.select_related('user', 'team').order_by('scope', 'name', 'id')
        saved_views = []
        for item in items:
            payload = {
                'id': item.id,
                'scope': item.scope,
                'name': item.name,
                'filters': item.filters_json,
                'columns': item.columns_json,
                'sort': item.sort_json,
                'is_default': item.is_default,
                'is_shared': item.is_shared,
                'team_id': item.team_id,
                'team': item.team.name if item.team else '',
                'owner': _user_label(item.user),
            }
            if item.scope == 'leads':
                try:
                    v2_payload = lead_saved_view_payload(
                        item,
                        site=site,
                        user=request.user,
                        list_url=reverse('console:lead_workspace_v2'),
                    )
                except ValidationError:
                    payload.update({'filters': {}, 'sort': ['sla'], 'apply_url': '', 'invalid': True})
                else:
                    payload.update({
                        'filters': v2_payload['filters'],
                        'sort': v2_payload['sort'],
                        'columns': v2_payload['columns'],
                        'apply_url': v2_payload['apply_url'],
                        'is_default': v2_payload['is_default'],
                    })
            saved_views.append(payload)
        return JsonResponse(
            {
                'ok': True,
                'saved_views': saved_views,
            }
        )
    form = SavedViewForm(request.POST, site=site, user=request.user)
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errors': _errors(form)}, status=400)
    if form.cleaned_data['scope'] == 'leads':
        try:
            normalized = normalize_lead_saved_view_state(
                form.cleaned_data['filters_json'],
                form.cleaned_data['sort_json'],
                site=site,
                user=request.user,
            )
        except ValidationError as exc:
            return _validation_response(exc)
        form.cleaned_data['filters_json'] = normalized['filters']
        form.cleaned_data['sort_json'] = normalized['sort']
    try:
        with transaction.atomic():
            try:
                saved_view = form.save()
            except IntegrityError as exc:
                raise ValidationError(
                    '保存视图时发生并发冲突，请重试。'
                ) from exc
            record_audit(
                actor=request.user,
                action='crm_saved_view_saved',
                entity_table='crm_saved_view',
                entity_id=saved_view.id,
                after={
                    'scope': saved_view.scope,
                    'name': saved_view.name,
                    'is_shared': saved_view.is_shared,
                    'team_id': saved_view.team_id,
                },
                request=request,
            )
    except ValidationError as exc:
        return _validation_response(exc, status=409)
    response = {
        'ok': True,
        'saved_view_id': saved_view.id,
        'message': '视图已保存。',
    }
    if saved_view.scope == 'leads':
        response['saved_view'] = lead_saved_view_payload(
            saved_view,
            site=site,
            user=request.user,
            list_url=reverse('console:lead_workspace_v2'),
        )
    return JsonResponse(response)


@login_required
@require_POST
@transaction.atomic
def bulk_assign(request):
    roles = user_role_keys(request.user)
    if not roles.intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN}):
        return JsonResponse({'ok': False, 'errors': {'__all__': ['只有销售经理或系统管理员可以批量分配。']}}, status=403)
    site, _locale = default_site_locale(_requested_locale(request))
    record_type = (request.POST.get('record_type') or '').strip()
    record_ids = list(dict.fromkeys(
        int(value) for value in request.POST.getlist('record_ids') if str(value).isdigit()
    ))
    assignee_id = request.POST.get('assignee_id')
    if not record_ids or not str(assignee_id).isdigit():
        return JsonResponse({'ok': False, 'errors': {'__all__': ['请选择记录和负责人。']}}, status=400)
    assignee = get_object_or_404(assignee_queryset_for_user(request.user, site=site), id=int(assignee_id))
    membership_queryset = SalesTeamMember.objects.filter(
        user=assignee,
        team__site=site,
        team__enabled=True,
    )
    if ROLE_SYSTEM_ADMIN not in roles:
        managed_team_ids = SalesTeamMember.objects.filter(
            user=request.user,
            membership_role='manager',
            team__site=site,
            team__enabled=True,
        ).values('team_id')
        membership_queryset = membership_queryset.filter(team_id__in=managed_team_ids)
    membership = membership_queryset.select_related('team').order_by('team_id').first()
    if membership is None:
        return JsonResponse({'ok': False, 'errors': {'assignee_id': ['该用户不属于当前站点的销售团队。']}}, status=400)

    if record_type == 'leads':
        queryset = lead_queryset_for_user(
            LeadSubmission.objects.select_for_update().filter(site=site, id__in=record_ids),
            request.user,
        )
        owner_field = 'assignee'
        table = 'lead_submission'
    elif record_type == 'opportunities':
        queryset = crm_owned_queryset_for_user(
            Opportunity.objects.select_for_update().filter(site=site, id__in=record_ids),
            request.user,
        )
        owner_field = 'owner_user'
        table = 'crm_opportunity'
    elif record_type == 'tasks':
        queryset = task_queryset_for_user(
            Task.objects.select_for_update().filter(site=site, id__in=record_ids),
            request.user,
        )
        owner_field = 'owner_user'
        table = 'crm_task'
    else:
        return JsonResponse({'ok': False, 'errors': {'record_type': ['记录类型无效。']}}, status=400)

    records = list(queryset)
    if len(records) != len(record_ids):
        return JsonResponse(
            {'ok': False, 'errors': {'record_ids': ['部分记录不存在或不在你的管理范围内。']}},
            status=404,
        )
    if record_type == 'tasks':
        invalid_task_ids = [
            record.id
            for record in records
            if record.team_id is None
            or not SalesTeamMember.objects.filter(
                team_id=record.team_id,
                user=assignee,
                team__site=site,
                team__enabled=True,
            ).exists()
        ]
        if invalid_task_ids:
            return JsonResponse(
                {
                    'ok': False,
                    'errors': {
                        'assignee_id': [
                            '负责人必须是每条任务当前团队的有效成员；任务团队不会因分配而改变。'
                        ]
                    },
                },
                status=400,
            )
    for record in records:
        before_owner = getattr(record, f'{owner_field}_id')
        setattr(record, owner_field, assignee)
        if record_type == 'tasks':
            record.save(update_fields=[owner_field, 'updated_at'])
        else:
            record.team = membership.team
            record.save(update_fields=[owner_field, 'team', 'updated_at'])
        if record_type == 'leads':
            activity_kwargs = {'submission': record}
        elif record_type == 'opportunities':
            activity_kwargs = {
                'submission': record.source_submission,
                'opportunity': record,
                'company': record.company,
                'contact': record.primary_contact,
            }
        else:
            activity_kwargs = {
                'submission': record.submission,
                'opportunity': record.opportunity,
                'company': record.company,
                'contact': record.contact,
            }
        append_activity(
            site=site,
            actor=request.user,
            activity_type='assignment',
            subject=f'负责人调整为 {assignee.get_full_name().strip() or assignee.get_username()}',
            metadata={'previous_owner_user_id': before_owner, 'owner_user_id': assignee.id},
            **activity_kwargs,
        )
        record_audit(
            actor=request.user,
            action='crm_bulk_assigned',
            entity_table=table,
            entity_id=record.id,
            before={'owner_user_id': before_owner},
            after={'owner_user_id': assignee.id, 'team_id': record.team_id},
            request=request,
        )
    return JsonResponse({'ok': True, 'updated': len(records)})
