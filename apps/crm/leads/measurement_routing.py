"""Immutable per-submission targets. No fallback to newly added integrations."""
from uuid import UUID
from django.conf import settings
from django.core.exceptions import ValidationError
from marketing.models import MarketingIntegration
from marketing.flow_runtime import runtime_enabled, runtime_environment, effective_revisions
from marketing.measurement_context import validate_measurement_context
from marketing.runtime_contract import source_path
from .models import LeadMeasurementRoute


def legacy_integrations(site):
    return MarketingIntegration.objects.select_related('provider').filter(site=site,
        provider__code='meta', integration_type='pixel', enabled=True, routing_mode='legacy_site',
        environment=runtime_environment()).order_by('pk')


def capture_submission_routes(submission, token):
    """Called inside the inquiry insert transaction, including explicit empty routes."""
    event_id = str((submission.identifiers_json or {}).get('client_event_id') or submission.submission_key)
    targets = {}
    if (submission.consent_json or {}).get('marketing') and submission.form.capi_enabled:
        for integration in legacy_integrations(submission.site):
            targets[integration.pk] = {'integration':integration, 'revisions':[], 'mode':'legacy_site',
                'events':{key: getattr(submission.form, attr) for key, attr in (
                    ('submitted','submission_event_name'),('contacted','contacted_event_name'),
                    ('qualified','qualified_event_name'),('won','won_event_name'))}}
    context = None
    try:
        path = source_path(submission.site, submission.source_url)
        context = validate_measurement_context(site=submission.site, form=submission.form, path=path, token=token)
    except ValidationError:
        path = ''
    if context and (submission.consent_json or {}).get('marketing') and submission.form.capi_enabled:
        for revision in effective_revisions(submission.site).filter(revision_key__in=context['revisions']):
            if revision.config_json['formId'] != submission.form_id or path not in revision.config_json['leadPaths']:
                continue
            if revision.pixel_id != revision.integration.public_id:
                continue
            target = targets.setdefault(revision.integration_id, {'integration':revision.integration,
                'revisions':[], 'events':{'submitted':'Lead'}, 'mode':'flow_scoped'})
            target['revisions'].append(str(revision.revision_key))
    for target in targets.values():
        LeadMeasurementRoute.objects.create(submission=submission, integration=target['integration'],
            pixel_id=target['integration'].public_id, event_id=event_id,
            revision_keys=target['revisions'], event_contract=target['events'], routing_mode=target['mode'])
    submission.config_json = {**(submission.config_json or {}), 'measurement_routing_version':2,
        'measurement_context_status':'validated' if context else 'missing_or_invalid'}
    submission.save(update_fields=['config_json','updated_at'])


def integration_sendable(submission, integration, stage_key):
    if (submission.trashed_at is not None or not integration.enabled or integration.site_id != submission.site_id or
        integration.environment != runtime_environment() or not submission.form.capi_enabled or
        not (submission.consent_json or {}).get('marketing') or submission.stage == 'spam' or
        (submission.config_json or {}).get('archived') or (submission.config_json or {}).get('privacy_deleted')):
        return False
    if not (submission.config_json or {}).get('measurement_routing_version'):
        return integration.routing_mode == 'legacy_site'
    route = LeadMeasurementRoute.objects.filter(submission=submission, integration=integration).first()
    if not route or route.pixel_id != integration.public_id or stage_key not in route.event_contract:
        return False
    if route.routing_mode == 'legacy_site':
        return integration.routing_mode == 'legacy_site'
    return runtime_enabled() and effective_revisions(submission.site).filter(
        integration=integration, revision_key__in=route.revision_keys).exists()


def select_meta_integrations(submission, stage_key):
    if not (submission.config_json or {}).get('measurement_routing_version'):
        return list(legacy_integrations(submission.site))
    routes = LeadMeasurementRoute.objects.filter(submission=submission).select_related('integration__provider')
    return [route.integration for route in routes if integration_sendable(submission,route.integration,stage_key)]


def browser_receipt(result, payload):
    submission = result.submission
    event_id = str((submission.identifiers_json or {}).get('client_event_id') or submission.submission_key)
    received_id = str(payload.get('client_event_id') or payload.get('event_id') or '')
    retry = not result.duplicate
    if result.duplicate:
        try:
            retry = UUID(received_id).version == 4 and received_id == event_id
        except (ValueError, TypeError, AttributeError):
            retry = False
    consent = payload.get('consent') or {}
    allowed = (settings.NEWCROWN_ALLOW_EXTERNAL_IO and retry
               and isinstance(consent, dict) and consent.get('marketing') is True)
    pixels, revisions = [], []
    if allowed and (submission.config_json or {}).get('measurement_routing_version') == 2:
        for route in LeadMeasurementRoute.objects.filter(submission=submission).select_related('integration'):
            if integration_sendable(submission, route.integration, 'submitted'):
                pixels.append(route.pixel_id)
                revisions.extend(route.revision_keys)
    return {'eventName':'Lead', 'eventId':event_id, 'pixelIds':sorted(set(pixels)),
        'revisionKeys':sorted(set(revisions)), 'shouldTrack':bool(pixels),
        'routingStatus':(submission.config_json or {}).get('measurement_context_status','legacy_unverified')}
