from __future__ import annotations

from collections import defaultdict

from sitecore.models import Category, Site, SiteLocale


def fetch_categories(site: Site, locale: SiteLocale):
    return list(
        Category.objects.filter(site=site, locale=locale)
        .select_related('parent')
        .order_by('sort_order', 'name', 'id')
    )


def ordered_categories(site: Site, locale: SiteLocale) -> list[Category]:
    rows = fetch_categories(site, locale)
    if not rows:
        return []

    rows_by_id = {row.id: row for row in rows}
    children: dict[int | None, list[Category]] = defaultdict(list)
    for row in rows:
        parent_id = row.parent_id if row.parent_id in rows_by_id else None
        children[parent_id].append(row)

    for bucket in children.values():
        bucket.sort(key=lambda item: (item.sort_order, item.name.lower(), item.id))

    ordered: list[Category] = []
    visited: set[int] = set()

    def visit(parent_id: int | None):
        for row in children.get(parent_id, []):
            if row.id in visited:
                continue
            visited.add(row.id)
            ordered.append(row)
            visit(row.id)

    visit(None)

    for row in rows:
        if row.id not in visited:
            visited.add(row.id)
            ordered.append(row)

    return ordered


def category_depth_map(categories: list[Category]) -> dict[int, int]:
    by_id = {category.id: category for category in categories}
    depth_map: dict[int, int] = {}

    def depth_for(category: Category) -> int:
        if category.id in depth_map:
            return depth_map[category.id]
        seen: set[int] = set()
        depth = 0
        current = category
        while current.parent_id and current.parent_id in by_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            current = by_id[current.parent_id]
            depth += 1
        depth_map[category.id] = depth
        return depth

    for category in categories:
        depth_for(category)
    return depth_map


def category_label_map(categories: list[Category]) -> dict[int, str]:
    depth_map = category_depth_map(categories)
    labels: dict[int, str] = {}
    for category in categories:
        prefix = '  ' * depth_map.get(category.id, 0)
        labels[category.id] = f'{prefix}{category.name}'
    return labels


def descendant_ids(categories: list[Category], category_id: int) -> set[int]:
    children: dict[int, list[int]] = defaultdict(list)
    for category in categories:
        if category.parent_id:
            children[category.parent_id].append(category.id)

    found: set[int] = set()
    stack = list(children.get(category_id, []))
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(children.get(current, []))
    return found


def category_tree_data(site: Site, locale: SiteLocale) -> list[dict[str, object]]:
    categories = ordered_categories(site, locale)
    if not categories:
        return []

    category_ids = {category.id for category in categories}
    children_map: dict[int | None, list[Category]] = defaultdict(list)
    for category in categories:
        parent_id = category.parent_id if category.parent_id in category_ids else None
        children_map[parent_id].append(category)

    for bucket in children_map.values():
        bucket.sort(key=lambda item: (item.sort_order, item.name.lower(), item.id))

    def build(parent_id: int | None, depth: int = 0) -> list[dict[str, object]]:
        if depth >= 3:
            return []
        nodes: list[dict[str, object]] = []
        for category in children_map.get(parent_id, []):
            nodes.append(
                {
                    'id': category.id,
                    'name': category.name,
                    'code': category.code,
                    'children': build(category.id, depth + 1),
                }
            )
        return nodes

    return build(None)
