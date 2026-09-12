from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db.models import F, Q


CRM_TARGET_FIELDS = ('submission', 'company', 'contact', 'opportunity')


@dataclass(frozen=True)
class CrmRelationshipScope:
    """The site/team scope established by one set of linked CRM targets."""

    site_id: int
    team_id: int | None
    target_count: int


def linked_targets_present_q() -> Q:
    """Require at least one relationship target, including for legacy rows."""

    query = Q(pk__in=[])
    for field_name in CRM_TARGET_FIELDS:
        query |= Q(**{f'{field_name}_id__isnull': False})
    return query


def linked_targets_match_record_site_q() -> Q:
    """Hide legacy rows whose linked object belongs to another record site."""

    query = Q()
    for field_name in CRM_TARGET_FIELDS:
        query &= (
            Q(**{f'{field_name}_id__isnull': True})
            | Q(**{f'{field_name}__site_id': F('site_id')})
        )
    return query


def all_linked_targets_in_scope_q(
    *,
    submission_ids,
    company_ids,
    contact_ids,
    opportunity_ids,
) -> Q:
    """Require every non-null target to be contained by its allowed ID set.

    This is deliberately an AND of four null-or-visible predicates.  An OR at
    this boundary lets one authorized target disclose the metadata attached to
    another team, site, or data subject.
    """

    allowed_ids = {
        'submission': submission_ids,
        'company': company_ids,
        'contact': contact_ids,
        'opportunity': opportunity_ids,
    }
    query = linked_targets_present_q()
    for field_name, record_ids in allowed_ids.items():
        query &= (
            Q(**{f'{field_name}_id__isnull': True})
            | Q(**{f'{field_name}_id__in': record_ids})
        )
    return query


def _record_id(record) -> int | None:
    value = getattr(record, 'pk', None)
    return int(value) if value is not None else None


def _record_team_id(record) -> int | None:
    value = getattr(record, 'team_id', None)
    return int(value) if value is not None else None


def validate_crm_relationship_targets(
    *,
    site,
    submission=None,
    company=None,
    contact=None,
    opportunity=None,
    record_team=None,
) -> CrmRelationshipScope:
    """Validate a multi-target CRM relationship before any row/file write.

    Fail-closed team contract: a single teamless target remains valid for
    historical/unassigned work.  Once a record links multiple targets, every
    target must have the same non-null team.  We do not let a teamless object
    bridge otherwise scoped records because its missing team cannot prove
    containment.  A Task's own team, when supplied, must exactly match the
    target scope.
    """

    site_id = _record_id(site)
    if site_id is None:
        raise ValidationError('销售关系必须属于已保存的站点。')

    targets = {
        'submission': submission,
        'company': company,
        'contact': contact,
        'opportunity': opportunity,
    }
    targets = {name: record for name, record in targets.items() if record is not None}
    if not targets:
        raise ValidationError('销售记录必须关联线索、企业、联系人或项目。')

    team_ids: list[int | None] = []
    for record in targets.values():
        if _record_id(record) is None:
            raise ValidationError('销售关系不能关联尚未保存的对象。')
        if getattr(record, 'site_id', None) != site_id:
            raise ValidationError('销售关系包含不属于当前站点的对象。')
        team_ids.append(_record_team_id(record))

    if len(targets) > 1:
        if any(team_id is None for team_id in team_ids):
            raise ValidationError('多对象销售关系要求每个关联对象都有明确且一致的销售团队。')
        if len(set(team_ids)) != 1:
            raise ValidationError('销售关系不能跨越不同销售团队。')
    resolved_team_id = team_ids[0] if team_ids else None

    if record_team is not None:
        record_team_id = _record_id(record_team)
        if record_team_id is None or getattr(record_team, 'site_id', None) != site_id:
            raise ValidationError('销售记录团队不属于当前站点。')
        if resolved_team_id is None or record_team_id != resolved_team_id:
            raise ValidationError('销售记录团队必须与所有关联对象的团队一致。')

    # Enforce the explicit relationships the CRM models can prove.  These
    # checks intentionally do not infer a relationship merely because two
    # targets happen to share a site/team.
    if company is not None and contact is not None:
        if getattr(contact, 'company_id', None) != _record_id(company):
            raise ValidationError('联系人不属于关联企业。')
    if company is not None and opportunity is not None:
        if getattr(opportunity, 'company_id', None) != _record_id(company):
            raise ValidationError('项目不属于关联企业。')
    if contact is not None and opportunity is not None:
        contact_company_id = getattr(contact, 'company_id', None)
        opportunity_company_id = getattr(opportunity, 'company_id', None)
        if contact_company_id is None or contact_company_id != opportunity_company_id:
            raise ValidationError('联系人和项目不属于同一企业。')
    if submission is not None and opportunity is not None:
        if getattr(opportunity, 'source_submission_id', None) != _record_id(submission):
            raise ValidationError('项目不是由关联线索转换而来。')

    return CrmRelationshipScope(
        site_id=site_id,
        team_id=resolved_team_id,
        target_count=len(targets),
    )
