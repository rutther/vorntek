from __future__ import annotations

import json

from django import forms

from leads.email_delivery import (
    SMTP_CONFIG_SECRET_KEY,
    SMTP_PASSWORD_SECRET_KEY,
    load_runtime_email_config,
    load_stored_smtp_settings,
    smtp_password_exists,
)

from .secret_store import delete_secret, store_secret


SECURITY_CHOICES = (
    ('ssl', 'SSL / TLS（通常为 465）'),
    ('tls', 'STARTTLS（通常为 587）'),
    ('none', '不加密（不建议）'),
)


class SmtpSettingsForm(forms.Form):
    enabled = forms.BooleanField(label='启用邮件通知', required=False)
    host = forms.CharField(
        label='SMTP 服务器',
        max_length=255,
        widget=forms.TextInput(attrs={'placeholder': '例如：smtphz.qiye.163.com'}),
    )
    port = forms.IntegerField(label='端口', min_value=1, max_value=65535)
    security = forms.ChoiceField(label='连接加密', choices=SECURITY_CHOICES)
    username = forms.EmailField(
        label='发信邮箱账号',
        widget=forms.EmailInput(attrs={'placeholder': '完整企业邮箱地址'}),
    )
    from_email = forms.EmailField(
        label='邮件发件人',
        widget=forms.EmailInput(attrs={'placeholder': '通常与发信邮箱账号相同'}),
    )
    password = forms.CharField(
        label='客户端授权密码',
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password', 'placeholder': '留空表示保持已保存密码'}),
        help_text='使用邮箱服务商生成的客户端授权密码；系统加密保存且不会再次回显。',
    )
    clear_stored_config = forms.BooleanField(label='删除后台 SMTP 配置', required=False)
    test_recipient = forms.EmailField(
        label='测试收件人',
        required=False,
        widget=forms.EmailInput(attrs={'placeholder': '保存并测试时接收测试邮件'}),
    )

    def __init__(self, *args, updated_by: str = 'django-console', default_email: str = '', **kwargs):
        super().__init__(*args, **kwargs)
        self.updated_by = updated_by
        self.password_exists = smtp_password_exists()
        self.stored_settings = load_stored_smtp_settings()
        runtime = load_runtime_email_config()
        if not self.is_bound:
            security = 'ssl' if runtime.use_ssl else ('tls' if runtime.use_tls else 'none')
            username = runtime.username or default_email
            runtime_ready = bool(getattr(runtime, 'ready', False))
            self.initial.update(
                {
                    'enabled': bool(self.stored_settings.get('enabled', runtime_ready)),
                    'host': runtime.host if runtime.host not in {'', 'localhost', '127.0.0.1'} else 'smtphz.qiye.163.com',
                    'port': runtime.port if runtime.port not in {0, 25} else 465,
                    'security': security if runtime.host not in {'', 'localhost', '127.0.0.1'} else 'ssl',
                    'username': username,
                    'from_email': runtime.from_email if runtime_ready and '@' in runtime.from_email else username,
                    'test_recipient': default_email or username,
                }
            )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('clear_stored_config'):
            return cleaned
        if cleaned.get('enabled') and not (self.password_exists or cleaned.get('password')):
            self.add_error('password', '首次启用时必须填写客户端授权密码。')
        if self.data.get('action') == 'save_test' and not cleaned.get('test_recipient'):
            self.add_error('test_recipient', '发送测试邮件前请填写测试收件人。')
        return cleaned

    def save(self) -> None:
        if self.cleaned_data.get('clear_stored_config'):
            delete_secret(SMTP_CONFIG_SECRET_KEY)
            delete_secret(SMTP_PASSWORD_SECRET_KEY)
            self.password_exists = False
            return

        payload = {
            'enabled': bool(self.cleaned_data.get('enabled')),
            'host': self.cleaned_data['host'].strip(),
            'port': int(self.cleaned_data['port']),
            'security': self.cleaned_data['security'],
            'username': self.cleaned_data['username'].strip(),
            'from_email': self.cleaned_data['from_email'].strip(),
            'timeout': 8,
        }
        store_secret(
            secret_key=SMTP_CONFIG_SECRET_KEY,
            plaintext=json.dumps(payload, ensure_ascii=True, separators=(',', ':')),
            updated_by=self.updated_by,
        )
        password = self.cleaned_data.get('password') or ''
        if password:
            store_secret(
                secret_key=SMTP_PASSWORD_SECRET_KEY,
                plaintext=password,
                updated_by=self.updated_by,
            )
            self.password_exists = True
