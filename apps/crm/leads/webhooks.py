from __future__ import annotations

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from leads.inbound import (
    InboundEventError,
    integration_secret,
    meta_leadgen_event_is_allowed,
    meta_leadgen_events,
    parse_json_body,
    record_inbound_event,
    record_and_process_event,
    verify_sha256_signature,
    webhook_verify_challenge,
    whatsapp_webhook_events,
)
from leads.whatsapp_ycloud import (
    verify_ycloud_signature,
    whatsapp_transport_provider,
    ycloud_event_matches_integration,
    ycloud_webhook_events,
)
from marketing.models import MarketingIntegration


def _enabled_integrations(provider_code: str, integration_type: str):
    return MarketingIntegration.objects.select_related('provider').filter(
        provider__code=provider_code,
        integration_type=integration_type,
        enabled=True,
        site__isnull=False,
    )


def _challenge_response(request, integrations):
    challenge = webhook_verify_challenge(
        mode=str(request.GET.get('hub.mode') or ''),
        token=str(request.GET.get('hub.verify_token') or ''),
        challenge=str(request.GET.get('hub.challenge') or ''),
        integrations=integrations,
    )
    return HttpResponse(challenge, content_type='text/plain') if challenge is not None else HttpResponse(status=403)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def meta_leadgen_webhook(request):
    integrations = list(_enabled_integrations('meta', 'leadgen'))
    if request.method == 'GET':
        return _challenge_response(request, integrations)
    try:
        payload = parse_json_body(request.body)
    except InboundEventError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    events = meta_leadgen_events(payload)
    by_page_id = {integration.public_id: integration for integration in integrations}
    if not events:
        return JsonResponse({'ok': True, 'received': 0})
    matched = [(event, by_page_id.get(event['page_id'])) for event in events]
    if any(integration is None for _, integration in matched):
        return JsonResponse({'ok': False, 'error': 'Page ID 未配置或接入未启用。'}, status=403)
    signature = str(request.headers.get('X-Hub-Signature-256') or '')
    if not all(
        verify_sha256_signature(request.body, signature, integration_secret(integration, 'app_secret'))
        for _, integration in matched
    ):
        return JsonResponse({'ok': False, 'error': 'Webhook 签名无效。'}, status=403)
    if any(not meta_leadgen_event_is_allowed(integration, event) for event, integration in matched):
        return JsonResponse({'ok': False, 'error': 'Form ID 未配置或不在接入白名单。'}, status=403)
    queued = 0
    processed = 0
    for event, integration in matched:
        try:
            receipt, created = record_inbound_event(
                integration=integration,
                provider_code='meta',
                event_type='leadgen',
                external_event_id=event['leadgen_id'],
                payload=event,
            )
        except InboundEventError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=409)
        queued += int(created)
        processed += int(receipt.status == 'processed')
    return JsonResponse({
        'ok': True,
        'received': len(matched),
        'queued': queued,
        'processed': processed,
    })


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def whatsapp_webhook(request):
    integrations = list(_enabled_integrations('whatsapp', 'cloud_api'))
    if request.method == 'GET':
        return _challenge_response(request, integrations)
    try:
        payload = parse_json_body(request.body)
    except InboundEventError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    events = whatsapp_webhook_events(payload)
    by_phone_number_id = {integration.public_id: integration for integration in integrations}
    if not events:
        signature = str(request.headers.get('X-Hub-Signature-256') or '')
        if not any(
            verify_sha256_signature(request.body, signature, integration_secret(integration, 'app_secret'))
            for integration in integrations
        ):
            return JsonResponse({'ok': False, 'error': 'Webhook 签名无效。'}, status=403)
        return JsonResponse({'ok': True, 'received': 0})
    matched = [(event, by_phone_number_id.get(event['phone_number_id'])) for event in events]
    if any(integration is None for _, integration in matched):
        return JsonResponse({'ok': False, 'error': 'Phone Number ID 未配置或接入未启用。'}, status=403)
    if any(
        str((integration.config_json or {}).get('waba_id') or '').strip()
        and event['waba_id'] != str((integration.config_json or {}).get('waba_id') or '').strip()
        for event, integration in matched
    ):
        return JsonResponse({'ok': False, 'error': 'WABA ID 与接入配置不一致。'}, status=403)
    signature = str(request.headers.get('X-Hub-Signature-256') or '')
    if not all(
        verify_sha256_signature(request.body, signature, integration_secret(integration, 'app_secret'))
        for _, integration in matched
    ):
        return JsonResponse({'ok': False, 'error': 'Webhook 签名无效。'}, status=403)
    processed = 0
    for event, integration in matched:
        if event['event_type'] in {'message', 'message_echo'}:
            external_event_id = event['message_id']
        elif event['event_type'] == 'status':
            external_event_id = f"{event['message_id']}:{event['event_fingerprint']}"
        else:
            external_event_id = event['event_fingerprint']
        receipt, _ = record_and_process_event(
            integration=integration,
            provider_code='whatsapp',
            event_type=event['event_type'],
            external_event_id=external_event_id,
            payload=event,
        )
        processed += int(receipt.status == 'processed')
    return JsonResponse({'ok': True, 'received': len(matched), 'processed': processed})


