from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from console.capabilities import (
    DataScope,
    SalesCapability,
    can_sales,
    require_sales,
    sales_capability_scope,
)

from .models import (
    Activity,
    Company,
    CompanyContactPoint,
    CompanyPoolState,
    Contact,
    CrmMutationReceipt,
    CustomerSource,
    LeadConversion,
    LeadSubmission,
    Opportunity,
    SalesTeam,
    SalesTeamMember,
    Task,
)


SOURCE_TYPES = frozenset({'manual', 'research', 'website_form', 'meta_native', 'other'})
INTAKE_METHODS = frozenset({'manual_create', 'file_import', 'automatic_receive'})
ACTIONABLE_TASK_STATUSES = frozenset({'open', 'in_progress'})
ACTIONABLE_OPPORTUNITY_STAGES = frozenset({'qualification', 'discovery', 'solution', 'quotation', 'negotiation', 'on_hold'})
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9._:-]{16,200}$')


class CustomerPoolError(ValidationError):
    def __init__(self, message: str, *, code: str = 'customer_pool_invalid'):
        super().__init__(message, code=code)
        self.workflow_code = code


class CustomerPoolConflict(CustomerPoolError):
    def __init__(self, message: str = '该客户已被其他销售领取，请刷新后重试。'):
        super().__init__(message, code='customer_pool_conflict')


@dataclass(frozen=True, slots=True)
class CustomerMutationResult:
    company: Company
    pool_state: CompanyPoolState
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class CustomerBulkClaimItemResult:
    company_id: int
    status: str
    message: str
    workflow_code: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class CustomerBulkClaimResult:
    items: tuple[CustomerBulkClaimItemResult, ...]

    @property
    def succeeded(self) -> int:
        return sum(item.status == 'claimed' for item in self.items)

    @property
    def failed(self) -> int:
        return sum(item.status == 'failed' for item in self.items)


def normalize_company_name(value: Any) -> str:
    return ' '.join(str(value or '').strip().casefold().split())


def normalize_email(value: Any) -> str:
    return str(value or '').strip().casefold()


def normalize_phone(value: Any) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    prefix = '+' if raw.startswith('+') else ''
    return prefix + ''.join(character for character in raw if character.isdigit())


def _clean(value: Any, *, field: str, maximum: int, required: bool = False) -> str:
    result = str(value or '').strip()
    if required and not result:
        raise CustomerPoolError(f'{field}不能为空。', code=f'{field}_required')
    if len(result) > maximum:
        raise CustomerPoolError(f'{field}不能超过 {maximum} 个字符。', code=f'{field}_too_long')
    return result


