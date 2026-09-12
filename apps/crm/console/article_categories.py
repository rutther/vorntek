from __future__ import annotations

from collections import defaultdict

from django.urls import reverse

from .payloads import (
    admin_locale_label,
    badge,
    build_row,
    default_site_locale,
    format_admin_datetime,
    link_action,
    locale_query_href,
    locale_switch_actions,
    make_page,
    status_label,
    status_tone,
)
from .taxonomy import category_depth_map, ordered_categories


def article_categories_payload(locale_code: str | None = None) -> dict:
    site, locale = default_site_locale(locale_code)
    categories = ordered_categories(site, locale)
    depth_map = category_depth_map(categories)

    children_map: dict[int, list[object]] = defaultdict(list)
    for category in categories:
        if category.parent_id:
            children_map[category.parent_id].append(category)

    counts = {
        'total': len(categories),
        'level_1': sum(1 for category in categories if depth_map.get(category.id, 0) == 0),
        'level_2': sum(1 for category in categories if depth_map.get(category.id, 0) == 1),
        'level_3': sum(1 for category in categories if depth_map.get(category.id, 0) == 2),
        'published': sum(1 for category in categories if category.status == 'published'),
        'draft': sum(1 for category in categories if category.status == 'draft'),
        'archived': sum(1 for category in categories if category.status == 'archived'),
    }

    rows = []
    for category in categories:
        depth = depth_map.get(category.id, 0)
        level_key = f'level_{min(depth + 1, 3)}'
        parent_name = category.parent.name if category.parent_id and category.parent else '一级分类'
        child_count = len(children_map.get(category.id, []))

        rows.append(
            build_row(
                row_id=f'category-{category.id}',
                tab_key='categories',
                filter_keys=[category.status, level_key],
                search=' '.join(
                    [
                        category.name,
                        category.code,
                        category.slug,
                        category.description or '',
                        parent_name,
                    ]
                ),
                title=category.name,
                subtitle=category.code,
                cells={
                    'slug': category.slug,
                    'parent': parent_name,
                    'status': badge(status_label(category.status), status_tone(category.status)),
                    'updatedAt': format_admin_datetime(category.updated_at),
                },
                details=[
                    {'label': '语言', 'value': admin_locale_label(locale)},
                    {'label': '分类代码', 'value': category.code},
                    {'label': 'URL 别名', 'value': category.slug},
                    {'label': '上级分类', 'value': parent_name},
                    {'label': '分类层级', 'value': str(min(depth + 1, 3))},
                    {'label': '排序', 'value': str(category.sort_order)},
                    {'label': '状态', 'value': status_label(category.status)},
                    {'label': '说明', 'value': category.description or '未填写'},
                    {'label': '最后更新', 'value': format_admin_datetime(category.updated_at)},
                ],
                actions=[
                    link_action(
                        '编辑分类',
                        locale_query_href(reverse('console:category_edit', args=[category.id]), locale.locale_code),
                    ),
                    link_action('查看文章', locale_query_href(reverse('console:articles'), locale.locale_code)),
                ],
                parent_id=f'category-{category.parent_id}' if category.parent_id else None,
                depth=depth,
                has_children=child_count > 0,
            )
        )

    payload = make_page(
        section_key='articles',
        title='文章分类',
        description='这里管理文章分类树。语言是外层筛选条件，分类树本身表达业务结构。',
        page_type='list',
        workspace_label='分类管理',
        workspace_meta=(
            f'当前语言 {admin_locale_label(locale)} / '
            f'一级 {counts["level_1"]} / 二级 {counts["level_2"]} / 三级 {counts["level_3"]}'
        ),
        search_placeholder='搜索分类名称、代码、URL 别名或说明',
    )
    payload['tabs'] = [{'key': 'categories', 'label': '分类树'}]
    payload['scopeActions'] = locale_switch_actions(site=site, current_locale=locale, route_name='console:article_categories')
    payload['actions'] = [
        link_action('新建分类', locale_query_href(reverse('console:category_create'), locale.locale_code), tone='primary'),
        link_action('返回文章', locale_query_href(reverse('console:articles'), locale.locale_code)),
    ]
    payload['filterGroups'] = [
        {
            'key': 'status',
            'label': '状态',
            'options': [
                {'key': 'all', 'label': '全部状态'},
                {'key': 'published', 'label': f'已发布 {counts["published"]}'},
                {'key': 'draft', 'label': f'草稿 {counts["draft"]}'},
                {'key': 'archived', 'label': f'已归档 {counts["archived"]}'},
            ],
        },
        {
            'key': 'level',
            'label': '层级',
            'options': [
                {'key': 'all', 'label': '全部层级'},
                {'key': 'level_1', 'label': f'一级 {counts["level_1"]}'},
                {'key': 'level_2', 'label': f'二级 {counts["level_2"]}'},
                {'key': 'level_3', 'label': f'三级 {counts["level_3"]}'},
            ],
        },
    ]
    payload['summary'] = [
        {'label': '当前语言', 'value': admin_locale_label(locale), 'meta': locale.locale_code},
        {'label': '分类总数', 'value': counts['total'], 'meta': '当前语言'},
        {'label': '一级分类', 'value': counts['level_1'], 'meta': '根节点'},
        {'label': '二级分类', 'value': counts['level_2'], 'meta': '中间节点'},
        {'label': '三级分类', 'value': counts['level_3'], 'meta': '最终节点'},
    ]
    payload['table'] = {
        'columnWidths': {
            'title': 248,
            'slug': 208,
            'parent': 160,
            'status': 92,
            'updatedAt': 132,
        },
        'columns': [
            {'key': 'title', 'label': '分类名称', 'type': 'title'},
            {'key': 'slug', 'label': 'URL 别名', 'type': 'path'},
            {'key': 'parent', 'label': '上级分类', 'type': 'text'},
            {'key': 'status', 'label': '状态', 'type': 'badge'},
            {'key': 'updatedAt', 'label': '更新', 'type': 'text'},
        ],
        'rows': rows,
        'emptyMessage': '当前语言还没有分类，先建立一级分类。',
    }
    payload['notes'] = [
        {
            'title': '设计原则',
            'body': '语言只是筛选范围，不是分类树的第一层。英文、阿拉伯语等语言共享同一套业务分类结构，只分别维护名称、slug 和 SEO。',
        },
        {
            'title': '交互方式',
            'body': '表格支持一级、二级、三级分类折叠。点击分类名前的箭头可以展开或收起子分类，筛选器只做视图过滤，不改变实际树结构。',
        },
    ]
    return payload


def category_editor_page_payload(*, title: str, description: str, locale_label: str) -> dict:
    return {
        'sectionKey': 'articles',
        'sectionLabel': '文章',
        'title': title,
        'description': description,
        'pageType': 'form',
        'workspaceLabel': title,
        'workspaceMeta': f'当前语言 {locale_label}。这里维护文章分类的层级、别名、SEO 和排序。',
        'searchPlaceholder': '',
        'tabs': [],
        'filters': [],
        'actions': [],
        'scopeActions': [],
        'summary': [],
        'table': None,
        'notes': [],
    }
