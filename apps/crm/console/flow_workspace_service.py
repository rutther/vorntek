"""CRM control-plane records. Does not configure integrations or send events."""
import copy
import hashlib
import json
import re
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.http import Http404
from django.utils import timezone

from leads.models import LeadFormDefinition
from marketing.models import (MarketingIntegration, MarketingLoop, MarketingLoopAssetBinding,
    MarketingLoopEdge, MarketingLoopNode, MarketingLoopTask, MarketingProvider)
from .audit import record_audit
from .access import SYSTEM_ROLES, user_has_any_role
from .models import AuditLog
from .flow_workspace_schema import (TEMPLATE, NODES, clean_name, clean_positions,
                                   clean_values, task_keys)
from .marketing_loop_service import available_dataset_sources


class FlowConflict(Exception):
    def __init__(self, message, loop=None):
        super().__init__(message)
        self.loop = loop


def request_key(value):
    try:
        result = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('请求标识无效，请刷新后重试。')
    if result.version != 4:
        raise ValidationError('请求标识无效，请刷新后重试。')
    return result


def workspace(loop):
    data = (loop.config_json or {}).get('workspace_v1')
    if not isinstance(data, dict):
        raise ValidationError('此流程使用旧版模板，请从旧流程入口查看；不会自动覆盖旧配置。')
    return copy.deepcopy(data)


def serialize(loop):
    data = workspace(loop)
    keys = task_keys(data)
    return {
        'id': str(loop.loop_key), 'code': loop.binding_key, 'name': loop.name,
        'environment': loop.environment, 'provider': 'meta', 'preset': 'website',
        'note': data.get('note',''),
        'configs': data['configs'], 'drafts': data['drafts'],
        'filterEnabled': data['filterEnabled'], 'positions': data['positions'],
        'revision': loop.desired_revision, 'version': data['version'],
        'check': None, 'history': data['history'], 'archived': loop.status == 'archived',
        'guideClosed': bool(data.get('guideState', {}).get('closed')),
        'updatedAt': loop.updated_at.isoformat(),
        'nextTask': next((k for k in keys if k not in data['configs']), 'dataset'),
        'lastConfigured': data.get('lastConfigured', ''),
        'sources': available_dataset_sources(loop), 'datasetName': loop.binding_key + 'Ds',
        'templateVersion': loop.template_version, 'taskKeys': keys,
        'pixelScopeManaged':bool(data.get('pixelScopeManaged')),
    }


def lookup(site, key, *, lock=False):
    query = MarketingLoop.objects.select_related('provider')
    if lock:
        query = query.select_for_update(of=('self',))
    try:
        return query.get(site=site, loop_key=key, provider__code='meta',
                         route_code='website_form', template_version=TEMPLATE)
    except MarketingLoop.DoesNotExist:
        raise Http404('找不到此流程。')


def _graph(loop, data):
    """Materialize only this template's planned topology, never observed health."""
    keys = task_keys(data)
    nodes = {}
    for index, key in enumerate(keys, 1):
        title, domain, kind = NODES[key]
        node, _ = MarketingLoopNode.objects.update_or_create(loop=loop, node_key=key,
            defaults={'title':title,'actor':domain,'node_type':kind,'sequence_no':index,
                      'status':'configured' if key in data['configs'] else 'pending',
                      'summary':'配置已记录，外部待核验' if key in data['configs'] else '等待配置记录',
                      'desired_json':data['configs'].get(key,{})})
        nodes[key] = node
        MarketingLoopTask.objects.update_or_create(loop=loop, task_key=key,
            defaults={'node':node,'title':title,'owner_type':domain,'sequence_no':index,
                      'status':'waiting' if key in data['configs'] else 'ready',
                      'input_json':data['drafts'].get(key,{}),
                      'result_json':{'configuration_recorded':key in data['configs']},
                      'completed_at':None})
    MarketingLoopTask.objects.filter(loop=loop).exclude(task_key__in=keys).update(status='skipped')
    MarketingLoopNode.objects.filter(loop=loop).exclude(node_key__in=keys).update(status='paused')
    pairs = [('ad','website'),('website','form'),('website','pixel'),('form','crm'),
             ('pixel','dataset'),('server','dataset')]
    pairs += [('crm','filter'),('filter','server')] if data['filterEnabled'] else [('crm','server')]
    edge_keys = []
    for source, target in pairs:
        key = f'{source}_to_{target}'
        edge_keys.append(key)
        MarketingLoopEdge.objects.update_or_create(loop=loop,edge_key=key,
            defaults={'source_node':nodes[source],'target_node':nodes[target],
                      'signal_code':'planned_route','status':'pending'})
    MarketingLoopEdge.objects.filter(loop=loop).exclude(edge_key__in=edge_keys).delete()
    return nodes


