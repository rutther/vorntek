"""Optional content/file pack; default planning needs no database or storage."""
import json

from django.core.management.base import BaseCommand, CommandError

from console.content_examples import apply_content_bundle, load_content_bundle
from console.example_bundle import _silent


class Command(BaseCommand):
    help = 'Plan optional synthetic content/files. Import needs --apply --synthetic-only --owner.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--synthetic-only', action='store_true')
        parser.add_argument('--site', default='siteos_demo')
        parser.add_argument('--owner')
        parser.add_argument('--reset', action='store_true', help='Validate pristine ownership and retain all identifiers/files; never delete or overwrite later work.')

    def handle(self, *args, **options):
        data, digest, _ = load_content_bundle()
        result = {'result':'PLAN ONLY', 'pack':data['pack'], 'source_digest':digest,
                  'notice':data['notice'], 'operation':'validate/retain' if options['reset'] else 'import',
                  'modules':{name:len(data[name]) for name in ('assets','attachments','articles','profiles','placements')}}
        if options['apply']:
            if not options['synthetic_only']:
                raise CommandError('Writing requires --synthetic-only for the named synthetic target.')
            _silent()
            if not options['owner']:
                raise CommandError('Writing requires an existing --owner; no accounts are created or changed.')
            result.update(apply_content_bundle(site_code=options['site'], owner_name=options['owner'], reset=options['reset']))
        self.stdout.write(json.dumps(result, ensure_ascii=False))
