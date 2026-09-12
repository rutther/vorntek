from __future__ import annotations

import json
import os
from urllib.parse import urlencode, urlsplit, urlunsplit

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from leads.email_delivery import load_runtime_email_config, smtp_password_last4
from leads.models import (
    LeadConversion,
    LeadEventOutbox,
    LeadFormDefinition,
    LeadInboundEvent,
    LeadSubmission,
    Opportunity,
    PrivacyRequest,
    RetentionPolicy,
    SalesTeam,
    SalesTeamMember,
    Task,
)
from leads.notifications import form_notification_recipients, send_smtp_test_notification
from leads.services import (
    LeadCaptureError,
    create_submission,
    update_submission_stage,
)
from marketing.models import MarketingIntegration
from marketing.public_config import public_measurement_config
from sitecore.models import Article, Category, MediaAsset, Site, SiteLocale, ThreeDPlacement, ThreeDViewProfile

from .article_categories import category_editor_page_payload
from .article_exports import build_articles_export_zip
from .article_imports import ArticleImportZipForm
from .article_workspace import article_workspace_bundle
from .acquisition_versions import lead_form_version_matches
from .acquisition_workspace import build_acquisition_forms_workspace
from .access import (
    GROUP_BY_ROLE,
    MARKETING_ROLES,
    PRIMARY_ROLE_PRIORITY,
    ROLE_LABELS,
    ROLE_CONTENT_OPS,
    ROLE_MARKETING_OPS,
    ROLE_SALES,
    ROLE_SALES_MANAGER,
    ROLE_SYSTEM_ADMIN,
    SYSTEM_ROLES,
    assignee_queryset_for_user,
    lead_queryset_for_user,
    primary_role_label,
    safe_next_url,
    user_has_any_role,
    user_role_keys,
)
from .category_forms import CategoryEditorForm
from .content_access import (
    ASSETS_IMPORT_LOCAL,
    ASSETS_READ,
    ASSETS_WRITE,
    CONTENT_LOCALE_MANAGE,
    CONTENT_READ,
    CONTENT_SET_PUBLISHED,
    CONTENT_WRITE,
    RELEASES_PREVIEW_BUILD,
    RELEASES_READ,
    effective_content_capabilities,
)
from .audit import (
    integration_snapshot,
    lead_form_snapshot,
    lead_submission_snapshot,
    privacy_request_snapshot,
    record_audit,
)
from .email_forms import SmtpSettingsForm
from .data_governance_forms import RetentionExecutionForm, RetentionPolicyForm
from .forms import ArticleEditorForm, AssetImportPathForm, AssetUploadForm, asset_mime_type
from .lead_forms import LeadFormEditorForm, LeadSubmissionEditorForm, default_form_schema
from .lead_exports import build_leads_csv, build_leads_xlsx, lead_export_queryset
from .leads_workspace import build_leads_workspace
from .locale_admin import create_locale_from_source, disable_locale, enable_locale, set_default_locale
from .locale_forms import LocaleBootstrapForm
from .marketing_forms import MarketingIntegrationEditorForm
from .marketing_services import (
    PRIVACY_TRANSITIONS,
    execute_privacy_action,
    privacy_subject_submissions,
    record_integration_diagnostic,
    register_privacy_request,
    transition_privacy_request,
)
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, admin_locale_label, assets_payload, default_site_locale, marketing_payload, releases_payload
from .privacy_forms import (
    PRIVACY_TYPE_CHOICES,
    PrivacyRequestRegistrationForm,
    PrivacyRequestWorkflowForm,
)
from .privacy_retention import execute_retention_policy
from .privacy_versions import (
    privacy_request_version_matches,
    retention_policy_version_matches,
)
from .privacy_workspace import (
    build_data_governance_workspace,
    build_privacy_queue_workspace,
    privacy_request_impact,
)
from .release_pipeline import (
    PreviewBuildConfigurationError,
    PreviewBuildError,
    PreviewBuildInProgress,
    run_preview_build,
)
from .secret_store import secret_last4
from .storage_security import (
    StorageSecurityError,
    private_file_response_headers,
    resolve_storage_asset_path,
)
from .taxonomy import category_tree_data
from .three_d_config import build_splat_viewer_url, placement_public_config, slot_label
from .three_d_forms import ThreeDPlacementForm, ThreeDViewProfileForm
from .three_d_payloads import three_d_asset_detail_context, three_d_asset_page_payload, three_d_page_payload, three_d_workspace_context
from .system_forms import ConsoleUserForm, SalesTeamForm
from .system_versions import sales_team_version_matches, system_user_version_matches


User = get_user_model()


# Every content-domain URL is registered here deliberately.  Middleware keeps
# anonymous and unrelated roles out; these exact capabilities remain the
# authoritative route-level policy and are enforced before domain queries,
# form/file processing, or preview execution.
CONTENT_ROUTE_CAPABILITIES: dict[str, frozenset[str]] = {
    'content_articles': frozenset({CONTENT_READ}),
    'articles': frozenset({CONTENT_READ}),
    'article_import': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'article_export': frozenset({CONTENT_READ}),
    'article_locale_create': frozenset({CONTENT_LOCALE_MANAGE, CONTENT_SET_PUBLISHED}),
    'article_locale_enable': frozenset({CONTENT_LOCALE_MANAGE, CONTENT_SET_PUBLISHED}),
    'article_locale_disable': frozenset({CONTENT_LOCALE_MANAGE, CONTENT_SET_PUBLISHED}),
    'article_locale_make_default': frozenset({CONTENT_LOCALE_MANAGE, CONTENT_SET_PUBLISHED}),
    'article_categories': frozenset({CONTENT_READ}),
    'category_create': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'category_edit': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'article_create': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'article_edit': frozenset({CONTENT_READ, CONTENT_WRITE}),
    'assets': frozenset({ASSETS_READ}),
    'assets_three_d': frozenset({ASSETS_READ}),
    'three_d_asset_detail': frozenset({ASSETS_READ}),
    'three_d_profile_create': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'three_d_profile_edit': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'three_d_placement_create': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'three_d_placement_edit': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'asset_upload': frozenset({ASSETS_READ, ASSETS_WRITE}),
    'asset_import_path': frozenset({ASSETS_READ, ASSETS_WRITE, ASSETS_IMPORT_LOCAL}),
    'asset_file': frozenset({ASSETS_READ}),
    'releases': frozenset({RELEASES_READ}),
    'release_build_preview': frozenset({RELEASES_PREVIEW_BUILD}),
}


def env_list(name: str, default: str = '') -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


def public_form_allowed_origins() -> set[str]:
    return set(env_list('SITEOS_PUBLIC_FORM_ALLOWED_ORIGINS'))


def apply_cors_headers(request, response: HttpResponse) -> HttpResponse:
    origin = (request.META.get('HTTP_ORIGIN') or '').strip().rstrip('/')
    allowed = public_form_allowed_origins()
    if origin and (origin in allowed or '*' in allowed):
        response['Access-Control-Allow-Origin'] = origin
        response['Vary'] = 'Origin'
        response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response['Access-Control-Allow-Headers'] = 'Content-Type, Accept'
        response['Access-Control-Max-Age'] = '600'
    return response


def navigation_payload(active_key: str, user) -> list[dict[str, object]]:
    """Compatibility wrapper around the canonical navigation builder."""

    return build_navigation(active_key, user)


def redirect_get_preserving_query(
    request,
    route_name: str,
    *,
    drop: tuple[str, ...] = (),
):
    """Redirect a legacy GET page without losing restorable URL context."""

    params = request.GET.copy()
    for key in drop:
        params.pop(key, None)
    target = reverse(route_name)
    encoded = params.urlencode()
    return redirect(f'{target}?{encoded}' if encoded else target)


def requested_locale_code(request) -> str | None:
    value = (request.GET.get('locale') or '').strip().lower()
    return value or None


def _require_resolved_capability(
    capabilities: frozenset[str],
    capability: str,
) -> None:
    if capability not in capabilities:
        raise PermissionDenied('当前账号没有在此站点执行该内容操作的权限。')


def _resolved_content_site_locale(request) -> tuple[Site, SiteLocale]:
    """Resolve an explicit locale strictly instead of silently falling back."""

    site, default_locale = default_site_locale()
    locale_code = requested_locale_code(request)
    if locale_code is None:
        return site, default_locale
    locale = get_object_or_404(
        SiteLocale.objects.filter(site=site, enabled=True),
        locale_code=locale_code,
    )
    return site, locale


def _require_route_capabilities(
    capabilities: frozenset[str],
    route_name: str,
) -> None:
    required = CONTENT_ROUTE_CAPABILITIES.get(route_name)
    if required is None:
        # A content route without an explicit policy is a programming error,
        # but fail closed in production even before the coverage test catches it.
        raise PermissionDenied('当前内容操作尚未配置权限策略。')
    for capability in required:
        _require_resolved_capability(capabilities, capability)


def content_locale_scope(
    request,
    route_name: str,
) -> tuple[Site, SiteLocale, frozenset[str]]:
    """Resolve and authorize the exact site/locale before any content query."""

    site, locale = _resolved_content_site_locale(request)
    capabilities = effective_content_capabilities(
        request.user,
        site=site,
        locale=locale,
    )
    _require_route_capabilities(capabilities, route_name)
    return site, locale, capabilities


def content_site_scope(
    request,
    route_name: str,
) -> tuple[Site, SiteLocale, frozenset[str]]:
    """Resolve and authorize one site-wide content, asset, or release action."""

    site, locale = _resolved_content_site_locale(request)
    capabilities = effective_content_capabilities(
        request.user,
        site=site,
        locale=None,
    )
    _require_route_capabilities(capabilities, route_name)
    return site, locale, capabilities


def readable_content_locale_ids(
    request,
    *,
    site,
    current_locale: SiteLocale,
    current_capabilities: frozenset[str],
) -> frozenset[int]:
    """Return only locale identities whose content can be read by this user."""

    visible: set[int] = set()
    for locale in SiteLocale.objects.filter(site=site, enabled=True).order_by('sort_order', 'id'):
        capabilities = (
            current_capabilities
            if locale.id == current_locale.id
            else effective_content_capabilities(
                request.user,
                site=site,
                locale=locale,
            )
        )
        if CONTENT_READ in capabilities:
            visible.add(locale.id)
    return frozenset(visible)


def request_client_ip(request) -> str:
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return forwarded or (request.META.get('HTTP_X_REAL_IP') or '').strip() or request.META.get('REMOTE_ADDR', '')


def preview_base_url(request=None) -> str:
    if request is None:
        return PUBLIC_PREVIEW_BASE_URL
    if os.getenv('SITEOS_PREVIEW_MIRROR_REQUEST_HOST', '').lower() not in {'1', 'true', 'yes'}:
        return PUBLIC_PREVIEW_BASE_URL
    current_host = request.get_host().split(':', 1)[0]
    parsed = urlsplit(PUBLIC_PREVIEW_BASE_URL)
    if parsed.hostname in {'127.0.0.1', 'localhost'} and current_host not in {'127.0.0.1', 'localhost'}:
        netloc = current_host
        if parsed.port:
            netloc = f'{netloc}:{parsed.port}'
        return urlunsplit((parsed.scheme, netloc, '', '', '')).rstrip('/')
    return PUBLIC_PREVIEW_BASE_URL


def preview_site_url(locale_code: str | None, request=None) -> str:
    return f'{preview_base_url(request)}/?lang={locale_code or "en"}'


def preview_public_url(path: str | None, request=None) -> str:
    if not path:
        return ''
    if path.startswith('http://') or path.startswith('https://'):
        return path
    normalized = path if path.startswith('/') else f'/{path}'
    return f'{preview_base_url(request)}{normalized}'


def render_console(
    request,
    *,
    section_key: str,
    page_payload: dict,
    locale_code: str | None = None,
    extra_stylesheets: list[str] | None = None,
    extra_scripts: list[str] | None = None,
):
    return render(
        request,
        'console/app_shell.html',
        {
            'page_payload': page_payload,
            'nav_items': navigation_payload(section_key, request.user),
            'console_home_url': reverse('console:workbench'),
            'console_role_label': primary_role_label(request.user),
            'preview_site_url': preview_site_url(locale_code, request=request),
            'extra_stylesheets': extra_stylesheets or [],
            'extra_scripts': extra_scripts or [],
        },
    )


