from __future__ import annotations

from collections.abc import Iterable

from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, Q, QuerySet
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from leads.crm_relationships import (
    all_linked_targets_in_scope_q,
    linked_targets_match_record_site_q,
)
from leads.models import (
    Activity,
    Company,
    Contact,
    LeadSubmission,
    Opportunity,
    SalesTeam,
    SalesTeamMember,
    SavedView,
    Task,
    WhatsAppConversation,
)


ROLE_SALES = 'sales'
ROLE_SALES_MANAGER = 'sales_manager'
ROLE_MARKETING_OPS = 'marketing_ops'
ROLE_CONTENT_OPS = 'content_ops'
ROLE_SYSTEM_ADMIN = 'system_admin'

GROUP_BY_ROLE = {
    ROLE_SALES: 'SiteOS Sales',
    ROLE_SALES_MANAGER: 'SiteOS Sales Manager',
    ROLE_MARKETING_OPS: 'SiteOS Marketing Ops',
    ROLE_CONTENT_OPS: 'SiteOS Content Ops',
    ROLE_SYSTEM_ADMIN: 'SiteOS System Admin',
}
ROLE_LABELS = {
    ROLE_SALES: '销售',
    ROLE_SALES_MANAGER: '销售经理',
    ROLE_MARKETING_OPS: '营销运营',
    ROLE_CONTENT_OPS: '内容运营',
    ROLE_SYSTEM_ADMIN: '系统管理员',
}
PRIMARY_ROLE_PRIORITY = (
    ROLE_SYSTEM_ADMIN,
    ROLE_MARKETING_OPS,
    ROLE_CONTENT_OPS,
    ROLE_SALES_MANAGER,
    ROLE_SALES,
)
ALL_CONSOLE_ROLES = frozenset(GROUP_BY_ROLE)
SALES_ROLES = frozenset({ROLE_SALES, ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN})
# Compatibility aliases for navigation and callers that still use the legacy
# names. Raw lead records and exports are sales objects; marketing operators
# consume only the aggregate marketing workbench and event-delivery evidence.
LEAD_ROLES = SALES_ROLES
LEAD_ATTRIBUTION_ROLES = SALES_ROLES
# This is deliberately only the middleware's coarse domain gate.  Every
# content, asset, locale, and preview endpoint must still resolve one exact
# ContentAccessGrant capability for its server-side site/locale scope.  Adding
# content_ops here therefore makes the role routable without implying any
# read, write, publish, local-import, or preview-build permission.
CONTENT_ROLES = frozenset({ROLE_CONTENT_OPS, ROLE_SYSTEM_ADMIN})
MARKETING_ROLES = frozenset({ROLE_MARKETING_OPS, ROLE_SYSTEM_ADMIN})
SYSTEM_ROLES = frozenset({ROLE_SYSTEM_ADMIN})


# This attribute is deliberately populated only by ConsoleAccessMiddleware.
# Django loads a distinct user object for each authenticated request, so the
# immutable value is safe to reuse throughout that request without turning
# user_role_keys() into a process- or model-instance-level cache.
_REQUEST_ROLE_KEYS_ATTR = '_console_request_role_keys'


PUBLIC_CONSOLE_VIEWS = frozenset({
    'public_lead_form_definition',
    'public_lead_submit',
})

