from __future__ import annotations

import json
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from leads.models import LeadFormDefinition, LeadSubmission

from .acquisition_versions import lead_form_version_token
from .access import assignee_queryset_for_user
from sitecore.models import Site, SiteLocale


FORM_STATUS_CHOICES = [
    ('active', '启用'),
    ('disabled', '停用'),
    ('archived', '归档'),
]

CHANNEL_CHOICES = [
    ('website', '网站表单'),
    ('whatsapp', 'WhatsApp'),
    ('messenger', 'Messenger'),
    ('phone', '电话'),
    ('email', '邮箱'),
    ('custom', '其他'),
]

SCOPE_TYPE_CHOICES = [
    ('site', '全站'),
    ('route', '指定路由'),
    ('cta', '指定 CTA'),
    ('page_type', '页面类型'),
    ('custom', '自定义'),
]

LEAD_STAGE_CHOICES = [
    ('new', '新提交'),
    ('contacted', '已联系'),
    ('qualified', '高质量线索'),
    ('won', '已成交'),
    ('lost', '已流失'),
    ('spam', '垃圾线索'),
]

SOURCE_CHANNEL_CHOICES = [
    ('meta_ads', 'Meta 广告'),
    ('paid_search', '付费搜索'),
    ('organic', '自然搜索'),
    ('referral', '外部推荐'),
    ('direct', '直接访问'),
    ('website', '网站其他入口'),
    ('whatsapp', 'WhatsApp'),
    ('email', '邮件'),
    ('other', '其他'),
]

FIELD_LIBRARY = [
    {
        'name': 'full_name',
        'type': 'text',
        'default_label': '姓名',
        'default_placeholder': '请输入姓名',
        'default_enabled': True,
        'default_required': True,
    },
    {
        'name': 'email',
        'type': 'email',
        'default_label': '邮箱',
        'default_placeholder': '请输入邮箱',
        'default_enabled': True,
        'default_required': False,
    },
    {
        'name': 'phone',
        'type': 'tel',
        'default_label': '电话',
        'default_placeholder': '请输入电话',
        'default_enabled': True,
        'default_required': False,
    },
    {
        'name': 'company',
        'type': 'text',
        'default_label': '公司',
        'default_placeholder': '请输入公司名称',
        'default_enabled': True,
        'default_required': True,
    },
    {
        'name': 'country',
        'type': 'text',
        'default_label': '国家',
        'default_placeholder': '请输入国家',
        'default_enabled': True,
        'default_required': True,
    },
    {
        'name': 'product_category',
        'type': 'select',
        'default_label': '产品类别',
        'default_placeholder': '请选择产品类别',
        'default_enabled': True,
        'default_required': True,
    },
    {
        'name': 'capacity',
        'type': 'text',
        'default_label': '产能目标',
        'default_placeholder': '例如 12,000 cans/hour',
        'default_enabled': True,
        'default_required': True,
    },
    {
        'name': 'message',
        'type': 'textarea',
        'default_label': '项目需求',
        'default_placeholder': '请输入项目需求',
        'default_enabled': True,
        'default_required': False,
    },
]


def default_form_schema() -> dict:
    return {
        'fields': [
            {
                'name': item['name'],
                'type': item['type'],
                'label': item['default_label'],
                'placeholder': item['default_placeholder'],
                'enabled': item['default_enabled'],
                'required': item['default_required'],
            }
            for item in FIELD_LIBRARY
        ]
    }


