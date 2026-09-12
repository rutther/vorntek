from django.core.management.base import BaseCommand

from leads.whatsapp_cloud import dispatch_pending_whatsapp_messages


class Command(BaseCommand):
    help = 'Dispatch queued WhatsApp Cloud API messages; mock mode is the safe default.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        attempted, succeeded = dispatch_pending_whatsapp_messages(limit=options['limit'])
        message = f'WhatsApp messages sent {succeeded}/{attempted}; unresolved {attempted - succeeded}'
        if attempted == succeeded:
            self.stdout.write(self.style.SUCCESS(message))
        else:
            self.stdout.write(self.style.WARNING(message))
