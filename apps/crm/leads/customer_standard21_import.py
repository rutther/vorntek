"""三大区「21 列标准格式」客资 CSV 通道（客户公海）。

一行 = 一个账户下的一位联系人 + 一条主路线；多行同 account_id 归到同一家企业。
21 列原样落 crm_customer_pool_row（导出靠它逐行还原），联系人 / 联系方式点
照旧走既有导入管道（发布门禁与打电话链路依赖它们）。

value 由统一引擎复算，逐行必须等于文件值（差值只允许 0 或 +5 的项目信号）。
不达标的那一行单独拒收并列明原因，不会静默改数。
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db.models import Max
from django.utils import timezone

from console.capabilities import SalesCapability, require_sales

from .customer_import_services import (
    MAX_RESEARCH_IMPORT_BYTES,
    MAX_SOURCE_ROWS,
    CustomerImportError,
    ImportPreviewResult,
    _persist_preview_payloads,
)
from .customer_value_engine import ValueReconciliationError, reconcile_file_value
from .models import Company, Contact, CustomerPoolRow

ADAPTER_VERSION = 'customer-pool-standard21-v1'
NAMESPACE = 'standard21'

STANDARD21_HEADERS = (
    'phone',
    'email',
    'country_name',
    'country',
    'person_name',
    'company',
    'value',
    'email_2',
    'email_3',
    'phone_2',
    'phone_3',
    'route_type',
    'route_tier',
    'account_id',
    'whatsapp_confirmed',
    'evidence_V',
    'identity_I',
    'tech_T',
    'priority_P',
    'restriction_note',
    'source_channel',
)
CONTACT_SLOTS = (
    ('phone', 'phone'),
    ('email', 'email'),
    ('phone_2', 'phone'),
    ('email_2', 'email'),
    ('phone_3', 'phone'),
    ('email_3', 'email'),
)
ROW_KEY_FIELDS = (
    'account_id',
    'person_name',
    'phone',
    'email',
    'phone_2',
    'email_2',
    'phone_3',
    'email_3',
)
SOURCE_TYPE_MAP = {
    'research_public': 'research',
    'research': 'research',
    'website_form': 'website_form',
    'website': 'website_form',
    'manual': 'manual',
}
COUNTRY_PATTERN = re.compile(r'^[A-Za-z]{2}$')
# 标准表头 → crm_customer_pool_row 列名（导出按此反向还原表头）
POOL_FIELD_BY_HEADER = {
    'phone': 'phone',
    'email': 'email',
    'country_name': 'country_name',
    'country': 'country_code',
    'person_name': 'person_name',
    'company': 'company_name',
    'value': 'value',
    'email_2': 'email_2',
    'email_3': 'email_3',
    'phone_2': 'phone_2',
    'phone_3': 'phone_3',
    'route_type': 'route_type',
    'route_tier': 'route_tier',
    'account_id': 'account_id',
    'whatsapp_confirmed': 'whatsapp_confirmed',
    'evidence_V': 'evidence_v',
    'identity_I': 'identity_i',
    'tech_T': 'tech_t',
    'priority_P': 'priority_p',
    'restriction_note': 'restriction_note',
    'source_channel': 'source_channel',
}
POOL_ROW_FIELDS = tuple(
    dict.fromkeys(
        (
            'company_id',
            'contact_id',
            'import_batch_id',
            'import_row_id',
            'row_number',
            'project_signal',
            *POOL_FIELD_BY_HEADER.values(),
        )
    )
)
# 只有这四个是外键；account_id 是文本列，不能被后缀规则误跳过。
POOL_ROW_FOREIGN_KEYS = ('company_id', 'contact_id', 'import_batch_id', 'import_row_id')
POOL_ROW_SCALAR_FIELDS = tuple(
    name for name in POOL_ROW_FIELDS if name not in POOL_ROW_FOREIGN_KEYS
)
# 只认“明确说不要用这条联系方式”的措辞；本文件仅“不导出”2 行命中。
HARD_RESTRICTION_MARKERS = (
    '不导出',
    '禁止联系',
    '勿扰',
    '拒联',
    'do_not_contact',
    'do not contact',
)
# 路线质量提示：挑选发布路线时降级用，不封禁企业。
ROUTE_FLAG_MARKERS = (
    '源表标记为特殊/历史路线',
    '源表标记为历史备用号码',
)
PLACEHOLDER_MARKERS = ('未采信',)


@dataclass(frozen=True, slots=True)
class Standard21Row:
    row_number: int
    values: dict[str, str]
    row_key: str
    value: Decimal
    project_signal: bool
    restricted: bool
    route_flags: tuple[str, ...]
    person_placeholder: bool


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def has_hard_restriction(note: str) -> bool:
    """该行是否被明确要求「不要用这条联系方式」。"""

    folded = (note or '').casefold()
    return any(marker.casefold() in folded for marker in HARD_RESTRICTION_MARKERS)


def route_flags_for_note(note: str) -> tuple[str, ...]:
    """路线质量软提示：挑选发布路线时降级用，不封禁企业。"""

    folded = (note or '').casefold()
    return tuple(marker for marker in ROUTE_FLAG_MARKERS if marker.casefold() in folded)


def read_standard21_csv(file_bytes: bytes) -> tuple[list[Standard21Row], list[dict]]:
    """严格解析标准 21 列 CSV；返回 (可用行, 逐行拒收清单)。"""
    if not file_bytes:
        raise CustomerImportError('请选择要导入的 21 列客资 CSV 文件。', code='file_required')
    if len(file_bytes) > MAX_RESEARCH_IMPORT_BYTES:
        raise CustomerImportError('21 列客资文件不能超过 10 MB。', code='file_too_large')
    try:
        decoded = file_bytes.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise CustomerImportError('文件不是 UTF-8 编码的 CSV。', code='file_encoding_invalid') from error
    reader = csv.reader(io.StringIO(decoded))
    try:
        header = [cell.strip() for cell in next(reader)]
    except StopIteration as error:
        raise CustomerImportError('文件为空。', code='file_empty') from error
    if tuple(header) != STANDARD21_HEADERS:
        raise CustomerImportError(
            '表头必须恰好是标准 21 列且顺序一致；应为：' + '、'.join(STANDARD21_HEADERS) + '。',
            code='standard21_header_mismatch',
        )
    entries: list[tuple[int, dict[str, str]]] = []
    rejected: list[dict] = []
    for row_number, cells in enumerate(reader, start=2):
        if not any(_text(cell) for cell in cells):
            continue
        if len(cells) != len(STANDARD21_HEADERS):
            rejected.append(_rejected(row_number, {}, f'该行有 {len(cells)} 列，标准格式要求 21 列。'))
            continue
        entries.append(
            (row_number, {name: _text(cell) for name, cell in zip(STANDARD21_HEADERS, cells)})
        )
    if not entries and not rejected:
        raise CustomerImportError('文件里没有数据行。', code='file_empty')
    if len(entries) + len(rejected) > MAX_SOURCE_ROWS:
        raise CustomerImportError(
            f'单个文件最多 {MAX_SOURCE_ROWS} 行，当前 {len(entries) + len(rejected)} 行。',
            code='too_many_rows',
        )
    rows, row_rejected = _build_rows(entries)
    return rows, [*rejected, *row_rejected]


def _rejected(row_number: int, values: dict[str, str], message: str) -> dict:
    return {
        'row_number': row_number,
        'message': message,
        'payload': {
            'kind': 'standard21',
            'external_key': values.get('account_id', ''),
            'company_name': values.get('company', ''),
        },
    }


def _build_rows(entries: list[tuple[int, dict[str, str]]]) -> tuple[list[Standard21Row], list[dict]]:
    rows: list[Standard21Row] = []
    rejected: list[dict] = []
    occurrences: Counter[str] = Counter()
    for row_number, values in entries:
        problem = _row_problem(values)
        if problem:
            rejected.append(_rejected(row_number, values, f'第 {row_number} 行 {problem}'))
            continue
        try:
            file_value = Decimal(values['value'])
        except (InvalidOperation, ValueError):
            rejected.append(_rejected(row_number, values, f'第 {row_number} 行的 value 不是数字。'))
            continue
        note = values['restriction_note']
        try:
            value, project_signal = reconcile_file_value(
                file_value=file_value,
                source_channel=values['source_channel'],
                identity=values['identity_I'],
                evidence_v=values['evidence_V'],
                route_tier=values['route_tier'],
                whatsapp_confirmed=values['whatsapp_confirmed'],
                has_email=any(values[slot] for slot in ('email', 'email_2', 'email_3')),
                priority=values['priority_P'],
            )
        except ValueReconciliationError as error:
            rejected.append(_rejected(row_number, values, f'第 {row_number} 行 value 复算不符：{error}'))
            continue
        key_payload = '|'.join(values[name] for name in ROW_KEY_FIELDS)
        digest = hashlib.sha256(key_payload.encode('utf-8')).hexdigest()
        occurrences[digest] += 1
        if occurrences[digest] > 1:
            # 同一文件内身份字段完全相同的重复行：按出现顺序加序号，重导仍幂等。
            digest = hashlib.sha256(
                f'{key_payload}|#{occurrences[digest]}'.encode('utf-8')
            ).hexdigest()
        rows.append(
            Standard21Row(
                row_number=row_number,
                values=values,
                row_key=digest,
                value=value,
                project_signal=project_signal,
                restricted=has_hard_restriction(note),
                route_flags=route_flags_for_note(note),
                person_placeholder=any(
                    marker.casefold() in note.casefold() for marker in PLACEHOLDER_MARKERS
                ),
            )
        )
    return rows, rejected


def _row_problem(values: dict[str, str]) -> str:
    if not values['account_id']:
        return '缺少 account_id，无法归户。'
    if not values['company']:
        return '缺少公司名。'
    if not COUNTRY_PATTERN.match(values['country']):
        return 'country 必须是两位 ISO 国家代码。'
    if not any(values[slot] for slot, _channel in CONTACT_SLOTS):
        return '电话和邮箱都为空。'
    return ''


def _most_common(values: list[str]) -> str:
    counts = Counter(value for value in values if value)
    if not counts:
        return ''
    top = max(counts.values())
    return next(value for value in values if value and counts[value] == top)


def _contact_payload(row: Standard21Row, *, slot: str, channel: str, value: str) -> dict:
    note = row.values['restriction_note']
    return {
        'external_key': row.values['account_id'],
        'contact_name': '' if row.person_placeholder else row.values['person_name'],
        'job_title': '',
        'channel': channel,
        'value': value,
        'extension': '',
        'purpose': 'business',
        'usage_status': 'restricted' if row.restricted else 'unknown',
        'evidence_note': note,
        'row_number': row.row_number,
        'evidence_json': {
            'slot': slot,
            'row_key': row.row_key,
            'account_id': row.values['account_id'],
            'source_channel': row.values['source_channel'],
            'route_type': row.values['route_type'],
            'route_tier': row.values['route_tier'],
            'evidence_V': row.values['evidence_V'],
            'identity_I': row.values['identity_I'],
            'tech_T': row.values['tech_T'],
            'priority_P': row.values['priority_P'],
            'whatsapp_confirmed': row.values['whatsapp_confirmed'],
            'restriction_note': note,
            'route_flags': list(row.route_flags),
            'person_placeholder': row.person_placeholder,
            'file_value': str(row.value),
            'project_signal': row.project_signal,
        },
    }


def _pool_row_entry(row: Standard21Row) -> dict:
    return {
        **row.values,
        'row_key': row.row_key,
        'row_number': row.row_number,
        'value': str(row.value),
        'project_signal': row.project_signal,
        'has_contact_record': bool(row.values['person_name']) and not row.person_placeholder,
    }


def build_standard21_payloads(rows: list[Standard21Row]) -> tuple[list[dict], int]:
    groups: OrderedDict[str, list[Standard21Row]] = OrderedDict()
    for row in rows:
        groups.setdefault(row.values['account_id'], []).append(row)
    payloads: list[dict] = []
    contact_row_count = 0
    for account_id, group in groups.items():
        source_channel = group[0].values['source_channel']
        contacts: list[dict] = []
        for row in group:
            for slot, channel in CONTACT_SLOTS:
                split_value = row.values[slot]
                if split_value:
                    contacts.append(
                        _contact_payload(row, slot=slot, channel=channel, value=split_value)
                    )
        contact_row_count += len(contacts)
        highest = max(row.value for row in group)
        payloads.append(
            {
                'kind': 'standard21',
                'external_key': account_id,
                'company_name': _most_common([row.values['company'] for row in group]),
                'country': _most_common([row.values['country'] for row in group]).upper(),
                'country_name': _most_common([row.values['country_name'] for row in group]),
                'city': '',
                'industry': '',
                'website': '',
                'source_type': SOURCE_TYPE_MAP.get(source_channel.casefold(), 'research'),
                'source_detail': f'三大区标准 21 列客资（{source_channel}）',
                'source_external_id': f'{ADAPTER_VERSION}:{account_id}',
                'source_evidence': {
                    'adapter_version': ADAPTER_VERSION,
                    'account_id': account_id,
                    'source_channel': source_channel,
                    'file_value': str(highest),
                },
                'notes': '',
                'company_row_number': group[0].row_number,
                'value': str(highest),
                'contacts': contacts,
                'pool_rows': [_pool_row_entry(row) for row in group],
            }
        )
    return payloads, contact_row_count


def preview_standard21_import(
    *,
    site,
    actor,
    file_bytes: bytes,
    original_name: str,
) -> ImportPreviewResult:
    require_sales(actor, SalesCapability.POOL_IMPORT)
    rows, rejected_rows = read_standard21_csv(file_bytes)
    payloads, contact_row_count = build_standard21_payloads(rows)
    result = _persist_preview_payloads(
        site=site,
        actor=actor,
        file_hash=hashlib.sha256(file_bytes).hexdigest(),
        original_name=original_name,
        namespace=NAMESPACE,
        source_type='research',
        adapter_version=ADAPTER_VERSION,
        payloads=payloads,
        contact_row_count=contact_row_count,
        rejected_rows=rejected_rows,
    )
    if not result.replayed:
        counts = dict(result.batch.counts_json)
        counts['accounts'] = len(payloads)
        counts['source_file_rows'] = len(rows) + len(rejected_rows)
        counts['pool_rows'] = sum(len(payload['pool_rows']) for payload in payloads)
        result.batch.counts_json = counts
        result.batch.save(update_fields=['counts_json', 'updated_at'])
    return result


def _pool_row_values(entry: dict) -> dict:
    values = {
        field: entry.get(header) or '' for header, field in POOL_FIELD_BY_HEADER.items()
    }
    values['country_code'] = values['country_code'].upper()
    values['value'] = Decimal(entry['value'])
    values['project_signal'] = bool(entry.get('project_signal'))
    values['row_number'] = int(entry.get('row_number') or 0)
    return values


def pool_row_standard_values(pool_row) -> list[str]:
    """按标准 21 列表头顺序取值，供导出逐行还原。"""
    result = []
    for header in STANDARD21_HEADERS:
        value = getattr(pool_row, POOL_FIELD_BY_HEADER[header])
        if header == 'value':
            result.append(f'{Decimal(value):.1f}')
        else:
            result.append('' if value is None else str(value))
    return result


def commit_standard21_rows(*, row, batch, site, actor, company: Company) -> None:
    """把该企业的 21 列源行逐行落到 crm_customer_pool_row。"""
    entries = row.payload_json.get('pool_rows') or []
    if not entries:
        return
    contacts = {
        contact.full_name: contact
        for contact in Contact.objects.filter(site=site, company=company)
    }
    existing = {
        pool_row.row_key: pool_row
        for pool_row in CustomerPoolRow.objects.filter(
            site=site, row_key__in=[entry['row_key'] for entry in entries]
        )
    }
    to_create: list[CustomerPoolRow] = []
    to_update: list[CustomerPoolRow] = []
    whatsapp_targets: list[tuple[Contact, str]] = []
    for entry in entries:
        values = _pool_row_values(entry)
        pool_row = existing.get(entry['row_key'])
        if pool_row is None:
            pool_row = CustomerPoolRow(site=site, row_key=entry['row_key'])
            to_create.append(pool_row)
        else:
            pool_row.updated_at = timezone.now()
            to_update.append(pool_row)
        pool_row.company_id = company.pk
        pool_row.contact_id = (
            contacts[values['person_name']].pk
            if entry.get('has_contact_record') and values['person_name'] in contacts
            else None
        )
        pool_row.import_batch_id = batch.pk
        pool_row.import_row_id = row.pk
        for name in POOL_ROW_SCALAR_FIELDS:
            setattr(pool_row, name, values[name])
        if values['whatsapp_confirmed'] == '已确认' and values['phone'] and pool_row.contact_id:
            whatsapp_targets.append((contacts[values['person_name']], values['phone']))
    if to_create:
        CustomerPoolRow.objects.bulk_create(to_create, batch_size=500)
    if to_update:
        CustomerPoolRow.objects.bulk_update(
            to_update, fields=[*POOL_ROW_FIELDS, 'updated_at'], batch_size=500
        )
    for contact, phone in whatsapp_targets:
        if not contact.whatsapp_phone:
            contact.whatsapp_phone = phone
            contact.save(update_fields=['whatsapp_phone', 'updated_at'])
    _refresh_company_value(site=site, company=company, fallback=row.payload_json.get('value'))
    if not company.country_code and row.payload_json.get('country'):
        company.country_code = row.payload_json['country'][:2].upper()
        company.save(update_fields=['country_code', 'updated_at'])


def _refresh_company_value(*, site, company: Company, fallback) -> None:
    highest = CustomerPoolRow.objects.filter(site=site, company=company).aggregate(
        highest=Max('value')
    )['highest']
    if highest is None and fallback is not None:
        highest = Decimal(str(fallback))
    if highest is None or company.value == highest:
        return
    company.value = highest
    company.save(update_fields=['value', 'updated_at'])