@transaction.atomic
def create_flow(*, site, actor, data, request=None):
    type(site).objects.select_for_update().get(pk=site.pk)
    key = request_key(data.get('requestId'))
    name = clean_name(data.get('name'))
    environment = data.get('environment')
    note = data.get('note','')
    if not isinstance(note,str) or len(note) > 1000:
        raise ValidationError('备注须为不超过1000字的文字。')
    note = note.strip()
    if not isinstance(environment,str) or environment not in {'test','production'}:
        raise ValidationError('请选择正式业务或演练测试。')
    existing = MarketingLoop.objects.filter(loop_key=key).first()
    # Keep pre-note request fingerprints compatible when no note was supplied.
    fingerprint = hashlib.sha256(json.dumps({'name':name,'environment':environment,
        **({'note':note} if note else {})},sort_keys=True).encode()).hexdigest()
    if existing:
        if existing.site_id != site.pk or existing.created_by_id != actor.pk or existing.template_version != TEMPLATE or workspace(existing).get('creationFingerprint') != fingerprint:
            raise FlowConflict('请求标识已被使用，未创建重复流程。')
        return existing
    provider = MarketingProvider.objects.get(code='meta', enabled=True)
    # Site base_url can be an internal listener. Stable code is the naming scope.
    scope = ''.join(word[:1].upper()+word[1:].lower() for word in re.findall(r'[A-Za-z0-9]+',site.code))
    if len(scope) < 2 or not scope[0].isalpha():
        scope = f'Site{site.pk}'
    scope = scope[:80]
    seq = (MarketingLoop.objects.filter(site=site,provider=provider,route_code='website_form',
        environment=environment,scope_code=scope).aggregate(value=Max('sequence_no'))['value'] or 0) + 1
    if seq > 999:
        raise ValidationError('本站流程编号已用尽，请联系管理员。')
    state = {'version':1,'configs':{},'drafts':{},'positions':{},'filterEnabled':False,
             'history':[],'receipts':{},'lastConfigured':'','creationFingerprint':fingerprint,'note':note}
    loop = MarketingLoop.objects.create(loop_key=key,site=site,provider=provider,name=name,
        binding_key=f'ncMeta{"Tst" if environment=="test" else "Prd"}WebLead{scope}{seq:02d}',
        scope_code=scope,sequence_no=seq,route_code='website_form',environment=environment,
        template_version=TEMPLATE,status='draft',current_task_key='dataset',
        config_json={'workspace_v1':state},created_by=actor,updated_by=actor)
    nodes = _graph(loop,state)
    MarketingLoopAssetBinding.objects.create(loop=loop,node=nodes['dataset'],provider=provider,
        role='optimization_dataset',asset_type='dataset',expected_name=loop.binding_key+'Ds',
        reuse_mode='undecided',status='planned')
    record_audit(actor=actor,action='flow_workspace_created',entity_table='marketing_loop',
        entity_id=loop.pk,after={'environment':environment,'code':loop.binding_key,'version':1},request=request)
    return loop


def _bind_dataset(loop, values):
    binding = MarketingLoopAssetBinding.objects.select_for_update().get(loop=loop,role='optimization_dataset')
    dataset_id = values['externalId']
    if binding.external_id:
        if binding.external_id != dataset_id:
            raise ValidationError('此流程已关联其他数据集。不能直接覆盖，请新建流程或单独迁移。')
        recorded_source = workspace(loop)['configs'].get('dataset',{}).get('source')
        if recorded_source and values['source'] != recorded_source:
            raise ValidationError('已登记数据源的来源不能改写；可以修改本地名称备注。')
        binding.observed_name = values.get('alias','')
        binding.save(update_fields=['observed_name','updated_at'])
        return
    sources = {item['id']:item for item in available_dataset_sources(loop)}
    if values['source'] == 'existing':
        if dataset_id not in sources:
            raise ValidationError('请选择本站同环境已登记的数据源；不能跨站点或环境复用。')
        binding.reuse_mode = sources[dataset_id]['mode']
        binding.expected_name = sources[dataset_id]['name']
    else:
        # Serialize registrations for the same provider, including other sites.
        MarketingProvider.objects.select_for_update().get(pk=loop.provider_id)
        if MarketingLoopAssetBinding.objects.filter(provider=loop.provider,asset_type='dataset',external_id=dataset_id).exclude(loop=loop).exists() or MarketingIntegration.objects.filter(provider=loop.provider,integration_type='pixel',public_id=dataset_id).exists():
            raise ValidationError('该数据源已登记，请从同站点同环境已有数据源中复用；不得跨环境重复登记。')
        binding.reuse_mode = 'new'
    binding.external_id = dataset_id
    binding.observed_name = values.get('alias','')
    binding.status = 'recorded'
    binding.save(update_fields=['external_id','observed_name','expected_name','reuse_mode','status','updated_at'])


