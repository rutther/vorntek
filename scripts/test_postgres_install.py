"""Exercise installation and recovery in a new, disposable local PostgreSQL cluster.

Requires psycopg and the application's Python dependencies. Does not register a
service, reuse an existing cluster, contact production, or delete test evidence.
The caller supplies an already-installed PostgreSQL binary directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
CRM = ROOT / 'apps' / 'crm'


def postgres_start_options(runtime, port, platform=None):
    """Keep the standalone test socket out of distro-owned system directories."""
    platform = os.name if platform is None else platform
    options = f'-h 127.0.0.1 -p {port}'
    if platform != 'nt':
        options += ' -k ' + shlex.quote(str(runtime))
    return options


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--http', action='store_true', help='Also exercise website-to-CRM HTTP using synthetic accounts.')
    parser.add_argument('--review-seconds', type=int, default=0, help='Keep the isolated HTTP server available briefly for browser review (max 900s).')
    args = parser.parse_args()
    if not 0 <= args.review_seconds <= 900 or (args.review_seconds and not args.http):
        parser.error('--review-seconds requires --http and must be between 0 and 900.')
    if (CRM / '.env').exists():
        parser.error('Refusing to test with an application .env present; use a clean source checkout.')
    binary = args.pg_bin.resolve()
    suffix = '.exe' if os.name == 'nt' else ''
    for name in ('postgres', 'initdb', 'pg_ctl', 'pg_dump', 'pg_restore'):
        if not (binary / (name + suffix)).is_file():
            parser.error(f'Missing PostgreSQL executable: {name}')

    runtime = Path(tempfile.mkdtemp(prefix='newCrownPg-'))
    # This directory is unique and contains only synthetic local test material.
    password = secrets.token_hex(32)
    pwfile = runtime / 'bootstrap-password'
    with os.fdopen(os.open(pwfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write(password)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('PG', 'SITEOS_', 'NEWCROWN_', 'VORNTEK_', 'DJANGO_'))}
    env.update({
        'PGPASSWORD': password, 'PGHOST': '127.0.0.1', 'PGPORT': str(port),
        'PGUSER': 'newcrown_app', 'PGSSLMODE': 'disable', 'PGCONNECT_TIMEOUT': '5',
        'SITEOS_ADMIN_DEBUG': '0', 'SITEOS_ADMIN_SECRET_KEY': secrets.token_hex(32),
        'SITEOS_SECRET_VAULT_KEY': secrets.token_hex(32),
        'SITEOS_ADMIN_DATABASE_HOST': '127.0.0.1', 'SITEOS_ADMIN_DATABASE_PORT': str(port),
        'SITEOS_ADMIN_DATABASE_USER': 'newcrown_app',
        'SITEOS_ADMIN_DATABASE_PASSWORD': password, 'SITEOS_ADMIN_DATABASE_SSLMODE': 'disable',
        'SITEOS_ADMIN_SECURE_SSL_REDIRECT': '0', 'NEWCROWN_ALLOW_EXTERNAL_IO': '0',
        'NEWCROWN_SITE_URL': 'http://localhost:8088',
        'NEWCROWN_SYNTHETIC_ACCEPTANCE': '1',
        'SITEOS_ARTICLE_RELEASE_ROOT': str(runtime / 'acceptance-crm-runtime' / 'article-releases'),
        'SITEOS_WEBSITE_RELEASE_ROOT': str(runtime / 'acceptance-crm-runtime' / 'website-releases'),
        'SITEOS_WEBSITE_SERVING_ROOT': str(runtime / 'acceptance-website-serving'),
        'SITEOS_WEBSITE_SOURCE_ROOT': str(ROOT / 'apps' / 'website'),
        'PYTHONIOENCODING': 'utf-8',
    })
    report = {'started_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'scope': 'unique local cluster; synthetic data only; no external integrations',
              'runtime_directory': str(runtime), 'checks': []}

    def run(
        command,
        *,
        db='newcrown_install',
        expected=0,
        contains=None,
        env_overrides=None,
    ):
        child_env = dict(env, SITEOS_ADMIN_DATABASE_NAME=db, PGDATABASE=db)
        child_env.update(env_overrides or {})
        # A Windows postgres child can inherit pg_ctl's pipe handles after
        # pg_ctl exits. Files avoid waiting for the server to close those pipes.
        with tempfile.TemporaryFile() as output_file:
            result = subprocess.run([str(part) for part in command], env=child_env, cwd=CRM,
                                    stdout=output_file, stderr=subprocess.STDOUT, timeout=180)
            output_file.seek(0)
            output = output_file.read().decode('utf-8', errors='replace').replace(password, '[redacted]')
        if (expected == 0 and result.returncode != 0) or (expected != 0 and result.returncode == 0):
            raise RuntimeError(f'Unexpected command result ({result.returncode}):\n{output[-10000:]}')
        if contains and contains not in output:
            raise RuntimeError(f'Expected diagnostic not found: {contains}\n{output[-5000:]}')
        return output

    def exe(name):
        return binary / (name + suffix)

    def manage(*command, **kwargs):
        return run([sys.executable, 'manage.py', *command], **kwargs)

    def connect(db='newcrown_install', user='newcrown_app'):
        return psycopg.connect(host='127.0.0.1', port=port, dbname=db, user=user,
                               password=password, sslmode='disable', autocommit=True)

    def checked(name, detail=None):
        report['checks'].append({'name': name, 'result': 'passed', 'detail': detail})
        print(f'PASS: {name}', flush=True)

    def fingerprint(db):
        with connect(db) as conn:
            tables = [row[0] for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
            result = {}
            for table in tables:
                rows = [row[0] for row in conn.execute(sql.SQL(
                    'SELECT row_to_json(t)::text FROM public.{} t ORDER BY row_to_json(t)::text'
                ).format(sql.Identifier(table)))]
                result[table] = {'rows': len(rows), 'sha256': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
            return result

    def schema_fingerprint(db):
        with connect(db) as conn:
            constraints = conn.execute("""SELECT c.relname, k.conname, k.contype,
                pg_get_constraintdef(k.oid) FROM pg_constraint k
                JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' ORDER BY c.relname,k.conname""").fetchall()
            indexes = conn.execute("""SELECT tablename,indexname,indexdef FROM pg_indexes
                WHERE schemaname='public' ORDER BY tablename,indexname""").fetchall()
            triggers = conn.execute("""SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid)
                FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""").fetchall()
            sequences = {}
            for (name,) in conn.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").fetchall():
                sequences[name] = conn.execute(sql.SQL('SELECT last_value,is_called FROM public.{}')
                                               .format(sql.Identifier(name))).fetchone()
            return {'constraints': constraints, 'indexes': indexes, 'triggers': triggers, 'sequences': sequences}

    started = False
    http_process = None
    http_log = None
    try:
        report['postgres_version'] = run([exe('postgres'), '--version']).strip()
        run([exe('initdb'), '-D', runtime / 'data', '-U', 'postgres', '--pwfile', pwfile,
             '--auth-host=scram-sha-256', '--auth-local=scram-sha-256', '--encoding=UTF8', '--locale=C'])
        try:
            run([exe('pg_ctl'), '-D', runtime / 'data', '-l', runtime / 'server.log',
                 '-o', postgres_start_options(runtime, port), '-w', '-t', '30', 'start'])
        except RuntimeError as exc:
            # This newly created cluster has no customer data or application
            # sessions yet. Include its startup diagnostic, redacting the only
            # database password, so ephemeral CI does not lose the actual cause.
            startup_log = runtime / 'server.log'
            diagnostic = startup_log.read_text(encoding='utf-8', errors='replace') if startup_log.exists() else 'No server log was created.'
            raise RuntimeError(f'{exc}\nPostgreSQL startup log:\n{diagnostic[-4000:].replace(password, "[redacted]")}') from exc
        started = True
        with connect('postgres', 'postgres') as conn:
            conn.execute(sql.SQL('CREATE ROLE newcrown_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}')
                         .format(sql.Literal(password)))
            for db in ('newcrown_install', 'newcrown_restore', 'newcrown_upgrade', 'newcrown_unowned',
                       'newcrown_archive_restore', 'newcrown_archive_failure', 'newcrown_archive_policy'):
                conn.execute(sql.SQL('CREATE DATABASE {} OWNER newcrown_app').format(sql.Identifier(db)))
        checked('isolated_loopback_cluster_non_superuser_app', report['postgres_version'])
        before = fingerprint('newcrown_install')
        manage('initialize_database', contains='PLAN ONLY')
        assert fingerprint('newcrown_install') == before == {}, 'Planning wrote database tables'
        checked('empty_database_plan_does_not_write')
        manage('initialize_database', '--apply', contains='62 models, 866 fields')
        checked('complete_sql_chain_install_as_non_superuser')
        manage('bootstrap_site')
        baseline = fingerprint('newcrown_install')
        manage('initialize_database', '--apply', contains='pending: 0')
        manage('bootstrap_site')
        assert fingerprint('newcrown_install') == baseline, 'Repeated initialization changed existing data'
        checked('repeat_initialize_and_bootstrap_preserve_all_rows', {'tables': len(baseline)})

        with connect() as conn:
            conn.execute("UPDATE site SET name='SYNTHETIC preserved configuration' WHERE code='siteos_demo'")
        manage('bootstrap_site')
        with connect() as conn:
            assert conn.execute("SELECT name FROM site WHERE code='siteos_demo'").fetchone()[0] == 'SYNTHETIC preserved configuration'
        checked('bootstrap_preserves_custom_site_settings')

        restored_acceptance_runtime = None
        if os.name != 'nt':
            acceptance_output = manage(
                'run_synthetic_website_deployment_acceptance',
                '--synthetic-only',
                '--expect-database',
                'newcrown_install',
                contains='"result": "passed"',
            )
            acceptance = json.loads(
                next(
                    line
                    for line in reversed(acceptance_output.splitlines())
                    if line.strip()
                )
            )
            assert acceptance['selectionEvents'] == 4
            assert acceptance['deploymentReceipts'] == 4
            assert acceptance['relativePointer'].startswith('releases/')
            assert acceptance['recoveryCacheRebuilt'] is True
            checked(
                'synthetic_website_selection_deploy_recovery_rollback_and_postgres_guards',
                acceptance,
            )
            acceptance_runtime = runtime / 'acceptance-crm-runtime'
            runtime_snapshot = runtime / 'acceptance-runtime-snapshot'
            restored_acceptance_runtime = runtime / 'restored-crm-runtime'
            restored_acceptance_runtime.mkdir()
            file_archive = ROOT / 'scripts' / 'file_archive.py'
            run([
                sys.executable,
                file_archive,
                'backup',
                '--volume',
                'crm-runtime',
                '--root',
                acceptance_runtime,
                '--bundle',
                runtime_snapshot,
                '--writers-stopped',
                '--apply',
            ], contains='file_snapshot_created')
            run([
                sys.executable,
                file_archive,
                'verify',
                '--volume',
                'crm-runtime',
                '--bundle',
                runtime_snapshot,
            ], contains='file_integrity_verified')
            run([
                sys.executable,
                file_archive,
                'restore',
                '--volume',
                'crm-runtime',
                '--root',
                restored_acceptance_runtime,
                '--bundle',
                runtime_snapshot,
                '--trusted-snapshot',
                '--writers-stopped',
                '--apply',
            ], contains='file_snapshot_restored')
            checked('website_candidate_runtime_snapshot_restored_to_new_root')
        else:
            report['checks'].append({
                'name': 'synthetic_website_deployment_posix_filesystem',
                'result': 'skipped',
                'detail': 'Real relative-symlink acceptance runs on Linux CI.',
            })

        with connect() as conn:
            conn.execute('SELECT pg_advisory_lock(72304466189427711)')
            manage('initialize_database', '--apply', expected=1, contains='migration lock')
            conn.execute('SELECT pg_advisory_unlock(72304466189427711)')
        checked('concurrent_installer_refused')

        heartbeat = runtime / 'scheduler.json'
        with connect() as conn:
            conn.execute('SELECT pg_advisory_lock(72304466189427712)')
            manage('run_scheduled_tasks', '--once', '--heartbeat', str(heartbeat), expected=1, contains='Another scheduler')
            conn.execute('SELECT pg_advisory_unlock(72304466189427712)')
        assert not heartbeat.exists(), 'Rejected scheduler touched the heartbeat'
        checked('duplicate_scheduler_refused')
        before = fingerprint('newcrown_install')
        manage('run_scheduled_tasks', '--once', '--heartbeat', str(heartbeat), contains='paused')
        assert fingerprint('newcrown_install') == before
        state = json.loads(heartbeat.read_text(encoding='utf-8'))
        assert state['state'] == 'stopped' and len(state['jobs']) == 3
        assert all(result['status'] == 'paused' for result in state['jobs'].values())
        checked('paused_scheduler_preserves_all_rows_and_stops_cleanly')

        # Fixture only: a genuine previous release consists of SQL 1..26. Skip
        # the current model checker while creating that old-version fixture.
        fixture = '''import os; os.environ['DJANGO_SETTINGS_MODULE']='siteos_admin.settings'
import django; django.setup()
from unittest.mock import patch
from django.conf import settings
from django.core.management import call_command
from siteos_admin.schema_install import load_chain
chain=load_chain(settings.BASE_DIR/'db'/'migrations')
def historical_check(name, *a, **kw):
    if name != 'check_unmanaged_schema': return call_command(name, *a, **kw)
with patch('console.management.commands.initialize_database.load_chain', return_value=chain[:26]), patch('console.management.commands.initialize_database.call_command', side_effect=historical_check):
    call_command('initialize_database', apply=True)
'''
        run([sys.executable, '-c', fixture], db='newcrown_upgrade')
        manage('initialize_database', '--apply', db='newcrown_upgrade', contains='pending: 1')
        manage('check_unmanaged_schema', db='newcrown_upgrade')
        checked('upgrade_from_real_0026_schema_to_0027')

        failure = '''import os; os.environ['DJANGO_SETTINGS_MODULE']='siteos_admin.settings'
import django; django.setup()
from unittest.mock import patch
from django.conf import settings
from django.core.management import call_command
from django.db import DatabaseError
from siteos_admin.schema_install import load_chain, Migration
chain=load_chain(settings.BASE_DIR/'db'/'migrations')
chain.append(Migration('0028_synthetic_failure.sql', 'a'*64, 'CREATE TABLE synthetic_rollback (id int); SELECT 1/0;'))
with patch('console.management.commands.initialize_database.load_chain', return_value=chain):
    try: call_command('initialize_database', apply=True)
    except DatabaseError: print('EXPECTED_TRANSACTION_FAILURE')
    else: raise AssertionError('Synthetic failure did not fail')
'''
        before = fingerprint('newcrown_install')
        run([sys.executable, '-c', failure], contains='EXPECTED_TRANSACTION_FAILURE')
        assert fingerprint('newcrown_install') == before, 'Failed SQL/ledger transaction changed data'
        checked('failed_sql_and_ledger_insert_roll_back_together')

        with connect('newcrown_unowned') as conn:
            conn.execute('CREATE TABLE synthetic_existing (id int)')
        manage('initialize_database', '--apply', db='newcrown_unowned', expected=1, contains='without an installation ledger')
        checked('existing_database_without_ledger_refused')

        # A synthetic secret exercises pgcrypto, not a real integration token.
        manage('shell', '-c', "from console.secret_store import store_secret, load_secret; "
               "store_secret(secret_key='synthetic.recovery', plaintext='synthetic-test-only', updated_by='test'); "
               "assert load_secret('synthetic.recovery') == 'synthetic-test-only'")

        dump = runtime / 'synthetic.backup'
        run([exe('pg_dump'), '-Fc', '--no-owner', '--no-acl', '-d', 'newcrown_install', '-f', dump])
        run([exe('pg_restore'), '--exit-on-error', '--single-transaction', '--no-owner', '--no-acl',
             '-d', 'newcrown_restore', dump])
        assert fingerprint('newcrown_restore') == fingerprint('newcrown_install'), 'Restored rows differ'
        source_schema = schema_fingerprint('newcrown_install')
        assert schema_fingerprint('newcrown_restore') == source_schema, 'Restored constraints, indexes, triggers or sequences differ'
        manage('initialize_database', '--apply', db='newcrown_restore', contains='pending: 0')
        manage('check_unmanaged_schema', db='newcrown_restore')
        manage('shell', '-c', "from console.secret_store import load_secret; "
               "assert load_secret('synthetic.recovery') == 'synthetic-test-only'", db='newcrown_restore')
        if restored_acceptance_runtime is not None:
            recovery_serving = runtime / 'restored-website-serving'
            recovery_env = {
                'SITEOS_ARTICLE_RELEASE_ROOT': str(
                    restored_acceptance_runtime / 'article-releases'
                ),
                'SITEOS_WEBSITE_RELEASE_ROOT': str(
                    restored_acceptance_runtime / 'website-releases'
                ),
                'SITEOS_WEBSITE_SERVING_ROOT': str(recovery_serving),
            }
            manage(
                'shell',
                '-c',
                'from django.conf import settings; '
                'from console.website_serving_store import WebsiteServingStore; '
                'WebsiteServingStore.initialize(settings.SITEOS_WEBSITE_SERVING_ROOT)',
                db='newcrown_restore',
                env_overrides=recovery_env,
            )
            before_reconcile = fingerprint('newcrown_restore')
            plan_output = manage(
                'reconcile_website_serving_cache',
                '--site-code',
                'siteos_demo',
                '--expect-database',
                'newcrown_restore',
                db='newcrown_restore',
                env_overrides=recovery_env,
                contains='"result": "plan_only"',
            )
            plan = json.loads(
                next(line for line in reversed(plan_output.splitlines()) if line.strip())
            )
            assert plan['action'] == 'rebuild_required'
            apply_output = manage(
                'reconcile_website_serving_cache',
                '--site-code',
                'siteos_demo',
                '--expect-database',
                'newcrown_restore',
                '--expect-deployment-id',
                str(plan['deploymentId']),
                '--expect-serving-version',
                plan['observedServingVersion'],
                '--writers-stopped',
                '--apply',
                db='newcrown_restore',
                env_overrides=recovery_env,
                contains='"result": "reconciled"',
            )
            reconciled = json.loads(
                next(line for line in reversed(apply_output.splitlines()) if line.strip())
            )
            assert reconciled['action'] == 'rebuilt'
            assert reconciled['databaseRowsChanged'] is False
            assert fingerprint('newcrown_restore') == before_reconcile
            assert os.readlink(recovery_serving / 'current') == (
                'releases/' + reconciled['targetVersion']
            )
            checked(
                'restored_database_and_runtime_rebuild_serving_cache_without_database_writes',
                {
                    'deployment_id': reconciled['deploymentId'],
                    'target_version': reconciled['targetVersion'],
                },
            )
        checked('custom_backup_restore_all_table_rows_and_schema',
                {'sha256': hashlib.sha256(dump.read_bytes()).hexdigest(), 'bytes': dump.stat().st_size,
                 'tables': len(fingerprint('newcrown_install')),
                 **{name: len(items) for name, items in source_schema.items()}, 'synthetic_vault_decryption': True})

        archive_script = ROOT / 'scripts' / 'database_archive.py'
        bundle = runtime / 'archive-bundle'

        def archive(operation, *extra, db='newcrown_install', expected=0, contains=None, path=bundle):
            return run([sys.executable, archive_script, operation, '--bundle', path,
                        '--pg-bin', binary, '--expect-database', db, *extra],
                       db=db, expected=expected, contains=contains)

        archive('backup', contains='plan_only')
        assert not bundle.exists()
        archive('backup', '--expect-database', 'wrong_database', expected=1, contains='identity')
        assert not bundle.exists()
        checked('archive_plan_and_identity_refusal_do_not_create_files')
        archive('backup', '--apply', contains='archive_created')
        archive('verify', contains='checksum_verified')
        archive('backup', '--apply', expected=1, contains='already exists')
        checked('private_archive_created_verified_and_overwrite_refused')
        restore_db = 'newcrown_archive_restore'
        archive('restore', db=restore_db, contains='plan_only')
        assert fingerprint(restore_db) == {}
        archive('restore', '--apply', db=restore_db, expected=1, contains='acknowledgements')
        with connect(restore_db) as conn:
            archive('restore', '--apply', '--trusted-archive', '--writers-stopped',
                    db=restore_db, expected=1, contains='Other database sessions')
        assert fingerprint(restore_db) == {}
        checked('archive_restore_plan_acknowledgement_and_session_guards')
        archive('restore', '--apply', '--trusted-archive', '--writers-stopped',
                db=restore_db, contains='database_restored')
        assert fingerprint(restore_db) == fingerprint('newcrown_install')
        assert schema_fingerprint(restore_db) == source_schema
        manage('shell', '-c', "from console.secret_store import load_secret; "
               "assert load_secret('synthetic.recovery') == 'synthetic-test-only'", db=restore_db)
        before = fingerprint(restore_db)
        archive('restore', '--apply', '--trusted-archive', '--writers-stopped',
                db=restore_db, expected=1, contains='not empty')
        assert fingerprint(restore_db) == before
        checked('operational_archive_restores_all_rows_schema_vault_and_refuses_nonempty')
        manifest_path = bundle / 'manifest.json'
        original_manifest = manifest_path.read_text(encoding='utf-8')
        damaged = json.loads(original_manifest)
        damaged['sha256'] = '0' * 64
        manifest_path.write_text(json.dumps(damaged), encoding='utf-8')
        archive('restore', '--apply', '--trusted-archive', '--writers-stopped',
                db='newcrown_archive_failure', expected=1, contains='checksum')
        assert fingerprint('newcrown_archive_failure') == {}
        manifest_path.write_text(original_manifest, encoding='utf-8')
        checked('corrupt_archive_refused_before_target_writes')

        # A real pg_restore failure after tables/data have been processed: a
        # synthetic RLS policy references a role deliberately absent at restore.
        # All roles/databases below belong to this newly created test cluster.
        with connect('postgres', 'postgres') as conn:
            conn.execute('CREATE ROLE synthetic_archive_reader NOLOGIN')
        with connect('newcrown_archive_policy') as conn:
            conn.execute('CREATE TABLE synthetic_policy_rows (id integer PRIMARY KEY)')
            conn.execute('INSERT INTO synthetic_policy_rows VALUES (1)')
            conn.execute('ALTER TABLE synthetic_policy_rows ENABLE ROW LEVEL SECURITY')
            conn.execute('CREATE POLICY synthetic_reader ON synthetic_policy_rows TO synthetic_archive_reader USING (true)')
        policy_bundle = runtime / 'policy-failure-bundle'
        archive('backup', '--apply', db='newcrown_archive_policy', path=policy_bundle)
        with connect('newcrown_archive_policy') as conn:
            conn.execute('DROP POLICY synthetic_reader ON synthetic_policy_rows')
        with connect('postgres', 'postgres') as conn:
            conn.execute('DROP ROLE synthetic_archive_reader')
        archive('restore', '--apply', '--trusted-archive', '--writers-stopped',
                db='newcrown_archive_failure', path=policy_bundle, expected=1, contains='command failed')
        assert fingerprint('newcrown_archive_failure') == {}
        # Plan verifies all empty-target catalogs, not only table counts.
        archive('restore', db='newcrown_archive_failure', contains='plan_only')
        checked('real_pg_restore_failure_rolls_back_created_objects_and_rows')

        with connect('newcrown_restore') as conn:
            conn.execute("DELETE FROM siteos_schema_migration WHERE name LIKE '0001_%'")
        before = fingerprint('newcrown_restore')
        manage('initialize_database', '--apply', db='newcrown_restore', expected=1, contains='Incomplete legacy ledger')
        assert fingerprint('newcrown_restore') == before
        checked('gapped_legacy_ledger_refused_without_changes')

        with connect('newcrown_upgrade') as conn:
            conn.execute('UPDATE siteos_schema_migration SET checksum=%s WHERE name LIKE %s', ['0' * 64, '0001_%'])
        before = fingerprint('newcrown_upgrade')
        manage('initialize_database', '--apply', db='newcrown_upgrade', expected=1, contains='checksum differs')
        assert fingerprint('newcrown_upgrade') == before
        checked('altered_applied_checksum_refused_without_changes')
        # Independently initialized database: never mix 200 demo customers into
        # the lead conversion test or an operator's existing customer pool.
        seed_db = 'vorntek_seed_test'
        with connect('postgres', 'postgres') as conn:
            conn.execute('CREATE DATABASE vorntek_seed_test OWNER newcrown_app')
        manage('initialize_database', '--apply', db=seed_db)
        manage('bootstrap_site', db=seed_db)
        seed_before = fingerprint(seed_db)
        manage('seed_vorntek_demo', db=seed_db, contains='PLAN ONLY')
        manage('seed_vorntek_demo', '--apply', db=seed_db, expected=1, contains='synthetic-only')
        assert fingerprint(seed_db) == seed_before
        checked('demo_plan_and_missing_ack_leave_all_rows_unchanged')
        with connect(seed_db) as conn:
            conn.execute("UPDATE site SET name='Unrelated synthetic site'")
        wrong_identity = fingerprint(seed_db)
        manage('seed_vorntek_demo', '--apply', '--synthetic-only', db=seed_db,
               expected=1, contains='Expected initialized Vorntek site')
        assert fingerprint(seed_db) == wrong_identity
        with connect(seed_db) as conn:
            conn.execute("UPDATE site SET name='Vorntek'")
            conn.execute("""CREATE FUNCTION synthetic_seed_failure() RETURNS trigger AS $$
                BEGIN IF NEW.raw_value = 'contact200@example.invalid' THEN
                RAISE EXCEPTION 'synthetic final-row failure'; END IF; RETURN NEW; END;
                $$ LANGUAGE plpgsql""")
            conn.execute('''CREATE TRIGGER synthetic_seed_failure BEFORE INSERT ON crm_company_contact_point
                FOR EACH ROW EXECUTE FUNCTION synthetic_seed_failure()''')
        seed_before = fingerprint(seed_db)
        manage('seed_vorntek_demo', '--apply', '--synthetic-only', db=seed_db,
               expected=1, contains='synthetic final-row failure')
        assert fingerprint(seed_db) == seed_before, 'Partial seed escaped transaction rollback'
        with connect(seed_db) as conn:
            conn.execute('DROP TRIGGER synthetic_seed_failure ON crm_company_contact_point')
            conn.execute('DROP FUNCTION synthetic_seed_failure()')
        checked('demo_wrong_identity_refused_and_last_row_failure_rolls_back_all_rows')
        manage('seed_vorntek_demo', '--apply', '--synthetic-only', db=seed_db, contains='CREATED')
        seeded = fingerprint(seed_db)
        seed_tables = {'crm_company', 'crm_company_pool_state', 'crm_company_contact_point', 'crm_customer_source'}
        for table in seed_tables:
            assert seeded[table]['rows'] == 200, table
        for table in set(seeded) - seed_tables:
            assert seeded[table] == seed_before[table], f'Seed changed unrelated table: {table}'
        with connect(seed_db) as conn:
            assert conn.execute('SELECT count(DISTINCT industry) FROM crm_company').fetchone()[0] == 7
            assert dict(conn.execute('SELECT source_type,count(*) FROM crm_customer_source GROUP BY source_type').fetchall()) == {
                'manual': 40, 'research': 40, 'website_form': 40, 'meta_native': 40, 'other': 40}
            assert dict(conn.execute('SELECT state,count(*) FROM crm_company_pool_state GROUP BY state').fetchall()) == {
                'available': 150, 'review': 40, 'archived': 10}
            assert conn.execute("""SELECT count(*) FROM crm_company_contact_point WHERE
                status='do_not_contact' AND usage_status='restricted' AND
                normalized_value LIKE '%@example.invalid' AND evidence_json->>'synthetic'='true'""").fetchone()[0] == 200
            assert conn.execute("SELECT count(*) FROM crm_customer_source WHERE evidence_status='declared' AND submission_id IS NULL").fetchone()[0] == 200
        checked('demo_200_customers_seven_industries_five_sources_no_accounts_messages_or_outbox')
        manage('seed_vorntek_demo', '--apply', '--synthetic-only', db=seed_db,
               expected=1, contains='Customer database is not empty')
        assert fingerprint(seed_db) == seeded
        checked('demo_reapply_refused_without_overwrite_or_duplicate')
        if args.http:
            with connect('postgres', 'postgres') as conn:
                conn.execute('CREATE DATABASE newcrown_http_test OWNER newcrown_app')
            manage('initialize_database', '--apply', db='newcrown_http_test')
            manage('bootstrap_site', db='newcrown_http_test')
            env['SITEOS_CANDIDATE_PASSWORD'] = secrets.token_hex(24)
            fixture = '''import os; os.environ['DJANGO_SETTINGS_MODULE']='siteos_admin.settings'
import django; django.setup()
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from console.access import GROUP_BY_ROLE
from leads.models import SalesTeam, SalesTeamMember
team=SalesTeam.objects.get(site__code='siteos_demo',code='default')
for username,role in [('local-admin','system_admin'),('local-sales','sales')]:
    user=get_user_model().objects.create_user(username=username,password=os.environ['SITEOS_CANDIDATE_PASSWORD'],is_staff=True,is_superuser=role=='system_admin')
    user.groups.add(Group.objects.get(name=GROUP_BY_ROLE[role]))
    SalesTeamMember.objects.create(team=team,user=user,membership_role='manager' if role=='system_admin' else 'member')
'''
            run([sys.executable, '-c', fixture], db='newcrown_http_test')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                http_port = sock.getsockname()[1]
            base_url = f'http://127.0.0.1:{http_port}/'
            env.update({
                # Seven product cases plus retries/browser review share one
                # loopback IP. This override applies ONLY to this disposable
                # local HTTP process; the deployment default remains 5/10 min.
                'SITEOS_LEAD_RATE_LIMIT_COUNT': '20',
                'SITEOS_ADMIN_SESSION_COOKIE_SECURE': '0', 'SITEOS_ADMIN_CSRF_COOKIE_SECURE': '0',
                'SITEOS_ADMIN_CSRF_TRUSTED_ORIGINS': base_url.rstrip('/'),
                'SITEOS_PUBLIC_FORM_ALLOWED_ORIGINS': 'https://local-website.test,' + base_url.rstrip('/'),
                'SITEOS_TEST_DATABASE_URL': f'postgresql://newcrown_app:{password}@127.0.0.1:{port}/newcrown_http_test',
                'SITEOS_EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
            })
            http_env = dict(env, SITEOS_ADMIN_DATABASE_NAME='newcrown_http_test')
            http_log = (runtime / 'http-server.log').open('wb')
            http_process = subprocess.Popen([sys.executable, str(ROOT/'scripts'/'serve_local_test.py'), '--port', str(http_port)],
                                            env=http_env, cwd=ROOT, stdout=http_log, stderr=subprocess.STDOUT)
            local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(50):
                if http_process.poll() is not None:
                    raise RuntimeError('Local HTTP server exited; inspect its private test log.')
                try:
                    with local_http.open(base_url + 'healthz/', timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError('Local HTTP server did not become ready.')
            for route in ('', 'contact/', 'assets/meta-pixel.js', 'admin/login/'):
                with local_http.open(base_url + route, timeout=5) as response:
                    assert response.status == 200 and len(response.read()) > 0
            with local_http.open(base_url + 'api/marketing/measurement-config/', timeout=5) as response:
                config = json.load(response)
                assert not config['meta_pixel_enabled'] and not config['google_tag_enabled']
            checked('same_origin_website_crm_http_and_disabled_measurement')
            run([sys.executable, ROOT/'scripts'/'check_local_stack.py', '--url', base_url],
                db='newcrown_http_test', contains='"result": "passed"')
            checked('read_only_stack_smoke_script_against_real_local_http')
            run([sys.executable, 'scripts/verify_website_crm_http_e2e.py', '--base-url', base_url],
                db='newcrown_http_test', contains='Website-to-CRM HTTP E2E passed')
            checked('website_submission_assignment_qualification_conversion_http_e2e')
            if args.review_seconds:
                review = {'url': base_url, 'completion_file': str(runtime/'browser.done'),
                          'scope': 'synthetic local browser inspection; not production'}
                (runtime/'review.json').write_text(json.dumps(review), encoding='utf-8')
                print('BROWSER_REVIEW_READY ' + json.dumps(review), flush=True)
                deadline = time.monotonic() + args.review_seconds
                while time.monotonic() < deadline and not (runtime/'browser.done').exists():
                    if http_process.poll() is not None:
                        raise RuntimeError('HTTP server stopped during browser review.')
                    time.sleep(0.2)
                # Report only database verification; visual assertions are a
                # separate browser record, not inferred from this wait.
                with connect('newcrown_http_test') as conn:
                    browser_rows = conn.execute("SELECT count(*) FROM lead_submission WHERE email='browser-local-check@example.invalid'").fetchone()[0]
                report['browser_review_submission_count'] = browser_rows
        report['result'] = 'passed'
    except Exception as exc:
        report['result'] = 'failed'
        report['error'] = str(exc).replace(password, '[redacted]')
        print(report['error'], file=sys.stderr)
    finally:
        if http_process is not None:
            if http_process.poll() is None:
                http_process.terminate()
                try:
                    http_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    http_process.kill()
                    http_process.wait(timeout=10)
            report['http_server_stopped'] = http_process.poll() is not None
        if http_log is not None:
            http_log.close()
        if started or (runtime / 'data' / 'postmaster.pid').exists():
            try:
                run([exe('pg_ctl'), '-D', runtime / 'data', '-m', 'fast', '-w', '-t', '30', 'stop'])
                report['cluster_stopped'] = True
            except Exception as exc:
                report['cluster_stopped'] = False
                report['stop_error'] = str(exc).replace(password, '[redacted]')
                report['result'] = 'failed'
        (runtime / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'Evidence retained: {runtime / "report.json"}', flush=True)
    return 0 if report.get('result') == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
