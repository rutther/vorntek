from __future__ import annotations

from django import forms

from .privacy_retention import update_retention_policy
from .privacy_versions import retention_policy_version_token


class RetentionPolicyForm(forms.Form):
    version = forms.CharField(required=False, max_length=256, widget=forms.HiddenInput)
    enabled = forms.BooleanField(label='启用数据保留策略', required=False)
    lead_pii_retention_days = forms.IntegerField(
        label='线索个人信息', min_value=0, max_value=36500,
        help_text='设为 0 表示关闭线索 PII 自动匿名化。',
    )
    inbound_payload_retention_days = forms.IntegerField(
        label='Webhook 原始载荷', min_value=1, max_value=36500,
    )
    outbox_payload_retention_days = forms.IntegerField(
        label='回传请求与响应载荷', min_value=1, max_value=36500,
    )
    privacy_request_retention_days = forms.IntegerField(
        label='已结束隐私请求的身份信息', min_value=1, max_value=36500,
    )
    whatsapp_message_retention_days = forms.IntegerField(
        label='WhatsApp 消息内容', min_value=1, max_value=36500, required=False,
    )
    whatsapp_media_retention_days = forms.IntegerField(
        label='WhatsApp 媒体文件', min_value=1, max_value=36500, required=False,
    )

    def __init__(self, *args, policy, **kwargs):
        self.policy = policy
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.update({
                'version': retention_policy_version_token(policy),
                'enabled': policy.enabled,
                'lead_pii_retention_days': policy.lead_pii_retention_days,
                'inbound_payload_retention_days': policy.inbound_payload_retention_days,
                'outbox_payload_retention_days': policy.outbox_payload_retention_days,
                'privacy_request_retention_days': policy.privacy_request_retention_days,
                'whatsapp_message_retention_days': policy.whatsapp_message_retention_days,
                'whatsapp_media_retention_days': policy.whatsapp_media_retention_days,
            })

    def save(self, *, actor):
        return update_retention_policy(
            policy=self.policy,
            actor=actor,
            enabled=bool(self.cleaned_data.get('enabled')),
            lead_pii_retention_days=self.cleaned_data['lead_pii_retention_days'],
            inbound_payload_retention_days=self.cleaned_data['inbound_payload_retention_days'],
            outbox_payload_retention_days=self.cleaned_data['outbox_payload_retention_days'],
            privacy_request_retention_days=self.cleaned_data['privacy_request_retention_days'],
            whatsapp_message_retention_days=(
                self.cleaned_data.get('whatsapp_message_retention_days')
                or self.policy.whatsapp_message_retention_days
            ),
            whatsapp_media_retention_days=(
                self.cleaned_data.get('whatsapp_media_retention_days')
                or self.policy.whatsapp_media_retention_days
            ),
        )


class RetentionExecutionForm(forms.Form):
    version = forms.CharField(required=True, max_length=256, widget=forms.HiddenInput)
    confirm = forms.CharField(
        label='执行确认词',
        required=True,
        max_length=80,
        widget=forms.TextInput(attrs={
            'autocomplete': 'off',
            'placeholder': '输入 APPLY RETENTION',
        }),
    )

    def clean_confirm(self):
        value = str(self.cleaned_data.get('confirm') or '').strip()
        if value != 'APPLY RETENTION':
            raise forms.ValidationError('请输入 APPLY RETENTION 确认执行。')
        return value
