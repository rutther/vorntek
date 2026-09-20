"""Render a verified article release without writes or network calls."""
from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from django.template import Context, Engine
from django.utils.safestring import mark_safe
from markdown_it import MarkdownIt

from .article_delivery import ArticleDeliveryError, article_path, content_digest


COPY = {
    'en': dict(home='Home', company='Company', products='Products', technology='Technology', solutions='Applications', service='Service', contact='Contact', articles='Articles', privacy='Privacy', skip='Skip to content', read='Read article', discuss='Discuss your project', empty='No published articles in this language.', preview='Preview — not published', language='Language'),
    'fr': dict(home='Accueil', company='Entreprise', products='Produits', technology='Technologie', solutions='Applications', service='Services', contact='Contact', articles='Articles', privacy='Confidentialité', skip='Aller au contenu', read='Lire l’article', discuss='Parlons de votre projet', empty='Aucun article publié dans cette langue.', preview='Aperçu — non publié', language='Langue'),
    'ar': dict(home='الرئيسية', company='الشركة', products='المنتجات', technology='التقنية', solutions='التطبيقات', service='الخدمات', contact='اتصل بنا', articles='المقالات', privacy='الخصوصية', skip='انتقل إلى المحتوى', read='اقرأ المقال', discuss='ناقش مشروعك', empty='لا توجد مقالات منشورة بهذه اللغة.', preview='معاينة — غير منشور', language='اللغة'),
    'zh': dict(home='首页', company='公司', products='产品与业务', technology='技术', solutions='应用', service='服务', contact='联系', articles='文章', privacy='隐私', skip='跳到主要内容', read='阅读文章', discuss='沟通项目需求', empty='该语言暂无已发布文章。', preview='预览 — 未发布', language='语言'),
}

_engine = Engine(dirs=[str(Path(__file__).parent / 'templates')], autoescape=True)


def _copy(locale: str) -> dict:
    return COPY.get(locale, COPY['en'])


def _safe_markdown_link(url: str) -> bool:
    if any(ord(char) < 32 for char in url) or '\\' in url or url.startswith('//'):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme.lower() in ('', 'https', 'mailto', 'tel')
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


def render_markdown(
    body: str,
    *,
    locale: str = 'en',
    page_title: str = '',
    article_links: dict | None = None,
    public_origin: str = '',
    default_locale: str = 'en',
    base_route_mode: str = 'localized',
) -> str:
    parser = MarkdownIt('commonmark', {'html': False}).enable('table')
    parser.validateLink = _safe_markdown_link
    tokens = parser.parse(body)
    if (
        page_title
        and len(tokens) >= 3
        and tokens[0].type == 'heading_open'
        and tokens[0].tag == 'h1'
        and tokens[1].type == 'inline'
        and tokens[1].content.strip() == page_title.strip()
        and tokens[2].type == 'heading_close'
    ):
        tokens = tokens[3:]
    for token in tokens:
        if token.type in ('heading_open', 'heading_close') and token.tag == 'h1':
            token.tag = 'h2'
        if token.type != 'inline':
            continue
        for child in token.children or []:
            if child.type == 'image':
                child.attrSet('loading', 'lazy')
                child.attrSet('decoding', 'async')
                src = child.attrGet('src') or ''
                if src.startswith(('assets/', './assets/')):
                    child.attrSet('src', '/' + src.removeprefix('./'))
            if child.type != 'link_open':
                continue
            href = child.attrGet('href') or ''
            destination = urlsplit(href)
            same_site = (href.startswith('/') and not href.startswith('//')) or (
                bool(public_origin)
                and destination.scheme == 'https'
                and destination.netloc == urlsplit(public_origin).netloc
            )
            if same_site and article_links is not None and destination.path in article_links:
                target = article_links[destination.path]
                child.attrSet(
                    'href',
                    target
                    + (('?' + destination.query) if destination.query else '')
                    + (('#' + destination.fragment) if destination.fragment else ''),
                )
            elif href.startswith('/') and not href.startswith('//'):
                parts = destination.path.strip('/').split('/')
                if (
                    parts[0]
                    in ('products', 'company', 'technology', 'solutions', 'service', 'contact', 'privacy')
                    or destination.path == '/articles/'
                ):
                    prefix = (
                        ''
                        if base_route_mode == 'shared' or locale == default_locale
                        else '/' + locale
                    )
                    child.attrSet('href', prefix + href)
    rendered = parser.renderer.render(tokens, parser.options, {})
    return rendered.replace(
        '<table>', '<div class="article-table" tabindex="0"><table>'
    ).replace('</table>', '</table></div>')


def _script_json(value: dict) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace('&', '\\u0026')
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
        .replace('\u2028', '\\u2028')
        .replace('\u2029', '\\u2029')
    )


def _validate_release(release: dict) -> None:
    payload = {key: value for key, value in release.items() if key != 'releaseSha256'}
    if content_digest(payload) != release.get('releaseSha256'):
        raise ArticleDeliveryError('snapshot_digest_mismatch')


