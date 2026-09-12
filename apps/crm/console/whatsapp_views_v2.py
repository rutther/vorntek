from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from leads.models import (
    SalesTeamMember,
    WhatsAppConversation,
    WhatsAppMedia,
    WhatsAppMessage,
    WhatsAppTemplate,
)
from leads.whatsapp_cloud import (
    WhatsAppIdempotencyConflict,
    WhatsAppPolicyError,
    dispatch_whatsapp_message,
    queue_whatsapp_message,
)

from .access import (
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    primary_role_label,
    user_role_keys,
    whatsapp_conversation_queryset_for_user,
)
from .audit import record_audit
from .capabilities import SalesCapability, can_sales, require_sales
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale
from .whatsapp_workspace_v2 import build_whatsapp_workspace_v2


User = get_user_model()


def _conversation_snapshot(conversation: WhatsAppConversation) -> dict:
    return {
        'site_id': conversation.site_id,
        'submission_id': conversation.submission_id,
        'company_id': conversation.company_id,
        'contact_id': conversation.contact_id,
        'owner_user_id': conversation.owner_user_id,
        'team_id': conversation.team_id,
        'status': conversation.status,
        'unread_count': conversation.unread_count,
        'closed_at': conversation.closed_at,
        'opted_out_at': conversation.opted_out_at,
    }


def _message_snapshot(message: WhatsAppMessage) -> dict:
    return {
        'conversation_id': message.conversation_id,
        'direction': message.direction,
        'message_type': message.message_type,
        'status': message.status,
        'template_id': message.template_id,
        'reply_to_id': message.reply_to_id,
        'attempts': message.attempts,
        'retryable': message.retryable,
    }


def _scoped_conversation(request, conversation_id: int, *, for_update: bool = False):
    site, _locale = default_site_locale(None)
    queryset = WhatsAppConversation.objects.filter(site=site).select_related(
        'integration', 'submission', 'contact', 'company', 'owner_user', 'team',
    )
    queryset = whatsapp_conversation_queryset_for_user(queryset, request.user)
    if for_update:
        queryset = queryset.select_for_update(of=('self',))
    conversation = queryset.filter(pk=conversation_id).first()
    if conversation is None:
        raise Http404('当前数据范围中没有这条 WhatsApp 会话。')
    return conversation


def _page_context(request, *, conversation_id: int | None = None) -> dict:
    require_sales(request.user, SalesCapability.READ)
    site, locale = default_site_locale(None)
    workspace = build_whatsapp_workspace_v2(
        request=request,
        site=site,
        conversation_id=conversation_id,
    )
    if workspace['selected_missing']:
        raise Http404('当前数据范围中没有这条 WhatsApp 会话。')
    return {
        'workspace': workspace,
        'nav_items': build_navigation('whatsapp', request.user),
        'console_role_label': primary_role_label(request.user),
        'preview_site_url': f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
        'create_actions': [],
    }


def _json_error(message: str, *, status: int = 400) -> JsonResponse:
    return JsonResponse({'ok': False, 'error': str(message)[:1000]}, status=status)


def _require_conversation_write(user, conversation: WhatsAppConversation) -> None:
    require_sales(
        user,
        SalesCapability.WRITE,
        record=conversation,
        message='请先认领会话，或由团队负责人完成转交。',
    )


def _mock_inline_dispatch(message: WhatsAppMessage) -> tuple[WhatsAppMessage, bool]:
    mode = str(
        (message.conversation.integration.config_json or {}).get('delivery_mode') or 'mock'
    ).strip().lower()
    if mode != 'mock' or not settings.SITEOS_WHATSAPP_INLINE_MOCK_DISPATCH:
        return message, False
    return dispatch_whatsapp_message(message_id=message.id)


def _upload_media_type(content_type: str) -> str:
    content_type = str(content_type or '').lower()
    if content_type.startswith('image/'):
        return 'image'
    if content_type.startswith('video/'):
        return 'video'
    if content_type.startswith('audio/'):
        return 'audio'
    return 'document'