def render_form_console(
    request,
    *,
    section_key: str,
    page_payload: dict,
    workspace_template: str,
    extra_context: dict[str, object] | None = None,
    locale_code: str | None = None,
    extra_stylesheets: list[str] | None = None,
    extra_scripts: list[str] | None = None,
):
    context = {
        'page_payload': page_payload,
        'nav_items': navigation_payload(section_key, request.user),
        'console_home_url': reverse('console:workbench'),
        'console_role_label': primary_role_label(request.user),
        'preview_site_url': preview_site_url(locale_code, request=request),
        'workspace_template': workspace_template,
        'extra_stylesheets': extra_stylesheets or [],
        'extra_scripts': extra_scripts or [],
    }
    if extra_context:
        context.update(extra_context)
    return render(request, 'console/app_shell.html', context)


def render_system_user_editor(
    request,
    *,
    form,
    editor_mode: str,
    edited_user=None,
    one_time_credentials: dict[str, str] | None = None,
):
    """Render account credentials without persisting plaintext in messages or sessions."""

    title = '新建后台账号'
    description = '设置登录信息、主角色和销售团队。'
    if edited_user is not None:
        title = edited_user.get_full_name().strip() or edited_user.get_username()
        description = '维护账号状态、主角色、销售团队和登录密码。'
    form_action = (
        reverse('console:system_user_edit', args=[edited_user.pk])
        if edited_user is not None
        else reverse('console:system_user_create')
    )
    response = render_form_console(
        request,
        section_key='system_users',
        page_payload=system_page_payload(title=title, description=description),
        workspace_template='console/_system_user_editor_workspace.html',
        extra_stylesheets=['console/system-users.css'],
        extra_scripts=['console/system-users.js'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'system_user_form': form,
            'system_user_form_action': form_action,
            'editor_mode': editor_mode,
            'edited_user': edited_user,
            'edited_user_role_label': (
                ROLE_LABELS.get(console_user_role_key(edited_user), '未分配角色')
                if edited_user is not None else ''
            ),
            'edited_user_last_login': (
                console_datetime_label(edited_user.last_login)
                if edited_user is not None else ''
            ),
            'one_time_credentials': one_time_credentials,
        },
    )
    response['Cache-Control'] = 'private, no-store, max-age=0'
    response['Pragma'] = 'no-cache'
    return response