def _hash_payload(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _token_hash(token: str) -> str:
    value = str(token or '').strip()
    if not TOKEN_PATTERN.fullmatch(value):
        raise CustomerPoolError('请求令牌无效，请刷新后重试。', code='idempotency_token_invalid')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _receipt(*, site, actor, scope: str, token: str, payload: dict[str, Any]) -> tuple[CrmMutationReceipt, bool]:
    token_hash = _token_hash(token)
    fingerprint_hash = _hash_payload(payload)
    try:
        receipt, created = CrmMutationReceipt.objects.get_or_create(
            site=site,
            actor_user=actor,
            mutation_scope=scope,
            token_hash=token_hash,
            defaults={'fingerprint_hash': fingerprint_hash, 'status': 'processing'},
        )
    except IntegrityError:
        receipt = CrmMutationReceipt.objects.select_for_update().get(
            site=site,
            actor_user=actor,
            mutation_scope=scope,
            token_hash=token_hash,
        )
        created = False
    if created:
        return receipt, False
    receipt = CrmMutationReceipt.objects.select_for_update().get(pk=receipt.pk)
    if receipt.fingerprint_hash != fingerprint_hash:
        raise CustomerPoolError('同一请求令牌已用于不同内容。', code='idempotency_conflict')
    if receipt.status != 'succeeded' or receipt.entity_id is None:
        raise CustomerPoolError('相同请求正在处理中，请稍后重试。', code='idempotency_in_progress')
    return receipt, True


def _finish_receipt(receipt: CrmMutationReceipt, *, company: Company, result: dict[str, Any]) -> None:
    receipt.status = 'succeeded'
    receipt.entity_table = 'crm_company'
    receipt.entity_id = company.pk
    receipt.result_json = result
    receipt.save(update_fields=['status', 'entity_table', 'entity_id', 'result_json', 'updated_at'])


def _team_for_actor(actor, *, requested_team: SalesTeam | None = None) -> SalesTeam:
    if requested_team is not None:
        if not requested_team.enabled:
            raise CustomerPoolError('销售团队已停用。', code='team_disabled')
        if sales_capability_scope(actor, SalesCapability.POOL_CLAIM) is DataScope.ALL:
            return requested_team
        if not SalesTeamMember.objects.filter(team=requested_team, user=actor).exists():
            raise PermissionDenied('当前账号不属于所选销售团队。')
        return requested_team
    memberships = list(
        SalesTeamMember.objects.select_related('team')
        .filter(user=actor, team__enabled=True)
        .order_by('team_id')[:2]
    )
    if not memberships:
        raise CustomerPoolError('当前账号未加入可用销售团队。', code='team_required')
    if len(memberships) > 1:
        raise CustomerPoolError('当前账号属于多个团队，请明确选择领取团队。', code='team_ambiguous')
    return memberships[0].team


def _review_team_for_actor(actor) -> SalesTeam | None:
    memberships = list(
        SalesTeamMember.objects.select_related('team')
        .filter(user=actor, team__enabled=True)
        .order_by('team_id')[:2]
    )
    if len(memberships) == 1:
        return memberships[0].team
    if sales_capability_scope(actor, SalesCapability.POOL_REVIEW) is DataScope.ALL and not memberships:
        return None
    if not memberships:
        raise CustomerPoolError('当前账号未加入可用销售团队。', code='team_required')
    raise CustomerPoolError('当前账号属于多个团队，请由管理员明确分诊团队。', code='team_ambiguous')


def _append_contact_points(*, company: Company, contact: Contact | None, email: str, phone: str, website: str) -> None:
    values = (
        ('email', email, normalize_email(email)),
        ('phone', phone, normalize_phone(phone)),
        ('website', website, website.strip().casefold()),
    )
    for channel, raw, normalized in values:
        if not raw:
            continue
        CompanyContactPoint.objects.get_or_create(
            site=company.site,
            company=company,
            channel=channel,
            normalized_value=normalized,
            extension='',
            defaults={
                'contact': contact,
                'raw_value': raw,
                'purpose': 'business',
                'status': 'unverified',
                'usage_status': 'unknown',
                'evidence_json': {},
            },
        )


@transaction.atomic
def create_manual_customer(
    *,
    site,
    actor,
    idempotency_token: str,
    company_name: str,
    country: str = '',
    city: str = '',
    industry: str = '',
    website: str = '',
    contact_name: str = '',
    email: str = '',
    phone: str = '',
    notes: str = '',
    source_type: str = 'manual',
    source_detail: str = 'CRM 手动创建',
    assignment_mode: str = 'review',
    team: SalesTeam | None = None,
) -> CustomerMutationResult:
    require_sales(actor, SalesCapability.POOL_CREATE)
    name = _clean(company_name, field='企业名称', maximum=300, required=True)
    resolved_source = str(source_type or '').strip()
    if resolved_source not in {'manual', 'research'}:
        raise CustomerPoolError('手动创建仅允许手动录入或研究开发来源。', code='source_type_invalid')
    resolved_email = _clean(email, field='邮箱', maximum=320)
    resolved_phone = _clean(phone, field='电话', maximum=80)
    if not resolved_email and not resolved_phone:
        raise CustomerPoolError('请至少填写一种邮箱或电话联系方式。', code='contact_route_required')
    resolved_assignment = str(assignment_mode or 'review').strip().casefold()
    if resolved_assignment not in {'review', 'self'}:
        raise CustomerPoolError('客户分诊方式无效。', code='assignment_mode_invalid')
    payload = {
        'site_id': site.pk,
        'company_name': name,
        'country': str(country or '').strip(),
        'city': str(city or '').strip(),
        'industry': str(industry or '').strip(),
        'website': str(website or '').strip(),
        'contact_name': str(contact_name or '').strip(),
        'email': resolved_email,
        'phone': resolved_phone,
        'notes': str(notes or '').strip(),
        'source_type': resolved_source,
        'source_detail': str(source_detail or '').strip(),
        'assignment_mode': resolved_assignment,
        'team_id': getattr(team, 'pk', None),
    }
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        scope='customer_pool.manual_create',
        token=idempotency_token,
        payload=payload,
    )
    if replayed:
        company = Company.objects.get(pk=receipt.entity_id, site=site)
        return CustomerMutationResult(company=company, pool_state=company.pool_state, replayed=True)

    duplicate_query = Company.objects.filter(
        site=site,
        normalized_name=normalize_company_name(name),
        country__iexact=str(country or '').strip(),
    )
    if str(website or '').strip():
        duplicate_query = duplicate_query | Company.objects.filter(
            site=site,
            website__iexact=str(website or '').strip(),
        )
    if duplicate_query.exists():
        raise CustomerPoolError(
            '发现可能重复的企业，请先搜索现有记录并由有权人员核验，不会自动合并。',
            code='duplicate_candidate',
        )

    resolved_team = (
        _team_for_actor(actor, requested_team=team)
        if resolved_assignment == 'self'
        else team or _review_team_for_actor(actor)
    )
    if resolved_team is not None and resolved_team.site_id != site.pk:
        raise CustomerPoolError('销售团队与客户站点不一致。', code='team_site_mismatch')
    company = Company.objects.create(
        site=site,
        owner_user=actor if resolved_assignment == 'self' else None,
        team=resolved_team,
        name=name,
        normalized_name=normalize_company_name(name),
        website=_clean(website, field='网站', maximum=500),
        industry=_clean(industry, field='行业', maximum=160),
        country=_clean(country, field='国家/地区', maximum=120),
        city=_clean(city, field='城市', maximum=120),
        status='prospect',
        source_channel=resolved_source,
        notes=_clean(notes, field='备注', maximum=4000),
    )
    pool_state = CompanyPoolState.objects.create(
        company=company,
        state='owned' if resolved_assignment == 'self' else 'review',
        evidence_status='declared' if resolved_assignment == 'self' else 'pending',
        claimed_at=timezone.now() if resolved_assignment == 'self' else None,
    )
    contact = None
    resolved_contact_name = _clean(contact_name, field='联系人姓名', maximum=200)
    if resolved_contact_name:
        contact = Contact.objects.create(
            site=site,
            company=company,
            owner_user=company.owner_user,
            team=company.team,
            full_name=resolved_contact_name,
            email=resolved_email,
            email_normalized=normalize_email(resolved_email),
            phone=resolved_phone,
            phone_normalized=normalize_phone(resolved_phone),
            country=company.country,
            status='active',
        )
    _append_contact_points(
        company=company,
        contact=contact,
        email=resolved_email,
        phone=resolved_phone,
        website=company.website,
    )
    CustomerSource.objects.create(
        site=site,
        company=company,
        source_type=resolved_source,
        intake_method='manual_create',
        source_detail=_clean(source_detail, field='来源详情', maximum=1000),
        responsible_user=actor,
        responsible_team=resolved_team,
        evidence_status='declared',
        evidence_json={'declared_by_user_id': actor.pk},
    )
    Activity.objects.create(
        site=site,
        company=company,
        actor_user=actor,
        activity_type='system',
        direction='internal',
        subject='创建客户',
        body='通过 CRM 手动创建客户记录。',
        metadata_json={'source_type': resolved_source, 'pool_state': pool_state.state, 'assignment_mode': resolved_assignment},
        occurred_at=timezone.now(),
    )
    _finish_receipt(receipt, company=company, result={'company_id': company.pk, 'pool_state': pool_state.state})
    return CustomerMutationResult(company=company, pool_state=pool_state)


