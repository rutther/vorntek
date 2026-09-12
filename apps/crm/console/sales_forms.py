from __future__ import annotations

import json

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from leads.models import Company, Contact, Opportunity, SalesTeam, SalesTeamMember, SavedView, Task

from .access import (
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    assignee_queryset_for_user,
    crm_owned_queryset_for_user,
    shareable_sales_team_queryset_for_user,
    user_role_keys,
)
from .sales_queries import PIPELINE_STAGES


DATETIME_WIDGET = forms.DateTimeInput(
    format='%Y-%m-%dT%H:%M',
    attrs={'type': 'datetime-local'},
)


class LeadConversionForm(forms.Form):
    opportunity_name = forms.CharField(
        label='项目名称',
        max_length=240,
        required=False,
        help_text='留空时，系统使用企业名称和产品范围生成项目名称。',
    )


class ActivityEditorForm(forms.Form):
    ACTIVITY_TYPES = [
        ('note', '跟进记录'),
        ('call', '电话'),
        ('email', '邮件'),
        ('whatsapp', 'WhatsApp'),
        ('meeting', '会议'),
        ('site_visit', '现场拜访'),
    ]
    DIRECTIONS = [
        ('outbound', '我方发起'),
        ('inbound', '客户发起'),
        ('internal', '内部记录'),
    ]

    activity_type = forms.ChoiceField(label='活动类型', choices=ACTIVITY_TYPES)
    direction = forms.ChoiceField(label='沟通方向', choices=DIRECTIONS)
    subject = forms.CharField(label='主题', max_length=240)
    body = forms.CharField(label='记录', widget=forms.Textarea(attrs={'rows': 5}))
    occurred_at = forms.DateTimeField(
        label='发生时间',
        widget=DATETIME_WIDGET,
        input_formats=['%Y-%m-%dT%H:%M'],
        initial=timezone.now,
    )


class CrmAttachmentUploadForm(forms.Form):
    title = forms.CharField(label='附件标题', max_length=240, required=False)
    file = forms.FileField(label='文件')


class CompanyEditorForm(forms.Form):
    name = forms.CharField(label='企业名称', max_length=240)
    website = forms.CharField(label='网站', max_length=500, required=False)
    industry = forms.CharField(label='行业', max_length=240, required=False)
    country = forms.CharField(label='国家/地区', max_length=160, required=False)
    city = forms.CharField(label='城市', max_length=160, required=False)
    status = forms.ChoiceField(
        label='状态',
        choices=[('prospect', '潜在客户'), ('customer', '客户'), ('inactive', '停用')],
    )
    owner_user = forms.ModelChoiceField(
        label='负责人', queryset=None, required=False,
    )
    notes = forms.CharField(label='企业备注', required=False)

    def __init__(self, *args, company: Company, site=None, user=None, **kwargs):
        self.company = company
        self.site = site or company.site
        self.user = user
        super().__init__(*args, **kwargs)
        if user is None:
            owners = get_user_model().objects.filter(pk=company.owner_user_id)
        else:
            owners = assignee_queryset_for_user(user, site=self.site)
            if company.team_id:
                owners = owners.filter(
                    sales_team_memberships__team_id=company.team_id,
                    sales_team_memberships__team__enabled=True,
                ).distinct()
            else:
                owners = owners.none()
            if company.owner_user_id and not owners.filter(pk=company.owner_user_id).exists():
                owners = get_user_model().objects.filter(
                    Q(pk__in=owners.values('pk')) | Q(pk=company.owner_user_id)
                )
        self.fields['owner_user'].queryset = owners.order_by('username')
        if not self.is_bound:
            for name in self.fields:
                self.initial[name] = getattr(company, f'{name}_id', None) if name == 'owner_user' else getattr(company, name)

    def clean_owner_user(self):
        if self.is_bound and 'owner_user' not in self.data:
            return self.company.owner_user
        owner = self.cleaned_data.get('owner_user')
        if owner is None:
            if self.user is not None and not user_role_keys(self.user).intersection(
                {ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN}
            ):
                raise ValidationError('销售不能把自己的企业改为未分配。')
            return None
        if owner.pk == self.company.owner_user_id:
            return owner
        if (
            not owner.is_active
            or self.company.team_id is None
            or not SalesTeamMember.objects.filter(
                user=owner,
                team_id=self.company.team_id,
                team__site=self.site,
                team__enabled=True,
            ).exists()
        ):
            raise ValidationError('负责人必须是企业所在销售团队的有效成员。')
        return owner

    def clean(self):
        cleaned = super().clean()
        if 'owner_user' not in cleaned or (
            self.is_bound and 'owner_user' not in self.data
        ):
            return cleaned
        owner = cleaned.get('owner_user')
        owner_id = owner.pk if owner is not None else None
        if owner_id == self.company.owner_user_id:
            return cleaned

        blockers = []
        if self.company.contacts.exclude(owner_user_id=owner_id).exists():
            blockers.append('联系人')
        if self.company.opportunities.filter(
            stage__in={
                'qualification', 'discovery', 'solution', 'quotation',
                'negotiation', 'on_hold',
            }
        ).exclude(owner_user_id=owner_id).exists():
            blockers.append('销售机会')
        if self.company.lead_conversions.exclude(
            submission__assignee_id=owner_id
        ).exists():
            blockers.append('原始线索')
        if blockers:
            labels = '、'.join(blockers)
            self.add_error(
                'owner_user',
                f'该企业仍有关联的{labels}由其他负责人持有。请先通过受控账户重分配流程统一负责人。',
            )
        return cleaned

    def save(self) -> Company:
        editable = (
            'name', 'website', 'industry', 'country', 'city', 'status', 'owner_user', 'notes',
        )
        update_fields = []
        for name in editable:
            if name == 'owner_user' and self.is_bound and 'owner_user' not in self.data:
                continue
            value = self.cleaned_data[name]
            setattr(self.company, name, value)
            update_fields.append(name)
        self.company.normalized_name = ' '.join(self.company.name.casefold().split())
        self.company.save(update_fields=[*update_fields, 'normalized_name', 'updated_at'])
        return self.company


