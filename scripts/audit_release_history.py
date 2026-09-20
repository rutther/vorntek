#!/usr/bin/env python3
"""Audit candidate-only Git objects without printing matched secret material."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


DEFAULT_MAX_BLOB_BYTES = 1_000_000
BLOCKED_PARTS = {'.secrets', '.runtime', 'backups', 'private'}
BLOCKED_SUFFIXES = (
    '.bak', '.dump', '.key', '.p12', '.pem', '.pfx', '.sqlite', '.sqlite3',
    '.tar', '.tar.gz', '.tgz', '.zip', '.7z',
)
PRIVATE_KEY_MARKERS = (
    b'-----BEGIN ' + b'PRIVATE KEY-----',
    b'-----BEGIN RSA ' + b'PRIVATE KEY-----',
    b'-----BEGIN EC ' + b'PRIVATE KEY-----',
    b'-----BEGIN OPENSSH ' + b'PRIVATE KEY-----',
)
SHAPED_SECRET_PATTERNS = {
    'AWS access key': re.compile(rb'\bAKIA[0-9A-Z]{16}\b'),
    'GitHub token': re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    'Meta token': re.compile(rb'\bEAA[A-Za-z0-9]{30,}\b'),
}
CREDENTIAL_URL = re.compile(
    rb'\b(?:postgres(?:ql)?|mysql|redis)://[^/\s:@]+:[^@{}\s]+@'
)
RUNTIME_PREFIXES = ('apps/', 'deploy/', 'scripts/')
RUNTIME_ROOT_FILES = {'compose.yaml', '.env.example'}
PRODUCTION_MARKERS = {
    'production domain': ('filline' + '.com').encode(),
    'production project path': ('/opt/web/' + 'site-stack-v2').encode(),
    'production IPv4 address': ('43.242.' + '200.69').encode(),
    'private workspace path': ('F:' + '\\WorkSpace1').encode(),
    'retired workstation path': ('E:' + '\\Qoder').encode(),
}


@dataclass(frozen=True)
class AuditFinding:
    kind: str
    object: str
    path: str


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ['git', '-c', 'core.quotepath=false', *args],
        cwd=root,
        input=input_bytes,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _resolve(root: Path, revision: str) -> str:
    return _git(root, 'rev-parse', '--verify', f'{revision}^{{commit}}').decode().strip()


def _is_test_fixture(path: str) -> bool:
    pure = PurePosixPath(path)
    return pure.name.startswith('test_') or (pure.parts and pure.parts[0] == 'tests')


def _is_runtime_input(path: str) -> bool:
    return path.startswith(RUNTIME_PREFIXES) or path in RUNTIME_ROOT_FILES


def _changed_blob_paths(root: Path, commits: list[str]) -> dict[str, set[str]]:
    """Bind every candidate blob version to every path changed to that version."""
    paths: dict[str, set[str]] = {}
    zero_oid = '0' * 40
    for commit in commits:
        raw = _git(
            root,
            'diff-tree', '--root', '--no-commit-id', '-r', '-z', '--no-renames',
            '--raw', commit,
        )
        fields = raw.split(b'\0')
        for index in range(0, len(fields) - 1, 2):
            header = fields[index]
            path_bytes = fields[index + 1]
            if not header or not path_bytes:
                continue
            parts = header.removeprefix(b':').split()
            if len(parts) != 5:
                raise ValueError('Unexpected raw Git diff record.')
            _old_mode, new_mode, _old_oid, new_oid_bytes, status = parts
            new_oid = new_oid_bytes.decode('ascii')
            if status == b'D' or new_oid == zero_oid or new_mode == b'160000':
                continue
            path = path_bytes.decode('utf-8', errors='surrogateescape')
            paths.setdefault(new_oid, set()).add(path)
    return paths


def audit_range(
    root: Path,
    base: str,
    head: str = 'HEAD',
    *,
    max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES,
    require_clean: bool = False,
) -> dict[str, object]:
    root = root.resolve()
    base_commit = _resolve(root, base)
    head_commit = _resolve(root, head)
    ancestor = subprocess.run(
        ['git', 'merge-base', '--is-ancestor', base_commit, head_commit],
        cwd=root,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise ValueError('The requested base is not an ancestor of the candidate head.')

    if require_clean and _git(root, 'status', '--porcelain', '--untracked-files=all').strip():
        raise ValueError('The working tree is dirty; refusing an exact release-history audit.')

    object_lines = _git(
        root, 'rev-list', '--objects', head_commit, f'^{base_commit}'
    ).splitlines()
    object_paths: dict[str, str] = {}
    for line in object_lines:
        oid_bytes, separator, path_bytes = line.partition(b' ')
        oid = oid_bytes.decode('ascii')
        object_paths.setdefault(
            oid,
            path_bytes.decode('utf-8', errors='surrogateescape') if separator else '',
        )

    commits = _git(
        root, 'rev-list', '--reverse', f'{base_commit}..{head_commit}'
    ).decode('ascii').splitlines()
    changed_blob_paths = _changed_blob_paths(root, commits)
    scan_oids = set(object_paths).union(changed_blob_paths)
    batch_input = b''.join(oid.encode('ascii') + b'\n' for oid in sorted(scan_oids))
    object_info = _git(
        root,
        'cat-file',
        '--batch-check=%(objectname) %(objecttype) %(objectsize)',
        input_bytes=batch_input,
    ).splitlines()

    findings: set[AuditFinding] = set()
    blob_count = 0
    text_blob_count = 0
    largest_blob_bytes = 0
    largest_blob_path = ''

    for info in object_info:
        oid_bytes, object_type, size_bytes = info.split(b' ', 2)
        if object_type != b'blob':
            continue
        blob_count += 1
        oid = oid_bytes.decode('ascii')
        size = int(size_bytes)
        paths = sorted(changed_blob_paths.get(oid) or {object_paths.get(oid, '')})
        representative_path = paths[0]
        if size > largest_blob_bytes:
            largest_blob_bytes = size
            largest_blob_path = representative_path
        if size > max_blob_bytes:
            findings.add(AuditFinding('oversized blob', oid, representative_path))

        for path in paths:
            pure = PurePosixPath(path)
            lower_path = path.lower()
            if BLOCKED_PARTS.intersection(pure.parts):
                findings.add(AuditFinding('blocked private/runtime path', oid, path))
            if pure.name.startswith('.env') and pure.name != '.env.example':
                findings.add(AuditFinding('blocked environment file', oid, path))
            if lower_path.endswith(BLOCKED_SUFFIXES):
                findings.add(AuditFinding('blocked backup/key/archive suffix', oid, path))

        payload = _git(root, 'cat-file', 'blob', oid)
        if b'\0' in payload[:8192]:
            continue
        text_blob_count += 1
        for marker in PRIVATE_KEY_MARKERS:
            if marker in payload:
                for path in paths:
                    findings.add(AuditFinding('private key material', oid, path))
        for label, pattern in SHAPED_SECRET_PATTERNS.items():
            if pattern.search(payload):
                for path in paths:
                    findings.add(AuditFinding(label, oid, path))
        if CREDENTIAL_URL.search(payload):
            for path in paths:
                if not _is_test_fixture(path):
                    findings.add(AuditFinding('credential-bearing service URL', oid, path))
        for path in paths:
            if _is_runtime_input(path):
                for label, marker in PRODUCTION_MARKERS.items():
                    if marker in payload:
                        findings.add(AuditFinding(label, oid, path))

    commit_count = int(
        _git(root, 'rev-list', '--count', f'{base_commit}..{head_commit}').decode().strip()
    )
    ordered_findings = sorted(findings, key=lambda item: (item.path, item.kind, item.object))
    return {
        'format': 'vorntek-release-history-audit-v1',
        'base_commit': base_commit,
        'head_commit': head_commit,
        'commit_count': commit_count,
        'unique_object_count': len(object_paths),
        'unique_blob_count': blob_count,
        'changed_blob_path_count': sum(len(paths) for paths in changed_blob_paths.values()),
        'text_blob_count': text_blob_count,
        'largest_blob_bytes': largest_blob_bytes,
        'largest_blob_path': largest_blob_path,
        'max_blob_bytes': max_blob_bytes,
        'finding_count': len(ordered_findings),
        'findings': [asdict(item) for item in ordered_findings],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Audit Git objects introduced after a trusted release base.'
    )
    parser.add_argument('--base', required=True, help='Trusted ancestor commit or tag.')
    parser.add_argument('--head', default='HEAD', help='Candidate commit (default: HEAD).')
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='Git repository root.')
    parser.add_argument(
        '--max-blob-bytes', type=int, default=DEFAULT_MAX_BLOB_BYTES,
        help='Reject newly introduced blobs larger than this value.',
    )
    parser.add_argument(
        '--require-clean', action='store_true',
        help='Refuse when tracked or untracked working-tree changes are present.',
    )
    args = parser.parse_args(argv)
    try:
        if args.max_blob_bytes <= 0:
            raise ValueError('--max-blob-bytes must be positive.')
        report = audit_range(
            args.root,
            args.base,
            args.head,
            max_blob_bytes=args.max_blob_bytes,
            require_clean=args.require_clean,
        )
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f'History audit refused: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report['finding_count'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