def preflight(loop, data, actor):
    configs = data['configs']
    checks = [{'nodeKey':key,'status':'recorded' if key in configs else 'pending',
               'message':'配置已记录，仍待实测' if key in configs else '尚未完成配置记录'} for key in task_keys(data)]
    dataset_id = configs.get('dataset',{}).get('externalId','')
    matching = bool(dataset_id) and loop.environment == 'production' and MarketingIntegration.objects.filter(
        site=loop.site,provider=loop.provider,integration_type='pixel',public_id=dataset_id,enabled=True).exists()
    checks.append({'nodeKey':'server','status':'recorded' if matching else 'pending',
        'message':'本站存在指向此数据集的已启用接入；这不证明事件投递成功。' if matching else '尚未确认本站可用回传接入与此数据集一致；未自动创建或启用接入。'})
    form_id = configs.get('form',{}).get('formId','')
    form = LeadFormDefinition.objects.filter(site=loop.site,pk=int(form_id)).first() if str(form_id).isdigit() else None
    checks.append({'nodeKey':'form','status':'recorded' if form and form.status=='active' else 'pending',
        'message':'关联表单当前启用；还需受控提交验证。' if form and form.status=='active' else '关联表单缺失或未启用，需重新核对。'})
    checks.append({'nodeKey':'server','status':'recorded' if form and form.capi_enabled else 'pending',
        'message':'表单已允许CAPI回传，仍需验证投递与去重。' if form and form.capi_enabled else '此表单尚未开启CAPI回传；流程记录不会自动开启它。'})
    if data['filterEnabled']:
        checks.append({'nodeKey':'filter','status':'pending','message':'筛选节点仅为计划记录，尚未应用到真实回传。'})
    return {'kind':'preflight','nodeKey':'server','result':'pending','note':'只读核对 CRM 配置；未请求 Meta、未发送事件，不构成连通验收。',
            'checks':checks,'reference':'CRM 当前配置','datasetId':dataset_id,
            'revision':loop.desired_revision,'at':timezone.now().isoformat(),'actor':actor.get_username()}


