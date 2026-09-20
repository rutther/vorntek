from __future__ import annotations

import os
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone

from leads.models import (
    LeadFormDefinition,
    LeadSubmission,
)
from leads.readiness import checks_overall, inspect_marketing_integration
from marketing.models import MarketingIntegration, ProviderEventMapping, TrackingRule
from sitecore.models import Article, Cta, MediaAsset, PageRoute, Release, ReleaseBuild, Site, SiteLocale

from .content_access import (
    ASSETS_IMPORT_LOCAL,
    ASSETS_READ,
    ASSETS_WRITE,
    CONTENT_READ,
    RELEASES_CANDIDATE_BUILD,
    RELEASES_PREVIEW_BUILD,
    RELEASES_READ,
)
from .marketing_queries import build_marketing_overview
from .navigation import SECTION_MAP


PUBLIC_PREVIEW_BASE_URL = os.getenv('SITEOS_PREVIEW_BASE_URL', 'http://127.0.0.1:25000').rstrip('/')


def admin_locale_label(locale: SiteLocale) -> str:
    labels = {
        'en': '英语',
        'ar': '阿拉伯语',
        'fr': '法语',
        'es': '西班牙语',
        'ru': '俄语',
    }
    return labels.get(locale.locale_code, locale.label)


def enabled_site_locales(site: Site) -> list[SiteLocale]:
    return list(SiteLocale.objects.filter(site=site, enabled=True).order_by('sort_order', 'locale_code'))


def locale_query_href(base_href: str, locale_code: str) -> str:
    separator = '&' if '?' in base_href else '?'
    return f'{base_href}{separator}locale={locale_code}'


def locale_switch_actions(*, site: Site, current_locale: SiteLocale, route_name: str) -> list[dict[str, object]]:
    actions = []
    for locale in enabled_site_locales(site):
        label = admin_locale_label(locale)
        actions.append(
            link_action(
                label,
                locale_query_href(reverse(route_name), locale.locale_code),
                tone='primary' if locale.id == current_locale.id else 'secondary',
            )
        )
    return actions


def default_site_locale(locale_code: str | None = None) -> tuple[Site, SiteLocale]:
    site = Site.objects.get(code='siteos_demo', enabled=True)
    target_code = (locale_code or site.default_locale).strip().lower()
    locale = (
        SiteLocale.objects.filter(site=site, locale_code=target_code, enabled=True).order_by('sort_order').first()
        or SiteLocale.objects.get(site=site, locale_code=site.default_locale, enabled=True)
    )
    return site, locale


def latest_content_update(site: Site):
    candidates = [
        Article.objects.filter(route__site=site).aggregate(value=Max('updated_at'))['value'],
        PageRoute.objects.filter(site=site).aggregate(value=Max('updated_at'))['value'],
        Cta.objects.filter(site=site).aggregate(value=Max('updated_at'))['value'],
        MediaAsset.objects.filter(site=site).aggregate(value=Max('updated_at'))['value'],
    ]
    return max([value for value in candidates if value], default=None)


def format_admin_datetime(value) -> str:
    if not value:
        return '未设置'
    return value.strftime('%Y-%m-%d %H:%M')


def status_label(value: str | None) -> str:
    mapping = {
        'draft': '草稿',
        'published': '已发布',
        'archived': '已归档',
        'active': '启用',
        'enabled': '启用',
        'disabled': '停用',
        'queued': '排队中',
        'running': '执行中',
        'failed': '失败',
        'succeeded': '成功',
        'built': '已构建',
        'live': '已上线',
        'exported': '已导出',
        'pending': '待处理',
        'sending': '发送中',
        'processing': '处理中',
        'processed': '已处理',
        'validated': '仅校验',
        'sent': '平台已接收',
        'partial': '部分成功',
        'skipped': '已跳过',
        'verified': '已核验',
        'completed': '已完成',
        'canceled': '已取消',
        'cancelled': '已取消',
        'rejected': '已拒绝',
    }
    if not value:
        return '未设置'
    return mapping.get(value, value)


def status_tone(value: str | None) -> str:
    if value in {'published', 'active', 'enabled', 'succeeded', 'built', 'live', 'processed', 'completed'}:
        return 'green'
    if value in {'queued', 'running', 'sending', 'processing', 'verified'}:
        return 'blue'
    if value == 'failed':
        return 'red'
    if value in {
        'draft', 'archived', 'disabled', 'pending', 'validated', 'partial',
        'skipped', 'rejected', 'canceled', 'cancelled',
    }:
        return 'amber'
    return 'slate'


def bytes_label(value: int | None) -> str:
    size = float(value or 0)
    units = ['B', 'KB', 'MB', 'GB']
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f'{size:.1f} {unit}' if unit != 'B' else f'{int(size)} B'
        size /= 1024
    return f'{size:.1f} GB'


def asset_type_label(value: str) -> str:
    return {
        'image': '图片',
        'video': '视频',
        'model3d': '3D',
        'document': '文档',
    }.get(value, value)


def badge(label: str, tone: str) -> dict[str, str]:
    return {'label': label, 'tone': tone}


def link_action(label: str, href: str, *, tone: str = 'secondary', external: bool = False) -> dict[str, object]:
    return {
        'kind': 'link',
        'label': label,
        'href': href,
        'tone': tone,
        'external': external,
    }


def post_action(label: str, href: str, *, tone: str = 'secondary') -> dict[str, object]:
    return {
        'kind': 'post',
        'label': label,
        'href': href,
        'tone': tone,
    }


def make_page(
    *,
    section_key: str,
    title: str,
    description: str,
    page_type: str,
    workspace_label: str,
    workspace_meta: str,
    search_placeholder: str,
) -> dict:
    section = SECTION_MAP[section_key]
    return {
        'sectionKey': section_key,
        'sectionLabel': section.label,
        'title': title,
        'description': description,
        'pageType': page_type,
        'workspaceLabel': workspace_label,
        'workspaceMeta': workspace_meta,
        'searchPlaceholder': search_placeholder,
        'tabs': [],
        'filters': [],
        'filterGroups': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }


