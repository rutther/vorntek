from __future__ import annotations

import re
from django.conf import settings

from .models import MarketingIntegration


GOOGLE_TAG_ID_PATTERN = re.compile(r'^(?:AW-\d+|GT-[A-Z0-9]+)$', re.IGNORECASE)


def normalize_google_tag_id(value: object) -> str:
    tag_id = str(value or '').strip().upper()
    return tag_id if GOOGLE_TAG_ID_PATTERN.fullmatch(tag_id) else ''


def public_measurement_config(*, site_code: str = 'siteos_demo') -> dict[str, object]:
    disabled = {
        'meta_pixel_enabled': False, 'meta_pixel_id': '',
        'google_tag_enabled': False, 'google_tag_id': '', 'consent_mode': 'v2',
    }
    if not settings.NEWCROWN_ALLOW_EXTERNAL_IO:
        return disabled
    pixel = MarketingIntegration.objects.filter(
        site__code=site_code, provider__code='meta', integration_type='pixel', enabled=True,
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
    return {
        'meta_pixel_enabled': pixel_enabled,
        'meta_pixel_id': pixel_id if pixel_enabled else '',
        'google_tag_enabled': tag_enabled,
        'google_tag_id': tag_id if tag_enabled else '',
        'consent_mode': 'v2',
    }