@transaction.atomic
def publish_reviewed_customer(
    *,
    company_id: int,
    site,
    actor,
    expected_version: int,
    idempotency_token: str,
    contact_point_id: int,
    team: SalesTeam,
) -> CustomerMutationResult:
    company = Company.objects.select_for_update().get(pk=company_id, site=site)
    require_sales(actor, SalesCapability.POOL_REVIEW, record=company)
    if not team.enabled or team.site_id != site.pk:
        raise CustomerPoolError('请选择当前站点的可用销售团队。', code='team_invalid')
    if sales_capability_scope(actor, SalesCapability.POOL_REVIEW) is not DataScope.ALL:
        if company.team_id != team.pk:
            raise PermissionDenied('销售经理不能把待核验客户发布到其他团队。')
        _team_for_actor(actor, requested_team=team)
    payload = {
        'site_id': site.pk,
        'company_id': company_id,
        'expected_version': expected_version,
        'contact_point_id': contact_point_id,
        'team_id': team.pk,
    }
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        scope='customer_pool.review_publish',
        token=idempotency_token,
        payload=payload,
    )
    if replayed:
        return CustomerMutationResult(company=company, pool_state=company.pool_state, replayed=True)
    pool_state = CompanyPoolState.objects.select_for_update().get(company=company)
    if pool_state.state != 'review' or pool_state.version != expected_version:
        raise CustomerPoolConflict('客户核验状态已经变化，请刷新后重试。')
    point = CompanyContactPoint.objects.select_for_update().filter(
        pk=contact_point_id,
        site=site,
        company=company,
    ).first()
    if (
        point is None
        or point.status in {'invalid', 'do_not_contact'}
        or point.usage_status == 'restricted'
    ):
        raise CustomerPoolError('请选择一条可核验且未被禁止使用的联系方式。', code='contact_point_invalid')
    now = timezone.now()
    evidence = dict(point.evidence_json or {})
    evidence['reviewed_by_user_id'] = actor.pk
    evidence['reviewed_at'] = now.isoformat()
    point.status = 'active'
    point.usage_status = 'permitted'
    point.evidence_json = evidence
    point.save(update_fields=['status', 'usage_status', 'evidence_json', 'updated_at'])
    company.owner_user = None
    company.team = team
    company.save(update_fields=['owner_user', 'team', 'updated_at'])
    pool_state.state = 'available'
    pool_state.evidence_status = 'verified'
    pool_state.published_at = now
    pool_state.version += 1
    pool_state.save(update_fields=['state', 'evidence_status', 'published_at', 'version', 'updated_at'])
    Activity.objects.create(
        site=site,
        company=company,
        actor_user=actor,
        activity_type='system',
        direction='internal',
        subject='核验并发布到客户公海',
        body='已确认团队和一条允许使用的联系方式。',
        metadata_json={'team_id': team.pk, 'contact_point_id': point.pk, 'version': pool_state.version},
        occurred_at=now,
    )
    _finish_receipt(
        receipt,
        company=company,
        result={'company_id': company.pk, 'pool_state': pool_state.state, 'version': pool_state.version},
    )
    return CustomerMutationResult(company=company, pool_state=pool_state)


