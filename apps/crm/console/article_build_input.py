"""Read one pinned active article artifact for whole-site composition.

The caller must compose this complete file set, not overlay it onto an older
article directory. An absent article is a withdrawal, not stale-content fallback.
"""
from __future__ import annotations

from .article_delivery import ArticleDeliveryError
from .article_release_store import ArticleReleaseStore


def read_article_build_input(
    store: ArticleReleaseStore, *, version: str, release_mode: bool
) -> dict:
    """Read one explicit immutable version without selecting or activating it."""

    if release_mode and store.preview:
        raise ArticleDeliveryError('preview_artifact_not_publishable')
    manifest = store.verify(version)
    documents = {}
    for name in manifest['files']:
        raw = store.read_version_file(version, name)
        try:
            documents[name] = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ArticleDeliveryError('artifact_encoding_invalid') from None
    store.verify(version)
    return {
        'schemaVersion': 1,
        'kind': 'cmsArticleBuildInput',
        'version': version,
        'manifest': manifest,
        'documents': documents,
    }


def read_active_article_build_input(
    store: ArticleReleaseStore, *, expected_version: str, release_mode: bool
) -> dict:
    if not expected_version or store.current() != expected_version:
        raise ArticleDeliveryError('active_version_changed')
    result = read_article_build_input(
        store,
        version=expected_version,
        release_mode=release_mode,
    )
    # Activation may proceed concurrently; never return a silently mixed version.
    store.verify(expected_version)
    if store.current() != expected_version:
        raise ArticleDeliveryError('active_version_changed')
    return result
