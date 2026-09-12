from __future__ import annotations

import hashlib
import io
import os
import re
import secrets
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from sitecore.models import MediaAsset, Release, ReleaseBuild, Site

from .storage_security import StorageSecurityError, resolve_storage_asset_path


DEFAULT_RELEASE_KEY = 'dev-current'
PREVIEW_WORKSPACE_SETTING = 'SITEOS_PREVIEW_WORKSPACE_ROOT'
_SAFE_IDENTIFIER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_SAFE_PUBLIC_PART_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$')
_SHA256_RE = re.compile(r'^[a-f0-9]{64}$', re.IGNORECASE)
_MISSING_ASSET_CODES = frozenset({'file_unavailable', 'not_a_file'})
_BUILD_OS_ENV_ALLOWLIST = frozenset(
    {
        'COMSPEC',
        'LANG',
        'LC_ALL',
        'PATHEXT',
        'SYSTEMROOT',
        'TEMP',
        'TMP',
        'TMPDIR',
        'WINDIR',
    }
)
_SENSITIVE_ENV_KEY_RE = re.compile(
    r'(?:API[_-]?KEY|CREDENTIAL|DATABASE|DSN|PASSWORD|PRIVATE|SECRET|TOKEN|VAULT)',
    re.IGNORECASE,
)


class PreviewBuildError(Exception):
    """A fail-closed preview error that never contains a host path."""

    public_message = '预览构建无法安全执行。'

    def __init__(self, code: str):
        self.code = code
        super().__init__(self.public_message)

    def __repr__(self) -> str:
        return f'{type(self).__name__}(code={self.code!r})'


class PreviewBuildConfigurationError(PreviewBuildError):
    public_message = '预览构建环境尚未安全配置。'


class PreviewBuildInProgress(PreviewBuildError):
    public_message = '该站点已有预览构建正在进行。'


@dataclass(frozen=True)
class BuildEnvironment:
    strategy: str
    ready: bool
    details: list[str]


@dataclass(frozen=True)
class BuildResult:
    release: Release
    build: ReleaseBuild
    succeeded: bool
    log_excerpt: str


def _configured_value(name: str) -> str:
    configured = getattr(settings, name, '')
    if configured in (None, ''):
        configured = os.getenv(name, '')
    return os.fspath(configured).strip() if configured not in (None, '') else ''


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        raise PreviewBuildConfigurationError('path_unavailable') from None

    if stat.S_ISLNK(metadata.st_mode):
        return True
    file_attributes = getattr(metadata, 'st_file_attributes', 0)
    reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    return bool(file_attributes and reparse_flag and file_attributes & reparse_flag)


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath((os.fspath(candidate), os.fspath(root)))
    except (OSError, ValueError):
        return False
    return os.path.normcase(common) == os.path.normcase(os.fspath(root))


def _safe_site_code(site_code: str) -> str:
    if not isinstance(site_code, str) or not _SAFE_IDENTIFIER_RE.fullmatch(site_code):
        raise PreviewBuildConfigurationError('invalid_site_code')
    return site_code


def preview_workspace_root() -> Path:
    """Return the explicitly configured, non-indirect preview workspace root."""

    configured = _configured_value(PREVIEW_WORKSPACE_SETTING)
    if not configured:
        raise PreviewBuildConfigurationError('workspace_not_configured')

    lexical_root = Path(configured)
    if not lexical_root.is_absolute():
        raise PreviewBuildConfigurationError('workspace_not_absolute')
    if _is_link_or_reparse_point(lexical_root):
        raise PreviewBuildConfigurationError('workspace_is_indirect')
    try:
        root = lexical_root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise PreviewBuildConfigurationError('workspace_unavailable') from None
    if not root.is_dir() or root == root.parent:
        raise PreviewBuildConfigurationError('workspace_invalid')
    return root


def _secure_path_under(root: Path, *parts: str, require_directory: bool = False) -> Path:
    candidate = root.joinpath(*parts)
    try:
        relative = Path(os.path.relpath(candidate, root))
    except (OSError, ValueError):
        raise PreviewBuildConfigurationError('path_outside_workspace') from None
    if relative == Path('..') or (relative.parts and relative.parts[0] == '..'):
        raise PreviewBuildConfigurationError('path_outside_workspace')

    current = root
    for part in relative.parts:
        if part in ('', '.'):
            continue
        if part == '..':
            raise PreviewBuildConfigurationError('path_outside_workspace')
        current = current / part
        if _is_link_or_reparse_point(current):
            raise PreviewBuildConfigurationError('indirect_path_not_allowed')

    try:
        resolved = candidate.resolve(strict=require_directory)
    except (OSError, RuntimeError):
        raise PreviewBuildConfigurationError('path_unavailable') from None
    if not _is_within(resolved, root):
        raise PreviewBuildConfigurationError('path_outside_workspace')
    if require_directory and not resolved.is_dir():
        raise PreviewBuildConfigurationError('directory_unavailable')
    return resolved


