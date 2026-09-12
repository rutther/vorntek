from __future__ import annotations

from datetime import datetime
from secrets import compare_digest


def account_version_token(value: datetime | None) -> str:
    """Return the opaque optimistic-lock token used by company/contact v2."""

    if value is None:
        return ''
    return value.isoformat(timespec='microseconds')


def account_version_matches(value: datetime | None, submitted: str | None) -> bool:
    expected = account_version_token(value)
    candidate = str(submitted or '').strip()
    return bool(expected and candidate and compare_digest(expected, candidate))