def _store_private_upload(upload) -> tuple[str, str, str, str]:
    if upload.size <= 0 or upload.size > settings.SITEOS_WHATSAPP_MEDIA_MAX_BYTES:
        raise WhatsAppPolicyError('附件为空或超过允许大小。')
    root = Path(settings.SITEOS_WHATSAPP_MEDIA_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.name or '').suffix.lower()[:16]
    destination = root / f'{uuid.uuid4().hex}{suffix}'
    try:
        with destination.open('xb') as handle:
            for chunk in upload.chunks():
                handle.write(chunk)
    except OSError as exc:
        raise WhatsAppPolicyError('无法保存 WhatsApp 私有附件。') from exc
    return (
        _upload_media_type(upload.content_type),
        str(destination),
        str(upload.content_type or '').lower(),
        Path(upload.name or 'attachment').name[:512],
    )


@login_required
@require_GET
def whatsapp_workspace_v2(request):
    context = _page_context(request)
    rows = context['workspace']['rows']
    if rows:
        return redirect(rows[0]['detail_url'])
    return render(request, 'console/v2/pages/whatsapp.html', context)


@login_required
@require_GET
def whatsapp_workspace_detail_v2(request, conversation_id: int):
    return render(
        request,
        'console/v2/pages/whatsapp.html',
        _page_context(request, conversation_id=conversation_id),
    )