CONTENT_VIEW_NAMES = frozenset({
    'content_articles',
    'articles', 'article_import', 'article_export', 'article_locale_create',
    'article_locale_enable', 'article_locale_disable', 'article_locale_make_default',
    'article_categories', 'category_create', 'category_edit', 'article_create',
    'article_edit', 'assets', 'assets_three_d', 'three_d_asset_detail',
    'three_d_profile_create', 'three_d_profile_edit', 'three_d_placement_create',
    'three_d_placement_edit', 'asset_upload', 'asset_import_path', 'asset_file',
    'releases', 'release_build_preview', 'article_release_preview',
    'article_preview_file', 'website_candidate_build', 'website_candidate_select',
    'website_candidate_deploy',
})
MARKETING_VIEW_NAMES = frozenset({
    'marketing_attribution',
    'marketing_events', 'marketing_inbound_retry',
    'marketing', 'marketing_integration_edit', 'marketing_forms',
    'marketing_form_create', 'marketing_form_edit', 'lead_form_create',
    'lead_form_edit', 'marketing_privacy',
    'marketing_overview_data', 'integration_evidence_data', 'integration_diagnose',
    'marketing_outbox_retry', 'privacy_requests_data', 'privacy_request_create',
    'privacy_request_update', 'privacy_request_register', 'privacy_request_manage',
})
SYSTEM_VIEW_NAMES = frozenset({
    'privacy_request_export', 'privacy_request_execute',
    'retention_policy_data', 'retention_policy_update', 'retention_policy_execute',
    'system_email', 'smtp_settings', 'system_data_governance',
    'system_users', 'system_user_create', 'system_user_edit', 'system_teams',
    'system_team_create', 'system_team_edit',
})
SHARED_CONSOLE_VIEW_NAMES = frozenset({'home', 'workbench'})
LEAD_ATTRIBUTION_VIEW_NAMES = frozenset()
SALES_VIEW_NAMES = frozenset({
    'sales_workspace', 'leads', 'lead_submission_edit', 'lead_submission_bulk_stage',
    'lead_submission_export_csv', 'lead_submission_export_xlsx',
    'workbench_data', 'pipeline_data', 'lead_convert', 'activity_create',
    'task_create', 'task_complete', 'opportunity_stage', 'saved_view_save',
    'opportunity_create_v2',
    'sales_bulk_assign', 'crm_collection_data', 'crm_record_detail',
    'company_update', 'contact_update', 'opportunity_update', 'crm_attachments',
    'crm_attachment_download', 'crm_attachment_archive',
    'lead_workspace_v2', 'lead_workspace_detail_v2', 'lead_disposition_v2',
    'lead_bulk_assign_v2',
    'opportunity_workspace_v2', 'opportunity_workspace_detail_v2',
    'company_workspace_v2', 'company_workspace_detail_v2',
    'contact_workspace_v2', 'contact_workspace_detail_v2',
    'contact_company_options_v2',
    'task_workspace_v2', 'task_new_v2', 'task_workspace_detail_v2',
    'activity_workspace_detail_v2', 'task_update_v2', 'task_start_v2',
    'task_cancel_v2', 'task_bulk_assign_v2', 'activity_correction_v2',
    'task_target_search_v2',
    'whatsapp_workspace_v2', 'whatsapp_workspace_detail_v2',
    'whatsapp_send_v2', 'whatsapp_note_v2', 'whatsapp_assign_v2',
    'whatsapp_state_v2', 'whatsapp_mark_read_v2', 'whatsapp_retry_v2',
    'whatsapp_media_download_v2',
    'customer_pool', 'customer_pool_detail', 'customer_pool_manual_create',
    'customer_pool_claim', 'customer_pool_bulk_claim', 'customer_pool_bulk_review', 'customer_pool_assign',
    'customer_pool_release', 'customer_pool_template',
    'customer_pool_import', 'customer_pool_review', 'customer_pool_archive',
    'customer_pool_restore', 'customer_pool_contact_search',
    'customer_pool_export', 'customer_pool_export_download',
    'customer_pool_export_retry', 'customer_pool_export_cancel',
    'customer_pool_export_status', 'customer_pool_export_standard21',
})
# Historical import compatibility. The old constant mixed attribution reads
# and operational sales writes; keeping it as the safe sales-only set prevents
# new call sites from recreating that privilege escalation.
LEAD_VIEW_NAMES = SALES_VIEW_NAMES


