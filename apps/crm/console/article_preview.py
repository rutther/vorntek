"""Build explicitly configured private article previews; never activate a site."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from sitecore.models import Release, ReleaseBuild

from .article_delivery import ArticleDeliveryError, ArticleLocale, ArticleVersion, build_article_release, snapshot_cms_articles
from .article_release_store import ArticleReleaseStore


def article_preview_target(site) -> tuple[Path, str]:
    root_value = getattr(settings, 'SITEOS_ARTICLE_PREVIEW_ROOT', '')
    origin = getattr(settings, 'SITEOS_ARTICLE_PUBLIC_ORIGIN', '')
    if root_value in (None, '') or not isinstance(origin, str) or not origin:
        raise ArticleDeliveryError('preview_target_not_configured')
    try:
        root = Path(root_value)
    except TypeError:
        raise ArticleDeliveryError('preview_target_not_configured') from None
    if not root.is_absolute() or root == Path(root.anchor):
        raise ArticleDeliveryError('preview_target_not_configured')
    return root / site.code, origin


def article_preview_store(site, *, initialize: bool) -> ArticleReleaseStore:
    root, _origin = article_preview_target(site)
    if initialize:
        return ArticleReleaseStore.initialize(root, site_code=site.code, preview=True)
    return ArticleReleaseStore(root, site_code=site.code, preview=True)


def _build_private_article_preview(*, site, article=None) -> dict:
    root, public_origin = article_preview_target(site)
    # Validate the complete database snapshot before creating local directories.
    if article is None:
        snapshot = snapshot_cms_articles(site=site, public_origin=public_origin)
    else:
        locale = article.route.locale
        if not site.enabled or article.route.site_id != site.pk or locale.site_id != site.pk or not locale.enabled:
            raise ArticleDeliveryError('preview_article_scope_mismatch')
        if article.category_id and (article.category.site_id != site.pk or article.category.locale_id != locale.pk):
            raise ArticleDeliveryError('category_locale_mismatch')
        config = article.config_json if isinstance(article.config_json, dict) else {}
        locales = [ArticleLocale(code=row.locale_code, label=row.label, direction=row.direction,
            is_default=row.locale_code == site.default_locale)
            for row in site.locales.filter(enabled=True).order_by('sort_order','locale_code')]
        site_config = site.config_json if isinstance(site.config_json, dict) else {}
        delivery = site_config.get('articleDelivery', {})
        delivery = delivery if isinstance(delivery, dict) else {}
        # Private projection only: a draft never changes its persisted status.
        snapshot = build_article_release([ArticleVersion(
            content_key=config.get('contentKey') or f'previewArticle{article.pk}', locale=locale.locale_code,
            slug=article.slug, public_slug=config.get('publicSlug',''), source_path=article.route.path,
            title=article.title, markdown=article.body_markdown, excerpt=article.excerpt,
            description=article.route.meta_description, author=article.author_name,
            og_title=article.route.og_title, og_description=article.route.og_description,
            og_image_url=article.route.og_image_url, robots=article.route.robots,
            cover_url=article.cover_image_url, status='published', route_status='published',
        )], locales=locales, site_code=site.code, site_name=site.name, public_origin=public_origin,
            brand_logo_url=str(delivery.get('brandLogoUrl') or ''),
            base_route_mode=str(delivery.get('baseRouteMode') or 'localized'))
    store = ArticleReleaseStore.initialize(root, site_code=site.code, preview=True)
    version = store.stage(snapshot)
    manifest = store.verify(version)
    return {
        'version': version,
        'sourceVersion': snapshot['releaseSha256'],
        'articleCount': len(snapshot['articles']),
        'files': sorted(manifest['files']),
        'scope': 'singleArticle' if article is not None else 'sitePublished',
        'missingTranslations': [] if article is not None else snapshot['missingTranslations'],
    }


def build_private_article_preview(*, site, article=None, actor='local-preview') -> dict:
    """Build and record a private preview without changing an active pointer."""

    started = timezone.now()
    try:
        result = _build_private_article_preview(site=site, article=article)
    except (ArticleDeliveryError, OSError) as exc:
        code = exc.code if isinstance(exc, ArticleDeliveryError) else 'preview_storage_unavailable'
        with transaction.atomic():
            release = Release.objects.create(
                site=site,
                release_key=f'article-preview-failed:{site.pk}:{uuid4().hex}',
                status='failed',
                created_by=actor,
                notes='文章私有预览失败，未发布。',
                snapshot_manifest={'kind': 'articlePreview', 'errorCode': code},
            )
            ReleaseBuild.objects.create(
                release=release,
                build_key='articlePreview',
                status='failed',
                log_excerpt=code,
                started_at=started,
                finished_at=timezone.now(),
                config_json={'kind': 'articlePreview'},
            )
        raise

    finished = timezone.now()
    release_key = (f'article-preview-single:{site.pk}:{article.pk}:{result["version"]}'
        if article is not None else f'article-preview:{site.pk}:{result["version"]}')
    with transaction.atomic():
        release, _created = Release.objects.get_or_create(
            release_key=release_key,
            defaults={
                'site': site,
                'status': 'built',
                'created_by': actor,
                'notes': '文章私有预览已生成，未发布。',
                'built_at': finished,
                'snapshot_manifest': {'kind': 'articlePreview', **result},
            },
        )
        manifest = release.snapshot_manifest if isinstance(release.snapshot_manifest, dict) else {}
        if (
            release.site_id != site.pk
            or release.status != 'built'
            or manifest.get('kind') != 'articlePreview'
            or manifest.get('version') != result['version']
        ):
            raise ArticleDeliveryError('preview_record_scope_mismatch')
        ReleaseBuild.objects.get_or_create(
            release=release,
            build_key='articlePreview',
            defaults={
                'status': 'succeeded',
                'started_at': started,
                'finished_at': finished,
                'log_excerpt': 'Private article artifact verified; not activated.',
                'config_json': {'kind': 'articlePreview', 'version': result['version']},
            },
        )
    return {**result, 'recordId': release.pk}


def reviewed_article_preview(*, site, version: str) -> Release | None:
    release = Release.objects.filter(
        site=site,
        release_key=f'article-preview:{site.pk}:{version}',
        status='built',
    ).first()
    if release is None or not isinstance(release.snapshot_manifest, dict):
        release = Release.objects.filter(site=site, status='built',
            snapshot_manifest__kind='articlePreview', snapshot_manifest__scope='singleArticle',
            snapshot_manifest__version=version).order_by('-pk').first()
        if release is None or not isinstance(release.snapshot_manifest, dict):
            return None
    manifest = release.snapshot_manifest
    if manifest.get('kind') != 'articlePreview' or manifest.get('version') != version:
        return None
    return release
