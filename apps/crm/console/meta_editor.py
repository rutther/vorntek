"""Section-scoped Meta settings; never apply a guided-flow configuration here."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import QueryDict
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .marketing_forms import MarketingIntegrationEditorForm, RESERVED_CONFIG_KEYS
from .secret_store import store_secret, delete_secret


def connection_check_time(diagnostic):
    """One timestamp convention for list/detail; never guess legacy timezone."""
    value = (diagnostic.get('checked_at') or diagnostic.get('tested_at')) if isinstance(diagnostic, dict) else None
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if not parsed:
        return {}
    zone = '时区未记录'
    if timezone.is_aware(parsed):
        parsed = timezone.localtime(parsed)
        offset = parsed.strftime('%z')
        zone = f'UTC{offset[:3]}:{offset[3:]}'
    return {'label': f'{parsed:%m-%d %H:%M} ({zone})', 'iso': value,
            'title': f'{parsed:%Y-%m-%d %H:%M} ({zone})'}


def connection_next_action(diagnostic, credential_saved):
    """Presentation only: never grant permission to apply or send events."""
    result = {'state': 'unchecked', 'can_return': False, 'title': '凭据已就绪，可以检查连接',
              'detail': '检查令牌身份和数据集读取权限，不发送测试事件。', 'badge': '待检查'}
    if not credential_saved:
        result.update(state='missing', title='先保存凭据，再检查连接', badge='待保存')
        return result
    if not isinstance(diagnostic, dict) or not diagnostic:
        return result
    checks = [row for row in diagnostic.get('checks', []) if isinstance(row, dict)]
    by_code = {row.get('code'): row for row in checks}
    required_failed = any(row.get('required', True) and row.get('status') != 'pass' for row in checks)
    if diagnostic.get('overall') == 'fail' or required_failed or by_code.get('token', {}).get('status') != 'pass':
        result.update(state='blocked', title='连接检查未通过', badge='需处理',
                      detail='先处理下方检查项，再重新检查。不会自动开启接入。')
        return result
    has_reminder = any(row.get('status') not in ('pass', 'info') and row.get('code') != 'enabled' for row in checks)
    if by_code.get('dataset_access', {}).get('status') == 'pass' and not has_reminder:
        result.update(state='ready', can_return=True, title='连接检查通过', badge='已检查',
                      detail='令牌身份和数据集读取检查通过；实际发送仍取决于方案应用和访客同意。')
    else:
        result.update(state='review', can_return=True, title='令牌有效，仍有待核对项', badge='有提醒',
                      detail='尚不能确认该数据集可正常回传。请查看待核对项；当前不代表实测通过。')
    return result


class MetaIntegrationSectionForm(MarketingIntegrationEditorForm):
    sections = {
        'credentials': ('primary_secret', 'secret_ref'),
        'identity': ('name',),
        'testing': ('api_version', 'test_event_code'),
        'advanced': ('config_json',),
        'state': ('enabled', 'confirm_global_change'),
        'clear': ('confirm_global_change',),
    }

    def __init__(self, data, *, integration, **kwargs):
        self.section = data.get('_section', '')
        self.expected_version = data.get('_version', '')
        initial = MarketingIntegrationEditorForm(integration=integration).initial
        merged = QueryDict('', mutable=True)
        for key, value in initial.items():
            merged[key] = 'on' if value is True else '' if value is False else str(value or '')
        for key in self.sections.get(self.section, ()):
            merged[key] = data.get(key, '')
        super().__init__(merged, integration=integration, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if self.section not in self.sections:
            raise ValidationError('无法识别保存区域，请刷新页面后重试。')
        if self.expected_version != self.integration.updated_at.isoformat():
            raise ValidationError('配置已经变化，请刷新页面并核对后重试；未保存的 Token 需要重新填写。')
        if self.section in {'state', 'clear'} and not cleaned.get('confirm_global_change'):
            self.add_error('confirm_global_change', '请确认此操作会影响使用本 Pixel 的全部流程。')
        if self.section == 'clear' and self.integration.enabled:
            raise ValidationError('接入仍在启用中，不能清除凭据。请先核对关联流程并停用。')
        if self.section == 'credentials' and not self._primary_secret_available_after_save(cleaned):
            self.add_error('primary_secret', '请填写 Access Token，或提供服务器中已配置的环境变量名。')
        if self.section == 'advanced' and RESERVED_CONFIG_KEYS.intersection(cleaned.get('config_json', {})):
            self.add_error('config_json', '系统管理字段不能从高级配置修改，请使用对应设置区域。')
        return cleaned

    @transaction.atomic
    def save(self):
        item = type(self.integration).objects.select_for_update().get(pk=self.integration.pk)
        if item.updated_at.isoformat() != self.expected_version:
            raise ValidationError('配置已经变化，请刷新页面并核对后重试。')
        config = dict(item.config_json or {})
        fields = ['updated_at']
        if self.section == 'credentials':
            token = str(self.cleaned_data.get('primary_secret') or '').strip()
            reference = str(self.cleaned_data.get('secret_ref') or '').strip()
            if token:
                store_secret(secret_key=self.primary_vault_key, plaintext=token, updated_by=self.updated_by)
                item.secret_ref = f'vault:{self.primary_vault_key}'
            elif reference:
                item.secret_ref = reference
            if token or item.secret_ref != self.integration.secret_ref:
                config.pop('last_diagnostic', None)
                fields.append('config_json')
            fields.append('secret_ref')
        elif self.section == 'identity':
            item.name = self.cleaned_data['name'].strip()
            fields.append('name')
        elif self.section == 'testing':
            for key in ('api_version', 'test_event_code'):
                value = str(self.cleaned_data.get(key) or '').strip()
                if value:
                    config[key] = value
                else:
                    config.pop(key, None)
            config.pop('last_diagnostic', None)
            fields.append('config_json')
        elif self.section == 'advanced':
            config = {key: value for key, value in config.items() if key in RESERVED_CONFIG_KEYS}
            config.update(self.cleaned_data['config_json'])
            config.pop('last_diagnostic', None)
            fields.append('config_json')
        elif self.section == 'state':
            item.enabled = bool(self.cleaned_data['enabled'])
            fields.append('enabled')
        elif self.section == 'clear':
            if str(item.secret_ref or '').startswith('vault:'):
                delete_secret(item.secret_ref.removeprefix('vault:'))
            item.secret_ref = None
            config.pop('last_diagnostic', None)
            fields.extend(['secret_ref', 'config_json'])
        item.config_json = config
        # The form already validates extension JSON as an object. An empty
        # object is the supported default, not a missing required setting.
        item.full_clean(exclude=['config_json'] if config == {} else None)
        item.save(update_fields=fields)
        return item
