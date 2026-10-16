"""Retry already-authorized file cleanup without re-running privacy actions."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from console.crm_files import attachment_storage_root, process_private_attachment_cleanup, reconcile_pending_uploads
from console.crm_upload_recovery import pending_upload_count, validate_upload_recovery_options
from leads.models import CrmAttachment
from sitecore.models import Site


class Command(BaseCommand):
    help = 'Preview or retry pending private attachment cleanup for one site; never retires active records.'

    def add_arguments(self, parser):
        parser.add_argument('--site', default='siteos_demo')
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--execute', action='store_true')
        parser.add_argument('--confirm', default='')
        parser.add_argument('--upload-cursor', default='')
        parser.add_argument('--uploads-only', action='store_true')

    def handle(self, *args, **options):
        if type(options['limit']) is not int or not 1 <= options['limit'] <= 100:
            raise CommandError('Limit must be between 1 and 100.')
        try:
            site = Site.objects.get(code=options['site'])
        except Site.DoesNotExist as error:
            raise CommandError('The site does not exist.') from error
        try:
            validate_upload_recovery_options(site_id=site.pk, limit=options['limit'], after=options['upload_cursor'])
        except ValidationError as error:
            raise CommandError('Upload recovery options are invalid.') from error
        pending = CrmAttachment.objects.filter(site=site).exclude(purge_storage_path='')
        retired_count = pending.count()
        upload_count = pending_upload_count(root=attachment_storage_root(), site_id=site.pk)
        self.stdout.write(json.dumps({'pending': retired_count, 'pending_uploads': upload_count, 'limit': options['limit']}))
        if not options['execute']:
            self.stdout.write('Preview only. No files or records were changed.')
            return
        if options['confirm'] != 'RETRY FILE CLEANUP':
            raise CommandError('Execution requires --confirm "RETRY FILE CLEANUP".')
        # Share one bounded budget; callers can page uploads independently when
        # retained shared files occupy the retirement queue.
        limit = options['limit']
        retired_budget = 0 if options['uploads_only'] else (max(1, limit // 2) if upload_count and retired_count else limit)
        result = (process_private_attachment_cleanup(site_id=site.pk, limit=retired_budget) if retired_budget
                  else {'processed': 0, 'removed': 0, 'pending': 0, 'skipped': 0})
        upload_budget = limit - result['processed']
        if upload_budget:
            result['upload_cleanup'] = reconcile_pending_uploads(site_id=site.pk, limit=upload_budget,
                                                                 after=options['upload_cursor'])
        result['remaining'] = pending.count()
        result['remaining_uploads'] = pending_upload_count(root=attachment_storage_root(), site_id=site.pk)
        self.stdout.write(json.dumps(result))
        if result['remaining'] or result['remaining_uploads']:
            self.stdout.write('Cleanup remains pending. Check filesystem access or retained shared references; retry later.')
        else:
            self.stdout.write('No pending physical attachment cleanup for this site.')
