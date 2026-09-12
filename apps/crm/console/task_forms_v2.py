from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from leads.models import SalesTeamMember

from .task_workspace_v2 import PRIORITIES, TASK_TYPES
from .task_versions import task_version_token


DATETIME_WIDGET = forms.DateTimeInput(
    format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}
)


class TaskMutationForm(forms.Form):
    version = forms.CharField(max_length=256)
    title = forms.CharField(label='任务标题', max_length=240)
    description = forms.CharField(label='说明', required=False, max_length=4000)
    task_type = forms.ChoiceField(
        label='类型', choices=[(key, value) for key, value in TASK_TYPES.items()]
    )
    priority = forms.ChoiceField(
        label='优先级', choices=[(key, value[0]) for key, value in PRIORITIES.items()]
    )
    due_at = forms.DateTimeField(
        label='到期时间', widget=DATETIME_WIDGET, input_formats=['%Y-%m-%dT%H:%M']
    )
    owner_user = forms.ModelChoiceField(
        label='负责人', queryset=get_user_model().objects.none(), required=False
    )

    def __init__(self, *args, task, site, user, allow_assign: bool, **kwargs):
        self.task = task
        self.site = site
        self.user = user
        self.allow_assign = allow_assign
        super().__init__(*args, **kwargs)
        users = get_user_model().objects.filter(pk=task.owner_user_id)
        if allow_assign and task.team_id:
            users = get_user_model().objects.filter(
                is_active=True,
                sales_team_memberships__team_id=task.team_id,
                sales_team_memberships__team__site=site,
                sales_team_memberships__team__enabled=True,
            ).distinct()
        self.fields['owner_user'].queryset = users.order_by('username')
        if not self.is_bound:
            self.initial.update({
                'version': task_version_token(task.updated_at),
                'title': task.title,
                'description': task.description,
                'task_type': task.task_type,
                'priority': task.priority,
                'due_at': timezone.localtime(task.due_at).strftime('%Y-%m-%dT%H:%M'),
                'owner_user': task.owner_user_id,
            })

    def clean_owner_user(self):
        owner = self.cleaned_data.get('owner_user') or self.task.owner_user
        if owner.pk != self.task.owner_user_id and not self.allow_assign:
            raise ValidationError('当前账号不能重新分配任务。')
        if (
            not owner.is_active
            or self.task.team_id is None
            or not SalesTeamMember.objects.filter(
                team_id=self.task.team_id,
                user=owner,
                team__site=self.site,
                team__enabled=True,
            ).exists()
        ):
            raise ValidationError('负责人必须是任务当前团队的有效成员。')
        return owner


class TaskCreateV2Form(forms.Form):
    idempotency_token = forms.CharField(max_length=200)
    target_type = forms.ChoiceField(
        choices=(('lead', '线索'), ('company', '企业'), ('contact', '联系人'), ('opportunity', '销售机会'))
    )
    target_id = forms.IntegerField(min_value=1)
    title = forms.CharField(label='任务标题', max_length=240)
    description = forms.CharField(label='说明', required=False, max_length=4000)
    task_type = forms.ChoiceField(choices=[(key, value) for key, value in TASK_TYPES.items()])
    priority = forms.ChoiceField(choices=[(key, value[0]) for key, value in PRIORITIES.items()])
    due_at = forms.DateTimeField(widget=DATETIME_WIDGET, input_formats=['%Y-%m-%dT%H:%M'])
    owner_user = forms.ModelChoiceField(queryset=get_user_model().objects.none())

    def __init__(self, *args, site, target_team, owner_queryset, **kwargs):
        self.site = site
        self.target_team = target_team
        super().__init__(*args, **kwargs)
        self.fields['owner_user'].queryset = owner_queryset.filter(is_active=True).order_by('username')

    def clean_owner_user(self):
        owner = self.cleaned_data['owner_user']
        if (
            self.target_team is None
            or not SalesTeamMember.objects.filter(
                team=self.target_team,
                user=owner,
                team__site=self.site,
                team__enabled=True,
            ).exists()
        ):
            raise ValidationError('负责人必须是关联对象所在团队的有效成员。')
        return owner