def user_role_keys(user) -> set[str] | frozenset[str]:
    cached_roles = getattr(user, _REQUEST_ROLE_KEYS_ATTR, None)
    if isinstance(cached_roles, frozenset):
        return cached_roles
    if not getattr(user, 'is_authenticated', False):
        return set()
    if getattr(user, 'is_superuser', False):
        return {ROLE_SYSTEM_ADMIN}
    group_names = set(user.groups.values_list('name', flat=True))
    return {role for role, group_name in GROUP_BY_ROLE.items() if group_name in group_names}


def user_has_any_role(user, roles: Iterable[str]) -> bool:
    return bool(user_role_keys(user).intersection(roles))


def primary_role_label(user) -> str:
    roles = user_role_keys(user)
    for role in PRIMARY_ROLE_PRIORITY:
        if role in roles:
            return ROLE_LABELS[role]
    return '未分配角色'


def allowed_roles_for_view(view_name: str) -> frozenset[str]:
    if view_name in SHARED_CONSOLE_VIEW_NAMES:
        return ALL_CONSOLE_ROLES
    if view_name in SYSTEM_VIEW_NAMES:
        return SYSTEM_ROLES
    if view_name in CONTENT_VIEW_NAMES:
        return CONTENT_ROLES
    if view_name in MARKETING_VIEW_NAMES:
        return MARKETING_ROLES
    if view_name in LEAD_ATTRIBUTION_VIEW_NAMES:
        return LEAD_ATTRIBUTION_ROLES
    if view_name in SALES_VIEW_NAMES:
        return SALES_ROLES
    return SYSTEM_ROLES


def default_console_route_name(user) -> str:
    # Every registered role starts from the neutral workbench.  In particular,
    # content_ops must not be sent to the article route before a site/locale
    # grant has been resolved.  Middleware still rejects users with no
    # registered role before this destination is rendered.
    return 'console:workbench'


def lead_queryset_for_user(queryset: QuerySet[LeadSubmission], user) -> QuerySet[LeadSubmission]:
    roles = user_role_keys(user)
    if ROLE_SYSTEM_ADMIN in roles:
        return queryset
    if ROLE_SALES_MANAGER in roles:
        managed_team_ids = SalesTeamMember.objects.filter(
            user=user,
            membership_role='manager',
            team__enabled=True,
        ).values('team_id')
        return queryset.filter(team_id__in=managed_team_ids)
    if ROLE_SALES in roles:
        return queryset.filter(assignee=user)
    return queryset.none()


def whatsapp_conversation_queryset_for_user(
    queryset: QuerySet[WhatsAppConversation],
    user,
) -> QuerySet[WhatsAppConversation]:
    """Scope WhatsApp content and reject corrupt cross-site relationship rows."""

    structurally_valid = queryset.filter(
        site_id=F('integration__site_id'),
    ).filter(
        Q(submission_id__isnull=True) | Q(submission__site_id=F('site_id')),
        Q(company_id__isnull=True) | Q(company__site_id=F('site_id')),
        Q(contact_id__isnull=True) | Q(contact__site_id=F('site_id')),
        Q(team_id__isnull=True) | Q(team__site_id=F('site_id'), team__enabled=True),
    ).filter(
        Q(submission_id__isnull=True)
        | Q(submission__team_id__isnull=True)
        | Q(team_id__isnull=True)
        | Q(submission__team_id=F('team_id')),
        Q(company_id__isnull=True)
        | Q(company__team_id__isnull=True)
        | Q(team_id__isnull=True)
        | Q(company__team_id=F('team_id')),
        Q(contact_id__isnull=True)
        | Q(contact__team_id__isnull=True)
        | Q(team_id__isnull=True)
        | Q(contact__team_id=F('team_id')),
    )
    roles = user_role_keys(user)
    if ROLE_SYSTEM_ADMIN in roles:
        return structurally_valid
    if ROLE_SALES_MANAGER in roles:
        managed_team_ids = SalesTeamMember.objects.filter(
            user=user,
            membership_role='manager',
            team__enabled=True,
        ).values('team_id')
        return structurally_valid.filter(team_id__in=managed_team_ids)
    if ROLE_SALES in roles:
        member_team_ids = SalesTeamMember.objects.filter(
            user=user,
            team__enabled=True,
        ).values('team_id')
        return structurally_valid.filter(
            Q(owner_user=user)
            | Q(owner_user__isnull=True, team_id__in=member_team_ids)
        )
    return structurally_valid.none()


