from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any, Iterable

from django.db import connection
from django.db.models import Q
from django.utils import timezone

from leads.email_delivery import email_delivery_configured
from leads.models import CustomerExportJob, LeadEventOutbox, LeadFormDefinition, LeadInboundEvent
from marketing.models import MarketingIntegration


EXPECTED_INTEGRATIONS = (
    ('meta', 'pixel'),
    ('meta', 'leadgen'),
    ('whatsapp', 'cloud_api'),
    ('google', 'data_manager'),
)

RECOMMENDED_EVENTS = {
    'submission_event_name': 'Lead',
    'contacted_event_name': 'contacted_lead',
    'qualified_event_name': 'qualified_lead',
    'won_event_name': 'converted',
}

PLACEHOLDER_PREFIXES = ('pending-', 'placeholder-', 'replace-', 'your-')
REQUIRED_TABLES = (
    'lead_form_definition',
    'lead_submission',
    'lead_event_outbox',
    'lead_inbound_event',
    'marketing_provider',
    'marketing_integration',
)


def readiness_check(
    code: str,
    label: str,
    status: str,
    detail: str,
    *,
    required: bool = True,
    section: str = 'local',
) -> dict[str, Any]:
    return {
        'code': code,
        'label': label,
        'status': status,
        'detail': str(detail).strip()[:500],
        'required': required,
        'section': section,
    }


def is_real_public_id(value: Any) -> bool:
    normalized = str(value or '').strip().lower()
    return bool(normalized and not normalized.startswith(PLACEHOLDER_PREFIXES))


def inspect_form_definition(form: Any) -> list[dict[str, Any]]:
    code = str(getattr(form, 'code', '') or 'form')
    checks = [
        readiness_check(
            f'form.{code}.active',
            f'表单 {code} 状态',
            'pass' if getattr(form, 'status', '') == 'active' else 'warn',
            '已启用' if getattr(form, 'status', '') == 'active' else '当前未启用',
            required=False,
            section='crm',
        ),
        readiness_check(
            f'form.{code}.capi',
            f'表单 {code} 服务器回传',
            'pass' if bool(getattr(form, 'capi_enabled', False)) else 'warn',
            '已启用' if bool(getattr(form, 'capi_enabled', False)) else '当前停用',
            required=False,
            section='crm',
        ),
    ]
    event_labels = {
        'submission_event_name': '提交',
        'contacted_event_name': '已联系',
        'qualified_event_name': '合格',
        'won_event_name': '成交',
    }
    for field_name, expected in RECOMMENDED_EVENTS.items():
        actual = str(getattr(form, field_name, '') or '').strip()
        checks.append(
            readiness_check(
                f'form.{code}.{field_name}',
                f'表单 {code} {event_labels[field_name]}事件',
                'pass' if actual == expected else 'fail',
                f'{actual or "未配置"}；建议值 {expected}',
                section='crm',
            )
        )
    return checks


def _configured_reference(value: Any) -> bool:
    return bool(str(value or '').strip())


def _config_value(config: dict[str, Any], key: str) -> str:
    return str(config.get(key) or '').strip()


def _config_check(
    integration_key: str,
    config: dict[str, Any],
    key: str,
    label: str,
    *,
    expected: str | None = None,
) -> dict[str, Any]:
    actual = _config_value(config, key)
    ready = actual == expected if expected is not None else bool(actual)
    detail = f'已配置为 {actual}' if ready and actual else '已配置'
    if not ready:
        detail = f'未配置；建议值 {expected}' if expected is not None else '未配置'
    return readiness_check(
        f'integration.{integration_key}.{key}',
        label,
        'pass' if ready else 'fail',
        detail,
        section='integration',
    )


def _webhook_integration_checks(integration_key: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _config_check(integration_key, config, 'form_code', '写入 CRM 的表单代码'),
        _config_check(integration_key, config, 'app_secret_ref', 'Webhook App Secret 引用'),
        _config_check(integration_key, config, 'webhook_verify_token_ref', 'Webhook 验证令牌引用'),
    ]


