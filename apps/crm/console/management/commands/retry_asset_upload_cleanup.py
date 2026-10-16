"""Bounded reconciliation of journaled asset writes, never a whole-storage sweep."""
import json
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from console.asset_upload_recovery import reconcile_pending_assets
from console.forms import asset_storage_root
from console.storage_security import StorageSecurityError
from sitecore.models import Site


class Command(BaseCommand):
    help='Preview/retry owned failed asset writes for one site; preserves every referenced file.'

    def add_arguments(self,parser):
        parser.add_argument('--site',required=True)
        parser.add_argument('--limit',type=int,default=100)
        parser.add_argument('--after',default='')
        parser.add_argument('--execute',action='store_true')
        parser.add_argument('--confirm',default='')

    def handle(self,*args,**options):
        if options['execute'] and options['confirm']!='RETRY ASSET CLEANUP':
            raise CommandError('Execution requires --confirm "RETRY ASSET CLEANUP".')
        try:
            site=Site.objects.get(code=options['site'])
            result=reconcile_pending_assets(root=asset_storage_root(),site_id=site.pk,limit=options['limit'],
                after=options['after'],execute=options['execute'])
        except (Site.DoesNotExist,ValidationError,StorageSecurityError,OSError) as error:
            raise CommandError('Asset recovery site, storage, or options are unavailable.') from error
        self.stdout.write(json.dumps(result))
        if not options['execute']: self.stdout.write('Preview only. No files or records changed.')