@transaction.atomic
def archive_reviewed_customer(
    *,
    company_id: int,
    site,
    actor,
    expected_version: int,
    idempotency_token: str,
    reason: str,
) -> CustomerMutationResult:
    """Archive a review record or retire an unowned public-pool record.

    Review rejection changes the evidence decision to rejected. Retiring an
    already verified public-pool record preserves its evidence decision and is
    only allowed when no owner or actionable work remains.
    """
    company = Company.objects.select_for_update().get(pk=company_id, site=site)
    require_sales(actor, SalesCapability.POOL_REVIEW, record=company)
    resolved_reason = _clean(reason, field='归档原因', maximum=1000, required=True)
    payload = {
        'site_id': site.pk,
        'company_id': company_id,
        'expected_version': expected_version,
        'reason': resolved_reason,
    }
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        scope='customer_pool.review_archive',
        token=idempotency_token,
        payload=payload,
    )
    if replayed:
        return CustomerMutationResult(company=company, pool_state=company.pool_state, replayed=True)
    pool_state = CompanyPoolState.objects.select_for_update().get(company=company)
    original_state = pool_state.state
    if original_state not in {'review', 'available'} or pool_state.version != expected_version:
        raise CustomerPoolConflict('客户状态已经变化，请刷新后重试。')
    if original_state == 'available':
        if company.owner_user_id is not None:
            raise CustomerPoolConflict('客户归属状态异常，请刷新后重试。')
        if Task.objects.filter(company=company, status__in=ACTIONABLE_TASK_STATUSES).exists():
            raise CustomerPoolError('该客户仍有未完成任务，不能从公海归档。', code='active_tasks_block_archive')
        if Opportunity.objects.filter(
            company=company,
            stage__in=ACTIONABLE_OPPORTUNITY_STAGES,
        ).exists():
            raise CustomerPoolError('该客户仍有进行中的商机，不能从公海归档。', code='active_opportunities_block_archive')
    now = timezone.now()
    pool_state.state = 'archived'
    if original_state == 'review':
        pool_state.evidence_status = 'rejected'
    pool_state.archived_at = now
    pool_state.version += 1
    pool_state.save(
        update_fields=['state', 'evidence_status', 'archived_at', 'version', 'updated_at']
    )
    Activity.objects.create(
        site=site,
        company=company,
        actor_user=actor,
        activity_type='system',
        direction='internal',
        subject='核验未通过并归档' if original_state == 'review' else '从客户公海归档',
        body=resolved_reason,
        metadata_json={'version': pool_state.version, 'previous_state': original_state},
        occurred_at=now,
    )
    _finish_receipt(
        receipt,
        company=company,
        result={'company_id': company.pk, 'pool_state': pool_state.state, 'version': pool_state.version},
    )
    return CustomerMutationResult(company=company, pool_state=pool_state)