def _apply_table_contract(payload: dict) -> dict:
    """Serialize the generic table's opt-in bulk-selection contract."""

    tables = [payload.get('table')]
    tables.extend(
        view.get('table')
        for view in (payload.get('views') or {}).values()
        if isinstance(view, dict)
    )
    for table in tables:
        if not isinstance(table, dict):
            continue
        table['selectable'] = bool(table.get('selectable', False))
        bulk_actions = table.get('bulkActions')
        table['bulkActions'] = list(bulk_actions) if isinstance(bulk_actions, (list, tuple)) else []
    return payload


def lead_stage_label(value: str) -> str:
    return {
        'new': '新提交',
        'contacted': '已联系',
        'qualified': '高质量线索',
        'won': '已成交',
        'lost': '已流失',
        'spam': '垃圾线索',
    }.get(value, value)


def lead_stage_tone(value: str) -> str:
    if value in {'won'}:
        return 'green'
    if value in {'qualified', 'contacted'}:
        return 'blue'
    if value in {'lost', 'spam'}:
        return 'red'
    return 'amber'


def build_row(
    *,
    row_id: str,
    tab_key: str,
    filter_keys: list[str],
    search: str,
    title: str,
    subtitle: str,
    cells: dict[str, object],
    details: list[dict[str, str]],
    actions: list[dict[str, object]],
    parent_id: str | None = None,
    depth: int = 0,
    has_children: bool = False,
) -> dict:
    return {
        'id': row_id,
        'tabKey': tab_key,
        'filterKeys': filter_keys,
        'search': search,
        'title': title,
        'subtitle': subtitle,
        'cells': cells,
        'details': details,
        'actions': actions,
        'parentId': parent_id,
        'depth': depth,
        'hasChildren': has_children,
    }


def content_payload(locale_code: str | None = None) -> dict:
    site, locale = default_site_locale(locale_code)
    latest_build = ReleaseBuild.objects.filter(release__site=site).order_by('-created_at').first()
    counts = Article.objects.filter(route__site=site, route__locale=locale).aggregate(
        total=Count('id'),
        published=Count('id', filter=Q(status='published')),
        draft=Count('id', filter=Q(status='draft')),
        archived=Count('id', filter=Q(status='archived')),
        seo_pending=Count('id', filter=Q(route__meta_description='')),
    )

    articles = (
        Article.objects.select_related('route', 'category')
        .filter(route__site=site, route__locale=locale)
        .order_by('-updated_at', '-id')[:160]
    )

    rows = []
    for article in articles:
        seo_ready = bool((article.route.meta_description or '').strip())
        excerpt = (article.excerpt or '').strip()
        filter_keys = [article.status]
        if not seo_ready:
            filter_keys.append('seo_pending')
        rows.append(
            build_row(
                row_id=f'article-{article.id}',
                tab_key='articles',
                filter_keys=filter_keys,
                search=' '.join(
                    [
                        article.title or '',
                        article.route.path or '',
                        excerpt,
                        article.category.name if article.category else '',
                    ]
                ),
                title=article.title,
                subtitle=excerpt or (article.category.name if article.category else '暂无摘要'),
                cells={
                    'path': article.route.path,
                    'category': article.category.name if article.category else '未分类',
                    'status': badge(status_label(article.status), status_tone(article.status)),
                    'seo': badge('已补全' if seo_ready else '待补', 'green' if seo_ready else 'amber'),
                    'updatedAt': format_admin_datetime(article.updated_at),
                },
                details=[
                    {'label': '栏目', 'value': article.category.name if article.category else '未分类'},
                    {'label': '语言', 'value': admin_locale_label(locale)},
                    {'label': '访问路径', 'value': article.route.path},
                    {'label': '发布状态', 'value': status_label(article.status)},
                    {'label': 'SEO 描述', 'value': article.route.meta_description or '未填写'},
                    {'label': '预计阅读时长', 'value': f'{article.reading_minutes or 0} 分钟'},
                    {'label': '最后更新', 'value': format_admin_datetime(article.updated_at)},
                ],
                actions=[
                    link_action('编辑', locale_query_href(reverse('console:article_edit', args=[article.id]), locale.locale_code)),
                    link_action('预览', f'{PUBLIC_PREVIEW_BASE_URL}{article.route.path}', external=True),
                ],
            )
        )

    latest_build_label = status_label(latest_build.status) if latest_build else '暂无预览构建'
    payload = make_page(
        section_key='articles',
        title='文章',
        description='这里管理文章内容。运营维护标题、摘要、正文和 SEO，程序员在这里查询稳定访问路径。',
        page_type='list',
        workspace_label='文章列表',
        workspace_meta=(
            f'当前语言 {admin_locale_label(locale)} / 最近内容更新时间 {format_admin_datetime(latest_content_update(site))} / '
            f'预览构建状态 {latest_build_label}'
        ),
        search_placeholder='搜索标题、摘要、路径或栏目',
    )
    payload['tabs'] = [{'key': 'articles', 'label': '文章'}]
    payload['filterGroups'] = [
        {
            'key': 'status',
            'label': '状态',
            'options': [
                {'key': 'all', 'label': '全部状态'},
                {'key': 'published', 'label': f'已发布 {counts["published"] or 0}'},
                {'key': 'draft', 'label': f'草稿 {counts["draft"] or 0}'},
                {'key': 'archived', 'label': f'已归档 {counts["archived"] or 0}'},
            ],
        },
        {
            'key': 'seo',
            'label': 'SEO',
            'options': [
                {'key': 'all', 'label': '全部'},
                {'key': 'seo_pending', 'label': f'待补 {counts["seo_pending"] or 0}'},
            ],
        },
    ]
    payload['scopeActions'] = locale_switch_actions(site=site, current_locale=locale, route_name='console:articles')
    payload['actions'] = [
        link_action('新建文章', locale_query_href(reverse('console:article_create'), locale.locale_code), tone='primary'),
        link_action('批量导入', locale_query_href(reverse('console:article_import'), locale.locale_code)),
        link_action('批量导出', locale_query_href(reverse('console:article_export'), locale.locale_code)),
        link_action('分类管理', locale_query_href(reverse('console:article_categories'), locale.locale_code)),
        link_action('发布中心', reverse('console:releases')),
    ]
    payload['summary'] = [
        {'label': '当前语言', 'value': admin_locale_label(locale), 'meta': locale.locale_code},
        {'label': '文章总数', 'value': counts['total'] or 0, 'meta': '当前语言'},
        {'label': '已发布', 'value': counts['published'] or 0, 'meta': '前台可见'},
        {'label': '草稿', 'value': counts['draft'] or 0, 'meta': '待编辑'},
        {'label': 'SEO 待补', 'value': counts['seo_pending'] or 0, 'meta': '缺少描述'},
    ]
    payload['table'] = {
        'columnWidths': {
            'title': 276,
            'path': 252,
            'category': 156,
            'status': 96,
            'seo': 96,
            'updatedAt': 140,
        },
        'columns': [
            {'key': 'title', 'label': '标题', 'type': 'title'},
            {'key': 'path', 'label': '路径', 'type': 'path'},
            {'key': 'category', 'label': '栏目', 'type': 'text'},
            {'key': 'status', 'label': '状态', 'type': 'badge'},
            {'key': 'seo', 'label': 'SEO', 'type': 'badge'},
            {'key': 'updatedAt', 'label': '更新', 'type': 'text'},
        ],
        'rows': rows,
        'emptyMessage': '当前没有文章记录。',
    }
    payload['notes'] = [
        {
            'title': '使用边界',
            'body': '文章是一语言一篇。自由页和全局组件继续走代码维护，后台专注内容、资产、营销和发布。',
        }
    ]
    return _apply_table_contract(payload)


