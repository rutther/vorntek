from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .crm_relationships import validate_crm_relationship_targets
from .models import (
    Activity,
    Company,
    CompanyContactPoint,
    CompanyPoolState,
    Contact,
    CustomerSource,
    LeadConversion,
    LeadSubmission,
    Opportunity,
    SalesTeamMember,
    Task,
)


ACTIVE_OPPORTUNITY_STAGES = (
    'qualification',
    'discovery',
    'solution',
    'quotation',
    'negotiation',
)
TERMINAL_OPPORTUNITY_STAGES = frozenset({'won', 'lost'})
OPPORTUNITY_STAGES = frozenset({
    *ACTIVE_OPPORTUNITY_STAGES,
    'on_hold',
    *TERMINAL_OPPORTUNITY_STAGES,
})

# This is the domain workflow, rather than a UI hint.  Active work advances or
# retreats one discovery step at a time; hold/loss are exits available from any
# active stage; and a closed opportunity may only be explicitly reopened.
OPPORTUNITY_STAGE_TRANSITIONS = {
    'qualification': frozenset({'discovery', 'on_hold', 'lost'}),
    'discovery': frozenset({'qualification', 'solution', 'on_hold', 'lost'}),
    'solution': frozenset({'discovery', 'quotation', 'on_hold', 'lost'}),
    'quotation': frozenset({'solution', 'negotiation', 'on_hold', 'won', 'lost'}),
    'negotiation': frozenset({'quotation', 'on_hold', 'won', 'lost'}),
    'on_hold': frozenset({*ACTIVE_OPPORTUNITY_STAGES, 'lost'}),
    'won': frozenset({*ACTIVE_OPPORTUNITY_STAGES, 'on_hold'}),
    'lost': frozenset({*ACTIVE_OPPORTUNITY_STAGES, 'on_hold'}),
}
OPPORTUNITY_STAGE_DEFAULT_PROBABILITIES = {
    'qualification': 10,
    'discovery': 20,
    'solution': 40,
    'quotation': 60,
    'negotiation': 80,
    # A hold preserves a meaningful in-flight probability.  Ten is the safe
    # fallback when a terminal 0/100 value cannot be preserved on reopening.
    'on_hold': 10,
}
ACTIONABLE_TASK_STATUSES = ('open', 'in_progress')
TERMINAL_TASK_STATUSES = frozenset({'completed', 'canceled'})


@dataclass(frozen=True)
class LeadConversionResult:
    conversion: LeadConversion
    company: Company
    contact: Contact
    opportunity: Opportunity
    created: bool


def normalize_company_name(value: str) -> str:
    return ' '.join((value or '').strip().casefold().split())


def normalize_email(value: str) -> str:
    return (value or '').strip().casefold()


def normalize_phone(value: str) -> str:
    text = (value or '').strip()
    if not text:
        return ''
    prefix = '+' if text.startswith('+') else ''
    digits = re.sub(r'\D+', '', text)
    return f'{prefix}{digits}' if digits else ''


def append_activity(
    *,
    site,
    actor,
    activity_type: str,
    subject: str = '',
    body: str = '',
    direction: str = 'internal',
    submission: LeadSubmission | None = None,
    company: Company | None = None,
    contact: Contact | None = None,
    opportunity: Opportunity | None = None,
    metadata: dict | None = None,
    occurred_at=None,
) -> Activity:
    validate_crm_relationship_targets(
        site=site,
        submission=submission,
        company=company,
        contact=contact,
        opportunity=opportunity,
    )
    return Activity.objects.create(
        site=site,
        submission=submission,
        company=company,
        contact=contact,
        opportunity=opportunity,
        actor_user=actor if getattr(actor, 'pk', None) else None,
        activity_type=activity_type,
        direction=direction,
        subject=(subject or '').strip(),
        body=(body or '').strip(),
        metadata_json=metadata or {},
        occurred_at=occurred_at or timezone.now(),
    )


