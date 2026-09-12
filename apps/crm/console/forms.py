from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile

from django import forms
from django.db import models, transaction
from django.utils import timezone
from django.utils.text import slugify

from sitecore.models import Article, Category, MediaAsset, MediaAssetBinding, PageRoute, Site, SiteLocale, Tag

from .storage_security import (
    StorageSecurityError,
    configured_storage_roots,
    resolve_import_source_path,
    resolve_storage_asset_path,
)
from .taxonomy import category_label_map, ordered_categories


SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]*$')

STATUS_CHOICES = [
    ('draft', '草稿'),
    ('published', '已发布'),
    ('archived', '已归档'),
]

CONTENT_UPLOAD_EXTENSIONS = {'.md', '.markdown', '.txt'}
CONTENT_UPLOAD_HELP = (
    '只支持 .md、.markdown、.txt。上传后会写入 Markdown 正文并覆盖下方正文框。'
    'RTF、DOCX、ODT、HTML 暂不接入，避免富文本解析和脚本注入风险。'
)

ASSET_TYPE_BY_EXTENSION = {
    '.jpg': 'image',
    '.jpeg': 'image',
    '.png': 'image',
    '.webp': 'image',
    '.mp4': 'video',
    '.glb': 'model3d',
    '.ply': 'model3d',
    '.sog': 'model3d',
    '.pdf': 'document',
}
ASSET_DIR_BY_TYPE = {
    'image': 'images',
    'video': 'videos',
    'model3d': 'models',
    'document': 'documents',
}
ASSET_MIME_BY_EXTENSION = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    '.mp4': 'video/mp4',
    '.glb': 'model/gltf-binary',
    '.ply': 'application/octet-stream',
    '.sog': 'application/octet-stream',
    '.pdf': 'application/pdf',
}
ASSET_MAX_BYTES = {
    'image': 8 * 1024 * 1024,
    'video': 128 * 1024 * 1024,
    'model3d': 128 * 1024 * 1024,
    'document': 20 * 1024 * 1024,
}


def asset_storage_root() -> Path:
    # The first explicitly configured root is the deterministic write root.
    # Additional roots may remain readable during an orderly storage migration.
    return configured_storage_roots()[0]


def resolve_storage_path(storage_path: str) -> Path:
    return resolve_storage_asset_path(storage_path)


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath(
            (os.fspath(path.resolve(strict=False)), os.fspath(root.resolve(strict=True)))
        )
    except (OSError, ValueError):
        return False
    return os.path.normcase(common) == os.path.normcase(os.fspath(root))


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        raise StorageSecurityError('storage_destination_unavailable') from None
    if stat.S_ISLNK(metadata.st_mode):
        return True
    file_attributes = getattr(metadata, 'st_file_attributes', 0)
    reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    return bool(file_attributes and reparse_flag and file_attributes & reparse_flag)


def _managed_asset_target(*, directory_name: str, year: str, month: str, filename: str) -> Path:
    if directory_name not in ASSET_DIR_BY_TYPE.values():
        raise StorageSecurityError('invalid_storage_destination')
    if not (year.isdecimal() and len(year) == 4 and month.isdecimal() and len(month) == 2):
        raise StorageSecurityError('invalid_storage_destination')
    if Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise StorageSecurityError('invalid_storage_destination')

    root = asset_storage_root()
    current = root
    for part in (directory_name, year, month):
        current = current / part
        if _is_link_or_reparse_point(current):
            raise StorageSecurityError('indirect_path_not_allowed')
        try:
            current.mkdir(exist_ok=True)
        except OSError:
            raise StorageSecurityError('storage_destination_unavailable') from None
        if not current.is_dir() or not _is_path_within(current, root):
            raise StorageSecurityError('outside_allowlist')

    target = current / filename
    if _is_link_or_reparse_point(target):
        raise StorageSecurityError('indirect_path_not_allowed')
    if target.exists():
        logical_path = (Path('storage') / 'assets' / directory_name / year / month / filename).as_posix()
        return resolve_storage_asset_path(logical_path)
    resolved_target = target.resolve(strict=False)
    if not _is_path_within(resolved_target, root):
        raise StorageSecurityError('outside_allowlist')
    return resolved_target