def lead_attribution_queryset_for_user(
    queryset: QuerySet[LeadSubmission],
    user,
) -> QuerySet[LeadSubmission]:
    """Compatibility scope for legacy lead renderers and exports.

    Marketing attribution is aggregate-only.  Any caller that still handles
    raw lead rows must therefore inherit the same owner/team scope as sales.
    """
    return lead_queryset_for_user(queryset, user)


def _crm_owned_queryset_for_roles(queryset: QuerySet, user, roles) -> QuerySet:
    if ROLE_SYSTEM_ADMIN in roles:
        return queryset
    if ROLE_SALES_MANAGER in roles:
        managed_team_ids = SalesTeamMember.objects.filter(
            user=user,
            membership_role='manager',
            team__enabled=True,
        ).values('team_id')
        return queryset.filter(team_id__in=managed_team_ids)
    if ROLE_SALES in roles:
        return queryset.filter(owner_user=user)
    return queryset.none()


def crm_owned_queryset_for_user(queryset: QuerySet, user) -> QuerySet:
    """Apply the all-site, team, or owner scope to CRM records with owner/team fields."""

    return _crm_owned_queryset_for_roles(queryset, user, user_role_keys(user))


def contact_queryset_for_user(queryset: QuerySet[Contact], user) -> QuerySet[Contact]:
    """Hide legacy contacts whose optional company target is not safely visible.

    CRM tables are managed outside Django and older rows can violate the
    intended same-site/ownership graph.  Scoping only the Contact row would
    still disclose the linked Company's name through select_related/search.
    """

    visible_companies = crm_owned_queryset_for_user(Company.objects.all(), user)
    return crm_owned_queryset_for_user(queryset, user).filter(
        Q(company_id__isnull=True)
        | (
            Q(company__site_id=F('site_id'))
            & Q(company_id__in=visible_companies.values('id'))
        )
    )


def opportunity_queryset_for_user(
    queryset: QuerySet[Opportunity],
    user,
) -> QuerySet[Opportunity]:
    """Fail closed unless every direct business target is same-site and visible."""

    visible_companies = crm_owned_queryset_for_user(Company.objects.all(), user)
    visible_contacts = contact_queryset_for_user(Contact.objects.all(), user)
    visible_leads = lead_queryset_for_user(LeadSubmission.objects.all(), user)
    return crm_owned_queryset_for_user(queryset, user).filter(
        Q(company_id__isnull=True)
        | (
            Q(company__site_id=F('site_id'))
            & Q(company_id__in=visible_companies.values('id'))
        ),
        Q(primary_contact_id__isnull=True)
        | (
            Q(primary_contact__site_id=F('site_id'))
            & Q(primary_contact_id__in=visible_contacts.values('id'))
        ),
        Q(source_submission_id__isnull=True)
        | (
            Q(source_submission__site_id=F('site_id'))
            & Q(source_submission_id__in=visible_leads.values('id'))
        ),
    ).filter(
        Q(company_id__isnull=True)
        | Q(primary_contact_id__isnull=True)
        | Q(primary_contact__company_id=F('company_id'))
    )


