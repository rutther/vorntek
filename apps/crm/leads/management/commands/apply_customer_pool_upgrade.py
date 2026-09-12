from __future__ import annotations

import hashlib

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from .apply_whatsapp_crm_upgrade import verified_sql


MIGRATION = (
    '0027_customer_pool_core.sql',
    '07e0dc5f77f96c4fa9cd73aa2efee8ccd541a71d51ec247686d89ca238edbb1d',
)
CONFIRM_TOKEN = 'APPLY-CUSTOMER-POOL-UPGRADE'
EXPECTED_TABLES = (
    'crm_company_pool_state',
    'crm_company_contact_point',
    'crm_customer_source',
    'crm_customer_import_batch',
    'crm_customer_import_row',
    'crm_customer_export_job',
)


def without_embedded_transaction(sql: str) -> str:
    lines = sql.strip().splitlines()
    if not lines or lines[0].strip().upper() != 'BEGIN;':
        raise CommandError(f'{MIGRATION[0]} 缺少预期的 BEGIN; 边界。')
    if lines[-1].strip().upper() != 'COMMIT;':
        raise CommandError(f'{MIGRATION[0]} 缺少预期的 COMMIT; 边界。')
    return '\n'.join(lines[1:-1]).strip()


def advisory_lock_id() -> int:
    digest = hashlib.sha256(b'siteos.customer-pool.production-upgrade.v1').digest()
    return int.from_bytes(digest[:8], byteorder='big', signed=True)


class Command(BaseCommand):
    help = (
        '校验并以单事务方式执行客户公海 0027 非托管 SQL。'
        '默认仅输出计划；生产写入必须显式 --apply 和确认令牌。'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='实际执行迁移；省略时只做校验和状态检查。')
        parser.add_argument('--confirm', default='', help=f'实际执行时必须等于 {CONFIRM_TOKEN}。')

    def handle(self, *args, **options):
        filename, expected_sha256 = MIGRATION
        sql = without_embedded_transaction(verified_sql(filename, expected_sha256))
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
                    self.stdout.write(f'{"present" if exists else "missing"}: {table_name}')

        if not options['apply']:
            self.stdout.write(self.style.WARNING('PLAN ONLY: 未修改数据库。'))
            return
        if connection.vendor != 'postgresql':
            raise CommandError('正式客户公海升级只允许在 PostgreSQL 上执行。')
        if options['confirm'] != CONFIRM_TOKEN:
            raise CommandError(f'拒绝执行：--confirm 必须等于 {CONFIRM_TOKEN}。')

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("SET LOCAL statement_timeout = '120s'")
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [advisory_lock_id()])
                cursor.execute(sql)
                cursor.execute(
                    "SELECT name FROM unnest(%s::text[]) AS name "
                    "WHERE to_regclass(name) IS NULL",
                    [list(EXPECTED_TABLES)],
                )
                missing = [row[0] for row in cursor.fetchall()]
                if missing:
                    raise CommandError('迁移后仍缺少表，整笔事务已回滚：' + ', '.join(missing))
        self.stdout.write(self.style.SUCCESS('客户公海 0027 已在单一事务中完成。'))
