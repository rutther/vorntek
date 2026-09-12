from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from sitecore.models import Article, Site, SiteLocale


def dump_front_matter(article: Article) -> str:
    route = article.route
    lines = [
        '---',
        f'title: {article.title}',
        f'slug: {article.slug}',
    ]
    if article.category:
        lines.append(f'category_code: {article.category.code}')
    if article.status:
        lines.append(f'status: {article.status}')
    if article.published_at:
        lines.append(f'published_at: {article.published_at.isoformat(timespec="minutes")}')
    if article.excerpt:
        lines.append(f'excerpt: {article.excerpt}')
    if article.author_name:
        lines.append(f'author_name: {article.author_name}')
    if article.reading_minutes:
        lines.append(f'reading_minutes: {article.reading_minutes}')
    if route.meta_description:
        lines.append(f'meta_description: {route.meta_description}')
    if route.og_title:
        lines.append(f'og_title: {route.og_title}')
    if route.og_description:
        lines.append(f'og_description: {route.og_description}')
    if route.og_image_url:
        lines.append(f'og_image_url: {route.og_image_url}')
    if route.robots:
        lines.append(f'robots: {route.robots}')
    lines.append('---')
    return '\n'.join(lines)


def build_article_markdown(article: Article) -> str:
    front_matter = dump_front_matter(article)
    body = (article.body_markdown or '').strip()
    if not body:
        body = f'# {article.title}\n\n{(article.excerpt or "").strip()}'.strip()
    return f'{front_matter}\n\n{body}\n'


def build_articles_export_zip(*, site: Site, locale: SiteLocale) -> tuple[BytesIO, int]:
    buffer = BytesIO()
    article_rows = list(
        Article.objects.select_related('route', 'category')
        .filter(route__site=site, route__locale=locale)
        .order_by('-updated_at', 'title')
    )
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        for article in article_rows:
            archive.writestr(f'{article.slug}.md', build_article_markdown(article))
    buffer.seek(0)
    return buffer, len(article_rows)