def site_preview_root(site_code: str) -> Path:
    return _secure_path_under(preview_workspace_root(), _safe_site_code(site_code), require_directory=True)


def web_app_dir(site_code: str) -> Path:
    return _secure_path_under(site_preview_root(site_code), 'apps', 'web', require_directory=True)


def web_dist_dir(site_code: str) -> Path:
    return _secure_path_under(web_app_dir(site_code), 'dist')


def web_public_dir(site_code: str) -> Path:
    return _secure_path_under(web_app_dir(site_code), 'public')


def _secure_web_file(site_code: str, *parts: str) -> Path:
    """Resolve a web-app file while rejecting every indirect path component."""

    return _secure_path_under(web_app_dir(site_code), *parts)


def _astro_entry_path(site_code: str, *, require_file: bool) -> Path:
    entry = _secure_web_file(site_code, 'node_modules', 'astro', 'bin', 'astro.mjs')
    if require_file and not entry.is_file():
        raise PreviewBuildConfigurationError('astro_runtime_unavailable')
    return entry


def snapshot_output_dir(site_code: str) -> Path:
    return _secure_path_under(web_app_dir(site_code), 'release', 'current')


def secure_snapshot_output_dir(raw_path: str | os.PathLike[str], *, site_code: str) -> Path:
    """Validate an explicitly supplied export directory inside one site's preview tree."""

    raw = os.fspath(raw_path).strip() if raw_path not in (None, '') else ''
    if not raw:
        raise PreviewBuildConfigurationError('snapshot_output_not_configured')
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise PreviewBuildConfigurationError('snapshot_output_not_absolute')

    root = site_preview_root(site_code)
    try:
        relative = Path(os.path.relpath(candidate, root))
    except (OSError, ValueError):
        raise PreviewBuildConfigurationError('snapshot_output_outside_site') from None
    if relative in (Path('.'), Path('..')) or (relative.parts and relative.parts[0] == '..'):
        raise PreviewBuildConfigurationError('snapshot_output_outside_site')
    return _secure_path_under(root, *relative.parts)


def prepare_snapshot_output_dir(raw_path: str | os.PathLike[str], *, site_code: str) -> Path:
    output_dir = secure_snapshot_output_dir(raw_path, site_code=site_code)
    root = site_preview_root(site_code)
    _ensure_directory(output_dir, root=root)
    return secure_snapshot_output_dir(output_dir, site_code=site_code)


def prepare_snapshot_file_path(
    raw_path: str | os.PathLike[str],
    *,
    output_dir: str | os.PathLike[str],
    site_code: str,
) -> Path:
    """Resolve one snapshot file without following indirect output children.

    ``prepare_snapshot_output_dir`` secures the caller-selected output root, but
    snapshot writers also create nested paths such as ``articles/index.json``.
    Validate every one of those components against the already site-scoped
    output root before opening a temporary file or replacing the destination.
    """

    output_root = secure_snapshot_output_dir(output_dir, site_code=site_code)
    if not output_root.is_dir():
        raise PreviewBuildConfigurationError('snapshot_output_unavailable')

    raw = os.fspath(raw_path).strip() if raw_path not in (None, '') else ''
    if not raw:
        raise PreviewBuildConfigurationError('snapshot_file_invalid')
    candidate = Path(raw)
    if not candidate.is_absolute() or any(part in {'.', '..'} for part in candidate.parts):
        raise PreviewBuildConfigurationError('snapshot_file_invalid')

    try:
        relative = Path(os.path.relpath(candidate, output_root))
    except (OSError, ValueError):
        raise PreviewBuildConfigurationError('snapshot_file_outside_output') from None
    if (
        relative in (Path('.'), Path('..'))
        or not relative.parts
        or relative.parts[0] == '..'
        or any(not _SAFE_PUBLIC_PART_RE.fullmatch(part) for part in relative.parts)
    ):
        raise PreviewBuildConfigurationError('snapshot_file_outside_output')

    parent = output_root.joinpath(*relative.parts[:-1])
    _ensure_directory(parent, root=output_root)
    _secure_path_under(
        output_root,
        *relative.parts[:-1],
        require_directory=True,
    )
    destination = _secure_path_under(output_root, *relative.parts)
    if destination.exists() and not destination.is_file():
        raise PreviewBuildConfigurationError('snapshot_file_invalid')
    return destination


