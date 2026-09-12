from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from leads.crm_relationships import CRM_TARGET_FIELDS, validate_crm_relationship_targets
from leads.models import CrmAttachment


ALLOWED_ATTACHMENT_EXTENSIONS = {
    '.csv': 10 * 1024 * 1024,
    '.docx': 25 * 1024 * 1024,
    '.dwg': 50 * 1024 * 1024,
    '.dxf': 50 * 1024 * 1024,
    '.jpeg': 10 * 1024 * 1024,
    '.jpg': 10 * 1024 * 1024,
    '.pdf': 25 * 1024 * 1024,
    '.png': 10 * 1024 * 1024,
    '.txt': 5 * 1024 * 1024,
    '.webp': 10 * 1024 * 1024,
    '.xls': 25 * 1024 * 1024,
    '.xlsx': 25 * 1024 * 1024,
}


def attachment_storage_root() -> Path:
    configured = os.getenv('SITEOS_CRM_ATTACHMENT_STORAGE_ROOT', '').strip()
    if configured:
        return Path(configured).resolve()
    project_root = settings.BASE_DIR.parent.parent
    return (project_root / 'storage' / 'crm-attachments').resolve()


def _safe_original_name(value: str) -> str:
    normalized = str(value or '').replace('\\', '/')
    return normalized.rsplit('/', 1)[-1].strip() or 'attachment'


def _stored_filename(original_name: str, digest: str, suffix: str) -> str:
    stem = slugify(Path(original_name).stem, allow_unicode=False).replace('-', '_')
    stem = re.sub(r'[^a-z0-9._-]+', '_', stem.lower()).strip('._-') or 'attachment'
    return f'{digest[:20]}-{stem[:72]}{suffix}'


def resolve_private_attachment_path(storage_path: str) -> Path:
    relative = Path(str(storage_path or '').strip())
    if not str(relative) or relative.is_absolute() or '..' in relative.parts:
        raise ValidationError('附件存储路径无效。')
    root = attachment_storage_root()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValidationError('附件存储路径超出私有目录。')
    return candidate


def _delete_unreferenced_attachment_files(storage_paths: tuple[str, ...]) -> None:
    for storage_path in storage_paths:
        if CrmAttachment.objects.filter(storage_path=storage_path).exists():
            continue
        try:
            path = resolve_private_attachment_path(storage_path)
        except ValidationError:
            continue
        if path.is_file():
            path.unlink()


def retire_private_attachments(queryset, *, reason: str) -> int:
    """Remove personal file content while retaining a neutral audit row."""
    removed_title = {
        'privacy_request': 'Removed by privacy request',
        'retention_policy': 'Removed by retention policy',
    }.get(str(reason or '').strip(), 'Removed personal attachment')
    attachments = list(queryset.select_for_update().exclude(storage_path=''))
    storage_paths = tuple(sorted({item.storage_path for item in attachments if item.storage_path}))
    for attachment in attachments:
        attachment.status = 'archived'
        attachment.title = removed_title
        attachment.original_name = ''
        attachment.mime_type = ''
        attachment.file_size_bytes = 0
        attachment.sha256 = ''
        attachment.storage_path = ''
        attachment.save(update_fields=[
            'status', 'title', 'original_name', 'mime_type', 'file_size_bytes',
            'sha256', 'storage_path',
        ])
    if storage_paths:
        transaction.on_commit(
            lambda paths=storage_paths: _delete_unreferenced_attachment_files(paths)
        )
    return len(attachments)


@transaction.atomic
def store_crm_attachment(
    *,
    site,
    uploaded_file,
    title: str,
    uploaded_by,
    target: dict,
) -> CrmAttachment:
    unexpected_targets = set(target) - set(CRM_TARGET_FIELDS)
    if unexpected_targets:
        raise ValidationError('附件关联对象类型无效。')
    validate_crm_relationship_targets(
        site=site,
        **{field_name: target.get(field_name) for field_name in CRM_TARGET_FIELDS},
    )
    original_name = _safe_original_name(getattr(uploaded_file, 'name', ''))
    suffix = Path(original_name).suffix.lower()
    max_bytes = ALLOWED_ATTACHMENT_EXTENSIONS.get(suffix)
    if max_bytes is None:
        raise ValidationError(
            '只允许上传 PDF、DOCX、XLS/XLSX、CSV、TXT、DWG/DXF、JPG、PNG 或 WebP。'
        )
    size = int(getattr(uploaded_file, 'size', 0) or 0)
    if size <= 0:
        raise ValidationError('文件为空，无法上传。')
    if size > max_bytes:
        raise ValidationError(f'文件超过 {max_bytes // (1024 * 1024)}MB 限制。')

    root = attachment_storage_root()
    root.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    created_path: Path | None = None
    try:
        digest = hashlib.sha256()
        with NamedTemporaryFile(delete=False, dir=root) as temporary:
            temp_path = Path(temporary.name)
            for chunk in uploaded_file.chunks():
                digest.update(chunk)
                temporary.write(chunk)
        sha256 = digest.hexdigest()

        existing = CrmAttachment.objects.filter(
            site=site,
            sha256=sha256,
        ).exclude(storage_path='').order_by('-created_at').first()
        if existing:
            storage_path = existing.storage_path
            existing_path = resolve_private_attachment_path(storage_path)
            if not existing_path.exists():
                existing_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(temp_path), existing_path)
                temp_path = None
                created_path = existing_path
        else:
            now = timezone.now()
            relative = Path(f'{now:%Y}') / f'{now:%m}' / _stored_filename(
                original_name,
                sha256,
                suffix,
            )
            target_path = resolve_private_attachment_path(relative.as_posix())
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp_path), target_path)
            temp_path = None
            created_path = target_path
            storage_path = relative.as_posix()

        mime_type = (
            getattr(uploaded_file, 'content_type', '')
            or mimetypes.guess_type(original_name)[0]
            or 'application/octet-stream'
        )
        attachment = CrmAttachment.objects.create(
            site=site,
            uploaded_by_user=uploaded_by,
            title=str(title or '').strip() or Path(original_name).stem,
            original_name=original_name,
            mime_type=mime_type,
            file_size_bytes=size,
            sha256=sha256,
            storage_path=storage_path,
            status='active',
            **target,
        )
        return attachment
    except Exception:
        if created_path and created_path.exists():
            created_path.unlink()
        raise
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
