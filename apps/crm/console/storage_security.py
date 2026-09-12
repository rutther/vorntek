from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Final

from django.conf import settings


STORAGE_ROOTS_SETTING: Final = 'SITEOS_ASSET_STORAGE_ROOTS'
LEGACY_STORAGE_ROOT_SETTING: Final = 'SITEOS_ASSET_STORAGE_ROOT'
IMPORT_ROOTS_SETTING: Final = 'SITEOS_ASSET_IMPORT_ROOTS'

_MISSING = object()
_LOGICAL_STORAGE_PREFIX: Final = ('storage', 'assets')
_WINDOWS_RESERVED_NAMES: Final = {
    'CON',
    'PRN',
    'AUX',
    'NUL',
    *(f'COM{number}' for number in range(1, 10)),
    *(f'LPT{number}' for number in range(1, 10)),
}
_PATH_PARTS_RE: Final = re.compile(r'[\\/]+')


class StorageSecurityError(Exception):
    """A fail-closed path validation error safe to show as a generic 404.

    The exception deliberately stores only a stable error code and a generic
    public message.  Candidate paths and allowlisted host paths must never be
    placed in the exception, because these errors can cross an HTTP boundary.
    """

    public_message = '请求的文件不可用。'

    def __init__(self, code: str):
        self.code = code
        super().__init__(self.public_message)

    def __repr__(self) -> str:
        return f'{type(self).__name__}(code={self.code!r})'


@dataclass(frozen=True, slots=True)
class FileResponseSecurityPolicy:
    """Headers for authenticated reads of files from private storage."""

    cache_control: str = 'no-store'
    content_type_options: str = 'nosniff'

    def as_headers(self) -> dict[str, str]:
        return {
            'Cache-Control': self.cache_control,
            'X-Content-Type-Options': self.content_type_options,
        }


PRIVATE_FILE_RESPONSE_POLICY: Final = FileResponseSecurityPolicy()


def private_file_response_headers() -> dict[str, str]:
    """Return a fresh header mapping suitable for an authenticated file read."""

    return PRIVATE_FILE_RESPONSE_POLICY.as_headers()


def configured_storage_roots() -> tuple[Path, ...]:
    """Return explicitly configured asset-storage roots.

    No project-relative default is inferred.  The plural setting is
    authoritative when present; only when it is entirely absent may the
    explicitly configured legacy singular setting be used.  With neither
    setting, validation remains closed.
    """

    plural_is_explicit, configured = _explicit_configuration(STORAGE_ROOTS_SETTING)
    if plural_is_explicit:
        return _validated_roots(_coerce_configured_roots(configured))

    legacy_is_explicit, legacy_root = _explicit_configuration(LEGACY_STORAGE_ROOT_SETTING)
    if legacy_is_explicit:
        return _validated_roots(_coerce_configured_roots(legacy_root))
    raise StorageSecurityError('allowlist_not_configured')


def configured_import_roots() -> tuple[Path, ...]:
    """Return explicitly configured roots from which local imports may read."""

    return _configured_roots(IMPORT_ROOTS_SETTING)


