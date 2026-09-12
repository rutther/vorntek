import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from sitecore.models import FreePage, Site


def default_output_dir() -> Path:
    return settings.BASE_DIR.parent.parent / 'content' / 'pages'


def route_to_file(output_dir: Path, route_path: str) -> Path:
    parts = [part for part in route_path.strip('/').split('/') if part]
    if not parts:
        parts = ['index']
    if route_path.endswith('/') and len(parts) > 1:
        filename = f'{parts[-1]}.json'
        dirs = parts[:-1]
    else:
        filename = f'{parts[-1]}.json'
        dirs = parts[:-1]
    return output_dir.joinpath(*dirs, filename)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + '\n', encoding='utf-8')
    temporary.replace(path)


class Command(BaseCommand):
    help = 'Export database-managed free pages into editable content/page JSON files.'

    def add_arguments(self, parser):
        parser.add_argument('--site-code', default='siteos_demo')
        parser.add_argument('--locale', default='en')
        parser.add_argument('--output-dir', default=str(default_output_dir()))
        parser.add_argument('--route-prefix', default='/en/')

    def handle(self, *args, **options):
        site = Site.objects.get(code=options['site_code'])
        output_dir = Path(options['output_dir']).resolve()
        route_prefix = options['route_prefix']

        pages = (
            FreePage.objects.select_related('route', 'route__locale')
            .filter(route__site=site, route__locale__locale_code=options['locale'], route__path__startswith=route_prefix)
            .order_by('route__path')
        )

        count = 0
        for page in pages:
            route = page.route
            payload = {
                'schemaVersion': 1,
                'sourceType': 'free_page',
                'route': {
                    'path': route.path,
                    'routeType': route.route_type,
                    'status': route.status,
                    'title': route.page_title,
                    'metaDescription': route.meta_description,
                    'canonicalUrl': route.canonical_url,
                    'robots': route.robots,
                },
                'social': {
                    'ogTitle': route.og_title,
                    'ogDescription': route.og_description,
                    'ogImageUrl': route.og_image_url,
                },
                'page': {
                    'templateKey': page.template_key,
                    'title': page.title,
                    'summary': page.summary,
                    'hero': page.hero_json,
                    'body': page.body_json,
                    'config': page.config_json,
                },
            }
            atomic_write_json(route_to_file(output_dir, route.path), payload)
            count += 1

        self.stdout.write(self.style.SUCCESS(f'Exported {count} free page content files to {output_dir}'))
