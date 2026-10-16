from __future__ import annotations

import re
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from marketing.models import (
    MarketingIntegration, MarketingLoop, MarketingLoopAssetBinding,
    MarketingLoopEdge, MarketingLoopNode, MarketingLoopTask, MarketingProvider,
)

from .audit import record_audit


NODES = (
    ('meta_ad', 'meta_ad', 'Meta 视频广告', 'meta'),
    ('website', 'website', '网站页面', 'website'),
    ('web_form', 'web_form', '网站表单', 'website'),
    ('crm', 'crm', 'CRM 线索', 'crm'),
    ('capi', 'capi', '服务器回传', 'crm'),
    ('meta_dataset', 'meta_dataset', 'Meta 数据集', 'meta'),
    ('optimizer', 'optimizer', 'Lead 优化', 'meta'),
)
EDGES = (
    ('ad_to_website', 'meta_ad', 'website', 'ad_click'),
    ('website_to_form', 'website', 'web_form', 'form_view'),
    ('form_to_crm', 'web_form', 'crm', 'lead_submission'),
    ('crm_to_capi', 'crm', 'capi', 'lead_event'),
    ('capi_to_dataset', 'capi', 'meta_dataset', 'server_event'),
    ('dataset_to_optimizer', 'meta_dataset', 'optimizer', 'lead_signal'),
    ('optimizer_to_ad', 'optimizer', 'meta_ad', 'delivery_feedback'),
)
TASKS = (
    ('dataset_setup', '连接 Meta 数据集', 'meta_dataset', 'shared'),
    ('website_pixel', '确认网站 Pixel', 'website', 'website'),
    ('form_crm', '确认网站表单进入 CRM', 'web_form', 'crm'),
    ('capi_setup', '连接 CRM 服务器回传', 'capi', 'crm'),
    ('event_test', '验证 Lead 与去重', 'meta_dataset', 'shared'),
    ('campaign_setup', '建立潜在客户广告', 'meta_ad', 'meta'),
    ('launch_review', '上线前检查', 'optimizer', 'shared'),
    ('operate', '观察并处理异常', 'crm', 'shared'),
)
ENV_CODE = {'production': 'Prd', 'test': 'Tst'}


def scope_code_for_site(site) -> str:
    hostname = urlparse(site.base_url or '').hostname or site.code
    first_label = hostname.split('.')[0]
    words = re.findall(r'[A-Za-z0-9]+', first_label)
    scope = ''.join(word[:1].upper() + word[1:].lower() for word in words)
    if len(scope) < 2 or not scope[0].isalpha():
        raise ValidationError('站点网址不能生成资产名称，请先检查站点基础网址。')
    return scope[:80]


def dataset_name(loop: MarketingLoop) -> str:
    return f'{loop.binding_key}Ds'


def available_dataset_sources(loop: MarketingLoop) -> list[dict]:
    """Only offer IDs already recorded for this site and environment.

    A Pixel integration is a site-level source, not a dataset owned by an
    individual campaign. Production and test assets must never be mixed.
    """
    sources: dict[str, dict] = {}
    for integration in MarketingIntegration.objects.filter(
        site=loop.site, provider=loop.provider, integration_type='pixel', environment=loop.environment,
    ).order_by('id'):
        if re.fullmatch(r'[1-9][0-9]{5,24}', integration.public_id or ''):
            sources[integration.public_id] = {
                'id': integration.public_id,
                'name': integration.name,
                'origin': 'CRM 网站接入',
                'mode': 'existing',
            }
    for binding in MarketingLoopAssetBinding.objects.filter(
        provider=loop.provider, asset_type='dataset', loop__site=loop.site,
        loop__environment=loop.environment,
    ).exclude(loop=loop).exclude(external_id='').exclude(status='retired').select_related('loop').order_by('id'):
        if not re.fullmatch(r'[1-9][0-9]{5,24}', binding.external_id):
            continue
        sources.setdefault(binding.external_id, {
            'id': binding.external_id,
            'name': binding.observed_name or binding.expected_name or binding.external_id,
            'origin': f'已在「{binding.loop.name}」登记',
            'mode': 'shared',
        })
    return list(sources.values())


