"""Read-only customer overview; the union must never broaden role scopes."""
import re

from django.db.models import Q

from leads.customer_pool_services import public_pool_queryset
from leads.customer_value_engine import VALUE_CEILING
from leads.models import Company, CustomerImportRow
from .access import crm_owned_queryset_for_user
from .capabilities import DataScope, SalesCapability, can_sales, sales_capability_scope


def parse_value_bound(raw):
    text = str(raw or '').strip()
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float('inf'), float('-inf')):
        return None
    return round(max(0.0, min(float(VALUE_CEILING), number)), 1)


POOL_STATE_LABELS = {
    'available': '待领取', 'owned': '已归属', 'review': '待核验',
    'archived': '已归档', 'unmanaged': '未纳入公海',
}


COUNTRY_CODE_PATTERN = re.compile(r'^[A-Za-z]{2}$')
MAX_COUNTRY_CODES = 40


def parse_country_filter(values):
    """国家筛选＝国家代码多选；顺手兼容旧书签里的国名写法（走名称包含匹配）。"""

    codes = []
    terms = []
    for raw in values:
        token = ' '.join(str(raw or '').split())[:60]
        if not token:
            continue
        if COUNTRY_CODE_PATTERN.match(token):
            code = token.upper()
            if code not in codes:
                codes.append(code)
        elif token not in terms:
            terms.append(token)
    return codes[:MAX_COUNTRY_CODES], terms[:5]


def country_condition(*, codes, terms, code_field, name_field):
    condition = Q()
    if codes:
        condition |= Q(**{f'{code_field}__in': codes})
    for term in terms:
        condition |= Q(**{f'{name_field}__icontains': term})
    return condition


def normalize_source_filters(source, intake):
    sources = {'manual', 'research', 'website_form', 'meta_native', 'other'}
    methods = {'manual_create', 'file_import', 'automatic_receive'}
    return (source if source in sources else 'all', intake if intake in methods else 'all')


def all_customers_queryset(*, site, user):
    base = Company.objects.filter(site=site)
    scope = sales_capability_scope(user, SalesCapability.POOL_READ)
    if scope is DataScope.ALL:
        return base
    if scope is DataScope.NONE:
        return base.none()
    owned = crm_owned_queryset_for_user(base.filter(pool_state__state='owned'), user)
    available = public_pool_queryset(site=site, actor=user)
    visible = Q(pk__in=owned.values('pk')) | Q(pk__in=available.values('pk'))
    if can_sales(user, SalesCapability.POOL_REVIEW):
        review = crm_owned_queryset_for_user(
            base.filter(pool_state__state__in=('review', 'archived')), user,
        )
        visible |= Q(pk__in=review.values('pk'))
    return base.filter(visible)


def filter_pool_state(queryset, state):
    if state == 'unmanaged':
        return queryset.filter(pool_state__isnull=True)
    if state in POOL_STATE_LABELS:
        return queryset.filter(pool_state__state=state)
    return queryset


def filter_pool_row_state(rows, state):
    if state == 'unmanaged':
        return rows.filter(company__pool_state__isnull=True)
    if state in POOL_STATE_LABELS:
        return rows.filter(company__pool_state__state=state)
    return rows


def filter_import_batch(queryset, batch_id):
    # Rows retain every processed batch, including unchanged matches. Keep the
    # original evidence lookup for older records without import-row provenance.
    companies = CustomerImportRow.objects.filter(
        batch_id=batch_id, status__in=('imported', 'unchanged'),
        batch__status__in=('succeeded', 'partial'),
    ).values('company_id')
    return queryset.filter(Q(pk__in=companies) | Q(customer_sources__evidence_json__batch_id=batch_id)).distinct()
