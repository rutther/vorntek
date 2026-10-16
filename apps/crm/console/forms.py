from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from pathlib import Path

from django import forms
from django.db import connection, models, transaction
from django.utils import timezone
from django.utils.text import slugify

from sitecore.models import Article, Category, MediaAsset, MediaAssetBinding, PageRoute, Site, SiteLocale, Tag

from .content_access import validate_current_content_write
from . import asset_upload_recovery
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


def _store_asset_stream(*,site:Site,name:str,size:int,chunks,title:str,alt_text:str,caption:str,created_by:str) -> MediaAsset:
    asset_type,suffix=classify_asset(name)
    max_bytes = ASSET_MAX_BYTES[asset_type]
    if size <= 0:
        raise forms.ValidationError('文件为空，无法入库。')
    if size > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise forms.ValidationError(f'文件过大。{asset_type} 当前限制为 {max_mb}MB。')

    storage_root = asset_storage_root()
    now=timezone.now()
    token=uuid.uuid4().hex
    directory_name=ASSET_DIR_BY_TYPE[asset_type]
    filename=f'{token[:16]}-upload-{token}{suffix}'
    target=_managed_asset_target(directory_name=directory_name,year=f'{now:%Y}',month=f'{now:%m}',filename=filename)
    marker=None
    keep_file=False
    try:
        with transaction.atomic():
            marker,data=asset_upload_recovery.begin_upload(root=storage_root,target=target,site_id=site.pk,token=token)
            digest=hashlib.sha256();written=0
            # Exclusive creation never overwrites a prior path, even on a token collision.
            with asset_upload_recovery.private_file(target,binary=True) as stream:
                asset_upload_recovery.confirm_created(marker,data)
                for chunk in chunks():
                    written+=len(chunk)
                    if written>max_bytes: raise forms.ValidationError('文件超过允许大小，未入库。')
                    digest.update(chunk);stream.write(chunk)
                stream.flush();os.fsync(stream.fileno())
            if written!=size: raise forms.ValidationError('文件内容发生变化或不完整，请重新选择文件。')
            target.chmod(0o644)
            sha256=digest.hexdigest()
            if connection.vendor=='postgresql':
                # Only same-site/same-content requests serialize; different assets remain independent.
                lock_id=int.from_bytes(hashlib.sha256(f'asset-content:{site.pk}:{sha256}'.encode()).digest()[:8],'big',signed=True)
                with connection.cursor() as cursor: cursor.execute('SELECT pg_advisory_xact_lock(%s)',[lock_id])
            existing=MediaAsset.objects.filter(site=site,sha256=sha256).first()
            if existing:
                resolve_storage_path(existing.storage_path)
                changed=[]
                if existing.status!='active': existing.status='active';changed.append('status')
                for field,value in (('title',title),('alt_text',alt_text),('caption',caption)):
                    if value and not getattr(existing,field): setattr(existing,field,value);changed.append(field)
                if changed: existing.save(update_fields=changed+['updated_at'])
                return existing
            relative=target.relative_to(storage_root).as_posix()
            row=MediaAsset.objects.create(site=site,asset_type=asset_type,original_name=Path(name).name,
                title=title or Path(name).stem,alt_text=alt_text,caption=caption,mime_type=asset_mime_type(name),
                file_ext=suffix,file_size_bytes=written,sha256=sha256,storage_path='storage/assets/'+relative,
                public_path='/assets/'+relative,status='active',created_by=created_by or 'system')
            keep_file=True
            transaction.on_commit(lambda selected=marker:asset_upload_recovery.clear_committed_marker(selected),robust=True)
            return row
    finally:
        if marker is not None and not keep_file and not connection.in_atomic_block:
            # An uncertain database outcome must keep its durable marker.
            # Reconciliation queries committed references before deleting our own file.
            try:
                asset_upload_recovery.reconcile_marker(root=storage_root,marker=marker,site_id=site.pk,token=token,execute=True)
            except Exception:
                asset_upload_recovery.logger.warning('Asset upload recovery remains pending after a failed write.')


def store_uploaded_asset(*, site: Site, uploaded_file, title: str, alt_text: str, caption: str, created_by: str) -> MediaAsset:
    return _store_asset_stream(site=site,name=uploaded_file.name,size=int(getattr(uploaded_file,'size',0) or 0),
        chunks=uploaded_file.chunks,title=title,alt_text=alt_text,caption=caption,created_by=created_by)