def inspect_marketing_integration(integration: Any) -> list[dict[str, Any]]:
    provider = str(getattr(getattr(integration, 'provider', None), 'code', '') or '').strip().lower()
    integration_type = str(getattr(integration, 'integration_type', '') or '').strip().lower()
    integration_key = f'{provider}.{integration_type}'
    config = dict(getattr(integration, 'config_json', {}) or {})
    secret_ref = str(getattr(integration, 'secret_ref', '') or '').strip()
    checks = [
        readiness_check(
            f'integration.{integration_key}.exists',
            f'{integration_key} 接入记录',
            'pass',
            '记录存在',
            section='integration',
        ),
        readiness_check(
            f'integration.{integration_key}.enabled',
            f'{integration_key} 接入开关',
            'pass' if bool(getattr(integration, 'enabled', False)) else 'warn',
            '已启用' if bool(getattr(integration, 'enabled', False)) else '当前停用',
            required=False,
            section='integration',
        ),
        readiness_check(
            f'integration.{integration_key}.public_id',
            f'{integration_key} 平台公开 ID',
            'pass' if is_real_public_id(getattr(integration, 'public_id', '')) else 'fail',
            '已配置真实 ID' if is_real_public_id(getattr(integration, 'public_id', '')) else '仍是空值或占位值',
            section='integration',
        ),
    ]

    if (provider, integration_type) == ('meta', 'pixel'):
        checks.append(
            readiness_check(
                f'integration.{integration_key}.secret_ref',
                'Meta CAPI Access Token 引用',
                'pass' if _configured_reference(secret_ref) else 'fail',
                '已配置引用' if secret_ref else '未配置',
                section='integration',
            )
        )
    elif (provider, integration_type) == ('meta', 'leadgen'):
        checks.extend(_webhook_integration_checks(integration_key, config))
        configured_form_ids = config.get('leadgen_form_ids')
        if isinstance(configured_form_ids, str):
            configured_form_ids = [
                item for item in configured_form_ids.replace(',', ' ').split() if item
            ]
        if not isinstance(configured_form_ids, list):
            configured_form_ids = []
        checks.append(
            readiness_check(
                f'integration.{integration_key}.leadgen_form_ids',
                'Meta Instant Form 白名单',
                'pass' if configured_form_ids else 'fail',
                f'已配置 {len(configured_form_ids)} 个 Form ID'
                if configured_form_ids
                else '未配置允许接收的 Form ID',
                section='integration',
            )
        )
        checks.append(
            readiness_check(
                f'integration.{integration_key}.secret_ref',
                'Meta Page Access Token 引用',
                'pass' if _configured_reference(secret_ref) else 'fail',
                '已配置引用' if secret_ref else '未配置',
                section='integration',
            )
        )
        checks.append(
            readiness_check(
                f'integration.{integration_key}.queue_initial_crm_event',
                'Instant Form 初始 CRM 事件',
                'pass' if bool(config.get('queue_initial_crm_event')) else 'fail',
                '已启用' if bool(config.get('queue_initial_crm_event')) else '未启用',
                section='integration',
            )
        )
        checks.append(
            _config_check(
                integration_key,
                config,
                'initial_crm_event_name',
                'Instant Form 初始事件名称',
                expected='initial_lead',
            )
        )
    elif (provider, integration_type) == ('whatsapp', 'cloud_api'):
        transport_provider = _config_value(config, 'transport_provider') or 'meta'
        checks.extend([
            _config_check(integration_key, config, 'form_code', '写入 CRM 的表单代码'),
            readiness_check(
                f'integration.{integration_key}.transport_provider',
                'WhatsApp 官方传输方式',
                'pass' if transport_provider in {'meta', 'ycloud'} else 'fail',
                transport_provider,
                section='integration',
            ),
        ])
        if transport_provider == 'ycloud':
            checks.extend([
                _config_check(
                    integration_key,
                    config,
                    'ycloud_webhook_secret_ref',
                    'YCloud Webhook Signing Secret 引用',
                ),
                _config_check(
                    integration_key,
                    config,
                    'ycloud_webhook_endpoint_id',
                    'YCloud Webhook Endpoint ID',
                ),
                _config_check(
                    integration_key,
                    config,
                    'business_phone_e164',
                    'WhatsApp E.164 业务号码',
                ),
            ])
        else:
            checks.extend([
                _config_check(integration_key, config, 'app_secret_ref', 'Webhook App Secret 引用'),
                _config_check(integration_key, config, 'webhook_verify_token_ref', 'Webhook 验证令牌引用'),
            ])
        checks.extend(
            [
                readiness_check(
                    f'integration.{integration_key}.secret_ref',
                    'YCloud API Key 引用' if transport_provider == 'ycloud' else 'WhatsApp Access Token 引用',
                    'pass' if _configured_reference(secret_ref) else 'fail',
                    '已配置引用' if secret_ref else '未配置',
                    section='integration',
                ),
                _config_check(integration_key, config, 'waba_id', 'WhatsApp Business Account ID'),
                readiness_check(
                    f'integration.{integration_key}.ctwa_identity_mode',
                    'CTWA 回传身份模式',
                    'pass' if _config_value(config, 'ctwa_identity_mode') in {'', 'waba', 'page'} else 'fail',
                    _config_value(config, 'ctwa_identity_mode') or 'waba（默认）',
                    section='integration',
                ),
            ]
        )
        if _config_value(config, 'ctwa_identity_mode') == 'page':
            checks.append(
                _config_check(
                    integration_key,
                    config,
                    'page_id',
                    'CTWA Meta Page ID（旧版）',
                )
            )
    elif (provider, integration_type) == ('google', 'data_manager'):
        mode = _config_value(config, 'credentials_mode') or 'adc'
        valid_mode = mode in {'adc', 'authorized_user', 'service_account'}
        checks.extend(
            [
                readiness_check(
                    f'integration.{integration_key}.credentials_mode',
                    'Google 凭据模式',
                    'pass' if valid_mode else 'fail',
                    mode if valid_mode else f'不支持的值 {mode}',
                    section='integration',
                ),
                readiness_check(
                    f'integration.{integration_key}.credentials_ref',
                    'Google 凭据来源',
                    'pass' if mode == 'adc' or _configured_reference(secret_ref) else 'fail',
                    '使用 ADC' if mode == 'adc' else ('已配置引用' if secret_ref else '未配置'),
                    section='integration',
                ),
                _config_check(
                    integration_key,
                    config,
                    'qualified_conversion_action_id',
                    'Google Qualified Lead 转化操作',
                ),
                _config_check(
                    integration_key,
                    config,
                    'converted_conversion_action_id',
                    'Google Converted Lead 转化操作',
                ),
                readiness_check(
                    f'integration.{integration_key}.validate_only',
                    'Google Data Manager 写入模式',
                    'warn' if bool(config.get('validate_only', True)) else 'pass',
                    '仅验证，不正式记账' if bool(config.get('validate_only', True)) else '正式写入模式',
                    required=False,
                    section='integration',
                ),
            ]
        )
    return checks


