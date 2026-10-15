from urllib.parse import urlencode, urlsplit

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from console.access import MARKETING_ROLES, primary_role_label, safe_next_url, user_has_any_role
from console.marketing_event_queries import OUTBOUND_STATUS, redact_event_error
from console.marketing_queries import outbox_retry_contract
from console.marketing_review_forms import MarketingReviewForm
from console.models import AuditLog
from console.navigation import build_navigation
from console.payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale
from leads.models import LeadEventAttempt, LeadEventOutbox
from leads.marketing_review import marketing_review_version, reconcile_marketing_event, reviewable


def _event(request, outbox_id):
    if not user_has_any_role(request.user, MARKETING_ROLES):
        raise PermissionDenied('当前账号没有处理营销事件的权限。')
    site, _locale = default_site_locale(None)
    supported = (Q(integration__provider__code='meta', integration__integration_type='pixel')
                 | Q(integration__provider__code='google', integration__integration_type='data_manager'))
    return get_object_or_404(LeadEventOutbox.objects.select_related('integration__provider', 'submission').filter(supported),
        pk=outbox_id, submission__site=site, integration__site=site)


def _back(request):
    fallback = reverse('console:marketing_events')
    candidate = safe_next_url(request, request.POST.get('return_to') or request.GET.get('return_to'), fallback=fallback)
    return candidate if urlsplit(candidate).path == fallback else fallback


def _original_target(event, latest):
    if event.integration.provider.code == 'google':
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        destinations = payload.get('destinations')
        if isinstance(destinations, list) and destinations and isinstance(destinations[0], dict):
            account = destinations[0].get('operatingAccount')
            if isinstance(account, dict) and account.get('accountId'):
                return str(account['accountId'])
    return latest.public_id if latest else event.integration.public_id


def _render(request, event, *, bound_form=None, status=200, error=''):
    event.refresh_from_db()
    attempts = list(LeadEventAttempt.objects.filter(outbox=event).order_by('-generation')[:20])
    latest = attempts[0] if attempts else None
    fresh = get_user_model().objects.filter(pk=request.user.pk, is_active=True).first()
    can_review = bool(fresh and user_has_any_role(fresh, MARKETING_ROLES))
    eligible = can_review and reviewable(event, latest)
    form = None
    if eligible:
        if status == 409 and bound_form:
            renewed = bound_form.data.copy()
            renewed['version'] = marketing_review_version(event, latest)
            renewed.pop('confirmed', None)
            renewed.pop('stopped', None)
            bound_form = MarketingReviewForm(renewed, outbox=event, attempt=latest)
        form = bound_form or MarketingReviewForm(outbox=event, attempt=latest,
            initial={'version': marketing_review_version(event, latest)})
        form.associate_errors()
    reviewed = AuditLog.objects.filter(action='marketing_attempt_reconciled',
        entity_table='lead_event_outbox', entity_id=event.pk).order_by('-created_at', '-pk').first()
    retryable, reason = outbox_retry_contract(event)
    state_labels = {'claimed': '发送前准备', 'handoff': '交接中或结果待确认', 'unknown': '结果待核对',
                    'accepted': '已记录平台接受', 'failed': '本次未成功', 'cancelled': '交接前取消'}
    history = [{'attempt': a, 'label': state_labels.get(a.state, a.state),
                'manual': bool((a.response_json or {}).get('manual_review'))} for a in attempts]
    return render(request, 'console/v2/pages/marketing_review.html', {
        'event': event, 'latest_attempt': latest, 'review_form': form, 'review_error': error,
        'review_history': history, 'reviewed': reviewed, 'can_review': can_review,
        'current_manual': bool((latest.response_json if latest else event.response_json or {}).get('manual_review')),
        'retained_evidence': bound_form['evidence'].value() if bound_form and not eligible else '',
        'status_label': OUTBOUND_STATUS.get(event.status, (event.status, 'secondary'))[0],
        'back_url': _back(request), 'save_url': reverse('console:marketing_outbox_reconcile', args=[event.pk]),
        'retry_reason': reason, 'retryable': retryable, 'error_summary': redact_event_error(event.last_error),
        'is_google': event.integration.provider.code == 'google',
        'original_target_id': _original_target(event, latest),
        'delivery_mode': latest.delivery_mode if latest else event.delivery_mode,
        'nav_items': build_navigation('marketing_events', request.user),
        'console_role_label': primary_role_label(request.user), 'preview_site_url': PUBLIC_PREVIEW_BASE_URL,
    }, status=status)


@login_required
@require_GET
def marketing_outbox_review(request, outbox_id):
    return _render(request, _event(request, outbox_id))


@login_required
@require_POST
def marketing_outbox_reconcile(request, outbox_id):
    event = _event(request, outbox_id)
    latest = LeadEventAttempt.objects.filter(outbox=event).first()
    form = MarketingReviewForm(request.POST, outbox=event, attempt=latest)
    if not form.is_valid():
        return _render(request, event, bound_form=form, status=400, error='尚未保存，请检查必填项；输入已保留。')
    try:
        changed = reconcile_marketing_event(actor_id=request.user.pk, session_key=request.session.session_key,
            outbox_id=event.pk, site_id=event.submission.site_id, **form.cleaned_data)
    except ValidationError as exc:
        return _render(request, event, bound_form=form, status=409, error=exc.messages[0])
    except PermissionDenied as exc:
        return _render(request, event, bound_form=form, status=403, error=str(exc))
    except DatabaseError:
        return _render(request, event, bound_form=form, status=503,
            error='暂未保存核对结果，记录没有改变。输入已保留，请稍后重试。')
    messages.success(request, '已保存人工核对结果；本页没有发送事件。' if changed else '这份核对已登记，没有重复保存或发送。')
    return redirect(reverse('console:marketing_outbox_review', args=[event.pk]) + '?' + urlencode({'return_to': _back(request)}))