@transaction.atomic
def create_website_form_loop(*, site, actor, name: str, environment: str, request=None) -> MarketingLoop:
    name = ' '.join(str(name or '').split())
    if not 2 <= len(name) <= 200:
        raise ValidationError('闭环名称需要 2–200 个字符。')
    if environment not in ENV_CODE:
        raise ValidationError('请选择正式或演练环境。')
    # Lock the parent site so concurrent creators cannot reserve the same sequence.
    type(site).objects.select_for_update().get(pk=site.pk)
    provider = MarketingProvider.objects.get(code='meta', enabled=True)
    scope = scope_code_for_site(site)
    maximum = MarketingLoop.objects.filter(
        site=site, provider=provider, route_code='website_form',
        environment=environment, scope_code=scope,
    ).aggregate(value=Max('sequence_no'))['value'] or 0
    sequence = maximum + 1
    if sequence > 999:
        raise ValidationError('该站点的闭环编号已用尽，请联系系统管理员。')
    binding_key = f'ncMeta{ENV_CODE[environment]}WebLead{scope}{sequence:02d}'
    loop = MarketingLoop.objects.create(
        site=site, provider=provider, name=name, binding_key=binding_key,
        route_code='website_form', environment=environment,
        scope_code=scope, sequence_no=sequence,
        status='draft', current_task_key='dataset_setup',
        config_json={'optimization_event': 'Lead', 'conversion_location': 'website'},
        created_by=actor, updated_by=actor,
    )
    nodes = {}
    for position, (key, node_type, title, node_actor) in enumerate(NODES, 1):
        nodes[key] = MarketingLoopNode.objects.create(
            loop=loop, node_key=key, node_type=node_type, title=title,
            actor=node_actor, sequence_no=position,
            status='waiting_user' if key == 'meta_dataset' else 'pending',
            summary='等待填写 Dataset ID' if key == 'meta_dataset' else '尚未验收',
            desired_json={'event_name': 'Lead'} if key in {'meta_dataset', 'optimizer'} else {},
        )
    for key, source, target, signal in EDGES:
        MarketingLoopEdge.objects.create(
            loop=loop, edge_key=key, source_node=nodes[source],
            target_node=nodes[target], signal_code=signal,
        )
    for position, (key, title, node_key, owner) in enumerate(TASKS, 1):
        MarketingLoopTask.objects.create(
            loop=loop, node=nodes[node_key], task_key=key, title=title,
            owner_type=owner, sequence_no=position,
            status='ready' if position == 1 else 'blocked',
        )
    MarketingLoopAssetBinding.objects.create(
        loop=loop, node=nodes['meta_dataset'], provider=provider,
        role='optimization_dataset', asset_type='dataset',
        expected_name=dataset_name(loop), reuse_mode='undecided', status='planned',
    )
    record_audit(
        actor=actor, action='marketing_loop_created', entity_table='marketing_loop',
        entity_id=loop.pk, after={
            'site_id': site.pk, 'route_code': loop.route_code,
            'binding_key': binding_key, 'environment': environment,
            'status': loop.status,
        }, request=request,
    )
    return loop


@transaction.atomic
def rename_website_form_loop(*, loop: MarketingLoop, actor, name: str, request=None) -> MarketingLoop:
    name = ' '.join(str(name or '').split())
    if not 2 <= len(name) <= 200:
        raise ValidationError('闭环名称需要 2–200 个字符。')
    loop = MarketingLoop.objects.select_for_update().get(pk=loop.pk, site=loop.site)
    if loop.status == 'archived':
        raise ValidationError('已归档闭环不能修改。')
    if loop.name == name:
        return loop
    old_name = loop.name
    loop.name = name
    loop.updated_by = actor
    loop.save(update_fields=['name', 'updated_by', 'updated_at'])
    record_audit(
        actor=actor, action='marketing_loop_renamed', entity_table='marketing_loop',
        entity_id=loop.pk, before={'name': old_name}, after={'name': name}, request=request,
    )
    return loop