def _ensure_directory(path: Path, *, root: Path) -> None:
    if not _is_within(path, root):
        raise PreviewBuildConfigurationError('path_outside_workspace')
    relative = Path(os.path.relpath(path, root))
    current = root
    for part in relative.parts:
        if part in ('', '.'):
            continue
        current = current / part
        if current.exists():
            if _is_link_or_reparse_point(current) or not current.is_dir():
                raise PreviewBuildConfigurationError('indirect_path_not_allowed')
            continue
        try:
            current.mkdir()
        except OSError:
            raise PreviewBuildConfigurationError('directory_unavailable') from None
    _secure_path_under(root, *relative.parts, require_directory=True)


def build_timeout_seconds() -> int:
    raw = _configured_value('SITEOS_WEB_BUILD_TIMEOUT_SECONDS') or '600'
    try:
        timeout = int(raw)
    except ValueError:
        raise PreviewBuildConfigurationError('invalid_build_timeout') from None
    if timeout < 1 or timeout > 3600:
        raise PreviewBuildConfigurationError('invalid_build_timeout')
    return timeout


def project_node_bin() -> Path | None:
    configured = _configured_value('SITEOS_PROJECT_NODE_BIN')
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute() or _is_link_or_reparse_point(path):
        raise PreviewBuildConfigurationError('invalid_node_runtime')
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise PreviewBuildConfigurationError('invalid_node_runtime') from None
    if not resolved.is_dir():
        raise PreviewBuildConfigurationError('invalid_node_runtime')
    return resolved


def windows_node_exe() -> Path | None:
    configured = _configured_value('SITEOS_WINDOWS_NODE_EXE')
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute() or _is_link_or_reparse_point(path):
        raise PreviewBuildConfigurationError('invalid_node_runtime')
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise PreviewBuildConfigurationError('invalid_node_runtime') from None
    if not resolved.is_file():
        raise PreviewBuildConfigurationError('invalid_node_runtime')
    return resolved


def _resolve_node_executable(raw_path: str | os.PathLike[str]) -> Path:
    path = Path(raw_path)
    if not path.is_absolute() or _is_link_or_reparse_point(path):
        raise PreviewBuildConfigurationError('invalid_node_runtime')
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise PreviewBuildConfigurationError('invalid_node_runtime') from None
    if not resolved.is_file():
        raise PreviewBuildConfigurationError('invalid_node_runtime')
    return resolved


def _build_subprocess_environment(node: Path) -> dict[str, str]:
    """Create a minimal environment for untrusted frontend build code.

    In particular, never inherit Django/database credentials, storage/import
    roots, preview configuration, Node injection flags, or the host PATH.
    """

    environment = {
        key: value
        for key in _BUILD_OS_ENV_ALLOWLIST
        if (value := os.environ.get(key)) not in (None, '')
    }
    environment.update(
        {
            'ASTRO_TELEMETRY_DISABLED': '1',
            'CI': '1',
            'NODE_ENV': 'production',
            'NO_COLOR': '1',
            'PATH': str(node.parent),
        }
    )
    return environment


def build_environment(site_code: str) -> BuildEnvironment:
    web_dir = web_app_dir(site_code)
    package_file = _secure_web_file(site_code, 'package.json')
    astro_script = _astro_entry_path(site_code, require_file=False)
    has_package = package_file.is_file()
    has_astro = astro_script.is_file()
    details = [
        f'package.json: {"ready" if has_package else "missing"}',
        f'Astro runtime: {"ready" if has_astro else "missing"}',
    ]

    local_bin = project_node_bin()
    if has_package and has_astro and local_bin is not None:
        local_node = local_bin / ('node.exe' if os.name == 'nt' else 'node')
        if local_node.is_file() and not _is_link_or_reparse_point(local_node):
            details.append('Build runner: explicitly configured project Node runtime')
            return BuildEnvironment(strategy='project_node', ready=True, details=details)

    if has_package and has_astro and shutil.which('node') is not None:
        details.append('Build runner: Node in current environment')
        return BuildEnvironment(strategy='system_node', ready=True, details=details)

    node_exe = windows_node_exe()
    if has_package and has_astro and node_exe is not None:
        details.append('Build runner: explicitly configured Node executable')
        return BuildEnvironment(strategy='explicit_node', ready=True, details=details)

    details.append('Build runner: unavailable')
    return BuildEnvironment(strategy='unavailable', ready=False, details=details)


