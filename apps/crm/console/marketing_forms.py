from __future__ import annotations

import json
import os
import re

from django import forms

from marketing.models import MarketingIntegration
from marketing.public_config import normalize_google_tag_id
from .secret_store import delete_secret, load_secret, store_secret


RESERVED_CONFIG_KEYS = {
    'api_version',
    'test_event_code',
    'form_code',
    'leadgen_form_ids',
    'waba_id',
    'page_id',
    'ctwa_identity_mode',
    'transport_provider',
    'business_phone_e164',
    'ycloud_webhook_endpoint_id',
    'ycloud_webhook_secret_ref',
    'app_secret_ref',
    'webhook_verify_token_ref',
    'contact_consent_confirmed',
    'marketing_consent_confirmed',
    'queue_initial_crm_event',
    'credentials_mode',
    'login_account_id',
    'qualified_conversion_action_id',
    'converted_conversion_action_id',
    'validate_only',
    'google_tag_id',
    'google_tag_enabled',
    'last_diagnostic',
}
FORBIDDEN_SECRET_KEYS = {
    'access_token', 'api_key', 'app_secret', 'verify_token', 'webhook_secret',
    'credentials_json', 'client_secret',
}
META_GRAPH_API_DEFAULT = 'v26.0'


class MarketingIntegrationEditorForm(forms.Form):
    name = forms.CharField(label='接入名称', max_length=200)
    enabled = forms.BooleanField(label='启用接入', required=False)
    public_id = forms.CharField(label='公开 ID', max_length=255)
    consent_category = forms.CharField(label='同意分类', max_length=64, initial='marketing')
    secret_ref = forms.CharField(
        label='环境变量名 / 密钥引用名',
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={
                'autocomplete': 'off',
                'autocapitalize': 'none',
                'spellcheck': 'false',
            }
        ),
        help_text='高级部署可使用环境变量；后台录入的密钥会加密保存并自动生成引用。',
    )
    primary_secret = forms.CharField(
        label='主密钥',
        required=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={'autocomplete': 'new-password', 'placeholder': '留空表示保持现状'},
        ),
        help_text='后台输入后会加密存储，业务表和页面不会保存明文。',
    )
    clear_stored_secret = forms.BooleanField(label='清除已保存主密钥', required=False)
    app_secret = forms.CharField(
        label='Meta App Secret',
        required=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={'autocomplete': 'new-password', 'placeholder': '留空表示保持现状'},
        ),
    )
    webhook_verify_token = forms.CharField(
        label='Webhook Verify Token',
        required=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                'autocomplete': 'new-password',
                'placeholder': '由你自定义，并在 Meta 后台填写同一个值',
            },
        ),
    )
    clear_webhook_secrets = forms.BooleanField(label='清除 Webhook 密钥', required=False)
    api_version = forms.CharField(label='Graph API 版本', max_length=32, required=False)
    test_event_code = forms.CharField(
        label='测试事件码',
        max_length=120,
        required=False,
        help_text='仅用于 Meta Events Manager 测试事件；正式发送时留空。',
    )
    form_code = forms.CharField(label='关联 CRM 表单代码', max_length=80, required=False)
    leadgen_form_ids = forms.CharField(
        label='允许接收的 Meta Instant Form ID',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '每行一个 Form ID'}),
        help_text='只接收这里明确列出的表单；可用换行、逗号或分号分隔。',
    )
    waba_id = forms.CharField(
        label='WhatsApp Business Account ID',
        max_length=255,
        required=False,
        help_text='用于校验 WhatsApp Cloud API 资产和 Webhook 来源。',
    )
    page_id = forms.CharField(
        label='CTWA Meta Page ID（旧版兼容）',
        max_length=255,
        required=False,
        help_text='仅当目标 Meta 数据源明确要求 Page 身份模式时填写。默认使用 WABA ID。',
    )
    ctwa_identity_mode = forms.ChoiceField(
        label='CTWA 回传身份模式',
        required=False,
        choices=(
            ('waba', 'WhatsApp Business Account ID（默认）'),
            ('page', 'Facebook Page ID（旧版兼容）'),
        ),
        help_text='按目标 Meta 测试事件合同验收；销售人员无需接触此设置。',
    )
    transport_provider = forms.ChoiceField(
        label='WhatsApp 官方传输方式',
        required=False,
        choices=(
            ('meta', 'Meta Cloud API 直连'),
            ('ycloud', 'YCloud 官方 Coexistence'),
        ),
        help_text='8155 优先采用可验证的官方 Coexistence；切换不会自动迁移或发送消息。',
    )
    business_phone_e164 = forms.CharField(
        label='业务号码（E.164）',
        max_length=32,
        required=False,
        help_text='例如 +15592028155。Phone Number ID 仍填写在上方公开 ID 中。',
    )
    ycloud_webhook_endpoint_id = forms.CharField(
        label='YCloud Webhook Endpoint ID',
        max_length=255,
        required=False,
        help_text='与 YCloud-Signature 一起唯一绑定回调端点。',
    )
    ycloud_webhook_secret = forms.CharField(
        label='YCloud Webhook Signing Secret',
        required=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={'autocomplete': 'new-password', 'placeholder': '留空表示保持现状'},
        ),
    )
    google_tag_id = forms.CharField(
        label='Google Tag ID',
        max_length=64,
        required=False,
        help_text='Google Ads 通常为 AW- 开头；也支持 GT- 开头的 Google tag。',
    )
    google_tag_enabled = forms.BooleanField(
        label='在访客同意后启用 Google 浏览器标签',
        required=False,
        help_text='开启后，网站从公开配置接口读取 Tag ID；拒绝隐私测量时不会加载 Google。',
    )
    contact_consent_confirmed = forms.BooleanField(label='已确认表单/会话允许业务联系', required=False)
    marketing_consent_confirmed = forms.BooleanField(label='已确认允许用于广告测量与优化', required=False)
    queue_initial_crm_event = forms.BooleanField(
        label='接收 Meta Instant Form 后回传初始 CRM Lead',
        required=False,
        help_text='Conversion Leads 验证至少需要初始 Meta 线索事件和目标 CRM 事件。',
    )
    credentials_mode = forms.ChoiceField(
        label='Google 凭据模式',
        required=False,
        choices=(
            ('adc', 'Application Default Credentials (推荐)'),
            ('authorized_user', 'OAuth Authorized User JSON'),
            ('service_account', 'Service Account JSON'),
        ),
    )
    login_account_id = forms.CharField(label='Google Ads Manager ID（可选）', max_length=64, required=False)
    qualified_conversion_action_id = forms.CharField(label='Qualified Lead Conversion Action ID', max_length=128, required=False)
    converted_conversion_action_id = forms.CharField(label='Converted Lead Conversion Action ID', max_length=128, required=False)
    validate_only = forms.BooleanField(
        label='仅验证，不正式写入 Google Ads',
        required=False,
        help_text='首次接通时建议开启；验证通过后关闭。',
    )
    config_json = forms.CharField(
        label='高级配置 JSON',
        required=False,
        widget=forms.Textarea(attrs={'rows': 8, 'placeholder': '{"notes":"..."}'}),
        help_text='仅填写非密钥扩展参数，密钥必须使用上方安全字段。',
    )

    def __init__(self, *args, integration: MarketingIntegration, updated_by: str = 'django-console', **kwargs):
        super().__init__(*args, **kwargs)
        self.integration = integration
        self.updated_by = updated_by
        self.provider_code = integration.provider.code
        self.integration_type = integration.integration_type
        self.is_meta_pixel = self.provider_code == 'meta' and self.integration_type == 'pixel'
        self.is_meta_leadgen = self.provider_code == 'meta' and self.integration_type == 'leadgen'
        self.is_whatsapp = self.provider_code == 'whatsapp' and self.integration_type == 'cloud_api'
        self.is_google = self.provider_code == 'google' and self.integration_type == 'data_manager'
        self.is_webhook = self.is_meta_leadgen or self.is_whatsapp
        config = dict(integration.config_json or {})
        raw_leadgen_form_ids = config.get('leadgen_form_ids') or []
        if isinstance(raw_leadgen_form_ids, str):
            raw_leadgen_form_ids = re.split(r'[\s,;]+', raw_leadgen_form_ids)
        if self.is_meta_pixel:
            self.fields['public_id'].label = 'Meta Pixel ID'
            self.fields['primary_secret'].label = 'Meta CAPI Access Token'
        elif self.is_meta_leadgen:
            self.fields['public_id'].label = 'Meta Page ID'
            self.fields['primary_secret'].label = 'Meta Page Access Token'
        elif self.is_whatsapp:
            self.fields['public_id'].label = 'WhatsApp Phone Number ID'
            self.fields['primary_secret'].label = 'WhatsApp 主凭据（Meta Token / YCloud API Key）'
        elif self.is_google:
            self.fields['public_id'].label = 'Google Ads Customer ID'
            self.fields['primary_secret'].label = 'Google OAuth / Service Account JSON'
        if not self.is_bound:
            self.initial.update(
                {
                    'name': integration.name,
                    'enabled': integration.enabled,
                    'public_id': integration.public_id,
                    'secret_ref': integration.secret_ref or '',
                    'consent_category': integration.consent_category,
                    'api_version': str(config.get('api_version') or (META_GRAPH_API_DEFAULT if self.provider_code in {'meta', 'whatsapp'} else '')),
                    'test_event_code': str(config.get('test_event_code') or ''),
                    'form_code': str(config.get('form_code') or 'project-inquiry'),
                    'leadgen_form_ids': '\n'.join(
                        str(value).strip()
                        for value in raw_leadgen_form_ids
                        if str(value).strip()
                    ),
                    'waba_id': str(config.get('waba_id') or ''),
                    'page_id': str(config.get('page_id') or ''),
                    'ctwa_identity_mode': str(config.get('ctwa_identity_mode') or 'waba'),
                    'transport_provider': str(config.get('transport_provider') or 'meta'),
                    'business_phone_e164': str(config.get('business_phone_e164') or ''),
                    'ycloud_webhook_endpoint_id': str(config.get('ycloud_webhook_endpoint_id') or ''),
                    'google_tag_id': str(config.get('google_tag_id') or ''),
                    'google_tag_enabled': bool(config.get('google_tag_enabled')),
                    'contact_consent_confirmed': bool(config.get('contact_consent_confirmed')),
                    'marketing_consent_confirmed': bool(config.get('marketing_consent_confirmed')),
                    'queue_initial_crm_event': bool(config.get('queue_initial_crm_event', self.is_meta_leadgen)),
                    'credentials_mode': str(config.get('credentials_mode') or 'adc'),
                    'login_account_id': str(config.get('login_account_id') or ''),
                    'qualified_conversion_action_id': str(config.get('qualified_conversion_action_id') or ''),
                    'converted_conversion_action_id': str(config.get('converted_conversion_action_id') or ''),
                    'validate_only': bool(config.get('validate_only', self.is_google)),
                    'config_json': json.dumps(
                        {key: value for key, value in config.items() if key not in RESERVED_CONFIG_KEYS},
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            )

    @property
    def primary_vault_key(self) -> str:
        current = str(self.integration.secret_ref or '').strip()
        if current.startswith('vault:'):
            return current.removeprefix('vault:')
        return f'marketing_integration:{self.integration.id}:primary_secret'

    def _config_secret_ref(self, key: str) -> str:
        config = dict(self.integration.config_json or {})
        return str(config.get(f'{key}_ref') or f'vault:marketing_integration:{self.integration.id}:{key}').strip()

    @staticmethod
    def _secret_available(reference: str) -> bool:
        ref = str(reference or '').strip()
        if ref.startswith('vault:'):
            try:
                return bool(load_secret(ref.removeprefix('vault:')))
            except Exception:
                return False
        return bool(ref and os.getenv(ref, '').strip())

    def _primary_secret_available_after_save(self, cleaned: dict) -> bool:
        """Validate the credential state that ``save()`` will leave behind.

        The editor deliberately keeps existing secrets when password inputs
        are blank.  A checked clear flag is different: an existing vault value
        must no longer satisfy enabled-integration validation, including when
        the unchanged vault reference is posted back by the text input.
        """

        if str(cleaned.get('primary_secret') or '').strip():
            return True

        current_ref = str(self.integration.secret_ref or '').strip()
        submitted_ref = str(cleaned.get('secret_ref') or '').strip()
        if cleaned.get('clear_stored_secret'):
            if current_ref.startswith('vault:') and submitted_ref == current_ref:
                return False
            return self._secret_available(submitted_ref)
        return self._secret_available(submitted_ref or current_ref)

    def clean_config_json(self):
        raw = (self.cleaned_data.get('config_json') or '').strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f'高级配置 JSON 无法解析：{exc.msg}') from exc
        if not isinstance(parsed, dict):
            raise forms.ValidationError('高级配置 JSON 必须是对象。')
        forbidden = sorted(FORBIDDEN_SECRET_KEYS.intersection(parsed))
        if forbidden:
            raise forms.ValidationError(f'不要在高级配置中保存密钥字段：{", ".join(forbidden)}。')
        return parsed

    def clean(self):
        cleaned = super().clean()
        if self.is_whatsapp:
            cleaned['ctwa_identity_mode'] = str(
                cleaned.get('ctwa_identity_mode') or 'waba'
            ).strip().lower()
            cleaned['transport_provider'] = str(
                cleaned.get('transport_provider') or 'meta'
            ).strip().lower()
        if self.provider_code in {'meta', 'whatsapp'}:
            api_version = str(cleaned.get('api_version') or META_GRAPH_API_DEFAULT).strip()
            if not re.fullmatch(r'v\d+\.\d+', api_version):
                self.add_error('api_version', 'Graph API 版本格式应类似 v26.0。')
            else:
                cleaned['api_version'] = api_version
        if self.is_google and cleaned.get('google_tag_enabled'):
            normalized_tag_id = normalize_google_tag_id(cleaned.get('google_tag_id'))
            if not normalized_tag_id:
                self.add_error('google_tag_id', '请输入有效的 AW- 或 GT- Google Tag ID。')
            else:
                cleaned['google_tag_id'] = normalized_tag_id
        if not cleaned.get('enabled'):
            return cleaned
        public_id = str(cleaned.get('public_id') or '').strip()
        if not public_id or public_id.startswith('pending-'):
            self.add_error('public_id', '启用前必须填写真实平台 ID。')
        primary_available = self._primary_secret_available_after_save(cleaned)
        if self.is_meta_pixel and not primary_available:
            self.add_error('primary_secret', '启用 Meta CAPI 前必须配置 Access Token。')
        if self.is_webhook:
            clear_webhook_secrets = bool(cleaned.get('clear_webhook_secrets'))
            is_ycloud = self.is_whatsapp and cleaned.get('transport_provider') == 'ycloud'
            if not primary_available:
                self.add_error('primary_secret', '启用 Webhook 前必须配置平台 Access Token。')
            if is_ycloud:
                ycloud_secret_available = bool(str(cleaned.get('ycloud_webhook_secret') or '').strip()) or (
                    not clear_webhook_secrets
                    and self._secret_available(self._config_secret_ref('ycloud_webhook_secret'))
                )
                if not ycloud_secret_available:
                    self.add_error('ycloud_webhook_secret', '启用 YCloud Webhook 前必须配置 Signing Secret。')
                if not str(cleaned.get('ycloud_webhook_endpoint_id') or '').strip():
                    self.add_error('ycloud_webhook_endpoint_id', '启用 YCloud Webhook 前必须填写 Endpoint ID。')
            else:
                app_available = bool(str(cleaned.get('app_secret') or '').strip()) or (
                    not clear_webhook_secrets
                    and self._secret_available(self._config_secret_ref('app_secret'))
                )
                verify_available = bool(str(cleaned.get('webhook_verify_token') or '').strip()) or (
                    not clear_webhook_secrets
                    and self._secret_available(self._config_secret_ref('webhook_verify_token'))
                )
                if not app_available:
                    self.add_error('app_secret', '启用 Webhook 前必须配置 Meta App Secret。')
                if not verify_available:
                    self.add_error('webhook_verify_token', '启用 Webhook 前必须配置 Verify Token。')
            if not cleaned.get('contact_consent_confirmed'):
                self.add_error('contact_consent_confirmed', '必须先确认表单或会话中的业务联系授权。')
            if not str(cleaned.get('form_code') or '').strip():
                self.add_error('form_code', '请选择外部线索写入的 CRM 表单。')
            if self.is_meta_leadgen:
                leadgen_form_ids = []
                for value in re.split(r'[\s,;]+', str(cleaned.get('leadgen_form_ids') or '')):
                    form_id = value.strip()
                    if form_id and form_id not in leadgen_form_ids:
                        leadgen_form_ids.append(form_id)
                cleaned['leadgen_form_ids'] = leadgen_form_ids
                if not leadgen_form_ids:
                    self.add_error(
                        'leadgen_form_ids',
                        '启用 Meta Instant Form 前必须填写至少一个允许的 Form ID。',
                    )
        if self.is_whatsapp and not str(cleaned.get('waba_id') or '').strip():
            self.add_error('waba_id', 'WhatsApp Webhook 接入需要 WhatsApp Business Account ID。')
        if self.is_whatsapp and cleaned.get('transport_provider') == 'ycloud':
            phone = re.sub(r'\D', '', str(cleaned.get('business_phone_e164') or ''))
            if not 8 <= len(phone) <= 15:
                self.add_error('business_phone_e164', 'YCloud 接入必须填写有效的 E.164 业务号码。')
            else:
                cleaned['business_phone_e164'] = f'+{phone}'
        if (
            self.is_whatsapp
            and str(cleaned.get('ctwa_identity_mode') or 'waba') == 'page'
            and not str(cleaned.get('page_id') or '').strip()
        ):
            self.add_error('page_id', '选择旧版 Page 身份模式时必须填写 Meta Page ID。')
        if self.is_google:
            mode = str(cleaned.get('credentials_mode') or 'adc')
            if mode != 'adc' and not primary_available:
                self.add_error('primary_secret', '所选 Google 凭据模式需要上传凭据 JSON。')
            if not str(cleaned.get('qualified_conversion_action_id') or '').strip():
                self.add_error('qualified_conversion_action_id', '必须填写 Qualified Lead Conversion Action ID。')
            if not str(cleaned.get('converted_conversion_action_id') or '').strip():
                self.add_error('converted_conversion_action_id', '必须填写 Converted Lead Conversion Action ID。')
        return cleaned

    def _store_optional_secret(self, *, field_name: str, key_name: str, config: dict) -> None:
        plaintext = str(self.cleaned_data.get(field_name) or '').strip()
        if not plaintext:
            return
        vault_key = f'marketing_integration:{self.integration.id}:{key_name}'
        store_secret(secret_key=vault_key, plaintext=plaintext, updated_by=self.updated_by)
        config[f'{key_name}_ref'] = f'vault:{vault_key}'

    def save(self) -> MarketingIntegration:
        config = dict(self.cleaned_data['config_json'])
        existing_config = dict(self.integration.config_json or {})
        for secret_ref_key in (
            'app_secret_ref', 'webhook_verify_token_ref',
            'ycloud_webhook_secret_ref', 'last_diagnostic',
        ):
            if existing_config.get(secret_ref_key):
                config[secret_ref_key] = existing_config[secret_ref_key]
        for field_name in (
            'api_version', 'test_event_code', 'form_code', 'waba_id', 'page_id',
            'ctwa_identity_mode', 'transport_provider', 'business_phone_e164',
            'ycloud_webhook_endpoint_id', 'credentials_mode',
            'login_account_id', 'qualified_conversion_action_id', 'converted_conversion_action_id',
            'google_tag_id',
        ):
            value = str(self.cleaned_data.get(field_name) or '').strip()
            if value:
                config[field_name] = value
        if self.is_meta_leadgen:
            config['leadgen_form_ids'] = list(self.cleaned_data.get('leadgen_form_ids') or [])
        config['contact_consent_confirmed'] = bool(self.cleaned_data.get('contact_consent_confirmed'))
        config['marketing_consent_confirmed'] = bool(self.cleaned_data.get('marketing_consent_confirmed'))
        config['queue_initial_crm_event'] = bool(self.cleaned_data.get('queue_initial_crm_event'))
        config['validate_only'] = bool(self.cleaned_data.get('validate_only'))
        config['google_tag_enabled'] = bool(self.cleaned_data.get('google_tag_enabled'))
        primary_secret = str(self.cleaned_data.get('primary_secret') or '').strip()
        secret_ref_input = str(self.cleaned_data.get('secret_ref') or '').strip()
        if primary_secret:
            store_secret(secret_key=self.primary_vault_key, plaintext=primary_secret, updated_by=self.updated_by)
            self.integration.secret_ref = f'vault:{self.primary_vault_key}'
        elif self.cleaned_data.get('clear_stored_secret'):
            if str(self.integration.secret_ref or '').startswith('vault:'):
                delete_secret(str(self.integration.secret_ref).removeprefix('vault:'))
            self.integration.secret_ref = secret_ref_input or None
        else:
            self.integration.secret_ref = secret_ref_input or self.integration.secret_ref or None
        if self.cleaned_data.get('clear_webhook_secrets'):
            for key_name in ('app_secret', 'webhook_verify_token', 'ycloud_webhook_secret'):
                ref = self._config_secret_ref(key_name)
                if ref.startswith('vault:'):
                    delete_secret(ref.removeprefix('vault:'))
                config.pop(f'{key_name}_ref', None)
        self._store_optional_secret(field_name='app_secret', key_name='app_secret', config=config)
        self._store_optional_secret(
            field_name='webhook_verify_token',
            key_name='webhook_verify_token',
            config=config,
        )
        self._store_optional_secret(
            field_name='ycloud_webhook_secret',
            key_name='ycloud_webhook_secret',
            config=config,
        )
        self.integration.name = self.cleaned_data['name'].strip()
        self.integration.enabled = bool(self.cleaned_data['enabled'])
        self.integration.public_id = self.cleaned_data['public_id'].strip()
        self.integration.consent_category = str(self.cleaned_data.get('consent_category') or 'marketing').strip() or 'marketing'
        self.integration.config_json = config
        self.integration.full_clean()
        self.integration.save(
            update_fields=[
                'name', 'enabled', 'public_id', 'secret_ref', 'consent_category', 'config_json', 'updated_at',
            ]
        )
        return self.integration

    def connection_status(self) -> dict[str, object]:
        primary_ref = str(self.integration.secret_ref or '').strip()
        app_ref = self._config_secret_ref('app_secret') if self.is_webhook else ''
        verify_ref = self._config_secret_ref('webhook_verify_token') if self.is_webhook else ''
        ycloud_ref = self._config_secret_ref('ycloud_webhook_secret') if self.is_whatsapp else ''
        mode = str((self.integration.config_json or {}).get('credentials_mode') or 'adc')
        return {
            'primary_mode': 'vault' if primary_ref.startswith('vault:') else ('env' if primary_ref else ('adc' if self.is_google and mode == 'adc' else 'unset')),
            'primary_exists': self._secret_available(primary_ref) if primary_ref else bool(self.is_google and mode == 'adc'),
            'app_secret_exists': self._secret_available(app_ref) if app_ref else False,
            'verify_token_exists': self._secret_available(verify_ref) if verify_ref else False,
            'ycloud_webhook_secret_exists': self._secret_available(ycloud_ref) if ycloud_ref else False,
            'primary_ref': primary_ref,
        }
