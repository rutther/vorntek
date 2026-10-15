from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from marketing.models import MarketingLoop, MarketingProvider

from .access import MARKETING_ROLES, primary_role_label, user_has_any_role
from .marketing_loop_service import (
    available_dataset_sources, create_website_form_loop, dataset_name,
    record_dataset_id, rename_website_form_loop,
)
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale


class CreateLoopForm(forms.Form):
    name = forms.CharField(label='这条闭环叫什么', min_length=2, max_length=200)
    environment = forms.ChoiceField(
        label='用于哪里',
        choices=(('', '请选择用途'), ('production', '正式业务'), ('test', '演练测试')),
    )
    provider = forms.ChoiceField(
        choices=(('meta', 'Meta'),), required=False, initial='meta',
    )
    route = forms.ChoiceField(
        choices=(('website_form', '网站线索闭环'),), required=False,
        initial='website_form',
    )

    def clean_provider(self):
        return self.cleaned_data.get('provider') or 'meta'

    def clean_route(self):
        return self.cleaned_data.get('route') or 'website_form'


class DatasetIdForm(forms.Form):
    source_mode = forms.ChoiceField(choices=(('reuse', '使用已有'), ('register', '登记新建')))
    dataset_id = forms.RegexField(
        label='Meta 数据集 ID',
        regex=r'^[1-9][0-9]{5,24}$',
        max_length=25,
        error_messages={'invalid': '请填写 Meta 页面显示的数据集数字 ID。'},
    )


class RenameLoopForm(forms.Form):
    name = forms.CharField(label='闭环名称', min_length=2, max_length=200)


def _require_marketing_role(user) -> None:
    if not user_has_any_role(user, MARKETING_ROLES):
        raise PermissionDenied('当前账号没有查看引导式闭环的权限。')


def _context(request, *, site, locale) -> dict:
    return {
        'nav_items': build_navigation('marketing_guided_loop', request.user),
        'console_role_label': primary_role_label(request.user),
        'preview_site_url': f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
        'create_actions': [],
        'site': site,
    }


def _loop_or_404(*, site, loop_key) -> MarketingLoop:
    return get_object_or_404(
        MarketingLoop.objects.select_related('provider', 'site').exclude(template_version='flow-scheme-1'),
        site=site, provider__code='meta', route_code='website_form', loop_key=loop_key,
    )


LOOP_STATUS_LABELS = {
    'draft': '草稿',
    'configuring': '配置中',
    'ready': '待上线',
    'running': '运行中',
    'paused': '已暂停',
    'degraded': '有异常',
    'archived': '已归档',
}

WORKFLOW_GUIDANCE = {
    'dataset_setup': ('确认接收网站与 CRM 事件的 Meta 数据源。已有数据源可以复用；新建的数据源只需登记数字 ID。', '同站点、同环境；记录 ID 后仍须验证。'),
    'website_pixel': ('核对网站 Pixel 使用的数据集 ID，并观察真实 PageView 事件。', '需要数据源 ID 与网站事件证据。'),
    'form_crm': ('选择这条路线的官网表单，验证成功提交只生成一条 CRM 线索。', '需要表单、CRM 线索与来源证据。'),
    'capi_setup': ('核对 CRM 的 Lead 回传配置、投递队列与失败重试。', '需要服务器回传证据。'),
    'event_test': ('核对浏览器和服务器的同一 Lead 事件 ID，并在 Meta 验证去重。', '需要同一次提交的双端事件证据。'),
    'campaign_setup': ('在 Meta 建立潜在客户广告，并让广告组选择网站、数据源和 Lead 事件。', '需要广告系列、广告组与转化事件绑定。'),
    'launch_review': ('检查落地页、预算、隐私同意、事件与回退方案，再决定是否上线。', '所有关键节点先通过验收。'),
    'operate': ('上线后观察线索、事件投递、质量与异常处理。', '持续监测，不是一次性完成。'),
}


def _loop_item(loop: MarketingLoop) -> dict:
    tasks = list(loop.tasks.all())
    nodes = list(loop.nodes.all())
    bindings = list(loop.asset_bindings.all())
    current_task = next(
        (task for task in tasks if task.task_key == loop.current_task_key),
        tasks[0] if tasks else None,
    )
    dataset_binding = next(
        (binding for binding in bindings if binding.role == 'optimization_dataset'),
        None,
    )
    failed_nodes = [node for node in nodes if node.status == 'failed']
    completed_tasks = sum(task.status == 'done' for task in tasks)
    if loop.status == 'degraded' or failed_nodes:
        health_code, health_label = 'attention', '需处理'
    elif loop.status == 'running':
        health_code, health_label = 'healthy', '正常'
    elif any(node.status in {'configured', 'healthy'} for node in nodes):
        health_code, health_label = 'checking', '待验证'
    else:
        health_code, health_label = 'unknown', '未验证'
    return {
        'loop': loop,
        'tasks': tasks,
        'nodes': nodes,
        'current_task': current_task,
        'dataset_binding': dataset_binding,
        'completed_tasks': completed_tasks,
        'total_tasks': len(tasks),
        'progress_percent': round(completed_tasks * 100 / len(tasks)) if tasks else 0,
        'lifecycle_label': LOOP_STATUS_LABELS.get(loop.status, loop.status),
        'health_code': health_code,
        'health_label': health_label,
        'environment_label': '正式业务' if loop.environment == 'production' else '演练测试',
    }