@transaction.atomic
def restore_archived_customer(
    *,
    company_id: int,
    site,
    actor,
    expected_version: int,
    idempotency_token: str,
    reason: str,
) -> CustomerMutationResult:
    company = Company.objects.select_for_update().get(pk=company_id, site=site)
    require_sales(actor, SalesCapability.POOL_REVIEW, record=company)
    resolved_reason = _clean(reason, field='恢复原因', maximum=1000, required=True)
    payload = {
        'site_id': site.pk,
        'company_id': company_id,
        'expected_version': expected_version,
        'reason': resolved_reason,
    }
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        scope='customer_pool.archive_restore',
        token=idempotency_token,
        payload=payload,
    )
    if replayed:
        return CustomerMutationResult(company=company, pool_state=company.pool_state, replayed=True)
    pool_state = CompanyPoolState.objects.select_for_update().get(company=company)
    if pool_state.state != 'archived' or pool_state.version != expected_version:
        raise CustomerPoolConflict('客户归档状态已经变化，请刷新后重试。')
    now = timezone.now()
    pool_state.state = 'review'
    pool_state.evidence_status = 'pending'
    pool_state.archived_at = None
    pool_state.version += 1
    pool_state.save(
        update_fields=['state', 'evidence_status', 'archived_at', 'version', 'updated_at']
    )
    Activity.objects.create(
        site=site,
        company=company,
        actor_user=actor,
        activity_type='system',
        direction='internal',
        subject='恢复到待核验',
        body=resolved_reason,
        metadata_json={'version': pool_state.version},
        occurred_at=now,
    )
    _finish_receipt(
        receipt,
        company=company,
        result={'company_id': company.pk, 'pool_state': pool_state.state, 'version': pool_state.version},
    )
    return CustomerMutationResult(company=company, pool_state=pool_state)


@transaction.atomic
def claim_customer(*, company_id: int, site, actor, expected_version: int, idempotency_token: str, team: SalesTeam | None = None) -> CustomerMutationResult:
    require_sales(actor, SalesCapability.POOL_CLAIM)
    payload = {'site_id': site.pk, 'company_id': company_id, 'expected_version': expected_version, 'team_id': getattr(team, 'pk', None)}
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        scope='customer_pool.claim',
        token=idempotency_token,
        payload=payload,
    )
    if replayed:
        company = Company.objects.get(pk=receipt.entity_id, site=site)
        return CustomerMutationResult(company=company, pool_state=company.pool_state, replayed=True)
    pool_state = CompanyPoolState.objects.select_for_update().select_related('company').get(company_id=company_id, company__site=site)
    company = Company.objects.select_for_update().get(pk=company_id, site=site)
    if pool_state.state != 'available' or company.owner_user_id is not None:
        raise CustomerPoolConflict()
    if pool_state.version != expected_version:
        raise CustomerPoolConflict('客户状态已经变化，请刷新后重试。')
    resolved_team = _team_for_actor(actor, requested_team=team)
    if company.team_id != resolved_team.pk and sales_capability_scope(
        actor, SalesCapability.POOL_CLAIM
    ) is not DataScope.ALL:
        raise PermissionDenied('该客户不属于当前账号可领取的团队公海。')
    contacts = Contact.objects.select_for_update().filter(company=company)
    conversions = LeadConversion.objects.select_for_update().filter(company=company)
    submission_ids = list(conversions.values_list('submission_id', flat=True))
    submissions = LeadSubmission.objects.select_for_update().filter(pk__in=submission_ids)
    contact_ids = list(contacts.values_list('pk', flat=True))
    opportunity_ids = list(
        Opportunity.objects.filter(company=company).values_list('pk', flat=True)
    )
    for label, queryset in (('联系人', contacts), ('关联询盘', submissions)):
        if queryset.exclude(site=site).exists() or queryset.exclude(
            Q(team_id=resolved_team.pk) | Q(team_id__isnull=True)
        ).exists():
            raise CustomerPoolError(
                f'{label}存在跨站点或跨团队记录，已停止领取。',
                code='claim_related_scope_invalid',
            )
    if Opportunity.objects.filter(
        pk__in=opportunity_ids,
        stage__in=ACTIONABLE_OPPORTUNITY_STAGES,
    ).exists() or Task.objects.filter(status__in=ACTIONABLE_TASK_STATUSES).filter(
        Q(company=company)
        | Q(contact_id__in=contact_ids)
        | Q(opportunity_id__in=opportunity_ids)
        | Q(submission_id__in=submission_ids)
    ).exists():
        raise CustomerPoolError(
            '该公海客户存在进行中的任务或商机，请由经理检查后分配。',
            code='claim_active_work_conflict',
        )
    now = timezone.now()
    company.owner_user = actor
    company.team = resolved_team
    company.save(update_fields=['owner_user', 'team', 'updated_at'])
    contact_count = contacts.update(owner_user=actor, team=resolved_team, updated_at=now)
    submission_count = submissions.update(assignee=actor, team=resolved_team, updated_at=now)
    pool_state.state = 'owned'
    pool_state.claimed_at = now
    pool_state.version += 1
    pool_state.save(update_fields=['state', 'claimed_at', 'version', 'updated_at'])
    Activity.objects.create(
        site=site,
        company=company,
        actor_user=actor,
        activity_type='assignment',
        direction='internal',
        subject='领取公海客户',
        body=f'客户由 {actor.get_username()} 领取。',
        metadata_json={
            'team_id': resolved_team.pk,
            'version': pool_state.version,
            'contacts_assigned': contact_count,
            'submissions_assigned': submission_count,
        },
        occurred_at=now,
    )
    _finish_receipt(receipt, company=company, result={'company_id': company.pk, 'pool_state': pool_state.state, 'version': pool_state.version})
    return CustomerMutationResult(company=company, pool_state=pool_state)


