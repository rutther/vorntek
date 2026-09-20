from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection

from siteos_admin.release import release_version
from siteos_admin.schema_install import Migration, load_chain, pending_migrations


LEDGER = 'siteos_schema_migration'


def migration_ledger_report(
    chain: list[Migration],
    *,
    tables: set[str],
    recorded: dict[str, str],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        'chain_count': len(chain),
        'chain_tip': chain[-1].name if chain else None,
        'recorded_count': len(recorded),
        'recorded_tip': next(reversed(recorded), None),
        'pending': [],
        'status': 'ok',
        'error': None,
    }
    if tables and LEDGER not in tables:
        report.update(
            status='invalid',
            error='existing_database_without_installation_ledger',
        )
        return report
    business_tables = {
        name
        for name in tables
        if not name.startswith(('auth_', 'django_')) and name != LEDGER
    }
    if LEDGER in tables and not recorded and business_tables:
        report.update(status='invalid', error='business_tables_without_recorded_baseline')
        return report
    try:
        pending = pending_migrations(chain, recorded)
    except CommandError as error:
        report.update(status='invalid', error=str(error))
        return report
    report['pending'] = [migration.name for migration in pending]
    return report


def _database_report(chain: list[Migration]) -> dict[str, Any]:
    report: dict[str, Any] = {
        'vendor': connection.vendor,
        'reachable': False,
        'ledger': None,
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
            tables = set(connection.introspection.table_names(cursor))
            recorded: dict[str, str] = {}
            if LEDGER in tables:
                cursor.execute(
                    'SELECT name, checksum FROM siteos_schema_migration ORDER BY name'
                )
                recorded = dict(cursor.fetchall())
    except Exception:
        report['error'] = 'database_unavailable'
        return report
    report['reachable'] = True
    if connection.vendor != 'postgresql':
        report['error'] = 'postgresql_required_for_install_or_upgrade'
        return report
    report['ledger'] = migration_ledger_report(
        chain,
        tables=tables,
        recorded=recorded,
    )
    return report


def _path_state(path: Path) -> dict[str, bool]:
    resolved = path.resolve()
    return {
        'exists': resolved.exists(),
        'directory': resolved.is_dir(),
        'readable': os.access(resolved, os.R_OK),
        'writable': os.access(resolved, os.W_OK),
    }


def _storage_report() -> dict[str, Any]:
    attachment_root = Path(
        os.getenv(
            'SITEOS_CRM_ATTACHMENT_STORAGE_ROOT',
            settings.BASE_DIR.parent.parent / 'storage' / 'crm-attachments',
        )
    )
    return {
        'static': _path_state(Path(settings.STATIC_ROOT)),
        'attachments': _path_state(attachment_root),
        'whatsapp_media': _path_state(Path(settings.SITEOS_WHATSAPP_MEDIA_ROOT)),
        'asset_allowlist_configured': bool(
            os.getenv('SITEOS_ASSET_STORAGE_ROOTS', '').strip()
            or os.getenv('SITEOS_ASSET_STORAGE_ROOT', '').strip()
        ),
    }


def build_preflight_report() -> dict[str, Any]:
    chain = load_chain(Path(settings.BASE_DIR) / 'db' / 'migrations')
    database = _database_report(chain)
    outbound = {
        'external_io': bool(settings.NEWCROWN_ALLOW_EXTERNAL_IO),
        'scheduled_tasks': bool(settings.NEWCROWN_RUN_SCHEDULED_TASKS),
        'whatsapp_live_send': bool(settings.SITEOS_WHATSAPP_ALLOW_LIVE_SEND),
    }
    outbound['paused_for_upgrade'] = not any(outbound.values())
    ledger = database.get('ledger') or {}
    ready = bool(
        database.get('vendor') == 'postgresql'
        and database.get('reachable')
        and ledger.get('status') == 'ok'
        and outbound['paused_for_upgrade']
    )
    return {
        'service': 'vorntek-crm',
        'version': release_version(),
        'database': database,
        'storage': _storage_report(),
        'outbound': outbound,
        'ready_for_upgrade': ready,
    }
