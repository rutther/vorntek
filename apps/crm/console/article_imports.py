from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import io
import logging
import re
import zipfile

from django import forms
from django.db import transaction

from sitecore.models import Article, Category, Site, SiteLocale

from .forms import ArticleEditorForm, CONTENT_UPLOAD_EXTENSIONS, STATUS_CHOICES, decode_text, normalize_markdown, normalize_slug
from .taxonomy import category_label_map, ordered_categories


FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
PARAGRAPH_RE = re.compile(r"(?ms)^(?!#)(.+?)$")
ALLOWED_IMPORT_EXTENSIONS = CONTENT_UPLOAD_EXTENSIONS
MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_ENTRY_BYTES = 2 * 1024 * 1024
MAX_ENTRY_COUNT = 200


logger = logging.getLogger(__name__)


@dataclass
class ImportEntryResult:
    filename: str
    status: str
    title: str
    slug: str
    path: str
    message: str


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1].strip()
    return value


def parse_front_matter(markdown: str) -> tuple[dict[str, str], str]:
    match = FRONT_MATTER_RE.match(markdown)
    if not match:
        return {}, markdown

    raw_meta, body = match.groups()
    metadata: dict[str, str] = {}
    for line in raw_meta.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or ':' not in line:
            continue
        key, value = line.split(':', 1)
        metadata[key.strip().lower()] = strip_quotes(value)
    return metadata, body


def infer_title(metadata: dict[str, str], body: str, filename: str) -> str:
    if metadata.get('title'):
        return metadata['title'].strip()
    heading = HEADING_RE.search(body or '')
    if heading:
        return heading.group(1).strip()
    return Path(filename).stem.replace('_', ' ').replace('-', ' ').strip() or 'Imported Article'


def infer_excerpt(metadata: dict[str, str], body: str) -> str:
    if metadata.get('excerpt'):
        return metadata['excerpt'].strip()
    paragraph = PARAGRAPH_RE.search(body or '')
    if not paragraph:
        return ''
    excerpt = re.sub(r'\s+', ' ', paragraph.group(1)).strip()
    return excerpt[:220]


def resolve_category(
    *,
    site: Site,
    locale: SiteLocale,
    category_value: str,
    default_category: Category | None,
) -> Category | None:
    value = (category_value or '').strip()
    if not value:
        return default_category
    return (
        Category.objects.filter(site=site, locale=locale, code=value).first()
        or Category.objects.filter(site=site, locale=locale, slug=value).first()
    )


def discover_existing_article(*, site: Site, locale: SiteLocale, slug: str) -> Article | None:
    path = f'/{locale.locale_code}/articles/{slug}/'
    return (
        Article.objects.select_related('route')
        .filter(route__site=site, route__locale=locale, route__path=path)
        .first()
    )


def build_form_payload(
    *,
    metadata: dict[str, str],
    body_markdown: str,
    filename: str,
    default_status: str,
    default_category: Category | None,
    site: Site,
    locale: SiteLocale,
) -> tuple[dict[str, object], str]:
    title = infer_title(metadata, body_markdown, filename)
    slug = normalize_slug(metadata.get('slug', ''), title)
    explicit_category = (metadata.get('category_code') or metadata.get('category') or '').strip()
    category = resolve_category(
        site=site,
        locale=locale,
        category_value=explicit_category,
        default_category=default_category,
    )
    if explicit_category and category is None:
        raise forms.ValidationError(f'未找到分类：{explicit_category}')
    excerpt = infer_excerpt(metadata, body_markdown)
    status = (metadata.get('status') or default_status).strip().lower() or default_status
    if status not in {choice[0] for choice in STATUS_CHOICES}:
        status = default_status

    payload: dict[str, object] = {
        'title': title,
        'slug': slug,
        'category': category.id if category else '',
        'status': status,
        'published_at': metadata.get('published_at', '').strip(),
        'excerpt': excerpt,
        'body_markdown': body_markdown,
        'cover_image_url': metadata.get('cover_image_url', '').strip(),
        'author_name': metadata.get('author_name', '').strip(),
        'reading_minutes': metadata.get('reading_minutes', '').strip() or '3',
        'meta_description': metadata.get('meta_description', '').strip(),
        'og_title': metadata.get('og_title', '').strip(),
        'og_description': metadata.get('og_description', '').strip(),
        'og_image_url': metadata.get('og_image_url', '').strip(),
        'robots': metadata.get('robots', '').strip() or 'index,follow,max-image-preview:large',
    }
    return payload, slug


