"""Existing-session/CSRF protected runtime commands; never executes external sends."""
import logging
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import DatabaseError, transaction
from django.http import JsonResponse, Http404
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from .flow_workspace_views import _scope, _payload
from .flow_workspace_service import lookup
from .access import SYSTEM_ROLES, user_has_any_role
from marketing.flow_runtime import runtime_command, runtime_snapshot, RuntimeConflict
from marketing.models import MarketingRuntimeBinding
from marketing.runtime_evidence import collect_runtime_evidence, record_runtime_observation

logger = logging.getLogger(__name__)


@login_required
@require_http_methods(['GET','POST'])
@never_cache
def flow_runtime_item(request, loop_key):
    site, _ = _scope(request)
    try:
        loop = lookup(site, loop_key)
        result = runtime_command(loop=loop, actor=request.user, data=_payload(request)) if request.method == 'POST' else runtime_snapshot(loop)
        result['canOperate'] = user_has_any_role(request.user, SYSTEM_ROLES)
        from marketing.pixel_catalog import pixel_catalog
        result['pageCatalog'] = pixel_catalog(site)
        return JsonResponse({'runtime': result})
    except RuntimeConflict as error:
        return JsonResponse({'error':str(error),'code':'conflict'},status=409)
    except ValidationError as error:
        return JsonResponse({'error':error.messages[0],'code':'invalid'},status=400)
    except Http404:
        return JsonResponse({'error':'找不到当前站点流程。','code':'not_found'},status=404)
    except DatabaseError as error:
        logger.error('Runtime storage unavailable (%s)',type(error).__name__)
        return JsonResponse({'error':'运行配置暂不可用，输入已保留。','code':'unavailable'},status=503)


@login_required
@require_http_methods(['GET','POST'])
@never_cache
def flow_runtime_checks(request, loop_key):
    site, _ = _scope(request)
    try:
        loop = lookup(site, loop_key)
        binding = MarketingRuntimeBinding.objects.select_related('integration').filter(loop=loop).first()
        if not binding or not binding.active_revision_key:
            return JsonResponse({'evidence':None})
        if request.method == 'POST':
            if not user_has_any_role(request.user, SYSTEM_ROLES):
                raise PermissionDenied('只有系统管理员可登记运行证据。')
            # The returned evidence and the write are one transaction. A failed
            # response must not leave a hidden successful registration behind.
            with transaction.atomic():
                binding = MarketingRuntimeBinding.objects.select_for_update().get(pk=binding.pk)
                record_runtime_observation(binding=binding, actor=request.user, data=_payload(request))
                evidence = collect_runtime_evidence(binding)
            return JsonResponse({'evidence':evidence, 'canOperate':True})
        return JsonResponse({'evidence':collect_runtime_evidence(binding),
            'canOperate':user_has_any_role(request.user, SYSTEM_ROLES)})
    except ValidationError as error:
        return JsonResponse({'error':error.messages[0]},status=400)
    except Http404:
        return JsonResponse({'error':'找不到当前站点流程。'},status=404)
    except DatabaseError as error:
        logger.error('Runtime evidence %s unavailable (%s)', request.method, type(error).__name__)
        if request.method == 'POST':
            return JsonResponse({'error':'登记未保存，所选事件和结果已保留。请稍后重试，无需再次提交询盘。',
                                 'code':'observation_unavailable'},status=503)
        return JsonResponse({'error':'暂时无法刷新证据。已显示的内容可能不是最新结果，请重试刷新。',
                             'code':'evidence_unavailable'},status=503)