def _table_columns(table_name: str) -> set[str]:
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table_name)
    return {column.name for column in description}


def _status_counts(queryset: Any) -> dict[str, int]:
    return dict(Counter(str(value) for value in queryset.values_list('status', flat=True)))


def checks_overall(checks: Iterable[dict[str, Any]]) -> str:
    checks = list(checks)
    if any(item['required'] and item['status'] == 'fail' for item in checks):
        return 'fail'
    if any(item['status'] in {'warn', 'fail'} for item in checks):
        return 'warn'
    return 'pass'


def collect_marketing_readiness(*, include_network: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    table_names = set(connection.introspection.table_names())
    missing_tables = sorted(set(REQUIRED_TABLES) - table_names)
    checks.append(
        readiness_check(
            'database.required_tables',
            'CRM 与营销数据表',
            'fail' if missing_tables else 'pass',
            f'缺少 {", ".join(missing_tables)}' if missing_tables else '核心表完整',
            section='database',
        )
    )

    if 'lead_form_definition' in table_names:
        columns = _table_columns('lead_form_definition')
        funnel_columns = set(RECOMMENDED_EVENTS)
        missing_columns = sorted(funnel_columns - columns)
        checks.append(
            readiness_check(
                'database.b2b_funnel_columns',
                'B2B CRM 漏斗字段迁移',
                'fail' if missing_columns else 'pass',
                f'缺少 {", ".join(missing_columns)}' if missing_columns else '0013 所需字段完整',
                section='database',
            )
        )
        if not missing_columns:
            forms = list(LeadFormDefinition.objects.filter(status='active').order_by('id'))
            checks.append(
                readiness_check(
                    'crm.active_forms',
                    '启用中的 CRM 表单',
                    'pass' if forms else 'fail',
                    f'{len(forms)} 个' if forms else '没有启用中的表单',
                    section='crm',
                )
            )
            for form in forms:
                checks.extend(inspect_form_definition(form))

    integrations: dict[tuple[str, str], MarketingIntegration] = {}
    if {'marketing_provider', 'marketing_integration'} <= table_names:
        records = list(MarketingIntegration.objects.select_related('provider').order_by('-enabled', 'id'))
        for integration in records:
            key = (
                str(integration.provider.code or '').strip().lower(),
                str(integration.integration_type or '').strip().lower(),
            )
            integrations.setdefault(key, integration)
        for key in EXPECTED_INTEGRATIONS:
            integration = integrations.get(key)
            if integration is None:
                integration_key = '.'.join(key)
                checks.append(
                    readiness_check(
                        f'integration.{integration_key}.exists',
                        f'{integration_key} 接入记录',
                        'fail',
                        '不存在',
                        section='integration',
                    )
                )
                continue
            checks.extend(inspect_marketing_integration(integration))
            if include_network:
                try:
                    from console.marketing_diagnostics import diagnose_marketing_integration

                    diagnostic = diagnose_marketing_integration(integration)
                except Exception as exc:
                    checks.append(
                        readiness_check(
                            f'network.{".".join(key)}.diagnostic',
                            f'{".".join(key)} 只读平台诊断',
                            'fail',
                            f'诊断异常：{type(exc).__name__}',
                            section='network',
                        )
                    )
                else:
                    checks.append(
                        readiness_check(
                            f'network.{".".join(key)}.diagnostic',
                            f'{".".join(key)} 只读平台诊断',
                            str(diagnostic.get('overall') or 'warn'),
                            str(diagnostic.get('summary') or '检查完成'),
                            section='network',
                        )
                    )

    queue_counts: dict[str, dict[str, int]] = {}
    if 'lead_event_outbox' in table_names:
        outbox_counts = _status_counts(LeadEventOutbox.objects.all())
        queue_counts['outbox'] = outbox_counts
        failed = outbox_counts.get('failed', 0)
        checks.append(
            readiness_check(
                'queue.outbox',
                '服务器回传队列',
                'warn' if failed else 'pass',
                f'状态统计 {outbox_counts or {"empty": 0}}',
                required=False,
                section='queue',
            )
        )
    if 'lead_inbound_event' in table_names:
        inbound_counts = _status_counts(LeadInboundEvent.objects.all())
        queue_counts['inbound'] = inbound_counts
        failed = inbound_counts.get('failed', 0)
        checks.append(
            readiness_check(
                'queue.inbound',
                'Webhook 入站队列',
                'warn' if failed else 'pass',
                f'状态统计 {inbound_counts or {"empty": 0}}',
                required=False,
                section='queue',
            )
        )
    if 'crm_customer_export_job' in table_names:
        export_counts = _status_counts(CustomerExportJob.objects.all())
        queue_counts['customer_exports'] = export_counts
        stale_before = timezone.now() - timedelta(minutes=15)
        stale = CustomerExportJob.objects.filter(
            Q(status='pending') | Q(status='processing'),
            updated_at__lte=stale_before,
        ).count()
        failed = export_counts.get('failed', 0)
        checks.append(
            readiness_check(
                'queue.customer_exports',
                '客户导出后台队列',
                'warn' if failed or stale else 'pass',
                f'状态统计 {export_counts or {"empty": 0}}；超时待处理 {stale}',
                required=False,
                section='queue',
            )
        )

    smtp_ready = email_delivery_configured()
    checks.append(
        readiness_check(
            'notification.smtp',
            '新线索邮件通知',
            'pass' if smtp_ready else 'warn',
            '邮件发送配置就绪' if smtp_ready else 'SMTP 尚未配置',
            required=False,
            section='notification',
        )
    )

    status = checks_overall(checks)
    return {
        'schema_version': 1,
        'mode': 'local_and_read_only_network' if include_network else 'local_only',
        'overall': status,
        'summary': {
            'pass': sum(1 for item in checks if item['status'] == 'pass'),
            'warn': sum(1 for item in checks if item['status'] == 'warn'),
            'fail': sum(1 for item in checks if item['status'] == 'fail'),
        },
        'checks': checks,
        'queue_counts': queue_counts,
        'acceptance_note': (
            '本报告不发送任何广告事件，也不读取或输出联系人与密钥。'
            '平台是否真实接收、匹配并用于优化，仍须在 Meta/Google 后台完成事件级验收。'
        ),
    }