def create_task(
    *,
    site,
    owner_user,
    title: str,
    due_at,
    submission: LeadSubmission | None = None,
    company: Company | None = None,
    contact: Contact | None = None,
    opportunity: Opportunity | None = None,
    team=None,
    created_by_user=None,
    description: str = '',
    task_type: str = 'follow_up',
    priority: str = 'normal',
    status: str = 'open',
    reminder_at=None,
    completed_at=None,
    outcome: str = '',
) -> Task:
    """Create a Task only after its complete relationship scope is proven."""

    relationship_scope = validate_crm_relationship_targets(
        site=site,
        submission=submission,
        company=company,
        contact=contact,
        opportunity=opportunity,
        record_team=team,
    )
    if team is None and relationship_scope.team_id is not None:
        # Do not create a teamless Task for a team-scoped target: it becomes a
        # scope orphan in manager queues.  Reuse the already validated target
        # relationship instead of accepting a second caller-controlled team.
        team = next(
            record.team
            for record in (submission, company, contact, opportunity)
            if record is not None and record.team_id == relationship_scope.team_id
        )
    return Task.objects.create(
        site=site,
        submission=submission,
        company=company,
        contact=contact,
        opportunity=opportunity,
        owner_user=owner_user,
        team=team,
        created_by_user=created_by_user,
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        status=status,
        due_at=due_at,
        reminder_at=reminder_at,
        completed_at=completed_at,
        outcome=outcome,
    )


def _existing_conversion_result(conversion: LeadConversion) -> LeadConversionResult:
    if not all((conversion.company_id, conversion.contact_id, conversion.opportunity_id)):
        raise ValidationError('已有转换记录不完整，请由系统管理员检查。')
    return LeadConversionResult(
        conversion=conversion,
        company=conversion.company,
        contact=conversion.contact,
        opportunity=conversion.opportunity,
        created=False,
    )


def _conversion_owner(*, submission: LeadSubmission):
    """Resolve a safe CRM owner before conversion creates related records.

    A historical lead may still point at a deactivated account or at a user who
    no longer belongs to its team.  Falling back to the actor is equally unsafe:
    Company, Contact, Opportunity and follow-up Task would then disagree about
    their accountable sales owner.  Keep existing conversions readable, but
    require explicit reassignment before materializing any new CRM records.
    """

    owner = submission.assignee
    if owner is None or not getattr(owner, 'is_active', False):
        raise ValidationError('线索负责人不可用，请先重新分配有效负责人再转换。')
    team = submission.team
    if (
        team is None
        or team.site_id != submission.site_id
        or not team.enabled
        or not SalesTeamMember.objects.filter(
            team_id=team.pk,
            user_id=owner.pk,
        ).exists()
    ):
        raise ValidationError('线索负责人必须是所属销售团队的有效成员，请先重新分配后再转换。')
    return owner