def claim_customers_bulk(
    *,
    site,
    actor,
    selections: list[tuple[int, int]],
    idempotency_token: str,
    team: SalesTeam | None = None,
) -> CustomerBulkClaimResult:
    """Claim an explicit list while keeping each company as its own transaction.

    A conflict on one company must not roll back successful claims for other
    explicitly selected companies. The per-company token is derived from the
    batch token, so replaying the same selection is safe.
    """

    require_sales(actor, SalesCapability.POOL_CLAIM)
    batch_token_hash = _token_hash(idempotency_token)
    if not selections:
        raise CustomerPoolError('请至少选择一家客户。', code='bulk_selection_required')
    if len(selections) > 100:
        raise CustomerPoolError('单次最多领取 100 家客户。', code='bulk_selection_too_large')
    company_ids = [company_id for company_id, _version in selections]
    if any(company_id <= 0 or version <= 0 for company_id, version in selections):
        raise CustomerPoolError('批量领取内容无效，请刷新后重试。', code='bulk_selection_invalid')
    if len(company_ids) != len(set(company_ids)):
        raise CustomerPoolError('同一家客户不能重复选择。', code='bulk_selection_duplicate')

    resolved_team = _team_for_actor(actor, requested_team=team)
    items: list[CustomerBulkClaimItemResult] = []
    for company_id, expected_version in selections:
        item_token = 'bulk-claim:' + hashlib.sha256(
            f'{batch_token_hash}:{company_id}'.encode('utf-8')
        ).hexdigest()
        try:
            result = claim_customer(
                company_id=company_id,
                site=site,
                actor=actor,
                expected_version=expected_version,
                idempotency_token=item_token,
                team=resolved_team,
            )
        except PermissionDenied:
            items.append(CustomerBulkClaimItemResult(
                company_id=company_id,
                status='failed',
                message='无权领取或客户不在当前团队公海。',
                workflow_code='permission_denied',
            ))
        except (Company.DoesNotExist, CompanyPoolState.DoesNotExist):
            items.append(CustomerBulkClaimItemResult(
                company_id=company_id,
                status='failed',
                message='客户不存在或已不在可见范围。',
                workflow_code='not_found',
            ))
        except CustomerPoolError as error:
            items.append(CustomerBulkClaimItemResult(
                company_id=company_id,
                status='failed',
                message='；'.join(error.messages),
                workflow_code=error.workflow_code,
            ))
        else:
            items.append(CustomerBulkClaimItemResult(
                company_id=company_id,
                status='claimed',
                message='已领取。' if not result.replayed else '该领取请求已处理。',
                workflow_code='claimed',
                replayed=result.replayed,
            ))
    return CustomerBulkClaimResult(items=tuple(items))


