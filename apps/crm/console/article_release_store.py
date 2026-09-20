"""Private, immutable article artifacts with compare-and-swap activation.

The store has no production path default and is deliberately separate from the
website tree, uploads and database. Only a verified active version is readable;
missing content is a withdrawal, never a reason to fall back to an older tree.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat

try:  # POSIX production path; Windows uses the local-test fallback below.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    _fcntl = None

from .article_delivery import ArticleDeliveryError, content_digest
from .article_rendering import render_article_documents, render_article_sitemap


_ID = re.compile(r'^[a-f0-9]{64}$')
_LOCALE = re.compile(r'^[a-z]{2}(?:-[A-Z]{2})?$')
_SLUG = re.compile(r'^[a-z0-9][a-z0-9_-]{0,179}$')
_SITE_CODE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$')


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        + '\n'
    ).encode('utf-8')


def _no_links(path: Path) -> None:
    """Reject symlink/reparse traversal through every existing ancestor."""

    for item in (path, *path.parents):
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, 'st_file_attributes', 0)
            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
        ):
            raise ArticleDeliveryError('unsafe_store_link')


@contextmanager
def _process_lock(
    path: Path,
    *,
    busy_code: str,
    unavailable_code: str,
):
    """Hold a crash-released POSIX lock or a Windows test-only lock file."""

    path = Path(path)
    _no_links(path)
    if _fcntl is not None:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as error:
            raise ArticleDeliveryError(unavailable_code) from error
        locked = False
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ArticleDeliveryError('unsafe_store_link')
            try:
                _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                raise ArticleDeliveryError(busy_code) from None
            os.ftruncate(descriptor, 0)
            os.write(descriptor, str(os.getpid()).encode('ascii'))
            yield
        finally:
            try:
                if locked:
                    _fcntl.flock(descriptor, _fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        return

    # Windows cannot serve the POSIX symlink tree in production. Retain an
    # exclusive-file fallback for local memory-pointer and artifact tests.
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ArticleDeliveryError(busy_code) from None
    except OSError as error:
        raise ArticleDeliveryError(unavailable_code) from error
    try:
        os.write(descriptor, str(os.getpid()).encode('ascii'))
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def _valid_artifact_name(name: object) -> bool:
    if not isinstance(name, str) or '\\' in name or name.startswith('/'):
        return False
    if name == 'article-sitemap.xml':
        return True
    path = PurePosixPath(name)
    if str(path) != name or any(part in ('', '.', '..') for part in path.parts):
        return False
    parts = list(path.parts)
    if parts and _LOCALE.fullmatch(parts[0]):
        parts.pop(0)
    if not parts or parts[0] != 'articles' or parts[-1] != 'index.html':
        return False
    middle = parts[1:-1]
    return not middle or (len(middle) == 1 and bool(_SLUG.fullmatch(middle[0])))


def _valid_article_route(route: object) -> bool:
    if (
        not isinstance(route, str)
        or not route.startswith('/')
        or not route.endswith('/')
        or '//' in route
        or any(char in route for char in ('\\', '?', '#', '%'))
    ):
        return False
    name = route[1:] + 'index.html'
    if not _valid_artifact_name(name):
        return False
    parts = list(PurePosixPath(name).parts)
    if parts and _LOCALE.fullmatch(parts[0]):
        parts.pop(0)
    return len(parts) == 3 and parts[0] == 'articles'


def _validate_manifest(payload: object, *, site_code: str, preview: bool) -> dict:
    if not isinstance(payload, dict):
        raise ArticleDeliveryError('artifact_manifest_mismatch')
    if (
        payload.get('schemaVersion') != 1
        or payload.get('siteCode') != site_code
        or payload.get('preview') is not preview
        or not _ID.fullmatch(str(payload.get('sourceSha256') or ''))
    ):
        raise ArticleDeliveryError('artifact_manifest_mismatch')
    files = payload.get('files')
    if (
        not isinstance(files, dict)
        or any(
            not _valid_artifact_name(name) or not _ID.fullmatch(str(digest or ''))
            for name, digest in files.items()
        )
    ):
        raise ArticleDeliveryError('unsafe_artifact_path')
    redirects = payload.get('redirects')
    if not isinstance(redirects, dict) or any(
        not _valid_article_route(source) or not _valid_article_route(target)
        for source, target in redirects.items()
    ):
        raise ArticleDeliveryError('artifact_manifest_mismatch')
    return payload


class ArticleReleaseStore:
    def __init__(self, root: Path, *, site_code: str, preview: bool):
        self.root = Path(root)
        self.site_code = site_code
        self.preview = preview
        if not _SITE_CODE.fullmatch(str(site_code or '')):
            raise ArticleDeliveryError('invalid_site_code')
        if not self.root.is_absolute() or self.root == Path(self.root.anchor):
            raise ArticleDeliveryError('invalid_store_root')
        _no_links(self.root)
        if not self.root.is_dir():
            raise ArticleDeliveryError('store_not_initialized')
        marker = self.root / 'owner.json'
        _no_links(marker)
        try:
            owner = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ArticleDeliveryError('store_not_initialized') from None
        if owner != {'schemaVersion': 1, 'siteCode': site_code, 'preview': preview}:
            raise ArticleDeliveryError('store_owner_mismatch')

    @classmethod
    def initialize(
        cls, root: Path, *, site_code: str, preview: bool
    ) -> 'ArticleReleaseStore':
        root = Path(root)
        if not _SITE_CODE.fullmatch(str(site_code or '')):
            raise ArticleDeliveryError('invalid_site_code')
        if not root.is_absolute() or root == Path(root.anchor):
            raise ArticleDeliveryError('invalid_store_root')
        _no_links(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(root.iterdir()):
            # Reuse only an already-owned store. Never adopt an arbitrary folder.
            return cls(root, site_code=site_code, preview=preview)
        marker = root / 'owner.json'
        with marker.open('xb') as handle:
            handle.write(
                _json({'schemaVersion': 1, 'siteCode': site_code, 'preview': preview})
            )
            handle.flush()
            os.fsync(handle.fileno())
        return cls(root, site_code=site_code, preview=preview)

    @contextmanager
    def _lock(self):
        _no_links(self.root)
        with _process_lock(
            self.root / 'publish.lock',
            busy_code='publication_in_progress',
            unavailable_code='publication_lock_unavailable',
        ):
            yield

    def _version(self, version: str) -> Path:
        if not isinstance(version, str) or not _ID.fullmatch(version):
            raise ArticleDeliveryError('invalid_release_id')
        folder = self.root / 'releases' / version
        _no_links(folder)
        return folder

    def stage(self, snapshot: dict) -> str:
        if snapshot.get('siteCode') != self.site_code:
            raise ArticleDeliveryError('snapshot_site_mismatch')
        if not self.preview and snapshot.get('missingTranslations'):
            raise ArticleDeliveryError('translations_not_ready')
        rendered = render_article_documents(snapshot, preview=self.preview)
        rendered['article-sitemap.xml'] = render_article_sitemap(snapshot)
        documents = {
            name: text.encode('utf-8') for name, text in sorted(rendered.items())
        }
        if any(not _valid_artifact_name(name) for name in documents):
            raise ArticleDeliveryError('unsafe_artifact_path')
        payload = {
            'schemaVersion': 1,
            'siteCode': self.site_code,
            'preview': self.preview,
            'sourceSha256': snapshot['releaseSha256'],
            'files': {name: _sha(raw) for name, raw in documents.items()},
            'redirects': snapshot['redirects'],
        }
        version = content_digest(payload)
        with self._lock():
            destination = self._version(version)
            if destination.exists():
                self.verify(version)
                return version
            stage = self.root / ('stage-' + secrets.token_hex(12))
            stage.mkdir(mode=0o700)
            for name, raw in documents.items():
                target = stage.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with target.open('xb') as handle:
                    handle.write(raw)
            (stage / 'manifest.json').write_bytes(_json(payload))
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            _no_links(destination.parent)
            os.replace(stage, destination)
            self.verify(version)
        return version

    def verify(self, version: str) -> dict:
        root = self._version(version)
        manifest = root / 'manifest.json'
        _no_links(manifest)
        try:
            payload = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ArticleDeliveryError('artifact_unavailable') from None
        if content_digest(payload) != version:
            raise ArticleDeliveryError('artifact_manifest_mismatch')
        payload = _validate_manifest(
            payload, site_code=self.site_code, preview=self.preview
        )
        files = payload['files']
        actual = set()
        for file in root.rglob('*'):
            _no_links(file)
            if file.is_file():
                actual.add(file.relative_to(root).as_posix())
        if actual != set(files) | {'manifest.json'}:
            raise ArticleDeliveryError('artifact_file_set_mismatch')
        for name, expected in files.items():
            if _sha(root.joinpath(*PurePosixPath(name).parts).read_bytes()) != expected:
                raise ArticleDeliveryError('artifact_file_changed')
        return payload

    def current(self) -> str:
        pointer = self.root / 'active.json'
        _no_links(pointer)
        if not pointer.exists():
            return ''
        try:
            value = json.loads(pointer.read_text(encoding='utf-8'))['version']
        except (OSError, ValueError, KeyError, TypeError):
            raise ArticleDeliveryError('active_pointer_invalid') from None
        self._version(value)
        return value

    def activate(self, version: str, *, expected: str) -> dict:
        """Activate or roll back; a mandatory expected version rejects stale tabs."""

        with self._lock():
            current = self.current()
            if current != expected:
                raise ArticleDeliveryError('active_version_changed')
            self.verify(version)
            if current == version:
                return {'version': version, 'previous': current, 'changed': False}
            pointer = {'version': version, 'previous': current}
            temporary = self.root / ('active-' + secrets.token_hex(12) + '.tmp')
            with temporary.open('xb') as handle:
                handle.write(_json(pointer))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.root / 'active.json')
            return {**pointer, 'changed': True}

    def read_version_file(self, version: str, name: str) -> bytes:
        payload = self.verify(version)
        if name not in payload['files'] or not _valid_artifact_name(name):
            raise ArticleDeliveryError('artifact_unavailable')
        raw = self._version(version).joinpath(*PurePosixPath(name).parts).read_bytes()
        if _sha(raw) != payload['files'][name]:
            raise ArticleDeliveryError('artifact_file_changed')
        return raw

    def read(self, route: object) -> tuple[bytes, str] | None:
        """Read only the active snapshot; never search prior releases on a miss."""

        if (
            not isinstance(route, str)
            or not route.startswith('/')
            or (not route.endswith('/') and route != '/article-sitemap.xml')
            or '//' in route
        ):
            return None
        name = route[1:] if route == '/article-sitemap.xml' else route[1:] + 'index.html'
        if not _valid_artifact_name(name):
            return None
        version = self.current()
        if not version:
            return None
        payload = self.verify(version)
        if name not in payload['files']:
            return None
        raw = self._version(version).joinpath(*PurePosixPath(name).parts).read_bytes()
        if _sha(raw) != payload['files'][name]:
            raise ArticleDeliveryError('artifact_file_changed')
        return raw, payload['files'][name]
