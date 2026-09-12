from __future__ import annotations

from copy import deepcopy

from django.core.management.base import BaseCommand
from django.db import transaction

from sitecore.models import (
    Article,
    ArticleTag,
    Category,
    ComponentBinding,
    CtaBinding,
    FreePage,
    NavigationItem,
    NavigationMenu,
    PageRoute,
    Site,
    SiteLocale,
    Tag,
)


LOCALE_LABELS = {
    'en': 'English',
    'ar': 'العربية',
    'fr': 'Français',
    'es': 'Español',
    'ru': 'Русский',
}

AR_CATEGORY_NAMES = {
    'Architecture': 'البنية المعمارية',
    'Company News': 'أخبار الشركة',
    'Product Technology': 'تقنية المنتج',
    'Project Insights': 'رؤى المشاريع',
}

AR_MENU_NAMES = {
    'Main Header': 'التنقل الرئيسي',
    'Main Footer': 'تنقل التذييل',
}

AR_NAV_LABELS = {
    'Company Profile': 'ملف الشركة',
    'Development History': 'تاريخ التطور',
    'Enterprise Concept': 'فلسفة المؤسسة',
    'News Center': 'مركز الأخبار',
    'Investor Relations': 'علاقات المستثمرين',
    'Service Commitment': 'التزام الخدمة',
    'Service Support': 'دعم الخدمة',
    'Remote Control': 'التحكم عن بعد',
    'Training System': 'نظام التدريب',
    'Contact Us': 'اتصل بنا',
    'Career': 'الوظائف',
    'Product Center': 'مركز المنتجات',
    'About Us': 'حولنا',
    'Solutions': 'الحلول',
    'Service Center': 'مركز الخدمة',
}

AR_PAGE_TITLES = {
    '/en/': 'أنظمة نيوكراون للتعبئة',
    '/en/about/': 'حول الشركة',
    '/en/news/': 'مركز الأخبار',
    '/en/articles/': 'مركز المقالات',
    '/en/product-center/': 'مركز المنتجات',
    '/en/fuwu/': 'مركز الخدمة',
    '/en/lxwm/': 'اتصل بنا',
}

AR_PAGE_SUMMARIES = {
    '/en/': 'موقع تجريبي لخطوط تعبئة المنتجات السائلة مبني على محتوى PostgreSQL ومخرجات Astro الثابتة ومسارات تحكم Django.',
}

AR_ARTICLE_TITLES = {
    'database-driven-website-product': 'هندسة موقع ويب مدفوعة بقاعدة البيانات',
    'beverage-line-smart-factory-planning': 'تخطيط المصنع الذكي لخطوط تعبئة المشروبات',
    'product-center-filling-system-selection': 'كيفية تنظيم مركز منتجات لمشتري أنظمة التعبئة',
    'service-center-line-delivery-framework': 'إطار مركز الخدمة لتسليم خطوط تعبئة السوائل',
    'global-brand-quality-line-requirements': 'ما الذي تطلبه العلامات العالمية من موردي خطوط التعبئة',
    'beverage-line-readiness-for-fast-moving-products': 'جاهزية خطوط المشروبات لإطلاق المنتجات سريعاً',
    'project-benchmark-complete-line-growth': 'مرجع مشروع لتخطيط خط متكامل يدعم نمو الطاقة',
    'water_filling_line_project_evaluation': 'كيفية تقييم مشروع خط تعبئة المياه',
}


def locale_path(path: str, source_locale: str, target_locale: str) -> str:
    source_prefix = f'/{source_locale}/'
    target_prefix = f'/{target_locale}/'
    if path == source_prefix:
        return target_prefix
    if path.startswith(source_prefix):
        return f'{target_prefix}{path[len(source_prefix):]}'
    return path


def replace_locale_refs(value, source_locale: str, target_locale: str):
    if isinstance(value, str):
        return value.replace(f'/{source_locale}/', f'/{target_locale}/')
    if isinstance(value, list):
        return [replace_locale_refs(item, source_locale, target_locale) for item in value]
    if isinstance(value, dict):
        return {key: replace_locale_refs(item, source_locale, target_locale) for key, item in value.items()}
    return deepcopy(value)


def translated_name(value: str, target_locale: str, mapping: dict[str, str]) -> str:
    if target_locale == 'ar':
        return mapping.get(value, value)
    return value


