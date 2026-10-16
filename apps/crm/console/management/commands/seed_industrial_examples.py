"""Optional industrial articles and equipment assets; default is a read-only plan."""
import json
from django.core.management.base import BaseCommand, CommandError
from console.example_bundle import _silent
from console.industrial_examples import apply_industrial_bundle, load_industrial_bundle


class Command(BaseCommand):
    help = 'Plan industrial examples. Writing needs --apply --synthetic-only --owner.'

    def add_arguments(self, parser):
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--synthetic-only',action='store_true')
        parser.add_argument('--site',default='siteos_demo')
        parser.add_argument('--owner')

    def handle(self,*args,**options):
        data,articles,files,digest = load_industrial_bundle()
        result = {'result':'PLAN ONLY','pack':data['pack'],'source_digest':digest,
                  'articles':len(articles['versions']),'models':len(data['products']),
                  'files':len(files),'notice':data['notice']}
        if options['apply']:
            if not options['synthetic_only']:
                raise CommandError('Writing requires --synthetic-only for the named synthetic target.')
            _silent()
            if not options['owner']:
                raise CommandError('Writing requires an existing --owner; accounts are never created or changed.')
            result.update(apply_industrial_bundle(site_code=options['site'],owner_name=options['owner']))
        self.stdout.write(json.dumps(result,ensure_ascii=False))
