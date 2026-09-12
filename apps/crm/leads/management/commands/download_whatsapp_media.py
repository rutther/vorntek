from django.core.management.base import BaseCommand

from leads.whatsapp_media import download_pending_whatsapp_media


class Command(BaseCommand):
    help = 'Download pending WhatsApp Cloud API media into private storage.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=25)

    def handle(self, *args, **options):
        result = download_pending_whatsapp_media(limit=options['limit'])
        self.stdout.write(
            self.style.SUCCESS(
                'WhatsApp media worker: '
                f"selected={result['selected']} downloaded={result['downloaded']} failed={result['failed']}"
            )
        )
