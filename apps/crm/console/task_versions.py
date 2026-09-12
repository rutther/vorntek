from __future__ import annotations

from datetime import datetime

from django.core import signing


VERSION_SALT = 'new-crown.crm-task-version.v1'


def task_version_token(value: datetime | None) -> str:
    if value is None:
        return ''
    payload = value.isoformat(timespec='microseconds')
    return signing.Signer(salt=VERSION_SALT).sign_object(payload, compress=True)


def task_version_value(token: str | None) -> datetime | None:
    try:
        payload = signing.Signer(salt=VERSION_SALT).unsign_object(
            str(token or '').strip()
        )
        return datetime.fromisoformat(payload)
    except (signing.BadSignature, TypeError, ValueError):
        return None


def task_version_matches(value: datetime | None, submitted: str | None) -> bool:
    return bool(value and task_version_value(submitted) == value)