@transaction.atomic
def record_dataset_id(*, loop: MarketingLoop, actor, dataset_id: str,
                      source_mode: str, request=None) -> MarketingLoop:
    dataset_id = str(dataset_id or '').strip()
    if not re.fullmatch(r'[1-9][0-9]{5,24}', dataset_id):
        raise ValidationError('请填写 Meta 页面显示的数据集数字 ID。')
    if source_mode not in {'register', 'reuse'}:
        raise ValidationError('请选择使用已有数据源或登记新数据源。')
    loop = MarketingLoop.objects.select_for_update().get(pk=loop.pk, site=loop.site)
    if loop.status == 'archived':
        raise ValidationError('已归档闭环不能修改。')
    binding = MarketingLoopAssetBinding.objects.select_for_update().get(
        loop=loop, role='optimization_dataset',
    )
    if binding.external_id and binding.external_id != dataset_id:
        raise ValidationError('这个闭环已记录另一个 Dataset ID。更换数据源需要单独迁移，不能直接覆盖。')
    if binding.external_id == dataset_id:
        return loop
    candidates = {item['id']: item for item in available_dataset_sources(loop)}
    other_binding_exists = MarketingLoopAssetBinding.objects.filter(
        provider=loop.provider, asset_type='dataset', external_id=dataset_id,
    ).exclude(loop=loop).exists()
    integration_exists = MarketingIntegration.objects.filter(
        provider=loop.provider, integration_type='pixel', public_id=dataset_id,
    ).exists()
    if source_mode == 'reuse':
        candidate = candidates.get(dataset_id)
        if candidate is None:
            raise ValidationError('这个数据源不在当前站点和环境的可复用列表中，请刷新后核对。')
        binding.reuse_mode = candidate['mode']
        binding.expected_name = candidate['name']
    else:
        if dataset_id in candidates:
            raise ValidationError('这个数据集已登记。请使用「已有数据源」，不要重复登记为新数据源。')
        if other_binding_exists or integration_exists:
            raise ValidationError('这个数据集 ID 已在其他站点或环境登记，请核对资产归属，不能在当前闭环重复登记。')
        binding.reuse_mode = 'new'
    binding.external_id = dataset_id
    binding.status = 'recorded'
    try:
        with transaction.atomic():
            binding.save(update_fields=['external_id', 'expected_name', 'reuse_mode', 'status', 'updated_at'])
    except IntegrityError as error:
        if getattr(getattr(error.__cause__, 'diag', None), 'constraint_name', '') != 'uq_marketing_loop_new_asset_id':
            raise
        raise ValidationError('这个数据集已被其他闭环登记。请刷新后选择「已有数据源」，不要重复登记。') from error
    node = MarketingLoopNode.objects.get(loop=loop, node_key='meta_dataset')
    node.status = 'configured'
    node.summary = 'ID 已记录；尚未验证 Meta 权限和事件。'
    node.observed_json = {'dataset_id': dataset_id}
    node.save(update_fields=['status', 'summary', 'observed_json', 'updated_at'])
    task = MarketingLoopTask.objects.get(loop=loop, task_key='dataset_setup')
    task.status = 'waiting'
    task.result_json = {'dataset_id': dataset_id}
    task.save(update_fields=['status', 'result_json', 'updated_at'])
    loop.status = 'configuring'
    loop.desired_revision += 1
    loop.updated_by = actor
    loop.save(update_fields=['status', 'desired_revision', 'updated_by', 'updated_at'])
    record_audit(
        actor=actor, action='marketing_loop_dataset_recorded',
        entity_table='marketing_loop', entity_id=loop.pk,
        after={'dataset_id': dataset_id, 'source_mode': source_mode,
               'verification': 'unverified'}, request=request,
    )
    return loop
