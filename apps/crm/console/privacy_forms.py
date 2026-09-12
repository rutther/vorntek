from __future__ import annotations

from django import forms

from leads.models import LeadSubmission

from .privacy_versions import privacy_request_version_token


PRIVACY_TYPE_CHOICES = (
    ('export', '导出个人数据'),
    ('withdraw_consent', '撤回营销同意'),
    ('restrict', '限制继续处理'),
    ('delete', '删除或匿名化'),
)


class PrivacyRequestRegistrationForm(forms.Form):
    request_type = forms.ChoiceField(label='请求类型', choices=PRIVACY_TYPE_CHOICES)
    requester_name = forms.CharField(label='请求人姓名', required=False, max_length=500)
    requester_email = forms.EmailField(label='邮箱', required=False, max_length=500)
    requester_phone = forms.CharField(label='电话或 WhatsApp', required=False, max_length=500)
    submission_id = forms.IntegerField(label='关联线索编号', required=False, min_value=1)

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.fields['requester_name'].widget.attrs.update({'autocomplete': 'name'})
        self.fields['requester_email'].widget.attrs.update({'autocomplete': 'email'})
        self.fields['requester_phone'].widget.attrs.update({'autocomplete': 'tel'})
        self.fields['submission_id'].help_text = '可留空；已知 CRM 线索时填写编号，可缩小数据主体匹配范围。'

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('requester_email') and not str(cleaned.get('requester_phone') or '').strip():
            raise forms.ValidationError('邮箱或电话至少填写一项。')
        submission_id = cleaned.get('submission_id')
        submission = None
        if submission_id:
            submission = LeadSubmission.objects.filter(site=self.site, id=submission_id).first()
            if submission is None:
                self.add_error('submission_id', '当前站点没有这条线索。')
        cleaned['submission'] = submission
        return cleaned


class PrivacyRequestWorkflowForm(forms.Form):
    version = forms.CharField(required=False, max_length=256, widget=forms.HiddenInput)
    resolution = forms.CharField(
        label='处理说明或结论',
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 5,
            'placeholder': '记录身份核验依据、与请求人的沟通结果，以及完成或拒绝的原因。',
        }),
    )
    confirm = forms.CharField(
        label='敏感操作确认词',
        required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'off'}),
    )

    def __init__(self, *args, privacy_request=None, **kwargs):
        self.privacy_request = privacy_request
        super().__init__(*args, **kwargs)
        if privacy_request is not None and not self.is_bound:
            self.initial['version'] = privacy_request_version_token(privacy_request)