def store_local_asset(*, site: Site, source_path: Path, title: str, alt_text: str, caption: str, created_by: str) -> MediaAsset:
    source_path = resolve_import_source_path(source_path)
    def chunks():
        with source_path.open('rb') as handle:
            yield from iter(lambda:handle.read(1024*1024),b'')
    return _store_asset_stream(site=site,name=source_path.name,size=int(source_path.stat().st_size or 0),
        chunks=chunks,title=title,alt_text=alt_text,caption=caption,created_by=created_by)


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
    content_key = forms.RegexField(
        regex=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$', required=False, max_length=160,
        label='多语言文章关联标识',
        help_text='同一篇文章的各语言版本填写相同标识，例如 articleGuide。建立后保持不变；草稿可留空，保存为已发布前必须补齐。',
        error_messages={'invalid': '请使用小写字母开头的驼峰标识，只含英文字母和数字。'},
        widget=forms.TextInput(attrs={'dir': 'ltr', 'placeholder': 'articleGuide'}),
    )
    title = forms.CharField(
        label='文章标题',
        max_length=220,
        widget=forms.TextInput(attrs={'placeholder': '例如：How to prepare a project brief'}),
    )
    slug = forms.CharField(
        label='路径别名',
        max_length=180,
        required=False,
        help_text='用于生成访问路径，只允许小写英文、数字、下划线和短横线。',
        widget=forms.TextInput(attrs={'placeholder': 'project-brief-guide'}),
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
                'content_key': config_json.get('contentKey', ''),
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
        if article and (article.config_json or {}).get('contentKey'):
            # Identity is not a translated label. A normal body edit must not
            # detach this version from its sibling articles, even via POST.
            self.fields['content_key'].disabled = True
        for name in ('title', 'excerpt', 'body_markdown', 'meta_description', 'og_title', 'og_description'):
            self.fields[name].widget.attrs.update({
                'lang': locale.locale_code,
                'dir': 'rtl' if locale.locale_code == 'ar' else 'ltr',
            })
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
        supplied = self.cleaned_data.get('slug', '').strip()
        # A normal content edit must preserve an already-valid public slug.
        # Legacy normalization replaces hyphens, silently changing live URLs.
        slug = supplied if SLUG_RE.fullmatch(supplied) else normalize_slug(supplied, self.cleaned_data.get('title', ''))
        if not SLUG_RE.fullmatch(slug):
            raise forms.ValidationError('请使用小写英文、数字、下划线或短横线。')
        return slug

    def clean_content_key(self) -> str:
        key = self.cleaned_data.get('content_key', '')
        if key:
            if not self.fields['content_key'].disabled and not re.fullmatch(r'[a-z][A-Za-z0-9]{0,79}', key):
                raise forms.ValidationError('新标识请使用小写字母开头的驼峰命名，最多80字符。')
            rows = Article.objects.filter(
                route__site=self.site, route__locale=self.locale,
                config_json__contentKey=key,
            )
            if self.article:
                rows = rows.exclude(pk=self.article.pk)
            if rows.exists():
                raise forms.ValidationError('当前语言已有这个关联标识的文章，请编辑原文章，避免重复译文。')
        return key

    def clean(self) -> dict:
        cleaned = super().clean()
        slug = cleaned.get('slug')
        status = cleaned.get('status')
        published_at = cleaned.get('published_at')

        if status == 'published' and not self.allow_publish:
            self.add_error('status', '当前账号不能把文章设为已发布。')
        if status == 'published' and not cleaned.get('content_key') and 'content_key' not in self.errors:
            self.add_error('content_key', '发布前请填写多语言文章关联标识，否则无法生成文章网页。可先保存为草稿。')
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
        key = data.get('content_key')
        original_key = (self.article.config_json or {}).get('contentKey') if self.article else None
        if key and not original_key:
            # Only identity allocation needs this scoped transaction lock.
            # Normal edits keep an immutable key and do not take an extra lock.
            if connection.vendor == 'postgresql':
                lock_id = int.from_bytes(hashlib.sha256(
                    f'article-identity:{self.site.pk}:{self.locale.pk}:{key}'.encode('utf-8')
                ).digest()[:8], 'big', signed=True)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])
            existing = Article.objects.filter(
                route__site=self.site, route__locale=self.locale, config_json__contentKey=key,
            )
            if self.article:
                existing = existing.exclude(pk=self.article.pk)
            if existing.exists():
                raise forms.ValidationError({'content_key': '这个关联标识刚被同语言的另一篇文章使用，本次未保存。请刷新核对原文章。'})
        if self.article:
            original = self.article
            # Existing saves write the route before the article.  Keep that
            # lock order for native edits and ZIP imports alike.
            route = PageRoute.objects.select_for_update().filter(
                pk=original.route_id, site=self.site, locale=self.locale,
            ).first()
            validate_current_content_write(original.route, route, allow_publish=self.allow_publish)
            current = Article.objects.select_for_update().filter(
                pk=original.pk, route_id=original.route_id,
            ).first()
            validate_current_content_write(original, current, allow_publish=self.allow_publish)
            self.article = current
        else:
            route = PageRoute(site=self.site, locale=self.locale)
        status = data['status']
        published_at = data['published_at'] if status == 'published' else None
        path = f'/{self.locale.locale_code}/articles/{data["slug"]}/'
        excerpt = data.get('excerpt', '').strip()
        meta_description = data.get('meta_description', '').strip() or excerpt
        og_title = data.get('og_title', '').strip() or data['title']
        og_description = data.get('og_description', '').strip() or meta_description

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
        if data.get('content_key'):
            article.config_json['contentKey'] = data['content_key']
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