def assets_payload(
    *,
    site: Site | None = None,
    capabilities: frozenset[str] = frozenset(),
    include_internal_paths: bool = False,
) -> dict:
    if ASSETS_READ not in capabilities:
        raise PermissionDenied('当前账号没有查看素材库的权限。')
    if site is None:
        site, _locale = default_site_locale()
    counts = {
        'active': MediaAsset.objects.filter(site=site, status='active').count(),
        'image': MediaAsset.objects.filter(site=site, asset_type='image', status='active').count(),
        'video': MediaAsset.objects.filter(site=site, asset_type='video', status='active').count(),
        'model3d': MediaAsset.objects.filter(site=site, asset_type='model3d', status='active').count(),
        'document': MediaAsset.objects.filter(site=site, asset_type='document', status='active').count(),
    }

    assets = MediaAsset.objects.filter(site=site).order_by('-updated_at', '-id')[:180]
    rows = []
    for asset in assets:
        rows.append(
            build_row(
                row_id=f'asset-{asset.id}',
                tab_key='assets',
                filter_keys=[asset.status, asset.asset_type],
                search=' '.join([asset.title or '', asset.original_name or '', asset.public_path or '', asset.asset_type]),
                title=asset.title or asset.original_name,
                subtitle=asset.original_name,
                cells={
                    'publicPath': asset.public_path,
                    'assetType': asset_type_label(asset.asset_type),
                    'status': badge(status_label(asset.status), status_tone(asset.status)),
                    'size': bytes_label(asset.file_size_bytes),
                    'updatedAt': format_admin_datetime(asset.updated_at),
                },
                details=[
                    {'label': '类型', 'value': asset_type_label(asset.asset_type)},
                    {'label': '公开路径', 'value': asset.public_path},
                    {'label': '文件大小', 'value': bytes_label(asset.file_size_bytes)},
                    {'label': 'MIME', 'value': asset.mime_type or '未设置'},
                    {'label': '状态', 'value': status_label(asset.status)},
                    {'label': '最后更新', 'value': format_admin_datetime(asset.updated_at)},
                ] + (
                    [{'label': '内部存储标识', 'value': asset.storage_path}]
                    if include_internal_paths else []
                ),
                actions=[
                    link_action('打开文件', reverse('console:asset_file', args=[asset.id]), external=True),
                ],
            )
        )

    payload = make_page(
        section_key='assets',
        title='资产',
        description='这里管理图片、视频、3D 和文档文件。程序员可以直接查公开路径，运营负责上传和维护资产。',
        page_type='list',
        workspace_label='资产列表',
        workspace_meta=f'启用资产 {counts["active"]} / 图片 {counts["image"]} / 视频 {counts["video"]} / 3D+文档 {counts["model3d"] + counts["document"]}',
        search_placeholder='搜索标题、原文件名、公开路径或资产类型',
    )
    payload['tabs'] = [{'key': 'assets', 'label': '资产'}]
    payload['filters'] = [
        {'key': 'all', 'label': '全部'},
        {'key': 'active', 'label': f'启用 {counts["active"]}'},
        {'key': 'image', 'label': f'图片 {counts["image"]}'},
        {'key': 'video', 'label': f'视频 {counts["video"]}'},
        {'key': 'model3d', 'label': f'3D {counts["model3d"]}'},
        {'key': 'document', 'label': f'文档 {counts["document"]}'},
    ]
    payload['actions'] = []
    if ASSETS_WRITE in capabilities:
        payload['actions'].append(
            link_action('上传资产', reverse('console:asset_upload'), tone='primary')
        )
    payload['actions'].append(
        link_action('3D 展示配置', reverse('console:assets_three_d'))
    )
    if ASSETS_WRITE in capabilities and ASSETS_IMPORT_LOCAL in capabilities:
        payload['actions'].append(
            link_action('本地文件导入', reverse('console:asset_import_path'))
        )
    payload['summary'] = [
        {'label': '启用资产', 'value': counts['active'], 'meta': '可被页面引用'},
        {'label': '图片', 'value': counts['image'], 'meta': '封面图和内容图'},
        {'label': '视频', 'value': counts['video'], 'meta': '媒体内容'},
        {'label': '3D / 文档', 'value': counts['model3d'] + counts['document'], 'meta': '模型与资料'},
    ]
    payload['table'] = {
        'actionWidth': 112,
        'columnWidths': {
            'title': 220,
            'publicPath': 280,
            'assetType': 90,
            'status': 88,
            'size': 88,
            'updatedAt': 132,
        },
        'columns': [
            {'key': 'title', 'label': '资产', 'type': 'title'},
            {'key': 'publicPath', 'label': '公开路径', 'type': 'path'},
            {'key': 'assetType', 'label': '类型', 'type': 'text'},
            {'key': 'status', 'label': '状态', 'type': 'badge'},
            {'key': 'size', 'label': '大小', 'type': 'text'},
            {'key': 'updatedAt', 'label': '更新', 'type': 'text', 'hideBelow': 1320},
        ],
        'rows': rows,
        'emptyMessage': '当前没有资产。',
    }
    payload['notes'] = [
        {
            'title': '使用边界',
            'body': '后台负责资产上传和路径查询，不在这里做页面代码编辑。页面引用关系会继续补进来，方便查谁在用这张图或这个 3D 文件。',
        }
    ]
    return _apply_table_contract(payload)


