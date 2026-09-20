"""Immutable whole-site bundles composed from static source and CMS articles.

The active pointer selects a verified bundle inside this private store. It is not
itself a deployment or a web-server switch; a serving adapter must honor it as a
separate, explicitly tested layer.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
from urllib.parse import urlsplit

from .article_delivery import ArticleDeliveryError, content_digest
from .article_release_store import _no_links


_ID = re.compile(r'^[a-f0-9]{64}$')
_SITE_CODE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$')
_LOCALE = re.compile(r'^[a-z]{2}(?:-[A-Z]{2})?$')
_ALLOWED_SUFFIXES = frozenset({
    '.html', '.css', '.js', '.json', '.xml', '.txt', '.ico',
    '.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif', '.svg',
    '.woff', '.woff2',
})
_DYNAMIC_PREFIXES = ('/admin/', '/api/', '/console/', '/static/', '/healthz/')
_CSS_URL = re.compile(
    r'url\(\s*(?P<quote>["\']?)(?P<value>.*?)(?P=quote)\s*\)',
    re.IGNORECASE,
)
_MAX_FILES = 10_000
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        + '\n'
    ).encode('utf-8')


def _valid_public_name(name: object) -> bool:
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 600
        or '\\' in name
        or '%' in name
        or name.startswith('/')
    ):
        return False
    path = PurePosixPath(name)
    if str(path) != name or any(
        part in ('', '.', '..') or part.startswith('.') or len(part) > 200
        for part in path.parts
    ):
        return False
    return Path(name).suffix.lower() in _ALLOWED_SUFFIXES


def _article_owned(name: str) -> bool:
    if name == 'article-sitemap.xml':
        return True
    parts = list(PurePosixPath(name).parts)
    if parts and _LOCALE.fullmatch(parts[0]):
        parts.pop(0)
    return bool(parts and parts[0] == 'articles')


def _scan_base_source(root: Path) -> tuple[dict[str, bytes], str]:
    root = Path(root)
    if not root.is_absolute() or root == Path(root.anchor):
        raise ArticleDeliveryError('website_source_not_configured')
    _no_links(root)
    if not root.is_dir():
        raise ArticleDeliveryError('website_source_not_configured')
    files: dict[str, bytes] = {}
    folded: set[str] = set()
    total = 0
    for item in sorted(root.rglob('*'), key=lambda value: value.as_posix()):
        _no_links(item)
        if not item.is_file():
            continue
        name = item.relative_to(root).as_posix()
        if not _valid_public_name(name):
            raise ArticleDeliveryError('unsafe_website_source_path')
        folded_name = name.casefold()
        if folded_name in folded:
            raise ArticleDeliveryError('website_source_case_collision')
        folded.add(folded_name)
        if _article_owned(name):
            # The CMS input wholly owns its namespace. Skipping old source pages
            # makes withdrawal deterministic instead of retaining stale content.
            continue
        info = item.stat()
        if info.st_nlink != 1:
            raise ArticleDeliveryError('unsafe_website_source_link')
        size = info.st_size
        if size > _MAX_FILE_BYTES:
            raise ArticleDeliveryError('website_source_file_too_large')
        total += size
        if total > _MAX_TOTAL_BYTES or len(files) >= _MAX_FILES:
            raise ArticleDeliveryError('website_source_too_large')
        raw = item.read_bytes()
        if len(raw) != size:
            raise ArticleDeliveryError('website_source_changed')
        files[name] = raw
    if 'index.html' not in files:
        raise ArticleDeliveryError('website_index_missing')
    digest = content_digest({name: _sha(raw) for name, raw in sorted(files.items())})
    return files, digest


def _article_files(build_input: dict, *, site_code: str) -> tuple[dict[str, bytes], str]:
    if (
        not isinstance(build_input, dict)
        or build_input.get('schemaVersion') != 1
        or build_input.get('kind') != 'cmsArticleBuildInput'
        or not _ID.fullmatch(str(build_input.get('version') or ''))
    ):
        raise ArticleDeliveryError('article_build_input_invalid')
    manifest = build_input.get('manifest')
    documents = build_input.get('documents')
    if (
        not isinstance(manifest, dict)
        or manifest.get('preview') is not False
        or manifest.get('siteCode') != site_code
        or content_digest(manifest) != build_input['version']
        or not isinstance(manifest.get('files'), dict)
        or not isinstance(documents, dict)
        or set(documents) != set(manifest['files'])
    ):
        raise ArticleDeliveryError('article_build_input_invalid')
    files = {}
    total = 0
    for name, text in documents.items():
        if not _valid_public_name(name) or not _article_owned(name) or not isinstance(text, str):
            raise ArticleDeliveryError('article_build_input_invalid')
        raw = text.encode('utf-8')
        total += len(raw)
        if (
            len(raw) > _MAX_FILE_BYTES
            or total > _MAX_TOTAL_BYTES
            or len(files) >= _MAX_FILES
        ):
            raise ArticleDeliveryError('article_build_input_too_large')
        if _sha(raw) != manifest['files'].get(name):
            raise ArticleDeliveryError('article_build_input_changed')
        files[name] = raw
    return files, build_input['version']


class _References(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values: list[tuple[str, bool]] = []

    def handle_starttag(self, tag, attrs):
        normalized = {key.lower(): value for key, value in attrs}
        resource_link = tag.lower() == 'link' and bool(
            {'stylesheet', 'icon', 'preload', 'modulepreload'}
            & set((normalized.get('rel') or '').lower().split())
        )
        for key, value in attrs:
            key = key.lower()
            if key == 'src' and value:
                self.values.append((value, True))
            elif key == 'href' and value:
                self.values.append((value, resource_link))
            elif key == 'srcset' and value:
                for candidate in value.split(','):
                    url = candidate.strip().split(' ', 1)[0]
                    if url:
                        self.values.append((url, True))


def _static_target(value: str, *, source: str, resource: bool) -> str | None:
    if value.startswith('#'):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ArticleDeliveryError('website_reference_invalid') from None
    if parsed.scheme or parsed.netloc:
        if resource and parsed.scheme != 'data':
            raise ArticleDeliveryError('website_external_resource')
        return None
    if not parsed.path:
        return None
    if parsed.path.startswith('//') or '\\' in parsed.path or '%' in parsed.path:
        raise ArticleDeliveryError('website_reference_invalid')
    if parsed.path.startswith('/'):
        if any(parsed.path.startswith(prefix) for prefix in _DYNAMIC_PREFIXES):
            return None
        if parsed.path == '/':
            return 'index.html'
        path = parsed.path.lstrip('/')
    else:
        parts = PurePosixPath(source).parent.joinpath(parsed.path).parts
        if any(part in ('', '.', '..') for part in parts):
            raise ArticleDeliveryError('website_reference_invalid')
        path = PurePosixPath(*parts).as_posix()
    return path + 'index.html' if parsed.path.endswith('/') else path


def _validate_internal_references(files: dict[str, bytes]) -> None:
    names = set(files)
    for name, raw in files.items():
        if not name.endswith('.html'):
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ArticleDeliveryError('website_html_encoding_invalid') from None
        parser = _References()
        parser.feed(text)
        parser.close()
        for value, resource in parser.values:
            target = _static_target(value, source=name, resource=resource)
            if target is not None and target not in names:
                raise ArticleDeliveryError('website_internal_reference_missing')
    for name, raw in files.items():
        if not name.endswith('.css'):
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ArticleDeliveryError('website_css_encoding_invalid') from None
        if '\\' in text or re.search(r'@import\b', text, re.IGNORECASE):
            raise ArticleDeliveryError('website_css_reference_unsafe')
        for match in _CSS_URL.finditer(text):
            target = _static_target(
                match.group('value').strip(),
                source=name,
                resource=True,
            )
            if target is not None and target not in names:
                raise ArticleDeliveryError('website_internal_reference_missing')


def _validate_manifest(payload: object, *, site_code: str) -> dict:
    if (
        not isinstance(payload, dict)
        or payload.get('schemaVersion') != 1
        or payload.get('kind') != 'websiteRelease'
        or payload.get('siteCode') != site_code
        or not _ID.fullmatch(str(payload.get('baseSourceSha256') or ''))
        or not _ID.fullmatch(str(payload.get('articleVersion') or ''))
        or not isinstance(payload.get('files'), dict)
    ):
        raise ArticleDeliveryError('website_manifest_mismatch')
    if any(
        not _valid_public_name(name) or not _ID.fullmatch(str(digest or ''))
        for name, digest in payload['files'].items()
    ):
        raise ArticleDeliveryError('website_manifest_mismatch')
    folded = [name.casefold() for name in payload['files']]
    if len(folded) != len(set(folded)) or len(folded) > _MAX_FILES:
        raise ArticleDeliveryError('website_manifest_mismatch')
    return payload


class WebsiteReleaseStore:
    def __init__(self, root: Path, *, site_code: str):
        self.root = Path(root)
        self.site_code = site_code
        if not _SITE_CODE.fullmatch(str(site_code or '')):
            raise ArticleDeliveryError('invalid_site_code')
        if not self.root.is_absolute() or self.root == Path(self.root.anchor):
            raise ArticleDeliveryError('invalid_website_store_root')
        _no_links(self.root)
        marker = self.root / 'owner.json'
        try:
            owner = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ArticleDeliveryError('website_store_not_initialized') from None
        if owner != {
            'schemaVersion': 1,
            'kind': 'websiteReleaseStore',
            'siteCode': site_code,
        }:
            raise ArticleDeliveryError('website_store_owner_mismatch')

    @classmethod
    def initialize(cls, root: Path, *, site_code: str) -> 'WebsiteReleaseStore':
        root = Path(root)
        if not _SITE_CODE.fullmatch(str(site_code or '')):
            raise ArticleDeliveryError('invalid_site_code')
        if not root.is_absolute() or root == Path(root.anchor):
            raise ArticleDeliveryError('invalid_website_store_root')
        _no_links(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(root.iterdir()):
            return cls(root, site_code=site_code)
        marker = root / 'owner.json'
        with marker.open('xb') as handle:
            handle.write(_json({
                'schemaVersion': 1,
                'kind': 'websiteReleaseStore',
                'siteCode': site_code,
            }))
            handle.flush()
            os.fsync(handle.fileno())
        return cls(root, site_code=site_code)

    @contextmanager
    def _lock(self):
        _no_links(self.root)
        lock = self.root / 'compose.lock'
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ArticleDeliveryError('website_composition_in_progress') from None
        try:
            os.write(descriptor, str(os.getpid()).encode('ascii'))
            yield
        finally:
            os.close(descriptor)
            lock.unlink()

    def _version(self, version: str) -> Path:
        if not isinstance(version, str) or not _ID.fullmatch(version):
            raise ArticleDeliveryError('invalid_website_release_id')
        path = self.root / 'releases' / version
        _no_links(path)
        return path

    def stage(self, *, base_root: Path, article_input: dict) -> str:
        base_files, base_digest = _scan_base_source(base_root)
        article_files, article_version = _article_files(
            article_input,
            site_code=self.site_code,
        )
        files = {**base_files, **article_files}
        if (
            len(files) > _MAX_FILES
            or sum(len(raw) for raw in files.values()) > _MAX_TOTAL_BYTES
        ):
            raise ArticleDeliveryError('website_source_too_large')
        _validate_internal_references(files)
        payload = {
            'schemaVersion': 1,
            'kind': 'websiteRelease',
            'siteCode': self.site_code,
            'baseSourceSha256': base_digest,
            'articleVersion': article_version,
            'files': {name: _sha(raw) for name, raw in sorted(files.items())},
        }
        version = content_digest(payload)
        with self._lock():
            destination = self._version(version)
            if destination.exists():
                self.verify(version)
                return version
            stage = self.root / ('stage-' + secrets.token_hex(12))
            stage.mkdir(mode=0o700)
            try:
                for name, raw in files.items():
                    target = stage.joinpath(*PurePosixPath(name).parts)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with target.open('xb') as handle:
                        handle.write(raw)
                (stage / 'manifest.json').write_bytes(_json(payload))
                destination.parent.mkdir(mode=0o700, exist_ok=True)
                _no_links(destination.parent)
                os.replace(stage, destination)
            finally:
                if stage.exists():
                    for item in sorted(stage.rglob('*'), reverse=True):
                        if item.is_file():
                            item.unlink()
                        elif item.is_dir():
                            item.rmdir()
                    stage.rmdir()
            self.verify(version)
        return version

    def verify(self, version: str) -> dict:
        root = self._version(version)
        manifest_path = root / 'manifest.json'
        _no_links(manifest_path)
        try:
            payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ArticleDeliveryError('website_artifact_unavailable') from None
        if content_digest(payload) != version:
            raise ArticleDeliveryError('website_manifest_mismatch')
        payload = _validate_manifest(payload, site_code=self.site_code)
        actual = set()
        for item in root.rglob('*'):
            _no_links(item)
            if item.is_file():
                if item.stat().st_nlink != 1:
                    raise ArticleDeliveryError('unsafe_website_artifact_link')
                actual.add(item.relative_to(root).as_posix())
        if actual != set(payload['files']) | {'manifest.json'}:
            raise ArticleDeliveryError('website_file_set_mismatch')
        total = 0
        files = {}
        for name, expected in payload['files'].items():
            raw = root.joinpath(*PurePosixPath(name).parts).read_bytes()
            total += len(raw)
            if len(raw) > _MAX_FILE_BYTES or total > _MAX_TOTAL_BYTES or _sha(raw) != expected:
                raise ArticleDeliveryError('website_file_changed')
            files[name] = raw
        _validate_internal_references(files)
        return payload

    def current(self) -> str:
        pointer = self.root / 'active.json'
        _no_links(pointer)
        if not pointer.exists():
            return ''
        try:
            version = json.loads(pointer.read_text(encoding='utf-8'))['version']
        except (OSError, ValueError, KeyError, TypeError):
            raise ArticleDeliveryError('website_active_pointer_invalid') from None
        self._version(version)
        return version

    def activate(self, version: str, *, expected: str) -> dict:
        """Select a bundle with CAS; this does not change a web-server root."""

        with self._lock():
            current = self.current()
            if current != expected:
                raise ArticleDeliveryError('website_active_version_changed')
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
        manifest = self.verify(version)
        if name not in manifest['files'] or not _valid_public_name(name):
            raise ArticleDeliveryError('website_artifact_unavailable')
        raw = self._version(version).joinpath(*PurePosixPath(name).parts).read_bytes()
        if _sha(raw) != manifest['files'][name]:
            raise ArticleDeliveryError('website_file_changed')
        return raw
