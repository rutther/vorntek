"""Private data-volume snapshots. No networking, database access or extraction.

Files are stored as SHA-256-addressed objects plus a private path manifest. No
archive-supplied executable code, symlinks or file modes are restored. Writers
must remain stopped; the acknowledgement is not a mechanism to stop them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import unicodedata


FORMAT = 'newcrown-file-snapshot-v1'
MARKER = '.newcrown-restore-incomplete'
VOLUMES = ('crm-files', 'crm-runtime')


class Refused(Exception):
    pass


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(value):
    if not isinstance(value, str) or not value or value in ('.', '..') or '\\' in value:
        raise Refused('Invalid relative file name.')
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise Refused('Absolute or noncanonical file name refused.')
    for part in path.parts:
        if part in ('.', '..') or part == MARKER or part[-1:] in (' ', '.'):
            raise Refused('Unsafe file name refused.')
        if any(ord(c) < 32 or c in ':*?"<>|' for c in part):
            raise Refused('Nonportable file name refused.')
        if part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                        *(f'COM{i}' for i in range(1, 10)),
                                        *(f'LPT{i}' for i in range(1, 10))}:
            raise Refused('Reserved operating-system file name refused.')
    return value


def regular(path, directory=False):
    mode = path.lstat().st_mode
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise Refused('Links/junctions are not supported.')
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise Refused('Only regular files and directories are supported.')


def inspect_tree(root, *, ignore_marker=False):
    regular(root, directory=True)
    files, directories = [], []
    for current, subdirs, names in os.walk(root, followlinks=False):
        for name in sorted(subdirs):
            path = Path(current)/name
            regular(path, directory=True)
            directories.append(safe_name(path.relative_to(root).as_posix()))
        for name in sorted(names):
            path = Path(current)/name
            if ignore_marker and path == root/MARKER:
                continue
            regular(path)
            if path.stat().st_nlink > 1:
                raise Refused('Hard-linked source files require a separate reviewed procedure.')
            files.append({'path': safe_name(path.relative_to(root).as_posix()),
                          'bytes': path.stat().st_size, 'sha256': digest(path)})
    result = {'directories': sorted(directories), 'files': sorted(files, key=lambda row: row['path'])}
    validate_entries(result)
    return result


def validate_entries(manifest):
    directories, files = manifest.get('directories'), manifest.get('files')
    if not isinstance(directories, list) or not isinstance(files, list):
        raise Refused('Missing file manifest entries.')
    seen = set()
    kinds = {}
    for name, kind in [(name, 'directory') for name in directories] + [(row.get('path'), 'file') for row in files]:
        safe_name(name)
        key = unicodedata.normalize('NFC', name).casefold()
        if key in seen:
            raise Refused('Duplicate or case/Unicode-colliding file names refused.')
        seen.add(key)
        kinds[name] = kind
    for name in kinds:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and kinds.get(str(parent)) != 'directory':
                raise Refused('Missing or non-directory parent entry.')
    for row in files:
        if type(row.get('bytes')) is not int or row['bytes'] < 0:
            raise Refused('Invalid file length.')
        if not isinstance(row.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', row['sha256']):
            raise Refused('Invalid object digest.')
    return sum(row['bytes'] for row in files)


def verify(bundle, volume):
    regular(bundle, directory=True)
    regular(bundle/'file-manifest.json')
    regular(bundle/'objects', directory=True)
    manifest = json.loads((bundle/'file-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != FORMAT or manifest.get('volume') != volume:
        raise Refused('File snapshot format/volume does not match.')
    validate_entries(manifest)
    expected = {row['sha256'] for row in manifest['files']}
    if {path.name for path in (bundle/'objects').iterdir()} != expected:
        raise Refused('Missing or unexpected data objects.')
    verified = {}
    for row in manifest['files']:
        path = bundle/'objects'/row['sha256']
        regular(path)
        if row['sha256'] not in verified:
            if digest(path) != row['sha256']:
                raise Refused('File object checksum mismatch.')
            verified[row['sha256']] = path.stat().st_size
        if verified[row['sha256']] != row['bytes']:
            raise Refused('File object length mismatch.')
    return manifest


def create_file(path, source):
    # Exclusive creation prevents silently replacing a target changed by another
    # process. Parent directories are still required to be exclusively controlled.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640), 'wb') as output:
        with source.open('rb') as stream:
            shutil.copyfileobj(stream, output)


def execute(args):
    if args.operation == 'verify':
        manifest = verify(args.bundle, args.volume)
        return {'result': 'file_integrity_verified', 'files': len(manifest['files'])}
    if args.root is None or not args.root.is_absolute():
        raise Refused('An explicit absolute --root is required.')
    regular(args.root, directory=True)
    root, bundle = args.root.resolve(), args.bundle.resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise Refused('Filesystem and user-home roots are not valid data-volume targets.')
    if root == bundle or root in bundle.parents or bundle in root.parents:
        raise Refused('Data and snapshot directories must be separate.')
    if args.operation == 'backup':
        if args.bundle.exists() or args.bundle.is_symlink():
            raise Refused('Snapshot destination already exists; no overwrite allowed.')
        before = inspect_tree(root)
        if not args.apply:
            return {'result': 'plan_only', 'files': len(before['files']), 'bytes': validate_entries(before)}
        if not args.writers_stopped:
            raise Refused('Snapshot requires --writers-stopped acknowledgement.')
        args.bundle.mkdir(mode=0o700, parents=False, exist_ok=False)
        (bundle/'objects').mkdir(mode=0o700)
        for row in before['files']:
            target = bundle/'objects'/row['sha256']
            if not target.exists():
                create_file(target, root/row['path'])
            if digest(target) != row['sha256']:
                raise Refused('Source changed during snapshot; no valid manifest issued.')
        if inspect_tree(root) != before:
            raise Refused('Source changed during snapshot; no valid manifest issued.')
        manifest = dict(before, format=FORMAT, volume=args.volume)
        with os.fdopen(os.open(bundle/'file-manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                       'w', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
        verify(bundle, args.volume)
        return {'result': 'file_snapshot_created', 'files': len(before['files'])}
    manifest = verify(args.bundle, args.volume)  # Verify every object before writes.
    before = inspect_tree(root)
    if before['files'] or not set(before['directories']).issubset(manifest['directories']):
        raise Refused('Restore requires a file-empty target with only matching empty directories.')
    if not args.apply:
        return {'result': 'plan_only', 'operation': 'restore', 'files': len(manifest['files'])}
    if not args.writers_stopped or not args.trusted_snapshot:
        raise Refused('Restore requires --writers-stopped and --trusted-snapshot acknowledgements.')
    marker = root/MARKER
    with marker.open('x', encoding='utf-8') as stream:
        stream.write('INCOMPLETE: keep services stopped; do not discard original recovery material.\n')
    for name in sorted(manifest['directories'], key=lambda value: (value.count('/'), value)):
        directory = root/name
        directory.mkdir(mode=0o750, exist_ok=True)
        regular(directory, directory=True)
    for row in manifest['files']:
        target = root/row['path']
        for parent in target.parents:
            if parent == root:
                break
            regular(parent, directory=True)
        create_file(target, args.bundle/'objects'/row['sha256'])
    expected = {key: manifest[key] for key in ('directories', 'files')}
    if inspect_tree(root, ignore_marker=True) != expected:
        raise Refused('Restored tree does not match; keep services stopped and preserve the incomplete marker.')
    marker.unlink()  # Only our own successful restore marker, never user files.
    return {'result': 'file_snapshot_restored', 'files': len(manifest['files']), 'services_started': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'verify', 'restore'))
    parser.add_argument('--volume', choices=VOLUMES, required=True)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--writers-stopped', action='store_true')
    parser.add_argument('--trusted-snapshot', action='store_true')
    try:
        print(json.dumps(execute(parser.parse_args())))
        return 0
    except Refused as exc:
        print(f'Refused: {exc}', file=sys.stderr)
    except Exception:
        print('File snapshot failed; inspect privately. Partial targets must remain offline.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