def classify_asset(filename: str) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    asset_type = ASSET_TYPE_BY_EXTENSION.get(suffix)
    if not asset_type:
        raise forms.ValidationError('只允许上传 jpg、jpeg、png、webp、mp4、glb、ply、sog、pdf。')
    return asset_type, suffix


def asset_mime_type(filename: str) -> str:
    return ASSET_MIME_BY_EXTENSION.get(Path(filename).suffix.lower(), 'application/octet-stream')


def asset_filename(original_name: str, digest: str, suffix: str) -> str:
    stem = slugify(Path(original_name).stem, allow_unicode=False).replace('-', '_')
    stem = re.sub(r'[^a-z0-9._-]+', '_', stem.lower()).strip('._-') or 'asset'
    return f'{digest[:16]}-{stem[:72]}{suffix}'


def store_uploaded_asset(*, site: Site, uploaded_file, title: str, alt_text: str, caption: str, created_by: str) -> MediaAsset:
    asset_type, suffix = classify_asset(uploaded_file.name)
    size = int(getattr(uploaded_file, 'size', 0) or 0)
    max_bytes = ASSET_MAX_BYTES[asset_type]
    if size <= 0:
        raise forms.ValidationError('文件为空，无法入库。')
    if size > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise forms.ValidationError(f'文件过大。{asset_type} 当前限制为 {max_mb}MB。')

    storage_root = asset_storage_root()
    digest = hashlib.sha256()
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, dir=storage_root) as temporary:
            temp_path = Path(temporary.name)
            for chunk in uploaded_file.chunks():
                digest.update(chunk)
                temporary.write(chunk)

        sha256 = digest.hexdigest()
        existing = MediaAsset.objects.filter(site=site, sha256=sha256).first()
        if existing:
            resolve_storage_path(existing.storage_path)
            changed_fields = []
            if existing.status != 'active':
                existing.status = 'active'
                changed_fields.append('status')
            if title and not existing.title:
                existing.title = title
                changed_fields.append('title')
            if alt_text and not existing.alt_text:
                existing.alt_text = alt_text
                changed_fields.append('alt_text')
            if caption and not existing.caption:
                existing.caption = caption
                changed_fields.append('caption')
            if changed_fields:
                changed_fields.append('updated_at')
                existing.save(update_fields=changed_fields)
            return existing

        now = timezone.now()
        directory_name = ASSET_DIR_BY_TYPE[asset_type]
        filename = asset_filename(uploaded_file.name, sha256, suffix)
        target = _managed_asset_target(
            directory_name=directory_name,
            year=f'{now:%Y}',
            month=f'{now:%m}',
            filename=filename,
        )
        shutil.move(str(temp_path), target)
        temp_path = None

        storage_path = (Path('storage') / 'assets' / directory_name / f'{now:%Y}' / f'{now:%m}' / filename).as_posix()
        public_path = f'/assets/{directory_name}/{now:%Y}/{now:%m}/{filename}'
        # Never persist the client-declared Content-Type.  asset_file serves
        # this value on our origin, so the MIME must come from our extension
        # allowlist and be paired with X-Content-Type-Options: nosniff.
        mime_type = asset_mime_type(uploaded_file.name)

        return MediaAsset.objects.create(
            site=site,
            asset_type=asset_type,
            original_name=Path(uploaded_file.name).name,
            title=title or Path(uploaded_file.name).stem,
            alt_text=alt_text,
            caption=caption,
            mime_type=mime_type,
            file_ext=suffix,
            file_size_bytes=size,
            sha256=sha256,
            storage_path=storage_path,
            public_path=public_path,
            status='active',
            created_by=created_by or 'system',
        )
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def store_local_asset(*, site: Site, source_path: Path, title: str, alt_text: str, caption: str, created_by: str) -> MediaAsset:
    source_path = resolve_import_source_path(source_path)
    asset_type, suffix = classify_asset(source_path.name)

    size = int(source_path.stat().st_size or 0)
    max_bytes = ASSET_MAX_BYTES[asset_type]
    if size <= 0:
        raise forms.ValidationError('源文件为空，无法入库。')
    if size > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise forms.ValidationError(f'文件过大。{asset_type} 当前限制为 {max_mb}MB。')

    asset_storage_root()

    digest = hashlib.sha256()
    with source_path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    sha256 = digest.hexdigest()

    existing = MediaAsset.objects.filter(site=site, sha256=sha256).first()
    if existing:
        resolve_storage_path(existing.storage_path)
        changed_fields = []
        if existing.status != 'active':
            existing.status = 'active'
            changed_fields.append('status')
        if title and not existing.title:
            existing.title = title
            changed_fields.append('title')
        if alt_text and not existing.alt_text:
            existing.alt_text = alt_text
            changed_fields.append('alt_text')
        if caption and not existing.caption:
            existing.caption = caption
            changed_fields.append('caption')
        if changed_fields:
            changed_fields.append('updated_at')
            existing.save(update_fields=changed_fields)
        return existing

    now = timezone.now()
    directory_name = ASSET_DIR_BY_TYPE[asset_type]
    filename = asset_filename(source_path.name, sha256, suffix)
    target = _managed_asset_target(
        directory_name=directory_name,
        year=f'{now:%Y}',
        month=f'{now:%m}',
        filename=filename,
    )
    shutil.copy2(source_path, target)

    storage_path = (Path('storage') / 'assets' / directory_name / f'{now:%Y}' / f'{now:%m}' / filename).as_posix()
    public_path = f'/assets/{directory_name}/{now:%Y}/{now:%m}/{filename}'
    mime_type = asset_mime_type(source_path.name)

    return MediaAsset.objects.create(
        site=site,
        asset_type=asset_type,
        original_name=source_path.name,
        title=title or source_path.stem,
        alt_text=alt_text,
        caption=caption,
        mime_type=mime_type,
        file_ext=suffix,
        file_size_bytes=size,
        sha256=sha256,
        storage_path=storage_path,
        public_path=public_path,
        status='active',
        created_by=created_by or 'system',
    )