@transaction.atomic
def convert_qualified_lead(
    *,
    submission_id: int,
    actor,
    opportunity_name: str = '',
    locked_submission_authorizer: Callable[[LeadSubmission], object] | None = None,
) -> LeadConversionResult:
    submission = (
        LeadSubmission.objects.select_for_update(of=('self',))
        .select_related('site', 'locale', 'assignee', 'team')
        .get(pk=submission_id)
    )
    if locked_submission_authorizer is not None:
        locked_submission_authorizer(submission)
    existing = (
        LeadConversion.objects.select_related('company', 'contact', 'opportunity')
        .filter(submission=submission)
        .first()
    )
    if existing:
        return _existing_conversion_result(existing)
    if submission.stage not in {'qualified', 'won'}:
        raise ValidationError('只有已合格或已成交线索可以转换为企业、联系人和项目。')

    company_name = (submission.company or '').strip()
    contact_name = (submission.full_name or '').strip()
    if not company_name:
        raise ValidationError('转换前必须确认企业名称。')
    if not contact_name:
        raise ValidationError('转换前必须确认联系人姓名。')

    # Resolve and validate the CRM owner before Company, Contact, Opportunity,
    # conversion, activity or task rows are written.  Any invalid legacy
    # assignment therefore fails as an all-or-nothing domain operation.
    conversion_owner = _conversion_owner(submission=submission)

    normalized_name = normalize_company_name(company_name)
    company = (
        Company.objects.filter(
            site=submission.site,
            normalized_name=normalized_name,
            country__iexact=(submission.country or '').strip(),
        )
        .order_by('id')
        .first()
    )
    if company is None:
        company = Company.objects.create(
            site=submission.site,
            owner_user=conversion_owner,
            team=submission.team,
            name=company_name,
            normalized_name=normalized_name,
            country=(submission.country or '').strip(),
            source_channel=submission.source_channel,
        )

    email_normalized = normalize_email(submission.email)
    phone_normalized = normalize_phone(submission.phone)
    contact_query = Contact.objects.filter(site=submission.site, company=company)
    identity = Q()
    if email_normalized:
        identity |= Q(email_normalized=email_normalized)
    if phone_normalized:
        identity |= Q(phone_normalized=phone_normalized)
    contact = contact_query.filter(identity).order_by('id').first() if identity else None
    if contact is None:
        contact = Contact.objects.create(
            site=submission.site,
            company=company,
            owner_user=conversion_owner,
            team=submission.team,
            full_name=contact_name,
            email=(submission.email or '').strip(),
            email_normalized=email_normalized,
            phone=(submission.phone or '').strip(),
            phone_normalized=phone_normalized,
            whatsapp_phone=(submission.phone or '').strip(),
            country=(submission.country or '').strip(),
            preferred_language=(submission.locale.locale_code if submission.locale_id else ''),
        )

    product_scope = str((submission.payload_json or {}).get('product_category') or '').strip()
    capacity_target = str((submission.payload_json or {}).get('capacity') or '').strip()
    resolved_opportunity_name = (opportunity_name or '').strip()
    if not resolved_opportunity_name:
        resolved_opportunity_name = f'{company.name} - {product_scope or "Beverage line project"}'
    _set_stage_audit_context(actor, '线索转换后建立销售项目')
    opportunity = Opportunity.objects.create(
        site=submission.site,
        company=company,
        primary_contact=contact,
        source_submission=submission,
        owner_user=conversion_owner,
        team=submission.team,
        name=resolved_opportunity_name,
        stage='qualification',
        product_scope=product_scope,
        capacity_target=capacity_target,
        source_channel=submission.source_channel,
        source_detail=submission.source_detail,
        next_follow_up_at=submission.next_follow_up_at,
    )
    conversion = LeadConversion.objects.create(
        submission=submission,
        company=company,
        contact=contact,
        opportunity=opportunity,
        converted_by_user=actor if getattr(actor, 'pk', None) else None,
        metadata_json={
            'source_channel': submission.source_channel,
            'source_detail': submission.source_detail,
            'utm': submission.utm_json or {},
            'identifiers': submission.identifiers_json or {},
        },
    )
    pool_state, _pool_created = CompanyPoolState.objects.select_for_update().get_or_create(
        company=company,
        defaults={
            'state': 'owned',
            'evidence_status': 'verified',
            'claimed_at': timezone.now(),
        },
    )
    if pool_state.state == 'available' and company.owner_user_id is None:
        company.owner_user = conversion_owner
        company.team = submission.team
        company.save(update_fields=['owner_user', 'team', 'updated_at'])
        pool_state.state = 'owned'
        pool_state.claimed_at = timezone.now()
        pool_state.version += 1
        pool_state.save(update_fields=['state', 'claimed_at', 'version', 'updated_at'])
    source_type = (
        'meta_native'
        if (submission.payload_json or {}).get('origin') == 'meta_instant_form'
        or submission.source_channel in {'meta_native', 'meta_lead_ads'}
        else 'website_form'
    )
    CustomerSource.objects.get_or_create(
        site=submission.site,
        source_type=source_type,
        external_record_id=f'lead_submission:{submission.pk}',
        defaults={
            'company': company,
            'submission': submission,
            'intake_method': 'automatic_receive',
            'source_detail': submission.source_detail,
            'occurred_at': submission.submitted_at,
            'received_at': submission.created_at,
            'responsible_user': conversion_owner,
            'responsible_team': submission.team,
            'attribution_json': {
                'source_channel': submission.source_channel,
                'utm': submission.utm_json or {},
            },
            'evidence_json': {
                'form_id': submission.form_id,
                'submission_key': str(submission.submission_key),
            },
            'evidence_status': 'verified',
        },
    )
    for channel, raw_value, normalized_value in (
        ('email', (submission.email or '').strip(), email_normalized),
        ('phone', (submission.phone or '').strip(), phone_normalized),
    ):
        if raw_value:
            CompanyContactPoint.objects.get_or_create(
                site=submission.site,
                company=company,
                channel=channel,
                normalized_value=normalized_value,
                extension='',
                defaults={
                    'contact': contact,
                    'raw_value': raw_value,
                    'purpose': 'business',
                    'status': 'unverified',
                    'usage_status': 'permitted' if (submission.consent_json or {}).get('contact') else 'unknown',
                    'evidence_json': {'submission_id': submission.pk},
                },
            )
    append_activity(
        site=submission.site,
        actor=actor,
        activity_type='conversion',
        subject='线索已转换为销售项目',
        submission=submission,
        company=company,
        contact=contact,
        opportunity=opportunity,
        metadata={'conversion_id': conversion.id},
    )
    if submission.next_follow_up_at:
        create_task(
            site=submission.site,
            submission=submission,
            company=company,
            contact=contact,
            opportunity=opportunity,
            owner_user=conversion_owner,
            team=submission.team,
            created_by_user=actor if getattr(actor, 'pk', None) else None,
            title=f'跟进 {contact.full_name}',
            task_type='follow_up',
            due_at=submission.next_follow_up_at,
        )
    return LeadConversionResult(
        conversion=conversion,
        company=company,
        contact=contact,
        opportunity=opportunity,
        created=True,
    )