@login_required
@require_GET
def marketing_guided_loop(request):
    _require_marketing_role(request.user)
    site, locale = default_site_locale(None)
    try:
        loops = list(
            MarketingLoop.objects.filter(
                site=site, provider__code='meta', route_code='website_form',
            )
            .exclude(status='archived')
            .select_related('provider', 'updated_by')
            .prefetch_related('nodes', 'tasks', 'asset_bindings')
            .order_by('-updated_at', '-id')
        )
        loop_items = [_loop_item(loop) for loop in loops]
        requested_key = request.GET.get('loop', '').strip()
        selected_item = next(
            (item for item in loop_items if str(item['loop'].loop_key) == requested_key),
            loop_items[0] if loop_items else None,
        )
    except DatabaseError:
        loop_items = []
        selected_item = None
        load_error = True
    else:
        load_error = False
    return render(request, 'console/v2/pages/marketing_guided_loop.html', {
        **_context(request, site=site, locale=locale),
        'loop_items': loop_items,
        'selected_item': selected_item,
        'load_error': load_error,
    }, status=503 if load_error else 200)


@login_required
@require_http_methods(['GET', 'POST'])
def marketing_guided_loop_create(request):
    _require_marketing_role(request.user)
    site, locale = default_site_locale(None)
    form = CreateLoopForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            loop = create_website_form_loop(
                site=site, actor=request.user,
                name=form.cleaned_data['name'],
                environment=form.cleaned_data['environment'], request=request,
            )
        except (ValidationError, MarketingProvider.DoesNotExist) as error:
            form.add_error(None, error.messages[0] if isinstance(error, ValidationError)
                           else 'Meta 平台尚未配置，请联系管理员。')
        else:
            messages.success(request, '闭环草稿已创建。现在选择可复用的数据源，或登记刚在 Meta 创建的数据集。')
            return redirect('console:marketing_guided_loop_detail', loop_key=loop.loop_key)
    return render(request, 'console/v2/pages/marketing_guided_loop_create.html', {
        **_context(request, site=site, locale=locale), 'form': form,
    })


@login_required
@require_GET
def marketing_guided_loop_detail(request, loop_key):
    _require_marketing_role(request.user)
    site, locale = default_site_locale(None)
    loop = _loop_or_404(site=site, loop_key=loop_key)
    nodes = list(loop.nodes.all())
    edges = list(loop.edges.select_related('source_node', 'target_node'))
    tasks = list(loop.tasks.all())
    binding = loop.asset_bindings.get(role='optimization_dataset')
    requested_task = request.GET.get('node', '').strip()
    selected_task = next(
        (task for task in tasks if task.task_key == requested_task),
        next((task for task in tasks if task.task_key == loop.current_task_key), tasks[0]),
    )
    workflow_steps = [
        {'task': task, 'summary': WORKFLOW_GUIDANCE[task.task_key][0],
         'evidence': WORKFLOW_GUIDANCE[task.task_key][1]}
        for task in tasks
    ]
    source_candidates = available_dataset_sources(loop)
    source_view = request.GET.get('source', '')
    if source_view not in {'reuse', 'register'}:
        source_view = 'reuse' if source_candidates else 'register'
    return render(request, 'console/v2/pages/marketing_guided_loop_detail.html', {
        **_context(request, site=site, locale=locale),
        'loop': loop, 'nodes': nodes, 'edges': edges, 'tasks': tasks,
        'workflow_steps': workflow_steps, 'source_view': source_view,
        'selected_task': selected_task,
        'dataset_binding': binding, 'dataset_name': dataset_name(loop),
        'source_candidates': source_candidates,
    })


@login_required
@require_POST
def marketing_guided_loop_rename(request, loop_key):
    _require_marketing_role(request.user)
    site, _locale = default_site_locale(None)
    loop = _loop_or_404(site=site, loop_key=loop_key)
    form = RenameLoopForm(request.POST)
    if not form.is_valid():
        messages.error(request, '闭环名称需要 2–200 个字符。')
    else:
        try:
            rename_website_form_loop(
                loop=loop, actor=request.user, name=form.cleaned_data['name'], request=request,
            )
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            messages.success(request, '闭环名称已更新。')
    return redirect(f"{reverse('console:marketing_guided_loop')}?loop={loop.loop_key}")


@login_required
@require_POST
def marketing_guided_loop_dataset(request, loop_key):
    _require_marketing_role(request.user)
    site, _locale = default_site_locale(None)
    loop = _loop_or_404(site=site, loop_key=loop_key)
    form = DatasetIdForm(request.POST)
    if not form.is_valid():
        messages.error(request, '数据集 ID 必须是 Meta 页面显示的数字编号。')
    else:
        try:
            record_dataset_id(
                loop=loop, actor=request.user,
                dataset_id=form.cleaned_data['dataset_id'],
                source_mode=form.cleaned_data['source_mode'], request=request,
            )
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            messages.success(request, '数据源 ID 已记录；Pixel、回传和 Lead 事件仍待验证。')
    return redirect(f"{reverse('console:marketing_guided_loop_detail', args=[loop.loop_key])}?node=dataset_setup")
