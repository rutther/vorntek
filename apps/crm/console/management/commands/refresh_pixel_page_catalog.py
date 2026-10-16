from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from marketing.page_inventory import refresh_catalog


class Command(BaseCommand):
    help = 'Refresh Pixel page/form evidence from the local serving release; never contacts a provider.'

    def add_arguments(self, parser):
        parser.add_argument('--root', type=Path)
        parser.add_argument('--output', type=Path)
        parser.add_argument('--site-code', default='siteos_demo')

    def handle(self, *args, **options):
        root = options['root'] or Path(settings.SITEOS_WEBSITE_SERVING_ROOT) / 'current'
        output = options['output'] or Path(getattr(settings, 'SITEOS_PIXEL_CATALOG_PATH',
            settings.BASE_DIR / '.runtime' / 'pixel-pages.json'))
        try:
            count = refresh_catalog(root, output, options['site_code'])
        except (OSError, ValueError) as exc:
            # Neither exception text nor HTML content belongs in worker logs.
            raise CommandError('Published page catalogue unavailable; prior evidence was not replaced.') from exc
        self.stdout.write(f'catalogue: {count} deployed pages')
