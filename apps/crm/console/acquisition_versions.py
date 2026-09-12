from __future__ import annotations

import hashlib
import json

from django.core import signing


LEAD_FORM_VERSION_SALT = 'console.lead-form-version.v1'


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def lead_form_version_payload(form_definition) -> dict[str, object]:
    return {
        'id': form_definition.pk,
        'site_id': form_definition.site_id,
        'locale_id': form_definition.locale_id,
        'code': form_definition.code,
        'name': form_definition.name,
        'category': form_definition.category,
        'channel': form_definition.channel,
        'scope_type': form_definition.scope_type,
        'scope_value': form_definition.scope_value,
        'status': form_definition.status,
        'submission_event_name': form_definition.submission_event_name,
        'contacted_event_name': form_definition.contacted_event_name,
        'qualified_event_name': form_definition.qualified_event_name,
        'won_event_name': form_definition.won_event_name,
        'capi_enabled': bool(form_definition.capi_enabled),
        'notify_emails': form_definition.notify_emails,
        'success_message': form_definition.success_message,
        'form_schema': form_definition.form_schema or {},
        'config_json': form_definition.config_json or {},
        'updated_at': (
            form_definition.updated_at.isoformat()
            if form_definition.updated_at
            else None
        ),
    }


def lead_form_version_token(form_definition) -> str:
    return signing.dumps(
        {'digest': _digest(lead_form_version_payload(form_definition))},
        salt=LEAD_FORM_VERSION_SALT,
        compress=True,
    )


def lead_form_version_matches(token: str, form_definition) -> bool:
    try:
        decoded = signing.loads(
            str(token or ''),
            salt=LEAD_FORM_VERSION_SALT,
        )
    except signing.BadSignature:
        return False
    return (
        isinstance(decoded, dict)
        and decoded.get('digest')
        == _digest(lead_form_version_payload(form_definition))
    )