def run_astro_build(*, site_code: str) -> subprocess.CompletedProcess[str]:
    environment = build_environment(site_code)
    if not environment.ready:
        raise PreviewBuildConfigurationError('build_runner_unavailable')

    web_dir = web_app_dir(site_code)
    # Re-resolve immediately before execution so a path validated during the
    # environment probe cannot silently become an indirect site-tree escape.
    astro_script = _astro_entry_path(site_code, require_file=True)
    if environment.strategy == 'project_node':
        local_bin = project_node_bin()
        if local_bin is None:
            raise PreviewBuildConfigurationError('build_runner_unavailable')
        node = _resolve_node_executable(local_bin / ('node.exe' if os.name == 'nt' else 'node'))
        process_env = _build_subprocess_environment(node)
        command = [str(node), str(astro_script), 'build']
    elif environment.strategy == 'system_node':
        node_path = shutil.which('node')
        if node_path is None:
            raise PreviewBuildConfigurationError('build_runner_unavailable')
        node = _resolve_node_executable(node_path)
        process_env = _build_subprocess_environment(node)
        command = [str(node), str(astro_script), 'build']
    elif environment.strategy == 'explicit_node':
        node_exe = windows_node_exe()
        if node_exe is None:
            raise PreviewBuildConfigurationError('build_runner_unavailable')
        node = _resolve_node_executable(node_exe)
        process_env = _build_subprocess_environment(node)
        command = [str(node), str(astro_script), 'build']
    else:
        raise PreviewBuildConfigurationError('build_runner_unavailable')

    return subprocess.run(
        command,
        cwd=web_dir,
        env=process_env,
        capture_output=True,
        text=True,
        timeout=build_timeout_seconds(),
    )


def _redact_known_paths(value: str, *, site_code: str) -> str:
    redacted = value
    try:
        known_paths = (
            web_dist_dir(site_code),
            web_public_dir(site_code),
            web_app_dir(site_code),
            site_preview_root(site_code),
            preview_workspace_root(),
        )
    except PreviewBuildError:
        known_paths = ()
    for path in known_paths:
        variants = {str(path), str(path).replace('\\', '/'), str(path).replace('/', '\\')}
        for variant in sorted(variants, key=len, reverse=True):
            redacted = redacted.replace(variant, '[preview-path]')
    return redacted


def _redact_sensitive_values(value: str) -> str:
    """Remove known server-side secrets before any build output is persisted."""

    secret_values: set[str] = set()
    for key, raw_value in os.environ.items():
        if not (key.upper().startswith('SITEOS_') or _SENSITIVE_ENV_KEY_RE.search(key)):
            continue
        if len(raw_value) >= 8:
            secret_values.add(raw_value)

    django_secret = getattr(settings, 'SECRET_KEY', '')
    if isinstance(django_secret, str) and len(django_secret) >= 8:
        secret_values.add(django_secret)

    redacted = value
    for secret_value in sorted(secret_values, key=len, reverse=True):
        redacted = redacted.replace(secret_value, '[redacted]')
    return redacted


def compact_log(*parts: str, site_code: str, limit: int = 12000) -> str:
    joined = '\n'.join(part.strip() for part in parts if part and part.strip())
    joined = _redact_known_paths(joined, site_code=site_code)
    joined = _redact_sensitive_values(joined)
    return joined[-limit:] if len(joined) > limit else joined


def resolve_asset_source(asset: MediaAsset) -> Path:
    """Resolve only an existing regular asset under the explicit storage allowlist."""

    return resolve_storage_asset_path(asset.storage_path)