def _matched_internal_route(site: Site, target_url: str | None):
    raw = (target_url or '').strip()
    if not raw:
        return None
    split = urlsplit(raw)
    if split.netloc and split.netloc.lower() != urlsplit(site.base_url).netloc.lower():
        return None
    path = split.path or raw
    if not path.startswith('/'):
        return None
    return PageRoute.objects.filter(site=site, path=path).select_related('locale').first()


def marketing_payload() -> dict:
    site, _locale = default_site_locale()
    overview = build_marketing_overview(site=site)
    integrations = list(
        MarketingIntegration.objects.select_related('provider')
        .filter(site=site)
        .order_by('provider__code', 'name')[:120]
    )
    ctas = list(Cta.objects.select_related('event').filter(site=site).order_by('business_goal', 'code')[:180])
    rules = list(
        TrackingRule.objects.select_related('integration__provider', 'canonical_event')
        .filter(integration__site=site)
        .order_by('priority', 'scope_type')[:180]
    )
    event_mappings = list(
        ProviderEventMapping.objects.select_related('provider', 'canonical_event').order_by('provider__code', 'canonical_event__code')[:180]
    )
    source_labels = {
        'meta_ads': 'Meta 广告',
        'paid_search': '付费搜索',
        'organic': '自然搜索',
        'referral': '引荐',
        'direct': '直接访问',
        'website': '网站表单',
        'whatsapp': 'WhatsApp',
        'email': '邮件',
        'other': '其他',
        'unknown': '未知来源',
    }
    source_rows = []
    for row in overview['sources']:
        source = str(row['source'] or 'unknown')
        source_rows.append(
            build_row(
                row_id=f'source-{source}',
                tab_key='overview',
                filter_keys=[source],
                search=f'{source} {source_labels.get(source, source)}',
                title=source_labels.get(source, source),
                subtitle=source,
                cells={
                    'leads': row['leads'],
                    'contacted': row['contacted'],
                    'qualified': row['qualified'],
                    'won': row['won'],
                    'wonValue': row['won_value'],
                },
                details=[
                    {'label': '线索', 'value': row['leads']},
                    {'label': '联系率', 'value': f'{row["contact_rate_percent"]}%'},
                    {'label': '合格率', 'value': f'{row["qualification_rate_percent"]}%'},
                    {'label': '成交率', 'value': f'{row["win_rate_percent"]}%'},
                    {'label': '成交金额', 'value': row['won_value']},
                ],
                actions=[],
            )
        )

    matching = overview['matching_completeness']
    matching_rows = []
    matching_definitions = [
        ('contactable', '邮箱或电话', 'Meta、Google 和销售联系所需的基础客户标识'),
        ('browser_identifier', '浏览器标识', '网站事件中的 fbp，用于连接浏览器会话'),
        ('ad_click_identifier', '广告点击标识', 'fbc、gclid、gbraid、wbraid 或 CTWA 点击标识'),
        ('external_lead_identifier', '平台线索标识', 'Lead Ads、WhatsApp 或外部系统返回的线索标识'),
    ]
    for key, label, purpose in matching_definitions:
        value = matching[key]
        matching_rows.append(
            build_row(
                row_id=f'matching-{key}',
                tab_key='matching',
                filter_keys=[key],
                search=f'{label} {purpose}',
                title=label,
                subtitle=purpose,
                cells={
                    'complete': value['count'],
                    'eligible': matching['eligible_leads'],
                    'percent': f'{value["percent"]}%',
                    'purpose': purpose,
                },
                details=[
                    {'label': '有营销同意的线索', 'value': matching['eligible_leads']},
                    {'label': '字段完整', 'value': value['count']},
                    {'label': '完整率', 'value': f'{value["percent"]}%'},
                    {'label': '用途', 'value': purpose},
                ],
                actions=[],
            )
        )

    integration_rows = []
    integration_readiness_counts = {'pass': 0, 'warn': 0, 'fail': 0}
    for item in integrations:
        readiness_checks = inspect_marketing_integration(item)
        readiness_status = checks_overall(readiness_checks)
        integration_readiness_counts[readiness_status] += 1
        blocking_labels = [
            check['label']
            for check in readiness_checks
            if check['required'] and check['status'] == 'fail'
        ]
        warning_labels = [check['label'] for check in readiness_checks if check['status'] == 'warn']
        if blocking_labels:
            readiness_detail = '；'.join(blocking_labels[:3])
        elif warning_labels:
            readiness_detail = '；'.join(warning_labels[:3])
        else:
            readiness_detail = '本地结构检查通过；仍需平台诊断验收'
        readiness_badge = {
            'pass': badge('结构就绪', 'green'),
            'warn': badge('待处理', 'amber'),
            'fail': badge('有阻断项', 'red'),
        }[readiness_status]
        integration_rows.append(
            build_row(
                row_id=f'integration-{item.id}',
                tab_key='integrations',
                filter_keys=['enabled' if item.enabled else 'disabled', item.provider.code, readiness_status],
                search=' '.join([item.provider.code, item.name, item.integration_type, item.public_id]),
                title=item.name,
                subtitle=item.provider.name,
                cells={
                    'provider': item.provider.name,
                    'integrationType': item.integration_type,
                    'publicId': item.public_id,
                    'status': badge('启用' if item.enabled else '停用', 'green' if item.enabled else 'amber'),
                    'readiness': readiness_badge,
                    'updatedAt': format_admin_datetime(item.updated_at),
                },
                details=[
                    {'label': '平台', 'value': item.provider.name},
                    {'label': '接入类型', 'value': item.integration_type},
                    {'label': '公开 ID', 'value': item.public_id},
                    {'label': '同意分类', 'value': item.consent_category},
                    {'label': '状态', 'value': '启用' if item.enabled else '停用'},
                    {'label': '结构体检', 'value': readiness_badge['label']},
                    {'label': '下一项', 'value': readiness_detail},
                ],
                actions=[
                    link_action('编辑接入', reverse('console:marketing_integration_edit', args=[item.id])),
                ],
            )
        )

    cta_rows = []
    unresolved_internal_count = 0
    external_count = 0
    for item in ctas:
        matched_route = _matched_internal_route(site, item.target_url)
        if item.target_type == 'internal' and not matched_route:
            unresolved_internal_count += 1
        if item.target_type != 'internal':
            external_count += 1

        if item.target_type == 'internal':
            target_kind = '受控站内页' if matched_route else '站内裸路径'
        else:
            target_kind = '外部链接'

        cta_rows.append(
            build_row(
                row_id=f'cta-{item.id}',
                tab_key='ctas',
                filter_keys=[item.target_type, 'enabled' if item.enabled else 'disabled'],
                search=' '.join([item.code, item.label, item.target_url or '', item.business_goal]),
                title=item.label,
                subtitle=item.code,
                cells={
                    'goal': item.business_goal,
                    'targetKind': target_kind,
                    'target': matched_route.path if matched_route else (item.target_url or '未设置'),
                    'status': badge('启用' if item.enabled else '停用', 'green' if item.enabled else 'amber'),
                    'updatedAt': format_admin_datetime(item.updated_at),
                },
                details=[
                    {'label': '业务目标', 'value': item.business_goal},
                    {'label': '目标类型', 'value': item.target_type},
                    {'label': '目标地址', 'value': item.target_url or '未设置'},
                    {'label': '匹配路由', 'value': matched_route.path if matched_route else '未匹配到受控路由'},
                    {'label': '事件', 'value': item.event.code if item.event else '未绑定'},
                    {'label': '状态', 'value': '启用' if item.enabled else '停用'},
                ],
                actions=[],
            )
        )

    rule_rows = []
    for item in rules:
        status_value = 'enabled' if item.enabled else 'disabled'
        rule_rows.append(
            build_row(
                row_id=f'rule-{item.id}',
                tab_key='rules',
                filter_keys=[status_value],
                search=' '.join(
                    [
                        item.integration.name,
                        item.integration.provider.code,
                        item.canonical_event.code if item.canonical_event else '',
                        item.scope_type,
                        item.scope_value,
                    ]
                ),
                title=f'{item.integration.provider.name} / {item.integration.name}',
                subtitle=f'{item.scope_type}:{item.scope_value}',
                cells={
                    'integration': item.integration.name,
                    'event': item.canonical_event.code if item.canonical_event else '未设置',
                    'priority': str(item.priority),
                    'status': badge(status_label(status_value), status_tone(status_value)),
                    'updatedAt': format_admin_datetime(item.updated_at),
                },
                details=[
                    {'label': '接入', 'value': item.integration.name},
                    {'label': '平台', 'value': item.integration.provider.name},
                    {'label': '事件', 'value': item.canonical_event.code if item.canonical_event else '未设置'},
                    {'label': '作用域', 'value': f'{item.scope_type}:{item.scope_value}'},
                    {'label': '优先级', 'value': str(item.priority)},
                    {'label': '状态', 'value': status_label(status_value)},
                ],
                actions=[],
            )
        )

    mapping_rows = []
    for item in event_mappings:
        mapping_rows.append(
            build_row(
                row_id=f'mapping-{item.id}',
                tab_key='mappings',
                filter_keys=['enabled' if item.enabled else 'disabled', item.provider.code],
                search=' '.join([item.provider.code, item.canonical_event.code, item.provider_event_name]),
                title=item.provider_event_name,
                subtitle=item.provider.name,
                cells={
                    'provider': item.provider.name,
                    'event': item.canonical_event.code,
                    'status': badge('启用' if item.enabled else '停用', 'green' if item.enabled else 'amber'),
                    'updatedAt': format_admin_datetime(item.updated_at),
                },
                details=[
                    {'label': '平台', 'value': item.provider.name},
                    {'label': '内部事件', 'value': item.canonical_event.code},
                    {'label': '平台事件', 'value': item.provider_event_name},
                    {'label': '状态', 'value': '启用' if item.enabled else '停用'},
                ],
                actions=[],
            )
        )

    payload = make_page(
        section_key='marketing',
        title='营销',
        description='查看获客来源、平台接入和归因规则；事件运营与隐私履约使用独立工作区。',
        page_type='list',
        workspace_label='营销运营',
        workspace_meta='聚焦来源归因、平台接入、CTA 与追踪映射。',
        search_placeholder='搜索来源、接入、CTA 或追踪规则',
    )
    payload['tabs'] = [
        {'key': 'overview', 'label': '来源漏斗'},
        {'key': 'matching', 'label': '匹配字段'},
        {'key': 'integrations', 'label': '平台接入'},
        {'key': 'ctas', 'label': 'CTA'},
        {'key': 'rules', 'label': '追踪规则'},
        {'key': 'mappings', 'label': '事件映射'},
    ]
    payload['views'] = {
        'overview': {
            'searchPlaceholder': '搜索来源渠道',
            'workspaceMeta': '联系、合格与成交均按曾经到达该阶段累计，成交不会从合格贡献中消失。',
            'filters': [{'key': 'all', 'label': '全部来源'}],
            'summary': [
                {'label': '全部线索', 'value': overview['funnel']['total'], 'meta': '站点累计'},
                {'label': '已联系', 'value': overview['funnel']['contacted'], 'meta': '累计到达'},
                {'label': '已合格', 'value': overview['funnel']['qualified'], 'meta': '累计到达'},
                {'label': '已成交', 'value': overview['funnel']['won'], 'meta': f'金额 {overview["funnel"]["won_value"]}'},
            ],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '来源', 'type': 'title'},
                    {'key': 'leads', 'label': '线索', 'type': 'text'},
                    {'key': 'contacted', 'label': '已联系', 'type': 'text'},
                    {'key': 'qualified', 'label': '已合格', 'type': 'text'},
                    {'key': 'won', 'label': '已成交', 'type': 'text'},
                    {'key': 'wonValue', 'label': '成交金额', 'type': 'text'},
                ],
                'rows': source_rows,
                'emptyMessage': '当前没有来源数据。',
            },
        },
        'matching': {
            'searchPlaceholder': '搜索匹配字段或用途',
            'workspaceMeta': '只统计已经取得营销同意的线索；字段完整不等于平台已经匹配。',
            'filters': [{'key': 'all', 'label': '全部字段'}],
            'summary': [
                {'label': '可用于匹配', 'value': matching['eligible_leads'], 'meta': '已取得营销同意'},
                {'label': '联系方式完整', 'value': f'{matching["contactable"]["percent"]}%', 'meta': '邮箱或电话'},
                {'label': '广告点击标识', 'value': f'{matching["ad_click_identifier"]["percent"]}%', 'meta': '广告归因标识'},
                {'label': '平台线索标识', 'value': f'{matching["external_lead_identifier"]["percent"]}%', 'meta': 'Lead Ads / WhatsApp'},
            ],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '匹配字段', 'type': 'title'},
                    {'key': 'complete', 'label': '字段完整', 'type': 'text'},
                    {'key': 'eligible', 'label': '可统计线索', 'type': 'text'},
                    {'key': 'percent', 'label': '完整率', 'type': 'text'},
                    {'key': 'purpose', 'label': '用途', 'type': 'text'},
                ],
                'rows': matching_rows,
                'emptyMessage': '当前没有可统计的匹配字段。',
            },
        },
        'integrations': {
            'searchPlaceholder': '搜索平台、接入名称、类型或公开 ID',
            'workspaceMeta': '接入决定平台脚本和服务端事件如何接入站点。',
            'filters': [
                {'key': 'all', 'label': '全部'},
                {'key': 'enabled', 'label': '启用'},
                {'key': 'disabled', 'label': '停用'},
                {'key': 'pass', 'label': '结构就绪'},
                {'key': 'warn', 'label': '待处理'},
                {'key': 'fail', 'label': '有阻断项'},
            ],
            'summary': [
                {'label': '接入总数', 'value': len(integrations), 'meta': '平台配置'},
                {'label': '启用中', 'value': sum(1 for item in integrations if item.enabled), 'meta': '正在生效'},
                {'label': '结构就绪', 'value': integration_readiness_counts['pass'], 'meta': '仍需平台验收'},
                {'label': '有阻断项', 'value': integration_readiness_counts['fail'], 'meta': '缺少必要配置'},
            ],
            'table': {
                'actionWidth': 112,
                'columnWidths': {
                    'title': 220,
                    'provider': 110,
                    'integrationType': 100,
                    'publicId': 170,
                    'status': 88,
                    'readiness': 96,
                    'updatedAt': 140,
                },
                'columns': [
                    {'key': 'title', 'label': '接入名称', 'type': 'title'},
                    {'key': 'provider', 'label': '平台', 'type': 'text'},
                    {'key': 'integrationType', 'label': '类型', 'type': 'text'},
                    {'key': 'publicId', 'label': '公开 ID', 'type': 'path'},
                    {'key': 'status', 'label': '状态', 'type': 'badge'},
                    {'key': 'readiness', 'label': '结构体检', 'type': 'badge'},
                    {'key': 'updatedAt', 'label': '更新', 'type': 'text'},
                ],
                'rows': integration_rows,
                'emptyMessage': '当前没有平台接入。',
            },
        },
        'ctas': {
            'searchPlaceholder': '搜索按钮文案、代码、目标地址或业务目标',
            'workspaceMeta': 'CTA 是动作对象，不是正文里的脚本片段。',
            'filters': [
                {'key': 'all', 'label': '全部'},
                {'key': 'internal', 'label': '站内'},
                {'key': 'external', 'label': '外链'},
                {'key': 'enabled', 'label': '启用'},
                {'key': 'disabled', 'label': '停用'},
            ],
            'summary': [
                {'label': 'CTA 总数', 'value': len(ctas), 'meta': '按钮与动作'},
                {'label': '站内裸路径', 'value': unresolved_internal_count, 'meta': '建议补受控路由'},
                {'label': '外链 CTA', 'value': external_count, 'meta': '跳转站外'},
            ],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '按钮文案', 'type': 'title'},
                    {'key': 'goal', 'label': '目标', 'type': 'text'},
                    {'key': 'targetKind', 'label': '目标类型', 'type': 'text'},
                    {'key': 'target', 'label': '目标地址', 'type': 'path'},
                    {'key': 'status', 'label': '状态', 'type': 'badge'},
                    {'key': 'updatedAt', 'label': '更新', 'type': 'text'},
                ],
                'rows': cta_rows,
                'emptyMessage': '当前没有 CTA。',
            },
        },
        'rules': {
            'searchPlaceholder': '搜索接入、事件、作用域或优先级',
            'workspaceMeta': '追踪规则负责把页面动作映射到平台接入。',
            'filters': [
                {'key': 'all', 'label': '全部'},
                {'key': 'enabled', 'label': '启用'},
                {'key': 'disabled', 'label': '停用'},
            ],
            'summary': [
                {'label': '规则总数', 'value': len(rules), 'meta': '映射逻辑'},
                {'label': '启用中', 'value': sum(1 for item in rules if item.enabled), 'meta': '当前生效'},
            ],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '规则', 'type': 'title'},
                    {'key': 'integration', 'label': '接入', 'type': 'text'},
                    {'key': 'event', 'label': '事件', 'type': 'text'},
                    {'key': 'priority', 'label': '优先级', 'type': 'text'},
                    {'key': 'status', 'label': '状态', 'type': 'badge'},
                    {'key': 'updatedAt', 'label': '更新', 'type': 'text'},
                ],
                'rows': rule_rows,
                'emptyMessage': '当前没有追踪规则。',
            },
        },
        'mappings': {
            'searchPlaceholder': '搜索平台、内部事件或平台事件名',
            'workspaceMeta': '事件映射定义内部事件如何翻译成各平台事件。',
            'filters': [
                {'key': 'all', 'label': '全部'},
                {'key': 'enabled', 'label': '启用'},
                {'key': 'disabled', 'label': '停用'},
            ],
            'summary': [
                {'label': '映射总数', 'value': len(event_mappings), 'meta': '平台事件字典'},
            ],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '平台事件', 'type': 'title'},
                    {'key': 'provider', 'label': '平台', 'type': 'text'},
                    {'key': 'event', 'label': '内部事件', 'type': 'text'},
                    {'key': 'status', 'label': '状态', 'type': 'badge'},
                    {'key': 'updatedAt', 'label': '更新', 'type': 'text'},
                ],
                'rows': mapping_rows,
                'emptyMessage': '当前没有事件映射。',
            },
        },
    }
    payload['summary'] = [{'label': '当前站点', 'value': site.name, 'meta': site.code}]
    payload['notes'] = [
        {
            'title': '控制思路',
            'body': '营销模块管理的是可控配置，不是代码片段垃圾桶。真正的页面实现仍然回到前端代码里维护。',
        }
    ]
    return _apply_table_contract(payload)


