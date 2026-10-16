from django import forms


class NotificationReviewForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    outcome = forms.ChoiceField(label='实际核对结果', choices=(
        ('', '按邮件平台记录选择'), ('accepted', '邮件平台已接受，不再重发'),
        ('not_accepted', '确认未接受，恢复正常重试'),
    ), widget=forms.Select(attrs={'class': 'form-select'}))
    evidence = forms.CharField(label='核对依据', min_length=10, max_length=500,
        help_text='记录日志引用、时间或消息编号；不要填写密码、Token或客户个人资料。',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': '例如：已核对本次发送的SMTP日志及消息编号，平台未接受。'}))
    confirmed = forms.BooleanField(label='我已核对本次尝试的记录，确认不是其他邮件。', required=True)
    stopped = forms.BooleanField(label='我已确认本次发送已经结束；如果可能仍在发送，先不登记。', required=True)