def _safe_public_parts(public_path: str) -> tuple[str, ...]:
    if not isinstance(public_path, str) or not public_path or public_path != public_path.strip():
        raise PreviewBuildError('unsafe_asset_public_path')
    if not public_path.startswith('/assets/') or '\\' in public_path or '\x00' in public_path:
        raise PreviewBuildError('unsafe_asset_public_path')
    raw_parts = tuple(part for part in public_path.lstrip('/').split('/') if part)
    if any(part in {'.', '..'} for part in raw_parts):
        raise PreviewBuildError('unsafe_asset_public_path')
    path = PurePosixPath(public_path.lstrip('/'))
    if not path.parts or path.parts[0] != 'assets' or len(path.parts) < 2:
        raise PreviewBuildError('unsafe_asset_public_path')
    if any(not _SAFE_PUBLIC_PART_RE.fullmatch(part) for part in path.parts):
        raise PreviewBuildError('unsafe_asset_public_path')
    return tuple(path.parts)


def asset_destination(asset: MediaAsset) -> Path:
    site_code = _safe_site_code(asset.site.code)
    public_root = web_public_dir(site_code)
    parts = _safe_public_parts(asset.public_path)
    destination = _secure_path_under(public_root, *parts)
    assets_root = _secure_path_under(public_root, 'assets')
    if not _is_within(destination, assets_root) or destination == assets_root:
        raise PreviewBuildError('unsafe_asset_public_path')
    return destination


def _file_sha256(path: Path, *, error_code: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
    except OSError:
        raise PreviewBuildError(error_code) from None
    return digest.hexdigest()


def _atomic_copy_file(
    source: Path,
    destination: Path,
    *,
    site_code: str,
    expected_digest: str,
) -> None:
    public_root = web_public_dir(site_code)
    _ensure_directory(destination.parent, root=public_root)
    destination = _secure_path_under(public_root, *Path(os.path.relpath(destination, public_root)).parts)

    temporary = destination.parent / f'.preview-copy-{secrets.token_hex(12)}.tmp'
    try:
        copied_digest = hashlib.sha256()
        with source.open('rb') as source_handle, temporary.open('xb') as destination_handle:
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b''):
                copied_digest.update(chunk)
                destination_handle.write(chunk)
        if not secrets.compare_digest(copied_digest.hexdigest(), expected_digest):
            raise PreviewBuildError('asset_source_changed_during_copy')
        os.replace(temporary, destination)
    except PreviewBuildError:
        raise
    except OSError:
        raise PreviewBuildError('asset_copy_failed') from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def copy_managed_assets(*, site_code: str) -> dict[str, object]:
    site = Site.objects.get(code=site_code, enabled=True)
    copied = 0
    skipped = 0
    validated_assets: list[tuple[MediaAsset, Path, Path, str]] = []
    destination_owners: dict[str, int] = {}

    for asset in MediaAsset.objects.select_related('site').filter(site=site, status='active').order_by(
        'asset_type', 'public_path'
    ):
        expected_digest = (asset.sha256 or '').strip().lower()
        if not _SHA256_RE.fullmatch(expected_digest):
            raise PreviewBuildError('asset_checksum_invalid')
        try:
            source = resolve_asset_source(asset)
        except StorageSecurityError as exc:
            if exc.code in _MISSING_ASSET_CODES:
                raise PreviewBuildError('asset_source_missing') from None
            raise PreviewBuildError('unsafe_asset_source') from None

        destination = asset_destination(asset)
        destination_key = os.path.normcase(os.fspath(destination))
        if destination_key in destination_owners:
            raise PreviewBuildError('duplicate_asset_destination')
        destination_owners[destination_key] = asset.id
        if destination.exists():
            if _is_link_or_reparse_point(destination) or not destination.is_file():
                raise PreviewBuildError('unsafe_asset_destination')
        source_digest = _file_sha256(source, error_code='asset_source_unavailable')
        if not secrets.compare_digest(source_digest, expected_digest):
            raise PreviewBuildError('asset_checksum_mismatch')
        validated_assets.append((asset, source, destination, expected_digest))

    # Do not mutate the preview public tree until every active database asset
    # has passed path, regular-file, and checksum validation.
    for _asset, source, destination, expected_digest in validated_assets:
        if destination.exists():
            destination_digest = _file_sha256(
                destination,
                error_code='asset_destination_unavailable',
            )
            if secrets.compare_digest(destination_digest, expected_digest):
                skipped += 1
                continue
        _atomic_copy_file(
            source,
            destination,
            site_code=site.code,
            expected_digest=expected_digest,
        )
        copied += 1

    return {
        'copied': copied,
        'skipped': skipped,
        'missingAssetIds': [],
    }


