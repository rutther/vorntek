from __future__ import annotations

import json
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from console.article_delivery import ArticleDeliveryError
from console.website_recovery import reconcile_website_serving_cache
from sitecore.models import Site


_VERSION = re.compile(r'^[a-f0-9]{64}$')


class Command(BaseCommand):
    help = (
        'Plan or explicitly rebuild the derived website serving cache from the '
        'latest immutable deployment receipt and verified candidate files.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--site-code', required=True)
        parser.add_argument('--expect-database', required=True)
        parser.add_argument('--expect-deployment-id', type=int)
        parser.add_argument(
            '--expect-serving-version',
            help='Use "bundled" for the image baseline or an exact 64-character version.',
        )
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--writers-stopped', action='store_true')

    def handle(self, *args, **options):
        if options['apply'] and (
            not options['writers_stopped']
            or options['expect_deployment_id'] is None
            or options['expect_serving_version'] is None
        ):
            raise CommandError(
                'Apply requires --writers-stopped, --expect-deployment-id and '
                '--expect-serving-version.'
            )
        if options['apply'] and (
            settings.NEWCROWN_ALLOW_EXTERNAL_IO
            or settings.SITEOS_WHATSAPP_ALLOW_LIVE_SEND
            or settings.NEWCROWN_RUN_SCHEDULED_TASKS
        ):
            raise CommandError(
                'External I/O, WhatsApp live sending, and scheduled tasks must '
                'remain disabled while rebuilding the serving cache.'
            )
        if connection.vendor != 'postgresql':
            raise CommandError('Website serving-cache reconciliation requires PostgreSQL.')
        actual_database = str(connection.settings_dict.get('NAME') or '')
        if actual_database != options['expect_database']:
            raise CommandError('Connected database does not match --expect-database.')

        expected = options['expect_serving_version']
        if expected == 'bundled':
            expected = ''
        elif expected is not None and not _VERSION.fullmatch(expected):
            raise CommandError(
                '--expect-serving-version must be "bundled" or an exact version.'
            )
        site = Site.objects.filter(code=options['site_code'], enabled=True).first()
        if site is None:
            raise CommandError('Enabled site was not found.')
        try:
            result = reconcile_website_serving_cache(
                site=site,
                apply=options['apply'],
                expected_deployment_id=options['expect_deployment_id'],
                expected_serving_version=expected,
            )
        except ArticleDeliveryError as error:
            raise CommandError(error.code) from error
        result['observedServingVersion'] = (
            result['observedServingVersion'] or 'bundled'
        )
        self.stdout.write(json.dumps(result, sort_keys=True))