@login_required
@require_GET
def whatsapp_media_download_v2(request, conversation_id: int, message_id: int, media_id: int):
    conversation = _scoped_conversation(request, conversation_id)
    media = WhatsAppMedia.objects.filter(
        pk=media_id,
        message_id=message_id,
        message__conversation=conversation,
        status='ready',
    ).first()
    if media is None or not media.storage_path:
        raise Http404('附件不存在、尚未就绪或不在当前会话。')
    root = Path(settings.SITEOS_WHATSAPP_MEDIA_ROOT).resolve()
    path = Path(media.storage_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Http404('附件存储路径无效。') from exc
    if not path.is_file():
        raise Http404('附件文件不存在。')
    record_audit(
        actor=request.user,
        action='whatsapp_media_downloaded',
        entity_table='whatsapp_media',
        entity_id=media.id,
        request=request,
        metadata={'conversation_id': conversation.id, 'message_id': message_id},
    )
    response = FileResponse(
        path.open('rb'),
        as_attachment=True,
        filename=Path(media.original_name or path.name).name,
        content_type=media.mime_type or 'application/octet-stream',
    )
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def whatsapp_send_v2(request, conversation_id: int):
    uploaded_path = None
    try:
        with transaction.atomic():
            conversation = _scoped_conversation(request, conversation_id, for_update=True)
            _require_conversation_write(request.user, conversation)
            mode = str(request.POST.get('mode') or 'text').strip().lower()
            template = None
            if mode == 'template':
                template_id = str(request.POST.get('template_id') or '').strip()
                if not template_id.isdigit():
                    raise WhatsAppPolicyError('请选择已批准模板。')
                template = WhatsAppTemplate.objects.filter(
                    pk=int(template_id),
                    integration=conversation.integration,
                    status='approved',
                ).first()
                if template is None:
                    raise WhatsAppPolicyError('模板不存在、未批准或不属于当前接入。')
            reply_to = None
            reply_to_id = str(request.POST.get('reply_to_id') or '').strip()
            if reply_to_id:
                if not reply_to_id.isdigit():
                    raise WhatsAppPolicyError('回复目标无效。')
                reply_to = WhatsAppMessage.objects.filter(
                    pk=int(reply_to_id),
                    conversation=conversation,
                ).first()
                if reply_to is None:
                    raise WhatsAppPolicyError('回复目标不属于当前会话。')

            media_type = media_path = mime_type = original_name = ''
            upload = request.FILES.get('attachment')
            if upload is not None:
                media_type, media_path, mime_type, original_name = _store_private_upload(upload)
                uploaded_path = Path(media_path)
            message, created = queue_whatsapp_message(
                conversation=conversation,
                actor=request.user,
                idempotency_token=str(request.POST.get('idempotency_token') or ''),
                body=str(request.POST.get('body') or ''),
                template=template,
                template_parameters=[],
                reply_to=reply_to,
                media_type=media_type,
                media_path=media_path,
                mime_type=mime_type,
                original_name=original_name,
            )
            if created:
                record_audit(
                    actor=request.user,
                    action='whatsapp_message_queued',
                    entity_table='whatsapp_message',
                    entity_id=message.id,
                    before=None,
                    after=_message_snapshot(message),
                    request=request,
                    metadata={'conversation_id': conversation.id},
                )
    except PermissionDenied:
        raise
    except WhatsAppIdempotencyConflict as exc:
        if uploaded_path is not None:
            uploaded_path.unlink(missing_ok=True)
        return _json_error(str(exc), status=409)
    except WhatsAppPolicyError as exc:
        if uploaded_path is not None:
            uploaded_path.unlink(missing_ok=True)
        return _json_error(str(exc), status=400)
    except Exception:
        if uploaded_path is not None:
            uploaded_path.unlink(missing_ok=True)
        raise

    message = WhatsAppMessage.objects.select_related('conversation__integration').get(pk=message.pk)
    message, sent = _mock_inline_dispatch(message)
    return JsonResponse({
        'ok': True,
        'created': created,
        'message': _message_snapshot(message),
        'sent': sent,
    }, status=201 if created else 200)


@login_required
@require_POST
def whatsapp_note_v2(request, conversation_id: int):
    body = str(request.POST.get('body') or '').strip()
    if not body or len(body) > 4000:
        return _json_error('内部备注不能为空，且不能超过 4000 个字符。')
    with transaction.atomic():
        conversation = _scoped_conversation(request, conversation_id, for_update=True)
        _require_conversation_write(request.user, conversation)
        note = WhatsAppMessage.objects.create(
            conversation=conversation,
            actor_user=request.user,
            direction='internal',
            message_type='text',
            body=body,
            status='recorded',
            queued_at=timezone.now(),
        )
        record_audit(
            actor=request.user,
            action='whatsapp_internal_note_created',
            entity_table='whatsapp_message',
            entity_id=note.id,
            after=_message_snapshot(note),
            request=request,
            metadata={'conversation_id': conversation.id},
        )
    return JsonResponse({'ok': True, 'message': _message_snapshot(note)}, status=201)


@login_required
@require_POST
def whatsapp_assign_v2(request, conversation_id: int):
    target_id = str(request.POST.get('owner_user_id') or '').strip()
    if not target_id.isdigit():
        return _json_error('请选择有效负责人。')
    with transaction.atomic():
        conversation = _scoped_conversation(request, conversation_id, for_update=True)
        before = _conversation_snapshot(conversation)
        roles = user_role_keys(request.user)
        target = User.objects.filter(pk=int(target_id), is_active=True).first()
        if target is None:
            return _json_error('负责人不存在或已停用。')
        member = bool(
            conversation.team_id
            and SalesTeamMember.objects.filter(
                team_id=conversation.team_id,
                user=target,
                team__enabled=True,
            ).exists()
        )
        if not member:
            return _json_error('负责人不是当前销售团队的有效成员。')
        is_self_claim = bool(
            ROLE_SALES in roles
            and conversation.owner_user_id is None
            and target.id == request.user.id
            and member
        )
        if not is_self_claim:
            require_sales(request.user, SalesCapability.ASSIGN, record=conversation)
        conversation.owner_user = target
        conversation.updated_at = timezone.now()
        conversation.save(update_fields=['owner_user', 'updated_at'])
        if conversation.submission_id and conversation.submission.team_id == conversation.team_id:
            conversation.submission.assignee = target
            conversation.submission.updated_at = conversation.updated_at
            conversation.submission.save(update_fields=['assignee', 'updated_at'])
        record_audit(
            actor=request.user,
            action='whatsapp_conversation_assigned',
            entity_table='whatsapp_conversation',
            entity_id=conversation.id,
            before=before,
            after=_conversation_snapshot(conversation),
            request=request,
        )
    return JsonResponse({'ok': True, 'conversation': _conversation_snapshot(conversation)})


@login_required
@require_POST
def whatsapp_state_v2(request, conversation_id: int):
    action = str(request.POST.get('action') or '').strip().lower()
    if action not in {'close', 'reopen'}:
        return _json_error('不支持的会话状态操作。')
    with transaction.atomic():
        conversation = _scoped_conversation(request, conversation_id, for_update=True)
        _require_conversation_write(request.user, conversation)
        before = _conversation_snapshot(conversation)
        if action == 'close':
            conversation.status = 'closed'
            conversation.closed_at = timezone.now()
        else:
            if conversation.status == 'blocked' or conversation.opted_out_at:
                return _json_error('停止联系或已阻止的会话不能直接重新打开。', status=409)
            conversation.status = 'open'
            conversation.closed_at = None
        conversation.updated_at = timezone.now()
        conversation.save(update_fields=['status', 'closed_at', 'updated_at'])
        record_audit(
            actor=request.user,
            action=(
                'whatsapp_conversation_closed'
                if action == 'close'
                else 'whatsapp_conversation_reopened'
            ),
            entity_table='whatsapp_conversation',
            entity_id=conversation.id,
            before=before,
            after=_conversation_snapshot(conversation),
            request=request,
        )
    return JsonResponse({'ok': True, 'conversation': _conversation_snapshot(conversation)})


@login_required
@require_POST
def whatsapp_mark_read_v2(request, conversation_id: int):
    with transaction.atomic():
        conversation = _scoped_conversation(request, conversation_id, for_update=True)
        _require_conversation_write(request.user, conversation)
        before = _conversation_snapshot(conversation)
        conversation.unread_count = 0
        conversation.updated_at = timezone.now()
        conversation.save(update_fields=['unread_count', 'updated_at'])
        record_audit(
            actor=request.user,
            action='whatsapp_conversation_read',
            entity_table='whatsapp_conversation',
            entity_id=conversation.id,
            before=before,
            after=_conversation_snapshot(conversation),
            request=request,
        )
    return JsonResponse({'ok': True, 'conversation': _conversation_snapshot(conversation)})


@login_required
@require_POST
def whatsapp_retry_v2(request, conversation_id: int, message_id: int):
    with transaction.atomic():
        conversation = _scoped_conversation(request, conversation_id, for_update=True)
        _require_conversation_write(request.user, conversation)
        message = WhatsAppMessage.objects.select_for_update().filter(
            pk=message_id,
            conversation=conversation,
            direction='outbound',
            status='failed',
        ).first()
        if message is None:
            raise Http404('没有可重试的发送记录。')
        if not message.retryable:
            return _json_error('该失败结果不可安全自动重试，需要人工核对。', status=409)
        before = _message_snapshot(message)
        message.status = 'queued'
        message.failed_at = None
        message.error_code = ''
        message.error_message = ''
        message.retryable = False
        message.next_attempt_at = None
        message.updated_at = timezone.now()
        message.save(update_fields=[
            'status', 'failed_at', 'error_code', 'error_message',
            'retryable', 'next_attempt_at', 'updated_at',
        ])
        record_audit(
            actor=request.user,
            action='whatsapp_message_retry_queued',
            entity_table='whatsapp_message',
            entity_id=message.id,
            before=before,
            after=_message_snapshot(message),
            request=request,
            metadata={'conversation_id': conversation.id},
        )
    message = WhatsAppMessage.objects.select_related('conversation__integration').get(pk=message.id)
    message, sent = _mock_inline_dispatch(message)
    return JsonResponse({'ok': True, 'message': _message_snapshot(message), 'sent': sent})