def article_editor_page_payload(*, title: str, description: str, locale_label: str) -> dict:
    return {
        'sectionKey': 'articles',
        'sectionLabel': '文章',
        'title': title,
        'description': description,
        'pageType': 'form',
        'workspaceLabel': title,
        'workspaceMeta': f'当前语言 {locale_label}。这里维护文章正文、SEO、封面和访问路径。',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def marketing_integration_editor_page_payload(*, title: str, description: str, locale_label: str) -> dict:
    return {
        'sectionKey': 'marketing',
        'sectionLabel': '营销配置',
        'title': title,
        'description': description,
        'pageType': 'form',
        'workspaceLabel': title,
        'workspaceMeta': f'当前语言 {locale_label}。这里维护营销平台标识、服务端密钥和事件回传设置。',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def privacy_request_page_payload(*, title: str, description: str, workspace_meta: str) -> dict:
    return {
        'sectionKey': 'marketing_privacy',
        'sectionLabel': '营销',
        'title': title,
        'description': description,
        'pageType': 'form',
        'workspaceLabel': title,
        'workspaceMeta': workspace_meta,
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def lead_form_editor_page_payload(*, title: str, description: str, locale_label: str) -> dict:
    return {
        'sectionKey': 'marketing_forms',
        'sectionLabel': '营销',
        'title': title,
        'description': description,
        'pageType': 'form',
        'workspaceLabel': title,
        'workspaceMeta': f'当前语言 {locale_label}。维护投放范围、客户字段、通知和回传事件。',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def lead_submission_editor_page_payload(*, title: str, description: str, locale_label: str) -> dict:
    return {
        'sectionKey': 'leads',
        'sectionLabel': '线索管理',
        'title': title,
        'description': description,
        'pageType': 'form',
        'workspaceLabel': title,
        'workspaceMeta': f'当前语言 {locale_label}。这里核验客户、安排跟进并建立销售项目。',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def system_page_payload(*, title: str, description: str, page_type: str = 'form') -> dict:
    return {
        'sectionKey': 'system',
        'sectionLabel': '系统',
        'title': title,
        'description': description,
        'pageType': page_type,
        'workspaceLabel': title,
        'workspaceMeta': '',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def console_user_role_key(user) -> str:
    if user.is_superuser:
        return ROLE_SYSTEM_ADMIN
    roles = user_role_keys(user)
    for role in PRIMARY_ROLE_PRIORITY:
        if role in roles:
            return role
    return ''


def console_user_snapshot(user) -> dict:
    memberships = SalesTeamMember.objects.filter(user=user).order_by(
        'team__site__code', 'team__code', 'id'
    )
    return {
        'id': user.pk,
        'username': user.get_username(),
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'is_active': user.is_active,
        'is_superuser': user.is_superuser,
        'role_keys': sorted(user_role_keys(user)),
        'teams': [
            {
                'team_id': membership.team_id,
                'membership_role': membership.membership_role,
            }
            for membership in memberships
        ],
    }


def sales_team_snapshot(team) -> dict:
    return {
        'id': team.pk,
        'site_id': team.site_id,
        'code': team.code,
        'name': team.name,
        'enabled': team.enabled,
    }


def sales_team_usage(team, *, lock=False) -> dict[str, int]:
    querysets = {
        'active_members': SalesTeamMember.objects.filter(team=team, user__is_active=True),
        'open_leads': LeadSubmission.objects.filter(team=team).exclude(
            stage__in=['won', 'lost', 'spam']
        ),
        'actionable_tasks': Task.objects.filter(team=team, status__in=['open', 'in_progress']),
        'open_opportunities': Opportunity.objects.filter(team=team).exclude(stage__in=['won', 'lost']),
    }
    if lock:
        for queryset in querysets.values():
            list(queryset.select_for_update().values_list('pk', flat=True))
    return {key: queryset.count() for key, queryset in querysets.items()}


def sales_team_usage_queryset(queryset):
    """Attach governance counts without an N+1 query per team."""
    return queryset.annotate(
        active_member_count=Count(
            'memberships',
            filter=Q(memberships__user__is_active=True),
            distinct=True,
        ),
        open_lead_count=Count(
            'lead_submissions',
            filter=~Q(lead_submissions__stage__in=['won', 'lost', 'spam']),
            distinct=True,
        ),
        actionable_task_count=Count(
            'crm_tasks',
            filter=Q(crm_tasks__status__in=['open', 'in_progress']),
            distinct=True,
        ),
        open_opportunity_count=Count(
            'crm_opportunities',
            filter=~Q(crm_opportunities__stage__in=['won', 'lost']),
            distinct=True,
        ),
    )


def console_datetime_label(value, *, empty='从未登录') -> str:
    if not value:
        return empty
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime('%Y-%m-%d %H:%M')


def article_import_page_payload(*, locale_label: str) -> dict:
    return {
        'sectionKey': 'articles',
        'sectionLabel': '文章',
        'title': '批量导入文章',
        'description': '适合把一批规范文章一次性写入数据库。内容来源先整理成 zip + Markdown，再由后台导入。',
        'pageType': 'form',
        'workspaceLabel': '批量导入文章',
        'workspaceMeta': f'当前语言 {locale_label}。导入格式为 zip + Markdown，每篇文章一个文件。',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def asset_upload_page_payload() -> dict:
    return {
        'sectionKey': 'assets',
        'sectionLabel': '资产',
        'title': '上传资产',
        'description': '图片、视频、3D 和 PDF 先进入受控资产库，再由文章或前端代码引用。',
        'pageType': 'form',
        'workspaceLabel': '上传资产',
        'workspaceMeta': '运营在这里上传；程序员后续在资产列表里查询公开路径并引用。',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def asset_import_path_page_payload() -> dict:
    return {
        'sectionKey': 'assets',
        'sectionLabel': '资产',
        'title': '导入本机文件',
        'description': '把现有磁盘文件纳入受控资产库。适合导入 3D 模型、历史图片或客户交付文件。',
        'pageType': 'form',
        'workspaceLabel': '导入本机文件',
        'workspaceMeta': '文件会复制进受控资产库，再生成稳定公开路径，不直接引用随机磁盘目录。',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def three_d_profile_page_payload(*, title: str) -> dict:
    return {
        "sectionKey": "assets",
        "sectionLabel": "资产",
        "title": title,
        "description": "配置 3D 资产的展示方式，包括背景、初始视角、FOV、交互和页面槽位。",
        "pageType": "form",
        "workspaceLabel": title,
        "workspaceMeta": "",
        "searchPlaceholder": "",
        "tabs": [],
        "filters": [],
        "filterGroups": [],
        "actions": [],
        "scopeActions": [],
        "summary": [],
        "table": None,
        "notes": [],
    }


def three_d_placement_page_payload(*, title: str) -> dict:
    return {
        "sectionKey": "assets",
        "sectionLabel": "资产",
        "title": title,
        "description": "把页面里的固定位置绑定到一套 3D 展示配置。",
        "pageType": "form",
        "workspaceLabel": title,
        "workspaceMeta": "",
        "searchPlaceholder": "",
        "tabs": [],
        "filters": [],
        "filterGroups": [],
        "actions": [],
        "scopeActions": [],
        "summary": [],
        "table": None,
        "notes": [],
    }


@login_required
@require_GET
def home(request):
    return redirect_get_preserving_query(request, 'console:workbench')


@login_required
@require_GET
def marketing_attribution(request):
    """Canonical GET-only alias for the current attribution renderer."""

    return marketing(request)


@login_required
@require_GET
def content_articles(request):
    """Canonical GET-only alias for the current article workspace renderer."""

    content_locale_scope(request, 'content_articles')
    return articles(request)


@login_required
@require_GET
def sales_workspace(request):
    locale_code = requested_locale_code(request)
    active_view = (request.GET.get('view') or 'workbench').strip().lower()
    allowed_views = {'workbench', 'pipeline', 'companies', 'contacts', 'tasks'}
    if active_view not in allowed_views:
        active_view = 'workbench'
    if active_view == 'workbench':
        return redirect_get_preserving_query(
            request,
            'console:workbench',
            drop=('view',),
        )
    if active_view == 'pipeline':
        # The routed v2 opportunity page replaces only the legacy GET
        # renderer. Keep JSON and POST APIs available while old clients move.
        query = {'view': 'board'}
        legacy_to_v2 = {
            'q': 'q',
            'stage': 'stage',
            'owner': 'owner',
            'source_channel': 'source',
            'due': 'due',
            'sort': 'sort',
            'page': 'page',
            'page_size': 'page_size',
            'include_closed': 'include_closed',
        }
        for legacy_key, canonical_key in legacy_to_v2.items():
            value = (request.GET.get(legacy_key) or '').strip()
            if value:
                query[canonical_key] = (
                    ' '.join(value.split())[:160]
                    if canonical_key == 'q'
                    else value[:64]
                )
        target = reverse('console:opportunity_workspace_v2')
        return redirect(f'{target}?{urlencode(query)}')
    if active_view in {'companies', 'contacts'}:
        target_name = (
            'console:company_workspace_v2'
            if active_view == 'companies'
            else 'console:contact_workspace_v2'
        )
        query = {}
        for key in ('q', 'status', 'owner', 'team', 'sort', 'page', 'page_size'):
            value = (request.GET.get(key) or '').strip()
            if value:
                query[key] = ' '.join(value.split())[:160] if key == 'q' else value[:64]
        target = reverse(target_name)
        return redirect(f'{target}?{urlencode(query)}' if query else target)
    if active_view == 'tasks':
        query = {}
        for key in (
            'q', 'status', 'owner', 'team', 'due', 'priority', 'type',
            'sort', 'page', 'page_size',
        ):
            value = (request.GET.get(key) or '').strip()
            if value:
                query[key] = ' '.join(value.split())[:100] if key == 'q' else value[:64]
        target = reverse('console:task_workspace_v2')
        return redirect(f'{target}?{urlencode(query)}' if query else target)
    site, _resolved_locale = default_site_locale(locale_code)
    view_copy = {
        'workbench': (
            '今天先处理什么',
            '按逾期、到期和新线索优先级推进；客户资料、项目、任务和沟通记录保持关联。',
        ),
        'pipeline': (
            '推进每一个销售项目',
            '按阶段查看项目、预计金额和下一次跟进；成交与丢单需要保留明确依据。',
        ),
        'companies': (
            '看清客户组织与项目关系',
            '企业、联系人和销售项目统一归档，避免同一客户的信息散落在不同线索里。',
        ),
        'contacts': (
            '掌握关键联系人',
            '集中查看联系人、所属企业、联系方式和负责人，让每次沟通都有上下文。',
        ),
        'tasks': (
            '兑现下一步行动',
            '按到期时间处理电话、会议、报价和跟进任务，完成后留下可追溯结果。',
        ),
    }
    heading, intro = view_copy[active_view]
    can_bulk_assign = user_has_any_role(request.user, {ROLE_SALES_MANAGER, ROLE_SYSTEM_ADMIN})
    assignment_users = []
    if can_bulk_assign:
        for assignee in assignee_queryset_for_user(request.user, site=site):
            assignment_users.append(
                {
                    'id': assignee.id,
                    'label': assignee.get_full_name().strip() or assignee.get_username(),
                }
            )
    page_payload = {
        'sectionKey': 'sales',
        'sectionLabel': '销售工作台',
        'title': heading,
        'description': intro,
        'pageType': 'form',
        'workspaceLabel': '销售工作台',
        'workspaceMeta': '',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }
    sales_config = {
        'activeView': active_view,
        'currentUserId': request.user.id,
        'locale': locale_code or 'en',
        'urls': {
            'workbench': reverse('console:workbench_data'),
            'pipeline': reverse('console:pipeline_data'),
            'collection': reverse('console:crm_collection_data', args=['__collection__']),
            'record': reverse('console:crm_record_detail', args=['__type__', 0]),
            'activityCreate': reverse('console:activity_create'),
            'taskCreate': reverse('console:task_create'),
            'taskComplete': reverse('console:task_complete', args=[0]),
            'opportunityStage': reverse('console:opportunity_stage', args=[0]),
            'attachments': reverse('console:crm_attachments'),
            'savedView': reverse('console:saved_view_save'),
            'bulkAssign': reverse('console:sales_bulk_assign'),
            'leads': reverse('console:lead_workspace_v2'),
            'marketing': reverse('console:marketing'),
        },
        'canBulkAssign': can_bulk_assign,
        'assignmentUsers': assignment_users,
    }
    return render_form_console(
        request,
        section_key='opportunities' if active_view == 'pipeline' else 'sales',
        page_payload=page_payload,
        workspace_template='console/_sales_workspace.html',
        locale_code=locale_code,
        extra_stylesheets=['console/sales-workspace.css'],
        extra_scripts=['console/sales-workspace.js'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'sales_workspace': {
                'active_view': active_view,
                'leads_url': reverse('console:lead_workspace_v2'),
                'heading': heading,
                'intro': intro,
            },
            'sales_config': sales_config,
        },
    )


@login_required
@require_GET
def articles(request):
    site, locale, capabilities = content_locale_scope(request, 'articles')
    site_capabilities = effective_content_capabilities(
        request.user,
        site=site,
        locale=None,
    )
    selected_taxon = (request.GET.get('category') or request.GET.get('scope') or 'all').strip() or 'all'
    initial_panel = (request.GET.get('panel') or '').strip() or None
    page_payload, workspace = article_workspace_bundle(
        locale.locale_code,
        selected_taxon=selected_taxon,
        initial_panel=initial_panel,
        capabilities=capabilities,
        site_capabilities=site_capabilities,
        visible_locale_ids=readable_content_locale_ids(
            request,
            site=site,
            current_locale=locale,
            current_capabilities=capabilities,
        ),
    )
    return render_form_console(
        request,
        section_key='articles',
        page_payload=page_payload,
        workspace_template='console/_article_workspace.html',
        locale_code=workspace['locale']['code'],
        extra_stylesheets=['console/article-workspace.css'],
        extra_scripts=['console/article-workspace.js'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'article_workspace': workspace,
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def article_locale_create(request):
    site, locale, _capabilities = content_site_scope(request, 'article_locale_create')
    if request.method == 'POST':
        form = LocaleBootstrapForm(request.POST, site=site, current_locale=locale)
        if form.is_valid():
            source_locale = get_object_or_404(
                SiteLocale.objects.filter(site=site, enabled=True),
                locale_code=form.cleaned_data['source_locale'],
            )
            new_locale = create_locale_from_source(
                site=site,
                source_locale=source_locale,
                target_code=form.cleaned_data['target_locale'],
            )
            messages.success(request, f'语言已创建并初始化：{admin_locale_label(new_locale)}')
            return redirect(f"{reverse('console:articles')}?locale={new_locale.locale_code}&panel=locale-manager")
    else:
        form = LocaleBootstrapForm(site=site, current_locale=locale)

    return render_form_console(
        request,
        section_key='articles',
        page_payload={
            'sectionKey': 'articles',
            'sectionLabel': '文章',
            'title': '新增语言',
            'description': '从已有语言复制文章、分类、导航和页面结构，初始化新语言版本。',
            'pageType': 'form',
            'workspaceLabel': '新增语言',
            'workspaceMeta': '',
            'searchPlaceholder': '',
            'tabs': [],
            'filters': [],
            'filterGroups': [],
            'actions': [],
            'scopeActions': [],
            'summary': [],
            'table': None,
            'notes': [],
        },
        workspace_template='console/_locale_editor_workspace.html',
        locale_code=locale.locale_code,
        extra_context={
            'locale_form': form,
            'editor_locale': locale,
        },
    )


@login_required
@require_POST
def article_locale_enable(request, locale_id: int):
    site, locale, _capabilities = content_site_scope(request, 'article_locale_enable')
    target = get_object_or_404(SiteLocale.objects.filter(site=site), id=locale_id)
    enable_locale(locale=target)
    messages.success(request, f'语言已启用：{admin_locale_label(target)}')
    return redirect(f"{reverse('console:articles')}?locale={locale.locale_code}&panel=locale-manager")


@login_required
@require_POST
def article_locale_disable(request, locale_id: int):
    site, locale, _capabilities = content_site_scope(request, 'article_locale_disable')
    target = get_object_or_404(SiteLocale.objects.filter(site=site), id=locale_id)
    try:
        disable_locale(site=site, locale=target)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'语言已停用：{admin_locale_label(target)}')
    return redirect(f"{reverse('console:articles')}?locale={locale.locale_code}&panel=locale-manager")


@login_required
@require_POST
def article_locale_make_default(request, locale_id: int):
    site, _locale, _capabilities = content_site_scope(request, 'article_locale_make_default')
    target = get_object_or_404(SiteLocale.objects.filter(site=site), id=locale_id)
    set_default_locale(site=site, locale=target)
    messages.success(request, f'默认语言已切换为：{admin_locale_label(target)}')
    return redirect(f"{reverse('console:articles')}?locale={target.locale_code}&panel=locale-manager")

@login_required
@require_GET
def article_categories(request):
    _site, locale, _capabilities = content_locale_scope(request, 'article_categories')
    return redirect(f"{reverse('console:articles')}?locale={locale.locale_code}&panel=taxonomy-manager")


@login_required
@require_GET
def assets(request):
    site, _locale, capabilities = content_site_scope(request, 'assets')
    return render_console(
        request,
        section_key='assets',
        page_payload=assets_payload(
            site=site,
            capabilities=capabilities,
            include_internal_paths=ROLE_SYSTEM_ADMIN in user_role_keys(request.user),
        ),
    )

@login_required
@require_GET
def assets_three_d(request):
    site, _locale, capabilities = content_site_scope(request, 'assets_three_d')
    return render_form_console(
        request,
        section_key='assets',
        page_payload=three_d_page_payload(site=site, capabilities=capabilities),
        workspace_template='console/_three_d_workspace.html',
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            **three_d_workspace_context(site=site, capabilities=capabilities),
        },
    )


@login_required
@require_GET
def three_d_asset_detail(request, asset_id: int):
    site, _locale, capabilities = content_site_scope(request, 'three_d_asset_detail')
    asset = get_object_or_404(
        MediaAsset.objects.filter(site=site, asset_type='model3d'),
        id=asset_id,
    )
    return render_form_console(
        request,
        section_key='assets',
        page_payload=three_d_asset_page_payload(asset, capabilities=capabilities),
        workspace_template='console/_three_d_asset_detail_workspace.html',
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            **three_d_asset_detail_context(
                asset,
                capabilities=capabilities,
                include_internal_paths=ROLE_SYSTEM_ADMIN in user_role_keys(request.user),
            ),
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def three_d_profile_create(request):
    site, _locale, _capabilities = content_site_scope(request, 'three_d_profile_create')
    initial_asset_id = (request.GET.get('asset_id') or '').strip()
    asset_id = int(initial_asset_id) if initial_asset_id.isdigit() else None
    if request.method == 'POST':
        form = ThreeDViewProfileForm(request.POST, site=site, initial_asset_id=asset_id)
        if form.is_valid():
            profile = form.save()
            messages.success(request, f'3D 展示配置已创建：{profile.name}')
            return redirect(reverse('console:three_d_profile_edit', args=[profile.id]))
    else:
        form = ThreeDViewProfileForm(site=site, initial_asset_id=asset_id)

    return render_form_console(
        request,
        section_key='assets',
        page_payload=three_d_profile_page_payload(title='新建 3D 展示配置'),
        workspace_template='console/_three_d_profile_editor_workspace.html',
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'three_d_profile_form': form,
            'three_d_profile': None,
            'three_d_preview_url': '',
            'three_d_slot_label': '',
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def three_d_profile_edit(request, profile_id: int):
    site, _locale, _capabilities = content_site_scope(request, 'three_d_profile_edit')
    profile = get_object_or_404(
        ThreeDViewProfile.objects.select_related('asset', 'poster_asset').filter(site=site),
        id=profile_id,
    )
    if request.method == 'POST':
        form = ThreeDViewProfileForm(request.POST, site=site, profile=profile)
        if form.is_valid():
            profile = form.save()
            messages.success(request, f'3D 展示配置已更新：{profile.name}')
            return redirect(reverse('console:three_d_profile_edit', args=[profile.id]))
    else:
        form = ThreeDViewProfileForm(site=site, profile=profile)

    preview_url = (
        preview_public_url(build_splat_viewer_url(profile), request=request)
        if profile.asset.file_ext in {'.sog', '.ply'}
        else preview_public_url(profile.asset.public_path, request=request)
    )
    placement = ThreeDPlacement.objects.filter(site=site, profile=profile).order_by('sort_order', 'id').first()
    return render_form_console(
        request,
        section_key='assets',
        page_payload=three_d_profile_page_payload(title=profile.name),
        workspace_template='console/_three_d_profile_editor_workspace.html',
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'three_d_profile_form': form,
            'three_d_profile': profile,
            'three_d_preview_url': preview_url,
            'three_d_slot_label': slot_label(placement.slot_code) if placement else '',
            'three_d_placement': placement,
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def three_d_placement_edit(request, placement_id: int):
    site, _locale, _capabilities = content_site_scope(request, 'three_d_placement_edit')
    placement = get_object_or_404(
        ThreeDPlacement.objects.select_related('profile', 'profile__asset').filter(site=site),
        id=placement_id,
    )
    if request.method == 'POST':
        form = ThreeDPlacementForm(request.POST, site=site, placement=placement)
        if form.is_valid():
            placement = form.save()
            messages.success(request, f'3D 槽位已更新：{placement.name}')
            return redirect(reverse('console:three_d_placement_edit', args=[placement.id]))
    else:
        form = ThreeDPlacementForm(site=site, placement=placement)

    preview = placement_public_config(placement)
    preview_url = preview_public_url(str(preview.get('viewerUrl') or preview.get('modelUrl') or ''), request=request)
    return render_form_console(
        request,
        section_key='assets',
        page_payload=three_d_placement_page_payload(title=placement.name),
        workspace_template='console/_three_d_placement_editor_workspace.html',
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'three_d_placement_form': form,
            'three_d_placement': placement,
            'three_d_preview_url': preview_url,
            'three_d_public_config': preview,
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def three_d_placement_create(request):
    site, _locale, _capabilities = content_site_scope(request, 'three_d_placement_create')
    if request.method == 'POST':
        form = ThreeDPlacementForm(request.POST, site=site)
        if form.is_valid():
            placement = form.save()
            messages.success(request, f'3D 页面槽位已创建：{placement.name}')
            return redirect(reverse('console:three_d_placement_edit', args=[placement.id]))
    else:
        form = ThreeDPlacementForm(site=site)

    return render_form_console(
        request,
        section_key='assets',
        page_payload=three_d_placement_page_payload(title='新增页面槽位'),
        workspace_template='console/_three_d_placement_editor_workspace.html',
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'three_d_placement_form': form,
            'three_d_placement': None,
            'three_d_preview_url': '',
            'three_d_public_config': {},
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def asset_import_path(request):
    site, _locale, _capabilities = content_site_scope(request, 'asset_import_path')
    imported_asset = None
    if request.method == 'POST':
        form = AssetImportPathForm(request.POST)
        if form.is_valid():
            try:
                imported_asset = form.save(site=site, created_by=request.user.get_username() or 'django-console')
            except StorageSecurityError:
                form.add_error(None, '资产存储未配置或文件已不可用。')
            else:
                messages.success(request, f'文件已导入资产库：{imported_asset.public_path}')
                form = AssetImportPathForm()
    else:
        form = AssetImportPathForm()

    return render_form_console(
        request,
        section_key='assets',
        page_payload=asset_import_path_page_payload(),
        workspace_template='console/_asset_import_path_workspace.html',
        extra_context={
            'asset_path_form': form,
            'imported_asset': imported_asset,
        },
    )


@login_required
def marketing(request):
    return render_console(request, section_key='marketing', page_payload=marketing_payload())


@login_required
def marketing_privacy(request):
    site, locale = default_site_locale(requested_locale_code(request))
    is_system_admin = user_has_any_role(request.user, SYSTEM_ROLES)
    workspace = build_privacy_queue_workspace(
        site=site,
        request=request,
        system_admin=is_system_admin,
    )
    return render_form_console(
        request,
        section_key='marketing_privacy',
        page_payload={
            **privacy_request_page_payload(
                title='隐私请求',
                description='登记、核验并推进数据主体请求；高风险动作由系统管理员执行。',
                workspace_meta='营销处理身份与沟通，系统处理导出、匿名化和保留策略。',
            ),
            'pageType': 'table',
        },
        workspace_template='console/_privacy_queue_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/privacy-queue.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'privacy_queue': workspace,
            'is_system_admin': is_system_admin,
        },
    )


@login_required
def privacy_request_register(request):
    site, locale = default_site_locale(requested_locale_code(request))
    if request.method == 'POST':
        form = PrivacyRequestRegistrationForm(request.POST, site=site)
        if form.is_valid():
            with transaction.atomic():
                item, created = register_privacy_request(
                    site=site,
                    request_type=form.cleaned_data['request_type'],
                    requester_name=form.cleaned_data['requester_name'],
                    requester_email=form.cleaned_data['requester_email'],
                    requester_phone=form.cleaned_data['requester_phone'],
                    submission=form.cleaned_data['submission'],
                    source='console_ui',
                )
                if created:
                    record_audit(
                        actor=request.user,
                        action='privacy_request_created',
                        entity_table='privacy_request',
                        entity_id=item.id,
                        request=request,
                        metadata={'request_type': item.request_type},
                    )
            if created:
                messages.success(request, '隐私请求已登记，下一步先核验数据主体身份。')
            else:
                messages.info(request, '相同数据主体已有进行中的同类请求，已打开原记录。')
            return redirect(
                f'{reverse("console:privacy_request_manage", args=[item.id])}'
                f'?locale={locale.locale_code}'
            )
    else:
        form = PrivacyRequestRegistrationForm(site=site)

    return render_form_console(
        request,
        section_key='marketing_privacy',
        page_payload=privacy_request_page_payload(
            title='登记隐私请求',
            description='记录数据主体请求并关联 CRM 线索；相同主体的进行中请求会自动合并。',
            workspace_meta='只登记真实收到的请求。邮箱或电话至少填写一项。',
        ),
        workspace_template='console/_privacy_request_register_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/privacy-workflow.css'],
        extra_context={
            'privacy_registration_form': form,
            'editor_locale': locale,
            'privacy_back_url': reverse('console:marketing_privacy'),
        },
    )


def _add_privacy_form_errors(form: PrivacyRequestWorkflowForm, exc: ValidationError) -> None:
    errors = exc.message_dict if hasattr(exc, 'message_dict') else {'__all__': exc.messages}
    for field_name, messages_list in errors.items():
        target = field_name if field_name in form.fields else None
        for message in messages_list if isinstance(messages_list, (list, tuple)) else [messages_list]:
            form.add_error(target, message)


@login_required
def privacy_request_manage(request, privacy_request_id: int):
    site, locale = default_site_locale(requested_locale_code(request))
    item = get_object_or_404(
        PrivacyRequest.objects.select_related('handled_by_user', 'submission'),
        id=privacy_request_id,
        site=site,
    )
    is_system_admin = user_has_any_role(request.user, SYSTEM_ROLES)
    return_context = str(
        request.POST.get('return_to')
        or request.GET.get('return')
        or ''
    ).strip().lower()
    if return_context != 'governance' or not is_system_admin:
        return_context = ''
    if request.method == 'POST':
        form = PrivacyRequestWorkflowForm(request.POST, privacy_request=item)
        if form.is_valid():
            action = str(request.POST.get('_action') or '').strip().lower()
            saved = False
            with transaction.atomic():
                item = get_object_or_404(
                    PrivacyRequest.objects.select_for_update(of=('self',)).select_related(
                        'handled_by_user', 'submission'
                    ),
                    id=privacy_request_id,
                    site=site,
                )
                if not privacy_request_version_matches(
                    form.cleaned_data.get('version', ''),
                    item,
                ):
                    form.add_error(
                        None,
                        '这条隐私请求已被其他人员更新。请刷新页面并核对最新状态、范围和处理结论。',
                    )
                else:
                    before = privacy_request_snapshot(item)
                    try:
                        if action.startswith('transition:'):
                            target_status = action.split(':', 1)[1]
                            if target_status == 'completed':
                                if not is_system_admin:
                                    raise PermissionDenied
                                if item.request_type != 'export':
                                    raise ValidationError({
                                        'status': '非导出请求必须执行撤回、限制或删除动作后才能完成。'
                                    })
                            transition_privacy_request(
                                privacy_request=item,
                                target_status=target_status,
                                actor=request.user,
                                resolution=form.cleaned_data['resolution'],
                            )
                            record_audit(
                                actor=request.user,
                                action='privacy_request_status_changed',
                                entity_table='privacy_request',
                                entity_id=item.id,
                                before=before,
                                after=privacy_request_snapshot(item),
                                request=request,
                            )
                            messages.success(request, '隐私请求状态已更新。')
                        elif action == 'execute':
                            if not is_system_admin:
                                raise PermissionDenied
                            required_confirmation = 'DELETE' if item.request_type == 'delete' else 'EXECUTE'
                            if str(form.cleaned_data['confirm'] or '').strip() != required_confirmation:
                                raise ValidationError({
                                    'confirm': f'请输入 {required_confirmation} 确认执行。'
                                })
                            result = execute_privacy_action(
                                privacy_request=item,
                                actor=request.user,
                            )
                            record_audit(
                                actor=request.user,
                                action='privacy_request_executed',
                                entity_table='privacy_request',
                                entity_id=item.id,
                                before=before,
                                after=privacy_request_snapshot(item),
                                request=request,
                                metadata=result,
                            )
                            messages.success(request, '隐私动作已执行，系统已保存处理范围与审计记录。')
                        else:
                            raise ValidationError({'__all__': '未识别的处理动作。'})
                    except ValidationError as exc:
                        _add_privacy_form_errors(form, exc)
                    else:
                        saved = True
            if saved:
                target = (
                    f'{reverse("console:privacy_request_manage", args=[item.id])}'
                    f'?locale={locale.locale_code}'
                )
                if return_context:
                    target = f'{target}&return=governance'
                return redirect(target)
    else:
        form = PrivacyRequestWorkflowForm(
            initial={'resolution': item.resolution},
            privacy_request=item,
        )

    item.refresh_from_db()
    allowed = PRIVACY_TRANSITIONS.get(item.status, frozenset())
    status_rank = {'pending': 0, 'verified': 1, 'processing': 2, 'completed': 3}.get(item.status, 0)
    status_steps = []
    for index, (code, label) in enumerate((
        ('pending', '已登记'),
        ('verified', '已核验'),
        ('processing', '处理中'),
        ('completed', '已完成'),
    )):
        if item.status == 'rejected':
            state = 'done' if index == 0 or (index == 1 and item.verified_at) else 'upcoming'
        else:
            state = 'done' if index < status_rank else ('current' if index == status_rank else 'upcoming')
        status_steps.append({'code': code, 'label': label, 'state': state})

    type_labels = dict(PRIVACY_TYPE_CHOICES)
    status_labels = {
        'pending': '待核验',
        'verified': '已核验',
        'processing': '处理中',
        'completed': '已完成',
        'rejected': '已拒绝',
    }
    subject_record_count = privacy_subject_submissions(item).count()
    return render_form_console(
        request,
        section_key='marketing_privacy',
        page_payload=privacy_request_page_payload(
            title=f'隐私请求 #{item.id}',
            description='核验身份、记录处理依据，并按权限完成导出、撤回、限制或匿名化。',
            workspace_meta=f'{type_labels.get(item.request_type, item.request_type)} · {status_labels.get(item.status, item.status)}',
        ),
        workspace_template='console/_privacy_request_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/privacy-workflow.css'],
        extra_context={
            'privacy_request': item,
            'privacy_workflow_form': form,
            'privacy_type_label': type_labels.get(item.request_type, item.request_type),
            'privacy_status_label': status_labels.get(item.status, item.status),
            'privacy_status_steps': status_steps,
            'subject_record_count': subject_record_count,
            'is_system_admin': is_system_admin,
            'can_verify': 'verified' in allowed,
            'can_start_processing': 'processing' in allowed,
            'can_reject': 'rejected' in allowed,
            'can_complete_export': (
                is_system_admin and item.request_type == 'export' and item.status == 'processing'
            ),
            'can_export': (
                is_system_admin
                and item.request_type == 'export'
                and item.status in {'verified', 'processing', 'completed'}
            ),
            'can_execute': (
                is_system_admin
                and item.request_type != 'export'
                and item.status == 'processing'
            ),
            'required_confirmation': 'DELETE' if item.request_type == 'delete' else 'EXECUTE',
            'editor_locale': locale,
            'privacy_impact': privacy_request_impact(item),
            'privacy_back_url': (
                reverse('console:system_data_governance')
                if return_context
                else reverse('console:marketing_privacy')
            ),
            'privacy_return_context': return_context,
        },
    )


@login_required
def marketing_integration_edit(request, integration_id: int):
    site, locale = default_site_locale(requested_locale_code(request))
    integration = get_object_or_404(
        MarketingIntegration.objects.select_related('provider'),
        id=integration_id,
        site=site,
    )
    if request.method == 'POST' and request.POST.get('_action') == 'diagnose':
        before = integration_snapshot(integration)
        record_audit(
            actor=request.user,
            action='marketing_integration_diagnose_requested',
            entity_table='marketing_integration',
            entity_id=integration.id,
            before=before,
            request=request,
        )
        diagnostic, _evidence = record_integration_diagnostic(
            integration=integration,
            actor=request.user,
        )
        record_audit(
            actor=request.user,
            action='marketing_integration_diagnosed',
            entity_table='marketing_integration',
            entity_id=integration.id,
            before=before,
            after=integration_snapshot(integration),
            request=request,
            metadata={'diagnostic_overall': diagnostic.get('overall', 'unknown')},
        )
        if diagnostic['overall'] == 'pass':
            messages.success(request, f'连接诊断完成：{diagnostic["summary"]}')
        else:
            messages.warning(request, f'连接诊断完成：{diagnostic["summary"]}')
        return redirect(f'{reverse("console:marketing_integration_edit", args=[integration.id])}?locale={locale.locale_code}')
    if request.method == 'POST':
        form = MarketingIntegrationEditorForm(
            request.POST,
            integration=integration,
            updated_by=request.user.get_username() or 'django-console',
        )
        if form.is_valid():
            before = integration_snapshot(integration)
            record_audit(
                actor=request.user,
                action='marketing_integration_update_requested',
                entity_table='marketing_integration',
                entity_id=integration.id,
                before=before,
                request=request,
            )
            integration = form.save()
            record_audit(
                actor=request.user,
                action='marketing_integration_updated',
                entity_table='marketing_integration',
                entity_id=integration.id,
                before=before,
                after=integration_snapshot(integration),
                request=request,
            )
            messages.success(request, f'接入配置已更新：{integration.name}')
            return redirect(f'{reverse("console:marketing_integration_edit", args=[integration.id])}?locale={locale.locale_code}')
    else:
        form = MarketingIntegrationEditorForm(
            integration=integration,
            updated_by=request.user.get_username() or 'django-console',
        )

    outbox_counts = LeadEventOutbox.objects.filter(integration=integration).aggregate(
        pending=Count('id', filter=Q(status='pending')),
        sending=Count('id', filter=Q(status='sending')),
        processing=Count('id', filter=Q(status='processing')),
        partial=Count('id', filter=Q(status='partial')),
        failed=Count('id', filter=Q(status='failed')),
        validated=Count('id', filter=Q(status='validated')),
        sent=Count('id', filter=Q(status='sent')),
        skipped=Count('id', filter=Q(status='skipped')),
    )
    inbound_counts = LeadInboundEvent.objects.filter(integration=integration).aggregate(
        pending=Count('id', filter=Q(status__in=['pending', 'processing'])),
        failed=Count('id', filter=Q(status='failed')),
        processed=Count('id', filter=Q(status='processed')),
    )
    config = dict(integration.config_json or {})
    connection_status = form.connection_status()
    vault_last4 = secret_last4(integration.secret_ref.removeprefix('vault:')) if (integration.secret_ref or '').startswith('vault:') else ''
    callback_url = ''
    if form.is_meta_leadgen:
        callback_url = integration.site.base_url.rstrip('/') + '/api/webhooks/meta/leadgen/'
    elif form.is_whatsapp:
        callback_url = (
            integration.site.base_url.rstrip('/') + '/api/webhooks/whatsapp/ycloud/'
            if str(config.get('transport_provider') or 'meta').strip().lower() == 'ycloud'
            else integration.site.base_url.rstrip('/') + '/api/webhooks/whatsapp/'
        )
    return render_form_console(
        request,
        section_key='marketing',
        page_payload=marketing_integration_editor_page_payload(
            title=integration.name,
            description='维护平台 ID、加密密钥、Webhook、CRM 阶段回传与诊断状态。',
            locale_label=admin_locale_label(locale),
        ),
        workspace_template='console/_marketing_integration_editor_workspace.html',
        locale_code=locale.locale_code,
        extra_context={
            'marketing_integration_editor': form,
            'integration': integration,
            'editor_locale': locale,
            'connection_status': connection_status,
            'is_meta_pixel': form.is_meta_pixel,
            'is_meta_leadgen': form.is_meta_leadgen,
            'is_whatsapp': form.is_whatsapp,
            'whatsapp_transport_provider': str(config.get('transport_provider') or 'meta').strip().lower(),
            'is_webhook': form.is_webhook,
            'is_google': form.is_google,
            'callback_url': callback_url,
            'vault_last4': vault_last4,
            'current_test_event_code': str(config.get('test_event_code') or ''),
            'last_diagnostic': config.get('last_diagnostic') if isinstance(config.get('last_diagnostic'), dict) else None,
            'outbox_pending_count': outbox_counts['pending'] or 0,
            'outbox_sending_count': outbox_counts['sending'] or 0,
            'outbox_processing_count': outbox_counts['processing'] or 0,
            'outbox_partial_count': outbox_counts['partial'] or 0,
            'outbox_failed_count': outbox_counts['failed'] or 0,
            'outbox_validated_count': outbox_counts['validated'] or 0,
            'outbox_sent_count': outbox_counts['sent'] or 0,
            'outbox_skipped_count': outbox_counts['skipped'] or 0,
            'inbound_pending_count': inbound_counts['pending'] or 0,
            'inbound_failed_count': inbound_counts['failed'] or 0,
            'inbound_processed_count': inbound_counts['processed'] or 0,
        },
    )


@login_required
def leads(request):
    if request.method in {'GET', 'HEAD'}:
        # Keep the historical route name for exports, editor callbacks and
        # old bookmarks, but remove the duplicate GET workspace. Only queue
        # state with a direct v2 equivalent is carried forward.
        target = reverse('console:lead_workspace_v2')
        if (request.GET.get('tab') or 'submissions').strip() == 'submissions':
            legacy_to_v2 = {
                'q': 'q',
                'stage': 'stage',
                'lead_locale': 'language',
                'source_channel': 'source',
                'assignee': 'owner',
                'page': 'page',
                'page_size': 'page_size',
            }
            query = {}
            for legacy_key, v2_key in legacy_to_v2.items():
                value = (request.GET.get(legacy_key) or '').strip()
                if value and value != 'all':
                    query[v2_key] = (
                        ' '.join(value.split())[:120]
                        if v2_key == 'q'
                        else value[:64]
                    )
            follow_up = (request.GET.get('follow_up') or '').strip()
            if follow_up == 'unassigned':
                query['owner'] = 'unassigned'
            elif follow_up in {'overdue', 'due'}:
                query['sla'] = 'overdue'
            if query:
                target = f'{target}?{urlencode(query)}'
        return redirect(target)

    locale_code = requested_locale_code(request)
    workspace = build_leads_workspace(request, locale_code)
    page_payload = {
        'sectionKey': 'leads',
        'sectionLabel': '线索管理',
        'title': '线索收件箱',
        'description': '集中处理网站、Meta、WhatsApp 与搜索广告带来的新线索。',
        'pageType': 'form',
        'workspaceLabel': '线索管理',
        'workspaceMeta': '',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }
    return render_form_console(
        request,
        section_key='leads',
        page_payload=page_payload,
        workspace_template='console/_leads_workspace.html',
        locale_code=locale_code,
        extra_stylesheets=['console/leads-workspace.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'leads_workspace': workspace,
        },
    )


@login_required
def marketing_forms(request):
    site, locale = default_site_locale(requested_locale_code(request))
    workspace = build_acquisition_forms_workspace(
        site=site,
        request=request,
    )
    return render_form_console(
        request,
        section_key='marketing_forms',
        page_payload={
            **lead_form_editor_page_payload(
                title='获客表单',
                description='管理公开表单的投放范围、字段、通知与阶段回传。',
                locale_label=admin_locale_label(locale),
            ),
            'pageType': 'table',
        },
        workspace_template='console/_acquisition_forms_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/acquisition-forms.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'acquisition_forms': workspace,
            'can_manage_email_delivery': user_has_any_role(request.user, SYSTEM_ROLES),
        },
    )


def _smtp_delivery_snapshot(delivery) -> dict:
    return {
        'source': delivery.source,
        'ready': bool(getattr(delivery, 'ready', False)),
        'host': delivery.host,
        'port': delivery.port,
        'security': (
            'ssl' if delivery.use_ssl else ('tls' if delivery.use_tls else 'none')
        ),
        'username': delivery.username,
        'from_email': delivery.from_email,
    }


@login_required
def smtp_settings(request):
    site, locale = default_site_locale(requested_locale_code(request))
    lead_form = LeadFormDefinition.objects.filter(site=site, code='project-inquiry').first()
    recipients = form_notification_recipients(lead_form) if lead_form else []
    default_email = recipients[0] if recipients else ''
    action = (request.POST.get('action') or 'save').strip() if request.method == 'POST' else ''
    form = SmtpSettingsForm(
        request.POST or None,
        updated_by=request.user.get_username() or 'django-console',
        default_email=default_email,
    )
    if request.method == 'POST' and form.is_valid():
        before = _smtp_delivery_snapshot(load_runtime_email_config())
        try:
            with transaction.atomic():
                form.save()
                after = _smtp_delivery_snapshot(load_runtime_email_config())
                record_audit(
                    actor=request.user,
                    action=(
                        'smtp_configuration_deleted'
                        if form.cleaned_data.get('clear_stored_config')
                        else 'smtp_configuration_updated'
                    ),
                    entity_table='admin_secret_store',
                    entity_id=None,
                    before=before,
                    after=after,
                    request=request,
                    metadata={
                        'password_changed': bool(form.cleaned_data.get('password')),
                        'test_requested': action == 'save_test',
                    },
                )
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            if form.cleaned_data.get('clear_stored_config'):
                messages.success(request, '后台 SMTP 配置已删除，系统将重新读取环境变量配置。')
                return redirect(f'{reverse("console:system_email")}?locale={locale.locale_code}')
            if action == 'save_test':
                sent, error = send_smtp_test_notification(form.cleaned_data['test_recipient'])
                record_audit(
                    actor=request.user,
                    action='smtp_test_completed',
                    entity_table='admin_secret_store',
                    entity_id=None,
                    request=request,
                    metadata={
                        'succeeded': sent,
                        'recipient': form.cleaned_data['test_recipient'],
                        'error': error if not sent else '',
                    },
                )
                if sent:
                    messages.success(request, f'SMTP 配置已保存，测试邮件已发送到 {form.cleaned_data["test_recipient"]}。')
                    return redirect(f'{reverse("console:system_email")}?locale={locale.locale_code}')
                form.add_error(None, f'配置已保存，但测试邮件发送失败：{error}')
            else:
                messages.success(request, 'SMTP 配置已加密保存。')
                return redirect(f'{reverse("console:system_email")}?locale={locale.locale_code}')

    delivery = load_runtime_email_config()
    source_label = {
        'vault': '后台加密配置',
        'environment': '服务器环境变量',
        'django-backend': 'Django 邮件后端',
        'error': '配置读取失败',
    }.get(delivery.source, delivery.source or '未设置')
    return render_form_console(
        request,
        section_key='system_email',
        page_payload=system_page_payload(
            title='邮件投递',
            description='管理后台通知使用的发件服务、加密凭据与真实投递测试。',
        ),
        workspace_template='console/_smtp_settings_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/system-email.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'smtp_settings_form': form,
            'smtp_delivery': delivery,
            'smtp_source_label': source_label,
            'smtp_password_last4': smtp_password_last4(),
            'smtp_notification_recipients': recipients,
            'editor_locale': locale,
        },
    )


def _retention_policy_snapshot(policy: RetentionPolicy) -> dict:
    return {
        'site_id': policy.site_id,
        'enabled': bool(policy.enabled),
        'lead_pii_retention_days': policy.lead_pii_retention_days,
        'inbound_payload_retention_days': policy.inbound_payload_retention_days,
        'outbox_payload_retention_days': policy.outbox_payload_retention_days,
        'privacy_request_retention_days': policy.privacy_request_retention_days,
        'whatsapp_message_retention_days': policy.whatsapp_message_retention_days,
        'whatsapp_media_retention_days': policy.whatsapp_media_retention_days,
        'updated_by_user_id': policy.updated_by_user_id,
        'config_json': policy.config_json or {},
    }


@login_required
def system_data_governance(request):
    site, locale = default_site_locale(requested_locale_code(request))
    workspace = build_data_governance_workspace(site=site)
    policy = workspace['policy']
    action = str(request.POST.get('_action') or '').strip().lower()
    policy_form = RetentionPolicyForm(policy=policy)
    execution_form = RetentionExecutionForm(
        initial={'version': workspace['policy_version']}
    )

    if request.method == 'POST' and action == 'save_policy':
        policy_form = RetentionPolicyForm(request.POST, policy=policy)
        if policy_form.is_valid():
            saved = False
            with transaction.atomic():
                policy = get_object_or_404(
                    RetentionPolicy.objects.select_for_update(),
                    id=policy.id,
                    site=site,
                )
                if not retention_policy_version_matches(
                    policy_form.cleaned_data.get('version', ''),
                    policy,
                ):
                    policy_form.add_error(
                        None,
                        '数据保留策略已被其他管理员更新。请刷新页面并核对最新天数与候选范围。',
                    )
                else:
                    before = _retention_policy_snapshot(policy)
                    policy_form.policy = policy
                    policy = policy_form.save(actor=request.user)
                    record_audit(
                        actor=request.user,
                        action='privacy_retention_policy_updated',
                        entity_table='privacy_retention_policy',
                        entity_id=policy.id,
                        before=before,
                        after=_retention_policy_snapshot(policy),
                        request=request,
                    )
                    saved = True
            if saved:
                messages.success(request, '数据保留策略已保存；候选影响已按新策略重新计算。')
                return redirect(
                    f'{reverse("console:system_data_governance")}?locale={locale.locale_code}'
                )
    elif request.method == 'POST' and action == 'execute_retention':
        execution_form = RetentionExecutionForm(request.POST)
        if execution_form.is_valid():
            executed = False
            try:
                with transaction.atomic():
                    policy = get_object_or_404(
                        RetentionPolicy.objects.select_for_update(),
                        id=policy.id,
                        site=site,
                    )
                    if not retention_policy_version_matches(
                        execution_form.cleaned_data.get('version', ''),
                        policy,
                    ):
                        execution_form.add_error(
                            None,
                            '数据保留策略或候选范围已经改变。请刷新页面并重新确认。',
                        )
                    else:
                        result = execute_retention_policy(
                            policy=policy,
                            actor=request.user,
                        )
                        record_audit(
                            actor=request.user,
                            action='privacy_retention_policy_executed',
                            entity_table='privacy_retention_policy',
                            entity_id=policy.id,
                            request=request,
                            metadata=result,
                        )
                        executed = True
            except ValidationError as exc:
                errors = exc.message_dict if hasattr(exc, 'message_dict') else {'__all__': exc.messages}
                for field_name, error_messages in errors.items():
                    target = field_name if field_name in execution_form.fields else None
                    for message in error_messages if isinstance(error_messages, (list, tuple)) else [error_messages]:
                        execution_form.add_error(target, message)
            if executed:
                messages.success(request, '数据保留策略已执行；实际清理数量已写入审计记录。')
                return redirect(
                    f'{reverse("console:system_data_governance")}?locale={locale.locale_code}'
                )
    elif request.method == 'POST':
        policy_form.add_error(None, '未识别的数据治理操作。')

    return render_form_console(
        request,
        section_key='system_data_governance',
        page_payload=system_page_payload(
            title='数据治理',
            description='管理高风险隐私动作、数据保留策略、候选影响和不可变审计证据。',
        ),
        workspace_template='console/_data_governance_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/privacy-queue.css', 'console/data-governance.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'data_governance': workspace,
            'retention_policy_form': policy_form,
            'retention_execution_form': execution_form,
        },
    )


@login_required
def lead_form_create(request):
    site, locale = default_site_locale(requested_locale_code(request))
    if request.method == 'POST':
        form = LeadFormEditorForm(request.POST, site=site)
        if form.is_valid():
            with transaction.atomic():
                lead_form = form.save()
                record_audit(
                    actor=request.user,
                    action='lead_form_created',
                    entity_table='lead_form_definition',
                    entity_id=lead_form.id,
                    after=lead_form_snapshot(lead_form),
                    request=request,
                )
            messages.success(request, f'线索表单已保存：{lead_form.name}')
            return redirect(f'{reverse("console:marketing_form_edit", args=[lead_form.id])}?locale={locale.locale_code}')
    else:
        form = LeadFormEditorForm(site=site)

    return render_form_console(
        request,
        section_key='marketing_forms',
        page_payload=lead_form_editor_page_payload(
            title='新建线索表单',
            description='定义网站表单代码、业务分类、作用域以及 Meta 回传事件名。',
            locale_label=admin_locale_label(locale),
        ),
        workspace_template='console/_lead_form_editor_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/lead-form-editor.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'lead_form_editor': form,
            'lead_field_rows': form.field_rows(),
            'editor_mode': 'create',
            'editor_locale': locale,
            'editor_back_url': reverse('console:marketing_forms'),
        },
    )


@login_required
def lead_form_edit(request, form_id: int):
    site, locale = default_site_locale(requested_locale_code(request))
    lead_form = get_object_or_404(LeadFormDefinition, id=form_id, site=site)
    if request.method == 'POST':
        saved = False
        with transaction.atomic():
            lead_form = get_object_or_404(
                LeadFormDefinition.objects.select_for_update(),
                id=form_id,
                site=site,
            )
            version_is_current = lead_form_version_matches(
                request.POST.get('version', ''),
                lead_form,
            )
            form = LeadFormEditorForm(
                request.POST,
                site=site,
                form_definition=lead_form,
            )
            if form.is_valid():
                if not version_is_current:
                    form.add_error(
                        None,
                        '表单已被其他运营人员更新。请刷新页面，核对最新字段、通知和投放范围后再保存。',
                    )
                else:
                    before = lead_form_snapshot(lead_form)
                    lead_form = form.save()
                    record_audit(
                        actor=request.user,
                        action='lead_form_updated',
                        entity_table='lead_form_definition',
                        entity_id=lead_form.id,
                        before=before,
                        after=lead_form_snapshot(lead_form),
                        request=request,
                    )
                    saved = True
        if saved:
            messages.success(request, f'线索表单已更新：{lead_form.name}')
            return redirect(f'{reverse("console:marketing_form_edit", args=[lead_form.id])}?locale={locale.locale_code}')
    else:
        form = LeadFormEditorForm(site=site, form_definition=lead_form)

    return render_form_console(
        request,
        section_key='marketing_forms',
        page_payload=lead_form_editor_page_payload(
            title=lead_form.name,
            description='这里维护公开接口、作用域、字段 JSON 和 Meta 事件名。',
            locale_label=admin_locale_label(locale),
        ),
        workspace_template='console/_lead_form_editor_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/lead-form-editor.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'lead_form_editor': form,
            'lead_field_rows': form.field_rows(),
            'editor_mode': 'edit',
            'lead_form': lead_form,
            'editor_locale': locale,
            'editor_back_url': reverse('console:marketing_forms'),
        },
    )


@login_required
def lead_submission_edit(request, submission_id: int):
    site, locale = default_site_locale(requested_locale_code(request))
    submission = get_object_or_404(
        lead_queryset_for_user(
            LeadSubmission.objects.select_related(
                'form', 'locale', 'route', 'cta', 'assignee', 'team', 'duplicate_of'
            ),
            request.user,
        ),
        id=submission_id,
        site=site,
    )
    if request.method == 'POST':
        form = LeadSubmissionEditorForm(request.POST, submission=submission, actor=request.user)
        if form.is_valid():
            before = lead_submission_snapshot(submission)
            record_audit(
                actor=request.user,
                action='lead_update_requested',
                entity_table='lead_submission',
                entity_id=submission.id,
                before=before,
                request=request,
            )
            form.save_operations()
            submission, queued, attempted, succeeded = update_submission_stage(
                submission=submission,
                stage=form.cleaned_data['stage'],
                buyer_value=form.cleaned_data['buyer_value'],
                buyer_currency=form.cleaned_data['buyer_currency'],
            )
            record_audit(
                actor=request.user,
                action='lead_updated',
                entity_table='lead_submission',
                entity_id=submission.id,
                before=before,
                after=lead_submission_snapshot(submission),
                request=request,
                metadata={
                    'queued_events': queued,
                    'dispatch_attempted': attempted,
                    'dispatch_succeeded': succeeded,
                },
            )
            messages.success(
                request,
                f'线索已更新：阶段={submission.stage}，新排队 {queued} 条，尝试发送 {attempted} 条，成功 {succeeded} 条。',
            )
            return redirect(f'{reverse("console:lead_submission_edit", args=[submission.id])}?locale={locale.locale_code}')
    else:
        form = LeadSubmissionEditorForm(submission=submission, actor=request.user)

    conversion = (
        LeadConversion.objects.select_related('company', 'contact', 'opportunity')
        .filter(submission=submission)
        .first()
    )

    return render_form_console(
        request,
        section_key='leads',
        page_payload=lead_submission_editor_page_payload(
            title=submission.full_name or submission.company or f'线索 {submission.id}',
            description='在这里核验客户信息、安排下一步，并将合格线索建立为销售项目。',
            locale_label=admin_locale_label(locale),
        ),
        workspace_template='console/_lead_submission_editor_workspace.html',
        locale_code=locale.locale_code,
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'lead_submission_editor': form,
            'submission': submission,
            'editor_locale': locale,
            'lead_conversion': conversion,
            'can_convert_lead': conversion is None and submission.stage in {'qualified', 'won'},
            'show_lead_technical_details': user_has_any_role(request.user, MARKETING_ROLES),
        },
        extra_stylesheets=['console/sales-workspace.css'],
        extra_scripts=['console/lead-detail-workspace.js'],
    )


@login_required
def lead_submission_export_csv(request):
    site, _locale = default_site_locale(requested_locale_code(request))
    body, count = build_leads_csv(lead_export_queryset(request, site))
    filename = f'vorntek-leads-{timezone.now():%Y%m%d-%H%M}.csv'
    response = HttpResponse(body, content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['X-Export-Count'] = str(count)
    return response


@login_required
def lead_submission_export_xlsx(request):
    site, _locale = default_site_locale(requested_locale_code(request))
    workbook, count = build_leads_xlsx(lead_export_queryset(request, site))
    filename = f'vorntek-leads-{timezone.now():%Y%m%d-%H%M}.xlsx'
    response = FileResponse(
        workbook,
        as_attachment=True,
        filename=filename,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['X-Export-Count'] = str(count)
    return response


@login_required
@require_POST
def lead_submission_bulk_stage(request):
    site, locale = default_site_locale(requested_locale_code(request))
    ids = [int(value) for value in request.POST.getlist('submission_ids') if str(value).isdigit()]
    stage = (request.POST.get('bulk_stage') or '').strip()
    fallback_url = f'{reverse("console:leads")}?locale={locale.locale_code}'
    next_url = safe_next_url(request, request.POST.get('next'), fallback_url)

    if not ids:
        messages.error(request, '请先勾选至少一条线索。')
        return redirect(next_url)

    if stage not in {'new', 'contacted', 'lost'}:
        messages.error(request, '批量推进只支持：新提交、已联系、已流失。高质量线索必须逐条完成合格判定。')
        return redirect(next_url)

    submissions = list(
        lead_queryset_for_user(
            LeadSubmission.objects.filter(site=site, id__in=ids),
            request.user,
        )
    )
    updated = 0
    for submission in submissions:
        before = lead_submission_snapshot(submission)
        record_audit(
            actor=request.user,
            action='lead_bulk_stage_requested',
            entity_table='lead_submission',
            entity_id=submission.id,
            before=before,
            request=request,
            metadata={'target_stage': stage},
        )
        update_submission_stage(
            submission=submission,
            stage=stage,
            buyer_value=submission.buyer_value,
            buyer_currency=submission.buyer_currency or 'USD',
        )
        record_audit(
            actor=request.user,
            action='lead_bulk_stage_updated',
            entity_table='lead_submission',
            entity_id=submission.id,
            before=before,
            after=lead_submission_snapshot(submission),
            request=request,
        )
        updated += 1

    messages.success(request, f'已批量更新 {updated} 条线索。')
    return redirect(next_url)


@login_required
@require_POST
def lead_outbox_dispatch(request, outbox_id: int):
    """Compatibility alias for the canonical exact-ID retry contract.

    The historical implementation dispatched any pending/failed record and
    bypassed the due-time, enabled-integration, attempt-limit, claim, audit and
    redaction rules used by Event Operations.  Keep the route name for old
    forms, but make the canonical service the only mutation path.
    """

    from .marketing_views import marketing_outbox_retry

    return marketing_outbox_retry(request, outbox_id)


@login_required
@require_POST
def lead_outbox_dispatch_pending(request):
    """Fail closed instead of scanning an implicit site-wide batch.

    Safe recovery now requires selecting one exact event in Event Operations.
    A future high-risk batch action needs an explicit scope, confirmation,
    upper bound and per-record result contract; the old ``limit=50`` endpoint
    supplied none of those guarantees.
    """

    return JsonResponse(
        {
            'ok': False,
            'code': 'legacy_batch_dispatch_retired',
            'message': '全站模糊批量回传已停用；请在事件运营中逐条确认并精确重试。',
            'events_url': reverse('console:marketing_events'),
        },
        status=410,
    )


@login_required
def system_users(request):
    search = (request.GET.get('q') or '').strip()
    role_filter = (request.GET.get('role') or '').strip()
    status_filter = (request.GET.get('status') or '').strip()
    team_filter = (request.GET.get('team') or '').strip()

    users = User.objects.all()
    if search:
        users = users.filter(
            Q(username__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
        )
    if role_filter == ROLE_SYSTEM_ADMIN:
        users = users.filter(
            Q(is_superuser=True) | Q(groups__name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])
        )
    elif role_filter in GROUP_BY_ROLE:
        users = users.filter(groups__name=GROUP_BY_ROLE[role_filter])
    elif role_filter == 'unassigned':
        users = users.exclude(groups__name__in=GROUP_BY_ROLE.values()).filter(is_superuser=False)
    elif role_filter == 'conflicted':
        users = users.annotate(
            console_role_count=Count(
                'groups',
                filter=Q(groups__name__in=GROUP_BY_ROLE.values()),
                distinct=True,
            )
        ).filter(console_role_count__gt=1, is_superuser=False)
    if status_filter == 'active':
        users = users.filter(is_active=True)
    elif status_filter == 'inactive':
        users = users.filter(is_active=False)
    if team_filter.isdigit():
        users = users.filter(sales_team_memberships__team_id=int(team_filter))

    users = users.distinct().prefetch_related(
        'groups', 'sales_team_memberships__team__site'
    ).order_by('-is_active', 'username')
    user_rows = []
    for user in users:
        role_keys = user_role_keys(user)
        role_key = console_user_role_key(user)
        memberships = sorted(
            user.sales_team_memberships.all(),
            key=lambda item: (item.team.site.name, item.team.name, item.id),
        )
        user_rows.append({
            'id': user.id,
            'username': user.get_username(),
            'display_name': user.get_full_name().strip() or user.get_username(),
            'email': user.email,
            'is_active': user.is_active,
            'is_self': user.pk == request.user.pk,
            'role_key': role_key,
            'role_label': ROLE_LABELS.get(role_key, '未分配角色'),
            'role_conflict': len(role_keys) > 1,
            'teams': [f'{item.team.site.name} / {item.team.name}' for item in memberships],
            'last_login': console_datetime_label(user.last_login),
            'date_joined': console_datetime_label(user.date_joined, empty='未知'),
        })

    all_users = User.objects.all()
    sales_group_names = [GROUP_BY_ROLE[ROLE_SALES], GROUP_BY_ROLE[ROLE_SALES_MANAGER]]
    higher_priority_group_names = [
        GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN],
        GROUP_BY_ROLE[ROLE_MARKETING_OPS],
        GROUP_BY_ROLE[ROLE_CONTENT_OPS],
    ]
    stats = {
        'total': all_users.count(),
        'active': all_users.filter(is_active=True).count(),
        'unassigned': all_users.exclude(groups__name__in=GROUP_BY_ROLE.values()).filter(
            is_superuser=False, is_active=True
        ).distinct().count(),
        'conflicted': all_users.annotate(
            console_role_count=Count(
                'groups',
                filter=Q(groups__name__in=GROUP_BY_ROLE.values()),
                distinct=True,
            )
        ).filter(console_role_count__gt=1, is_active=True, is_superuser=False).count(),
        'needs_team': all_users.filter(
            groups__name__in=sales_group_names, is_active=True
        ).exclude(
            is_superuser=True
        ).exclude(
            groups__name__in=higher_priority_group_names
        ).exclude(
            sales_team_memberships__team__enabled=True
        ).distinct().count(),
        'inactive': all_users.filter(is_active=False).count(),
    }
    teams = list(
        SalesTeam.objects.select_related('site')
        .annotate(
            member_count=Count(
                'memberships',
                filter=Q(memberships__user__is_active=True),
                distinct=True,
            )
        )
        .order_by('site__name', 'name', 'id')
    )
    return render_form_console(
        request,
        section_key='system_users',
        page_payload=system_page_payload(
            title='账号与角色',
            description='管理后台身份、主角色、团队归属和登录状态。',
        ),
        workspace_template='console/_system_users_workspace.html',
        extra_stylesheets=['console/system-users.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'system_user_rows': user_rows,
            'system_user_stats': stats,
            'system_teams': teams,
            'role_choices': ROLE_LABELS.items(),
            'selected_search': search,
            'selected_role': role_filter,
            'selected_status': status_filter,
            'selected_team': team_filter,
        },
    )


@login_required
def system_teams(request):
    search = ' '.join((request.GET.get('q') or '').split())
    status_filter = (request.GET.get('status') or '').strip()
    site_filter = (request.GET.get('site') or '').strip()

    teams = SalesTeam.objects.select_related('site')
    if search:
        teams = teams.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(site__name__icontains=search)
        )
    if status_filter == 'enabled':
        teams = teams.filter(enabled=True)
    elif status_filter == 'disabled':
        teams = teams.filter(enabled=False)
    if site_filter.isdigit():
        teams = teams.filter(site_id=int(site_filter))

    team_rows = []
    filtered_teams = sales_team_usage_queryset(teams).order_by(
        '-enabled', 'site__name', 'name', 'id'
    )
    for team in filtered_teams:
        usage = {
            'active_members': team.active_member_count,
            'open_leads': team.open_lead_count,
            'actionable_tasks': team.actionable_task_count,
            'open_opportunities': team.open_opportunity_count,
        }
        team_rows.append({
            'id': team.pk,
            'name': team.name,
            'code': team.code,
            'site_name': team.site.name,
            'enabled': team.enabled,
            'active_members': usage['active_members'],
            'open_leads': usage['open_leads'],
            'actionable_tasks': usage['actionable_tasks'],
            'open_opportunities': usage['open_opportunities'],
            'has_blockers': bool(sum(usage.values())),
        })

    all_teams = list(sales_team_usage_queryset(SalesTeam.objects.all()).order_by('id'))
    stats = {
        'total': len(all_teams),
        'enabled': sum(1 for team in all_teams if team.enabled),
        'with_members': sum(1 for team in all_teams if team.active_member_count),
        'with_work': sum(
            1 for team in all_teams
            if team.open_lead_count
            or team.actionable_task_count
            or team.open_opportunity_count
        ),
    }
    sites = Site.objects.order_by('name', 'id')
    return render_form_console(
        request,
        section_key='system_teams',
        page_payload=system_page_payload(
            title='销售团队',
            description='管理团队范围、成员归属和仍在进行的销售工作。',
        ),
        workspace_template='console/_system_teams_workspace.html',
        extra_stylesheets=['console/system-users.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'system_team_rows': team_rows,
            'system_team_stats': stats,
            'system_sites': sites,
            'selected_search': search,
            'selected_status': status_filter,
            'selected_site': site_filter,
        },
    )


@login_required
def system_user_create(request):
    form = ConsoleUserForm(request.POST or None, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        plaintext_password = form.cleaned_data['new_password']
        with transaction.atomic():
            user = form.save()
            record_audit(
                actor=request.user,
                action='console_user_created',
                entity_table='auth_user',
                entity_id=user.id,
                after=console_user_snapshot(user),
                request=request,
                metadata={'password_changed': True},
            )
        return render_system_user_editor(
            request,
            form=ConsoleUserForm(instance=user, actor=request.user),
            editor_mode='edit',
            edited_user=user,
            one_time_credentials={
                'username': user.get_username(),
                'password': plaintext_password,
                'action': '账号已创建',
            },
        )

    return render_system_user_editor(
        request,
        form=form,
        editor_mode='create',
    )


@login_required
def system_user_edit(request, user_id: int):
    edited_user = get_object_or_404(User, pk=user_id)
    if request.method == 'POST':
        saved = False
        with transaction.atomic():
            edited_user = get_object_or_404(User.objects.select_for_update(), pk=user_id)
            before = console_user_snapshot(edited_user)
            version_is_current = system_user_version_matches(
                request.POST.get('version', ''), edited_user
            )
            form = ConsoleUserForm(request.POST, instance=edited_user, actor=request.user)
            if form.is_valid():
                if not version_is_current:
                    form.add_error(None, '账号已被其他管理员更新。请刷新页面，核对最新角色、团队和状态后再保存。')
                else:
                    password_changed = bool(form.cleaned_data.get('new_password'))
                    edited_user = form.save()
                    record_audit(
                        actor=request.user,
                        action='console_user_updated',
                        entity_table='auth_user',
                        entity_id=edited_user.id,
                        before=before,
                        after=console_user_snapshot(edited_user),
                        request=request,
                        metadata={'password_changed': password_changed},
                    )
                    saved = True
        if saved:
            if password_changed:
                return render_system_user_editor(
                    request,
                    form=ConsoleUserForm(instance=edited_user, actor=request.user),
                    editor_mode='edit',
                    edited_user=edited_user,
                    one_time_credentials={
                        'username': edited_user.get_username(),
                        'password': form.cleaned_data['new_password'],
                        'action': '密码已重置',
                    },
                )
            messages.success(request, f'后台账号已更新：{edited_user.get_username()}')
            return redirect('console:system_user_edit', user_id=edited_user.id)
    else:
        form = ConsoleUserForm(instance=edited_user, actor=request.user)

    return render_system_user_editor(
        request,
        form=form,
        editor_mode='edit',
        edited_user=edited_user,
    )


@login_required
def system_team_create(request):
    form = SalesTeamForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            team = form.save()
            record_audit(
                actor=request.user,
                action='sales_team_created',
                entity_table='sales_team',
                entity_id=team.id,
                after=sales_team_snapshot(team),
                request=request,
            )
        messages.success(request, f'销售团队已创建：{team.name}')
        return redirect('console:system_team_edit', team_id=team.id)

    return render_form_console(
        request,
        section_key='system_teams',
        page_payload=system_page_payload(
            title='新建销售团队',
            description='建立站点内的销售数据访问范围。',
        ),
        workspace_template='console/_system_team_editor_workspace.html',
        extra_stylesheets=['console/system-users.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'sales_team_form': form,
            'editor_mode': 'create',
            'edited_team': None,
        },
    )


@login_required
def system_team_edit(request, team_id: int):
    edited_team = get_object_or_404(SalesTeam.objects.select_related('site'), pk=team_id)
    if request.method == 'POST':
        saved = False
        with transaction.atomic():
            edited_team = get_object_or_404(
                SalesTeam.objects.select_for_update().select_related('site'), pk=team_id
            )
            before = sales_team_snapshot(edited_team)
            was_enabled = edited_team.enabled
            version_is_current = sales_team_version_matches(
                request.POST.get('version', ''), edited_team
            )
            form = SalesTeamForm(request.POST, instance=edited_team)
            if form.is_valid():
                if not version_is_current:
                    form.add_error(None, '团队已被其他管理员更新。请刷新页面核对最新状态后再保存。')
                else:
                    disabling = was_enabled and not form.cleaned_data.get('enabled')
                    blockers = sales_team_usage(edited_team, lock=disabling)
                    blocking_total = sum(blockers.values())
                    if disabling and blocking_total:
                        form.add_error(
                            'enabled',
                            '该团队仍有业务范围，不能停用：'
                            f'启用成员 {blockers["active_members"]}、'
                            f'开放线索 {blockers["open_leads"]}、'
                            f'可执行任务 {blockers["actionable_tasks"]}、'
                            f'开放销售机会 {blockers["open_opportunities"]}。'
                            '请先迁移成员和记录。',
                        )
                    else:
                        edited_team = form.save()
                        record_audit(
                            actor=request.user,
                            action='sales_team_updated',
                            entity_table='sales_team',
                            entity_id=edited_team.id,
                            before=before,
                            after=sales_team_snapshot(edited_team),
                            request=request,
                        )
                        saved = True
        if saved:
            messages.success(request, f'销售团队已更新：{edited_team.name}')
            return redirect('console:system_team_edit', team_id=edited_team.id)
    else:
        form = SalesTeamForm(instance=edited_team)

    usage = sales_team_usage(edited_team)

    return render_form_console(
        request,
        section_key='system_teams',
        page_payload=system_page_payload(
            title=edited_team.name,
            description='维护团队名称和启用状态。',
        ),
        workspace_template='console/_system_team_editor_workspace.html',
        extra_stylesheets=['console/system-users.css'],
        extra_context={
            'hide_toolrows': True,
            'hide_topbar_center': True,
            'sales_team_form': form,
            'editor_mode': 'edit',
            'edited_team': edited_team,
            'team_member_count': usage['active_members'],
            'team_open_lead_count': usage['open_leads'],
            'team_actionable_task_count': usage['actionable_tasks'],
            'team_open_opportunity_count': usage['open_opportunities'],
        },
    )


@csrf_exempt
def public_lead_form_definition(request, form_code: str):
    if request.method == 'OPTIONS':
        return apply_cors_headers(request, HttpResponse(status=204))
    if request.method != 'GET':
        return apply_cors_headers(request, JsonResponse({'ok': False, 'error': 'Method not allowed.'}, status=405))

    lead_form = LeadFormDefinition.objects.select_related('locale').filter(code=form_code, status='active').first()
    if not lead_form:
        return apply_cors_headers(request, JsonResponse({'ok': False, 'error': 'Form not found.'}, status=404))

    schema = lead_form.form_schema or default_form_schema()
    fields = []
    for item in (schema.get('fields') or []):
        if not isinstance(item, dict):
            continue
        if 'enabled' in item and not bool(item.get('enabled')):
            continue
        field_name = str(item.get('name') or item.get('key') or '').strip()
        if not field_name:
            continue
        fields.append(
            {
                'name': field_name,
                'type': str(item.get('type') or 'text').strip(),
                'label': str(item.get('label') or '').strip(),
                'placeholder': str(item.get('placeholder') or '').strip(),
                'required': bool(item.get('required')),
            }
        )

    response = JsonResponse(
        {
            'ok': True,
            'form': {
                'code': lead_form.code,
                'name': lead_form.name,
                'locale': lead_form.locale.locale_code if lead_form.locale else None,
                'success_message': lead_form.success_message or '',
                'fields': fields,
            },
        }
    )
    return apply_cors_headers(request, response)


@csrf_exempt
def public_marketing_measurement_config(request):
    if request.method == 'OPTIONS':
        return apply_cors_headers(request, HttpResponse(status=204))
    if request.method != 'GET':
        return apply_cors_headers(request, JsonResponse({'ok': False, 'error': 'Method not allowed.'}, status=405))

    response = JsonResponse({'ok': True, **public_measurement_config()})
    response['Cache-Control'] = 'no-store, max-age=0'
    response['X-Content-Type-Options'] = 'nosniff'
    return apply_cors_headers(request, response)


@login_required
@require_GET
def releases(request):
    site, _locale, capabilities = content_site_scope(request, 'releases')
    return render_console(
        request,
        section_key='releases',
        page_payload=releases_payload(
            site=site,
            capabilities=capabilities,
            include_diagnostics=ROLE_SYSTEM_ADMIN in user_role_keys(request.user),
        ),
    )


@login_required
@require_http_methods(['GET', 'POST'])
def article_create(request):
    site, locale, capabilities = content_locale_scope(request, 'article_create')
    allow_publish = CONTENT_SET_PUBLISHED in capabilities
    asset_capabilities = effective_content_capabilities(
        request.user,
        site=site,
        locale=None,
    )
    can_read_assets = ASSETS_READ in asset_capabilities
    can_write_assets = can_read_assets and ASSETS_WRITE in asset_capabilities
    if request.method == 'POST':
        form = ArticleEditorForm(
            request.POST,
            request.FILES,
            site=site,
            locale=locale,
            allow_publish=allow_publish,
            allow_assets=can_read_assets,
        )
        if form.is_valid():
            article = form.save()
            messages.success(request, f'文章已保存：{article.title}')
            return redirect(f'{reverse("console:article_edit", args=[article.id])}?locale={locale.locale_code}')
    else:
        form = ArticleEditorForm(
            site=site,
            locale=locale,
            allow_publish=allow_publish,
            allow_assets=can_read_assets,
        )

    return render_form_console(
        request,
        section_key='articles',
        page_payload=article_editor_page_payload(
            title='新建文章',
            description='文章适用于新闻、帮助、案例等规范化内容。运营在这里录入标题、摘要、正文、SEO 和封面。',
            locale_label=admin_locale_label(locale),
        ),
        workspace_template='console/_article_editor_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/article-workspace.css'],
        extra_context={
            'editor_form': form,
            'editor_mode': 'create',
            'editor_path': f'/{locale.locale_code}/articles/{{slug}}/',
            'preview_url': '',
            'editor_locale': locale,
            'category_tree': category_tree_data(site, locale),
            'selected_category_id': None,
            'canReadAssets': can_read_assets,
            'canWriteAssets': can_write_assets,
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def article_edit(request, article_id: int):
    site, locale, capabilities = content_locale_scope(request, 'article_edit')
    article = get_object_or_404(
        Article.objects.select_related('route', 'category'),
        id=article_id,
        route__site=site,
        route__locale=locale,
    )
    allow_publish = CONTENT_SET_PUBLISHED in capabilities
    if article.status == 'published' and not allow_publish:
        raise PermissionDenied('当前账号不能修改已发布文章。')
    asset_capabilities = effective_content_capabilities(
        request.user,
        site=site,
        locale=None,
    )
    can_read_assets = ASSETS_READ in asset_capabilities
    can_write_assets = can_read_assets and ASSETS_WRITE in asset_capabilities
    if request.method == 'POST':
        form = ArticleEditorForm(
            request.POST,
            request.FILES,
            site=site,
            locale=locale,
            article=article,
            allow_publish=allow_publish,
            allow_assets=can_read_assets,
        )
        if form.is_valid():
            article = form.save()
            messages.success(request, f'文章已更新：{article.title}')
            return redirect(f'{reverse("console:article_edit", args=[article.id])}?locale={locale.locale_code}')
    else:
        form = ArticleEditorForm(
            site=site,
            locale=locale,
            article=article,
            allow_publish=allow_publish,
            allow_assets=can_read_assets,
        )

    return render_form_console(
        request,
        section_key='articles',
        page_payload=article_editor_page_payload(
            title=article.title,
            description='这里直接维护文章正文、封面、SEO 和发布状态。程序员也可以在这里查询稳定访问路径。',
            locale_label=admin_locale_label(locale),
        ),
        workspace_template='console/_article_editor_workspace.html',
        locale_code=locale.locale_code,
        extra_stylesheets=['console/article-workspace.css'],
        extra_context={
            'editor_form': form,
            'editor_mode': 'edit',
            'article': article,
            'editor_path': article.route.path,
            'preview_url': f'{PUBLIC_PREVIEW_BASE_URL}{article.route.path}',
            'editor_locale': locale,
            'category_tree': category_tree_data(site, locale),
            'selected_category_id': article.category_id,
            'canReadAssets': can_read_assets,
            'canWriteAssets': can_write_assets,
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def article_import(request):
    site, locale, capabilities = content_locale_scope(request, 'article_import')
    allow_publish = CONTENT_SET_PUBLISHED in capabilities
    import_result = None
    if request.method == 'POST':
        form = ArticleImportZipForm(
            request.POST,
            request.FILES,
            site=site,
            locale=locale,
            allow_publish=allow_publish,
        )
        if form.is_valid():
            try:
                import_result = form.save()
            except forms.ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f'导入完成：新增 {import_result["created"]} 篇，更新 {import_result["updated"]} 篇，'
                    f'失败 {import_result["failed"]} 篇，跳过 {import_result["skipped"]} 个文件。',
                )
    else:
        form = ArticleImportZipForm(site=site, locale=locale, allow_publish=allow_publish)

    return render_form_console(
        request,
        section_key='articles',
        page_payload=article_import_page_payload(locale_label=admin_locale_label(locale)),
        workspace_template='console/_article_import_workspace.html',
        locale_code=locale.locale_code,
        extra_context={
            'import_form': form,
            'import_result': import_result,
            'editor_locale': locale,
        },
    )


@login_required
@require_GET
def article_export(request):
    site, locale, _capabilities = content_locale_scope(request, 'article_export')
    archive, count = build_articles_export_zip(site=site, locale=locale)
    filename = f'articles-{locale.locale_code}-{count}.zip'
    return FileResponse(archive, as_attachment=True, filename=filename, content_type='application/zip')


@login_required
@require_http_methods(['GET', 'POST'])
def category_create(request):
    site, locale, capabilities = content_locale_scope(request, 'category_create')
    allow_publish = CONTENT_SET_PUBLISHED in capabilities
    if request.method == 'POST':
        form = CategoryEditorForm(
            request.POST,
            site=site,
            locale=locale,
            allow_publish=allow_publish,
        )
        if form.is_valid():
            category = form.save()
            messages.success(request, f'分类已保存：{category.name}')
            return redirect(f'{reverse("console:category_edit", args=[category.id])}?locale={locale.locale_code}')
    else:
        form = CategoryEditorForm(site=site, locale=locale, allow_publish=allow_publish)

    return render_form_console(
        request,
        section_key='articles',
        page_payload=category_editor_page_payload(
            title='新建分类',
            description='分类决定文章列表、筛选和 SEO 结构。语言是外层维度，分类本身表达业务层级。',
            locale_label=admin_locale_label(locale),
        ),
        workspace_template='console/_category_editor_workspace.html',
        locale_code=locale.locale_code,
        extra_context={
            'category_form': form,
            'editor_mode': 'create',
            'editor_locale': locale,
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def category_edit(request, category_id: int):
    site, locale, capabilities = content_locale_scope(request, 'category_edit')
    category = get_object_or_404(Category.objects.select_related('parent'), id=category_id, site=site, locale=locale)
    allow_publish = CONTENT_SET_PUBLISHED in capabilities
    if category.status == 'published' and not allow_publish:
        raise PermissionDenied('当前账号不能修改已发布分类。')

    if request.method == 'POST':
        form = CategoryEditorForm(
            request.POST,
            site=site,
            locale=locale,
            category=category,
            allow_publish=allow_publish,
        )
        if form.is_valid():
            category = form.save()
            messages.success(request, f'分类已更新：{category.name}')
            return redirect(f'{reverse("console:category_edit", args=[category.id])}?locale={locale.locale_code}')
    else:
        form = CategoryEditorForm(
            site=site,
            locale=locale,
            category=category,
            allow_publish=allow_publish,
        )

    return render_form_console(
        request,
        section_key='articles',
        page_payload=category_editor_page_payload(
            title=category.name,
            description='这里维护分类层级、URL 别名、排序和 SEO。文章编辑器会直接复用这棵分类树。',
            locale_label=admin_locale_label(locale),
        ),
        workspace_template='console/_category_editor_workspace.html',
        locale_code=locale.locale_code,
        extra_context={
            'category_form': form,
            'editor_mode': 'edit',
            'editor_locale': locale,
            'category': category,
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def asset_upload(request):
    site, _locale, _capabilities = content_site_scope(request, 'asset_upload')
    uploaded_asset = None
    if request.method == 'POST':
        form = AssetUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                uploaded_asset = form.save(site=site, created_by=request.user.get_username() or 'django-console')
            except StorageSecurityError:
                form.add_error(None, '资产存储未配置或不可用。')
            else:
                messages.success(request, f'资产已入库：{uploaded_asset.public_path}')
                form = AssetUploadForm()
    else:
        form = AssetUploadForm()

    return render_form_console(
        request,
        section_key='assets',
        page_payload=asset_upload_page_payload(),
        workspace_template='console/_asset_upload_workspace.html',
        extra_context={
            'asset_form': form,
            'uploaded_asset': uploaded_asset,
        },
    )


@login_required
@require_GET
def asset_file(request, asset_id: int):
    site, _locale, _capabilities = content_site_scope(request, 'asset_file')
    asset = get_object_or_404(MediaAsset, id=asset_id, site=site)
    try:
        source = resolve_storage_asset_path(asset.storage_path)
        stream = source.open('rb')
    except (StorageSecurityError, OSError):
        raise Http404('资产文件不可用。') from None
    # Existing rows may predate the fixed MIME allowlist, so do not trust the
    # persisted/client-originated mime_type at read time either.
    response = FileResponse(stream, content_type=asset_mime_type(source.name))
    for header, value in private_file_response_headers().items():
        response[header] = value
    return response


@csrf_exempt
def public_lead_submit(request, form_code: str):
    if request.method == 'OPTIONS':
        return apply_cors_headers(request, HttpResponse(status=204))
    if request.method != 'POST':
        return apply_cors_headers(request, JsonResponse({'ok': False, 'error': 'Method not allowed.'}, status=405))

    site, locale = default_site_locale(requested_locale_code(request))
    lead_form = get_object_or_404(LeadFormDefinition, site=site, code=form_code, status='active')
    try:
        payload = json.loads(request.body.decode('utf-8')) if request.body else {}
    except json.JSONDecodeError:
        return apply_cors_headers(request, JsonResponse({'ok': False, 'error': 'Invalid JSON payload.'}, status=400))

    if not isinstance(payload, dict):
        return apply_cors_headers(request, JsonResponse({'ok': False, 'error': 'The request body must be a JSON object.'}, status=400))

    try:
        result = create_submission(
            form_definition=lead_form,
            locale=locale if lead_form.locale_id is None else lead_form.locale,
            payload=payload,
            client_ip=request_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
    except LeadCaptureError as exc:
        response = JsonResponse({'ok': False, 'error': str(exc), 'code': exc.code}, status=exc.status)
        if exc.status == 429:
            response['Retry-After'] = '600'
        return apply_cors_headers(request, response)
    except ValueError as exc:
        return apply_cors_headers(request, JsonResponse({'ok': False, 'error': str(exc)}, status=400))

    return apply_cors_headers(
        request,
        JsonResponse(
            {
                'ok': True,
                'submission_id': result.submission.id,
                'submission_key': str(result.submission.submission_key),
                'queued_events': result.queued_events,
                'dispatch_attempted': result.dispatch_attempted,
                'dispatch_succeeded': result.dispatch_succeeded,
                'duplicate': result.duplicate,
                'notification_sent': result.notification_sent,
                'message': lead_form.success_message or 'Your inquiry has been received.',
            }
        ),
    )



@login_required
@require_POST
def release_build_preview(request):
    site, _locale, capabilities = content_site_scope(request, 'release_build_preview')
    actor = request.user.get_username() or 'django-console'
    try:
        result = run_preview_build(created_by=actor, site_code=site.code)
    except PreviewBuildInProgress:
        messages.warning(request, '该站点已有预览构建正在进行，请稍后重试。')
    except PreviewBuildConfigurationError:
        messages.error(request, '预览构建环境尚未安全配置，请联系系统管理员。')
    except PreviewBuildError:
        messages.error(request, '预览构建无法安全执行，请联系系统管理员。')
    else:
        if result.succeeded:
            messages.success(request, f'预览产物已生成：{result.build.build_key}')
        else:
            messages.error(request, f'预览构建未完成：{result.build.build_key}。')
    destination = 'console:releases' if RELEASES_READ in capabilities else 'console:workbench'
    return redirect(destination)
