#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'vorntek-source-release-v1'


class Refused(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> bytes:
    try:
        return subprocess.run(
            ['git', *args],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise Refused('A readable Git checkout is required.') from error


def git_tree(prefix: str | None = None) -> tuple[int, str]:
    args = ['ls-tree', '-r', '-z', '--full-tree', 'HEAD']
    if prefix:
        args.extend(['--', prefix])
    payload = git(*args)
    entries = [entry for entry in payload.split(b'\0') if entry]
    return len(entries), hashlib.sha256(payload).hexdigest()


def dirty() -> bool:
    return bool(git('status', '--porcelain=v1', '--untracked-files=all').strip())


def migration_summary() -> dict[str, object]:
    directory = ROOT / 'apps' / 'crm' / 'db' / 'migrations'
    migrations = sorted(directory.glob('[0-9][0-9][0-9][0-9]_*.sql'))
    manifest_names = []
    for line in (directory / 'SHA256SUMS').read_text(encoding='ascii').splitlines():
        if line.strip():
            _digest, name = line.split()
            manifest_names.append(name)
    names = [path.name for path in migrations]
    if names != manifest_names:
        raise Refused('Migration files and SHA256SUMS order differ.')
    return {
        'count': len(names),
        'tip': names[-1] if names else None,
        'checksums_sha256': sha256(directory / 'SHA256SUMS'),
    }


def build_manifest(*, require_clean: bool = False) -> dict[str, object]:
    is_dirty = dirty()
    if require_clean and is_dirty:
        raise Refused('Working tree is not clean; release manifest was not issued.')
    version = (ROOT / 'VERSION').read_text(encoding='ascii').strip()
    commit = git('rev-parse', 'HEAD').decode('ascii').strip()
    source_count, source_digest = git_tree()
    website_count, website_digest = git_tree('apps/website')
    return {
        'format': FORMAT,
        'version': version,
        'git_commit': commit,
        'dirty': is_dirty,
        'source_tree': {
            'tracked_files': source_count,
            'git_tree_sha256': source_digest,
        },
        'website_tree': {
            'tracked_files': website_count,
            'git_tree_sha256': website_digest,
        },
        'migrations': migration_summary(),
        'compose_sha256': sha256(ROOT / 'compose.yaml'),
        'crm_requirements_lock_sha256': sha256(ROOT / 'apps' / 'crm' / 'requirements.lock'),
    }


def write_exclusive(path: Path, payload: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise Refused('Output already exists; refusing to overwrite it.')
    descriptor, temporary_name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise Refused('Output appeared during generation; refusing to overwrite it.')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Issue a deterministic source release manifest; no build artifacts are uploaded.'
    )
    parser.add_argument('--require-clean', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        manifest = build_manifest(require_clean=args.require_clean)
        payload = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode(
            'utf-8'
        )
        if args.output:
            write_exclusive(args.output, payload)
        else:
            print(payload.decode('utf-8'), end='')
        return 0
    except Refused as error:
        parser.exit(1, f'Refused: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
