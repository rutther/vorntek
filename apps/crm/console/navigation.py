from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from django.db.models import Q
from django.urls import reverse

from .access import (
    ALL_CONSOLE_ROLES,
    CONTENT_ROLES,
    LEAD_ROLES,
    MARKETING_ROLES,
    ROLE_CONTENT_OPS,
    ROLE_SYSTEM_ADMIN,
    SYSTEM_ROLES,
    user_role_keys,
)
from .content_access import ASSETS_READ, CONTENT_READ, RELEASES_READ


@dataclass(frozen=True)
class AdminSection:
    key: str
    label: str
    route_name: str
    description: str
    roles: frozenset[str]
    icon: str
    href_suffix: str = ''


@dataclass(frozen=True)
class AdminDomain:
    """One stable top-level product domain in the canonical console IA."""

    key: str
    label: str
    route_name: str
    description: str
    roles: frozenset[str]
    icon: str


@dataclass(frozen=True)
class ContentNavigationEntry:
    """One usable second-level entry in the website-content domain."""

    key: str
    label: str
    route_name: str
    description: str
    capability: str
    site_wide_only: bool


# Content routes currently resolve the one live console site by this code in
# ``payloads.default_site_locale``.  Navigation must use the same boundary:
# a grant for a different site is valid policy data, but it is not a usable
# entrance in this single-site console yet.
CONSOLE_CONTENT_SITE_CODE = 'siteos_demo'


CONTENT_NAVIGATION_ENTRIES: tuple[ContentNavigationEntry, ...] = (
    ContentNavigationEntry(
        key='articles',
        label='文章与页面',
        route_name='console:content_articles',
        description='文章、分类与语言内容',
        capability=CONTENT_READ,
        site_wide_only=False,
    ),
    ContentNavigationEntry(
        key='assets',
        label='素材库',
        route_name='console:assets',
        description='图片、视频、PDF 与 3D 素材',
        capability=ASSETS_READ,
        site_wide_only=True,
    ),
    ContentNavigationEntry(
        key='releases',
        label='预览与快照',
        route_name='console:releases',
        description='预览构建、内容快照与受控部署记录',
        capability=RELEASES_READ,
        site_wide_only=True,
    ),
)


ADMIN_SECTIONS: tuple[AdminSection, ...] = (
    AdminSection(
        key='sales',
        label='今日工作',
        route_name='console:sales_workspace',
        description='待办与需要立即响应的客户',
        roles=LEAD_ROLES,
        icon='list-todo',
    ),
    AdminSection(
        key='leads',
        label='线索',
        route_name='console:lead_workspace_v2',
        description='收件箱、分配与资格判断',
        roles=LEAD_ROLES,
        icon='inbox',
    ),
    AdminSection(
        key='opportunities',
        label='销售机会',
        route_name='console:opportunity_workspace_v2',
        description='项目管道、报价与成交进度',
        roles=LEAD_ROLES,
        icon='badge-dollar-sign',
    ),
    AdminSection(
        key='marketing',
        label='营销回传',
        route_name='console:marketing',
        description='Meta、广告来源与 CAPI 状态',
        roles=MARKETING_ROLES,
        icon='radio-tower',
    ),
    AdminSection(
        key='articles',
        label='内容与网站',
        route_name='console:articles',
        description='文章、产品页与搜索内容',
        roles=CONTENT_ROLES,
        icon='panels-top-left',
    ),
    AdminSection(
        key='assets',
        label='素材库',
        route_name='console:assets',
        description='图片、视频、3D 与 PDF 文件',
        roles=CONTENT_ROLES,
        icon='images',
    ),
    AdminSection(
        key='releases',
        label='预览与快照',
        route_name='console:releases',
        description='预览构建、内容快照与受控部署记录',
        roles=CONTENT_ROLES,
        icon='rocket',
    ),
    AdminSection(
        key='system',
        label='系统设置',
        route_name='console:system_users',
        description='用户、角色与销售团队',
        roles=SYSTEM_ROLES,
        icon='settings',
    ),
)


SECTION_MAP = {section.key: section for section in ADMIN_SECTIONS}


