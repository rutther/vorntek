"""Read-only PostgreSQL contract for ContentAccessGrant constraints and indexes."""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

from check_candidate_postgres_contract import require_safe_candidate_dsn


EXPECTED_CAPABILITIES = {
    'content.read',
    'content.write',
    'content.set_published',
    'content.locale.manage',
    'assets.read',
    'assets.write',
    'assets.import_local',
    'releases.read',
    'releases.preview_build',
}

EXPECTED_INDEXES = {
    'uq_content_grant_user_site_active': True,
    'uq_content_grant_user_locale_active': True,
    'uq_content_grant_group_site_active': True,
    'uq_content_grant_group_locale_active': True,
    'idx_content_grant_user_lookup': False,
    'idx_content_grant_group_lookup': False,
}

EXPECTED_COLUMN_TYPES = {
    'id': ('int8', 'NO'),
    'user_id': ('int4', 'YES'),
    'group_id': ('int4', 'YES'),
    'site_id': ('int8', 'NO'),
    'locale_id': ('int8', 'YES'),
    'capability': ('text', 'NO'),
    'enabled': ('bool', 'NO'),
    'granted_by_user_id': ('int4', 'YES'),
    'reason': ('text', 'NO'),
    'created_at': ('timestamptz', 'NO'),
    'updated_at': ('timestamptz', 'NO'),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Read-only ContentAccessGrant constraint check for local candidate PostgreSQL.'
    )
    parser.add_argument(
        '--dsn',
        default=os.getenv('SITEOS_CANDIDATE_POSTGRES_URL', ''),
        help='Local candidate PostgreSQL DSN; database name must end in _test.',
    )
    return parser.parse_args()


def normalized(value: str) -> str:
    return ' '.join(value.lower().replace('"', '').split())


def require_fragment(definition: str, fragment: str, *, label: str) -> None:
    if normalized(fragment) not in normalized(definition):
        raise SystemExit(f'{label} is missing contract fragment: {fragment}')


def main() -> int:
    args = parse_args()
    _parsed, expected_database = require_safe_candidate_dsn(args.dsn)

    with psycopg.connect(args.dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SET default_transaction_read_only = on')
            cursor.execute(
                'SELECT current_database(), current_setting(%s), to_regclass(%s)::oid',
                ['default_transaction_read_only', 'content_access_grant'],
            )
            database_name, read_only, table_oid = cursor.fetchone()
            if database_name != expected_database:
                raise SystemExit(
                    f'Connected database mismatch: expected {expected_database!r}, got {database_name!r}.'
                )
            if read_only != 'on':
                raise SystemExit('PostgreSQL did not enable the read-only session guard.')
            if table_oid is None:
                raise SystemExit('Missing table: content_access_grant')

            cursor.execute(
                '''
                SELECT column_name, udt_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'content_access_grant'
                ''',
            )
            column_types = {
                column_name: (udt_name, is_nullable)
                for column_name, udt_name, is_nullable in cursor.fetchall()
            }
            if column_types != EXPECTED_COLUMN_TYPES:
                raise SystemExit(
                    'ContentAccessGrant column contract mismatch: '
                    f'expected {EXPECTED_COLUMN_TYPES!r}, got {column_types!r}.'
                )

            cursor.execute(
                '''
                SELECT conname, pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = %s
                ''',
                [table_oid],
            )
            constraints = dict(cursor.fetchall())

            subject_check = constraints.get('content_access_grant_subject_xor', '')
            require_fragment(subject_check, 'user_id IS NULL', label='subject XOR check')
            require_fragment(subject_check, 'group_id IS NULL', label='subject XOR check')
            require_fragment(subject_check, '<>', label='subject XOR check')

            capability_check = constraints.get('content_access_grant_capability_check', '')
            for capability in EXPECTED_CAPABILITIES:
                require_fragment(
                    capability_check,
                    capability,
                    label='capability whitelist check',
                )

            locale_site_fk = constraints.get('content_access_grant_locale_site_fk', '')
            require_fragment(
                locale_site_fk,
                'FOREIGN KEY (site_id, locale_id) REFERENCES site_locale(site_id, id)',
                label='locale/site composite foreign key',
            )

            foreign_key_fragments = {
                'content_access_grant_user_id_fkey': (
                    'FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE',
                    'user subject foreign key',
                ),
                'content_access_grant_group_id_fkey': (
                    'FOREIGN KEY (group_id) REFERENCES auth_group(id) ON DELETE CASCADE',
                    'group subject foreign key',
                ),
                'content_access_grant_site_id_fkey': (
                    'FOREIGN KEY (site_id) REFERENCES site(id) ON DELETE CASCADE',
                    'site foreign key',
                ),
                'content_access_grant_locale_id_fkey': (
                    'FOREIGN KEY (locale_id) REFERENCES site_locale(id) ON DELETE CASCADE',
                    'locale foreign key',
                ),
                'content_access_grant_granted_by_user_id_fkey': (
                    'FOREIGN KEY (granted_by_user_id) REFERENCES auth_user(id) ON DELETE SET NULL',
                    'grantor foreign key',
                ),
            }
            for constraint_name, (fragment, label) in foreign_key_fragments.items():
                require_fragment(
                    constraints.get(constraint_name, ''),
                    fragment,
                    label=label,
                )

            cursor.execute(
                '''
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'content_access_grant'
                ''',
            )
            indexes = dict(cursor.fetchall())
            for name, must_be_unique in EXPECTED_INDEXES.items():
                definition = indexes.get(name, '')
                if not definition:
                    raise SystemExit(f'Missing ContentAccessGrant index: {name}')
                require_fragment(definition, 'WHERE', label=name)
                require_fragment(definition, 'enabled', label=name)
                if must_be_unique:
                    require_fragment(definition, 'CREATE UNIQUE INDEX', label=name)

            cursor.execute(
                '''
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'site_locale'
                  AND indexname = 'uq_site_locale_site_id_id'
                ''',
            )
            site_locale_index = cursor.fetchone()
            if site_locale_index is None:
                raise SystemExit('Missing site_locale(site_id, id) unique index.')
            require_fragment(
                site_locale_index[0],
                'CREATE UNIQUE INDEX',
                label='site_locale composite identity index',
            )
            require_fragment(
                site_locale_index[0],
                '(site_id, id)',
                label='site_locale composite identity index',
            )

    print(
        'ContentAccessGrant PostgreSQL contract passed: exact column types/nullability, '
        'subject XOR, capability whitelist, foreign-key delete semantics, locale/site '
        'integrity, four active unique scopes, and two lookup indexes.'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
