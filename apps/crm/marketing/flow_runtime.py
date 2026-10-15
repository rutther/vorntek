"""Versioned runtime control plane. No SSH, Meta writes or implicit activation."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from console.access import SYSTEM_ROLES, user_has_any_role
from console.flow_workspace_service import workspace, request_key
from .models import (MarketingIntegration, MarketingLoop, MarketingRuntimeBinding,
                     MarketingRuntimeRevision, MarketingRuntimeCommand)
from .runtime_contract import digest, normalize_draft


class RuntimeConflict(Exception):
    pass


def credential_available(integration):
    from leads.services import resolve_meta_access_token
    try:
        return bool(resolve_meta_access_token(integration))
    except Exception:
        return False


def runtime_enabled():
    return bool(getattr(settings, 'SITEOS_META_RUNTIME_ENABLED', False))


def runtime_environment():
    return getattr(settings, 'SITEOS_META_RUNTIME_ENVIRONMENT', 'production')


def effective_revisions(site):
    if not runtime_enabled():
        return MarketingRuntimeRevision.objects.none()
    bindings = MarketingRuntimeBinding.objects.filter(loop__site=site, paused=False,
        integration__enabled=True, loop__environment=runtime_environment()).exclude(loop__status='archived')
    return MarketingRuntimeRevision.objects.filter(site=site, environment=runtime_environment(),
        revision_key__in=bindings.values('active_revision_key')).select_related('integration', 'binding__loop')


def integration_runtime_summary(integration):
    """Safe management summary; no secrets and no per-flow credential lookups."""
    from leads.models import LeadFormDefinition
    if not runtime_enabled() or integration.integration_type != 'pixel':
        return None
    bindings = list(MarketingRuntimeBinding.objects.filter(integration=integration)
        .select_related('loop').order_by('id'))
    revisions = {str(r.revision_key):r for r in MarketingRuntimeRevision.objects.filter(
        binding__in=bindings, revision_key__in=[b.active_revision_key for b in bindings if b.active_revision_key])}
    rows = []
    form_names = dict(LeadFormDefinition.objects.filter(site=integration.site).values_list('id', 'name'))
    for binding in bindings:
        active = revisions.get(str(binding.active_revision_key))
        scope = active.config_json if active else binding.draft_json
        rows.append({'name':binding.loop.name, 'url':f'/admin/marketing/plans/#/flow/{binding.loop.loop_key}',
            'state':'not_applied' if not active else 'paused' if binding.paused else 'inactive' if not integration.enabled or binding.loop.status=='archived' or
                binding.loop.environment!=runtime_environment() else 'applied' if active else 'not_applied',
            'scope':scope,
            'scopeLabel':'已应用范围' if active else '待应用范围',
            'formName':form_names.get(scope.get('formId'), '未选择'),
            'version':binding.version, 'environment':binding.loop.environment})
    return {'flows':rows, 'count':len(rows), 'environment':integration.environment,
        'mode':integration.routing_mode, 'enabled':integration.enabled}


def runtime_snapshot(loop):
    from leads.models import LeadFormDefinition
    from .runtime_dependencies import runtime_dependencies
    state = workspace(loop)
    binding = MarketingRuntimeBinding.objects.filter(loop=loop).select_related('integration').first()
    integration = binding.integration if binding else None
    draft = dict(binding.draft_json) if binding else {}
    active = (MarketingRuntimeRevision.objects.filter(binding=binding,
        revision_key=binding.active_revision_key).first() if binding and binding.active_revision_key else None)
    payload = {'draft': draft, 'pixelId': integration.public_id if integration else '',
        'environment': loop.environment, 'flowVersion': loop.desired_revision}
    checks = []
    def require(condition, code, message):
        if not condition:
            checks.append({'code': code, 'message': message})
    require(runtime_enabled(), 'runtime_disabled', '运行功能尚未由部署管理员启用。')
    require(getattr(settings, 'SITEOS_META_RUNTIME_LOADER_VERSION', 0) >= 2,
        'loader_upgrade', '先部署并核对网站加载器与发送进程的兼容版本。')
    require(loop.status != 'archived', 'archived', '流程已归档。')
    require(loop.environment == runtime_environment(), 'environment', '流程环境与本站发送环境不同。')
    require(not state.get('filterEnabled'), 'filter_unsupported', '筛选规则尚未接入本运行合同，请关闭筛选后应用。')
    require(bool(integration), 'integration', '先准备本数据集的实际接入。')
    require(bool(draft), 'scope', '先保存精确页面范围与实际表单。')
    if integration:
        require(integration.site_id == loop.site_id and integration.environment == loop.environment,
            'ownership', '接入站点或环境不匹配。')
        require(integration.public_id == state.get('configs', {}).get('dataset', {}).get('externalId'),
            'asset_changed', '登记的数据集与实际接入不同，不能静默替换。')
        require(integration.consent_category == 'marketing', 'consent', '此接入必须使用营销同意。')
        require(credential_available(integration), 'credential', '请在接入管理页安全录入可用的回传凭据。')
    if draft:
        try:
            normalize_draft(loop.site, draft)
            if state.get('pixelScopeManaged'):
                from .pixel_catalog import validate_scope
                validate_scope(loop.site,draft)
        except ValidationError:
            require(False, 'form_invalid', '页面或表单范围已失效，请重新保存范围。')
    overlaps = []
    if draft:
        for revision in effective_revisions(loop.site).exclude(binding=binding):
            paths = set(draft.get('landingPaths', []) + draft.get('leadPaths', []))
            if paths.intersection(revision.config_json.get('landingPaths', []) + revision.config_json.get('leadPaths', [])):
                overlaps.append({'pixelId': revision.pixel_id, 'flowName': revision.binding.loop.name,
                                 'samePixel': bool(integration and revision.integration_id == integration.pk)})
    legacy = list(MarketingIntegration.objects.filter(site=loop.site, provider__code='meta',
        integration_type='pixel', routing_mode='legacy_site', enabled=True).values_list('public_id', flat=True))
    return {'version': binding.version if binding else 0, 'flowVersion':loop.desired_revision,
        'forms':list(LeadFormDefinition.objects.filter(site=loop.site, status='active', channel='website',
            submission_event_name='Lead').values('id','code','name')),
        'history':[{'key':str(r.revision_key),'createdAt':r.created_at.isoformat(),'scope':r.config_json}
            for r in MarketingRuntimeRevision.objects.filter(binding=binding).order_by('-created_at')[:20]],
        'draft':draft, 'configHash':digest(payload),
        'dependencies':runtime_dependencies(loop.site, draft, active.config_json if active else None),
        'integration':({'id':integration.pk, 'pixelId':integration.public_id, 'name':integration.name,
            'environment':integration.environment, 'routingMode':integration.routing_mode,
            'enabled':integration.enabled,
            'editUrl':f'/admin/marketing/integrations/{integration.pk}/'} if integration else None),
        'activeRevision':({'key':str(active.revision_key), 'configHash':active.config_hash,
            'createdAt':active.created_at.isoformat(), 'scope':active.config_json} if active else None),
        'runtimeState': ('paused' if binding and binding.paused else
            'inactive' if active and (not integration.enabled or not runtime_enabled() or
                loop.environment != runtime_environment() or loop.status == 'archived') else
            'applied' if active else 'not_applied'),
        'configState': 'unapplied_changes' if active and active.config_hash != digest(payload) else 'saved' if draft else 'empty',
        'verificationState':'untested', 'blockingChecks':checks, 'overlaps':overlaps,
        'sharedFlows':integration_runtime_summary(integration)['flows'] if integration and runtime_enabled() else [],
        'preservedPixelIds':legacy, 'freshnessSeconds':60,
        'boundary':'页面范围也包含自然访问；暂停不撤回已发送事件，旧页面最多60秒刷新配置。'}


@transaction.atomic
def runtime_command(*, loop, actor, data):
    actions = {'prepare_integration', 'save_draft', 'apply', 'pause', 'resume', 'rollback'}
    action = data.get('action')
    if action not in actions:
        raise ValidationError('不支持此接入操作。')
    if action != 'save_draft' and not user_has_any_role(actor, SYSTEM_ROLES):
        raise PermissionDenied('只有系统管理员可以应用或暂停实际接入。')
    allowed = {'action', 'requestId', 'expectedVersion'} | {
        'save_draft':{'draft'}, 'apply':{'configHash','expectedFlowVersion'},
        'resume':{'configHash','expectedFlowVersion'},
        'rollback':{'configHash','expectedFlowVersion','targetRevision'},
    }.get(action, set())
    if set(data) - allowed:
        raise ValidationError('请求包含不支持的字段，不要在此填写凭据。')
    rid = request_key(data.get('requestId'))
    loop = MarketingLoop.objects.select_for_update().get(pk=loop.pk)
    if loop.status == 'archived':
        raise ValidationError('流程已归档，请先恢复。')
    binding, _ = MarketingRuntimeBinding.objects.get_or_create(loop=loop)
    fingerprint = digest(data)
    previous = MarketingRuntimeCommand.objects.filter(binding=binding, request_id=rid).first()
    if previous:
        if previous.digest != fingerprint or previous.actor_id != actor.pk:
            raise RuntimeConflict('相同请求标识对应不同内容，未修改接入。')
        return previous.result_json
    if type(data.get('expectedVersion')) is not int or data['expectedVersion'] != binding.version:
        raise RuntimeConflict('接入已被更新，请重新读取并核对；输入仍保留。')
    state = workspace(loop)
    if loop.provider.code != 'meta' or loop.route_code != 'website_form':
        raise ValidationError('此实际接入仅适用于 Meta 网站获客流程。')
    if action == 'prepare_integration':
        pixel = state.get('configs', {}).get('dataset', {}).get('externalId', '')
        if not isinstance(pixel, str) or not pixel.isdigit() or not 5 <= len(pixel) <= 30:
            raise ValidationError('先在01登记准确的数据集数字ID。')
        if binding.integration_id and binding.integration.public_id != pixel:
            raise ValidationError('已有接入不可替换，请建立新的流程。')
        from .integration_registry import ensure_meta_pixel_integration
        integration, _ = ensure_meta_pixel_integration(site=loop.site, provider=loop.provider,
            public_id=pixel, environment=loop.environment, name=loop.name[:170] + ' · 网站接入')
        binding.integration = integration
    elif action == 'save_draft':
        if state.get('pixelScopeManaged'):
            raise ValidationError('请返回04整步保存，不能单独改写共享表单与页面范围。')
        binding.draft_json = normalize_draft(loop.site, data.get('draft'))
    elif action == 'pause':
        if not binding.active_revision_key:
            raise ValidationError('本流程还没有活动接入。')
        binding.paused = True
    else:
        if binding.integration_id:
            binding.integration = MarketingIntegration.objects.select_for_update().get(pk=binding.integration_id)
        current = runtime_snapshot(loop)
        if data.get('configHash') != current['configHash'] or data.get('expectedFlowVersion') != loop.desired_revision:
            raise RuntimeConflict('影响预览已过期，请重新核对再应用。')
        if action == 'rollback':
            try:
                old = MarketingRuntimeRevision.objects.get(binding=binding, revision_key=data.get('targetRevision'))
            except (MarketingRuntimeRevision.DoesNotExist, ValidationError, ValueError):
                raise ValidationError('找不到本流程的目标版本。')
            if state.get('pixelScopeManaged') and str(old.config_json.get('formId')) != str(state.get('configs',{}).get('form',{}).get('formId')):
                raise ValidationError('目标版本使用另一张表单，请先返回04选择该表单并保存，再核对回滚。')
            binding.draft_json = normalize_draft(loop.site, old.config_json)
            binding.save()
            current = runtime_snapshot(loop)
        if current['blockingChecks']:
            raise ValidationError(current['blockingChecks'][0]['message'])
        if action == 'resume' and not binding.paused:
            raise ValidationError('本流程当前并未暂停。')
        revision = MarketingRuntimeRevision.objects.create(binding=binding,
            integration=binding.integration, site=loop.site, environment=loop.environment,
            pixel_id=binding.integration.public_id, flow_version=loop.desired_revision,
            config_hash=current['configHash'], config_json=binding.draft_json, created_by=actor)
        binding.active_revision_key = revision.revision_key
        binding.paused = False
        # New scoped records are harmless to legacy senders only after coordinated deployment.
        binding.integration.enabled = True
        binding.integration.save(update_fields=['enabled','updated_at'])
    binding.version += 1
    binding.save()
    result = runtime_snapshot(loop)
    MarketingRuntimeCommand.objects.create(binding=binding, request_id=rid, actor=actor,
        digest=fingerprint, action=action, result_json=result)
    return result
