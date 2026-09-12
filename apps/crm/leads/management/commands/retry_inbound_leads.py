from django.core.management.base import BaseCommand

from leads.inbound import retry_inbound_events


class Command(BaseCommand):
    help = 'Retry durable Meta Instant Form and WhatsApp webhook receipts.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--max-attempts', type=int, default=8)
        parser.add_argument('--provider', choices=['meta', 'whatsapp'], default='')

    def handle(self, *args, **options):
        limit = max(1, min(int(options['limit']), 1000))
        max_attempts = max(1, min(int(options['max_attempts']), 50))
        attempted, succeeded = retry_inbound_events(limit=limit, max_attempts=max_attempts, provider=options['provider'])
        message = f'Inbound lead events processed {succeeded}/{attempted}; failed {attempted - succeeded}'
        if attempted == succeeded:
            self.stdout.write(self.style.SUCCESS(message))
        else:
            self.stdout.write(self.style.WARNING(message))