class Command(BaseCommand):
    help = 'Bootstrap a new locale by cloning routes, pages, articles, categories and navigation from an existing locale.'

    def add_arguments(self, parser):
        parser.add_argument('--site-code', default='siteos_demo')
        parser.add_argument('--source-locale', default='en')
        parser.add_argument('--target-locale', default='ar')
        parser.add_argument('--target-label', default='')
        parser.add_argument('--direction', default='')

    @transaction.atomic
    def handle(self, *args, **options):
        site = Site.objects.get(code=options['site_code'], enabled=True)
        source_code = options['source_locale'].strip().lower()
        target_code = options['target_locale'].strip().lower()
        source_locale = SiteLocale.objects.get(site=site, locale_code=source_code, enabled=True)

        target_label = options['target_label'].strip() or LOCALE_LABELS.get(target_code, target_code.upper())
        direction = options['direction'].strip() or ('rtl' if target_code == 'ar' else source_locale.direction)
        target_locale, created = SiteLocale.objects.update_or_create(
            site=site,
            locale_code=target_code,
            defaults={
                'label': target_label,
                'direction': direction,
                'enabled': True,
                'is_default': False,
                'sort_order': source_locale.sort_order + 10,
            },
        )

        category_map = self.clone_categories(site, source_locale, target_locale)
        tag_map = self.clone_tags(site, source_locale, target_locale)
        route_map = self.clone_routes(site, source_locale, target_locale)
        page_count = self.clone_pages(source_locale, target_locale, route_map)
        article_map = self.clone_articles(source_locale, target_locale, route_map, category_map)
        self.clone_article_tags(source_locale, article_map, tag_map)
        menu_count, item_count = self.clone_navigation(site, source_locale, target_locale, route_map)
        component_count = self.clone_component_bindings(site, source_locale, target_locale)
        cta_binding_count = self.clone_cta_bindings(site, source_locale, target_locale)

        action = 'created' if created else 'updated'
        self.stdout.write(
            self.style.SUCCESS(
                f'Locale {target_code} {action}: '
                f'{len(category_map)} categories, {len(route_map)} routes, {page_count} pages, '
                f'{len(article_map)} articles, {menu_count} menus/{item_count} items, '
                f'{component_count} component bindings, {cta_binding_count} CTA bindings.'
            )
        )

    def clone_categories(self, site: Site, source_locale: SiteLocale, target_locale: SiteLocale) -> dict[int, Category]:
        mapping: dict[int, Category] = {}
        rows = Category.objects.filter(site=site, locale=source_locale).order_by('sort_order', 'id')
        for row in rows:
            target, _created = Category.objects.update_or_create(
                site=site,
                locale=target_locale,
                code=row.code,
                defaults={
                    'name': translated_name(row.name, target_locale.locale_code, AR_CATEGORY_NAMES),
                    'slug': row.slug,
                    'description': row.description,
                    'status': row.status,
                    'sort_order': row.sort_order,
                    'parent': None,
                    'seo_json': replace_locale_refs(row.seo_json or {}, source_locale.locale_code, target_locale.locale_code),
                },
            )
            mapping[row.id] = target
        for row in rows:
            target = mapping[row.id]
            target.parent = mapping.get(row.parent_id)
            target.save(update_fields=['parent'])
        return mapping

    def clone_tags(self, site: Site, source_locale: SiteLocale, target_locale: SiteLocale) -> dict[int, Tag]:
        mapping: dict[int, Tag] = {}
        rows = Tag.objects.filter(site=site, locale=source_locale).order_by('name', 'id')
        for row in rows:
            target, _created = Tag.objects.update_or_create(
                site=site,
                locale=target_locale,
                code=row.code,
                defaults={'name': row.name, 'slug': row.slug},
            )
            mapping[row.id] = target
        return mapping

    def clone_routes(self, site: Site, source_locale: SiteLocale, target_locale: SiteLocale) -> dict[int, PageRoute]:
        mapping: dict[int, PageRoute] = {}
        rows = PageRoute.objects.filter(site=site, locale=source_locale).order_by('path')
        for row in rows:
            target_path = locale_path(row.path, source_locale.locale_code, target_locale.locale_code)
            target, _created = PageRoute.objects.update_or_create(
                site=site,
                locale=target_locale,
                path=target_path,
                defaults={
                    'route_type': row.route_type,
                    'status': row.status,
                    'page_title': self.localized_page_title(row.path, row.page_title, target_locale.locale_code),
                    'meta_description': replace_locale_refs(row.meta_description or '', source_locale.locale_code, target_locale.locale_code),
                    'canonical_url': replace_locale_refs(row.canonical_url or '', source_locale.locale_code, target_locale.locale_code),
                    'og_title': replace_locale_refs(row.og_title or '', source_locale.locale_code, target_locale.locale_code),
                    'og_description': replace_locale_refs(row.og_description or '', source_locale.locale_code, target_locale.locale_code),
                    'og_image_url': replace_locale_refs(row.og_image_url or '', source_locale.locale_code, target_locale.locale_code),
                    'robots': row.robots,
                    'published_at': row.published_at,
                },
            )
            mapping[row.id] = target
        return mapping

    def clone_pages(self, source_locale: SiteLocale, target_locale: SiteLocale, route_map: dict[int, PageRoute]) -> int:
        count = 0
        rows = FreePage.objects.select_related('route').filter(route__locale=source_locale).order_by('route__path')
        for row in rows:
            target_route = route_map[row.route_id]
            hero_json = replace_locale_refs(row.hero_json or {}, source_locale.locale_code, target_locale.locale_code)
            body_json = replace_locale_refs(row.body_json or {}, source_locale.locale_code, target_locale.locale_code)
            config_json = replace_locale_refs(row.config_json or {}, source_locale.locale_code, target_locale.locale_code)

            target_title = self.localized_page_title(row.route.path, row.title, target_locale.locale_code)
            target_summary = self.localized_page_summary(row.route.path, row.summary, target_locale.locale_code)

            if target_locale.locale_code == 'ar' and row.route.path == '/en/':
                hero_json['eyebrow'] = 'تكامل المصنع الذكي للمنتجات السائلة'
                hero_json['headline'] = 'حلول متكاملة للمعالجة والنفخ والتعبئة والتغليف والخدمات الصناعية'
                if 'home' in config_json and isinstance(config_json['home'], dict):
                    config_json['home']['heroIntro'] = (
                        'منصة عرض لخطوط تعبئة المياه والمشروبات والمنتجات السائلة مع صفحات منتجات ومركز خدمة ومحتوى تسويقي.'
                    )

            FreePage.objects.update_or_create(
                route=target_route,
                defaults={
                    'template_key': row.template_key,
                    'title': target_title,
                    'summary': target_summary,
                    'hero_json': hero_json,
                    'body_json': body_json,
                    'config_json': config_json,
                },
            )
            count += 1
        return count

    def clone_articles(
        self,
        source_locale: SiteLocale,
        target_locale: SiteLocale,
        route_map: dict[int, PageRoute],
        category_map: dict[int, Category],
    ) -> dict[int, Article]:
        mapping: dict[int, Article] = {}
        rows = Article.objects.select_related('route', 'category').filter(route__locale=source_locale).order_by('id')
        for row in rows:
            target_route = route_map[row.route_id]
            target_title = self.localized_article_title(row.slug, row.title, target_locale.locale_code)
            target_route.page_title = target_title
            target_route.save(update_fields=['page_title'])

            target, _created = Article.objects.update_or_create(
                route=target_route,
                defaults={
                    'category': category_map.get(row.category_id),
                    'title': target_title,
                    'slug': row.slug,
                    'excerpt': row.excerpt,
                    'body_markdown': replace_locale_refs(row.body_markdown or '', source_locale.locale_code, target_locale.locale_code),
                    'body_json': replace_locale_refs(row.body_json or {}, source_locale.locale_code, target_locale.locale_code),
                    'cover_image_url': replace_locale_refs(row.cover_image_url or '', source_locale.locale_code, target_locale.locale_code),
                    'author_name': row.author_name,
                    'status': row.status,
                    'published_at': row.published_at,
                    'reading_minutes': row.reading_minutes,
                    'seo_json': replace_locale_refs(row.seo_json or {}, source_locale.locale_code, target_locale.locale_code),
                    'config_json': replace_locale_refs(row.config_json or {}, source_locale.locale_code, target_locale.locale_code),
                },
            )
            mapping[row.id] = target
        return mapping

    def clone_article_tags(self, source_locale: SiteLocale, article_map: dict[int, Article], tag_map: dict[int, Tag]) -> None:
        rows = ArticleTag.objects.select_related('article', 'tag').filter(article__route__locale=source_locale)
        for row in rows:
            target_article = article_map.get(row.article_id)
            target_tag = tag_map.get(row.tag_id)
            if not target_article or not target_tag:
                continue
            ArticleTag.objects.update_or_create(article=target_article, tag=target_tag, defaults={})

    def clone_navigation(
        self,
        site: Site,
        source_locale: SiteLocale,
        target_locale: SiteLocale,
        route_map: dict[int, PageRoute],
    ) -> tuple[int, int]:
        menu_count = 0
        item_count = 0
        menu_rows = NavigationMenu.objects.filter(site=site, locale=source_locale).order_by('region', 'sort_order', 'id')
        for menu in menu_rows:
            target_menu, _created = NavigationMenu.objects.update_or_create(
                site=site,
                locale=target_locale,
                code=menu.code,
                defaults={
                    'name': translated_name(menu.name, target_locale.locale_code, AR_MENU_NAMES),
                    'region': menu.region,
                    'enabled': menu.enabled,
                    'sort_order': menu.sort_order,
                },
            )
            NavigationItem.objects.filter(menu=target_menu).delete()
            source_items = list(
                NavigationItem.objects.filter(menu=menu).select_related('route').order_by('parent_id', 'sort_order', 'id')
            )
            created_items: dict[int, NavigationItem] = {}
            pending = source_items.copy()
            while pending:
                progressed = False
                for item in pending[:]:
                    if item.parent_id and item.parent_id not in created_items:
                        continue
                    target_item = NavigationItem.objects.create(
                        menu=target_menu,
                        parent=created_items.get(item.parent_id),
                        route=route_map.get(item.route_id),
                        label=self.localized_nav_label(item.label, target_locale.locale_code),
                        href=replace_locale_refs(item.href or '', source_locale.locale_code, target_locale.locale_code),
                        sort_order=item.sort_order,
                        enabled=item.enabled,
                        config_json=replace_locale_refs(item.config_json or {}, source_locale.locale_code, target_locale.locale_code),
                    )
                    created_items[item.id] = target_item
                    pending.remove(item)
                    progressed = True
                    item_count += 1
                if not progressed:
                    raise RuntimeError(f'Failed to rebuild navigation tree for menu {menu.code}.')
            menu_count += 1
        return menu_count, item_count

    def clone_component_bindings(self, site: Site, source_locale: SiteLocale, target_locale: SiteLocale) -> int:
        count = 0
        rows = ComponentBinding.objects.filter(site=site, locale=source_locale).order_by('region', 'priority', 'id')
        for row in rows:
            ComponentBinding.objects.update_or_create(
                component=row.component,
                site=site,
                locale=target_locale,
                scope_type=row.scope_type,
                scope_value=row.scope_value,
                region=row.region,
                defaults={
                    'priority': row.priority,
                    'enabled': row.enabled,
                    'config_json': replace_locale_refs(row.config_json or {}, source_locale.locale_code, target_locale.locale_code),
                },
            )
            count += 1
        return count

    def clone_cta_bindings(self, site: Site, source_locale: SiteLocale, target_locale: SiteLocale) -> int:
        count = 0
        rows = CtaBinding.objects.filter(site=site, locale=source_locale).order_by('position', 'priority', 'id')
        for row in rows:
            CtaBinding.objects.update_or_create(
                cta=row.cta,
                site=site,
                locale=target_locale,
                scope_type=row.scope_type,
                scope_value=row.scope_value,
                position=row.position,
                defaults={
                    'priority': row.priority,
                    'enabled': row.enabled,
                    'config_json': replace_locale_refs(row.config_json or {}, source_locale.locale_code, target_locale.locale_code),
                },
            )
            count += 1
        return count

    def localized_page_title(self, source_path: str, fallback: str, target_locale: str) -> str:
        if target_locale == 'ar':
            return AR_PAGE_TITLES.get(source_path, fallback)
        return fallback

    def localized_page_summary(self, source_path: str, fallback: str, target_locale: str) -> str:
        if target_locale == 'ar':
            return AR_PAGE_SUMMARIES.get(source_path, fallback)
        return fallback

    def localized_article_title(self, slug: str, fallback: str, target_locale: str) -> str:
        if target_locale == 'ar':
            return AR_ARTICLE_TITLES.get(slug, fallback)
        return fallback

    def localized_nav_label(self, label: str, target_locale: str) -> str:
        if target_locale == 'ar':
            return AR_NAV_LABELS.get(label, label)
        return label
