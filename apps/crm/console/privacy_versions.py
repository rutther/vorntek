from __future__ import annotations

import hashlib
import json

from django.core import signing


PRIVACY_REQUEST_VERSION_SALT = 'console.privacy-request-version.v1'
RETENTION_POLICY_VERSION_SALT = 'console.retention-policy-version.v1'


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def privacy_request_version_payload(item) -> dict[str, object]:
    return {
        'id': item.pk,
        'site_id': item.site_id,
        'submission_id': item.submission_id,
        'handled_by_user_id': item.handled_by_user_id,
        'request_type': item.request_type,
        'status': item.status,
        'requester_name': item.requester_name,
        'requester_email': item.requester_email,
        'requester_phone': item.requester_phone,
        'subject_key': item.subject_key,
        'request_json': item.request_json or {},
        'resolution': item.resolution,
        'verified_at': item.verified_at.isoformat() if item.verified_at else None,
        'completed_at': item.completed_at.isoformat() if item.completed_at else None,
        'updated_at': item.updated_at.isoformat() if item.updated_at else None,
    }


def retention_policy_version_payload(policy) -> dict[str, object]:
    return {
        'id': policy.pk,
        'site_id': policy.site_id,
        'enabled': bool(policy.enabled),
        'lead_pii_retention_days': policy.lead_pii_retention_days,
        'inbound_payload_retention_days': policy.inbound_payload_retention_days,
        'outbox_payload_retention_days': policy.outbox_payload_retention_days,
        'privacy_request_retention_days': policy.privacy_request_retention_days,
        'whatsapp_message_retention_days': policy.whatsapp_message_retention_days,
        'whatsapp_media_retention_days': policy.whatsapp_media_retention_days,
        'updated_by_user_id': policy.updated_by_user_id,
        'config_json': policy.config_json or {},
        'updated_at': policy.updated_at.isoformat() if policy.updated_at else None,
    }


def _token(payload: dict[str, object], *, salt: str) -> str:
    return signing.dumps({'digest': _digest(payload)}, salt=salt, compress=True)


def _matches(token: str, payload: dict[str, object], *, salt: str) -> bool:
    try:
        decoded = signing.loads(str(token or ''), salt=salt)
    except signing.BadSignature:
        return False
    return isinstance(decoded, dict) and decoded.get('digest') == _digest(payload)


def privacy_request_version_token(item) -> str:
    return _token(
        privacy_request_version_payload(item),
        salt=PRIVACY_REQUEST_VERSION_SALT,
    )


def privacy_request_version_matches(token: str, item) -> bool:
    return _matches(
        token,
        privacy_request_version_payload(item),
        salt=PRIVACY_REQUEST_VERSION_SALT,
    )


def retention_policy_version_token(policy) -> str:
    return _token(
        retention_policy_version_payload(policy),
        salt=RETENTION_POLICY_VERSION_SALT,
    )


def retention_policy_version_matches(token: str, policy) -> bool:
    return _matches(
        token,
        retention_policy_version_payload(policy),
        salt=RETENTION_POLICY_VERSION_SALT,
    )
