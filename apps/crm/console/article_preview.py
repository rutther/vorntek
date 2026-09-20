"""Build explicitly configured private article previews; never activate a site."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from sitecore.models import Release, ReleaseBuild

from .article_delivery import ArticleDeliveryError, snapshot_cms_articles
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


def _build_private_article_preview(*, site) -> dict:
    root, public_origin = article_preview_target(site)
    # Validate the complete database snapshot before creating local directories.
    snapshot = snapshot_cms_articles(site=site, public_origin=public_origin)
    store = ArticleReleaseStore.initialize(root, site_code=site.code, preview=True)
    version = store.stage(snapshot)
    manifest = store.verify(version)
    return {
        'version': version,
        'sourceVersion': snapshot['releaseSha256'],
        'articleCount': len(snapshot['articles']),
        'files': sorted(manifest['files']),
        'scope': 'sitePublished',
        'missingTranslations': snapshot['missingTranslations'],
    }


def build_private_article_preview(*, site, actor='local-preview') -> dict:
    """Build and record a private preview without changing an active pointer."""

    started = timezone.now()
    try:
        result = _build_private_article_preview(site=site)
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
    release_key = f'article-preview:{site.pk}:{result["version"]}'
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
        return None
    manifest = release.snapshot_manifest
    if manifest.get('kind') != 'articlePreview' or manifest.get('version') != version:
        return None
    return release