class LeadFormEditorForm(forms.Form):
    version = forms.CharField(required=False, max_length=256, widget=forms.HiddenInput)
    code = forms.CharField(label='表单代码', max_length=80)
    name = forms.CharField(label='表单名称', max_length=200)
    category = forms.CharField(label='业务分类', max_length=80, initial='general')
    locale = forms.ModelChoiceField(label='语言', queryset=SiteLocale.objects.none(), required=False, empty_label='全站共享')
    channel = forms.ChoiceField(label='收集渠道', choices=CHANNEL_CHOICES)
    scope_type = forms.ChoiceField(label='作用域类型', choices=SCOPE_TYPE_CHOICES)
    scope_value = forms.CharField(label='作用域值', max_length=255, initial='*')
    status = forms.ChoiceField(label='状态', choices=FORM_STATUS_CHOICES)
    capi_enabled = forms.BooleanField(label='启用 Meta CAPI', required=False, initial=True)
    submission_event_name = forms.CharField(label='提交事件名', max_length=80, initial='Lead')
    contacted_event_name = forms.CharField(label='已联系事件名', max_length=80, initial='contacted_lead')
    qualified_event_name = forms.CharField(label='合格事件名', max_length=80, initial='qualified_lead')
    won_event_name = forms.CharField(label='成交事件名', max_length=80, initial='converted')
    notify_emails = forms.CharField(label='通知邮箱', required=False, widget=forms.Textarea(attrs={'rows': 2}))
    success_message = forms.CharField(label='成功提示', required=False, widget=forms.Textarea(attrs={'rows': 3}))
    config_json = forms.CharField(
        label='高级配置 JSON',
        required=False,
        widget=forms.Textarea(attrs={'rows': 10}),
        help_text='例如 meta.test_event_code、风控开关、后续 webhook 规则等。',
    )

    def __init__(self, *args, site: Site, form_definition: LeadFormDefinition | None = None, **kwargs):
        self.site = site
        self.form_definition = form_definition
        super().__init__(*args, **kwargs)
        self.fields['locale'].queryset = SiteLocale.objects.filter(site=site, enabled=True).order_by('sort_order', 'locale_code')
        self.code_locked = bool(
            form_definition
            and LeadSubmission.objects.filter(form_id=form_definition.id).exists()
        )
        if self.code_locked:
            self.fields['code'].disabled = True
            self.fields['code'].help_text = '该表单已经接收过提交，公开地址已锁定。如需新地址，请新建表单。'
            self.initial['code'] = form_definition.code
        self.field_specs = self._resolved_field_specs()
        self._install_field_editor_fields()
        if form_definition and not self.is_bound:
            self.initial.update(
                {
                    'version': lead_form_version_token(form_definition),
                    'code': form_definition.code,
                    'name': form_definition.name,
                    'category': form_definition.category,
                    'locale': form_definition.locale_id,
                    'channel': form_definition.channel,
                    'scope_type': form_definition.scope_type,
                    'scope_value': form_definition.scope_value,
                    'status': form_definition.status,
                    'capi_enabled': form_definition.capi_enabled,
                    'submission_event_name': form_definition.submission_event_name,
                    'contacted_event_name': form_definition.contacted_event_name,
                    'qualified_event_name': form_definition.qualified_event_name,
                    'won_event_name': form_definition.won_event_name,
                    'notify_emails': form_definition.notify_emails,
                    'success_message': form_definition.success_message,
                    'config_json': json.dumps(form_definition.config_json or {}, ensure_ascii=False, indent=2),
                }
            )

    def _resolved_field_specs(self) -> list[dict]:
        schema = (self.form_definition.form_schema if self.form_definition else None) or default_form_schema()
        configured = {
            str(item.get('name') or item.get('key') or '').strip(): item
            for item in (schema.get('fields') or [])
            if isinstance(item, dict) and str(item.get('name') or item.get('key') or '').strip()
        }
        specs: list[dict] = []
        for item in FIELD_LIBRARY:
            current = configured.get(item['name'], {})
            specs.append(
                {
                    'name': item['name'],
                    'type': item['type'],
                    'default_label': item['default_label'],
                    'enabled': bool(current.get('enabled', item['default_enabled'] if 'enabled' in current else True)),
                    'required': bool(current.get('required', item['default_required'])),
                    'label': str(current.get('label') or item['default_label']),
                    'placeholder': str(current.get('placeholder') or item['default_placeholder']),
                }
            )
        return specs

    def _install_field_editor_fields(self):
        for spec in self.field_specs:
            name = spec['name']
            self.fields[f'field_{name}_enabled'] = forms.BooleanField(label='启用', required=False)
            self.fields[f'field_{name}_required'] = forms.BooleanField(label='必填', required=False)
            self.fields[f'field_{name}_label'] = forms.CharField(label='字段标题', max_length=120, required=False)
            self.fields[f'field_{name}_placeholder'] = forms.CharField(label='占位提示', max_length=200, required=False)
            if not self.is_bound:
                self.initial[f'field_{name}_enabled'] = spec['enabled']
                self.initial[f'field_{name}_required'] = spec['required']
                self.initial[f'field_{name}_label'] = spec['label']
                self.initial[f'field_{name}_placeholder'] = spec['placeholder']

    def field_rows(self) -> list[dict]:
        rows = []
        for spec in self.field_specs:
            name = spec['name']
            rows.append(
                {
                    'name': name,
                    'type': spec['type'],
                    'default_label': spec['default_label'],
                    'enabled_field': self[f'field_{name}_enabled'],
                    'required_field': self[f'field_{name}_required'],
                    'label_field': self[f'field_{name}_label'],
                    'placeholder_field': self[f'field_{name}_placeholder'],
                }
            )
        return rows

    def clean_code(self):
        code = (self.cleaned_data['code'] or '').strip().lower().replace('-', '_')
        if not code:
            raise forms.ValidationError('表单代码不能为空。')
        queryset = LeadFormDefinition.objects.filter(site=self.site, code=code)
        if self.form_definition:
            queryset = queryset.exclude(id=self.form_definition.id)
        if queryset.exists():
            raise forms.ValidationError('该表单代码已存在。')
        return code

    def _clean_json_field(self, field_name: str) -> dict:
        raw = (self.cleaned_data.get(field_name) or '').strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f'JSON 格式错误：{exc.msg}') from exc
        if not isinstance(value, dict):
            raise forms.ValidationError('这里只接受 JSON 对象。')
        return value

    def clean_config_json(self):
        return self._clean_json_field('config_json')

    def cleaned_form_schema(self) -> dict:
        fields = []
        enabled_names = set()
        required_names = set()
        for spec in self.field_specs:
            name = spec['name']
            enabled = bool(self.cleaned_data.get(f'field_{name}_enabled'))
            required = bool(self.cleaned_data.get(f'field_{name}_required'))
            label = (self.cleaned_data.get(f'field_{name}_label') or spec['default_label']).strip() or spec['default_label']
            placeholder = (self.cleaned_data.get(f'field_{name}_placeholder') or '').strip()
            if enabled:
                enabled_names.add(name)
            if required:
                required_names.add(name)
            fields.append(
                {
                    'name': name,
                    'type': spec['type'],
                    'label': label,
                    'placeholder': placeholder,
                    'enabled': enabled,
                    'required': required,
                }
            )

        if 'full_name' not in enabled_names:
            raise forms.ValidationError('姓名字段必须启用。')
        if 'email' not in enabled_names and 'phone' not in enabled_names:
            raise forms.ValidationError('邮箱和电话至少要启用一个。')
        return {'fields': fields}

    def save(self) -> LeadFormDefinition:
        payload = {
            'site': self.site,
            'locale': self.cleaned_data['locale'],
            'code': self.cleaned_data['code'],
            'name': self.cleaned_data['name'].strip(),
            'category': self.cleaned_data['category'].strip() or 'general',
            'channel': self.cleaned_data['channel'],
            'scope_type': self.cleaned_data['scope_type'],
            'scope_value': self.cleaned_data['scope_value'].strip() or '*',
            'status': self.cleaned_data['status'],
            'capi_enabled': self.cleaned_data['capi_enabled'],
            'submission_event_name': self.cleaned_data['submission_event_name'].strip() or 'Lead',
            'contacted_event_name': self.cleaned_data['contacted_event_name'].strip() or 'contacted_lead',
            'qualified_event_name': self.cleaned_data['qualified_event_name'].strip() or 'qualified_lead',
            'won_event_name': self.cleaned_data['won_event_name'].strip() or 'converted',
            'notify_emails': self.cleaned_data['notify_emails'].strip(),
            'success_message': self.cleaned_data['success_message'].strip(),
            'form_schema': self.cleaned_form_schema(),
            'config_json': self.cleaned_data['config_json'],
        }
        if self.form_definition:
            for key, value in payload.items():
                setattr(self.form_definition, key, value)
            self.form_definition.save()
            return self.form_definition
        return LeadFormDefinition.objects.create(**payload)


