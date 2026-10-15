"""Evidence levels stay separate. Client reports never prove Meta reception."""
from collections import Counter
from django.core.exceptions import ValidationError
from django.utils import timezone
from leads.models import LeadMeasurementRoute, LeadEventOutbox
from .models import IntegrationCheck


def collect_runtime_evidence(binding):
    key = str(binding.active_revision_key or '')
    routes = list(LeadMeasurementRoute.objects.filter(integration=binding.integration)
                  .order_by('-id')[:501]) if key else []
    matching = [r for r in routes[:500] if key in r.revision_keys]
    events = list(LeadEventOutbox.objects.filter(integration=binding.integration,
        submission_id__in=[r.submission_id for r in matching], stage_key='submitted').order_by('-id'))
    counts = Counter(event.status for event in events)
    pending = [e.created_at for e in events if e.status in ('pending','failed','sending','processing')]
    observations = list(IntegrationCheck.objects.filter(integration=binding.integration,
        check_type='runtime_observation', details_json__revisionKey=key)
        .order_by('-checked_at', '-id')[:101]) if key else []
    current = observations[:100]
    observations_limited = len(observations) > 100
    return {'revisionKey':key, 'inquiries':len(matching), 'serverAccepted':counts['sent'],
        'pending':sum(counts[s] for s in ('pending','sending','processing')), 'failed':counts['failed'],
        'skipped':counts['skipped'], 'dedupVerified':False,
        'verificationState':'partial' if counts['sent'] or current else 'untested',
        'sampleLimited':len(routes)>500, 'observationsLimited':observations_limited,
        'scope':'最近500条目标记录中的当前运行版本' + (
            '；当前版本人工观察仅显示最近100条，更早观察未纳入' if observations_limited else ''),
        'recentEvents':[{'eventId':e.event_id,'status':e.status,'at':e.created_at.isoformat()} for e in events[:20]],
        'oldestPendingSeconds':int((timezone.now()-min(pending)).total_seconds()) if pending else 0,
        'observations':[{'source':c.details_json.get('source'), 'eventId':c.details_json.get('eventId'),
            'at':c.checked_at.isoformat(), 'level':'manual', 'currentCredential':
            c.details_json.get('integrationVersion') == binding.integration.updated_at.isoformat()} for c in current],
        'nextAction':'在 Meta 核对当前版本同一事件编号的浏览器、服务器与去重证据；API接收不等于去重通过。'}


def record_runtime_observation(*, binding, actor, data):
    allowed = {'source','eventId','revisionKey'}
    if set(data) - allowed or data.get('source') not in {'browser_call','meta_browser','meta_server','meta_dedup'}:
        raise ValidationError('只能登记指定通道的观察，不接收自动通过标记、凭据或客户资料。')
    if data.get('revisionKey') != str(binding.active_revision_key):
        raise ValidationError('运行版本已改变，请重新测试当前版本。')
    event_id = data.get('eventId')
    if not isinstance(event_id,str) or not 1 <= len(event_id) <= 120:
        raise ValidationError('请填写本次询盘的事件编号。')
    routes = LeadMeasurementRoute.objects.filter(integration=binding.integration, event_id=event_id)
    if not any(data['revisionKey'] in route.revision_keys for route in routes):
        raise ValidationError('事件编号不属于当前版本。')
    # Caller locks the binding. Retrying a lost response must not append the
    # same observation twice; a changed credential version remains a new check.
    check, _ = IntegrationCheck.objects.get_or_create(integration=binding.integration, actor_user=actor,
        check_type='runtime_observation', delivery_mode='live', status='unknown', evidence_level='manual',
        details_json={**data, 'integrationVersion':binding.integration.updated_at.isoformat()},
        defaults={'summary':'人工观察已登记；不作为自动接收或去重证明。'})
    return check