def _all_linked_targets_visible_queryset(
    queryset: QuerySet,
    user,
    *,
    roles=None,
) -> QuerySet:
    """Apply fail-closed, all-target containment to relationship records."""

    roles = user_role_keys(user) if roles is None else roles
    site_contract = linked_targets_match_record_site_q()
    if ROLE_SYSTEM_ADMIN in roles:
        # System admins may read every valid target, but corrupt legacy rows
        # must not cross a site's request boundary or surface as unlinked data.
        return queryset.filter(site_contract).filter(
            all_linked_targets_in_scope_q(
                submission_ids=LeadSubmission.objects.values('id'),
                company_ids=Company.objects.values('id'),
                contact_ids=Contact.objects.values('id'),
                opportunity_ids=Opportunity.objects.values('id'),
            )
        )
    if ROLE_SALES_MANAGER in roles:
        team_ids = SalesTeamMember.objects.filter(
            user=user,
            membership_role='manager',
            team__enabled=True,
        ).values('team_id')
        return queryset.filter(site_contract).filter(
            all_linked_targets_in_scope_q(
                submission_ids=LeadSubmission.objects.filter(team_id__in=team_ids).values('id'),
                company_ids=Company.objects.filter(team_id__in=team_ids).values('id'),
                contact_ids=Contact.objects.filter(team_id__in=team_ids).values('id'),
                opportunity_ids=Opportunity.objects.filter(team_id__in=team_ids).values('id'),
            )
        )
    if ROLE_SALES in roles:
        return queryset.filter(site_contract).filter(
            all_linked_targets_in_scope_q(
                submission_ids=LeadSubmission.objects.filter(assignee=user).values('id'),
                company_ids=Company.objects.filter(owner_user=user).values('id'),
                contact_ids=Contact.objects.filter(owner_user=user).values('id'),
                opportunity_ids=Opportunity.objects.filter(owner_user=user).values('id'),
            )
        )
    return queryset.none()


def activity_queryset_for_user(queryset: QuerySet[Activity], user) -> QuerySet[Activity]:
    return _all_linked_targets_visible_queryset(queryset, user)


def task_queryset_for_user(
    queryset: QuerySet[Task],
    user,
    *,
    roles=None,
) -> QuerySet[Task]:
    """Scope both the Task owner/team row and every linked business target."""

    roles = user_role_keys(user) if roles is None else roles
    return _all_linked_targets_visible_queryset(
        _crm_owned_queryset_for_roles(queryset, user, roles),
        user,
        roles=roles,
    )


def assignee_queryset_for_user(user, *, site=None):
    users = get_user_model().objects.filter(is_active=True)
    roles = user_role_keys(user)
    if ROLE_SYSTEM_ADMIN in roles:
        if site is not None:
            users = users.filter(
                sales_team_memberships__team__site=site,
                sales_team_memberships__team__enabled=True,
            ).distinct()
        return users.order_by('username')
    if ROLE_SALES_MANAGER in roles:
        managed_team_ids = SalesTeamMember.objects.filter(
            user=user,
            membership_role='manager',
            team__enabled=True,
        )
        if site is not None:
            managed_team_ids = managed_team_ids.filter(team__site=site)
        return users.filter(
            sales_team_memberships__team_id__in=managed_team_ids.values('team_id')
        ).distinct().order_by('username')
    if ROLE_SALES in roles:
        return users.filter(id=user.id)
    return users.none()


