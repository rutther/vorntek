"""Materialize verified website candidates and switch the Nginx root atomically.

This store is deliberately separate from :mod:`website_release_store`.  The
release store owns private build evidence; this store owns only immutable
public files and the one relative symlink consumed by Nginx.  Database policy
and deployment receipts are enforced by the deployment service, not here.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets

from .article_delivery import ArticleDeliveryError, content_digest
from .article_release_store import _no_links, _process_lock
from .website_release_store import (
    _MAX_FILE_BYTES,
    _MAX_FILES,
    _MAX_TOTAL_BYTES,
    _json,
    _sha,
    _valid_public_name,
    _validate_internal_references,
)


_VERSION = re.compile(r'^[a-f0-9]{64}$')
_OWNER = {'schemaVersion': 1, 'kind': 'websiteServingStore'}
_BUNDLED_TARGET = 'releases/bundled'
_DEPLOYMENT_MANIFEST = '.newcrown-deployment.json'


def _safe_root(root: Path) -> Path:
    root = Path(root)
    if not root.is_absolute() or root == Path(root.anchor):
        raise ArticleDeliveryError('website_serving_root_invalid')
    _no_links(root)
    return root


class WebsiteServingStore:
    """An owner-marked serving tree with immutable releases and an atomic pointer."""

    def __init__(self, root: Path):
        self.root = _safe_root(root)
        try:
            owner = json.loads((self.root / 'owner.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ArticleDeliveryError('website_serving_store_not_initialized') from None
        if owner != _OWNER:
            raise ArticleDeliveryError('website_serving_store_owner_mismatch')
        releases = self.root / 'releases'
        _no_links(releases)
        if not releases.is_dir():
            raise ArticleDeliveryError('website_serving_store_not_initialized')

    @classmethod
    def initialize(cls, root: Path) -> 'WebsiteServingStore':
        """Initialize an empty test/installation store without selecting content."""

        root = _safe_root(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(root.iterdir()):
            return cls(root)
        with (root / 'owner.json').open('xb') as handle:
            handle.write(_json(_OWNER))
            handle.flush()
            os.fsync(handle.fileno())
        (root / 'releases').mkdir(mode=0o700)
        return cls(root)

    @contextmanager
    def _lock(self):
        _no_links(self.root)
        with _process_lock(
            self.root / 'deploy.lock',
            busy_code='website_deployment_in_progress',
            unavailable_code='website_deployment_lock_unavailable',
        ):
            yield

    @staticmethod
    def _release_target(version: str) -> str:
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ArticleDeliveryError('website_deployment_version_invalid')
        return f'releases/{version}'

    def _release_root(self, version: str) -> Path:
        path = self.root.joinpath(*PurePosixPath(self._release_target(version)).parts)
        _no_links(path)
        return path

    def current(self) -> str:
        pointer = self.root / 'current'
        try:
            if not pointer.is_symlink():
                if pointer.exists():
                    raise ArticleDeliveryError('website_serving_pointer_invalid')
                return ''
            target = os.readlink(pointer)
        except OSError:
            raise ArticleDeliveryError('website_serving_pointer_invalid') from None
        if target == _BUNDLED_TARGET:
            bundled = self.root / 'releases' / 'bundled'
            _no_links(bundled)
            if not bundled.is_dir():
                raise ArticleDeliveryError('website_serving_pointer_invalid')
            return ''
        prefix = 'releases/'
        if not target.startswith(prefix) or '/' in target[len(prefix):]:
            raise ArticleDeliveryError('website_serving_pointer_invalid')
        version = target[len(prefix):]
        self._release_root(version)
        return version

    def _replace_current(self, version: str) -> None:
        """Install the relative Nginx pointer with one same-filesystem rename."""

        target = self._release_target(version)
        temporary = self.root / ('current-' + secrets.token_hex(12) + '.tmp')
        try:
            os.symlink(target, temporary, target_is_directory=True)
            os.replace(temporary, self.root / 'current')
        finally:
            temporary.unlink(missing_ok=True)

    def verify(self, version: str, *, expected_manifest: dict | None = None) -> dict:
        root = self._release_root(version)
        manifest_path = root / _DEPLOYMENT_MANIFEST
        _no_links(manifest_path)
        try:
            payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ArticleDeliveryError('website_deployment_artifact_unavailable') from None
        files = payload.get('files') if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get('schemaVersion') != 1
            or payload.get('kind') != 'websiteDeploymentArtifact'
            or payload.get('version') != version
            or payload.get('sourceManifestSha256') != version
            or not isinstance(files, dict)
            or len(files) > _MAX_FILES
            or any(
                not _valid_public_name(name) or not _VERSION.fullmatch(str(digest or ''))
                for name, digest in files.items()
            )
        ):
            raise ArticleDeliveryError('website_deployment_manifest_mismatch')
        if content_digest({
            'schemaVersion': 1,
            'kind': 'websiteRelease',
            'siteCode': payload.get('siteCode'),
            'baseSourceSha256': payload.get('baseSourceSha256'),
            'articleVersion': payload.get('articleVersion'),
            'files': files,
        }) != version:
            raise ArticleDeliveryError('website_deployment_manifest_mismatch')
        if expected_manifest is not None and any(
            payload.get(key) != expected_manifest.get(key)
            for key in ('siteCode', 'baseSourceSha256', 'articleVersion', 'files')
        ):
            raise ArticleDeliveryError('website_deployment_source_mismatch')

        actual: set[str] = set()
        total = 0
        contents: dict[str, bytes] = {}
        for item in root.rglob('*'):
            _no_links(item)
            if not item.is_file():
                continue
            if item.stat().st_nlink != 1:
                raise ArticleDeliveryError('unsafe_website_deployment_link')
            name = item.relative_to(root).as_posix()
            actual.add(name)
            if name == _DEPLOYMENT_MANIFEST:
                continue
            if name not in files:
                raise ArticleDeliveryError('website_deployment_file_set_mismatch')
            raw = item.read_bytes()
            total += len(raw)
            if (
                len(raw) > _MAX_FILE_BYTES
                or total > _MAX_TOTAL_BYTES
                or _sha(raw) != files[name]
            ):
                raise ArticleDeliveryError('website_deployment_file_changed')
            contents[name] = raw
        if actual != set(files) | {_DEPLOYMENT_MANIFEST}:
            raise ArticleDeliveryError('website_deployment_file_set_mismatch')
        _validate_internal_references(contents)
        return payload

    def deploy(self, source_store, version: str, *, expected: str) -> dict:
        """Copy one verified candidate and atomically replace ``current``.

        ``expected`` is the version recorded by the deployment ledger.  The
        empty value represents the image-bundled installation baseline.
        """

        if expected != '':
            self._release_target(expected)
        self._release_target(version)
        with self._lock():
            current = self.current()
            if current != expected:
                raise ArticleDeliveryError('website_deployed_version_changed')
            source_manifest = source_store.verify(version)
            if content_digest(source_manifest) != version:
                raise ArticleDeliveryError('website_deployment_source_mismatch')
            destination = self._release_root(version)
            if destination.exists():
                self.verify(version, expected_manifest=source_manifest)
            else:
                stage = self.root / ('stage-' + secrets.token_hex(12))
                stage.mkdir(mode=0o700)
                try:
                    for name, digest in source_manifest['files'].items():
                        raw = source_store.read_version_file(version, name)
                        if _sha(raw) != digest:
                            raise ArticleDeliveryError('website_deployment_source_changed')
                        output = stage.joinpath(*PurePosixPath(name).parts)
                        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        with output.open('xb') as handle:
                            handle.write(raw)
                    deployment_manifest = {
                        'schemaVersion': 1,
                        'kind': 'websiteDeploymentArtifact',
                        'version': version,
                        'sourceManifestSha256': version,
                        'siteCode': source_manifest['siteCode'],
                        'baseSourceSha256': source_manifest['baseSourceSha256'],
                        'articleVersion': source_manifest['articleVersion'],
                        'files': source_manifest['files'],
                    }
                    (stage / _DEPLOYMENT_MANIFEST).write_bytes(_json(deployment_manifest))
                    os.replace(stage, destination)
                finally:
                    if stage.exists():
                        for item in sorted(stage.rglob('*'), reverse=True):
                            if item.is_file():
                                item.unlink()
                            elif item.is_dir():
                                item.rmdir()
                        stage.rmdir()
                self.verify(version, expected_manifest=source_manifest)

            # A second source verification closes the copy/switch race.
            if source_store.verify(version) != source_manifest:
                raise ArticleDeliveryError('website_deployment_source_changed')
            self._replace_current(version)
            if self.current() != version:
                raise ArticleDeliveryError('website_serving_pointer_invalid')
            return {
                'version': version,
                'previous': current,
                'changed': current != version,
            }