def render_article_sitemap(release: dict) -> str:
    _validate_release(release)
    root = ET.Element('urlset', {'xmlns': 'http://www.sitemaps.org/schemas/sitemap/0.9'})
    for row in release['articles']:
        directives = {part.strip().lower() for part in row.get('robots', '').split(',')}
        if directives.intersection({'noindex', 'none'}):
            continue
        url = ET.SubElement(root, 'url')
        ET.SubElement(url, 'loc').text = row['canonical']
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding='unicode'
    )


def render_article_documents(release: dict, *, preview: bool = True) -> dict[str, str]:
    """Render immutable HTML. Marketing scripts are deliberately not injected."""

    _validate_release(release)
    template = _engine.get_template('console/article_delivery_page.html')
    documents = {}
    locale_rows = release['locales']
    default_locale = release['defaultLocale']
    base_route_mode = release.get('baseRouteMode', 'localized')
    if base_route_mode not in {'localized', 'shared'}:
        raise ArticleDeliveryError('invalid_base_route_mode')
    for locale_row in locale_rows:
        language = locale_row['code']
        direction = locale_row['direction']
        label = locale_row['label']
        t = _copy(language)
        prefix = '' if language == default_locale else '/' + language
        base_prefix = '' if base_route_mode == 'shared' else prefix
        rows = [row for row in release['articles'] if row['language'] == language]
        article_links = {}
        for source in release['articles']:
            translated = source['alternates'].get(language)
            if translated:
                article_links[source['path']] = urlsplit(translated).path
        for alias, destination in release['redirects'].items():
            if destination in article_links:
                article_links[alias] = article_links[destination]
        hub = prefix + '/articles/'
        base = {
            'language': language,
            'languageLabel': label,
            'direction': direction,
            'siteName': release['siteName'],
            'brandLogoUrl': release['brandLogoUrl'],
            't': t,
            'prefix': prefix,
            'basePrefix': base_prefix,
            'preview': preview,
            'releaseHash': release['releaseSha256'],
            'nav': [
                {'label': t[key], 'href': base_prefix + '/' + key + '/'}
                for key in (
                    'company',
                    'products',
                    'technology',
                    'solutions',
                    'articles',
                    'service',
                    'contact',
                )
            ],
        }
        for row in [None, *rows]:
            route = row['path'] if row else hub
            canonical = row['canonical'] if row else release['publicOrigin'] + hub
            alternates = (
                row['alternates']
                if row
                else {
                    **{
                        item['code']: release['publicOrigin']
                        + (
                            ''
                            if item['code'] == default_locale
                            else '/' + item['code']
                        )
                        + '/articles/'
                        for item in locale_rows
                    },
                    'x-default': release['publicOrigin'] + '/articles/',
                }
            )
            title = row['title'] if row else t['articles']
            description = row['description'] if row else t['articles']
            page_node = {
                '@type': 'Article' if row else 'CollectionPage',
                '@id': canonical + '#page',
                'name': title,
                'url': canonical,
                'inLanguage': language,
                'description': description,
            }
            if row:
                page_node['headline'] = title
            schema = {
                '@context': 'https://schema.org',
                '@graph': [
                    page_node,
                    {
                        '@type': 'BreadcrumbList',
                        '@id': canonical + '#breadcrumb',
                        'itemListElement': [
                            {
                                '@type': 'ListItem',
                                'position': 1,
                                'name': release['siteName'],
                                'item': release['publicOrigin'] + base_prefix + '/',
                            },
                            {
                                '@type': 'ListItem',
                                'position': 2,
                                'name': title,
                                'item': canonical,
                            },
                        ],
                    },
                ],
            }
            languages = []
            for item in locale_rows:
                code = item['code']
                if code not in alternates:
                    continue
                languages.append(
                    {
                        'code': code,
                        'label': item['label'],
                        'href': urlsplit(alternates[code]).path,
                        'current': code == language,
                    }
                )
            context = {
                **base,
                'row': row,
                'rows': rows,
                'title': title,
                'canonical': canonical,
                'description': description,
                'ogImage': (
                    (
                        release['publicOrigin'] + row['ogImageUrl']
                        if row['ogImageUrl'].startswith('/')
                        else row['ogImageUrl']
                    )
                    if row
                    else ''
                ),
                'alternates': [
                    {'language': code, 'url': url}
                    for code, url in alternates.items()
                ],
                'languages': languages,
                'bodyHtml': (
                    mark_safe(
                        render_markdown(
                            row['bodyMarkdown'],
                            locale=language,
                            page_title=row['title'],
                            article_links=article_links,
                            public_origin=release['publicOrigin'],
                            default_locale=default_locale,
                            base_route_mode=base_route_mode,
                        )
                    )
                    if row
                    else ''
                ),
                'schemaJson': mark_safe(_script_json(schema)),
            }
            documents[route.strip('/') + '/index.html'] = template.render(
                Context(context, use_l10n=False)
            )
    return documents