@transaction.atomic
def mutate_flow(*, site, actor, key, data, request=None):
    type(site).objects.select_for_update().get(pk=site.pk)
    loop = lookup(site,key,lock=True)
    state = workspace(loop)
    rid = str(request_key(data.get('requestId')))
    digest = hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    receipt = state['receipts'].get(rid)
    if receipt is None:
        # JSON receipts are a small cache only. The transactional audit row is
        # durable, including after PostgreSQL has reordered JSONB object keys.
        prior = AuditLog.objects.filter(entity_table='marketing_loop',entity_id=loop.pk,
            metadata_json__flow_request=rid).order_by('-id').first()
        receipt = (prior.metadata_json or {}).get('flow_receipt') if prior else None
    if receipt:
        if receipt['digest'] != digest or receipt['actor'] != actor.pk:
            raise FlowConflict('同一请求标识对应了不同操作，未覆盖数据。',loop)
        return lookup(site,receipt['createdId']) if receipt.get('createdId') else loop
    if type(data.get('expectedVersion')) is not int or data['expectedVersion'] != state['version']:
        raise FlowConflict('流程已在其他页面更新。当前输入已保留，请加载最新版本后核对再保存。',loop)
    action = data.get('action')
    if action in {'archive','restore'} and not user_has_any_role(actor, SYSTEM_ROLES):
        raise PermissionDenied('归档或恢复方案会改变运行选择，仅系统管理员可操作。')
    if loop.status == 'archived' and action not in {'restore','duplicate','close_guide','reopen_guide'}:
        raise ValidationError('流程已归档，请恢复后再修改。')
    old_revision = loop.desired_revision
    result = loop
    if action == 'rename':
        loop.name = clean_name(data.get('name'))
    elif action in {'close_guide','reopen_guide'}:
        # Guide lifecycle never writes bindings, applied revisions or integrations.
        state['guideState'] = {'closed':action == 'close_guide',
            'updatedAt':timezone.now().isoformat(), 'updatedBy':actor.pk}
    elif action in {'archive','restore'}:
        loop.status = 'archived' if action=='archive' else ('configuring' if state['configs'] else 'draft')
    elif action == 'duplicate':
        if MarketingLoop.objects.filter(loop_key=rid).exists():
            raise FlowConflict('此请求标识已被其他创建使用，未修改已有流程。',loop)
        result = create_flow(site=site,actor=actor,data={'requestId':rid,
            'name':loop.name[:190]+' · 副本','environment':loop.environment},request=request)
        target = workspace(result)
        target['filterEnabled'] = state['filterEnabled']
        target['positions'] = copy.deepcopy(state['positions'])
        result.config_json = {'workspace_v1':target}
        result.save(update_fields=['config_json','updated_at'])
        _graph(result,target)
    elif action == 'save_pixel_step':
        from .pixel_step_service import save_pixel_step
        save_pixel_step(loop=loop,state=state,data=data)
    elif action in {'save_config','save_draft'}:
        node = data.get('nodeKey')
        if node not in task_keys(state):
            raise ValidationError('此节点不在当前流程中。')
        values = clean_values(site,node,data.get('values'),draft=action=='save_draft')
        if state.get('pixelScopeManaged') and node in {'pixel','form'}:
            if node=='pixel' or values!=state['configs'].get('form'):
                raise ValidationError('请返回04统一修改网站接入与表单，避免两处配置不一致。')
        if action=='save_draft':
            state['drafts'][node] = values
        else:
            if node=='dataset':
                _bind_dataset(loop,values)
            if state['configs'].get(node) != values:
                loop.desired_revision += 1
            state['configs'][node] = values
            state['drafts'].pop(node,None)
        state['lastConfigured'] = node
    elif action == 'positions':
        state['positions'] = clean_positions(data.get('positions'),task_keys(state))
    elif action == 'filter':
        enabled = data.get('enabled')
        if not isinstance(enabled,bool):
            raise ValidationError('筛选节点状态无效。')
        if enabled != state['filterEnabled']:
            loop.desired_revision += 1
            state['filterEnabled'] = enabled
            if not enabled:
                state['configs'].pop('filter',None)
                state['drafts'].pop('filter',None)
                state['positions'].pop('filter',None)
    elif action in {'evidence','preflight'}:
        if len(state['history']) >= 1000:
            raise ValidationError('此流程已有1000条记录。请先联系管理员导出归档，旧记录未被删除。')
        if action=='preflight':
            entry = preflight(loop,state,actor)
        else:
            node = data.get('nodeKey')
            result_code = data.get('result')
            note, reference = data.get('note',''),data.get('reference','')
            if node not in task_keys(state) or not isinstance(result_code,str) or result_code not in {'observed','issue','pending'}:
                raise ValidationError('请选择有效的节点和观察结果。人工记录不能标记自动验证通过。')
            if not isinstance(note,str) or not 8 <= len(note.strip()) <= 2000 or not isinstance(reference,str) or len(reference)>500:
                raise ValidationError('实操说明需要8–2000字，证据引用不超过500字；不要填写Token或个人资料。')
            entry = {'kind':'manual','nodeKey':node,'result':result_code,'note':note.strip(),
                     'reference':reference.strip(),'revision':loop.desired_revision,
                     'at':timezone.now().isoformat(),'actor':actor.get_username(),
                     'datasetId':state['configs'].get('dataset',{}).get('externalId','')}
        state['history'].insert(0,entry)
    else:
        raise ValidationError('不支持此操作。当前工作区不提供自动发布或模拟验证。')
    # Copying does not alter the source configuration. Keep its edit token valid
    # while retaining the request receipt under the same transaction lock.
    if action != 'duplicate':
        state['version'] += 1
    state['receipts'][rid] = {'digest':digest,'actor':actor.pk,
        **({'createdId':str(result.loop_key)} if action=='duplicate' else {})}
    state['receipts'] = dict(list(state['receipts'].items())[-64:])
    loop.config_json = {'workspace_v1':state}
    loop.current_task_key = next((k for k in task_keys(state) if k not in state['configs']),'dataset')
    if loop.status != 'archived':
        loop.status = 'configuring' if state['configs'] else 'draft'
    loop.updated_by = actor
    loop.save(update_fields=['name','status','current_task_key','desired_revision','config_json','updated_by','updated_at'])
    if action in {'save_config','save_draft','save_pixel_step','filter'}:
        _graph(loop,state)
    record_audit(actor=actor,action='flow_workspace_'+action,entity_table='marketing_loop',entity_id=loop.pk,
        before={'revision':old_revision,'version':data['expectedVersion']},
        after={'revision':loop.desired_revision,'version':state['version'],'node':data.get('nodeKey',''),
               'external_action':False},request=request,
        metadata={'flow_request':rid,'flow_receipt':state['receipts'][rid]})
    return result