class ContactEditorForm(forms.Form):
    full_name = forms.CharField(label='联系人姓名', max_length=240)
    job_title = forms.CharField(label='职位', max_length=240, required=False)
    email = forms.EmailField(label='邮箱', max_length=320, required=False)
    phone = forms.CharField(label='电话', max_length=120, required=False)
    whatsapp_phone = forms.CharField(label='WhatsApp', max_length=120, required=False)
    country = forms.CharField(label='国家/地区', max_length=160, required=False)
    preferred_language = forms.CharField(label='首选语言', max_length=16, required=False)
    status = forms.ChoiceField(
        label='状态',
        choices=[('active', '有效'), ('inactive', '停用')],
    )
    company = forms.ModelChoiceField(label='企业', queryset=None, required=False)
    owner_user = forms.ModelChoiceField(label='负责人', queryset=None, required=False)
    notes = forms.CharField(label='联系人备注', required=False)

    def __init__(self, *args, contact: Contact, site=None, user=None, **kwargs):
        self.contact = contact
        self.site = site or contact.site
        self.user = user
        super().__init__(*args, **kwargs)
        if user is None:
            owners = get_user_model().objects.filter(pk=contact.owner_user_id)
            companies = Company.objects.filter(pk=contact.company_id)
        else:
            owners = assignee_queryset_for_user(user, site=self.site)
            if contact.team_id:
                owners = owners.filter(
                    sales_team_memberships__team_id=contact.team_id,
                    sales_team_memberships__team__enabled=True,
                ).distinct()
            else:
                owners = owners.none()
            if contact.owner_user_id and not owners.filter(pk=contact.owner_user_id).exists():
                owners = get_user_model().objects.filter(
                    Q(pk__in=owners.values('pk')) | Q(pk=contact.owner_user_id)
                )
            companies = crm_owned_queryset_for_user(
                Company.objects.filter(site=self.site), user
            ).filter(Q(team_id=contact.team_id) | Q(pk=contact.company_id))
        self.fields['owner_user'].queryset = owners.order_by('username')
        self.fields['company'].queryset = companies.order_by('name', 'id')
        if not self.is_bound:
            for name in self.fields:
                if name in {'owner_user', 'company'}:
                    self.initial[name] = getattr(contact, f'{name}_id')
                else:
                    self.initial[name] = getattr(contact, name)

    def clean_owner_user(self):
        if self.is_bound and 'owner_user' not in self.data:
            return self.contact.owner_user
        owner = self.cleaned_data.get('owner_user')
        if owner is None:
            if self.user is not None and not user_role_keys(self.user).intersection(
                {ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN}
            ):
                raise ValidationError('销售不能把自己的联系人改为未分配。')
            return None
        if owner.pk == self.contact.owner_user_id:
            return owner
        if (
            not owner.is_active
            or self.contact.team_id is None
            or not SalesTeamMember.objects.filter(
                user=owner,
                team_id=self.contact.team_id,
                team__site=self.site,
                team__enabled=True,
            ).exists()
        ):
            raise ValidationError('负责人必须是联系人所在销售团队的有效成员。')
        return owner

    def clean_company(self):
        if self.is_bound and 'company' not in self.data:
            return self.contact.company
        company = self.cleaned_data.get('company')
        if company is not None and (
            company.site_id != self.site.pk
            or company.team_id != self.contact.team_id
        ):
            raise ValidationError('联系人只能关联同站点、同销售团队的企业。')
        if self.contact.pk:
            if self.contact.primary_opportunities.exclude(company=company).exists():
                raise ValidationError('联系人仍是其他企业销售机会的主要联系人，不能直接改企业。')
            if self.contact.lead_conversions.exclude(company=company).exists():
                raise ValidationError('联系人仍关联其他企业的线索转换，不能直接改企业。')
        return company

    def clean(self):
        cleaned = super().clean()
        owner = cleaned.get('owner_user', self.contact.owner_user)
        company = cleaned.get('company', self.contact.company)
        owner_id = owner.pk if owner is not None else None
        company_id = company.pk if company is not None else None
        owner_changed = (
            'owner_user' in cleaned
            and (not self.is_bound or 'owner_user' in self.data)
            and owner_id != self.contact.owner_user_id
        )
        company_changed = (
            'company' in cleaned
            and (not self.is_bound or 'company' in self.data)
            and company_id != self.contact.company_id
        )
        if (owner_changed or company_changed) and company is not None:
            if company.owner_user_id != owner_id:
                self.add_error(
                    'company',
                    '联系人与企业必须由同一负责人持有；请同时选择企业负责人，或先完成受控账户重分配。',
                )
        if owner_changed:
            blockers = []
            if self.contact.primary_opportunities.exclude(
                owner_user_id=owner_id
            ).exists():
                blockers.append('销售机会')
            if self.contact.lead_conversions.exclude(
                submission__assignee_id=owner_id
            ).exists():
                blockers.append('原始线索')
            if blockers:
                labels = '、'.join(blockers)
                self.add_error(
                    'owner_user',
                    f'该联系人仍有关联的{labels}由其他负责人持有。请先通过受控账户重分配流程统一负责人。',
                )
        return cleaned

    def save(self) -> Contact:
        editable = (
            'full_name', 'job_title', 'email', 'phone', 'whatsapp_phone', 'country',
            'preferred_language', 'status', 'company', 'owner_user', 'notes',
        )
        update_fields = []
        for name in editable:
            if name in {'company', 'owner_user'} and self.is_bound and name not in self.data:
                continue
            value = self.cleaned_data[name]
            setattr(self.contact, name, value)
            update_fields.append(name)
        self.contact.email_normalized = self.contact.email.strip().casefold()
        digits = ''.join(character for character in self.contact.phone if character.isdigit())
        prefix = '+' if self.contact.phone.strip().startswith('+') else ''
        self.contact.phone_normalized = f'{prefix}{digits}' if digits else ''
        self.contact.save(
            update_fields=[
                *update_fields,
                'email_normalized',
                'phone_normalized',
                'updated_at',
            ]
        )
        return self.contact


