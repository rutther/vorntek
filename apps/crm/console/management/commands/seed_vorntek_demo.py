"""Opt-in additive seeding for an empty, isolated Vorntek customer database."""

import json
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from console.demo_data import MARKER, customer_fixtures
from leads.models import Company, CompanyContactPoint, CompanyPoolState, CustomerSource, SalesTeam
from marketing.models import MarketingIntegration
from sitecore.models import Site


class Command(BaseCommand):
    help = 'Preview 200 fictional customers; --apply --synthetic-only seeds an empty isolated Vorntek customer database.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--synthetic-only', action='store_true',
                            help='Confirm this target is an isolated fictional-data environment, not production.')

    def handle(self, *args, **options):
        rows = customer_fixtures()
        summary = {
            'result': 'PLAN ONLY', 'companies': len(rows),
            'business_lines': len({row['business_line'] for row in rows}),
            'sources': dict(Counter(row['source_type'] for row in rows)),
            'states': dict(Counter(row['state'] for row in rows)),
            'notice': MARKER,
        }
        if not options['apply']:
            self.stdout.write(json.dumps(summary, ensure_ascii=False))
            return
        if not options['synthetic_only']:
            raise CommandError('Writing requires --apply --synthetic-only for an isolated demo target.')
        if settings.NEWCROWN_ALLOW_EXTERNAL_IO or settings.SITEOS_WHATSAPP_ALLOW_LIVE_SEND:
            raise CommandError('External I/O and WhatsApp live sending must remain disabled.')
        if connection.vendor != 'postgresql':
            raise CommandError('Apply requires PostgreSQL and its transaction/table locking safeguards.')
        with transaction.atomic():
            with connection.cursor() as cursor:
                # Fail promptly rather than disrupting a busy application. Block all
                # company inserts until commit, including callers not using this CLI.
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute('LOCK TABLE crm_company IN SHARE ROW EXCLUSIVE MODE')
            if Company.objects.exists():
                raise CommandError('Customer database is not empty; refusing to duplicate or overwrite records.')
            if Site.objects.count() != 1:
                raise CommandError('Expected exactly one initialized Vorntek site.')
            site = Site.objects.select_for_update().filter(code='siteos_demo', name='Vorntek').first()
            if site is None:
                raise CommandError('Expected initialized Vorntek site; bootstrap does not rename existing sites.')
            if MarketingIntegration.objects.filter(enabled=True).exists():
                raise CommandError('Disable integrations before initializing fictional customer data.')
            team = SalesTeam.objects.filter(site=site, code='default').first()
            if team is None:
                raise CommandError('Run bootstrap_site to create the default sales team first.')
            now = timezone.now()
            for row in rows:
                evidence = {'synthetic': True, 'generator': 'vorntekDemoV1',
                            'business_line': row['business_line'], 'notice': MARKER}
                company = Company.objects.create(
                    site=site, team=team, name=row['name'], normalized_name=row['name'].lower(),
                    website=row['website'], industry=row['industry'], country=row['country'],
                    source_channel=row['source_type'], notes=MARKER,
                )
                CompanyPoolState.objects.create(
                    company=company, state=row['state'],
                    evidence_status='declared' if row['state'] == 'available' else 'pending' if row['state'] == 'review' else 'rejected',
                    archived_at=now if row['state'] == 'archived' else None,
                )
                CompanyContactPoint.objects.create(
                    site=site, company=company, channel='email', raw_value=row['email'],
                    normalized_value=row['email'], purpose='business',
                    status='do_not_contact', usage_status='restricted', evidence_json=evidence,
                )
                CustomerSource.objects.create(
                    site=site, company=company, source_type=row['source_type'],
                    intake_method=row['intake_method'], source_detail=f'{MARKER}; simulated source label only',
                    external_record_id=f'vorntekDemo:{row["number"]:03d}',
                    responsible_team=team, evidence_json=evidence, evidence_status='declared',
                )
        summary['result'] = 'CREATED'
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
