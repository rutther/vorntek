from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from urllib.parse import urlencode, urlsplit

from console.access import ROLE_SYSTEM_ADMIN, lead_queryset_for_user, primary_role_label, safe_next_url, user_has_any_role
from console.capabilities import SalesCapability, require_sales
from console.navigation import build_navigation
from console.notification_forms import NotificationReviewForm
from console.models import AuditLog
from console.payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale
from leads.models import LeadNotificationAttempt, LeadSubmission
from leads.notification_review import notification_review_version, reconcile_notification_attempt


STATE_LABELS = {'claimed': '发送前准备', 'handoff': '正在交接或待确认', 'unknown': '结果待核对',
                'accepted': '邮件平台已接受', 'failed': '本次未发送成功', 'cancelled': '交接前已取消'}


def _return_url(request, lead):
    path = reverse('console:lead_workspace_detail_v2', args=[lead.pk])
    fallback = path + ('?view=trash' if lead.trashed_at else '')
    result = safe_next_url(request, request.POST.get('next') or request.GET.get('next'), fallback=fallback)
    return result if urlsplit(result).path == path else fallback


def _lead(request, submission_id):
    require_sales(request.user, SalesCapability.READ)
    site, _locale = default_site_locale((request.GET.get('locale') or request.POST.get('locale') or '').strip() or None)
    record = lead_queryset_for_user(LeadSubmission.objects.filter(site=site), request.user).filter(pk=submission_id).first()
    if record is None:
        raise Http404('没有可查看的线索。')
    return record


def _render(request, lead, *, bound_form=None, selected_id=None, status=200, error=''):
    fresh_actor = get_user_model().objects.filter(pk=request.user.pk, is_active=True).first()
    can_review = bool(fresh_actor and user_has_any_role(fresh_actor, {ROLE_SYSTEM_ADMIN}))
    attempts = list(LeadNotificationAttempt.objects.filter(submission=lead).order_by('-created_at')[:20])
    reviewed_ids = set(AuditLog.objects.filter(action='notification_attempt_reconciled', entity_table='lead_submission',
        entity_id=lead.pk, metadata_json__attempt_id__in=[str(a.pk) for a in attempts]).values_list('metadata_json__attempt_id', flat=True))
    items = []
    seen_kinds = set()
    for attempt in attempts:
        historical = attempt.kind in seen_kinds
        seen_kinds.add(attempt.kind)
        form = None
        if can_review and attempt.state in ('unknown', 'handoff'):
            if status == 409 and selected_id == attempt.pk and bound_form is not None:
                renewed = bound_form.data.copy()
                renewed['version'] = notification_review_version(attempt)
                renewed.pop('confirmed', None)
                renewed.pop('stopped', None)
                bound_form = NotificationReviewForm(renewed, auto_id=f'id_review_{attempt.pk}_%s')
            form = bound_form if selected_id == attempt.pk else NotificationReviewForm(
                initial={'version': notification_review_version(attempt)}, auto_id=f'id_review_{attempt.pk}_%s')
        label = '通知未配置或已停用' if attempt.policy_disabled else STATE_LABELS[attempt.state]
        items.append({'attempt': attempt, 'state_label': label, 'form': form, 'historical': historical,
                      'reviewed': str(attempt.pk) in reviewed_ids,
                      'action_url': reverse('console:notification_reconcile', args=[lead.pk, attempt.pk])})
    back_url = _return_url(request, lead)
    return render(request, 'console/v2/pages/notification_review.html', {
        'lead': lead, 'review_items': items, 'can_review': can_review, 'review_error': error,
        'nav_items': build_navigation('leads', request.user), 'console_role_label': primary_role_label(request.user),
        'back_url': back_url, 'preview_site_url': PUBLIC_PREVIEW_BASE_URL,
    }, status=status)


@login_required
@require_GET
def notification_review(request, submission_id):
    return _render(request, _lead(request, submission_id))


@login_required
@require_POST
def notification_reconcile(request, submission_id, attempt_id):
    lead = _lead(request, submission_id)
    if not LeadNotificationAttempt.objects.filter(pk=attempt_id, submission=lead).exists():
        raise Http404('没有可核对的发送记录。')
    form = NotificationReviewForm(request.POST, auto_id=f'id_review_{attempt_id}_%s')
    if not form.is_valid():
        return _render(request, lead, bound_form=form, selected_id=attempt_id, status=400,
                       error='尚未保存，请检查下方必填项；输入已保留。')
    try:
        changed = reconcile_notification_attempt(
            actor_id=request.user.pk, session_key=request.session.session_key, submission_id=lead.pk,
            site_id=lead.site_id, attempt_id=attempt_id, **form.cleaned_data)
    except ValidationError as exc:
        return _render(request, lead, bound_form=form, selected_id=attempt_id, status=409, error=exc.messages[0])
    except PermissionDenied as exc:
        return _render(request, lead, bound_form=form, selected_id=attempt_id, status=403, error=str(exc))
    except DatabaseError:
        return _render(request, lead, bound_form=form, selected_id=attempt_id, status=503,
                       error='暂未保存核对结果，记录没有改变。输入已保留，请稍后重试。')
    messages.success(request, '已登记核对结果；此处没有发送邮件。' if changed else '这份核对结果已经登记，没有重复操作。')
    next_url = _return_url(request, lead)
    target = reverse('console:notification_review', args=[lead.pk])
    return redirect(target + ('?' + urlencode({'next': next_url}) if next_url else ''))