def _set_stage_audit_context(actor, reason: str) -> None:
    # PostgreSQL triggers consume transaction-local settings.  SQLite is a
    # supported test/development backend and has no set_config() equivalent.
    if connection.vendor != 'postgresql':
        return
    actor_id = str(actor.pk) if getattr(actor, 'pk', None) else ''
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('siteos.actor_user_id', %s, true)", [actor_id])
        cursor.execute("SELECT set_config('siteos.stage_reason', %s, true)", [(reason or '').strip()])


@transaction.atomic
def change_opportunity_stage(
    *,
    opportunity_id: int,
    stage: str,
    actor,
    reason: str = '',
) -> Opportunity:
    if stage not in OPPORTUNITY_STAGES:
        raise ValidationError('销售阶段无效。')
    opportunity = Opportunity.objects.select_for_update().select_related('site').get(pk=opportunity_id)
    if opportunity.stage == stage:
        return opportunity
    resolved_reason = (reason or '').strip()
    previous_stage = opportunity.stage
    allowed_targets = OPPORTUNITY_STAGE_TRANSITIONS.get(previous_stage, frozenset())
    if stage not in allowed_targets:
        raise ValidationError(
            f'销售阶段不能从“{previous_stage}”直接变更为“{stage}”。'
        )
    if stage == 'won' and not resolved_reason:
        raise ValidationError('成交时必须记录成交原因。')
    if stage == 'lost' and not resolved_reason:
        raise ValidationError('丢单时必须记录丢单原因。')
    if previous_stage in TERMINAL_OPPORTUNITY_STAGES and not resolved_reason:
        raise ValidationError('重新开启已关闭项目时必须记录原因。')

    _set_stage_audit_context(actor, resolved_reason)
    opportunity.stage = stage
    if stage == 'won':
        opportunity.probability = 100
        opportunity.won_reason = resolved_reason
        opportunity.lost_reason = ''
        opportunity.closed_at = timezone.now()
    elif stage == 'lost':
        opportunity.probability = 0
        opportunity.lost_reason = resolved_reason
        opportunity.won_reason = ''
        opportunity.closed_at = timezone.now()
    else:
        # Non-terminal stages must never retain stale closure evidence.  Keep a
        # useful in-flight probability where possible, but replace terminal
        # 0/100 values with the target stage's documented default.
        opportunity.closed_at = None
        opportunity.won_reason = ''
        opportunity.lost_reason = ''
        if opportunity.probability in {0, 100}:
            opportunity.probability = OPPORTUNITY_STAGE_DEFAULT_PROBABILITIES[stage]
    opportunity.save(
        update_fields=[
            'stage',
            'probability',
            'won_reason',
            'lost_reason',
            'closed_at',
            'updated_at',
        ]
    )
    append_activity(
        site=opportunity.site,
        actor=actor,
        activity_type='stage_change',
        subject=f'项目阶段：{previous_stage} → {stage}',
        body=resolved_reason,
        opportunity=opportunity,
        company=opportunity.company,
        contact=opportunity.primary_contact,
        submission=opportunity.source_submission,
        metadata={'from_stage': previous_stage, 'to_stage': stage},
    )
    return opportunity


