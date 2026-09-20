from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from .error_redaction import redact_event_error
from .models import AuditLog


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


def _json_ready(value):
    """Recursively normalize audit snapshots for PostgreSQL JSON fields."""
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    return _json_value(value)


def lead_submission_snapshot(submission) -> dict:
    return {
        'stage': submission.stage,
        'team_id': submission.team_id,
        'assignee_id': submission.assignee_id,
        'source_channel': submission.source_channel,
        'source_detail': submission.source_detail,
        'next_follow_up_at': _json_value(submission.next_follow_up_at),
        'follow_up_notes': submission.follow_up_notes,
        'qualification_score': submission.qualification_score,
        'qualification_json': submission.qualification_json or {},
        'qualification_notes': submission.qualification_notes,
        'qualification_overridden': submission.qualification_overridden,
        'buyer_value': _json_value(submission.buyer_value),
        'buyer_currency': submission.buyer_currency,
    }


def lead_form_snapshot(form_definition) -> dict:
    return {
        'site_id': form_definition.site_id,
        'locale_id': form_definition.locale_id,
        'code': form_definition.code,
        'name': form_definition.name,
        'category': form_definition.category,
        'channel': form_definition.channel,
        'scope_type': form_definition.scope_type,
        'scope_value': form_definition.scope_value,
        'status': form_definition.status,
        'capi_enabled': form_definition.capi_enabled,
        'submission_event_name': form_definition.submission_event_name,
        'contacted_event_name': form_definition.contacted_event_name,
        'qualified_event_name': form_definition.qualified_event_name,
        'won_event_name': form_definition.won_event_name,
        'notify_emails': form_definition.notify_emails,
        'success_message': form_definition.success_message,
        'form_schema': form_definition.form_schema or {},
        'config_json': form_definition.config_json or {},
    }


def integration_snapshot(integration) -> dict:
    config = dict(integration.config_json or {})
    return {
        'site_id': integration.site_id,
        'provider': integration.provider.code,
        'name': integration.name,
        'integration_type': integration.integration_type,
        'public_id': integration.public_id,
        'enabled': integration.enabled,
        'consent_category': integration.consent_category,
        'config_keys': sorted(config),
    }


def outbox_snapshot(outbox) -> dict:
    return {
        'status': outbox.status,
        'delivery_mode': outbox.delivery_mode,
        'attempts': outbox.attempts,
        'last_error': redact_event_error(outbox.last_error) if outbox.last_error else '',
        'provider_request_id': outbox.provider_request_id,
        'provider_received_at': _json_value(outbox.provider_received_at),
        'provider_processed_at': _json_value(outbox.provider_processed_at),
        'match_status': outbox.match_status,
        'last_attempt_at': _json_value(outbox.last_attempt_at),
        'next_attempt_at': _json_value(outbox.next_attempt_at),
        'dispatched_at': _json_value(outbox.dispatched_at),
    }


def privacy_request_snapshot(privacy_request) -> dict:
    return {
        'site_id': privacy_request.site_id,
        'submission_id': privacy_request.submission_id,
        'request_type': privacy_request.request_type,
        'status': privacy_request.status,
        'handled_by_user_id': privacy_request.handled_by_user_id,
        'resolution': privacy_request.resolution,
        'verified_at': _json_value(privacy_request.verified_at),
        'completed_at': _json_value(privacy_request.completed_at),
    }


def record_audit(
    *,
    actor,
    action: str,
    entity_table: str,
    entity_id: int | None,
    before: dict | None = None,
    after: dict | None = None,
    request=None,
    source: str = 'console',
    metadata: dict | None = None,
) -> AuditLog:
    request_id = ''
    request_metadata = dict(metadata or {})
    if request is not None:
        request_id = str(request.headers.get('X-Request-ID') or uuid4())
        request_metadata.update({
            'method': request.method,
            'path': request.path,
        })
    actor_user = actor if getattr(actor, 'is_authenticated', False) else None
    actor_name = actor_user.get_username() if actor_user else str(actor or 'system')
    return AuditLog.objects.create(
        actor=actor_name or 'system',
        actor_user=actor_user,
        action=action,
        entity_table=entity_table,
        entity_id=entity_id,
        before_json=_json_ready(before),
        after_json=_json_ready(after),
        request_id=request_id,
        source=source,
        metadata_json=_json_ready(request_metadata),
    )
