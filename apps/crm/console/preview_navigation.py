"""Choose an installed preview workflow without weakening executor permissions."""
import os

from django.conf import settings
from django.urls import reverse

from .content_access import CONTENT_READ, RELEASES_READ, RELEASES_PREVIEW_BUILD


def custom_preview_configured() -> bool:
    configured = getattr(settings, 'SITEOS_PREVIEW_WORKSPACE_ROOT', '')
    return bool(str(configured or os.getenv('SITEOS_PREVIEW_WORKSPACE_ROOT', '')).strip())


def preview_workflow_entry(capabilities) -> dict[str, str]:
    if {CONTENT_READ, RELEASES_READ, RELEASES_PREVIEW_BUILD}.issubset(capabilities):
        return {'url': reverse('console:article_release_preview'), 'method': 'get'}
    if RELEASES_PREVIEW_BUILD in capabilities and custom_preview_configured():
        return {'url': reverse('console:release_build_preview'), 'method': 'post'}
    return {'url': '', 'method': ''}
