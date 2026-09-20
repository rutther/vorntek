"""Deterministic, product-neutral CMS article release contract.

This module reads no request data, writes no files and sends no network
requests.  It freezes only explicitly published CMS rows into a digest-bound
payload that a separate renderer and activation layer can verify.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


_LOCALE = re.compile(r'^[a-z]{2}(?:-[A-Z]{2})?$')
_SLUG = re.compile(r'^[a-z0-9][a-z0-9_-]{0,179}$')
_KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$')


class ArticleDeliveryError(ValueError):
    """Stable diagnostic code without content, paths or configuration."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ArticleLocale:
    code: str
    label: str
    direction: str = 'ltr'
    is_default: bool = False


@dataclass(frozen=True)
class ArticleVersion:
    content_key: str
    locale: str
    slug: str
    title: str
    markdown: str
    excerpt: str = ''
    description: str = ''
    og_title: str = ''
    og_description: str = ''
    og_image_url: str = ''
    robots: str = 'index,follow,max-image-preview:large'
    author: str = ''
    cover_url: str = ''
    category_key: str = ''
    category_label: str = ''
    category_locale: str = ''
    source_path: str = ''
    public_slug: str = ''
    status: str = 'draft'
    route_status: str = 'draft'
    enabled: bool = True


def content_digest(value: dict | list) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _validate_locales(locales: list[ArticleLocale]) -> tuple[dict[str, ArticleLocale], str]:
    if not locales:
        raise ArticleDeliveryError('locales_required')
    result = {}
    default_codes = []
    for locale in locales:
        if (
            not isinstance(locale.code, str)
            or not _LOCALE.fullmatch(locale.code)
            or locale.code in result
        ):
            raise ArticleDeliveryError('invalid_locale')
        if locale.direction not in {'ltr', 'rtl'}:
            raise ArticleDeliveryError('invalid_locale_direction')
        label = str(locale.label or '').strip()
        if not label or len(label) > 100:
            raise ArticleDeliveryError('invalid_locale_label')
        result[locale.code] = ArticleLocale(
            code=locale.code,
            label=label,
            direction=locale.direction,
            is_default=bool(locale.is_default),
        )
        if locale.is_default:
            default_codes.append(locale.code)
    if len(default_codes) != 1:
        raise ArticleDeliveryError('one_default_locale_required')
    return result, default_codes[0]


def article_path(locale: str, slug: str, *, default_locale: str) -> str:
    if not _LOCALE.fullmatch(str(locale or '')):
        raise ArticleDeliveryError('invalid_locale')
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise ArticleDeliveryError('invalid_article_slug')
    prefix = '' if locale == default_locale else '/' + locale
    return f'{prefix}/articles/{slug}/'


def _public_origin(value: str) -> str:
    try:
        url = urlsplit(value)
        valid = (
            url.scheme == 'https'
            and url.hostname
            and '.' in url.hostname
            and url.hostname not in {'localhost', '127.0.0.1', '0.0.0.0'}
            and not url.hostname.endswith(('.localhost', '.local'))
            and url.username is None
            and url.password is None
            and url.port in (None, 443)
            and url.path in ('', '/')
            and not url.query
            and not url.fragment
        )
    except (ValueError, TypeError):
        valid = False
    if valid:
        try:
            valid = ipaddress.ip_address(url.hostname).is_global
        except ValueError:
            pass
    if not valid:
        raise ArticleDeliveryError('invalid_public_origin')
    return value.rstrip('/')


def _public_asset_url(value: str) -> str:
    if not value:
        return ''
    if any(char in value for char in ('\\', '<', '>', '"', "'")):
        raise ArticleDeliveryError('invalid_asset_url')
    try:
        url = urlsplit(value)
        if url.query or url.fragment or url.username or url.password:
            raise ArticleDeliveryError('invalid_asset_url')
        if value.startswith('/') and not value.startswith('//'):
            if '%' in value or '..' in url.path.split('/'):
                raise ArticleDeliveryError('invalid_asset_url')
            return value
        if url.scheme == 'https' and url.hostname:
            return value
    except ValueError:
        pass
    raise ArticleDeliveryError('invalid_asset_url')