def synchronize_submission_next_follow_up(*, submission_id: int | None):
    """Keep the lead's denormalized follow-up time aligned with actionable tasks.

    Callers that mutate a task already run in a transaction.  Locking the lead
    here makes completion/cancellation and simultaneous task creation resolve to
    one deterministic earliest due time instead of leaving an expired timestamp
    behind after the last task is closed.
    """

    if not submission_id:
        return None
    submission = LeadSubmission.objects.select_for_update().get(pk=submission_id)
    next_due_at = (
        Task.objects.filter(
            submission_id=submission_id,
            status__in=ACTIONABLE_TASK_STATUSES,
        )
        .order_by('due_at', 'id')
        .values_list('due_at', flat=True)
        .first()
    )
    if submission.next_follow_up_at != next_due_at:
        submission.next_follow_up_at = next_due_at
        submission.save(update_fields=['next_follow_up_at', 'updated_at'])
    return next_due_at


def _close_task(*, task_id: int, actor, target_status: str, outcome: str = '') -> Task:
    if target_status not in TERMINAL_TASK_STATUSES:
        raise ValidationError('任务终结状态无效。')
    task = (
        Task.objects.select_for_update(of=('self',))
        .select_related('site', 'submission', 'company', 'contact', 'opportunity')
        .get(pk=task_id)
    )
    if task.status == target_status:
        synchronize_submission_next_follow_up(submission_id=task.submission_id)
        return task
    if task.status in TERMINAL_TASK_STATUSES:
        raise ValidationError('已完成或已取消的任务不能改写为另一终态。')

    task.status = target_status
    task.completed_at = timezone.now()
    task.outcome = (outcome or '').strip()
    task.save(update_fields=['status', 'completed_at', 'outcome', 'updated_at'])
    synchronize_submission_next_follow_up(submission_id=task.submission_id)
    action_label = '完成' if target_status == 'completed' else '取消'
    append_activity(
        site=task.site,
        actor=actor,
        activity_type='task',
        subject=f'{action_label}任务：{task.title}',
        body=task.outcome,
        submission=task.submission,
        company=task.company,
        contact=task.contact,
        opportunity=task.opportunity,
        metadata={'task_id': task.id, 'status': target_status},
    )
    return task


@transaction.atomic
def complete_task(*, task_id: int, actor, outcome: str = '') -> Task:
    return _close_task(
        task_id=task_id,
        actor=actor,
        target_status='completed',
        outcome=outcome,
    )


@transaction.atomic
def cancel_task(*, task_id: int, actor, outcome: str = '') -> Task:
    return _close_task(
        task_id=task_id,
        actor=actor,
        target_status='canceled',
        outcome=outcome,
    )
