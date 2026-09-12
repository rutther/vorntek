import io
import json
import os
from pathlib import Path
import signal
import threading
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from siteos_admin.job_scheduler import jobs, tick


LOCK_ID = 72304466189427712


class Command(BaseCommand):
    help = 'Run the packaged serial scheduler with a PostgreSQL singleton lock. Paused by default.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--healthcheck', action='store_true')
        parser.add_argument('--heartbeat', type=Path, default=settings.BASE_DIR / '.runtime' / 'scheduler.json')

    def handle(self, *args, **options):
        path = options['heartbeat']
        if options['healthcheck']:
            try:
                state = json.loads(path.read_text(encoding='utf-8'))
                healthy = 0 <= time.time() - state['updated_at'] < 120 and state['state'] in {'running', 'paused'}
            except (OSError, ValueError, KeyError, TypeError):
                healthy = False
            if not healthy:
                raise CommandError('Scheduler heartbeat missing, stale or stopped.')
            return
        if connection.vendor != 'postgresql':
            raise CommandError('PostgreSQL is required for the scheduler singleton lock.')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s), pg_backend_pid()', [LOCK_ID])
            acquired, backend_pid = cursor.fetchone()
        if not acquired:
            raise CommandError('Another scheduler owns the database lock.')
        stop = threading.Event()
        old_handlers = {}
        last_results = {}

        def heartbeat(state):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
            temporary.write_text(json.dumps({'updated_at': time.time(), 'state': state,
                                             'jobs': last_results}), encoding='utf-8')
            os.replace(temporary, path)

        def verify_session():
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                if cursor.fetchone()[0] != backend_pid:
                    raise CommandError('Scheduler database session changed; refusing to run without the lock.')

        def execute(job):
            verify_session()
            # Do not copy command output (which may contain business details)
            # into heartbeat files or scheduler logs.
            call_command(job.name, **job.kwargs, stdout=io.StringIO(), stderr=io.StringIO())
            verify_session()

        try:
            for signum in (signal.SIGTERM, signal.SIGINT):
                old_handlers[signum] = signal.signal(signum, lambda *_: stop.set())
            schedule = jobs(time.monotonic())
            while not stop.is_set():
                verify_session()
                enabled = settings.NEWCROWN_RUN_SCHEDULED_TASKS and settings.NEWCROWN_ALLOW_EXTERNAL_IO
                heartbeat('running' if enabled else 'paused')
                for result in tick(schedule, now=time.monotonic(), enabled=enabled,
                                   execute=execute, clock=time.monotonic, force=options['once'], stop_requested=stop.is_set):
                    last_results[result['job']] = result
                    self.stdout.write(json.dumps(result))
                heartbeat('running' if enabled else 'paused')
                if options['once']:
                    break
                stop.wait(10)
        finally:
            try:
                heartbeat('stopped')
            finally:
                for signum, handler in old_handlers.items():
                    signal.signal(signum, handler)
                # Closing the owning session also releases its advisory lock.
                connection.close()
