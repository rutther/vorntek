"""Network-silent rendering of a frozen article artifact on the admin origin."""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

from .article_delivery import ArticleDeliveryError
from .article_release_store import ArticleReleaseStore, _no_links


STYLES = frozenset({'styles.css'})
IMAGE_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.avif': 'image/avif',
}
_BLOCKED_CONTAINERS = frozenset({'script', 'style', 'iframe', 'object', 'embed', 'form'})
_ALLOWED_TAGS = frozenset({
    'html', 'head', 'meta', 'title', 'link', 'body', 'a', 'header', 'strong',
    'nav', 'details', 'summary', 'span', 'main', 'p', 'section', 'h1', 'h2',
    'h3', 'h4', 'h5', 'h6', 'div', 'article', 'img', 'footer', 'table',
    'thead', 'tbody', 'tr', 'th', 'td', 'ul', 'ol', 'li', 'blockquote', 'pre',
    'code', 'hr', 'br',
})
_GLOBAL_ATTRIBUTES = frozenset({'class', 'id', 'lang', 'dir', 'role', 'title', 'tabindex'})
_TAG_ATTRIBUTES = {
    'meta': frozenset({'charset', 'name', 'content', 'property'}),
    'link': frozenset({'rel', 'href'}),
    'a': frozenset({'href', 'hreflang'}),
    'img': frozenset({'src', 'alt', 'loading', 'decoding', 'width', 'height'}),
    'th': frozenset({'scope', 'colspan', 'rowspan'}),
    'td': frozenset({'colspan', 'rowspan'}),
}
_UNSAFE_CSS = re.compile(
    rb'@import|url\s*\(|expression\s*\(|javascript\s*:|https?\s*:|//',
    re.IGNORECASE,
)