class TaskEditorForm(forms.Form):
    TASK_TYPES = [
        ('follow_up', '跟进'),
        ('call', '电话'),
        ('email', '邮件'),
        ('whatsapp', 'WhatsApp'),
        ('meeting', '会议'),
        ('quote', '报价'),
        ('review', '内部评审'),
        ('other', '其他'),
    ]
    PRIORITIES = [
        ('normal', '普通'),
        ('high', '高'),
        ('urgent', '紧急'),
        ('low', '低'),
    ]

    title = forms.CharField(label='任务', max_length=240)
    task_type = forms.ChoiceField(label='类型', choices=TASK_TYPES)
    priority = forms.ChoiceField(label='优先级', choices=PRIORITIES)
    owner_user = forms.ModelChoiceField(label='负责人', queryset=None)
    due_at = forms.DateTimeField(
        label='到期时间',
        widget=DATETIME_WIDGET,
        input_formats=['%Y-%m-%dT%H:%M'],
    )
    reminder_at = forms.DateTimeField(
        label='提醒时间',
        required=False,
        widget=DATETIME_WIDGET,
        input_formats=['%Y-%m-%dT%H:%M'],
    )
    description = forms.CharField(
        label='说明',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )

    def __init__(self, *args, site, user, target_team: SalesTeam | None, **kwargs):
        self.site = site
        self.target_team = target_team
        super().__init__(*args, **kwargs)
        owners = assignee_queryset_for_user(user, site=site).filter(is_active=True)
        if (
            target_team is None
            or target_team.site_id != site.pk
            or not target_team.enabled
        ):
            owners = owners.none()
        else:
            owners = owners.filter(
                sales_team_memberships__team_id=target_team.pk,
                sales_team_memberships__team__enabled=True,
            ).distinct()
        self.fields['owner_user'].queryset = owners
        if not self.is_bound and user.is_authenticated:
            self.initial['owner_user'] = user.pk

    def clean_owner_user(self):
        owner = self.cleaned_data['owner_user']
        if (
            not owner.is_active
            or self.target_team is None
            or self.target_team.site_id != self.site.pk
            or not SalesTeamMember.objects.filter(
                user=owner,
                team=self.target_team,
                team__site=self.site,
                team__enabled=True,
            ).exists()
        ):
            raise ValidationError('任务负责人必须是关联对象所在销售团队的有效成员。')
        return owner

    def clean(self):
        cleaned = super().clean()
        due_at = cleaned.get('due_at')
        reminder_at = cleaned.get('reminder_at')
        if reminder_at and due_at and reminder_at > due_at:
            self.add_error('reminder_at', '提醒时间不能晚于到期时间。')
        return cleaned


