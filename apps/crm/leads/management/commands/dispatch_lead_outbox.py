from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from leads.services import dispatch_pending_outbox


class Command(BaseCommand):
    help = 'Dispatch pending Meta CAPI and Google Data Manager lead events, then poll asynchronous results.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--max-attempts', type=int, default=8)
        parser.add_argument('--retry-delay-minutes', type=int, default=5)

    def handle(self, *args, **options):
        limit = max(1, min(int(options['limit']), 1000))
        max_attempts = max(1, min(int(options['max_attempts']), 50))
        retry_delay_minutes = max(0, min(int(options['retry_delay_minutes']), 1440))
        failed_before = timezone.now() - timedelta(minutes=retry_delay_minutes)

        attempted, succeeded = dispatch_pending_outbox(
            limit=limit,
            max_attempts=max_attempts,
            failed_before=failed_before,
        )
        message = f'Marketing outbox completed {succeeded}/{attempted}; pending or failed {attempted - succeeded}'
        if attempted == succeeded:
            self.stdout.write(self.style.SUCCESS(message))
        else:
            self.stdout.write(self.style.WARNING(message))
