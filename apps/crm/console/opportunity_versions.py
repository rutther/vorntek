from __future__ import annotations

from datetime import datetime
from secrets import compare_digest


def opportunity_version_token(value: datetime | None) -> str:
    """Serialize ``updated_at`` as the opaque optimistic-lock token.

    Microseconds and the UTC offset are always present, so a token survives an
    HTML round trip without relying on locale formatting or database-specific
    timestamp stringification.
    """

    if value is None:
        return ''
    return value.isoformat(timespec='microseconds')


def opportunity_version_matches(value: datetime | None, submitted: str | None) -> bool:
    expected = opportunity_version_token(value)
    candidate = str(submitted or '').strip()
    return bool(expected and candidate and compare_digest(expected, candidate))
