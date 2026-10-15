"""Batch, read-only plan status. Applied is not event verification."""
from .flow_runtime import runtime_enabled, runtime_environment
from .models import MarketingRuntimeBinding


def plan_runtime_summaries(loops):
    result = {str(loop.loop_key): {'state':'unavailable' if not runtime_enabled() else 'not_applied',
        'activeRevision':None, 'integrationId':None} for loop in loops}
    if not runtime_enabled():
        return result
    for binding in MarketingRuntimeBinding.objects.filter(loop__in=loops).select_related('loop','integration'):
        active = binding.active_revision_key
        result[str(binding.loop.loop_key)] = {
            'state': 'paused' if binding.paused else 'not_applied' if not active else
                'inactive' if (not binding.integration or not binding.integration.enabled or
                    binding.loop.status == 'archived' or binding.loop.environment != runtime_environment()) else 'applied',
            'activeRevision':str(active) if active else None,
            'integrationId':binding.integration_id,
        }
    return result