# The legacy section list above remains a payload compatibility contract for
# pages that have not migrated to v2.  New and migrated shells render only this
# five-domain navigation.  The content domain uses CONTENT_ROLES as a coarse
# gate and then resolves its usable second-level entries from exact grants.
ADMIN_DOMAINS: tuple[AdminDomain, ...] = (
    AdminDomain(
        key='workbench',
        label='工作台',
        route_name='console:workbench',
        description='按角色汇总现在最需要处理的工作',
        roles=ALL_CONSOLE_ROLES,
        icon='layout-dashboard',
    ),
    AdminDomain(
        key='sales',
        label='销售',
        route_name='console:lead_workspace_v2',
        description='线索、客户、商机与跟进任务',
        roles=LEAD_ROLES,
        icon='briefcase-business',
    ),
    AdminDomain(
        key='marketing',
        label='营销',
        route_name='console:marketing_events',
        description='归因、平台接入与事件证据',
        roles=MARKETING_ROLES,
        icon='radio-tower',
    ),
    AdminDomain(
        key='content',
        label='网站内容',
        route_name='console:content_articles',
        description='文章、素材、3D 与预览快照',
        roles=CONTENT_ROLES,
        icon='panels-top-left',
    ),
    AdminDomain(
        key='system',
        label='系统',
        route_name='console:system_users',
        description='用户、权限、配置与数据治理',
        roles=SYSTEM_ROLES,
        icon='settings',
    ),
)


LEGACY_SECTION_DOMAIN = {
    'home': 'workbench',
    'workbench': 'workbench',
    'sales': 'sales',
    'leads': 'sales',
    'whatsapp': 'sales',
    'opportunities': 'sales',
    'companies': 'sales',
    'customer_pool': 'sales',
    'contacts': 'sales',
    'tasks': 'sales',
    'marketing': 'marketing',
    'marketing_events': 'marketing',
    'marketing_attribution': 'marketing',
    'marketing_forms': 'marketing',
    'marketing_form_create': 'marketing',
    'marketing_form_edit': 'marketing',
    'marketing_privacy': 'marketing',
    'privacy_request_register': 'marketing',
    'privacy_request_manage': 'marketing',
    'articles': 'content',
    'content_articles': 'content',
    'assets': 'content',
    'releases': 'content',
    'content': 'content',
    'system': 'system',
    'system_users': 'system',
    'system_teams': 'system',
    'system_email': 'system',
    'system_data_governance': 'system',
    'smtp_settings': 'system',
}


def active_domain_key(active_key: str) -> str:
    """Resolve a legacy page key to its canonical top-level domain."""

    return LEGACY_SECTION_DOMAIN.get(str(active_key or '').strip(), str(active_key or '').strip())


def _valid_user_id(user) -> int | None:
    user_id = getattr(user, 'pk', None)
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        return None
    return user_id


def content_navigation_entries(
    user,
    *,
    active_key: str = '',
    role_keys=None,
) -> list[dict[str, object]]:
    """Return only second-level content entrances the user can actually open.

    System administrators retain the complete content IA without a grant
    lookup.  Content operators are resolved fail-closed from enabled direct or
    group grants on the console's currently reachable site.  Article reads may
    be locale-scoped and preserve that locale in the link.  Asset and release
    list routes resolve site-wide policy, so locale-only grants deliberately do
    not advertise those otherwise-dead entrances.

    Write, publish, local-import, and preview-build capabilities never imply a
    read entrance.
    """

    roles = frozenset(user_role_keys(user) if role_keys is None else role_keys)
    if ROLE_SYSTEM_ADMIN in roles:
        grant_rows = [
            {
                'capability': entry.capability,
                'locale_id': None,
                'locale__locale_code': None,
            }
            for entry in CONTENT_NAVIGATION_ENTRIES
        ]
    elif ROLE_CONTENT_OPS in roles:
        user_id = _valid_user_id(user)
        if user_id is None or not getattr(user, 'is_authenticated', False):
            return []

        # Imported lazily so URL discovery and schema tooling do not depend on
        # the unmanaged grant table until a real content-operator navigation
        # decision is required.
        from .models import ContentAccessGrant

        group_ids = user.groups.values('id')
        grant_rows = list(
            ContentAccessGrant.objects.filter(
                enabled=True,
                site__code=CONSOLE_CONTENT_SITE_CODE,
                site__enabled=True,
                capability__in={
                    entry.capability for entry in CONTENT_NAVIGATION_ENTRIES
                },
            )
            .filter(Q(user_id=user_id) | Q(group_id__in=group_ids))
            .filter(Q(locale_id__isnull=True) | Q(locale__enabled=True))
            .values('capability', 'locale_id', 'locale__locale_code')
            .order_by('locale__sort_order', 'locale__locale_code', 'id')
        )
    else:
        return []

    normalized_active_key = str(active_key or '').strip()
    if normalized_active_key == 'content_articles':
        normalized_active_key = 'articles'

    entries: list[dict[str, object]] = []
    for definition in CONTENT_NAVIGATION_ENTRIES:
        matching_rows = [
            row
            for row in grant_rows
            if row['capability'] == definition.capability
            and (not definition.site_wide_only or row['locale_id'] is None)
        ]
        if not matching_rows:
            continue

        # Prefer a site-wide article grant because its canonical URL opens the
        # default locale.  With only locale grants, preserve the first enabled
        # locale in deterministic site ordering so the domain never lands on a
        # locale the user cannot read.
        selected_row = next(
            (row for row in matching_rows if row['locale_id'] is None),
            matching_rows[0],
        )
        href = reverse(definition.route_name)
        locale_code = selected_row['locale__locale_code']
        if not definition.site_wide_only and locale_code:
            href = f'{href}?{urlencode({"locale": locale_code})}'
        entries.append(
            {
                'key': definition.key,
                'label': definition.label,
                'description': definition.description,
                'href': href,
                'active': definition.key == normalized_active_key,
            }
        )

    if entries and active_domain_key(normalized_active_key) == 'content' and not any(
        entry['active'] for entry in entries
    ):
        entries[0]['active'] = True
    return entries


