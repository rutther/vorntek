from __future__ import annotations

from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from leads.email_delivery import build_email_connection, email_delivery_configured, load_runtime_email_config
from leads.models import LeadSubmission


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


def _project_context(submission):
    if _field(submission, 'business_line'):
        return '应用背景', _field(submission, 'application_context') or '未填写'
    return '产能目标', _field(submission, 'capacity') or '未填写'


def _subject(submission: LeadSubmission, *, reminder: bool) -> str:
    prefix = '待跟进提醒' if reminder else '新项目询盘'
    identity = submission.company or submission.full_name or submission.email or submission.phone or f'#{submission.id}'
    product = _field(submission, 'product_category')
    capacity = _field(submission, 'capacity')
    summary = ' / '.join(item for item in [product, capacity] if item)
    return f'[{prefix}] {identity}' + (f' - {summary}' if summary else '')


def _text_body(submission: LeadSubmission, *, reminder: bool) -> str:
    rows = [
        '这是一条尚未处理的网站项目询盘。' if reminder else 'Vorntek 收到一条新的项目询盘。',
        '',
        f'姓名：{submission.full_name or "未填写"}',
        f'公司：{submission.company or "未填写"}',
        f'国家/地区：{submission.country or "未填写"}',
        f'邮箱：{submission.email or "未填写"}',
        f'电话/WhatsApp：{submission.phone or "未填写"}',
        f'产品类别：{_field(submission, "product_category") or "未填写"}',
        '：'.join(_project_context(submission)),
        f'项目需求：{submission.message or "未填写"}',
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
        ('姓名', submission.full_name or '未填写'),
        ('公司', submission.company or '未填写'),
        ('国家/地区', submission.country or '未填写'),
        ('邮箱', submission.email or '未填写'),
        ('电话/WhatsApp', submission.phone or '未填写'),
        ('产品类别', _field(submission, 'product_category') or '未填写'),
        _project_context(submission),
        ('项目需求', submission.message or '未填写'),
        ('来源渠道', submission.source_channel or 'website'),
        ('来源说明', submission.source_detail or '未记录'),
    ]
    rows = ''.join(
        f'<tr><th style="padding:8px 12px;text-align:left;background:#f4f6f9;border:1px solid #d8dee8">{escape(label)}</th>'
        f'<td style="padding:8px 12px;border:1px solid #d8dee8">{escape(str(value))}</td></tr>'
        for label, value in fields
    )
    lead = '这条询盘仍处于待跟进状态，请尽快确认负责人和下一步。' if reminder else 'Vorntek 收到一条新的项目询盘。'
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
    recipients = notification_recipients(submission)
    if not recipients:
        if not reminder:
            submission.notification_status = 'disabled'
            submission.notification_error = '表单未配置通知邮箱。'
            submission.save(update_fields=['notification_status', 'notification_error', 'updated_at'])
        return False

    if not email_delivery_configured():
        message = 'SMTP 账号和授权密码尚未配置。'
        if reminder:
            submission.notification_error = message
            submission.save(update_fields=['notification_error', 'updated_at'])
        else:
            submission.notification_status = 'disabled'
            submission.notification_error = message
            submission.save(update_fields=['notification_status', 'notification_error', 'updated_at'])
        return False

    delivery = load_runtime_email_config()
    message = EmailMultiAlternatives(
        subject=_subject(submission, reminder=reminder),
        body=_text_body(submission, reminder=reminder),
        from_email=delivery.from_email,
        to=recipients,
        reply_to=[submission.email] if submission.email else None,
        connection=build_email_connection(delivery),
    )
    message.attach_alternative(_html_body(submission, reminder=reminder), 'text/html')

    try:
        sent = bool(message.send(fail_silently=False))
    except Exception as exc:  # Delivery failure must not break public form submission.
        if reminder:
            submission.notification_error = f'提醒发送失败：{str(exc)[:700]}'
            submission.save(update_fields=['notification_error', 'updated_at'])
        else:
            submission.notification_status = 'failed'
            submission.notification_error = str(exc)[:700]
            submission.save(update_fields=['notification_status', 'notification_error', 'updated_at'])
        return False

    now = timezone.now()
    if reminder:
        submission.reminder_sent_at = now
        submission.notification_error = ''
        submission.save(update_fields=['reminder_sent_at', 'notification_error', 'updated_at'])
    else:
        submission.notification_status = 'sent' if sent else 'failed'
        submission.notification_error = '' if sent else '邮件后端未确认发送。'
        submission.notified_at = now if sent else None
        submission.save(update_fields=['notification_status', 'notification_error', 'notified_at', 'updated_at'])
    return sent


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