class OpportunityCreateV2Form(forms.Form):
    idempotency_token = forms.CharField(max_length=200)
    company_id = forms.IntegerField(min_value=1)
    name = forms.CharField(label='项目名称', max_length=240)
    owner_user = forms.ModelChoiceField(queryset=get_user_model().objects.none())
    value_amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False)
    currency = forms.CharField(min_length=3, max_length=3, initial='USD')
    probability = forms.IntegerField(min_value=0, max_value=100, initial=10)
    expected_close_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    product_scope = forms.CharField(required=False, max_length=2000)
    capacity_target = forms.CharField(required=False, max_length=2000)
    packaging_format = forms.CharField(required=False, max_length=2000)
    next_step = forms.CharField(required=False, max_length=2000)
    next_follow_up_at = forms.DateTimeField(
        required=False, widget=DATETIME_WIDGET, input_formats=['%Y-%m-%dT%H:%M']
    )

    def __init__(self, *args, site, company, owner_queryset, **kwargs):
        self.site = site
        self.company = company
        super().__init__(*args, **kwargs)
        self.fields['owner_user'].queryset = owner_queryset.filter(is_active=True).order_by('username')

    def clean_company_id(self):
        value = self.cleaned_data['company_id']
        if value != self.company.pk:
            raise ValidationError('企业关联已变化，请刷新页面后重试。')
        return value

    def clean_currency(self):
        value = (self.cleaned_data['currency'] or '').strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValidationError('币种必须使用三个英文字母，例如 USD。')
        return value

    def clean_owner_user(self):
        owner = self.cleaned_data['owner_user']
        if (
            self.company.team_id is None
            or not SalesTeamMember.objects.filter(
                team_id=self.company.team_id,
                user=owner,
                team__site=self.site,
                team__enabled=True,
            ).exists()
        ):
            raise ValidationError('负责人必须是企业所在团队的有效成员。')
        return owner


class TaskStartV2Form(forms.Form):
    version = forms.CharField(max_length=256)


class TaskCompleteV2Form(forms.Form):
    version = forms.CharField(max_length=256)
    outcome = forms.CharField(label='完成结果', min_length=1, max_length=2000)
    create_next_task = forms.BooleanField(required=False)
    next_title = forms.CharField(required=False, max_length=240)
    next_description = forms.CharField(required=False, max_length=4000)
    next_task_type = forms.ChoiceField(
        required=False, choices=[(key, value) for key, value in TASK_TYPES.items()]
    )
    next_priority = forms.ChoiceField(
        required=False, choices=[(key, value[0]) for key, value in PRIORITIES.items()]
    )
    next_due_at = forms.DateTimeField(
        required=False, widget=DATETIME_WIDGET, input_formats=['%Y-%m-%dT%H:%M']
    )
    next_owner_user = forms.ModelChoiceField(
        required=False, queryset=get_user_model().objects.none()
    )

    def __init__(self, *args, task, site, owner_queryset, **kwargs):
        self.task = task
        self.site = site
        super().__init__(*args, **kwargs)
        self.fields['next_owner_user'].queryset = owner_queryset.filter(is_active=True).order_by('username')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('create_next_task'):
            required = {
                'next_title': '下一任务标题',
                'next_task_type': '下一任务类型',
                'next_priority': '下一任务优先级',
                'next_due_at': '下一任务到期时间',
                'next_owner_user': '下一任务负责人',
            }
            for field, label in required.items():
                if not cleaned.get(field):
                    self.add_error(field, f'{label}不能为空。')
        return cleaned


class TaskCancelV2Form(forms.Form):
    version = forms.CharField(max_length=256)
    reason = forms.CharField(label='取消原因', min_length=1, max_length=2000)


class ActivityCreateV2Form(forms.Form):
    idempotency_token = forms.CharField(max_length=200)
    target_type = forms.ChoiceField(
        choices=(('lead', '线索'), ('company', '企业'), ('contact', '联系人'), ('opportunity', '销售机会'))
    )
    target_id = forms.IntegerField(min_value=1)
    activity_type = forms.ChoiceField(
        choices=(('note', '跟进记录'), ('call', '电话'), ('email', '邮件'), ('whatsapp', 'WhatsApp'), ('meeting', '会议'), ('site_visit', '现场拜访'))
    )
    direction = forms.ChoiceField(
        choices=(('inbound', '入站'), ('outbound', '出站'), ('internal', '内部'))
    )
    subject = forms.CharField(max_length=240)
    body = forms.CharField(max_length=8000)
    occurred_at = forms.DateTimeField(widget=DATETIME_WIDGET, input_formats=['%Y-%m-%dT%H:%M'])


class ActivityCorrectionV2Form(forms.Form):
    idempotency_token = forms.CharField(max_length=200)
    subject = forms.CharField(max_length=240)
    body = forms.CharField(max_length=8000)
