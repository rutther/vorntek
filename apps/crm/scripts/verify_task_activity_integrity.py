#!/usr/bin/env python
"""Rollback-only direct PostgreSQL checks for migration 0025 integrity rules."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ADMIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADMIN_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'siteos_admin.settings')

import django  # noqa: E402

django.setup()

from django.db import DatabaseError, connection, transaction  # noqa: E402


def expect_database_rejection(label, statement, parameters=()):
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(statement, parameters)
    except DatabaseError:
        print(f'PASS reject: {label}')
        return
    raise SystemExit(f'FAIL accepted invalid SQL: {label}')


def scalar(statement, parameters=()):
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        row = cursor.fetchone()
    return row[0] if row else None


def main():
    if connection.vendor != 'postgresql':
        raise SystemExit('This verification requires PostgreSQL.')

    task = None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, site_id, owner_user_id, team_id, company_id
            FROM crm_task
            WHERE status IN ('open', 'in_progress') AND company_id IS NOT NULL
            ORDER BY id
            LIMIT 1
            """
        )
        task = cursor.fetchone()
    if not task:
        raise SystemExit('No actionable company-linked task is available for verification.')
    task_id, site_id, owner_user_id, team_id, company_id = task
    activity_id = scalar('SELECT id FROM crm_activity ORDER BY id LIMIT 1')
    outsider_user_id = scalar(
        """
        SELECT u.id FROM auth_user u
        WHERE NOT EXISTS (
          SELECT 1 FROM sales_team_member m WHERE m.team_id = %s AND m.user_id = u.id
        )
        ORDER BY u.id LIMIT 1
        """,
        (team_id,),
    )
    if activity_id is None or outsider_user_id is None:
        raise SystemExit('Candidate fixtures lack an activity or out-of-team user.')

    with transaction.atomic():
        expect_database_rejection(
            'actionable task cannot carry outcome',
            "UPDATE crm_task SET outcome = 'invalid' WHERE id = %s",
            (task_id,),
        )
        expect_database_rejection(
            'task team cannot diverge from target team',
            'UPDATE crm_task SET team_id = NULL WHERE id = %s',
            (task_id,),
        )
        expect_database_rejection(
            'task owner must belong to target team',
            """
            INSERT INTO crm_task(
              site_id, company_id, owner_user_id, team_id, created_by_user_id,
              title, description, task_type, priority, status, due_at, outcome
            ) VALUES (%s, %s, %s, %s, %s, 'invalid owner', '', 'follow_up',
                      'normal', 'open', now() + interval '1 day', '')
            """,
            (site_id, company_id, outsider_user_id, team_id, owner_user_id),
        )
        expect_database_rejection(
            'task requires at least one target',
            """
            INSERT INTO crm_task(
              site_id, owner_user_id, team_id, created_by_user_id,
              title, description, task_type, priority, status, due_at, outcome
            ) VALUES (%s, %s, %s, %s, 'no target', '', 'follow_up',
                      'normal', 'open', now() + interval '1 day', '')
            """,
            (site_id, owner_user_id, team_id, owner_user_id),
        )
        expect_database_rejection(
            'activity is append-only',
            "UPDATE crm_activity SET subject = 'mutated' WHERE id = %s",
            (activity_id,),
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sales_team(site_id, code, name, enabled) VALUES (%s, %s, %s, true) RETURNING id",
                (site_id, f'integrity_{task_id}', 'Rollback integrity team'),
            )
            foreign_team_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO crm_contact(
                  site_id, company_id, owner_user_id, team_id, full_name
                ) VALUES (%s, %s, %s, %s, 'Rollback contact') RETURNING id
                """,
                (site_id, company_id, owner_user_id, foreign_team_id),
            )
            foreign_contact_id = cursor.fetchone()[0]
        expect_database_rejection(
            'activity targets cannot cross teams',
            """
            INSERT INTO crm_activity(
              site_id, company_id, contact_id, actor_user_id, activity_type,
              direction, subject, body, metadata_json, occurred_at
            ) VALUES (%s, %s, %s, %s, 'note', 'internal', 'invalid scope', '', '{}'::jsonb, now())
            """,
            (site_id, company_id, foreign_contact_id, owner_user_id),
        )

        required_objects = {
            'crm_task_completion_check': scalar(
                "SELECT count(*) FROM pg_constraint WHERE conname = 'crm_task_completion_check'"
            ),
            'crm_task_validate_relationship': scalar(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'crm_task_validate_relationship' AND NOT tgisinternal"
            ),
            'crm_activity_validate_relationship': scalar(
                "SELECT count(*) FROM pg_trigger WHERE tgname = 'crm_activity_validate_relationship' AND NOT tgisinternal"
            ),
            'idx_crm_task_attention': scalar(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_crm_task_attention'"
            ),
            'idx_crm_activity_task_id': scalar(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_crm_activity_task_id'"
            ),
            'idx_crm_mutation_receipt_expiry': scalar(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_crm_mutation_receipt_expiry'"
            ),
        }
        missing = [name for name, count in required_objects.items() if count != 1]
        if missing:
            raise SystemExit(f'FAIL missing PostgreSQL objects: {missing}')
        transaction.set_rollback(True)

    print('Task/activity PostgreSQL integrity contract passed (all fixture writes rolled back).')


if __name__ == '__main__':
    main()
