"""Atomic sub-operation of the existing workspace command transaction."""
from django.core.exceptions import ValidationError
from marketing.models import MarketingRuntimeBinding
from marketing.runtime_contract import normalize_draft, normalize_path
from .flow_workspace_schema import clean_values


def save_pixel_step(*, loop, state, data):
    from .flow_workspace_service import FlowConflict
    from marketing.pixel_catalog import validate_scope
    binding = MarketingRuntimeBinding.objects.select_for_update().filter(loop=loop).first()
    version = binding.version if binding else 0
    if type(data.get('expectedRuntimeVersion')) is not int or data['expectedRuntimeVersion'] != version:
        raise FlowConflict('网站接入范围已更新。输入已保留，请读取最新版本并核对。',loop)
    intent=data.get('intent')
    if intent not in ('draft','save'):
        raise ValidationError('请选择暂存草稿或保存本步。')
    values=clean_values(loop.site,'pixel',data.get('values'),draft=intent=='draft')
    scope=data.get('scope')
    if not isinstance(scope,dict) or set(scope) != {'landingPaths','leadPaths','formId','consentRequired'}:
        raise ValidationError('页面接入范围字段不完整或不受支持。')
    if scope['consentRequired'] is not True:
        raise ValidationError('必须获得营销同意后才发送。')
    for key in ('landingPaths','leadPaths'):
        if not isinstance(scope[key],list) or len(scope[key])>30:
            raise ValidationError('每组最多选择30个页面。')
        scope[key]=list(dict.fromkeys(normalize_path(p) for p in scope[key]))
    form_id=scope['formId']
    if isinstance(form_id,bool) or not isinstance(form_id,(int,str)) or len(str(form_id))>20:
        raise ValidationError('表单编号无效。')
    if form_id != '' and (not str(form_id).isdigit() or int(form_id)<=0):
        raise ValidationError('表单编号无效。')
    if intent=='draft':
        state['drafts']['pixel']={**values,'scope':scope}
        state['lastConfigured']='pixel'
        return
    if not binding or not binding.integration_id:
        raise ValidationError('请先由管理员准备本数据集的网站接入；当前可暂存草稿。')
    integration=binding.integration
    if (integration.site_id!=loop.site_id or integration.environment!=loop.environment
            or integration.public_id!=state['configs'].get('dataset',{}).get('externalId')):
        raise ValidationError('本流程数据集与实际接入不一致，请先核对。')
    normalized=normalize_draft(loop.site,scope,require_capi=False)
    validate_scope(loop.site,normalized)
    form_values={'formId':str(normalized['formId'])}
    if (state['configs'].get('pixel')!=values or state['configs'].get('form')!=form_values
            or binding.draft_json!=normalized):
        loop.desired_revision+=1
    binding.draft_json=normalized
    binding.version+=1
    binding.save(update_fields=['draft_json','version','updated_at'])
    state['configs']['pixel']=values
    state['configs']['form']=form_values
    state['pixelScopeManaged']=True
    state['drafts'].pop('pixel',None)
    state['drafts'].pop('form',None)
    state['lastConfigured']='pixel'