class ArticleImportZipForm(forms.Form):
    import_file = forms.FileField(
        label='批量导入包',
        help_text='上传 zip 文件。每篇文章对应一个 md、markdown 或 txt 文件；支持简单 front matter。',
        widget=forms.ClearableFileInput(attrs={'accept': '.zip'}),
    )
    default_status = forms.ChoiceField(label='默认状态', choices=STATUS_CHOICES, initial='draft')
    default_category = forms.ModelChoiceField(
        label='默认分类',
        queryset=Category.objects.none(),
        required=False,
        empty_label='不设默认分类',
    )

    def __init__(
        self,
        *args,
        site: Site,
        locale: SiteLocale,
        allow_publish: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.site = site
        self.locale = locale
        self.allow_publish = bool(allow_publish)
        if not self.allow_publish:
            self.fields['default_status'].choices = [
                choice for choice in STATUS_CHOICES if choice[0] != 'published'
            ]
        category_rows = ordered_categories(site, locale)
        category_labels = category_label_map(category_rows)
        self.fields['default_category'].queryset = Category.objects.filter(
            id__in=[row.id for row in category_rows if row.status == 'published']
        ).order_by('parent_id', 'sort_order', 'name', 'id')
        self.fields['default_category'].label_from_instance = lambda obj: category_labels.get(obj.id, obj.name)

    def clean_import_file(self):
        uploaded = self.cleaned_data['import_file']
        suffix = Path(uploaded.name).suffix.lower()
        if suffix != '.zip':
            raise forms.ValidationError('只支持 zip 批量导入包。')
        size = int(getattr(uploaded, 'size', 0) or 0)
        if size <= 0:
            raise forms.ValidationError('导入包为空。')
        if size > MAX_ZIP_BYTES:
            raise forms.ValidationError('导入包过大，当前限制为 16MB。')
        try:
            uploaded.seek(0)
            with zipfile.ZipFile(uploaded) as archive:
                entries = [info for info in archive.infolist() if not info.is_dir()]
            uploaded.seek(0)
        except zipfile.BadZipFile as exc:
            raise forms.ValidationError('zip 文件损坏或格式不正确。') from exc
        if len(entries) > MAX_ENTRY_COUNT:
            raise forms.ValidationError(f'导入包中文件过多，当前上限为 {MAX_ENTRY_COUNT} 个。')
        return uploaded

    def clean(self):
        cleaned = super().clean()
        uploaded = cleaned.get('import_file')
        if self.allow_publish or uploaded is None:
            return cleaned
        if cleaned.get('default_status') == 'published':
            self.add_error('default_status', '当前账号不能通过批量导入发布文章。')
            return cleaned

        try:
            uploaded.seek(0)
            with zipfile.ZipFile(uploaded) as archive:
                for info in archive.infolist():
                    if info.is_dir() or Path(info.filename).suffix.lower() not in ALLOWED_IMPORT_EXTENSIONS:
                        continue
                    if info.file_size > MAX_ENTRY_BYTES:
                        continue
                    metadata, _body = parse_front_matter(decode_text(archive.read(info)))
                    if (metadata.get('status') or '').strip().lower() == 'published':
                        self.add_error(
                            'import_file',
                            f'{info.filename} 请求发布状态；当前账号只能导入草稿或归档内容。',
                        )
                        break
        finally:
            uploaded.seek(0)
        return cleaned

    def save(self) -> dict[str, object]:
        uploaded = self.cleaned_data['import_file']
        uploaded.seek(0)
        raw = uploaded.read()
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = [info for info in archive.infolist() if not info.is_dir()]

        results: list[ImportEntryResult] = []
        created = 0
        updated = 0
        failed = 0
        skipped = 0

        default_status = self.cleaned_data['default_status']
        default_category = self.cleaned_data.get('default_category')

        for info in entries:
            file_path = Path(info.filename)
            if file_path.name.startswith('.'):
                continue
            if file_path.suffix.lower() not in ALLOWED_IMPORT_EXTENSIONS:
                skipped += 1
                results.append(
                    ImportEntryResult(
                        filename=info.filename,
                        status='跳过',
                        title='',
                        slug='',
                        path='',
                        message='已跳过：只导入 md、markdown、txt 文件。',
                    )
                )
                continue
            if info.file_size > MAX_ENTRY_BYTES:
                failed += 1
                results.append(
                    ImportEntryResult(
                        filename=info.filename,
                        status='失败',
                        title='',
                        slug='',
                        path='',
                        message='文件过大：单篇导入当前限制为 2MB。',
                    )
                )
                continue

            try:
                decoded = decode_text(archive.read(info))
                markdown = normalize_markdown(decoded)
                metadata, body_markdown = parse_front_matter(markdown)
                if not body_markdown.strip():
                    raise forms.ValidationError('正文为空。')

                payload, slug = build_form_payload(
                    metadata=metadata,
                    body_markdown=body_markdown,
                    filename=info.filename,
                    default_status=default_status,
                    default_category=default_category,
                    site=self.site,
                    locale=self.locale,
                )
                existing = discover_existing_article(site=self.site, locale=self.locale, slug=slug)
                if existing and existing.status == 'published' and not self.allow_publish:
                    raise forms.ValidationError(
                        '当前账号不能通过批量导入修改、降级或归档已发布文章。'
                    )

                with transaction.atomic():
                    form = ArticleEditorForm(
                        payload,
                        site=self.site,
                        locale=self.locale,
                        article=existing,
                        allow_publish=self.allow_publish,
                    )
                    if not form.is_valid():
                        errors = []
                        for field, field_errors in form.errors.items():
                            for error in field_errors:
                                if field == '__all__':
                                    errors.append(str(error))
                                else:
                                    errors.append(f'{field}: {error}')
                        raise forms.ValidationError('；'.join(errors) or '表单校验失败。')
                    article = form.save()

                result_status = 'updated' if existing else 'created'
                if existing:
                    updated += 1
                else:
                    created += 1
                results.append(
                    ImportEntryResult(
                        filename=info.filename,
                        status='更新' if existing else '新增',
                        title=article.title,
                        slug=article.slug,
                        path=article.route.path,
                        message='已写入数据库。',
                    )
                )
            except forms.ValidationError as exc:
                failed += 1
                results.append(
                    ImportEntryResult(
                        filename=info.filename,
                        status='失败',
                        title='',
                        slug='',
                        path='',
                        message='；'.join(exc.messages) or '表单校验失败。',
                    )
                )
            except Exception:  # pragma: no cover - defensive logging path
                logger.exception('Unexpected article ZIP import entry failure.')
                failed += 1
                results.append(
                    ImportEntryResult(
                        filename=info.filename,
                        status='失败',
                        title='',
                        slug='',
                        path='',
                        message='导入失败；未写入数据库。请联系系统管理员查看服务日志。',
                    )
                )

        return {
            'created': created,
            'updated': updated,
            'failed': failed,
            'skipped': skipped,
            'total': len(results),
            'entries': results,
        }
