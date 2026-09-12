from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


MIGRATIONS = (
    (
        '0024_content_access_grants.sql',
        'a56bdf849f972e0a759e7dc18568215b46dcbb4e323ff47e0655bc0b5fe9e6cc',
    ),
    (
        '0025_task_activity_integrity.sql',
        'f911f13b0b1b738687a4c14947b97a020b2375b44288a1c6d706826e10783b47',
    ),
    (
        '0026_whatsapp_crm_core.sql',
        '3138780f4e1bc1a130e97efd9f9b9d73a6409b09b746858300164c1581681c66',
    ),
)
CONFIRM_TOKEN = 'APPLY-WHATSAPP-CRM-UPGRADE'
EXPECTED_TABLES = (
    'content_access_grant',
    'crm_mutation_receipt',
    'whatsapp_template',
    'whatsapp_conversation',
    'whatsapp_message',
    'whatsapp_media',
    'whatsapp_delivery_event',
)


def migration_directory() -> Path:
    return Path(settings.BASE_DIR) / 'db' / 'migrations'


def verified_sql(filename: str, expected_sha256: str) -> str:
    path = migration_directory() / filename
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CommandError(f'无法读取迁移文件 {filename}: {exc}') from exc
    try:
        sql = payload.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise CommandError(f'{filename} 不是有效的 UTF-8 SQL。') from exc

    # Git archives produced on Windows may contain CRLF even when the checkout
    # uses LF. PostgreSQL treats those line endings identically, so pin the
    # semantic UTF-8 SQL content after normalizing all line endings to LF.
    normalized_sql = sql.replace('\r\n', '\n').replace('\r', '\n')
    actual = hashlib.sha256(normalized_sql.encode('utf-8')).hexdigest()
    if actual != expected_sha256:
        raise CommandError(
            f'{filename} 规范化 SHA256 不匹配；'
            f'期望 {expected_sha256}，实际 {actual}。'
        )
    return normalized_sql


def without_embedded_transaction(sql: str, *, filename: str) -> str:
    """Keep the release upgrade inside one Django-owned transaction."""

    stripped = sql.strip()
    if filename != '0026_whatsapp_crm_core.sql':
        return stripped
    lines = stripped.splitlines()
    if not lines or lines[0].strip().upper() != 'BEGIN;':
        raise CommandError(f'{filename} 缺少预期的 BEGIN; 边界。')
    if lines[-1].strip().upper() != 'COMMIT;':
        raise CommandError(f'{filename} 缺少预期的 COMMIT; 边界。')
    return '\n'.join(lines[1:-1]).strip()


def advisory_lock_id() -> int:
    digest = hashlib.sha256(b'siteos.whatsapp-crm.production-upgrade.v1').digest()
    return int.from_bytes(digest[:8], byteorder='big', signed=True)


class Command(BaseCommand):
    help = (
        '校验并以单事务方式执行 WhatsApp CRM 的 0024-0026 非托管 SQL。'
        '默认仅输出计划；生产写入必须显式 --apply 和确认令牌。'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='实际执行迁移；省略时只做文件校验和数据库状态检查。',
        )
        parser.add_argument(
            '--confirm',
            default='',
            help=f'实际执行时必须等于 {CONFIRM_TOKEN}。',
        )

    def handle(self, *args, **options):
        scripts = []
        for filename, expected_sha256 in MIGRATIONS:
            sql = verified_sql(filename, expected_sha256)
            scripts.append((filename, without_embedded_transaction(sql, filename=filename)))
            self.stdout.write(f'OK {filename} {expected_sha256}')

        self.stdout.write(f'Database vendor: {connection.vendor}')
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT name, to_regclass(name) IS NOT NULL "
                    "FROM unnest(%s::text[]) AS name",
                    [list(EXPECTED_TABLES)],
                )
                for table_name, exists in cursor.fetchall():
                    state = 'present' if exists else 'missing'
                    self.stdout.write(f'{state}: {table_name}')

        if not options['apply']:
            self.stdout.write(self.style.WARNING('PLAN ONLY: 未修改数据库。'))
            return

        if connection.vendor != 'postgresql':
            raise CommandError('正式 WhatsApp CRM 升级只允许在 PostgreSQL 上执行。')
        if options['confirm'] != CONFIRM_TOKEN:
            raise CommandError(f'拒绝执行：--confirm 必须等于 {CONFIRM_TOKEN}。')

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("SET LOCAL statement_timeout = '120s'")
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [advisory_lock_id()])
                for filename, sql in scripts:
                    self.stdout.write(f'Applying {filename} ...')
                    cursor.execute(sql)
                cursor.execute(
                    "SELECT name FROM unnest(%s::text[]) AS name "
                    "WHERE to_regclass(name) IS NULL",
                    [list(EXPECTED_TABLES)],
                )
                missing = [row[0] for row in cursor.fetchall()]
                if missing:
                    raise CommandError(
                        '迁移后仍缺少表，整笔事务已回滚：' + ', '.join(missing)
                    )

        self.stdout.write(self.style.SUCCESS('WhatsApp CRM 0024-0026 已在单一事务中完成。'))
