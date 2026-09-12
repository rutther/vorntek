"""Retired reset entry point; pure compatibility helpers for synthetic tests only."""

from __future__ import annotations

import os

QUALIFICATION_KEYS = (
    'contactable',
    'company_verified',
    'project_confirmed',
    'technical_fit',
    'next_step_confirmed',
)


def qualification_state_for_score(score: int) -> dict[str, bool]:
    """Return the canonical five-check qualification state for a demo score."""
    if not isinstance(score, int) or isinstance(score, bool) or score < 0 or score > 100 or score % 20:
        raise ValueError('Demo qualification scores must be integer multiples of 20 from 0 to 100.')

    completed_count = score // 20
    state = {
        key: index < completed_count
        for index, key in enumerate(QUALIFICATION_KEYS)
    }
    assert sum(state.values()) * 20 == score
    return state


def candidate_role_usernames() -> dict[str, str]:
    """Return local-only demo accounts keyed by the canonical role constants."""

    # Importing this pure helper must not configure Django or access a database.
    from console.access import (
        ROLE_CONTENT_OPS,
        ROLE_MARKETING_OPS,
        ROLE_SALES,
        ROLE_SALES_MANAGER,
        ROLE_SYSTEM_ADMIN,
    )

    return {
        ROLE_SYSTEM_ADMIN: 'local-admin',
        ROLE_MARKETING_OPS: 'local-marketing',
        ROLE_CONTENT_OPS: 'local-content',
        ROLE_SALES_MANAGER: 'local-manager',
        ROLE_SALES: 'local-sales',
    }


def candidate_demo_password() -> str:
    """Load a caller-supplied local password without embedding a shared default."""

    password = os.environ.get('SITEOS_CANDIDATE_PASSWORD', '')
    if len(password) < 12:
        raise SystemExit(
            'SITEOS_CANDIDATE_PASSWORD must be set to a local-only password of at least 12 characters.'
        )
    return password


def main() -> int:
    raise SystemExit(
        "Legacy schema-reset seeder is retired. Use manage.py seed_vorntek_demo "
        "to preview safe synthetic customer initialization; see docs/DEMO_DATA.md."
    )


if __name__ == "__main__":
    main()
