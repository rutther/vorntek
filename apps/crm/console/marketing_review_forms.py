from django import forms


class MarketingReviewForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    event_id = forms.CharField(label='完整事件编号', max_length=120,
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off', 'spellcheck': 'false'}))
    outcome = forms.ChoiceField(label='实际核对结果', choices=(
        ('', '按平台记录选择，不能确认先不登记'),
        ('accepted', '确认平台已接受，本次不再上传'),
        ('not_accepted', '确认平台未接受，恢复原资格重试')),
        widget=forms.Select(attrs={'class': 'form-select'}))
    request_id = forms.CharField(label='平台请求编号（Google requestId）', max_length=500, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off', 'spellcheck': 'false'}))
    evidence = forms.CharField(label='核对依据', min_length=10, max_length=500,
        help_text='记录平台日志引用、核对时间与编号说明；不要填写密码、Token或客户个人资料。',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}))
    confirmed = forms.BooleanField(label='我已按完整事件编号核对，确认不是其他事件。')
    stopped = forms.BooleanField(label='我已确认本次发送结束；可能还在发送时先不登记。')

    def __init__(self, *args, outbox, attempt=None, **kwargs):
        self.outbox, self.attempt = outbox, attempt
        super().__init__(*args, **kwargs)
        if outbox.integration.provider.code == 'meta':
            self.fields['request_id'].label = '平台日志引用编号（选填）'

    def clean(self):
        data = super().clean()
        if data.get('event_id') and data['event_id'].strip() != self.outbox.event_id:
            self.add_error('event_id', '请粘贴与上方一致的完整编号，不要只比较前几位。')
        validation = (self.attempt.delivery_mode == 'validation' if self.attempt else
                      bool((self.outbox.payload_json or {}).get('validateOnly')))
        if (data.get('outcome') == 'accepted' and self.outbox.integration.provider.code == 'google'
                and not validation and not data.get('request_id')):
            self.add_error('request_id', 'Google正式上传必须填写已核对的完整requestId；只有接受不代表处理成功。')
        return data

    def associate_errors(self):
        focused = False
        for name, field in self.fields.items():
            bound = self[name]
            described = []
            if field.help_text:
                described.append(bound.id_for_label + '_help')
            if name in self.errors:
                field.widget.attrs['aria-invalid'] = 'true'
                if not focused and not field.widget.is_hidden:
                    field.widget.attrs['autofocus'] = True
                    focused = True
                described.append(bound.id_for_label + '_errors')
            if described:
                field.widget.attrs['aria-describedby'] = ' '.join(described)