def assignable_assignee_queryset_for_lead(user, lead):
    """Return active users that the disposition service can assign to ``lead``.

    This is deliberately narrower than :func:`assignee_queryset_for_user`,
    which is also used by owner filters and older forms.  A lead with a team
    may only be assigned to an active member of that enabled team.  For an
    unteamed lead, the workflow service can infer a team only when the target
    user has exactly one enabled membership for the site, so the chooser uses
    the same rule instead of advertising options that will inevitably fail.
    """

    users = get_user_model().objects.filter(is_active=True)
    roles = user_role_keys(user)
    is_admin = ROLE_SYSTEM_ADMIN in roles
    is_manager = ROLE_SALES_MANAGER in roles
    if not (is_admin or is_manager):
        return users.none()

    site_id = getattr(lead, 'site_id', None)
    if not site_id:
        return users.none()

    memberships = SalesTeamMember.objects.filter(
        team__site_id=site_id,
        team__enabled=True,
        user__is_active=True,
    )
    managed_team_ids = None
    if is_manager and not is_admin:
        managed_team_ids = SalesTeamMember.objects.filter(
            user=user,
            membership_role='manager',
            team__site_id=site_id,
            team__enabled=True,
        ).values('team_id')

    lead_team_id = getattr(lead, 'team_id', None)
    if lead_team_id:
        if managed_team_ids is not None and not managed_team_ids.filter(
            team_id=lead_team_id
        ).exists():
            return users.none()
        member_ids = memberships.filter(team_id=lead_team_id).values('user_id')
        return users.filter(id__in=member_ids).distinct().order_by('username')

    # Count across *all* enabled teams for the site before applying a manager's
    # managed-team restriction.  Otherwise a user in one managed and one
    # unmanaged team would look unique to the UI but fail _resolve_assignment.
    uniquely_teamed_user_ids = (
        memberships.values('user_id')
        .annotate(enabled_team_count=Count('team_id', distinct=True))
        .filter(enabled_team_count=1)
        .values('user_id')
    )
    users = users.filter(id__in=uniquely_teamed_user_ids)
    if managed_team_ids is not None:
        users = users.filter(
            sales_team_memberships__team_id__in=managed_team_ids,
        )
    return users.distinct().order_by('username')


def shareable_sales_team_queryset_for_user(user, *, site):
    """Teams on which ``SavedViewForm`` may grant shared visibility."""

    roles = user_role_keys(user)
    teams = SalesTeam.objects.filter(site=site, enabled=True)
    if ROLE_SYSTEM_ADMIN in roles:
        return teams.order_by('name', 'id')
    if ROLE_SALES_MANAGER in roles:
        return teams.filter(
            memberships__user=user,
            memberships__membership_role='manager',
        ).distinct().order_by('name', 'id')
    return teams.none()


def saved_view_queryset_for_user(queryset: QuerySet[SavedView], user, *, site):
    """Scope personal and team-shared views without widening sales access."""

    roles = user_role_keys(user)
    queryset = queryset.filter(site=site)
    if ROLE_SYSTEM_ADMIN in roles:
        return queryset.filter(Q(user=user) | Q(is_shared=True))
    if roles.intersection({ROLE_SALES, ROLE_SALES_MANAGER}):
        team_ids = SalesTeamMember.objects.filter(
            user=user,
            team__site=site,
            team__enabled=True,
        ).values('team_id')
        return queryset.filter(
            Q(user=user) | Q(is_shared=True, team_id__in=team_ids)
        )
    return queryset.none()


def lead_attribution_assignee_queryset_for_user(user, *, site=None):
    """Return attribution filter choices without granting assignment rights."""
    roles = user_role_keys(user)
    if ROLE_MARKETING_OPS in roles:
        return get_user_model().objects.filter(is_active=True).order_by('username')
    return assignee_queryset_for_user(user, site=site)


def safe_next_url(request, candidate: str | None, fallback: str) -> str:
    value = (candidate or '').strip()
    if value and url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return fallback


class ConsoleAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if not match or match.namespace != 'console':
            return None
        if match.url_name in PUBLIC_CONSOLE_VIEWS:
            return None
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        # A model instance may be reused by RequestFactory callers or tests.
        # Clear an earlier request's explicit cache before resolving this
        # request, then share one immutable role set with all downstream
        # permission, navigation, and workbench helpers.
        if isinstance(getattr(request.user, _REQUEST_ROLE_KEYS_ATTR, None), frozenset):
            delattr(request.user, _REQUEST_ROLE_KEYS_ATTR)
        request_roles = frozenset(user_role_keys(request.user))
        setattr(request.user, _REQUEST_ROLE_KEYS_ATTR, request_roles)

        if not request_roles.intersection(allowed_roles_for_view(match.url_name)):
            raise PermissionDenied('当前账号没有访问这个后台模块的权限。')
        return None
