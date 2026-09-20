"""Audited whole-site candidate selection without deployment side effects."""
from __future__ import annotations

import re
from uuid import UUID

from django.db import transaction

from sitecore.models import Release, Site

from .article_delivery import ArticleDeliveryError
from .models import WebsiteReleaseSelection
from .website_candidate import website_release_store


_VERSION = re.compile(r'^[a-f0-9]{64}$')


def _text(value, *, code: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise ArticleDeliveryError(code)
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise ArticleDeliveryError(code)
    return normalized


def _token(value) -> UUID:
    try:
        token = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ArticleDeliveryError('website_selection_token_invalid') from None
    if token.version != 4:
        raise ArticleDeliveryError('website_selection_token_invalid')
    return token


def _version(value, *, allow_empty: bool, code: str) -> str:
    if allow_empty and value == '':
        return ''
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ArticleDeliveryError(code)
    return value


def website_candidate_for_selection(*, site, record_id: int) -> tuple[Release, dict]:
    if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id <= 0:
        raise ArticleDeliveryError('website_candidate_missing')
    record = Release.objects.filter(pk=record_id, site=site, status='built').first()
    manifest = record.snapshot_manifest if record is not None else None
    version = manifest.get('version') if isinstance(manifest, dict) else None
    if (
        record is None
        or not isinstance(manifest, dict)
        or manifest.get('kind') != 'websiteCandidate'
        or manifest.get('scope') != 'wholeSite'
        or not _VERSION.fullmatch(str(version or ''))
        or not _VERSION.fullmatch(str(manifest.get('articleVersion') or ''))
        or not _VERSION.fullmatch(str(manifest.get('baseSourceVersion') or ''))
        or not isinstance(manifest.get('fileCount'), int)
        or isinstance(manifest.get('fileCount'), bool)
        or manifest['fileCount'] <= 0
    ):
        raise ArticleDeliveryError('website_candidate_missing')
    return record, manifest


def current_website_selection(*, site) -> WebsiteReleaseSelection | None:
    return (
        WebsiteReleaseSelection.objects.select_related('release')
        .filter(site=site)
        .order_by('-id')
        .first()
    )


def _verified_candidate(*, site, record_id: int) -> tuple[Release, dict]:
    record, manifest = website_candidate_for_selection(site=site, record_id=record_id)
    store = website_release_store(site, initialize=False)
    before = store.current()
    verified = store.verify(manifest['version'])
    after = store.current()
    if before != after:
        raise ArticleDeliveryError('website_candidate_pointer_changed')
    if (
        verified.get('articleVersion') != manifest['articleVersion']
        or verified.get('baseSourceSha256') != manifest['baseSourceVersion']
        or len(verified.get('files') or {}) != manifest['fileCount']
    ):
        raise ArticleDeliveryError('website_candidate_record_mismatch')
    return record, manifest


def select_website_candidate(
    *,
    site,
    record_id: int,
    expected_version: str,
    request_token,
    actor: str,
    reason: str,
) -> dict:
    """Append one CAS selection receipt; never activate a store or deploy files."""

    expected = _version(
        expected_version,
        allow_empty=True,
        code='website_selection_expected_invalid',
    )
    token = _token(request_token)
    normalized_actor = _text(
        actor,
        code='website_selection_actor_invalid',
        minimum=1,
        maximum=150,
    )
    normalized_reason = _text(
        reason,
        code='website_selection_reason_invalid',
        minimum=8,
        maximum=500,
    )
    record, manifest = _verified_candidate(site=site, record_id=record_id)

    with transaction.atomic():
        Site.objects.select_for_update().get(pk=site.pk)
        replay = (
            WebsiteReleaseSelection.objects.select_related('release')
            .filter(request_token=token)
            .first()
        )
        if replay is not None:
            if (
                replay.site_id != site.pk
                or replay.release_id != record.pk
                or replay.version != manifest['version']
                or replay.previous_version != expected
                or replay.selected_by != normalized_actor
                or replay.reason != normalized_reason
            ):
                raise ArticleDeliveryError('website_selection_request_reused')
            return {
                'selectionId': replay.pk,
                'version': replay.version,
                'previous': replay.previous_version,
                'changed': False,
                'replayed': True,
            }

        current = (
            WebsiteReleaseSelection.objects.filter(site=site)
            .order_by('-id')
            .first()
        )
        current_version = current.version if current is not None else ''
        if current_version != expected:
            raise ArticleDeliveryError('website_selection_changed')
        if current_version == manifest['version']:
            raise ArticleDeliveryError('website_candidate_already_selected')

        selection = WebsiteReleaseSelection.objects.create(
            request_token=token,
            site=site,
            release=record,
            version=manifest['version'],
            previous_version=current_version,
            selected_by=normalized_actor,
            reason=normalized_reason,
        )
    return {
        'selectionId': selection.pk,
        'version': selection.version,
        'previous': selection.previous_version,
        'changed': True,
        'replayed': False,
    }