class OpportunityEditorForm(forms.Form):
    name = forms.CharField(label='项目名称', max_length=240)
    owner_user = forms.ModelChoiceField(label='负责人', queryset=None, required=False)
    value_amount = forms.DecimalField(
        label='预计金额',
        max_digits=14,
        decimal_places=2,
        min_value=0,
        required=False,
    )
    currency = forms.CharField(label='币种', min_length=3, max_length=3, initial='USD')
    probability = forms.IntegerField(label='成交概率', min_value=0, max_value=100)
    expected_close_date = forms.DateField(
        label='预计成交日期',
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    product_scope = forms.CharField(label='产品范围', required=False)
    capacity_target = forms.CharField(label='产能目标', required=False)
    packaging_format = forms.CharField(label='包装形式', required=False)
    next_step = forms.CharField(label='下一步', required=False)
    next_follow_up_at = forms.DateTimeField(
        label='下次跟进时间',
        required=False,
        widget=DATETIME_WIDGET,
        input_formats=['%Y-%m-%dT%H:%M'],
    )

    def __init__(self, *args, site, user, opportunity: Opportunity, **kwargs):
        self.site = site
        self.user = user
        self.opportunity = opportunity
        super().__init__(*args, **kwargs)
        assignable = assignee_queryset_for_user(user, site=site)
        if opportunity.team_id:
            assignable = assignable.filter(
                sales_team_memberships__team_id=opportunity.team_id,
                sales_team_memberships__team__enabled=True,
            ).distinct()
        else:
            assignable = assignable.none()
        assignable_ids = list(assignable.values_list('id', flat=True))
        if opportunity.owner_user_id and opportunity.owner_user_id not in assignable_ids:
            # Keep a disabled or historical current owner selectable only as
            # the unchanged value. This lets an authorized manager update an
            # unrelated field without silently clearing or reassigning it.
            assignable_ids.append(opportunity.owner_user_id)
        self.fields['owner_user'].queryset = get_user_model().objects.filter(
            id__in=assignable_ids,
        ).order_by('username')
        if not self.is_bound:
            for name in self.fields:
                self.initial[name] = getattr(opportunity, name)

    def clean_currency(self) -> str:
        value = (self.cleaned_data['currency'] or '').strip().upper()
        if not value.isalpha() or len(value) != 3:
            raise ValidationError('币种必须使用三个英文字母，例如 USD。')
        return value

    def clean_owner_user(self):
        owner = self.cleaned_data.get('owner_user')
        if owner is None:
            if not user_role_keys(self.user).intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN}):
                raise ValidationError('销售不能把自己的项目改为未分配。')
            return None
        if owner.pk == self.opportunity.owner_user_id:
            return owner
        if (
            not owner.is_active
            or self.opportunity.team_id is None
            or not SalesTeamMember.objects.filter(
                user=owner,
                team_id=self.opportunity.team_id,
                team__site=self.site,
                team__enabled=True,
            ).exists()
        ):
            raise ValidationError('负责人必须是项目所在销售团队的有效成员。')
        return owner

    def save(self) -> Opportunity:
        for name, value in self.cleaned_data.items():
            setattr(self.opportunity, name, value)
        update_fields = [*self.cleaned_data.keys(), 'updated_at']
        self.opportunity.save(update_fields=update_fields)
        return self.opportunity


