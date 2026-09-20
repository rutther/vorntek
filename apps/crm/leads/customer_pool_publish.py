"""客户公海核验发布：确定性挑选发布路线 + 批量勾选发布。

发布门禁只有一套（publish_reviewed_customer）。批量发布不是"跳过核验"，
而是把逐家手点换成系统循环：每一家独立事务、独立审计，任何一家失败都不影响其它家。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied

from console.capabilities import SalesCapability, require_sales

from .customer_pool_services import (
    TOKEN_PATTERN,
    CustomerPoolError,
    publish_reviewed_customer,
)
from .customer_standard21_import import (
    CONTACT_SLOTS,
    has_hard_restriction,
    route_flags_for_note,
)
from .models import Company, CompanyContactPoint, CompanyPoolState, CustomerPoolRow

# 发布点挑选：路线层级优先；同层内源表软提示降级、证据 V1>V2>V3、再看 WhatsApp 已确认。
ROUTE_TIER_ORDER = ('本人直联', '公开业务手机', '指定人物转接', '企业总机', '部门入口')
EVIDENCE_ORDER = ('V1', 'V2', 'V3')
SLOT_ORDER = {slot: index for index, (slot, _channel) in enumerate(CONTACT_SLOTS)}
BULK_PUBLISH_LIMIT = 100


@dataclass(frozen=True, slots=True)
class PublishRoute:
    point: CompanyContactPoint
    pool_row: CustomerPoolRow | None
    label: str


@dataclass(frozen=True, slots=True)
class CustomerBulkPublishItemResult:
    company_id: int
    status: str
    message: str
    workflow_code: str
    company_name: str = ''
    route_label: str = ''
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class CustomerBulkPublishResult:
    items: tuple[CustomerBulkPublishItemResult, ...]

    @property
    def succeeded(self) -> int:
        return sum(item.status == 'published' for item in self.items)

    @property
    def failed(self) -> int:
        return sum(item.status == 'failed' for item in self.items)


def _publishable(point: CompanyContactPoint) -> bool:
    """与 publish_reviewed_customer 同一门禁口径：禁止联系或限制使用的点不能当发布点。"""

    return point.status not in {'invalid', 'do_not_contact'} and point.usage_status != 'restricted'


def _route_label(*, tier: str, evidence: str, whatsapp: str, flags: tuple[str, ...]) -> str:
    parts = [tier or '未标注路线层级']
    if evidence:
        parts.append(evidence)
    if whatsapp == '已确认':
        parts.append('WhatsApp 已确认')
    label = ' · '.join(parts)
    if flags:
        label += f'（源表提示：{"、".join(flags)}，优先级已降级）'
    return label


def _route_sort_key(*, pool_row: CustomerPoolRow, flags: tuple[str, ...]) -> tuple:
    tier = pool_row.route_tier or ''
    evidence = pool_row.evidence_v or ''
    return (
        ROUTE_TIER_ORDER.index(tier) if tier in ROUTE_TIER_ORDER else len(ROUTE_TIER_ORDER),
        1 if flags else 0,
        EVIDENCE_ORDER.index(evidence) if evidence in EVIDENCE_ORDER else len(EVIDENCE_ORDER),
        0 if pool_row.whatsapp_confirmed == '已确认' else 1,
        -(pool_row.value if pool_row.value is not None else Decimal('0')),
        pool_row.row_number or 0,
        pool_row.row_key,
    )


def preferred_publish_route(*, site, company) -> PublishRoute | None:
    """挑一条确定性发布路线，找不到可发布的联系方式时返回 None。

    有 21 列标准路线时只在标准行里挑（一行 = 一个公司 + 一条联系方式），
    源表标了"不导出"的行直接出局——即使全企业的行都被封，也不改挑别的行。
    只有完全没有标准行的历史资料才退回既有联系方式，同样取排序后的第一条。
    """

    points_by_row_key: dict[str, list[CompanyContactPoint]] = {}
    fallback: list[CompanyContactPoint] = []
    for point in CompanyContactPoint.objects.filter(site=site, company=company).order_by('id'):
        if not _publishable(point):
            continue
        fallback.append(point)
        row_key = str((point.evidence_json or {}).get('row_key') or '')
        if row_key:
            points_by_row_key.setdefault(row_key, []).append(point)

    candidates: list[tuple[tuple, PublishRoute]] = []
    for pool_row in CustomerPoolRow.objects.filter(site=site, company=company):
        points = points_by_row_key.get(pool_row.row_key) or []
        if not points or has_hard_restriction(pool_row.restriction_note):
            continue
        point = min(
            points,
            key=lambda item: SLOT_ORDER.get(
                str((item.evidence_json or {}).get('slot') or ''), len(SLOT_ORDER)
            ),
        )
        flags = route_flags_for_note(pool_row.restriction_note)
        candidates.append((
            _route_sort_key(pool_row=pool_row, flags=flags),
            PublishRoute(
                point=point,
                pool_row=pool_row,
                label=_route_label(
                    tier=pool_row.route_tier or '',
                    evidence=pool_row.evidence_v or '',
                    whatsapp=pool_row.whatsapp_confirmed or '',
                    flags=flags,
                ),
            ),
        ))
    if candidates:
        return min(candidates, key=lambda item: item[0])[1]
    if points_by_row_key:
        return None
    if fallback:
        return PublishRoute(point=fallback[0], pool_row=None, label='历史资料联系方式')
    return None


def publish_reviewed_customers_bulk(
    *,
    site,
    actor,
    team,
    company_ids: list[int],
    idempotency_token: str,
) -> CustomerBulkPublishResult:
    """勾选一批待核验客户，逐家跑与单独发布完全相同的门禁。

    每家的请求令牌由批次令牌、客户与当前版本派生：同一批选择被重复提交时，
    已发布过的客户不会重复发布；并发重复提交（同一版本）会被识别为已处理。
    """

    require_sales(actor, SalesCapability.POOL_REVIEW)
    if not company_ids:
        raise CustomerPoolError('请至少选择一家待核验客户。', code='bulk_selection_required')
    if len(company_ids) > BULK_PUBLISH_LIMIT:
        raise CustomerPoolError(
            f'单次最多核验发布 {BULK_PUBLISH_LIMIT} 家客户。', code='bulk_selection_too_large'
        )
    if any(company_id <= 0 for company_id in company_ids):
        raise CustomerPoolError('批量核验内容无效，请刷新后重试。', code='bulk_selection_invalid')
    if len(company_ids) != len(set(company_ids)):
        raise CustomerPoolError('同一家客户不能重复选择。', code='bulk_selection_duplicate')
    if team is None or not team.enabled or team.site_id != site.pk:
        raise CustomerPoolError('请选择当前站点的可用销售团队。', code='team_invalid')
    token = str(idempotency_token or '').strip()
    if not TOKEN_PATTERN.fullmatch(token):
        raise CustomerPoolError('请求令牌无效，请刷新后重试。', code='idempotency_token_invalid')
    batch_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()

    items: list[CustomerBulkPublishItemResult] = []
    for company_id in company_ids:
        route: PublishRoute | None = None
        company_name = ''
        try:
            company = Company.objects.get(pk=company_id, site=site)
            company_name = company.name
            state = CompanyPoolState.objects.get(company=company)
            item_token = 'bulk-review:' + hashlib.sha256(
                f'{batch_hash}:{company_id}:{state.version}'.encode('utf-8')
            ).hexdigest()
            route = preferred_publish_route(site=site, company=company)
            if route is None:
                raise CustomerPoolError(
                    '没有可核验的联系方式：源表要求不导出或联系方式已失效，请人工处理。',
                    code='no_publishable_route',
                )
            result = publish_reviewed_customer(
                company_id=company_id,
                site=site,
                actor=actor,
                expected_version=state.version,
                idempotency_token=item_token,
                contact_point_id=route.point.pk,
                team=team,
            )
        except PermissionDenied:
            items.append(CustomerBulkPublishItemResult(
                company_id=company_id,
                company_name=company_name,
                status='failed',
                message='无权核验该客户或客户不在可核验范围。',
                workflow_code='permission_denied',
                route_label=route.label if route else '',
            ))
        except (Company.DoesNotExist, CompanyPoolState.DoesNotExist):
            items.append(CustomerBulkPublishItemResult(
                company_id=company_id,
                company_name=company_name,
                status='failed',
                message='客户不存在或已不在待核验范围。',
                workflow_code='not_found',
                route_label=route.label if route else '',
            ))
        except CustomerPoolError as error:
            items.append(CustomerBulkPublishItemResult(
                company_id=company_id,
                company_name=company_name,
                status='failed',
                message='；'.join(error.messages),
                workflow_code=error.workflow_code,
                route_label=route.label if route else '',
            ))
        else:
            items.append(CustomerBulkPublishItemResult(
                company_id=company_id,
                company_name=result.company.name,
                status='published',
                message='已核验并发布到公海。' if not result.replayed else '该发布请求已处理。',
                workflow_code='published',
                route_label=route.label,
                replayed=result.replayed,
            ))
    return CustomerBulkPublishResult(items=tuple(items))
