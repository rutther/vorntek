from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.urls import reverse

from sitecore.models import Article, Category, ReleaseBuild, SiteLocale

from .content_access import (
    CONTENT_LOCALE_MANAGE,
    CONTENT_READ,
    CONTENT_SET_PUBLISHED,
    CONTENT_WRITE,
    RELEASES_READ,
)
from .payloads import (
    PUBLIC_PREVIEW_BASE_URL,
    admin_locale_label,
    default_site_locale,
    format_admin_datetime,
    make_page,
    status_label,
    status_tone,
)
from .taxonomy import category_depth_map, ordered_categories


def _workspace_href(route_name: str, locale_code: str, **params: object) -> str:
    query = {'locale': locale_code}
    for key, value in params.items():
        if value in (None, '', False):
            continue
        query[key] = value
    return f'{reverse(route_name)}?{urlencode(query)}'


def _seo_audit(article: Article) -> dict[str, object]:
    route = article.route
    missing_required: list[str] = []
    missing_recommended: list[str] = []

    if not (route.meta_description or '').strip():
        missing_required.append('Meta 描述')

    if not (route.og_title or '').strip():
        missing_recommended.append('OG 标题')

    if not (route.og_description or '').strip():
        missing_recommended.append('OG 描述')

    if not ((route.og_image_url or '').strip() or (article.cover_image_url or '').strip()):
        missing_recommended.append('OG 图片')

    if not (route.robots or '').strip():
        missing_recommended.append('Robots')

    if missing_required:
        return {
            'key': 'critical',
            'label': '关键缺失',
            'tone': 'red',
            'missing': missing_required + missing_recommended,
        }

    if missing_recommended:
        return {
            'key': 'warning',
            'label': '待完善',
            'tone': 'amber',
            'missing': missing_recommended,
        }

    return {
        'key': 'complete',
        'label': '完整',
        'tone': 'green',
        'missing': [],
    }


def _category_maps(
    categories: list[Category],
) -> tuple[dict[int, list[int]], dict[int, str], dict[int, int], dict[int | None, list[Category]]]:
    by_id = {category.id: category for category in categories}
    depth_map = category_depth_map(categories)
    children_map: dict[int | None, list[Category]] = defaultdict(list)

    for category in categories:
        parent_id = category.parent_id if category.parent_id in by_id else None
        children_map[parent_id].append(category)

    for bucket in children_map.values():
        bucket.sort(key=lambda item: (item.sort_order, item.name.lower(), item.id))

    trail_ids: dict[int, list[int]] = {}
    trail_labels: dict[int, str] = {}
    for category in categories:
        nodes: list[Category] = [category]
        current = category
        seen: set[int] = set()
        while current.parent_id and current.parent_id in by_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            current = by_id[current.parent_id]
            nodes.append(current)
        nodes.reverse()
        trail_ids[category.id] = [node.id for node in nodes]
        trail_labels[category.id] = ' / '.join(node.name for node in nodes)

    return trail_ids, trail_labels, depth_map, children_map