class OpportunityStageForm(forms.Form):
    stage = forms.ChoiceField(label='销售阶段', choices=PIPELINE_STAGES)
    reason = forms.CharField(
        label='变更说明',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('stage') in {'won', 'lost'} and not (cleaned.get('reason') or '').strip():
            self.add_error('reason', '成交或丢单时必须记录原因。')
        return cleaned


class SavedViewForm(forms.Form):
    SCOPES = [
        ('leads', '线索'),
        ('companies', '企业'),
        ('contacts', '联系人'),
        ('opportunities', '销售管道'),
        ('tasks', '任务'),
    ]

    scope = forms.ChoiceField(label='视图范围', choices=SCOPES)
    name = forms.CharField(label='视图名称', max_length=120)
    filters_json = forms.CharField(widget=forms.HiddenInput, initial='{}')
    columns_json = forms.CharField(widget=forms.HiddenInput, initial='[]', required=False)
    sort_json = forms.CharField(widget=forms.HiddenInput, initial='[]', required=False)
    is_default = forms.BooleanField(label='设为默认视图', required=False)
    is_shared = forms.BooleanField(label='团队共享', required=False)
    team = forms.ModelChoiceField(label='共享团队', queryset=None, required=False)

    def __init__(self, *args, site, user, **kwargs):
        self.site = site
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields['team'].queryset = shareable_sales_team_queryset_for_user(
            user,
            site=site,
        )

    def _clean_json(self, field: str, expected_type):
        raw = self.cleaned_data.get(field) or ('{}' if expected_type is dict else '[]')
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError('保存的筛选条件格式无效。') from exc
        if not isinstance(value, expected_type):
            raise ValidationError('保存的筛选条件类型无效。')
        return value

    def clean_filters_json(self):
        return self._clean_json('filters_json', dict)

    def clean_columns_json(self):
        return self._clean_json('columns_json', list)

    def clean_sort_json(self):
        return self._clean_json('sort_json', list)

    def clean_is_shared(self) -> bool:
        value = bool(self.cleaned_data.get('is_shared'))
        if value and not user_role_keys(self.user).intersection({ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN}):
            raise ValidationError('只有销售经理或系统管理员可以创建团队共享视图。')
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('is_shared') and cleaned.get('team') is None:
            self.add_error('team', '团队共享视图必须选择共享团队。')
        if not cleaned.get('is_shared'):
            cleaned['team'] = None
        return cleaned

    @transaction.atomic
    def save(self) -> SavedView:
        # Lock a stable parent row rather than the (possibly empty) set of
        # existing defaults.  This serializes first-time concurrent default
        # saves for one user/site/scope and protects PostgreSQL's partial unique
        # index from the clear-then-create race.
        get_user_model().objects.select_for_update().only('pk').get(pk=self.user.pk)
        if self.cleaned_data['is_default']:
            SavedView.objects.filter(
                site=self.site,
                user=self.user,
                scope=self.cleaned_data['scope'],
                is_default=True,
            ).update(is_default=False)
        saved_view, _created = SavedView.objects.update_or_create(
            site=self.site,
            user=self.user,
            scope=self.cleaned_data['scope'],
            name=self.cleaned_data['name'].strip(),
            defaults={
                'filters_json': self.cleaned_data['filters_json'],
                'columns_json': self.cleaned_data['columns_json'],
                'sort_json': self.cleaned_data['sort_json'],
                'is_default': self.cleaned_data['is_default'],
                'is_shared': self.cleaned_data['is_shared'],
                'team': self.cleaned_data['team'],
            },
        )
        return saved_view
