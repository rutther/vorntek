from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from console.audit import record_audit
from console.privacy_retention import execute_retention_policy, retention_preview
from leads.models import RetentionPolicy


class Command(BaseCommand):
    help = 'Preview or execute the configured privacy retention policy for one site.'

    def add_arguments(self, parser):
        parser.add_argument('--site', default='siteos_demo')
        parser.add_argument('--execute', action='store_true')
        parser.add_argument('--confirm', default='')

    def handle(self, *args, **options):
        try:
            policy = RetentionPolicy.objects.select_related('site').get(site__code=options['site'])
        except RetentionPolicy.DoesNotExist as exc:
            raise CommandError('The site does not have a privacy retention policy.') from exc

        preview = retention_preview(policy)
        self.stdout.write(json.dumps(preview, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder))
        if not options['execute']:
            self.stdout.write(self.style.WARNING('Preview only. No data was changed.'))
            return
        if options['confirm'] != 'APPLY RETENTION':
            raise CommandError('Execution requires --confirm "APPLY RETENTION".')
        result = execute_retention_policy(policy=policy, actor=None)
        record_audit(
            actor='management-command',
            action='privacy_retention_policy_executed',
            entity_table='privacy_retention_policy',
            entity_id=policy.id,
            source='management_command',
            metadata=result,
        )
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, cls=DjangoJSONEncoder))
        self.stdout.write(self.style.SUCCESS('Privacy retention policy executed.'))
