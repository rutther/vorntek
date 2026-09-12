import json
import os
import re
import secrets
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from marketing.models import MarketingIntegration, ProviderEventMapping, TrackingRule
from sitecore.models import (
    Article,
    ArticleTag,
    ComponentBinding,
    Cta,
    CtaBinding,
    FreePage,
    MediaAsset,
    MediaAssetBinding,
    NavigationItem,
    NavigationMenu,
    PageRoute,
    Release,
    Site,
    SiteLocale,
    ThreeDPlacement,
    ThreeDViewProfile,
)
from console.three_d_config import placement_public_config
from console.release_pipeline import (
    PreviewBuildConfigurationError,
    prepare_snapshot_file_path,
    prepare_snapshot_output_dir,
)


META_PIXEL_ID_RE = re.compile(r'^[0-9]{5,32}$')
PROVIDER_EVENT_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,80}$')
RELEASE_KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$')
ARTICLE_FILE_SLUG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$')


def atomic_write_json(
    path: Path,
    payload: dict,
    *,
    output_dir: Path,
    site_code: str,
) -> None:
    path = prepare_snapshot_file_path(
        path,
        output_dir=output_dir,
        site_code=site_code,
    )
    temporary_path = path.parent / f'.snapshot-{secrets.token_hex(12)}.tmp'
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def isoformat(value) -> str | None:
    return value.isoformat() if value else None


