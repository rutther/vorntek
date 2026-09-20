"""Build an exact, immutable whole-site candidate from a reviewed article preview."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from sitecore.models import Release, ReleaseBuild

from .article_build_input import read_article_build_input
from .article_delivery import ArticleDeliveryError, snapshot_cms_articles
from .article_release_store import ArticleReleaseStore
from .website_release_store import WebsiteReleaseStore


def _absolute_setting(name: str, code: str) -> Path:
    try:
        value = Path(getattr(settings, name, ''))
    except TypeError:
        raise ArticleDeliveryError(code) from None
    if not value.is_absolute() or value == Path(value.anchor):
        raise ArticleDeliveryError(code)
    return value


def article_live_store(site, *, initialize: bool) -> ArticleReleaseStore:
    root = _absolute_setting(
        'SITEOS_ARTICLE_RELEASE_ROOT', 'article_release_target_not_configured'
    ) / site.code
    if initialize:
        return ArticleReleaseStore.initialize(root, site_code=site.code, preview=False)
    return ArticleReleaseStore(root, site_code=site.code, preview=False)


def website_release_store(site, *, initialize: bool) -> WebsiteReleaseStore:
    root = _absolute_setting(
        'SITEOS_WEBSITE_RELEASE_ROOT', 'website_release_target_not_configured'
    ) / site.code
    if initialize:
        return WebsiteReleaseStore.initialize(root, site_code=site.code)
    return WebsiteReleaseStore(root, site_code=site.code)


def _reviewed_preview(*, site, record_id: int) -> Release:
    record = Release.objects.filter(pk=record_id, site=site, status='built').first()
    manifest = record.snapshot_manifest if record is not None else None
    if (
        record is None
        or not isinstance(manifest, dict)
        or manifest.get('kind') != 'articlePreview'
        or manifest.get('scope') != 'sitePublished'
        or not manifest.get('version')
        or not manifest.get('sourceVersion')
    ):
        raise ArticleDeliveryError('reviewed_article_preview_missing')
    return record


def _build_website_candidate(*, site, preview_record_id: int) -> dict:
    preview = _reviewed_preview(site=site, record_id=preview_record_id)
    reviewed = preview.snapshot_manifest
    origin = getattr(settings, 'SITEOS_ARTICLE_PUBLIC_ORIGIN', '')
    snapshot = snapshot_cms_articles(site=site, public_origin=origin)
    if snapshot['releaseSha256'] != reviewed['sourceVersion']:
        raise ArticleDeliveryError('content_changed_since_preview')
    if snapshot['missingTranslations']:
        raise ArticleDeliveryError('translations_not_ready')

    article_store = article_live_store(site, initialize=True)
    article_current = article_store.current()
    article_version = article_store.stage(snapshot)
    article_input = read_article_build_input(
        article_store,
        version=article_version,
        release_mode=True,
    )
    website_store = website_release_store(site, initialize=True)
    website_current = website_store.current()
    website_version = website_store.stage(
        base_root=_absolute_setting(
            'SITEOS_WEBSITE_SOURCE_ROOT', 'website_source_not_configured'
        ),
        article_input=article_input,
    )
    manifest = website_store.verify(website_version)
    if (
        article_store.current() != article_current
        or website_store.current() != website_current
    ):
        raise ArticleDeliveryError('candidate_build_changed_active_pointer')
    return {
        'version': website_version,
        'sourceVersion': snapshot['releaseSha256'],
        'articleVersion': article_version,
        'baseSourceVersion': manifest['baseSourceSha256'],
        'fileCount': len(manifest['files']),
        'scope': 'wholeSite',
        'previewRecordId': preview.pk,
    }


def build_website_candidate(*, site, preview_record_id: int, actor='local-candidate') -> dict:
    """Build and audit a whole-site candidate without selecting or deploying it."""

    started = timezone.now()
    try:
        result = _build_website_candidate(
            site=site,
            preview_record_id=preview_record_id,
        )
    except (ArticleDeliveryError, OSError) as exc:
        code = exc.code if isinstance(exc, ArticleDeliveryError) else 'website_storage_unavailable'
        with transaction.atomic():
            release = Release.objects.create(
                site=site,
                release_key=f'website-candidate-failed:{site.pk}:{uuid4().hex}',
                status='failed',
                created_by=actor,
                notes='整站候选构建失败，未选择、未部署。',
                snapshot_manifest={'kind': 'websiteCandidate', 'errorCode': code},
            )
            ReleaseBuild.objects.create(
                release=release,
                build_key='websiteCandidate',
                status='failed',
                log_excerpt=code,
                started_at=started,
                finished_at=timezone.now(),
                config_json={'kind': 'websiteCandidate'},
            )
        raise

    finished = timezone.now()
    key = (
        f'website-candidate:{site.pk}:{result["previewRecordId"]}:{result["version"]}'
    )
    with transaction.atomic():
        release, _created = Release.objects.get_or_create(
            release_key=key,
            defaults={
                'site': site,
                'status': 'built',
                'created_by': actor,
                'notes': '整站候选已固化，未选择、未部署。',
                'built_at': finished,
                'snapshot_manifest': {'kind': 'websiteCandidate', **result},
            },
        )
        manifest = release.snapshot_manifest if isinstance(release.snapshot_manifest, dict) else {}
        if (
            release.site_id != site.pk
            or release.status != 'built'
            or manifest.get('kind') != 'websiteCandidate'
            or manifest.get('version') != result['version']
            or manifest.get('previewRecordId') != preview_record_id
        ):
            raise ArticleDeliveryError('website_candidate_record_mismatch')
        ReleaseBuild.objects.get_or_create(
            release=release,
            build_key='websiteCandidate',
            defaults={
                'status': 'succeeded',
                'started_at': started,
                'finished_at': finished,
                'log_excerpt': 'Whole-site candidate verified; not selected or deployed.',
                'config_json': {
                    'kind': 'websiteCandidate',
                    'version': result['version'],
                },
            },
        )
    return {**result, 'recordId': release.pk}