def releases_payload(
    *,
    site: Site | None = None,
    capabilities: frozenset[str] = frozenset(),
    include_diagnostics: bool = False,
) -> dict:
    if RELEASES_READ not in capabilities:
        raise PermissionDenied('当前账号没有查看预览与快照记录的权限。')
    if site is None:
        site, _locale = default_site_locale()
    build_search_placeholder = (
        '搜索构建键、快照键、产物路径或日志摘要'
        if include_diagnostics
        else '搜索构建键或快照键'
    )
    release_search_placeholder = (
        '搜索快照键、操作者、产物路径或备注'
        if include_diagnostics
        else '搜索快照键、操作者或备注'
    )
    builds = list(
        ReleaseBuild.objects.select_related('release')
        .filter(release__site=site)
        .order_by('-created_at')[:80]
    )
    releases = list(Release.objects.filter(site=site).order_by('-created_at')[:80])
    can_article_preview = {
        CONTENT_READ,
        RELEASES_READ,
        RELEASES_PREVIEW_BUILD,
    }.issubset(capabilities)
    can_candidate_build = {
        CONTENT_READ,
        RELEASES_READ,
        RELEASES_CANDIDATE_BUILD,
    }.issubset(capabilities)

    build_rows = []
    for item in builds:
        build_rows.append(
            build_row(
                row_id=f'build-{item.id}',
                tab_key='builds',
                filter_keys=[item.status],
                search=' '.join(
                    [item.build_key, item.release.release_key]
                    + ([item.artifact_path or '', item.log_excerpt or ''] if include_diagnostics else [])
                ),
                title=item.build_key,
                subtitle=item.release.release_key,
                cells={
                    'buildType': item.release.status,
                    'status': badge(status_label(item.status), status_tone(item.status)),
                    'operator': item.release.created_by or 'system',
                    'updatedAt': format_admin_datetime(item.created_at),
                },
                details=[
                    {'label': '构建键', 'value': item.build_key},
                    {'label': '快照键', 'value': item.release.release_key},
                    {'label': '状态', 'value': status_label(item.status)},
                    {'label': '开始时间', 'value': format_admin_datetime(item.started_at)},
                    {'label': '结束时间', 'value': format_admin_datetime(item.finished_at)},
                ] + (
                    [
                        {'label': '内部产物路径', 'value': item.artifact_path or '未设置'},
                        {'label': '内部日志摘要', 'value': item.log_excerpt or '无'},
                    ]
                    if include_diagnostics else []
                ),
                actions=[],
            )
        )

    release_rows = []
    for item in releases:
        snapshot = item.snapshot_manifest if isinstance(item.snapshot_manifest, dict) else {}
        article_preview_version = (
            snapshot.get('version')
            if snapshot.get('kind') == 'articlePreview' and item.status == 'built'
            else ''
        )
        preview_actions = []
        if can_article_preview and article_preview_version:
            preview_actions.append(
                link_action(
                    '打开文章私有预览',
                    reverse(
                        'console:article_preview_file',
                        kwargs={
                            'version': article_preview_version,
                            'artifact': 'articles/index.html',
                        },
                    ),
                )
            )
        if (
            can_candidate_build
            and article_preview_version
            and snapshot.get('scope') == 'sitePublished'
            and snapshot.get('sourceVersion')
        ):
            preview_actions.append(
                post_action(
                    '固化整站候选',
                    reverse(
                        'console:website_candidate_build',
                        kwargs={'preview_record_id': item.id},
                    ),
                    tone='secondary',
                )
            )
        release_rows.append(
            build_row(
                row_id=f'release-{item.id}',
                tab_key='releases',
                filter_keys=[item.status],
                search=' '.join(
                    [item.release_key, item.created_by or '', item.notes or '']
                    + ([item.artifact_path or ''] if include_diagnostics else [])
                ),
                title=item.release_key,
                subtitle=item.notes or '内容快照',
                cells={
                    'buildType': 'snapshot',
                    'status': badge(status_label(item.status), status_tone(item.status)),
                    'operator': item.created_by or 'system',
                    'updatedAt': format_admin_datetime(item.created_at),
                },
                details=[
                    {'label': '快照键', 'value': item.release_key},
                    {'label': '状态', 'value': status_label(item.status)},
                    {'label': '操作者', 'value': item.created_by or 'system'},
                    {'label': '导出时间', 'value': format_admin_datetime(item.exported_at)},
                    {'label': '构建时间', 'value': format_admin_datetime(item.built_at)},
                    {'label': '发布时间', 'value': format_admin_datetime(item.published_at)},
                ] + (
                    [{'label': '内部产物路径', 'value': item.artifact_path or '未设置'}]
                    if include_diagnostics else []
                ),
                actions=preview_actions,
            )
        )

    payload = make_page(
        section_key='releases',
        title='预览与快照',
        description='这里生成预览构建、审查文章快照并固化完整站点候选。候选不会被自动选择或部署；生产发布由受控部署流程执行，后台不直接上线生产。',
        page_type='list',
        workspace_label='预览与快照',
        workspace_meta='先审查文章私有预览，再从指定预览固化完整站点候选；生产发布另走受控部署流程。',
        search_placeholder=build_search_placeholder,
    )
    payload['tabs'] = [
        {'key': 'builds', 'label': '构建任务'},
        {'key': 'releases', 'label': '内容快照'},
    ]
    payload['actions'] = []
    if RELEASES_PREVIEW_BUILD in capabilities:
        payload['actions'].append(
            post_action('生成预览产物', reverse('console:release_build_preview'), tone='primary')
        )
    if can_article_preview:
        payload['actions'].append(
            post_action(
                '生成文章私有预览',
                reverse('console:article_release_preview'),
                tone='secondary',
            )
        )
    payload['views'] = {
        'builds': {
            'searchPlaceholder': build_search_placeholder,
            'workspaceMeta': '构建任务显示预览构建的实时状态。',
            'filters': [
                {'key': 'all', 'label': '全部'},
                {'key': 'queued', 'label': '排队中'},
                {'key': 'running', 'label': '执行中'},
                {'key': 'failed', 'label': '失败'},
                {'key': 'succeeded', 'label': '成功'},
            ],
            'summary': [
                {'label': '构建任务', 'value': len(builds), 'meta': '最近 80 条'},
            ],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '构建键', 'type': 'title'},
                    {'key': 'buildType', 'label': '类型', 'type': 'text'},
                    {'key': 'status', 'label': '状态', 'type': 'badge'},
                    {'key': 'operator', 'label': '操作者', 'type': 'text'},
                    {'key': 'updatedAt', 'label': '创建时间', 'type': 'text'},
                ],
                'rows': build_rows,
                'emptyMessage': '当前没有构建任务。',
            },
        },
        'releases': {
            'searchPlaceholder': release_search_placeholder,
            'workspaceMeta': '内容快照是数据库和文件导出的稳定版本，不等同于生产发布。',
            'filters': [
                {'key': 'all', 'label': '全部'},
                {'key': 'draft', 'label': '草稿'},
                {'key': 'exported', 'label': '已导出'},
                {'key': 'built', 'label': '已构建'},
                {'key': 'live', 'label': '已上线'},
            ],
            'summary': [
                {'label': '内容快照', 'value': len(releases), 'meta': '最近 80 条'},
            ],
            'table': {
                'columns': [
                    {'key': 'title', 'label': '快照键', 'type': 'title'},
                    {'key': 'buildType', 'label': '类型', 'type': 'text'},
                    {'key': 'status', 'label': '状态', 'type': 'badge'},
                    {'key': 'operator', 'label': '操作者', 'type': 'text'},
                    {'key': 'updatedAt', 'label': '创建时间', 'type': 'text'},
                ],
                'rows': release_rows,
                'emptyMessage': '当前没有内容快照。',
            },
        },
    }
    payload['summary'] = [{'label': '当前站点', 'value': site.name, 'meta': site.code}]
    payload['notes'] = [
        {
            'title': '生产发布边界',
            'body': 'Django 后台可以生成预览、内容快照和完整站点候选，但候选不会自动成为公开网站；生产发布由受控部署流程执行，选择、部署、切换和回滚由后续受控发布层负责。',
        }
    ]
    return _apply_table_contract(payload)
