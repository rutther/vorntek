"""Small private upload journals bridge database rollback and owned files."""
import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import stat
import uuid

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q

from leads.models import CrmAttachment
from .storage_security import StorageSecurityError, resolve_storage_asset_path

JOURNAL_DIRECTORY = '.pending-uploads'
MARKER_RE = re.compile(r'([1-9][0-9]*)-([0-9a-f]{32})\.json\Z')
PATH_RE = re.compile(r'[0-9]{4}/(?:0[1-9]|1[0-2])/upload-([0-9a-f]{32})(\.[a-z0-9]+)\Z')
ALLOWED_SUFFIXES = frozenset({'.csv', '.docx', '.dwg', '.dxf', '.jpeg', '.jpg', '.pdf', '.png',
                            '.txt', '.webp', '.xls', '.xlsx'})


def private_file(path, *, binary=False):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        return os.fdopen(descriptor, 'wb' if binary else 'w', **({} if binary else {'encoding': 'utf-8'}))
    except BaseException:
        os.close(descriptor)
        raise


def _lock_id(site_id, token):
    return int.from_bytes(hashlib.sha256(f'crm-upload:{site_id}:{token}'.encode()).digest()[:8], 'big', signed=True)


def _lock(site_id, token, *, try_only=False):
    if connection.vendor != 'postgresql':
        return not try_only  # SQLite UI/tests never perform orphan deletion.
    with connection.cursor() as cursor:
        function = 'pg_try_advisory_xact_lock' if try_only else 'pg_advisory_xact_lock'
        cursor.execute('SELECT ' + function + '(%s)', [_lock_id(site_id, token)])
        value = cursor.fetchone()[0]
    return bool(value) if try_only else True


def _direct_directory(directory):
    metadata = directory.lstat()
    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        raise ValidationError('上传恢复目录不可使用间接路径。')


def _write_marker(marker, data, *, exclusive=False):
    if exclusive:
        with private_file(marker) as stream:
            json.dump(data, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = marker.with_name(f'.{marker.stem}-{uuid.uuid4().hex}.tmp')
    try:
        with private_file(temporary) as stream:
            json.dump(data, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def allocate_upload(*, root: Path, directory: Path, site_id: int, suffix: str):
    token = uuid.uuid4().hex
    relative = (directory / f'upload-{token}{suffix}').relative_to(root).as_posix()
    if not PATH_RE.fullmatch(relative) or suffix not in ALLOWED_SUFFIXES:
        raise ValidationError('上传恢复路径无效。')
    _lock(site_id, token)
    # Validate every generated parent before writing the marker or blob.
    _direct_directory(directory.parent)
    _direct_directory(directory)
    journal = root / JOURNAL_DIRECTORY
    journal.mkdir(exist_ok=True, mode=0o700)
    _direct_directory(journal)
    marker = journal / f'{site_id}-{token}.json'
    data = {'version': 1, 'site_id': site_id, 'token': token, 'storage_path': relative, 'created': False}
    _write_marker(marker, data, exclusive=True)
    return root / relative, marker, data


def confirm_created(marker, data):
    _write_marker(marker, {**data, 'created': True})


def clear_upload_marker(marker):
    marker.unlink(missing_ok=True)


def resolve_cleanup_file(*, root: Path, storage_path: str) -> Path | None:
    """Validate every component before treating a cleanup target as absent."""
    relative = Path(str(storage_path or '').strip())
    if relative.is_absolute() or '..' in relative.parts:
        raise StorageSecurityError('outside_allowlist')
    # Absolute lexical form avoids the public asset resolver's logical-prefix
    # mapping; private attachment paths must identify exactly their stored file.
    candidate = root / relative
    try:
        return resolve_storage_asset_path(candidate, roots=[root])
    except StorageSecurityError as error:
        if error.code != 'file_unavailable':
            raise
        try:
            candidate.lstat()
        except FileNotFoundError:
            return None
        raise


def pending_upload_count(*, root: Path, site_id: int):
    journal = root / JOURNAL_DIRECTORY
    if not journal.exists():
        return 0
    _direct_directory(journal)
    with os.scandir(journal) as entries:
        return sum(1 for entry in entries if (match := MARKER_RE.fullmatch(entry.name)) and int(match[1]) == site_id)


@transaction.atomic
def _reconcile_marker(root, marker, site_id, token):
    if not _lock(site_id, token, try_only=True):
        return 'busy'
    try:
        safe_marker = resolve_storage_asset_path(marker.relative_to(root).as_posix(), roots=[root])
        if safe_marker.stat().st_size > 4096:
            return 'invalid'
        data = json.loads(safe_marker.read_text(encoding='utf-8'))
        relative = data.get('storage_path', '') if isinstance(data, dict) else ''
        path_match = PATH_RE.fullmatch(relative) if isinstance(relative, str) else None
        if (not isinstance(data, dict) or type(data.get('version')) is not int or data.get('version') != 1 or
                type(data.get('site_id')) is not int or data.get('site_id') != site_id or
                data.get('token') != token or not isinstance(data.get('created'), bool) or not path_match or
                path_match[1] != token or path_match[2] not in ALLOWED_SUFFIXES):
            return 'invalid'
        if CrmAttachment.objects.filter(Q(storage_path=relative) | Q(purge_storage_path=relative)).exists():
            clear_upload_marker(safe_marker)
            return 'referenced'
        safe_blob = resolve_cleanup_file(root=root, storage_path=relative)
        if safe_blob is not None:
            if not data['created']:
                return 'uncertain'
            safe_blob.unlink(missing_ok=True)
        clear_upload_marker(safe_marker)
        return 'removed'
    except FileNotFoundError:
        return 'skipped'
    except StorageSecurityError as error:
        if error.code == 'file_unavailable':
            try:
                marker.lstat()
            except FileNotFoundError:
                return 'skipped'
        return 'invalid'
    except (UnicodeError, json.JSONDecodeError, ValidationError):
        return 'invalid'
    except OSError:
        return 'failed'


def validate_upload_recovery_options(*, site_id: int, limit=100, after=''):
    if type(site_id) is not int or site_id <= 0:
        raise ValidationError('上传恢复站点无效。')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValidationError('每次上传恢复数量必须为1到100。')
    if not isinstance(after, str) or (after and (not (match := MARKER_RE.fullmatch(after)) or int(match[1]) != site_id)):
        raise ValidationError('上传恢复游标无效。')


def reconcile_pending_uploads(*, root: Path, site_id: int, limit=100, after=''):
    validate_upload_recovery_options(site_id=site_id, limit=limit, after=after)
    result = {key: 0 for key in ('processed', 'removed', 'referenced', 'busy', 'invalid', 'uncertain', 'failed', 'skipped')}
    result['next_cursor'] = ''
    journal = root / JOURNAL_DIRECTORY
    if not journal.exists():
        return result
    _direct_directory(journal)
    with os.scandir(journal) as entries:
        names = heapq.nsmallest(limit + 1, (entry.name for entry in entries
            if entry.name > after and (match := MARKER_RE.fullmatch(entry.name)) and int(match[1]) == site_id))
    for name in names[:limit]:
        token = MARKER_RE.fullmatch(name)[2]
        status = _reconcile_marker(root, journal / name, site_id, token)
        result['processed'] += 1
        result[status] += 1
    if len(names) > limit:
        result['next_cursor'] = names[limit - 1]
    return result
