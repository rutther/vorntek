"""Explicit, resource-bound navigation. No guessed origin or shared session state."""
from uuid import UUID
from urllib.parse import urlencode

from django.urls import reverse

from marketing.models import MarketingLoop, MarketingRuntimeBinding
from .flow_workspace_schema import TEMPLATE


def integration_guide_context(request, integration):
    if request.GET.get('origin') != 'guide':
        return None
    try:
        key = UUID(request.GET.get('flow', ''))
    except (ValueError, TypeError, AttributeError):
        return None
    # A matching dataset string alone is not enough to prove a binding.
    loop = MarketingLoop.objects.filter(loop_key=key, site_id=integration.site_id,
        provider_id=integration.provider_id, environment=integration.environment,
        route_code='website_form', template_version=TEMPLATE).first()
    if not loop or not MarketingRuntimeBinding.objects.filter(loop=loop, integration=integration).exists():
        return None
    return {'name': loop.name, 'key': str(key),
            'query': urlencode({'origin': 'guide', 'flow': str(key)}),
            'url': f'{reverse("console:marketing_guided_loop")}#/flow/{key}?view=tasks&node=server'}
