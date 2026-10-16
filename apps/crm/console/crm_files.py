from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import sys
from pathlib import Path
from collections import defaultdict

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from leads.crm_relationships import CRM_TARGET_FIELDS, validate_crm_relationship_targets
from leads.models import CrmAttachment
from . import crm_upload_recovery
from .storage_security import StorageSecurityError

logger = logging.getLogger(__name__)


def reconcile_pending_uploads(*, site_id: int, limit: int = 100, after=''):
    return crm_upload_recovery.reconcile_pending_uploads(
        root=attachment_storage_root(), site_id=site_id, limit=limit, after=after)


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


def resolve_private_attachment_path(storage_path: str) -> Path:
    relative = Path(str(storage_path or '').strip())
    if not str(relative) or relative.is_absolute() or '..' in relative.parts:
        raise ValidationError('附件存储路径无效。')
    root = attachment_storage_root()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValidationError('附件存储路径超出私有目录。')
    return candidate


@transaction.atomic
def _cleanup_private_attachment(attachment_id: int, site_id: int) -> str:
    pending = CrmAttachment.objects.filter(pk=attachment_id, site_id=site_id).exclude(purge_storage_path='')
    row = pending.select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked).first()
    if row is None:
        return 'skipped'
    code = ''
    if CrmAttachment.objects.filter(storage_path=row.purge_storage_path).exists():
        code = 'shared'
    else:
        try:
            path = crm_upload_recovery.resolve_cleanup_file(
                root=attachment_storage_root(), storage_path=row.purge_storage_path)
            if path is not None:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            # A previously committed delete or another cleanup already won.
            pass
        except ValidationError:
            code = 'invalid_path'
        except StorageSecurityError as error:
            code = 'filesystem' if error.code == 'file_unavailable' else 'invalid_path'
        except OSError:
            code = 'filesystem'
    row.purge_attempts += 1
    row.purge_last_attempt_at = timezone.now()
    row.purge_error_code = code
    if not code:
        row.purge_storage_path = ''
    row.save(update_fields=['purge_storage_path', 'purge_attempts', 'purge_error_code', 'purge_last_attempt_at'])
    return 'pending' if code else 'removed'


def process_private_attachment_cleanup(*, site_id: int, limit: int = 100, attachment_ids=None) -> dict:
    """Bounded, idempotent retry of already authorized logical retirements."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValidationError('每次附件清理数量必须为1到100。')
    pending = CrmAttachment.objects.filter(site_id=site_id).exclude(purge_storage_path='')
    if attachment_ids is not None:
        pending = pending.filter(pk__in=attachment_ids)
    ids = list(pending.order_by(F('purge_last_attempt_at').asc(nulls_first=True), 'pk').values_list('pk', flat=True)[:limit])
    result = {'processed': 0, 'removed': 0, 'pending': 0, 'skipped': 0}
    for attachment_id in ids:
        status = _cleanup_private_attachment(attachment_id, site_id)
        result['processed'] += 1
        result[status] += 1
    return result


@transaction.atomic
def retire_private_attachments(queryset, *, reason: str) -> int:
    """Remove personal file content while retaining a neutral audit row."""
    removed_title = {
        'privacy_request': 'Removed by privacy request',
        'retention_policy': 'Removed by retention policy',
    }.get(str(reason or '').strip(), 'Removed personal attachment')
    attachments = list(queryset.select_for_update().exclude(storage_path=''))
    pending_by_site = defaultdict(list)
    for attachment in attachments:
        attachment.status = 'archived'
        attachment.title = removed_title
        attachment.original_name = ''
        attachment.mime_type = ''
        attachment.file_size_bytes = 0
        attachment.sha256 = ''
        attachment.purge_storage_path = attachment.storage_path
        attachment.purge_attempts = 0
        attachment.purge_error_code = ''
        attachment.purge_last_attempt_at = None
        attachment.storage_path = ''
        attachment.save(update_fields=[
            'status', 'title', 'original_name', 'mime_type', 'file_size_bytes',
            'sha256', 'storage_path', 'purge_storage_path', 'purge_attempts',
            'purge_error_code', 'purge_last_attempt_at',
        ])
        pending_by_site[attachment.site_id].append(attachment.pk)
    for site_id, ids in pending_by_site.items():
        transaction.on_commit(
            lambda site=site_id, selected=tuple(ids): process_private_attachment_cleanup(
                site_id=site, attachment_ids=selected), robust=True,
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
    require_new_blob: bool = False,
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
    now = timezone.now()
    directory = resolve_private_attachment_path(f'{now:%Y}/{now:%m}')
    directory.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    marker = None
    try:
        digest = hashlib.sha256()
        candidate, marker, marker_data = crm_upload_recovery.allocate_upload(
            root=root, directory=directory, site_id=site.pk, suffix=suffix)
        with crm_upload_recovery.private_file(candidate, binary=True) as temporary:
            temp_path = candidate
            crm_upload_recovery.confirm_created(marker, marker_data)
            for chunk in uploaded_file.chunks():
                digest.update(chunk)
                temporary.write(chunk)
        sha256 = digest.hexdigest()
        storage_path = temp_path.relative_to(root).as_posix()

        existing = CrmAttachment.objects.filter(
            site=site,
            sha256=sha256,
        ).exclude(storage_path='').select_for_update().order_by('-created_at').first()
        if existing:
            # Optional synthetic packs must not adopt/repair an operator's
            # existing file. Normal uploads retain their deduplication behavior.
            if require_new_blob:
                raise ValidationError('Untracked existing private attachment content conflicts; no data was overwritten.')
            storage_path = existing.storage_path
            existing_path = resolve_private_attachment_path(storage_path)
            if not existing_path.exists():
                existing_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(temp_path, existing_path)
                except FileExistsError:
                    # Another upload already restored this shared legacy blob.
                    pass
            if not existing_path.is_file():
                raise ValidationError('附件存储文件不可用。')

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
        if storage_path == temp_path.relative_to(root).as_posix():
            # The new row owns this file. Shared/repaired legacy paths never
            # enter failure cleanup; only this call's exclusive temp does.
            temp_path = None
            def clear_marker(selected=marker):
                try:
                    crm_upload_recovery.clear_upload_marker(selected)
                except OSError:
                    logger.warning('Committed upload marker cleanup remains pending.')
            transaction.on_commit(clear_marker, robust=True)
            marker = None
        return attachment
    finally:
        if temp_path:
            original_failure = sys.exc_info()[0] is not None
            try:
                temp_path.unlink(missing_ok=True)
                if marker is not None:
                    crm_upload_recovery.clear_upload_marker(marker)
                    marker = None
            except OSError:
                if not original_failure:
                    raise
                # Preserve the actual upload failure. Durable orphan recovery
                # is separate from the committed retirement cleanup markers.
                logger.warning('Private upload cleanup remains pending after a failed upload.')
        elif marker is not None:
            # Allocation failed before this call created any bytes. Do not
            # touch a colliding existing blob; remove only our owned marker.
            try:
                crm_upload_recovery.clear_upload_marker(marker)
            except OSError:
                logger.warning('Unconfirmed upload allocation remains pending.')
