from __future__ import annotations

import re

from django import forms


_VERSION = re.compile(r'^[a-f0-9]{64}$')


class WebsiteCandidateSelectionForm(forms.Form):
    expected_version = forms.CharField(required=False, widget=forms.HiddenInput)
    request_token = forms.UUIDField(widget=forms.HiddenInput)
    reason = forms.CharField(
        label='选择原因',
        min_length=8,
        max_length=500,
        widget=forms.Textarea(attrs={'rows': 5, 'autocomplete': 'off'}),
        help_text='请说明本次选择或回滚的依据；该说明会进入不可变审计记录。',
    )

    def clean_expected_version(self):
        value = self.cleaned_data['expected_version']
        if value and not _VERSION.fullmatch(value):
            raise forms.ValidationError('当前选择版本无效，请刷新页面。')
        return value

    def clean_request_token(self):
        value = self.cleaned_data['request_token']
        if value.version != 4:
            raise forms.ValidationError('请求令牌无效，请刷新页面。')
        return value

    def clean_reason(self):
        return self.cleaned_data['reason'].strip()
