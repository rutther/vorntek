import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, RequestDataTooBig, ValidationError
from django.db import DatabaseError, transaction
from django.http import Http404, JsonResponse, RawPostDataException
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from marketing.models import MarketingLoop, MarketingProvider
from .access import MARKETING_ROLES, SYSTEM_ROLES, user_has_any_role
from .flow_workspace_schema import TEMPLATE, definitions
from .flow_workspace_service import FlowConflict, create_flow, lookup, mutate_flow, serialize
from .marketing_guided_loop_views import _context
from .payloads import default_site_locale
from .navigation import build_navigation
from marketing.plan_summary import plan_runtime_summaries

logger = logging.getLogger(__name__)


def _scope(request):
    if not user_has_any_role(request.user, MARKETING_ROLES):
        raise PermissionDenied('当前账号没有引导式配置权限。')
    return default_site_locale(None)


@login_required
@require_GET
@never_cache
def flow_workspace(request):
    site, locale = _scope(request)
    return render(request, 'console/v2/pages/flow_workspace.html', {
        **_context(request,site=site,locale=locale),
        'resource_navigation':True,
        'flow_bootstrap': {'mode':'crm','apiUrl':reverse('console:flow_workspace_collection'),
                           'csrfToken':get_token(request)},
    })


@login_required
@require_GET
@never_cache
def marketing_plans(request):
    site, locale = _scope(request)
    return render(request, 'console/v2/pages/flow_workspace.html', {
        **_context(request,site=site,locale=locale),
        'nav_items':build_navigation('marketing_plans', request.user),
        'plan_workspace':True, 'resource_navigation':True,
        'flow_bootstrap':{'mode':'crm', 'surface':'plans',
            'apiUrl':reverse('console:flow_workspace_collection'), 'csrfToken':get_token(request)},
    })


def _serialized(loop):
    return {**serialize(loop), 'runtime':plan_runtime_summaries([loop])[str(loop.loop_key)]}


def _payload(request):
    # CSRF may already consume a multipart stream. Reject its media type before
    # reading body rather than turning a valid-CSRF client mistake into a 500.
    if request.content_type != 'application/json':
        raise ValidationError('此接口只接受 application/json，请重新提交。')
    try:
        body = request.body
    except (RawPostDataException, RequestDataTooBig):
        raise ValidationError('请求内容无法读取或过大，请重新提交。') from None
    if len(body) > 16000:
        raise ValidationError('记录过大，请缩短内容；不要粘贴截图数据或秘密。')
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise ValidationError('请求内容不是有效 JSON。')
    if not isinstance(data,dict):
        raise ValidationError('请求内容必须为对象。')
    return data


def _error(error):
    if isinstance(error,FlowConflict):
        return JsonResponse({'error':str(error),'code':'conflict',
            **({'current':serialize(error.loop)} if error.loop else {})},status=409)
    if isinstance(error,ValidationError):
        return JsonResponse({'error':error.messages[0],'code':'invalid'},status=400)
    if isinstance(error,MarketingProvider.DoesNotExist):
        return JsonResponse({'error':'Meta 平台尚未配置，请联系管理员。','code':'unavailable'},status=503)
    # Do not echo SQL or production values in a public error response.
    logger.error('Flow workspace storage failed (%s)',type(error).__name__)
    return JsonResponse({'error':'CRM 保存/读取暂时不可用，当前输入未丢失，请稍后重试。','code':'storage_unavailable'},status=503)


@login_required
@require_http_methods(['GET','POST'])
@never_cache
def flow_workspace_collection(request):
    site, _ = _scope(request)
    try:
        if request.method == 'POST':
            data = _payload(request)
            if data.get('action') != 'create' or set(data) - {'action','requestId','name','environment','note'}:
                raise ValidationError('不支持此创建操作。')
            loop = create_flow(site=site,actor=request.user,data=data,request=request)
            return JsonResponse({'loop':_serialized(loop)})
        loops = MarketingLoop.objects.filter(site=site,provider__code='meta',route_code='website_form').select_related('provider')
        loops = list(loops)
        supported = [loop for loop in loops if loop.template_version == TEMPLATE]
        runtimes = plan_runtime_summaries(supported)
        current = [{**serialize(loop), 'runtime':runtimes[str(loop.loop_key)]} for loop in supported]
        legacy = [{'id':str(loop.loop_key),'name':loop.name,'status':loop.status,
                   'url':reverse('console:marketing_guided_loop_detail',args=[loop.loop_key])}
                  for loop in loops if loop.template_version!=TEMPLATE]
        defs = definitions(site)
        return JsonResponse({'loops':current,'definitions':defs,'legacyLoops':legacy,
            'canManageRuntime':user_has_any_role(request.user, SYSTEM_ROLES),
            'catalog':{'forms':[{'id':key,'name':name} for key,name in defs['form']['fields'][0]['options']]}})
    except (ValidationError,FlowConflict,DatabaseError,MarketingProvider.DoesNotExist) as error:
        return _error(error)


@login_required
@require_http_methods(['GET','POST'])
@never_cache
def flow_workspace_item(request, loop_key):
    site, _ = _scope(request)
    try:
        if request.method=='GET':
            loop = lookup(site,loop_key)
        else:
            data = _payload(request)
            allowed = {
                'rename':{'name'}, 'duplicate':set(), 'archive':set(), 'restore':set(),
                'close_guide':set(), 'reopen_guide':set(),
                'save_config':{'nodeKey','values'},'save_draft':{'nodeKey','values'},
                'save_pixel_step':{'intent','values','scope','expectedRuntimeVersion'},
                'positions':{'positions'},'filter':{'enabled'},'preflight':set(),
                'evidence':{'nodeKey','result','note','reference'},
            }
            action = data.get('action')
            if not isinstance(action,str) or action not in allowed or set(data)-({'action','requestId','expectedVersion'}|allowed[action]):
                raise ValidationError('请求包含不支持的操作或字段。')
            with transaction.atomic():
                loop = mutate_flow(site=site,actor=request.user,key=loop_key,data=data,request=request)
                if action=='save_pixel_step':
                    from marketing.models import MarketingRuntimeBinding
                    binding=MarketingRuntimeBinding.objects.filter(loop=loop).first()
                    return JsonResponse({'loop':{**_serialized(loop),'savedRuntimeVersion':binding.version if binding else 0}})
        return JsonResponse({'loop':_serialized(loop)})
    except Http404:
        return JsonResponse({'error':'找不到当前站点的流程。','code':'not_found'},status=404)
    except (ValidationError,FlowConflict,DatabaseError,MarketingProvider.DoesNotExist) as error:
        return _error(error)