class Command(BaseCommand):
    help = 'Export the current database-managed release snapshot for the Astro delivery app.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            required=True,
            help='Explicit site-scoped preview directory where snapshot files will be written.',
        )
        parser.add_argument(
            '--release-id',
            default='dev-current',
            help='Release identifier to write into manifest.json.',
        )
        parser.add_argument(
            '--site-code',
            default='siteos_demo',
            help='Site code to export.',
        )
        parser.add_argument(
            '--created-by',
            default='system',
            help='Actor recorded on the release row.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        release_id = options['release_id']
        if not isinstance(release_id, str) or not RELEASE_KEY_RE.fullmatch(release_id):
            raise CommandError('发布快照键无效。')
        try:
            output_dir = prepare_snapshot_output_dir(options['output_dir'], site_code=options['site_code'])
        except PreviewBuildConfigurationError:
            raise CommandError('发布快照输出目录未安全配置。') from None
        generated_at = timezone.now().isoformat()

        with transaction.atomic():
            site = Site.objects.select_for_update().get(code=options['site_code'], enabled=True)
            release = Release.objects.select_for_update().filter(release_key=release_id).first()
            if release is not None and release.site_id != site.id:
                raise CommandError('发布快照键已由其他站点占用。')
            if release is None:
                try:
                    with transaction.atomic():
                        release = Release.objects.create(
                            site=site,
                            release_key=release_id,
                            status='draft',
                            snapshot_manifest={},
                            created_by=options['created_by'],
                        )
                except IntegrityError:
                    raise CommandError('发布快照键已由其他站点占用。') from None
            site_snapshot = self.build_site_snapshot(site, generated_at)
            routes_snapshot = self.build_routes_snapshot(site, generated_at)
            pages_snapshot = self.build_pages_snapshot(site, generated_at)
            articles_snapshot = self.build_articles_snapshot(site, generated_at)
            assets_snapshot = self.build_assets_snapshot(site, generated_at)
            navigation_snapshot = self.build_navigation_snapshot(site, generated_at)
            components_snapshot = self.build_components_snapshot(site, generated_at)
            ctas_snapshot = self.build_ctas_snapshot(site, generated_at)
            marketing_snapshot = self.build_marketing_snapshot(site, generated_at)

        manifest = {
            'schemaVersion': 1,
            'releaseId': release_id,
            'generatedAt': generated_at,
            'source': 'django.export_release_snapshot',
            'siteCode': site.code,
            'files': [
                'site.json',
                'routes.json',
                'navigation.json',
                'components.json',
                'ctas.json',
                'pages.json',
                'articles/index.json',
                'assets.json',
                'marketing.json',
            ],
        }

        try:
            def write_json(path: Path, payload: dict) -> None:
                atomic_write_json(
                    path,
                    payload,
                    output_dir=output_dir,
                    site_code=site.code,
                )

            write_json(output_dir / 'site.json', site_snapshot)
            write_json(output_dir / 'routes.json', routes_snapshot)
            write_json(output_dir / 'navigation.json', navigation_snapshot)
            write_json(output_dir / 'components.json', components_snapshot)
            write_json(output_dir / 'ctas.json', ctas_snapshot)
            write_json(output_dir / 'pages.json', pages_snapshot)
            write_json(output_dir / 'articles' / 'index.json', articles_snapshot)
            for article in articles_snapshot['articles']:
                slug = article.get('slug', '')
                if not isinstance(slug, str) or not ARTICLE_FILE_SLUG_RE.fullmatch(slug):
                    raise CommandError('文章快照文件名无效。')
                write_json(output_dir / 'articles' / f'{slug}.json', {'article': article})
            write_json(output_dir / 'assets.json', assets_snapshot)
            write_json(output_dir / 'marketing.json', marketing_snapshot)
            write_json(output_dir / 'manifest.json', manifest)
        except CommandError:
            raise
        except OSError:
            raise CommandError('发布快照写入失败。') from None

        with transaction.atomic():
            release = Release.objects.select_for_update().get(pk=release.pk, site=site)
            release.status = 'exported'
            release.snapshot_manifest = manifest
            release.created_by = options['created_by']
            release.exported_at = timezone.now()
            release.save(update_fields=['status', 'snapshot_manifest', 'created_by', 'exported_at'])

        self.stdout.write(self.style.SUCCESS('Exported site-scoped preview snapshot.'))

    def build_site_snapshot(self, site: Site, generated_at: str) -> dict:
        locales = [
            {
                'id': locale.id,
                'localeCode': locale.locale_code,
                'label': locale.label,
                'direction': locale.direction,
                'isDefault': locale.is_default,
                'sortOrder': locale.sort_order,
            }
            for locale in SiteLocale.objects.filter(site=site, enabled=True).order_by('sort_order', 'locale_code')
        ]

        return {
            'schemaVersion': 1,
            'generatedAt': generated_at,
            'site': {
                'id': site.id,
                'code': site.code,
                'name': site.name,
                'baseUrl': site.base_url,
                'defaultLocale': site.default_locale,
                'config': site.config_json,
                'locales': locales,
            },
        }

    def build_routes_snapshot(self, site: Site, generated_at: str) -> dict:
        routes = [
            self.serialize_route(route)
            for route in PageRoute.objects.select_related('locale')
            .filter(site=site, status='published', locale__enabled=True)
            .order_by('path')
        ]

        return {
            'schemaVersion': 1,
            'generatedAt': generated_at,
            'routes': routes,
        }

    def build_pages_snapshot(self, site: Site, generated_at: str) -> dict:
        pages = []
        page_rows = (
            FreePage.objects.select_related('route', 'route__locale')
            .filter(route__site=site, route__status='published', route__locale__enabled=True)
            .order_by('route__path')
        )

        for page in page_rows:
            pages.append(
                {
                    'id': page.id,
                    'route': self.serialize_route(page.route),
                    'templateKey': page.template_key,
                    'title': page.title,
                    'summary': page.summary,
                    'hero': page.hero_json,
                    'body': page.body_json,
                    'config': page.config_json,
                }
            )

        return {
            'schemaVersion': 1,
            'generatedAt': generated_at,
            'freePages': pages,
        }

    def build_articles_snapshot(self, site: Site, generated_at: str) -> dict:
        article_rows = (
            Article.objects.select_related('route', 'route__locale', 'category')
            .filter(route__site=site, route__status='published', route__locale__enabled=True, status='published')
            .order_by('-published_at', 'title')
        )
        article_ids = [article.id for article in article_rows]
        tag_rows = (
            ArticleTag.objects.select_related('tag')
            .filter(article_id__in=article_ids)
            .order_by('tag__name')
        )
        tags_by_article: dict[int, list[dict]] = {}
        for row in tag_rows:
            tags_by_article.setdefault(row.article_id, []).append(
                {
                    'id': row.tag.id,
                    'code': row.tag.code,
                    'name': row.tag.name,
                    'slug': row.tag.slug,
                }
            )

        articles = []
        for article in article_rows:
            articles.append(
                {
                    'id': article.id,
                    'route': self.serialize_route(article.route),
                    'category': self.serialize_category(article.category),
                    'title': article.title,
                    'slug': article.slug,
                    'excerpt': article.excerpt,
                    'bodyMarkdown': article.body_markdown,
                    'body': article.body_json,
                    'coverImageUrl': article.cover_image_url,
                    'authorName': article.author_name,
                    'publishedAt': isoformat(article.published_at),
                    'readingMinutes': article.reading_minutes,
                    'seo': article.seo_json,
                    'config': article.config_json,
                    'tags': tags_by_article.get(article.id, []),
                }
            )

        categories = []
        seen_categories = set()
        for article in articles:
            category = article['category']
            if category and category['id'] not in seen_categories:
                categories.append(category)
                seen_categories.add(category['id'])

        return {
            'schemaVersion': 1,
            'generatedAt': generated_at,
            'categories': categories,
            'articles': articles,
        }

    def build_navigation_snapshot(self, site: Site, generated_at: str) -> dict:
        menus = []
        menu_rows = (
            NavigationMenu.objects.select_related('locale')
            .filter(site=site, locale__enabled=True, enabled=True)
            .order_by('region', 'sort_order', 'code')
        )

        for menu in menu_rows:
            item_rows = (
                NavigationItem.objects.select_related('route')
                .filter(menu=menu, enabled=True)
                .order_by('parent_id', 'sort_order', 'label')
            )
            items = []
            for item in item_rows:
                items.append(
                    {
                        'id': item.id,
                        'parentId': item.parent_id,
                        'label': item.label,
                        'href': item.route.path if item.route_id else item.href,
                        'routeId': item.route_id,
                        'sortOrder': item.sort_order,
                        'config': item.config_json,
                    }
                )

            menus.append(
                {
                    'id': menu.id,
                    'code': menu.code,
                    'name': menu.name,
                    'locale': menu.locale.locale_code,
                    'region': menu.region,
                    'sortOrder': menu.sort_order,
                    'items': items,
                }
            )

        return {
            'schemaVersion': 1,
            'generatedAt': generated_at,
            'menus': menus,
        }

    def build_assets_snapshot(self, site: Site, generated_at: str) -> dict:
        assets = [
            {
                'id': asset.id,
                'type': asset.asset_type,
                'title': asset.title,
                'originalName': asset.original_name,
                'altText': asset.alt_text,
                'caption': asset.caption,
                'mimeType': asset.mime_type,
                'fileExt': asset.file_ext,
                'fileSizeBytes': asset.file_size_bytes,
                'sha256': asset.sha256,
                'publicPath': asset.public_path,
                'config': asset.config_json,
            }
            for asset in MediaAsset.objects.filter(site=site, status='active').order_by('asset_type', 'public_path')
        ]
        bindings = [
            {
                'id': binding.id,
                'assetId': binding.asset_id,
                'locale': binding.locale.locale_code if binding.locale_id else None,
                'entityType': binding.entity_type,
                'entityId': binding.entity_id,
                'role': binding.role,
                'sortOrder': binding.sort_order,
                'config': binding.config_json,
            }
            for binding in MediaAssetBinding.objects.select_related('locale', 'asset')
            .filter(site=site, enabled=True, asset__status='active')
            .order_by('entity_type', 'entity_id', 'role', 'sort_order')
        ]

        profiles = [
            {
                'id': profile.id,
                'code': profile.code,
                'name': profile.name,
                'purpose': profile.purpose,
                'status': profile.status,
                'backgroundColor': profile.background_color,
                'camera': {
                    'position': {
                        'x': float(profile.camera_position_x),
                        'y': float(profile.camera_position_y),
                        'z': float(profile.camera_position_z),
                    },
                    'target': {
                        'x': float(profile.camera_target_x),
                        'y': float(profile.camera_target_y),
                        'z': float(profile.camera_target_z),
                    },
                    'fov': float(profile.fov),
                },
                'showUi': profile.show_ui,
                'allowInteraction': profile.allow_interaction,
                'assetId': profile.asset_id,
                'posterAssetId': profile.poster_asset_id,
                'notes': profile.notes,
                'config': profile.config_json,
            }
            for profile in ThreeDViewProfile.objects.select_related('asset')
            .filter(site=site)
            .exclude(status='archived')
            .order_by('purpose', 'name')
        ]
        placements = [
            placement_public_config(placement)
            for placement in ThreeDPlacement.objects.select_related('profile', 'profile__asset', 'profile__poster_asset')
            .filter(site=site)
            .order_by('sort_order', 'slot_code')
        ]

        return {
            'schemaVersion': 1,
            'generatedAt': generated_at,
            'assets': assets,
            'bindings': bindings,
            'threeDProfiles': profiles,
            'threeDPlacements': placements,
        }

    def build_components_snapshot(self, site: Site, generated_at: str) -> dict:
        bindings = []
        binding_rows = (
            ComponentBinding.objects.select_related('component', 'locale')
            .filter(site=site, component__enabled=True, enabled=True)
            .order_by('region', 'priority', 'component__code')
        )

        for binding in binding_rows:
            bindings.append(
                {
                    'id': binding.id,
                    'componentCode': binding.component.code,
                    'componentName': binding.component.name,
                    'componentType': binding.component.component_type,
                    'locale': binding.locale.locale_code if binding.locale_id else None,
                    'scopeType': binding.scope_type,
                    'scopeValue': binding.scope_value,
                    'region': binding.region,
                    'priority': binding.priority,
                    'config': binding.config_json,
                }
            )

        return {
            'schemaVersion': 1,
            'generatedAt': generated_at,
            'bindings': bindings,
        }

    def build_ctas_snapshot(self, site: Site, generated_at: str) -> dict:
        ctas = [
            {
                'id': cta.id,
                'code': cta.code,
                'name': cta.name,
                'label': cta.label,
                'targetUrl': cta.target_url,
                'targetType': cta.target_type,
                'businessGoal': cta.business_goal,
                'eventCode': cta.event.code if cta.event_id else 'cta_click',
                'config': cta.config_json,
            }
            for cta in Cta.objects.select_related('event').filter(site=site, enabled=True).order_by('code')
        ]

        bindings = []
        binding_rows = (
            CtaBinding.objects.select_related('cta', 'locale')
            .filter(site=site, cta__enabled=True, enabled=True)
            .order_by('position', 'priority', 'cta__code')
        )
        for binding in binding_rows:
            bindings.append(
                {
                    'id': binding.id,
                    'ctaCode': binding.cta.code,
                    'locale': binding.locale.locale_code if binding.locale_id else None,
                    'scopeType': binding.scope_type,
                    'scopeValue': binding.scope_value,
                    'position': binding.position,
                    'priority': binding.priority,
                    'config': binding.config_json,
                }
            )

        return {
            'schemaVersion': 1,
            'generatedAt': generated_at,
            'ctas': ctas,
            'bindings': bindings,
        }

    def build_marketing_snapshot(self, site: Site, generated_at: str) -> dict:
        meta_pixels = []
        mapped_event_codes: set[str] = set()

        integrations = (
            MarketingIntegration.objects.select_related('provider')
            .filter(
                site=site,
                provider__code='meta',
                provider__enabled=True,
                integration_type='pixel',
                enabled=True,
            )
            .order_by('id')
        )

        for integration in integrations:
            if not META_PIXEL_ID_RE.fullmatch(integration.public_id or ''):
                self.stderr.write(f'Skipped invalid Meta Pixel ID on integration {integration.id}')
                continue

            rules = []
            tracking_rules = (
                TrackingRule.objects.select_related('canonical_event')
                .filter(integration=integration, enabled=True)
                .order_by('priority', 'id')
            )

            for rule in tracking_rules:
                if rule.canonical_event_id:
                    mapped_event_codes.add(rule.canonical_event.code)
                rules.append(
                    {
                        'scopeType': rule.scope_type,
                        'scopeValue': rule.scope_value,
                        'priority': rule.priority,
                        'eventCode': rule.canonical_event.code if rule.canonical_event_id else None,
                    }
                )

            if not rules:
                continue

            meta_pixels.append(
                {
                    'id': integration.id,
                    'name': integration.name,
                    'pixelId': integration.public_id,
                    'consentCategory': integration.consent_category,
                    'trackingRules': rules,
                }
            )

        meta_event_map = {}
        mappings = (
            ProviderEventMapping.objects.select_related('provider', 'canonical_event')
            .filter(
                provider__code='meta',
                provider__enabled=True,
                canonical_event__code__in=mapped_event_codes,
                enabled=True,
            )
            .order_by('canonical_event__code', 'provider_event_name')
        )
        for mapping in mappings:
            if PROVIDER_EVENT_RE.fullmatch(mapping.provider_event_name or ''):
                meta_event_map[mapping.canonical_event.code] = mapping.provider_event_name

        return {
            'schemaVersion': 1,
            'generatedAt': generated_at,
            'integrations': {
                'metaPixels': meta_pixels,
            },
            'eventMappings': {
                'meta': meta_event_map,
            },
        }

    def serialize_route(self, route: PageRoute) -> dict:
        return {
            'id': route.id,
            'locale': route.locale.locale_code,
            'path': route.path,
            'routeType': route.route_type,
            'status': route.status,
            'title': route.page_title,
            'metaDescription': route.meta_description,
            'canonicalUrl': route.canonical_url,
            'ogTitle': route.og_title,
            'ogDescription': route.og_description,
            'ogImageUrl': route.og_image_url,
            'robots': route.robots,
            'publishedAt': isoformat(route.published_at),
        }

    def serialize_category(self, category) -> dict | None:
        if not category:
            return None

        return {
            'id': category.id,
            'parentId': category.parent_id,
            'code': category.code,
            'name': category.name,
            'slug': category.slug,
            'description': category.description,
            'seo': category.seo_json,
        }