class AssetUploadForm(forms.Form):
    file = forms.FileField(
        label='上传资产',
        help_text='白名单：jpg、jpeg、png、webp、mp4、glb、ply、sog、pdf。文件只进入受控资产库，不执行脚本，不作为插件安装。',
        widget=forms.ClearableFileInput(attrs={'accept': '.jpg,.jpeg,.png,.webp,.mp4,.glb,.ply,.sog,.pdf'}),
    )
    title = forms.CharField(label='资产标题', max_length=220, required=False)
    alt_text = forms.CharField(
        label='替代文本',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': '图片用于 SEO 和无障碍；视频、3D、PDF 可留空。'}),
    )
    caption = forms.CharField(label='说明', required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def clean_file(self):
        uploaded_file = self.cleaned_data['file']
        asset_type, _suffix = classify_asset(uploaded_file.name)
        size = int(getattr(uploaded_file, 'size', 0) or 0)
        if size <= 0:
            raise forms.ValidationError('文件为空，无法入库。')
        max_bytes = ASSET_MAX_BYTES[asset_type]
        if size > max_bytes:
            max_mb = max_bytes // (1024 * 1024)
            raise forms.ValidationError(f'文件过大。{asset_type} 当前限制为 {max_mb}MB。')
        return uploaded_file

    def save(self, *, site: Site, created_by: str) -> MediaAsset:
        return store_uploaded_asset(
            site=site,
            uploaded_file=self.cleaned_data['file'],
            title=self.cleaned_data.get('title', '').strip(),
            alt_text=self.cleaned_data.get('alt_text', '').strip(),
            caption=self.cleaned_data.get('caption', '').strip(),
            created_by=created_by,
        )


class AssetImportPathForm(forms.Form):
    file_path = forms.CharField(
        label='源文件路径',
        max_length=500,
        help_text='只能从系统管理员显式配置的本地导入目录中选择文件。',
        widget=forms.TextInput(attrs={'placeholder': r'/mnt/d/media/3D-PJ/.../model.ply'}),
    )
    title = forms.CharField(label='资产标题', max_length=220, required=False)
    alt_text = forms.CharField(label='替代文本', required=False, widget=forms.Textarea(attrs={'rows': 2}))
    caption = forms.CharField(label='说明', required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def clean_file_path(self):
        raw = (self.cleaned_data['file_path'] or '').strip()
        if not raw:
            raise forms.ValidationError('源文件路径不能为空。')
        try:
            path = resolve_import_source_path(raw)
        except StorageSecurityError:
            raise forms.ValidationError('文件不在允许的导入目录中或不可用。') from None
        classify_asset(path.name)
        return path

    def save(self, *, site: Site, created_by: str) -> MediaAsset:
        return store_local_asset(
            site=site,
            source_path=self.cleaned_data['file_path'],
            title=self.cleaned_data.get('title', '').strip(),
            alt_text=self.cleaned_data.get('alt_text', '').strip(),
            caption=self.cleaned_data.get('caption', '').strip(),
            created_by=created_by,
        )


def decode_text(raw: bytes) -> str:
    for encoding in ('utf-8-sig', 'utf-16', 'gb18030', 'cp1252', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def normalize_markdown(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = [re.sub(r'[ \t]+$', '', line) for line in text.split('\n')]
    normalized = '\n'.join(lines)
    normalized = re.sub(r'\n{3,}', '\n\n', normalized)
    return normalized.strip()


def first_markdown_image(markdown: str) -> str:
    if not markdown:
        return ''

    markdown_image = re.search(r'!\[[^\]]*\]\(([^)\s]+)(?:\s+["\'][^"\']*["\'])?\)', markdown)
    if markdown_image and markdown_image.group(1):
        return markdown_image.group(1).strip()

    html_image = re.search(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', markdown, re.IGNORECASE)
    if html_image and html_image.group(1):
        return html_image.group(1).strip()

    return ''


def uploaded_file_to_markdown(uploaded_file) -> str:
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in CONTENT_UPLOAD_EXTENSIONS:
        raise forms.ValidationError(f'不支持 {suffix or "无扩展名"} 文件。请上传 md、markdown 或 txt。')

    raw = uploaded_file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise forms.ValidationError('文件过大。当前限制为 2MB，建议先拆分正文或整理为 Markdown。')

    return normalize_markdown(decode_text(raw))


def fallback_slug() -> str:
    return f'article-{timezone.now():%Y%m%d%H%M%S}'


def fallback_tag_slug(value: str) -> str:
    digest = hashlib.sha1(value.encode('utf-8')).hexdigest()[:8]
    return f'tag_{digest}'


def normalize_slug(value: str, title: str) -> str:
    raw = value.strip() or title.strip()
    normalized = slugify(raw, allow_unicode=False).replace('-', '_')
    return normalized or fallback_slug()


def normalize_tag_tokens(value: str) -> list[str]:
    tokens = re.split(r'[,，\n]+', value or '')
    seen: set[str] = set()
    normalized: list[str] = []
    for token in tokens:
        cleaned = token.strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
    return normalized


def next_tag_identity(*, site: Site, locale: SiteLocale, raw_name: str) -> tuple[str, str]:
    base_slug = slugify(raw_name, allow_unicode=False).replace('-', '_') or fallback_tag_slug(raw_name)
    base_code = base_slug
    slug = base_slug
    code = base_code
    index = 2
    while Tag.objects.filter(site=site, locale=locale).filter(models.Q(code=code) | models.Q(slug=slug)).exists():
        code = f'{base_code}_{index}'
        slug = f'{base_slug}_{index}'
        index += 1
    return code, slug


class ArticleEditorForm(forms.Form):
    title = forms.CharField(
        label='文章标题',
        max_length=220,
        widget=forms.TextInput(attrs={'placeholder': '例如：How to evaluate a water filling line project'}),
    )
    slug = forms.CharField(
        label='路径别名',
        max_length=180,
        required=False,
        help_text='用于生成访问路径，只允许小写英文、数字、下划线和短横线。',
        widget=forms.TextInput(attrs={'placeholder': 'water_filling_line_project_evaluation'}),
    )
    category = forms.ModelChoiceField(
        label='文章分类',
        queryset=Category.objects.none(),
        required=False,
        empty_label='不选分类',
    )
    status = forms.ChoiceField(label='状态', choices=STATUS_CHOICES)
    published_at = forms.DateTimeField(
        label='发布时间',
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )
    excerpt = forms.CharField(
        label='摘要',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '用于文章列表、卡片摘要和基础 SEO 的简短概述。'}),
    )
    body_markdown = forms.CharField(
        label='正文（Markdown）',
        required=False,
        widget=forms.Textarea(attrs={'rows': 18, 'placeholder': '# 一级标题\n\n在这里编写文章正文……'}),
    )
    content_file = forms.FileField(
        label='导入正文文件',
        required=False,
        help_text=CONTENT_UPLOAD_HELP,
        widget=forms.ClearableFileInput(attrs={'accept': '.md,.markdown,.txt'}),
    )
    cover_asset = forms.ModelChoiceField(
        label='资产库封面图',
        queryset=MediaAsset.objects.none(),
        required=False,
        empty_label='不使用资产库封面',
        help_text='推荐从资产库选择封面。选择后会自动写入公开图片路径，并建立资产引用记录；如果不选，系统会尝试用正文第一张图片作为默认封面。',
    )
    cover_image_url = forms.CharField(label='封面图地址', required=False)
    author_name = forms.CharField(label='作者', max_length=160, required=False)
    reading_minutes = forms.IntegerField(label='阅读分钟', min_value=1, initial=3)
    meta_description = forms.CharField(
        label='页面描述',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
    )
    og_title = forms.CharField(label='社媒分享标题', required=False)
    og_description = forms.CharField(label='社媒分享描述', required=False, widget=forms.Textarea(attrs={'rows': 2}))
    og_image_url = forms.CharField(label='社媒分享图片地址', required=False)
    robots = forms.CharField(label='搜索引擎指令', required=False, initial='index,follow,max-image-preview:large')

    def __init__(
        self,
        *args,
        site: Site,
        locale: SiteLocale,
        article: Article | None = None,
        allow_publish: bool = False,
        allow_assets: bool = False,
        **kwargs,
    ):
        self.site = site
        self.locale = locale
        self.article = article
        self.allow_publish = bool(allow_publish)
        self.allow_assets = bool(allow_assets)

        if article and not kwargs.get('initial'):
            config_json = article.config_json or {}
            kwargs['initial'] = {
                'title': article.title,
                'slug': article.slug,
                'category': article.category_id,
                'status': article.status,
                'published_at': article.published_at,
                'excerpt': article.excerpt,
                'body_markdown': article.body_markdown,
                'cover_asset': config_json.get('coverAssetId'),
                'cover_image_url': article.cover_image_url,
                'author_name': article.author_name,
                'reading_minutes': article.reading_minutes,
                'meta_description': article.route.meta_description,
                'og_title': article.route.og_title,
                'og_description': article.route.og_description,
                'og_image_url': article.route.og_image_url,
                'robots': article.route.robots,
            }

        super().__init__(*args, **kwargs)
        if not self.allow_publish:
            self.fields['status'].choices = [
                choice for choice in STATUS_CHOICES if choice[0] != 'published'
            ]
        if not article and not self.allow_publish and not self.is_bound:
            self.fields['status'].initial = 'draft'
        self.fields['slug'].help_text = (
            f'用于生成路径：/{self.locale.locale_code}/articles/{{slug}}/。'
            '只允许小写英文、数字、下划线和短横线。'
        )
        category_rows = [row for row in ordered_categories(site, locale) if row.status == 'published']
        category_labels = category_label_map(category_rows)
        self.fields['category'].queryset = Category.objects.filter(id__in=[row.id for row in category_rows]).order_by(
            'parent_id', 'sort_order', 'name', 'id'
        )
        self.fields['category'].label_from_instance = lambda obj: category_labels.get(obj.id, obj.name)
        if self.allow_assets:
            self.fields['cover_asset'].queryset = MediaAsset.objects.filter(
                site=site,
                asset_type='image',
                status='active',
            ).order_by('-updated_at', 'title', 'original_name')
        else:
            # Do not query or render the asset catalogue when the operator only
            # has content-write permission. Existing bindings are preserved by
            # save() as long as the public cover URL is not changed.
            self.fields.pop('cover_asset')

    def clean_slug(self) -> str:
        slug = normalize_slug(self.cleaned_data.get('slug', ''), self.cleaned_data.get('title', ''))
        if not SLUG_RE.fullmatch(slug):
            raise forms.ValidationError('请使用小写英文、数字、下划线或短横线。')
        return slug

    def clean(self) -> dict:
        cleaned = super().clean()
        slug = cleaned.get('slug')
        status = cleaned.get('status')
        published_at = cleaned.get('published_at')

        if status == 'published' and not self.allow_publish:
            self.add_error('status', '当前账号不能把文章设为已发布。')
        if status == 'published' and not published_at:
            cleaned['published_at'] = timezone.now()

        if slug:
            path = f'/{self.locale.locale_code}/articles/{slug}/'
            route_query = PageRoute.objects.filter(site=self.site, locale=self.locale, path=path)
            if self.article:
                route_query = route_query.exclude(id=self.article.route_id)
            if route_query.exists():
                self.add_error('slug', '这个 slug 已经被其他路由使用。')

        uploaded_file = cleaned.get('content_file')
        if uploaded_file:
            cleaned['body_markdown'] = uploaded_file_to_markdown(uploaded_file)

        return cleaned

    @transaction.atomic
    def save(self) -> Article:
        data = self.cleaned_data
        status = data['status']
        published_at = data['published_at'] if status == 'published' else None
        path = f'/{self.locale.locale_code}/articles/{data["slug"]}/'
        excerpt = data.get('excerpt', '').strip()
        meta_description = data.get('meta_description', '').strip() or excerpt
        og_title = data.get('og_title', '').strip() or data['title']
        og_description = data.get('og_description', '').strip() or meta_description

        route = self.article.route if self.article else PageRoute(site=self.site, locale=self.locale)
        route.path = path
        route.route_type = 'article'
        route.status = status
        route.page_title = data['title']
        route.meta_description = meta_description
        route.canonical_url = ''
        route.og_title = og_title
        route.og_description = og_description
        route.og_image_url = data.get('og_image_url', '').strip()
        route.robots = data.get('robots', '').strip() or 'index,follow,max-image-preview:large'
        route.published_at = published_at
        route.save()

        article = self.article or Article(route=route)
        article.category = data.get('category')
        article.title = data['title']
        article.slug = data['slug']
        article.excerpt = excerpt
        article.body_markdown = data.get('body_markdown', '').strip()
        article.body_json = article.body_json or {}
        original_cover_image_url = self.article.cover_image_url if self.article else ''
        original_cover_asset_id = (
            (self.article.config_json or {}).get('coverAssetId')
            if self.article else None
        )
        cover_asset = data.get('cover_asset') if self.allow_assets else None
        cover_image_url = data.get('cover_image_url', '').strip()
        preserve_existing_cover_binding = bool(
            not self.allow_assets
            and self.article
            and original_cover_asset_id
            and cover_image_url == original_cover_image_url
        )
        if not cover_asset and not cover_image_url:
            cover_image_url = first_markdown_image(article.body_markdown)
        article.cover_image_url = cover_asset.public_path if cover_asset else cover_image_url
        article.author_name = data.get('author_name', '').strip()
        article.status = status
        article.published_at = published_at
        article.reading_minutes = data['reading_minutes']
        article.seo_json = article.seo_json or {}
        article.config_json = article.config_json or {}
        if cover_asset:
            article.config_json['coverAssetId'] = cover_asset.id
        elif not preserve_existing_cover_binding:
            article.config_json.pop('coverAssetId', None)
        article.save()

        cover_bindings = MediaAssetBinding.objects.filter(
            site=self.site,
            locale=self.locale,
            entity_type='article',
            entity_id=article.id,
            role='cover',
        )
        if cover_asset:
            MediaAssetBinding.objects.update_or_create(
                asset=cover_asset,
                site=self.site,
                locale=self.locale,
                entity_type='article',
                entity_id=article.id,
                role='cover',
                defaults={'enabled': True, 'sort_order': 10, 'config_json': {}},
            )
            cover_bindings.exclude(asset=cover_asset).update(enabled=False)
        elif not preserve_existing_cover_binding:
            cover_bindings.update(enabled=False)

        return article
