from __future__ import annotations

import re

from django import forms


_VERSION = re.compile(r'^[a-f0-9]{64}$')


class WebsiteDeploymentForm(forms.Form):
    expected_selection_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_deployed_version = forms.CharField(
        required=False,
        widget=forms.HiddenInput,
    )
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    reason = forms.CharField(
        label='部署原因',
        min_length=8,
        max_length=500,
        widget=forms.Textarea(attrs={'rows': 5, 'autocomplete': 'off'}),
        help_text='说明本次上线或回滚依据；该说明会写入不可变部署收据。',
    )

    def clean_expected_deployed_version(self):
        value = self.cleaned_data['expected_deployed_version']
        if value and not _VERSION.fullmatch(value):
            raise forms.ValidationError('当前部署版本无效，请刷新页面。')
        return value

    def clean_request_token(self):
        value = self.cleaned_data['request_token']
        if value.version != 4:
            raise forms.ValidationError('请求令牌无效，请刷新页面。')
        return value

    def clean_reason(self):
        return self.cleaned_data['reason'].strip()
