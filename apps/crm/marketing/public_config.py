from __future__ import annotations

import re
from django.conf import settings

from .models import MarketingIntegration


GOOGLE_TAG_ID_PATTERN = re.compile(r'^(?:AW-\d+|GT-[A-Z0-9]+)$', re.IGNORECASE)


def normalize_google_tag_id(value: object) -> str:
    tag_id = str(value or '').strip().upper()
    return tag_id if GOOGLE_TAG_ID_PATTERN.fullmatch(tag_id) else ''


def public_measurement_config(*, site_code: str = 'siteos_demo', site=None, path='/') -> dict[str, object]:
    disabled = {
        'meta_pixel_enabled': False, 'meta_pixel_id': '',
        'google_tag_enabled': False, 'google_tag_id': '', 'consent_mode': 'v2',
        'schemaVersion': 2, 'metaPixels': [], 'measurementContext': '',
    }
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return disabled
    if site is not None:
        site_code = site.code
    pixel = MarketingIntegration.objects.filter(
        site__code=site_code, provider__code='meta', integration_type='pixel',
        routing_mode='legacy_site', enabled=True,
    ).order_by('id').first()
    pixel_id = str(pixel.public_id or '').strip() if pixel else ''
    pixel_enabled = bool(re.fullmatch(r'\d{5,25}', pixel_id))
    integration = (
        MarketingIntegration.objects.select_related('provider')
        .filter(
            site__code=site_code,
            provider__code='google',
            integration_type='data_manager',
        )
        .order_by('id')
        .first()
    )
    config = dict(integration.config_json or {}) if integration else {}
    tag_id = normalize_google_tag_id(config.get('google_tag_id'))
    tag_enabled = bool(config.get('google_tag_enabled')) and bool(tag_id)
    result = {
        'meta_pixel_enabled': pixel_enabled if site is None else False,
        'meta_pixel_id': pixel_id if pixel_enabled and site is None else '',
        'google_tag_enabled': tag_enabled,
        'google_tag_id': tag_id if tag_enabled else '',
        'consent_mode': 'v2',
    }
    if site is None:
        return result
    from .runtime_contract import normalize_path, LEGACY_EVENTS, digest, FRESH_SECONDS
    from .flow_runtime import effective_revisions, runtime_environment
    from .measurement_context import issue_measurement_context
    path = normalize_path(path)
    pixels = {}
    for legacy in MarketingIntegration.objects.filter(site=site, provider__code='meta',
            integration_type='pixel', routing_mode='legacy_site', environment=runtime_environment(), enabled=True):
        pixels[legacy.public_id] = {'pixelId':legacy.public_id, 'events':list(LEGACY_EVENTS),
                                   'revisionKeys':[], 'legacy':True}
    revisions, forms = [], []
    for revision in effective_revisions(site):
        config = revision.config_json
        events = []
        if path in config['landingPaths']:
            events.append('PageView')
        if path in config['leadPaths']:
            events.append('Lead')
            forms.append(config['formId'])
            revisions.append(str(revision.revision_key))
        if not events or revision.pixel_id != revision.integration.public_id:
            continue
        item = pixels.setdefault(revision.pixel_id, {'pixelId':revision.pixel_id,
            'events':[], 'revisionKeys':[], 'legacy':False})
        item['events'] = sorted(set(item['events'] + events))
        item['revisionKeys'].append(str(revision.revision_key))
    result.update(schemaVersion=2, metaPixels=list(pixels.values()), path=path,
        configVersion=digest(list(pixels.values())), maxAgeSeconds=FRESH_SECONDS,
        measurementContext=issue_measurement_context(site=site, path=path, revisions=revisions, form_ids=forms))
    return result
