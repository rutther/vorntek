import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from sitecore.models import FreePage, PageRoute, Site, SiteLocale


def default_input_dir() -> Path:
    return settings.BASE_DIR.parent.parent / 'content' / 'pages'


class Command(BaseCommand):
    help = 'Import editable content/page JSON files into page_route and free_page tables.'

    def add_arguments(self, parser):
        parser.add_argument('--site-code', default='siteos_demo')
        parser.add_argument('--locale', default='en')
        parser.add_argument('--input-dir', default=str(default_input_dir()))
        parser.add_argument('--glob', default='**/*.json')

    def handle(self, *args, **options):
        site = Site.objects.get(code=options['site_code'])
        locale = SiteLocale.objects.get(site=site, locale_code=options['locale'], enabled=True)
        input_dir = Path(options['input_dir']).resolve()
        files = sorted(input_dir.glob(options['glob']))

        if not files:
            raise CommandError(f'No JSON files found under {input_dir}')

        count = 0
        with transaction.atomic():
            for path in files:
                payload = json.loads(path.read_text(encoding='utf-8'))
                if payload.get('sourceType') != 'free_page':
                    continue

                route_data = payload.get('route') or {}
                social_data = payload.get('social') or {}
                page_data = payload.get('page') or {}
                route_path = route_data.get('path')
                if not route_path or not route_path.startswith('/'):
                    raise CommandError(f'Invalid route.path in {path}')

                route, _ = PageRoute.objects.update_or_create(
                    site=site,
                    locale=locale,
                    path=route_path,
                    defaults={
                        'route_type': route_data.get('routeType') or 'free_page',
                        'status': route_data.get('status') or 'published',
                        'page_title': route_data.get('title') or page_data.get('title') or route_path,
                        'meta_description': route_data.get('metaDescription') or page_data.get('summary') or '',
                        'canonical_url': route_data.get('canonicalUrl') or '',
                        'og_title': social_data.get('ogTitle') or page_data.get('title') or '',
                        'og_description': social_data.get('ogDescription') or route_data.get('metaDescription') or '',
                        'og_image_url': social_data.get('ogImageUrl') or '',
                        'robots': route_data.get('robots') or 'index,follow,max-image-preview:large',
                        'published_at': timezone.now() if route_data.get('status', 'published') == 'published' else None,
                    },
                )

                FreePage.objects.update_or_create(
                    route=route,
                    defaults={
                        'template_key': page_data.get('templateKey') or 'generic_page',
                        'title': page_data.get('title') or route.page_title,
                        'summary': page_data.get('summary') or route.meta_description,
                        'hero_json': page_data.get('hero') or {},
                        'body_json': page_data.get('body') or {},
                        'config_json': page_data.get('config') or {},
                    },
                )
                count += 1

        self.stdout.write(self.style.SUCCESS(f'Imported {count} free page content files from {input_dir}'))
