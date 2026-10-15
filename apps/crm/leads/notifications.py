from __future__ import annotations

from html import escape
import smtplib

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import connection, transaction
from django.utils import timezone

from leads.email_delivery import build_email_connection, email_delivery_configured, load_runtime_email_config
from leads.models import LeadSubmission
from leads.form_schema import CORE_FIELDS, configured_submission_fields, effective_form_fields, public_form_fields
from leads.notification_attempts import (
    begin_notification_handoff, claim_notification, finish_notification_attempt,
    notification_message_hash, notification_message_id,
)


def _split_notification_emails(raw: str) -> list[str]:
    normalized = (raw or '').replace(';', ',').replace('\n', ',')
    return [item.strip() for item in normalized.split(',') if item.strip()]


def notification_recipients(submission: LeadSubmission) -> list[str]:
    return _split_notification_emails(submission.form.notify_emails)


def form_notification_recipients(form_definition) -> list[str]:
    return _split_notification_emails(form_definition.notify_emails)


def submission_admin_url(submission: LeadSubmission) -> str:
    base = str(getattr(settings, 'SITEOS_ADMIN_PUBLIC_URL', 'http://localhost:8088/admin')).rstrip('/')
    return f'{base}/leads/submissions/{submission.id}/'


def _field(submission: LeadSubmission, key: str) -> str:
    return str((submission.payload_json or {}).get(key) or '').strip()


def _notification_fields(submission):
    configured = configured_submission_fields(submission)
    if configured is not None:
        return [(field['name'], field['label'], field['value']) for field in configured]
    fields = public_form_fields(submission.form)
    # Retain existing explicitly legacy-form context, not a product default.
    if effective_form_fields(submission.form) is None and _field(submission, 'business_line'):
        fields.append({'name': 'application_context', 'label': '应用背景'})
    rows = []
    for field in fields:
        name = field['name']
        value = str(getattr(submission, name, '') or '') if name in CORE_FIELDS else _field(submission, name)
        labels = {option['value']: option.get('label', option['value'])
                  for option in field.get('options', []) if isinstance(option, dict)}
        rows.append((name, field['label'], labels.get(value, value) or '未填写'))
    return rows


def _subject(submission: LeadSubmission, *, reminder: bool) -> str:
    prefix = '待跟进提醒' if reminder else '新咨询'
    fields = _notification_fields(submission)
    enabled = {name for name, _label, _value in fields}
    identity = next((getattr(submission, name) for name in ('company', 'full_name', 'email', 'phone')
                     if name in enabled and getattr(submission, name, '')), f'#{submission.id}')
    summary = ' / '.join(value[:100] for name, _label, value in fields
                         if name not in CORE_FIELDS and value != '未填写')[:200]
    return f'[{prefix}] {identity}' + (f' - {summary}' if summary else '')


def _text_body(submission: LeadSubmission, *, reminder: bool) -> str:
    rows = [
        '这是一条尚未处理的网站咨询。' if reminder else 'Vorntek 收到一条新的网站咨询。',
        '',
        *(f'{label}：{value}' for _name, label, value in _notification_fields(submission)),
        f'来源渠道：{submission.source_channel or "website"}',
        f'来源说明：{submission.source_detail or "未记录"}',
        f'提交页面：{submission.source_url or "未记录"}',
        f'提交时间：{timezone.localtime(submission.submitted_at):%Y-%m-%d %H:%M}',
        '',
        f'后台查看：{submission_admin_url(submission)}',
    ]
    return '\n'.join(rows)


def _html_body(submission: LeadSubmission, *, reminder: bool) -> str:
    fields = [
        *((label, value) for _name, label, value in _notification_fields(submission)),
        ('来源渠道', submission.source_channel or 'website'),
        ('来源说明', submission.source_detail or '未记录'),
    ]
    rows = ''.join(
        f'<tr><th style="padding:8px 12px;text-align:left;background:#f4f6f9;border:1px solid #d8dee8">{escape(label)}</th>'
        f'<td style="padding:8px 12px;border:1px solid #d8dee8">{escape(str(value))}</td></tr>'
        for label, value in fields
    )
    lead = '这条咨询仍处于待跟进状态，请尽快确认负责人和下一步。' if reminder else 'Vorntek 收到一条新的网站咨询。'
    return (
        '<div style="font-family:Arial,sans-serif;color:#13213a;line-height:1.6">'
        f'<h2 style="margin:0 0 12px">{escape(_subject(submission, reminder=reminder))}</h2>'
        f'<p>{escape(lead)}</p><table style="border-collapse:collapse;width:100%;max-width:720px">{rows}</table>'
        f'<p style="margin-top:20px"><a href="{escape(submission_admin_url(submission))}" '
        'style="display:inline-block;padding:10px 16px;background:#1246d8;color:#fff;text-decoration:none">进入后台处理</a></p>'
        '</div>'
    )