@csrf_exempt
@require_http_methods(['POST'])
def ycloud_whatsapp_webhook(request):
    """Receive official Cloud API events delivered by YCloud Coexistence.

    This endpoint is intentionally separate from Meta's Graph webhook because
    the signature, event envelope and retry contract are provider-specific.
    Both paths converge on the same normalized CRM receipt pipeline.
    """

    integrations = []
    for integration in _enabled_integrations('whatsapp', 'cloud_api'):
        try:
            if whatsapp_transport_provider(integration) == 'ycloud':
                integrations.append(integration)
        except ValueError:
            continue
    try:
        payload = parse_json_body(request.body)
    except InboundEventError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    signature = str(request.headers.get('YCloud-Signature') or '')
    endpoint_id = str(request.headers.get('X-Webhook-Endpoint-ID') or '').strip()
    matched_integrations = []
    for integration in integrations:
        configured_endpoint_id = str(
            (integration.config_json or {}).get('ycloud_webhook_endpoint_id') or ''
        ).strip()
        if configured_endpoint_id and endpoint_id != configured_endpoint_id:
            continue
        try:
            secret = integration_secret(integration, 'ycloud_webhook_secret')
        except Exception:
            secret = ''
        if verify_ycloud_signature(request.body, signature, secret):
            matched_integrations.append(integration)
    if not matched_integrations:
        return JsonResponse({'ok': False, 'error': 'YCloud Webhook 签名或端点身份无效。'}, status=403)
    if len(matched_integrations) != 1:
        return JsonResponse({'ok': False, 'error': 'YCloud Webhook 无法唯一匹配接入配置。'}, status=409)
    integration = matched_integrations[0]
    if not ycloud_event_matches_integration(payload, integration=integration):
        return JsonResponse({'ok': False, 'error': 'YCloud WABA 或业务号码与接入配置不一致。'}, status=403)

    events = ycloud_webhook_events(payload, integration=integration)
    if not events:
        return JsonResponse({'ok': True, 'received': 0, 'processed': 0})
    processed = 0
    for event in events:
        if event['event_type'] in {'message', 'message_echo'}:
            external_event_id = event['message_id']
        elif event['event_type'] == 'status':
            external_event_id = f"{event['message_id']}:{event['event_fingerprint']}"
        else:
            external_event_id = str(event.get('provider_event_id') or '')
        receipt, _ = record_and_process_event(
            integration=integration,
            provider_code='whatsapp',
            event_type=event['event_type'],
            external_event_id=external_event_id,
            payload=event,
        )
        processed += int(receipt.status == 'processed')
    return JsonResponse({'ok': True, 'received': len(events), 'processed': processed})
