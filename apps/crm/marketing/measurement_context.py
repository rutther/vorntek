"""Short-lived signed configuration context; not visitor authentication."""
from urllib.parse import urlsplit
from django.core import signing
from django.core.exceptions import ValidationError
from sitecore.models import Site
from .runtime_contract import CONTEXT_SECONDS, normalize_path, site_origins
from .flow_runtime import runtime_environment

SALT = 'siteos.measurement-context.v2'


def resolve_measurement_site(request):
    # Deliberately ignore X-Forwarded-Host and caller-selected site IDs.
    host = str(request.META.get('HTTP_HOST', '')).lower()
    if not host or any(char in host for char in '/\\@ \r\n'):
        return None
    matches = [site for site in Site.objects.filter(enabled=True)
               if host in {urlsplit(origin).netloc for origin in site_origins(site)}]
    return matches[0] if len(matches) == 1 else None


def issue_measurement_context(*, site, path, revisions, form_ids):
    return signing.dumps({'v':2, 'site':site.pk, 'environment':runtime_environment(),
        'path':normalize_path(path), 'revisions':sorted(set(revisions)),
        'forms':sorted(set(form_ids))}, salt=SALT, compress=True)


def validate_measurement_context(*, site, form, path, token):
    if not isinstance(token, str) or not token or len(token) > 12000:
        return None
    try:
        data = signing.loads(token, salt=SALT, max_age=CONTEXT_SECONDS)
        if (not isinstance(data,dict) or data.get('v') != 2 or data.get('site') != site.pk
            or data.get('environment') != runtime_environment() or form.site_id != site.pk
            or data.get('path') != normalize_path(path) or form.pk not in data.get('forms', [])):
            return None
        return data
    except (signing.BadSignature, ValueError, TypeError, ValidationError):
        return None