def _safe_base(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        raise ArticleDeliveryError('preview_base_invalid') from None
    if (
        not isinstance(value, str)
        or not value.startswith('/')
        or not value.endswith('/')
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or '\\' in value
        or '..' in PurePosixPath(parsed.path).parts
    ):
        raise ArticleDeliveryError('preview_base_invalid')
    return value


def _resource_path(raw: str) -> str | None:
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    path = parsed.path.lstrip('/')
    if (
        parsed.scheme
        or parsed.netloc
        or not raw.startswith('/')
        or raw.startswith('//')
        or '\\' in path
        or '%' in path
        or any(part in ('', '.', '..') for part in PurePosixPath(path).parts)
    ):
        return None
    if path in STYLES:
        return path
    suffix = Path(path).suffix.lower()
    if path.startswith('assets/') and suffix in IMAGE_TYPES:
        return path
    return None


def _allowed_attribute(tag: str, key: str) -> bool:
    return (
        key in _GLOBAL_ATTRIBUTES
        or key in _TAG_ATTRIBUTES.get(tag, frozenset())
        or key.startswith('aria-')
        or key.startswith('data-')
    ) and not key.startswith('on')


class PreviewDocument(HTMLParser):
    def __init__(self, *, base: str, files: dict[str, str]):
        super().__init__(convert_charrefs=False)
        self.base = _safe_base(base)
        self.files = files
        self.output: list[str] = []
        self.resources: set[str] = set()
        self.blocked_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _BLOCKED_CONTAINERS:
            self.blocked_depth += 1
            return
        if self.blocked_depth or tag == 'base' or tag not in _ALLOWED_TAGS:
            return
        values = {
            key.lower(): value
            for key, value in attrs
            if _allowed_attribute(tag, key.lower())
        }
        if tag == 'meta' and any(key.lower() == 'http-equiv' for key, _value in attrs):
            return
        if tag == 'link':
            if values.get('rel', '').lower() != 'stylesheet':
                return
            resource = _resource_path(values.get('href') or '')
            if resource not in STYLES:
                return
            self.resources.add(resource)
            values['href'] = self.base + resource
        elif tag == 'img':
            resource = _resource_path(values.get('src') or '')
            if resource is None or resource in STYLES:
                values.pop('src', None)
            else:
                self.resources.add(resource)
                values['src'] = self.base + resource
        elif tag == 'a':
            raw = values.get('href') or ''
            if raw.startswith('#') and '/' not in raw:
                values['href'] = raw
            else:
                try:
                    parsed = urlsplit(raw)
                except ValueError:
                    parsed = urlsplit('invalid:')
                path = parsed.path.lstrip('/')
                candidate = path + 'index.html' if parsed.path.endswith('/') else path
                if (
                    not parsed.scheme
                    and not parsed.netloc
                    and raw.startswith('/')
                    and not raw.startswith('//')
                    and candidate in self.files
                ):
                    values['href'] = (
                        self.base
                        + candidate
                        + (('?' + parsed.query) if parsed.query else '')
                        + (('#' + parsed.fragment) if parsed.fragment else '')
                    )
                else:
                    values['href'] = '#'
                    values['aria-disabled'] = 'true'
                    values['title'] = '此操作不属于文章私有预览'
        rendered_attributes = ''.join(
            ' ' + key + ('' if value is None else '="' + escape(value, quote=True) + '"')
            for key, value in values.items()
        )
        self.output.append(f'<{tag}{rendered_attributes}>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _BLOCKED_CONTAINERS:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return
        if not self.blocked_depth and tag in _ALLOWED_TAGS:
            self.output.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.blocked_depth:
            self.output.append(data)

    def handle_entityref(self, name):
        if not self.blocked_depth:
            self.output.append('&' + name + ';')

    def handle_charref(self, name):
        if not self.blocked_depth:
            self.output.append('&#' + name + ';')

    def handle_decl(self, decl):
        if not self.blocked_depth and decl.lower() == 'doctype html':
            self.output.append('<!doctype html>')


def _asset_file(root: Path, artifact: str) -> Path | None:
    if _resource_path('/' + artifact) != artifact:
        return None
    _no_links(root)
    file = root.joinpath(*PurePosixPath(artifact).parts)
    _no_links(file)
    try:
        resolved_root = root.resolve(strict=True)
        resolved_file = file.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not resolved_file.is_relative_to(resolved_root) or not resolved_file.is_file():
        return None
    return resolved_file


def read_private_preview(
    *,
    version: str,
    artifact: str,
    base: str,
    store: ArticleReleaseStore,
    asset_root: Path,
) -> tuple[bytes, str] | None:
    if not store.preview:
        raise ArticleDeliveryError('preview_store_scope_mismatch')
    base = _safe_base(base)
    manifest = store.verify(version)
    if artifact == 'article-sitemap.xml' and artifact in manifest['files']:
        raw = store.read_version_file(version, artifact)
        return raw, 'application/xml; charset=utf-8'
    if artifact in manifest['files']:
        raw = store.read_version_file(version, artifact)
        if not artifact.endswith('.html'):
            return None
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ArticleDeliveryError('artifact_encoding_invalid') from None
        parser = PreviewDocument(base=base, files=manifest['files'])
        parser.feed(text)
        parser.close()
        store.verify(version)
        return ''.join(parser.output).encode('utf-8'), 'text/html; charset=utf-8'

    resources = set()
    for name in manifest['files']:
        if not name.endswith('.html'):
            continue
        parser = PreviewDocument(base=base, files=manifest['files'])
        parser.feed(store.read_version_file(version, name).decode('utf-8'))
        parser.close()
        resources.update(parser.resources)
    if artifact not in resources:
        return None
    root = Path(asset_root)
    if not root.is_absolute() or root == Path(root.anchor):
        raise ArticleDeliveryError('preview_assets_not_configured')
    file = _asset_file(root, artifact)
    if file is None:
        return None
    maximum = 2 * 1024 * 1024 if artifact in STYLES else 25 * 1024 * 1024
    if file.stat().st_size > maximum:
        raise ArticleDeliveryError('preview_asset_too_large')
    raw = file.read_bytes()
    if artifact in STYLES:
        # Refuse CSS escapes rather than trying to duplicate a browser's tokenizer;
        # an escaped import/url/protocol must not bypass the network-silent contract.
        if b'\\' in raw or _UNSAFE_CSS.search(raw):
            raise ArticleDeliveryError('preview_stylesheet_not_network_silent')
        mime = 'text/css; charset=utf-8'
    else:
        mime = IMAGE_TYPES[file.suffix.lower()]
    store.verify(version)
    return raw, mime
