"""Private PostgreSQL logical archive utility. Plan-only unless --apply is given.

This does not back up file volumes, provision roles, adopt legacy ledgers, stop
writers or start services. See docs/BACKUP_RESTORE.md before using real data.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import psycopg


LOCK_ID = 72304466189427711  # Same cooperative lock as schema initialization.
CONNECTION_KEYS = ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGSSLMODE',
                   'PGSSLROOTCERT', 'PGSSLCERT', 'PGSSLKEY', 'PGPASSFILE', 'PGPASSWORD')


class Refused(Exception):
    pass


def connection_env(environ):
    env = {k: v for k, v in environ.items() if not k.startswith('PG')}
    env.update({k: environ[k] for k in CONNECTION_KEYS if environ.get(k)})
    for key in ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER'):
        if not env.get(key):
            raise Refused(f'{key} must be explicitly configured.')
    for key in ('PGDATABASE', 'PGUSER'):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,62}', env[key]):
            raise Refused(f'{key} must be a simple identifier, not a connection string.')
    if not env['PGPORT'].isdigit() or not 0 < int(env['PGPORT']) < 65536:
        raise Refused('PGPORT is invalid.')
    if environ.get('PGPASSWORD_FILE'):
        if env.get('PGPASSWORD') or env.get('PGPASSFILE'):
            raise Refused('Use only one password input mechanism.')
        env['PGPASSWORD'] = Path(environ['PGPASSWORD_FILE']).read_text(encoding='utf-8').strip()
        if not env['PGPASSWORD']:
            raise Refused('Password file is empty.')
    env['PGCONNECT_TIMEOUT'] = '10'
    env['PGAPPNAME'] = 'newcrown-archive'
    return env


def connect(env):
    keys = {'PGHOST': 'host', 'PGPORT': 'port', 'PGDATABASE': 'dbname', 'PGUSER': 'user',
            'PGSSLMODE': 'sslmode', 'PGSSLROOTCERT': 'sslrootcert', 'PGSSLCERT': 'sslcert',
            'PGSSLKEY': 'sslkey', 'PGPASSFILE': 'passfile', 'PGPASSWORD': 'password'}
    # Explicit parameters plus a sanitized process environment keep pg tools and
    # psycopg on the same target; inherited PGSERVICE/PGOPTIONS cannot redirect it.
    return psycopg.connect(**{v: env[k] for k, v in keys.items() if k in env},
                           connect_timeout=10, application_name='newcrown-archive', autocommit=True)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def private_json(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')


def validate_bundle(bundle):
    if bundle.is_symlink() or not bundle.is_dir():
        raise Refused('Archive directory must be a regular, existing directory.')
    for name in ('manifest.json', 'database.dump'):
        if (bundle / name).is_symlink() or not (bundle / name).is_file():
            raise Refused('Archive files must be regular files.')
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    dump = bundle / 'database.dump'
    if manifest.get('format') != 'newcrown-database-archive-v1':
        raise Refused('Unknown archive manifest format.')
    if manifest.get('bytes') != dump.stat().st_size or manifest.get('sha256') != sha256(dump):
        raise Refused('Archive checksum or size mismatch.')
    if not isinstance(manifest.get('postgres_major'), int):
        raise Refused('Missing database version.')
    return manifest


def assert_empty(conn):
    # More than a table check: refuse user schemas, functions, types, extensions,
    # sequences, large objects and replication objects as well.
    queries = [
        "SELECT count(*) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')",
        "SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql'",
        'SELECT count(*) FROM pg_largeobject_metadata',
        'SELECT count(*) FROM pg_foreign_server',
        'SELECT count(*) FROM pg_event_trigger',
        'SELECT count(*) FROM pg_publication',
        'SELECT count(*) FROM pg_subscription WHERE subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())',
    ]
    for catalog, column in (('pg_class', 'relnamespace'), ('pg_proc', 'pronamespace'),
                            ('pg_type', 'typnamespace'), ('pg_operator', 'oprnamespace'),
                            ('pg_collation', 'collnamespace'), ('pg_conversion', 'connamespace')):
        queries.append(f"SELECT count(*) FROM {catalog} c JOIN pg_namespace n ON n.oid=c.{column} "
                       "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'")
    if any(conn.execute(query).fetchone()[0] for query in queries):
        raise Refused('Target database is not empty; no objects were overwritten.')


def tool(binary, name):
    return str(binary / (name + ('.exe' if os.name == 'nt' else ''))) if binary else name


def run_tool(command, env, output=None):
    result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                            stdout=output if output is not None else subprocess.DEVNULL,
                            stderr=subprocess.PIPE, timeout=1800)
    # Never print raw SQL/server errors: they can contain private rows or secrets.
    if result.returncode:
        raise Refused('PostgreSQL archive command failed; no success manifest was issued. Diagnose privately.')
    if result.stderr.strip():
        raise Refused('PostgreSQL archive command reported diagnostics; private review is required before acceptance.')


def execute(args, environ=None):
    if args.operation == 'verify':
        validate_bundle(args.bundle)
        return {'result': 'checksum_verified', 'scope': 'integrity only; not authenticity or recovery proof'}
    env = connection_env(os.environ if environ is None else environ)
    # libpq also reads environment defaults for unspecified fields. Remove those
    # from this process before opening its verification connection.
    for key in list(os.environ):
        if key.startswith('PG'):
            del os.environ[key]
    manifest = validate_bundle(args.bundle) if args.operation == 'restore' else None
    with connect(env) as conn:
        database, major, superuser = conn.execute(
            "SELECT current_database(), current_setting('server_version_num')::int / 10000, "
            "(SELECT rolsuper FROM pg_roles WHERE rolname=current_user)").fetchone()
        if database != args.expect_database:
            raise Refused('Database identity does not match --expect-database.')
        if superuser:
            raise Refused('Use the non-superuser application database owner, not a cluster administrator.')
        if args.operation == 'backup':
            if args.bundle.exists() or args.bundle.is_symlink():
                raise Refused('Backup destination already exists; overwriting is not supported.')
            if not args.apply:
                return {'result': 'plan_only', 'operation': 'backup', 'postgres_major': major}
            args.bundle.mkdir(mode=0o700, parents=False, exist_ok=False)
            dump = args.bundle / 'database.dump'
            with os.fdopen(os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                run_tool([tool(args.pg_bin, 'pg_dump'), '--format=custom', '--no-password',
                          '--lock-wait-timeout=10000'], env, stream)
            run_tool([tool(args.pg_bin, 'pg_restore'), '--list', str(dump)], env)
            private_json(args.bundle / 'manifest.json', {
                'format': 'newcrown-database-archive-v1', 'postgres_major': major,
                'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'sha256': sha256(dump), 'bytes': dump.stat().st_size,
                'scope': 'database only; no roles, file volumes, secrets or recovery proof',
            })
            return {'result': 'archive_created', 'scope': 'database only; restoration must be tested'}
        if major != manifest['postgres_major']:
            raise Refused('Cross-major restores need a separate verified upgrade procedure.')
        assert_empty(conn)
        if not args.apply:
            return {'result': 'plan_only', 'operation': 'restore', 'target_empty': True}
        if not args.trusted_archive or not args.writers_stopped:
            raise Refused('Restore requires --trusted-archive and --writers-stopped acknowledgements.')
        if not conn.execute('SELECT pg_try_advisory_lock(%s)', (LOCK_ID,)).fetchone()[0]:
            raise Refused('Another migration/recovery holds the cooperative lock.')
        if conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND pid <> pg_backend_pid()").fetchone()[0]:
            raise Refused('Other database sessions exist; stop application and workers before restoring.')
        assert_empty(conn)
        run_tool([tool(args.pg_bin, 'pg_restore'), '--no-password', '--single-transaction',
                  '--exit-on-error', '--no-owner', '--no-acl', '--dbname', database,
                  str(args.bundle / 'database.dump')], env)
        return {'result': 'database_restored', 'services_started': False,
                'scope': 'verify schema/data/files/vault and queue quarantine before service startup'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'verify', 'restore'))
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--expect-database', help='Exact expected database name; required for database access.')
    parser.add_argument('--pg-bin', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--trusted-archive', action='store_true')
    parser.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args()
    if args.operation != 'verify' and not args.expect_database:
        parser.error('--expect-database is required.')
    try:
        print(json.dumps(execute(args)))
        return 0
    except Refused as exc:
        print(f'Refused: {exc}', file=sys.stderr)
    except Exception:
        print('Archive operation failed; inspect the private environment without sharing secrets.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
