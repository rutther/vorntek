"""Plan/import/reset only the separately tracked synthetic example bundle."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from console.example_bundle import MODULES, load_bundle


class Command(BaseCommand):
    help = 'Plan optional synthetic examples. Writes require --apply --synthetic-only and an existing --owner.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--synthetic-only', action='store_true')
        parser.add_argument('--reset', action='store_true', help='Refresh pristine example dates in place; retain identifiers and immutable history, never purge data.')
        parser.add_argument('--site', default='siteos_demo')
        parser.add_argument('--owner')

    def handle(self, *args, **options):
        data, digest = load_bundle()
        result = {'result':'PLAN ONLY', 'pack':data['pack'], 'notice':data['notice'],
                  'operation':'reset' if options['reset'] else 'import', 'source_digest':digest,
                  'modules':{section:len(data[section]) for section in MODULES}}
        if options['apply']:
            if not options['synthetic_only']:
                raise CommandError('Writing requires --synthetic-only for the named synthetic target.')
            if (settings.NEWCROWN_ALLOW_EXTERNAL_IO or settings.NEWCROWN_RUN_SCHEDULED_TASKS
                    or settings.SITEOS_WHATSAPP_ALLOW_LIVE_SEND):
                raise CommandError('External delivery and scheduled tasks must remain disabled.')
            if not options['owner']:
                raise CommandError('Writing requires an existing --owner account; no account is created or changed.')
            from console.example_bundle import apply_bundle
            result.update(apply_bundle(site_code=options['site'], owner_name=options['owner'],
                                       reset=options['reset']))
        self.stdout.write(json.dumps(result, ensure_ascii=False))
