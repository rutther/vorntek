"""Run the candidate's PostgreSQL schema contract without database writes.

The historical contract scripts under ``remote-edit/db/tests`` resolve the
application through ``remote-edit/apps/admin`` and create temporary schemas.
This candidate-local replacement resolves every path from this repository and
uses PostgreSQL's read-only session guard before it runs Django introspection.
It is deliberately restricted to a local database whose name ends in ``_test``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


ADMIN_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ADMIN_DIR / 'db' / 'migrations'
CHECKSUM_FILE = MIGRATIONS_DIR / 'SHA256SUMS'
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Read-only model/schema parity check for the local candidate database.'
    )
    parser.add_argument(
        '--dsn',
        default=os.getenv('SITEOS_CANDIDATE_POSTGRES_URL', ''),
        help=(
            'Local candidate PostgreSQL DSN. The host must be local and the '
            'database name must end in _test.'
        ),
    )
    return parser.parse_args()


def require_safe_candidate_dsn(dsn: str):
    if not dsn:
        raise SystemExit('Set SITEOS_CANDIDATE_POSTGRES_URL or pass --dsn.')

    parsed = urlparse(dsn)
    if parsed.scheme not in {'postgres', 'postgresql'}:
        raise SystemExit('The candidate DSN must use postgres:// or postgresql://.')
    if parsed.hostname not in LOCAL_HOSTS:
        raise SystemExit('Refusing a non-local PostgreSQL host.')
    database_name = parsed.path.lstrip('/')
    if not database_name.endswith('_test'):
        raise SystemExit('The candidate PostgreSQL database name must end in _test.')
    return parsed, database_name


def verify_migration_checksums() -> int:
    if not CHECKSUM_FILE.is_file():
        raise SystemExit(f'Missing migration checksum manifest: {CHECKSUM_FILE}')

    expected: dict[str, str] = {}
    for line in CHECKSUM_FILE.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        try:
            digest, filename = stripped.split(maxsplit=1)
        except ValueError as exc:
            raise SystemExit(f'Invalid SHA256SUMS line: {line!r}') from exc
        expected[filename.strip()] = digest.lower()

    migrations = sorted(MIGRATIONS_DIR.glob('[0-9][0-9][0-9][0-9]_*.sql'))
    actual_names = {path.name for path in migrations}
    expected_names = set(expected)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        untracked = sorted(actual_names - expected_names)
        raise SystemExit(
            f'Migration manifest mismatch: missing={missing}, untracked={untracked}'
        )

    mismatches = []
    for migration in migrations:
        actual = hashlib.sha256(migration.read_bytes()).hexdigest()
        if actual != expected[migration.name]:
            mismatches.append(migration.name)
    if mismatches:
        raise SystemExit(f'Migration checksum drift detected: {mismatches}')

    return len(migrations)


def main() -> int:
    args = parse_args()
    _parsed, expected_database = require_safe_candidate_dsn(args.dsn)
    migration_count = verify_migration_checksums()

    sys.path.insert(0, str(ADMIN_DIR))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'siteos_admin.settings'
    os.environ['SITEOS_ADMIN_DEBUG'] = '1'
    os.environ['SITEOS_ADMIN_DATABASE_URL'] = args.dsn
    os.environ['SITEOS_ADMIN_DATABASE_SSLMODE'] = 'disable'
    os.environ['SITEOS_ADMIN_SECURE_SSL_REDIRECT'] = '0'

    import django

    django.setup()

    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    connection.ensure_connection()
    try:
        # This guard is set before any contract introspection. If a future
        # contract change accidentally performs DDL or DML, PostgreSQL rejects it.
        with connection.cursor() as cursor:
            cursor.execute('SET default_transaction_read_only = on')
            cursor.execute(
                'SELECT current_database(), current_setting(%s)',
                ['default_transaction_read_only'],
            )
            database_name, read_only = cursor.fetchone()

        if database_name != expected_database:
            raise SystemExit(
                f'Connected database mismatch: expected {expected_database!r}, got {database_name!r}.'
            )
        if read_only != 'on':
            raise SystemExit('PostgreSQL did not enable the read-only session guard.')

        call_command('check', verbosity=0)

        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            labels = [f'{migration.app_label}.{migration.name}' for migration, _ in pending]
            raise SystemExit(f'Pending managed Django migrations: {labels}')

        call_command('check_unmanaged_schema', verbosity=0)
    finally:
        connection.close()

    print(
        'Candidate PostgreSQL read-only contract passed: '
        f'{migration_count} SQL migration checksums, managed migration state, '
        'and unmanaged Django model/schema parity.'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
