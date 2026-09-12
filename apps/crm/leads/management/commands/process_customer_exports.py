import signal
import threading

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from console.customer_pool_exports import process_pending_customer_exports


class Command(BaseCommand):
    help = 'Process queued customer-pool exports in a separate worker process.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--interval-seconds', type=float, default=2.0)

    def handle(self, *args, **options):
        interval = max(0.5, min(float(options['interval_seconds']), 60.0))
        stop = threading.Event()
        old_handlers = {}
        if options['watch'] and threading.current_thread() is not threading.main_thread():
            raise CommandError('Watch mode must run on the main thread to handle shutdown signals.')
        try:
            if options['watch']:
                for signum in (signal.SIGTERM, signal.SIGINT):
                    old_handlers[signum] = signal.signal(signum, lambda *_: stop.set())
                self.stdout.write('Customer export worker watching.')
                self.stdout.flush()
            while not stop.is_set():
                attempted, succeeded = process_pending_customer_exports(
                    limit=options['limit'], stop_requested=stop.is_set,
                )
                message = (
                    f'Customer export jobs processed {succeeded}/{attempted}; '
                    f'failed or canceled {attempted - succeeded}'
                )
                if attempted or not options['watch']:
                    if attempted == succeeded:
                        self.stdout.write(self.style.SUCCESS(message))
                    else:
                        self.stdout.write(self.style.WARNING(message))
                if not options['watch']:
                    return
                close_old_connections()
                stop.wait(interval)
            self.stdout.write('Customer export worker stopped.')
        finally:
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)
            close_old_connections()