def build_article_release(
    versions: list[ArticleVersion],
    *,
    locales: list[ArticleLocale],
    site_code: str,
    site_name: str,
    public_origin: str,
    brand_logo_url: str = '',
    base_route_mode: str = 'localized',
) -> dict:
    """Freeze public versions and explicit language relationships.

    Missing translations remain explicit.  They are never replaced by another
    language.  An empty release is valid and represents withdrawal of every
    previously published article.
    """

    if not isinstance(site_code, str) or not _KEY.fullmatch(site_code):
        raise ArticleDeliveryError('invalid_site_code')
    clean_site_name = str(site_name or '').strip()
    if not clean_site_name or len(clean_site_name) > 200:
        raise ArticleDeliveryError('invalid_site_name')
    locale_map, default_locale = _validate_locales(locales)
    origin = _public_origin(public_origin)
    logo = _public_asset_url(brand_logo_url)
    if base_route_mode not in {'localized', 'shared'}:
        raise ArticleDeliveryError('invalid_base_route_mode')
    articles, identities, paths, redirects = [], set(), set(), {}
    groups: dict[str, dict[str, str]] = {}

    for item in versions:
        if item.status != 'published' or item.route_status != 'published' or not item.enabled:
            continue
        if item.locale not in locale_map:
            raise ArticleDeliveryError('unsupported_locale')
        if not _KEY.fullmatch(item.content_key or ''):
            raise ArticleDeliveryError('missing_content_identity')
        article_path(item.locale, item.slug, default_locale=default_locale)
        destination = article_path(
            item.locale,
            item.public_slug or item.slug,
            default_locale=default_locale,
        )
        identity = (item.content_key, item.locale)
        if identity in identities:
            raise ArticleDeliveryError('duplicate_content_locale')
        if destination in paths:
            raise ArticleDeliveryError('duplicate_public_path')
        if not item.title.strip() or not item.markdown.strip():
            raise ArticleDeliveryError('empty_published_content')
        if item.category_key and item.category_locale != item.locale:
            raise ArticleDeliveryError('category_locale_mismatch')
        identities.add(identity)
        paths.add(destination)
        groups.setdefault(item.content_key, {})[item.locale] = destination

        if item.source_path and item.source_path != destination:
            expected = f'/{item.locale}/articles/{item.slug}/'
            if item.source_path != expected:
                raise ArticleDeliveryError('unexpected_source_path')
            if item.source_path in redirects and redirects[item.source_path] != destination:
                raise ArticleDeliveryError('conflicting_redirect')
            redirects[item.source_path] = destination

        locale = locale_map[item.locale]
        document = {
            'contentKey': item.content_key,
            'language': item.locale,
            'direction': locale.direction,
            'path': destination,
            'canonical': origin + destination,
            'title': item.title,
            'excerpt': item.excerpt,
            'bodyMarkdown': item.markdown,
            'description': item.description or item.excerpt,
            'author': item.author,
            'coverUrl': _public_asset_url(item.cover_url),
            'ogTitle': item.og_title or item.title,
            'ogDescription': item.og_description or item.description or item.excerpt,
            'ogImageUrl': _public_asset_url(item.og_image_url or item.cover_url),
            'robots': item.robots or 'index,follow,max-image-preview:large',
            'category': (
                {'key': item.category_key, 'label': item.category_label}
                if item.category_key
                else None
            ),
        }
        document['contentSha256'] = content_digest(document)
        articles.append(document)

    if paths.intersection(redirects):
        raise ArticleDeliveryError('redirect_shadows_article')
    for article in articles:
        siblings = groups[article['contentKey']]
        article['alternates'] = {
            locale: origin + destination
            for locale, destination in sorted(siblings.items())
        }
        if default_locale in siblings:
            article['alternates']['x-default'] = origin + siblings[default_locale]

    payload = {
        'schemaVersion': 1,
        'siteCode': site_code,
        'siteName': clean_site_name,
        'brandLogoUrl': logo,
        'baseRouteMode': base_route_mode,
        'publicOrigin': origin,
        'defaultLocale': default_locale,
        'locales': [
            {
                'code': locale.code,
                'label': locale.label,
                'direction': locale.direction,
                'isDefault': locale.is_default,
            }
            for locale in locale_map.values()
        ],
        'articles': sorted(articles, key=lambda row: row['path']),
        'redirects': dict(sorted(redirects.items())),
        'missingTranslations': [
            {
                'contentKey': key,
                'languages': [code for code in locale_map if code not in siblings],
            }
            for key, siblings in sorted(groups.items())
            if set(siblings) != set(locale_map)
        ],
    }
    return {**payload, 'releaseSha256': content_digest(payload)}


def snapshot_cms_articles(*, site, public_origin: str) -> dict:
    """Read published rows for one site into the product-neutral contract."""

    from sitecore.models import Article

    if not site.enabled:
        raise ArticleDeliveryError('site_disabled')
    locale_rows = list(site.locales.filter(enabled=True).order_by('sort_order', 'locale_code'))
    locales = [
        ArticleLocale(
            code=row.locale_code,
            label=row.label,
            direction=row.direction,
            is_default=row.locale_code == site.default_locale,
        )
        for row in locale_rows
    ]
    rows = Article.objects.select_related('route__locale', 'category').filter(
        route__site_id=site.pk,
        route__status='published',
        status='published',
        route__locale__enabled=True,
    ).order_by('pk')
    versions = []
    for row in rows:
        locale = row.route.locale
        if locale.site_id != site.pk:
            raise ArticleDeliveryError('route_site_mismatch')
        if row.category_id and (
            row.category.site_id != site.pk or row.category.locale_id != locale.pk
        ):
            raise ArticleDeliveryError('category_locale_mismatch')
        config = row.config_json if isinstance(row.config_json, dict) else {}
        versions.append(
            ArticleVersion(
                content_key=config.get('contentKey', ''),
                locale=locale.locale_code,
                slug=row.slug,
                public_slug=config.get('publicSlug', ''),
                source_path=row.route.path,
                title=row.title,
                markdown=row.body_markdown,
                excerpt=row.excerpt,
                description=row.route.meta_description,
                og_title=row.route.og_title,
                og_description=row.route.og_description,
                og_image_url=row.route.og_image_url,
                robots=row.route.robots,
                author=row.author_name,
                cover_url=row.cover_image_url,
                category_key=row.category.code if row.category_id else '',
                category_label=row.category.name if row.category_id else '',
                category_locale=locale.locale_code if row.category_id else '',
                status=row.status,
                route_status=row.route.status,
                enabled=locale.enabled,
            )
        )
    site_config = site.config_json if isinstance(site.config_json, dict) else {}
    delivery_config = site_config.get('articleDelivery', {})
    if not isinstance(delivery_config, dict):
        delivery_config = {}
    return build_article_release(
        versions,
        locales=locales,
        site_code=site.code,
        site_name=site.name,
        public_origin=public_origin,
        brand_logo_url=str(delivery_config.get('brandLogoUrl') or ''),
        base_route_mode=str(delivery_config.get('baseRouteMode') or 'localized'),
    )
