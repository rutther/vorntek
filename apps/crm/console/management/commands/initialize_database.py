from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from siteos_admin.schema_install import load_chain, pending_migrations


LOCK_ID = 72304466189427711
LEDGER = 'siteos_schema_migration'


class Command(BaseCommand):
    help = 'Initialize/upgrade the verified SQL chain. Default is read-only planning.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        chain = load_chain(settings.BASE_DIR / 'db' / 'migrations')
        if connection.vendor != 'postgresql':
            raise CommandError('PostgreSQL is required; SQLite cannot validate this installation.')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_ID])
            if not cursor.fetchone()[0]:
                raise CommandError('Another installation owns the migration lock.')
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                tables = {row[0] for row in cursor.fetchall()}
                recorded = {}
                if tables and LEDGER not in tables:
                    raise CommandError('Existing database without an installation ledger; refusing to initialize.')
                if LEDGER in tables:
                    cursor.execute('SELECT name, checksum FROM siteos_schema_migration')
                    recorded = dict(cursor.fetchall())
                    if not recorded and any(not name.startswith(('auth_', 'django_')) and name != LEDGER for name in tables):
                        raise CommandError('Business tables exist without a recorded baseline; refusing SQL replay.')
            pending = pending_migrations(chain, recorded)
            self.stdout.write(f'Verified SQL: {len(chain)}; recorded: {len(recorded)}; pending: {len(pending)}')
            if not options['apply']:
                self.stdout.write('PLAN ONLY: no database changes.')
                return
            with connection.cursor() as cursor:
                cursor.execute('''CREATE TABLE IF NOT EXISTS siteos_schema_migration (
                    name text PRIMARY KEY, checksum char(64) NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now())''')
            call_command('migrate', interactive=False, verbosity=0)
            for migration in pending:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                        cursor.execute("SET LOCAL statement_timeout = '120s'")
                        cursor.execute(migration.sql)
                        cursor.execute('INSERT INTO siteos_schema_migration (name, checksum) VALUES (%s, %s)',
                                       [migration.name, migration.checksum])
                self.stdout.write(f'Applied {migration.name}')
            call_command('check_unmanaged_schema')
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [LOCK_ID])