def build_navigation(active_key: str, user) -> list[dict[str, object]]:
    """Return the canonical role- and capability-aware navigation payload."""

    active_domain = active_domain_key(active_key)
    role_keys = frozenset(user_role_keys(user))
    content_entries = content_navigation_entries(
        user,
        active_key=active_key,
        role_keys=role_keys,
    )
    navigation: list[dict[str, object]] = []
    for domain in ADMIN_DOMAINS:
        if not role_keys.intersection(domain.roles):
            continue
        if domain.key == 'content' and not content_entries:
            continue

        item: dict[str, object] = {
            'key': domain.key,
            'label': domain.label,
            'description': domain.description,
            'icon': domain.icon,
            'href': (
                content_entries[0]['href']
                if domain.key == 'content'
                else reverse(domain.route_name)
            ),
            'active': domain.key == active_domain,
        }
        if domain.key == 'content':
            item['children'] = content_entries
        elif domain.key == 'sales':
            sales_entries = (
                ('whatsapp', 'WhatsApp 会话', 'console:whatsapp_workspace_v2', '客户消息、模板与跟进上下文', ''),
                ('leads', '线索', 'console:lead_workspace_v2', '收件、分配与资格判断', ''),
                ('customer_pool', '客户公海', 'console:customer_pool', '多来源客户、领取与来源证据', ''),
                ('opportunities', '销售机会', 'console:opportunity_workspace_v2', '阶段、金额与下一步', ''),
                ('companies', '企业', 'console:company_workspace_v2', '客户组织与关系摘要', ''),
                ('contacts', '联系人', 'console:contact_workspace_v2', '人员、渠道与客户上下文', ''),
                ('tasks', '任务', 'console:task_workspace_v2', '待办、到期与执行结果', ''),
            )
            item['children'] = [
                {
                    'key': key,
                    'label': label,
                    'description': description,
                    'href': f'{reverse(route_name)}{suffix}',
                    'active': key == active_key,
                }
                for key, label, route_name, description, suffix in sales_entries
            ]
        elif domain.key == 'marketing':
            marketing_entries = (
                ('marketing_events', '事件运营', 'console:marketing_events', '平台事件收发、失败恢复与证据'),
                ('marketing_forms', '获客表单', 'console:marketing_forms', '公开表单、字段、通知与回传'),
                ('marketing_privacy', '隐私请求', 'console:marketing_privacy', '登记、核验与处理数据主体请求'),
                ('marketing', '平台与归因', 'console:marketing', '来源漏斗、平台接入与归因规则'),
            )
            item['children'] = [
                {
                    'key': key,
                    'label': label,
                    'description': description,
                    'href': reverse(route_name),
                    'active': key == active_key,
                }
                for key, label, route_name, description in marketing_entries
            ]
        elif domain.key == 'system':
            system_entries = (
                ('system_users', '账号与角色', 'console:system_users', '后台身份、角色与登录状态'),
                ('system_teams', '销售团队', 'console:system_teams', '团队范围、成员与业务占用'),
                ('system_email', '邮件投递', 'console:system_email', '发件服务、密钥与运行状态'),
                ('system_data_governance', '数据治理', 'console:system_data_governance', '隐私执行、数据保留与审计证据'),
            )
            item['children'] = [
                {
                    'key': key,
                    'label': label,
                    'description': description,
                    'href': reverse(route_name),
                    'active': key == active_key,
                }
                for key, label, route_name, description in system_entries
            ]
        navigation.append(item)
    return navigation