def article_workspace_bundle(
    locale_code: str | None = None,
    *,
    selected_taxon: str = 'all',
    initial_panel: str | None = None,
    capabilities: frozenset[str] = frozenset(),
    site_capabilities: frozenset[str] | None = None,
    visible_locale_ids: frozenset[int] = frozenset(),
) -> tuple[dict, dict]:
    site, locale = default_site_locale(locale_code)
    resolved_site_capabilities = capabilities if site_capabilities is None else site_capabilities
    can_read = CONTENT_READ in capabilities
    can_write = CONTENT_WRITE in capabilities
    can_publish = CONTENT_SET_PUBLISHED in capabilities
    can_manage_locales = CONTENT_LOCALE_MANAGE in resolved_site_capabilities
    can_activate_locales = can_manage_locales and CONTENT_SET_PUBLISHED in resolved_site_capabilities
    can_read_releases = RELEASES_READ in resolved_site_capabilities
    if not can_read:
        raise PermissionDenied('当前账号没有查看此语言内容的权限。')
    latest_build = (
        ReleaseBuild.objects.filter(release__site=site).order_by('-created_at').first()
        if can_read_releases else None
    )
    latest_build_label = (
        status_label(latest_build.status)
        if latest_build else ('暂无预览构建' if can_read_releases else '')
    )

    categories = ordered_categories(site=site, locale=locale)
    trail_ids, trail_labels, depth_map, children_map = _category_maps(categories)

    articles = list(
        Article.objects.select_related('route', 'category', 'category__parent')
        .filter(route__site=site, route__locale=locale)
        .order_by('-updated_at', '-id')[:500]
    )

    direct_article_counts = {
        row['category']: row['count']
        for row in (
            Article.objects.filter(route__site=site, route__locale=locale, category__isnull=False)
            .values('category')
            .annotate(count=Count('id'))
        )
    }

    @lru_cache(maxsize=None)
    def branch_count(category_id: int) -> int:
        total = direct_article_counts.get(category_id, 0)
        for child in children_map.get(category_id, []):
            total += branch_count(child.id)
        return total

    article_rows: list[dict[str, object]] = []
    counts = {
        'total': 0,
        'published': 0,
        'draft': 0,
        'archived': 0,
        'seo_complete': 0,
        'seo_warning': 0,
        'seo_critical': 0,
        'seo_incomplete': 0,
        'uncategorized': 0,
    }

    for article in articles:
        seo = _seo_audit(article)
        category_path = trail_labels.get(article.category_id, '未分类') if article.category_id else '未分类'
        article_rows.append(
            {
                'id': article.id,
                'title': article.title,
                'excerpt': (article.excerpt or '').strip(),
                'path': article.route.path,
                'categoryId': article.category_id,
                'categoryPath': category_path,
                'categoryTrailIds': trail_ids.get(article.category_id, []) if article.category_id else [],
                'statusKey': article.status,
                'statusLabel': status_label(article.status),
                'statusTone': status_tone(article.status),
                'seoKey': seo['key'],
                'seoLabel': seo['label'],
                'seoTone': seo['tone'],
                'seoMissing': seo['missing'],
                'updatedAt': format_admin_datetime(article.updated_at),
                'editHref': (
                    f'{reverse("console:article_edit", args=[article.id])}?{urlencode({"locale": locale.locale_code})}'
                    if can_write and (article.status != 'published' or can_publish) else ''
                ),
                'previewHref': f'{PUBLIC_PREVIEW_BASE_URL}{article.route.path}',
            }
        )

        counts['total'] += 1
        counts[article.status] = counts.get(article.status, 0) + 1
        counts[f'seo_{seo["key"]}'] += 1
        if seo['key'] in {'warning', 'critical'}:
            counts['seo_incomplete'] += 1
        if article.category_id is None:
            counts['uncategorized'] += 1

    category_rows: list[dict[str, object]] = []
    for category in categories:
        category_rows.append(
            {
                'id': category.id,
                'parentId': category.parent_id,
                'depth': depth_map.get(category.id, 0),
                'level': min(depth_map.get(category.id, 0) + 1, 3),
                'name': category.name,
                'code': category.code,
                'slug': category.slug,
                'description': category.description or '',
                'statusKey': category.status,
                'statusLabel': status_label(category.status),
                'statusTone': status_tone(category.status),
                'directCount': direct_article_counts.get(category.id, 0),
                'branchCount': branch_count(category.id),
                'childCount': len(children_map.get(category.id, [])),
                'trailLabel': trail_labels.get(category.id, category.name),
                'editHref': (
                    f'{reverse("console:category_edit", args=[category.id])}?{urlencode({"locale": locale.locale_code})}'
                    if can_write and (category.status != 'published' or can_publish) else ''
                ),
                'newChildHref': (
                    _workspace_href('console:category_create', locale.locale_code, parent=category.id)
                    if can_write else ''
                ),
            }
        )

    enabled_locale_items = list(
        SiteLocale.objects.filter(
            site=site,
            enabled=True,
            id__in=visible_locale_ids,
        ).order_by('sort_order', 'locale_code')
    )
    all_locale_items = (
        list(SiteLocale.objects.filter(site=site).order_by('sort_order', 'locale_code'))
        if can_manage_locales else []
    )
    initial_selected_taxa: list[str] = [selected_taxon] if selected_taxon.isdigit() else []
    initial_scope = 'uncategorized' if selected_taxon == 'uncategorized' else 'all'
    panel_key = initial_panel if initial_panel in {'taxonomy-manager', 'locale-manager'} else ''
    if panel_key == 'taxonomy-manager' and not can_write:
        panel_key = ''
    if panel_key == 'locale-manager' and not can_manage_locales:
        panel_key = ''

    page_payload = make_page(
        section_key='articles',
        title='文章',
        description='维护文章、分类、导入导出与基础 SEO。',
        page_type='workspace',
        workspace_label='文章',
        workspace_meta='',
        search_placeholder='',
    )

    workspace = {
        'locale': {
            'code': locale.locale_code,
            'label': admin_locale_label(locale),
        },
        'permissions': {
            'canRead': can_read,
            'canWrite': can_write,
            'canPublish': can_publish,
            'canManageLocales': can_manage_locales,
            'canActivateLocales': can_activate_locales,
            'canReadReleases': can_read_releases,
        },
        'localeActions': [
            {
                'label': admin_locale_label(item_locale),
                'href': _workspace_href(
                    'console:articles',
                    item_locale.locale_code,
                    scope='uncategorized' if initial_scope == 'uncategorized' else None,
                    category=initial_selected_taxa[0] if len(initial_selected_taxa) == 1 else None,
                    panel=panel_key or None,
                ),
                'active': item_locale.id == locale.id,
            }
            for item_locale in enabled_locale_items
        ],
        'localeManager': {
            'title': '语言管理',
            'summary': '语言增删改与默认语言切换应进入独立面板，不应挤在文章工具栏里。',
            'createHref': (
                _workspace_href('console:article_locale_create', locale.locale_code)
                if can_activate_locales else ''
            ),
            'items': [
                {
                    'id': item_locale.id,
                    'label': admin_locale_label(item_locale),
                    'code': item_locale.locale_code,
                    'direction': item_locale.direction,
                    'enabled': item_locale.enabled,
                    'isDefault': item_locale.is_default,
                    'switchHref': (
                        _workspace_href('console:articles', item_locale.locale_code)
                        if item_locale.enabled and item_locale.id in visible_locale_ids else ''
                    ),
                    'enableHref': (
                        reverse('console:article_locale_enable', args=[item_locale.id])
                        if can_activate_locales and not item_locale.enabled else ''
                    ),
                    'disableHref': (
                        reverse('console:article_locale_disable', args=[item_locale.id])
                        if can_activate_locales and item_locale.enabled and not item_locale.is_default else ''
                    ),
                    'makeDefaultHref': (
                        reverse('console:article_locale_make_default', args=[item_locale.id])
                        if can_activate_locales and not item_locale.is_default else ''
                    ),
                }
                for item_locale in all_locale_items
            ],
        },
        'toolbarActions': {
            'primary': {
                'label': '新建文章',
                'href': _workspace_href('console:article_create', locale.locale_code),
            } if can_write else None,
            'secondary': (
                [{'label': '批量导入', 'href': _workspace_href('console:article_import', locale.locale_code)}]
                if can_write else []
            ) + [
                {'label': '批量导出', 'href': _workspace_href('console:article_export', locale.locale_code)},
            ],
        },
        'taxonomyActions': {
            'label': '分类管理',
            'rootCreateHref': (
                _workspace_href('console:category_create', locale.locale_code)
                if can_write else ''
            ),
        },
        'filters': {
            'status': [
                {'key': 'all', 'label': '全部', 'count': counts['total']},
                {'key': 'published', 'label': '已发布', 'count': counts['published']},
                {'key': 'draft', 'label': '草稿', 'count': counts['draft']},
                {'key': 'archived', 'label': '已归档', 'count': counts['archived']},
            ],
            'seo': [
                {'key': 'all', 'label': '全部', 'count': counts['total']},
                {'key': 'incomplete', 'label': '未完成', 'count': counts['seo_incomplete']},
                {'key': 'critical', 'label': '关键缺失', 'count': counts['seo_critical']},
                {'key': 'complete', 'label': '完整', 'count': counts['seo_complete']},
            ],
        },
        'stats': {
            **counts,
            'categories': len(category_rows),
        },
        'selection': {
            'scope': initial_scope,
            'selectedTaxa': initial_selected_taxa,
        },
        'pagination': {
            'pageSize': 50,
        },
        'initialPanel': panel_key,
        'buildStatusLabel': latest_build_label,
        'articles': article_rows,
        'categories': category_rows,
    }
    return page_payload, workspace