def resolve_storage_asset_path(
    storage_path: str | os.PathLike[str],
    *,
    roots: Iterable[str | os.PathLike[str]] | str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve a stored asset path inside an explicit storage allowlist.

    Database values in the existing ``storage/assets/...`` logical form are
    mapped relative to each configured storage root.  A root-relative value is
    also accepted.  An absolute value is accepted only when it is already
    contained by an allowlisted root.  The returned path exists, is a regular
    file, and contains no symlink, junction, or other reparse-point hop below
    its allowlisted root.
    """

    allowed_roots = _validated_roots(_coerce_roots(roots)) if roots is not None else configured_storage_roots()
    raw = _candidate_text(storage_path)
    _validate_candidate_syntax(raw)

    candidate = Path(raw)
    if _is_host_absolute(raw):
        return _resolve_absolute_candidate(candidate, allowed_roots)
    if _has_foreign_or_anchored_absolute_syntax(raw):
        raise StorageSecurityError('unsupported_absolute_path')

    parts = tuple(part for part in _PATH_PARTS_RE.split(raw) if part)
    if tuple(part.casefold() for part in parts[:2]) == _LOGICAL_STORAGE_PREFIX:
        parts = parts[2:]
    if not parts:
        raise StorageSecurityError('not_a_file')
    return _resolve_relative_candidate(parts, allowed_roots)


def resolve_import_source_path(
    source_path: str | os.PathLike[str],
    *,
    roots: Iterable[str | os.PathLike[str]] | str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve an existing absolute local-import source under its allowlist."""

    allowed_roots = _validated_roots(_coerce_roots(roots)) if roots is not None else configured_import_roots()
    raw = _candidate_text(source_path)
    _validate_candidate_syntax(raw)
    if not _is_host_absolute(raw):
        raise StorageSecurityError('absolute_path_required')
    return _resolve_absolute_candidate(Path(raw), allowed_roots)


def _configured_roots(setting_name: str) -> tuple[Path, ...]:
    is_explicit, configured = _explicit_configuration(setting_name)
    if not is_explicit:
        raise StorageSecurityError('allowlist_not_configured')
    return _validated_roots(_coerce_configured_roots(configured))


def _explicit_configuration(setting_name: str) -> tuple[bool, object]:
    configured = getattr(settings, setting_name, _MISSING)
    if configured is not _MISSING:
        return True, configured
    if setting_name in os.environ:
        return True, os.environ[setting_name]
    return False, ''


def _coerce_configured_roots(value: object) -> tuple[str | os.PathLike[str], ...]:
    if isinstance(value, str) and value:
        value = tuple(part.strip() for part in value.split(os.pathsep) if part.strip())
    return _coerce_roots(value)


def _coerce_roots(value: object) -> tuple[str | os.PathLike[str], ...]:
    if value is None or value == '':
        return ()
    if isinstance(value, (str, os.PathLike)):
        return (value,)
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise StorageSecurityError('invalid_allowlist') from None


def _validated_roots(
    roots: Iterable[str | os.PathLike[str]],
) -> tuple[Path, ...]:
    root_values = tuple(roots)
    if not root_values:
        raise StorageSecurityError('allowlist_not_configured')

    resolved_roots: list[Path] = []
    for root_value in root_values:
        raw = _candidate_text(root_value)
        _validate_candidate_syntax(raw)
        if not _is_host_absolute(raw):
            raise StorageSecurityError('allowlist_root_not_absolute')

        lexical_root = Path(raw)
        if _is_link_or_reparse_point(lexical_root):
            raise StorageSecurityError('allowlist_root_is_indirect')
        try:
            root = lexical_root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise StorageSecurityError('allowlist_root_unavailable') from None
        if not root.is_dir():
            raise StorageSecurityError('allowlist_root_not_directory')
        if root == root.parent:
            raise StorageSecurityError('allowlist_root_too_broad')
        if not any(_same_path(root, existing) for existing in resolved_roots):
            resolved_roots.append(root)

    if not resolved_roots:
        raise StorageSecurityError('allowlist_not_configured')
    return tuple(resolved_roots)


def _resolve_absolute_candidate(candidate: Path, roots: tuple[Path, ...]) -> Path:
    matching_roots = tuple(root for root in roots if _lexically_within(candidate, root))
    if not matching_roots:
        raise StorageSecurityError('outside_allowlist')

    failures: list[StorageSecurityError] = []
    for root in matching_roots:
        try:
            return _resolve_file_under_root(candidate, root)
        except StorageSecurityError as exc:
            failures.append(exc)
    raise failures[0]


def _resolve_relative_candidate(parts: tuple[str, ...], roots: tuple[Path, ...]) -> Path:
    found: list[Path] = []
    indirect_failure = False
    for root in roots:
        candidate = root.joinpath(*parts)
        try:
            resolved = _resolve_file_under_root(candidate, root)
        except StorageSecurityError as exc:
            if exc.code == 'indirect_path_not_allowed':
                indirect_failure = True
            continue
        if not any(_same_path(resolved, existing) for existing in found):
            found.append(resolved)

    if len(found) > 1:
        raise StorageSecurityError('ambiguous_path')
    if found:
        return found[0]
    if indirect_failure:
        raise StorageSecurityError('indirect_path_not_allowed')
    raise StorageSecurityError('file_unavailable')


def _resolve_file_under_root(candidate: Path, root: Path) -> Path:
    if not _lexically_within(candidate, root):
        raise StorageSecurityError('outside_allowlist')
    _reject_indirect_components(candidate, root)

    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise StorageSecurityError('file_unavailable') from None
    if not _is_within(resolved, root):
        raise StorageSecurityError('outside_allowlist')
    if not resolved.is_file():
        raise StorageSecurityError('not_a_file')
    return resolved


def _reject_indirect_components(candidate: Path, root: Path) -> None:
    try:
        relative = Path(os.path.relpath(candidate, root))
    except (OSError, ValueError):
        raise StorageSecurityError('outside_allowlist') from None

    current = root
    for part in relative.parts:
        if part in ('', '.'):
            continue
        if part == '..':
            raise StorageSecurityError('outside_allowlist')
        current = current / part
        if _is_link_or_reparse_point(current):
            raise StorageSecurityError('indirect_path_not_allowed')


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        raise StorageSecurityError('file_unavailable') from None

    if stat.S_ISLNK(metadata.st_mode):
        return True
    file_attributes = getattr(metadata, 'st_file_attributes', 0)
    reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    return bool(file_attributes and reparse_flag and file_attributes & reparse_flag)


def _candidate_text(value: str | os.PathLike[str]) -> str:
    try:
        raw = os.fspath(value)
    except TypeError:
        raise StorageSecurityError('invalid_path') from None
    if not isinstance(raw, str):
        raise StorageSecurityError('invalid_path')
    if not raw or raw != raw.strip() or '\x00' in raw:
        raise StorageSecurityError('invalid_path')
    return raw


def _validate_candidate_syntax(raw: str) -> None:
    if raw.startswith(('\\\\?\\', '\\\\.\\')):
        raise StorageSecurityError('unsupported_absolute_path')

    parts = tuple(part for part in _PATH_PARTS_RE.split(raw) if part)
    if any(part in {'.', '..'} for part in parts):
        raise StorageSecurityError('parent_reference_not_allowed')

    windows_path = PureWindowsPath(raw)
    drive = windows_path.drive
    for index, part in enumerate(parts):
        if index == 0 and drive and part.casefold() == drive.casefold():
            continue
        if ':' in part:
            raise StorageSecurityError('alternate_stream_not_allowed')
        if part.endswith((' ', '.')):
            raise StorageSecurityError('ambiguous_windows_path')
        base_name = part.rstrip(' .').split('.', 1)[0].upper()
        if base_name in _WINDOWS_RESERVED_NAMES:
            raise StorageSecurityError('reserved_windows_name')


def _is_host_absolute(raw: str) -> bool:
    return Path(raw).is_absolute()


def _has_foreign_or_anchored_absolute_syntax(raw: str) -> bool:
    windows_path = PureWindowsPath(raw)
    return bool(windows_path.drive or windows_path.root)


def _lexically_within(candidate: Path, root: Path) -> bool:
    try:
        relative = os.path.relpath(candidate, root)
    except (OSError, ValueError):
        return False
    relative_parts = Path(relative).parts
    return relative != os.pardir and (not relative_parts or relative_parts[0] != os.pardir)


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((os.fspath(candidate), os.fspath(root)))
    except (OSError, ValueError):
        return False
    return os.path.normcase(common) == os.path.normcase(os.fspath(root))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(left)) == os.path.normcase(os.fspath(right))