def send_submission_notification(submission: LeadSubmission, *, reminder: bool = False) -> bool:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return False
    if connection.in_atomic_block:
        submission_id = submission.pk
        transaction.on_commit(
            lambda: _send_submission_notification_now(LeadSubmission(pk=submission_id), reminder=reminder),
            robust=True,
        )
        # Queued is not sent. A rollback discards the callback and no delivery
        # fact or external side effect has happened inside the caller's commit.
        return False
    return _send_submission_notification_now(submission, reminder=reminder)


def _send_submission_notification_now(submission: LeadSubmission, *, reminder: bool = False) -> bool:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return False
    # The caller may hold a pre-trash snapshot. Do not construct an SMTP
    # connection or rewrite notification state for a retired lead.
    submission = LeadSubmission.objects.select_related('form').filter(pk=submission.pk, trashed_at__isnull=True).first()
    if submission is None or (not reminder and submission.notification_status == 'sent'):
        return False
    claimed = claim_notification(submission.pk, 'reminder' if reminder else 'initial')
    if claimed is None:
        return False
    submission, attempt = claimed
    recipients = notification_recipients(submission)
    if not recipients:
        finish_notification_attempt(attempt.pk, 'failed', '表单未配置通知邮箱。', disabled=True)
        return False

    delivery = load_runtime_email_config()
    if not delivery.ready:
        finish_notification_attempt(attempt.pk, 'failed', delivery.error or 'SMTP 账号和授权密码尚未配置。', disabled=not bool(delivery.error))
        return False

    try:
        message = EmailMultiAlternatives(
            subject=_subject(submission, reminder=reminder),
            body=_text_body(submission, reminder=reminder),
            from_email=delivery.from_email,
            to=recipients,
            reply_to=[submission.email] if submission.email else None,
            connection=build_email_connection(delivery),
            headers={'Message-ID': notification_message_id(attempt.pk, delivery.from_email)},
        )
        message.attach_alternative(_html_body(submission, reminder=reminder), 'text/html')
    except Exception as exc:
        finish_notification_attempt(attempt.pk, 'failed', f'发送准备失败：{str(exc)[:500]}')
        return False
    if begin_notification_handoff(attempt.pk, recipients, delivery, notification_message_hash(message),
                                   message.extra_headers['Message-ID']) is None:
        return False
    try:
        sent = bool(message.send(fail_silently=False))
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused,
            smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError) as exc:
        finish_notification_attempt(attempt.pk, 'failed', f'邮件平台明确拒绝（{type(exc).__name__}）。')
        return False
    except Exception as exc:
        finish_notification_attempt(attempt.pk, 'unknown', f'发送结果未确认（{type(exc).__name__}）；请核对邮件平台后再处理。')
        return False
    recorded = finish_notification_attempt(attempt.pk, 'accepted' if sent else 'failed',
                                           '' if sent else '邮件后端未确认发送。')
    return bool(sent and recorded)



def send_smtp_test_notification(recipient: str) -> tuple[bool, str]:
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return False, 'External delivery is disabled for this deployment.'
    delivery = load_runtime_email_config()
    if not delivery.ready:
        return False, delivery.error or 'SMTP 配置不完整。'
    message = EmailMultiAlternatives(
        subject='[SMTP 测试] Vorntek 新线索通知',
        body=(
            '这是一封由 Vorntek 后台发送的 SMTP 配置测试邮件。\n\n'
            '如果你收到这封邮件，说明服务器连接、身份验证和发信地址配置已经通过。'
        ),
        from_email=delivery.from_email,
        to=[recipient],
        connection=build_email_connection(delivery),
    )
    try:
        sent = bool(message.send(fail_silently=False))
    except Exception as exc:
        return False, str(exc)[:700]
    return sent, '' if sent else '邮件后端未确认发送。'