class LeadSubmissionEditorForm(forms.Form):
    stage = forms.ChoiceField(label='线索阶段', choices=LEAD_STAGE_CHOICES)
    assignee = forms.ModelChoiceField(
        label='负责人',
        queryset=get_user_model().objects.none(),
        required=False,
        empty_label='暂未分配',
    )
    source_channel = forms.ChoiceField(label='来源渠道', choices=SOURCE_CHANNEL_CHOICES)
    source_detail = forms.CharField(label='来源说明', required=False, max_length=500)
    next_follow_up_at = forms.DateTimeField(
        label='下次跟进时间',
        required=False,
        input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
    )
    follow_up_notes = forms.CharField(
        label='跟进备注',
        required=False,
        max_length=8000,
        widget=forms.Textarea(attrs={'rows': 8, 'placeholder': '记录联系结果、客户关注点、待补资料和下一步。'}),
    )
    follow_up_entry = forms.CharField(
        label='新增跟进记录',
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': '填写本次联系结果。保存后会追加到历史记录，不会覆盖之前内容。'}),
    )
    qualification_contactable = forms.BooleanField(label='联系人真实可达', required=False)
    qualification_company = forms.BooleanField(label='企业身份已核验', required=False)
    qualification_project = forms.BooleanField(label='项目需求真实明确', required=False)
    qualification_fit = forms.BooleanField(label='设备与技术范围匹配', required=False)
    qualification_next_step = forms.BooleanField(label='已有明确下一步', required=False)
    qualification_overridden = forms.BooleanField(label='人工覆盖评分', required=False)
    qualification_notes = forms.CharField(
        label='合格判定说明',
        required=False,
        max_length=4000,
        widget=forms.Textarea(attrs={'rows': 5, 'placeholder': '记录核验依据、缺失信息或人工覆盖原因。'}),
    )
    buyer_value = forms.DecimalField(label='成交金额', required=False, min_value=Decimal('0'), decimal_places=2, max_digits=12)
    buyer_currency = forms.CharField(label='币种', required=False, max_length=8, initial='USD')

    def __init__(self, *args, submission: LeadSubmission, actor=None, **kwargs):
        self.submission = submission
        self.actor = actor
        super().__init__(*args, **kwargs)
        if actor is None:
            self.fields['assignee'].queryset = get_user_model().objects.none()
        else:
            self.fields['assignee'].queryset = assignee_queryset_for_user(
                actor,
                site=submission.site,
            )
        if not self.is_bound:
            qualification = submission.qualification_json or {}
            self.initial.update(
                {
                    'stage': submission.stage,
                    'assignee': submission.assignee_id,
                    'source_channel': submission.source_channel or 'website',
                    'source_detail': submission.source_detail,
                    'next_follow_up_at': submission.next_follow_up_at,
                    'follow_up_notes': submission.follow_up_notes,
                    'qualification_contactable': bool(qualification.get('contactable')),
                    'qualification_company': bool(qualification.get('company_verified')),
                    'qualification_project': bool(qualification.get('project_confirmed')),
                    'qualification_fit': bool(qualification.get('technical_fit')),
                    'qualification_next_step': bool(qualification.get('next_step_confirmed')),
                    'qualification_overridden': submission.qualification_overridden,
                    'qualification_notes': submission.qualification_notes,
                    'buyer_value': submission.buyer_value,
                    'buyer_currency': submission.buyer_currency or 'USD',
                }
            )

    def clean_buyer_currency(self):
        return (self.cleaned_data.get('buyer_currency') or 'USD').strip().upper() or 'USD'

    def clean(self):
        cleaned = super().clean()
        score = 20 * sum(
            bool(cleaned.get(field_name))
            for field_name in (
                'qualification_contactable',
                'qualification_company',
                'qualification_project',
                'qualification_fit',
                'qualification_next_step',
            )
        )
        overridden = bool(cleaned.get('qualification_overridden'))
        notes = (cleaned.get('qualification_notes') or '').strip()
        if cleaned.get('stage') in {'qualified', 'won'} and score < 80 and not overridden:
            raise forms.ValidationError('高质量线索至少需要满足五项判定中的四项；特殊情况请勾选人工覆盖并说明原因。')
        if overridden and not notes:
            self.add_error('qualification_notes', '人工覆盖评分时必须填写原因。')
        self.qualification_score = score
        return cleaned

    def cleaned_qualification(self) -> dict:
        return {
            'contactable': bool(self.cleaned_data.get('qualification_contactable')),
            'company_verified': bool(self.cleaned_data.get('qualification_company')),
            'project_confirmed': bool(self.cleaned_data.get('qualification_project')),
            'technical_fit': bool(self.cleaned_data.get('qualification_fit')),
            'next_step_confirmed': bool(self.cleaned_data.get('qualification_next_step')),
        }

    def save_operations(self, *, actor=None) -> LeadSubmission:
        actor = actor or self.actor
        self.submission.assignee = self.cleaned_data['assignee']
        self.submission.source_channel = self.cleaned_data['source_channel']
        self.submission.source_detail = (self.cleaned_data.get('source_detail') or '').strip()
        self.submission.next_follow_up_at = self.cleaned_data.get('next_follow_up_at')
        self.submission.follow_up_notes = (self.cleaned_data.get('follow_up_notes') or '').strip()
        self.submission.qualification_score = self.qualification_score
        self.submission.qualification_json = self.cleaned_qualification()
        self.submission.qualification_notes = (self.cleaned_data.get('qualification_notes') or '').strip()
        self.submission.qualification_overridden = bool(self.cleaned_data.get('qualification_overridden'))
        follow_up_entry = (self.cleaned_data.get('follow_up_entry') or '').strip()
        config = dict(self.submission.config_json or {})
        if follow_up_entry:
            now = timezone.now()
            history = list(config.get('follow_up_history') or [])
            history.append(
                {
                    'created_at': now.isoformat(),
                    'created_at_label': timezone.localtime(now).strftime('%Y-%m-%d %H:%M'),
                    'actor': actor.get_username() if actor and hasattr(actor, 'get_username') else '',
                    'stage': self.cleaned_data['stage'],
                    'note': follow_up_entry,
                }
            )
            config['follow_up_history'] = history[-100:]
        self.submission.config_json = config
        self.submission.save(
            update_fields=[
                'assignee',
                'source_channel',
                'source_detail',
                'next_follow_up_at',
                'follow_up_notes',
                'qualification_score',
                'qualification_json',
                'qualification_notes',
                'qualification_overridden',
                'config_json',
                'updated_at',
            ]
        )
        return self.submission
