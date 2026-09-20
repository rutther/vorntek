from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from siteos_admin.preflight import build_preflight_report


class Command(BaseCommand):
    help = 'Report release, migration, storage and outbound-I/O readiness without writes.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument(
            '--strict',
            action='store_true',
            help='Exit nonzero unless PostgreSQL/ledger checks pass and outbound writers are paused.',
        )

    def handle(self, *args, **options):
        report = build_preflight_report()
        if options['as_json']:
            self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            database = report['database']
            ledger = database.get('ledger') or {}
            self.stdout.write(f"Service: {report['service']} {report['version']}")
            self.stdout.write(
                f"Database: vendor={database['vendor']} reachable={database['reachable']}"
            )
            if ledger:
                self.stdout.write(
                    'Migrations: '
                    f"chain={ledger['chain_count']} recorded={ledger['recorded_count']} "
                    f"pending={len(ledger['pending'])} status={ledger['status']}"
                )
            elif database.get('error'):
                self.stdout.write(f"Migration check: {database['error']}")
            self.stdout.write(
                'Outbound writers paused: '
                f"{report['outbound']['paused_for_upgrade']}"
            )
            self.stdout.write(f"Ready for upgrade: {report['ready_for_upgrade']}")
        if options['strict'] and not report['ready_for_upgrade']:
            raise CommandError('Release preflight did not pass; no changes were made.')