@transaction.atomic
def assign_customer(
    *,
    company_id: int,
    site,
    actor,
    target_user,
    expected_version: int,
    idempotency_token: str,
    reason: str,
) -> CustomerMutationResult:
    """Assign or hand off one customer inside its existing sales team.

    Customer ownership, contacts, converted inbound submissions and active work
    move together. Completed tasks and closed opportunities remain historical;
    the activity record captures the exact mutation counts and previous owner.
    """

    company = (
        Company.objects.select_for_update(of=('self',))
        .select_related('team', 'owner_user')
        .get(pk=company_id, site=site)
    )
    require_sales(actor, SalesCapability.ASSIGN, record=company)
    resolved_reason = _clean(reason, field='分配/交接原因', maximum=1000, required=True)
    payload = {
        'site_id': site.pk,
        'company_id': company_id,
        'target_user_id': getattr(target_user, 'pk', None),
        'expected_version': expected_version,
        'reason': resolved_reason,
    }
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        scope='customer_pool.assign',
        token=idempotency_token,
        payload=payload,
    )
    if replayed:
        return CustomerMutationResult(company=company, pool_state=company.pool_state, replayed=True)

    pool_state = CompanyPoolState.objects.select_for_update().get(company=company)
    if pool_state.state not in {'available', 'owned'}:
        raise CustomerPoolError('只有待领取或已归属客户可以分配。', code='assignment_state_invalid')
    if pool_state.version != expected_version:
        raise CustomerPoolConflict('客户状态已经变化，请刷新后重试。')
    if company.team_id is None or not company.team.enabled or company.team.site_id != site.pk:
        raise CustomerPoolError('客户没有可用的销售团队，请先完成核验。', code='assignment_team_invalid')
    if pool_state.state == 'available' and company.owner_user_id is not None:
        raise CustomerPoolConflict('客户负责人和公海状态不一致，请停止操作并检查。')
    if pool_state.state == 'owned' and company.owner_user_id is None:
        raise CustomerPoolConflict('客户负责人和公海状态不一致，请停止操作并检查。')
    if target_user is None or not getattr(target_user, 'is_active', False):
        raise CustomerPoolError('目标负责人不存在或已停用。', code='assignee_inactive')
    if not SalesTeamMember.objects.filter(
        team=company.team,
        user=target_user,
        team__site=site,
        team__enabled=True,
    ).exists():
        raise CustomerPoolError('目标负责人必须是客户所在团队的有效成员。', code='assignee_team_invalid')
    if target_user.pk == company.owner_user_id:
        raise CustomerPoolError('目标负责人没有变化。', code='assignee_unchanged')

    contacts = Contact.objects.select_for_update().filter(company=company)
    contact_ids = list(contacts.values_list('pk', flat=True))
    opportunity_ids = list(
        Opportunity.objects.filter(company=company).values_list('pk', flat=True)
    )
    active_opportunities = Opportunity.objects.select_for_update().filter(
        pk__in=opportunity_ids,
        stage__in=ACTIONABLE_OPPORTUNITY_STAGES,
    )
    conversions = LeadConversion.objects.select_for_update().filter(company=company)
    submission_ids = list(conversions.values_list('submission_id', flat=True))
    submissions = LeadSubmission.objects.select_for_update().filter(pk__in=submission_ids)
    active_task_ids = list(
        Task.objects.filter(status__in=ACTIONABLE_TASK_STATUSES)
        .filter(
            Q(company=company)
            | Q(contact_id__in=contact_ids)
            | Q(opportunity_id__in=opportunity_ids)
            | Q(submission_id__in=submission_ids)
        )
        .values_list('pk', flat=True)
        .distinct()
    )
    active_tasks = Task.objects.select_for_update().filter(pk__in=active_task_ids)

    scoped_relations = (
        ('联系人', contacts),
        ('进行中商机', active_opportunities),
        ('未完成任务', active_tasks),
        ('关联询盘', submissions),
    )
    for label, queryset in scoped_relations:
        if queryset.exclude(site=site).exists() or queryset.exclude(
            Q(team_id=company.team_id) | Q(team_id__isnull=True)
        ).exists():
            raise CustomerPoolError(
                f'{label}存在跨站点或跨团队记录，已停止交接。',
                code='assignment_related_scope_invalid',
            )

    now = timezone.now()
    previous_owner = company.owner_user
    contact_count = contacts.update(
        owner_user=target_user,
        team=company.team,
        updated_at=now,
    )
    opportunity_count = active_opportunities.update(
        owner_user=target_user,
        team=company.team,
        updated_at=now,
    )
    task_count = active_tasks.update(
        owner_user=target_user,
        team=company.team,
        updated_at=now,
    )
    submission_count = submissions.update(
        assignee=target_user,
        team=company.team,
        updated_at=now,
    )

    company.owner_user = target_user
    company.save(update_fields=['owner_user', 'updated_at'])
    pool_state.state = 'owned'
    pool_state.claimed_at = now
    pool_state.version += 1
    pool_state.save(update_fields=['state', 'claimed_at', 'version', 'updated_at'])

    action_label = '分配客户' if previous_owner is None else '交接客户'
    Activity.objects.create(
        site=site,
        company=company,
        actor_user=actor,
        activity_type='assignment',
        direction='internal',
        subject=action_label,
        body=resolved_reason,
        metadata_json={
            'previous_owner_id': getattr(previous_owner, 'pk', None),
            'target_owner_id': target_user.pk,
            'team_id': company.team_id,
            'version': pool_state.version,
            'contacts_reassigned': contact_count,
            'submissions_reassigned': submission_count,
            'active_tasks_reassigned': task_count,
            'active_opportunities_reassigned': opportunity_count,
        },
        occurred_at=now,
    )
    _finish_receipt(
        receipt,
        company=company,
        result={
            'company_id': company.pk,
            'pool_state': pool_state.state,
            'version': pool_state.version,
            'target_owner_id': target_user.pk,
            'contacts_reassigned': contact_count,
            'submissions_reassigned': submission_count,
            'active_tasks_reassigned': task_count,
            'active_opportunities_reassigned': opportunity_count,
        },
    )
    return CustomerMutationResult(company=company, pool_state=pool_state)