def preview_release_key(site_code: str) -> str:
    return f'{DEFAULT_RELEASE_KEY}:{_safe_site_code(site_code)}'


def _build_key(started_at) -> str:
    return f'preview-{started_at:%Y%m%d%H%M%S%f}-{secrets.token_hex(4)}'


def _create_running_build(*, site_code: str, created_by: str, environment: BuildEnvironment) -> tuple[Release, ReleaseBuild]:
    release_key = preview_release_key(site_code)
    with transaction.atomic():
        site = Site.objects.select_for_update().get(code=site_code, enabled=True)
        if ReleaseBuild.objects.filter(release__site=site, status='running').exists():
            raise PreviewBuildInProgress('site_build_in_progress')

        conflicting_release = Release.objects.filter(release_key=release_key).exclude(site=site).first()
        if conflicting_release is not None:
            raise PreviewBuildError('release_key_owned_by_other_site')

        release, _ = Release.objects.get_or_create(
            site=site,
            release_key=release_key,
            defaults={'status': 'draft', 'created_by': created_by, 'snapshot_manifest': {}},
        )
        started_at = timezone.now()
        build = ReleaseBuild.objects.create(
            release=release,
            build_key=_build_key(started_at),
            status='running',
            started_at=started_at,
            config_json={
                'buildEnvironment': environment.__dict__,
                'siteCode': site.code,
                'outputScope': 'site-preview',
            },
        )
    return release, build


def run_preview_build(*, created_by: str, site_code: str) -> BuildResult:
    """Build one site's local preview; this function never publishes production output."""

    site_code = _safe_site_code(site_code)
    output_dir = snapshot_output_dir(site_code)
    environment = build_environment(site_code)
    release, build = _create_running_build(site_code=site_code, created_by=created_by, environment=environment)
    output = io.StringIO()

    try:
        call_command(
            'export_release_snapshot',
            output_dir=str(output_dir),
            release_id=release.release_key,
            site_code=site_code,
            created_by=created_by,
            stdout=output,
            stderr=output,
        )
        asset_copy_result = copy_managed_assets(site_code=site_code)
        output.write(
            '\nManaged assets: '
            f"copied={asset_copy_result['copied']}, "
            f"skipped={asset_copy_result['skipped']}, "
            f"missing={len(asset_copy_result['missingAssetIds'])}\n"
        )

        process = run_astro_build(site_code=site_code)
        log_excerpt = compact_log(output.getvalue(), process.stdout, process.stderr, site_code=site_code)
        finished_at = timezone.now()
        succeeded = process.returncode == 0

        with transaction.atomic():
            build.status = 'succeeded' if succeeded else 'failed'
            build.artifact_path = f'preview://{site_code}/dist' if succeeded else ''
            build.log_excerpt = log_excerpt
            build.finished_at = finished_at
            build.config_json = {
                **build.config_json,
                'returnCode': process.returncode,
                'command': 'astro-build',
            }
            build.save(update_fields=['status', 'artifact_path', 'log_excerpt', 'finished_at', 'config_json'])

            release.status = 'built' if succeeded else 'failed'
            release.artifact_path = f'preview://{site_code}/dist' if succeeded else ''
            release.built_at = finished_at if succeeded else None
            release.notes = 'Local preview build succeeded.' if succeeded else 'Local preview build failed.'
            release.save(update_fields=['status', 'artifact_path', 'built_at', 'notes'])

        return BuildResult(release=release, build=build, succeeded=succeeded, log_excerpt=log_excerpt)
    except Exception as exc:
        finished_at = timezone.now()
        if isinstance(exc, (PreviewBuildError, StorageSecurityError)):
            failure = f'{type(exc).__name__}: {exc.code}'
        else:
            failure = f'{type(exc).__name__}: preview step failed'
        log_excerpt = compact_log(output.getvalue(), failure, site_code=site_code)
        with transaction.atomic():
            build.status = 'failed'
            build.log_excerpt = log_excerpt
            build.finished_at = finished_at
            build.save(update_fields=['status', 'log_excerpt', 'finished_at'])

            release.status = 'failed'
            release.artifact_path = ''
            release.built_at = None
            release.notes = 'Local preview build failed before Astro completed.'
            release.save(update_fields=['status', 'artifact_path', 'built_at', 'notes'])

        return BuildResult(release=release, build=build, succeeded=False, log_excerpt=log_excerpt)