@transaction.atomic
def release_customer(*, company_id: int, site, actor, expected_version: int, idempotency_token: str, reason: str) -> CustomerMutationResult:
    company = Company.objects.select_for_update().get(pk=company_id, site=site)
    require_sales(actor, SalesCapability.POOL_RELEASE, record=company)
    payload = {'site_id': site.pk, 'company_id': company_id, 'expected_version': expected_version, 'reason': str(reason or '').strip()}
    receipt, replayed = _receipt(
        site=site,
        actor=actor,
        scope='customer_pool.release',
        token=idempotency_token,
        payload=payload,
    )
    if replayed:
        return CustomerMutationResult(company=company, pool_state=company.pool_state, replayed=True)
    pool_state = CompanyPoolState.objects.select_for_update().get(company=company)
    if pool_state.state != 'owned' or pool_state.version != expected_version:
        raise CustomerPoolConflict('客户状态已经变化，请刷新后重试。')
    if Task.objects.filter(company=company, status__in=ACTIONABLE_TASK_STATUSES).exists():
        raise CustomerPoolError('该客户仍有未完成任务，不能释放到公海。', code='active_tasks_block_release')
    if Opportunity.objects.filter(company=company, stage__in=ACTIONABLE_OPPORTUNITY_STAGES).exists():
        raise CustomerPoolError('该客户仍有进行中的商机，不能释放到公海。', code='active_opportunities_block_release')
    resolved_reason = _clean(reason, field='释放原因', maximum=1000, required=True)
    now = timezone.now()
    company.owner_user = None
    company.save(update_fields=['owner_user', 'updated_at'])
    pool_state.state = 'available'
    pool_state.released_at = now
    pool_state.version += 1
    pool_state.save(update_fields=['state', 'released_at', 'version', 'updated_at'])
    Contact.objects.filter(company=company).update(owner_user=None, team=company.team, updated_at=now)
    Activity.objects.create(
        site=site,
        company=company,
        actor_user=actor,
        activity_type='assignment',
        direction='internal',
        subject='释放到客户公海',
        body=resolved_reason,
        metadata_json={'version': pool_state.version},
        occurred_at=now,
    )
    _finish_receipt(receipt, company=company, result={'company_id': company.pk, 'pool_state': pool_state.state, 'version': pool_state.version})
    return CustomerMutationResult(company=company, pool_state=pool_state)


def public_pool_queryset(*, site, actor):
    if not can_sales(actor, SalesCapability.POOL_READ):
        return Company.objects.none()
    queryset = (
        Company.objects.filter(site=site, owner_user__isnull=True, pool_state__state='available')
        .select_related('pool_state')
        .prefetch_related('customer_sources', 'contact_points')
        .order_by('-pool_state__published_at', '-id')
    )
    scope = sales_capability_scope(actor, SalesCapability.POOL_READ)
    if scope is DataScope.ALL:
        return queryset
    if scope is DataScope.TEAM:
        team_ids = SalesTeamMember.objects.filter(
            user=actor,
            membership_role='manager',
            team__enabled=True,
        ).values('team_id')
        return queryset.filter(team_id__in=team_ids)
    if scope is DataScope.OWN:
        team_ids = SalesTeamMember.objects.filter(
            user=actor,
            team__enabled=True,
        ).values('team_id')
        return queryset.filter(team_id__in=team_ids)
    return queryset.none()
