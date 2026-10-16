"""CRM source aggregation, intentionally not an ad attribution engine."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .access import MARKETING_ROLES, primary_role_label, user_has_any_role
from .marketing_queries import build_marketing_overview
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale


@login_required
@require_GET
@never_cache
def source_analysis(request):
    if not user_has_any_role(request.user, MARKETING_ROLES):
        raise PermissionDenied('当前账号没有来源分析权限。')
    site, locale = default_site_locale(None)
    context = {'site':site, 'resource_navigation':True,
        'nav_items':build_navigation('marketing_attribution', request.user),
        'console_role_label':primary_role_label(request.user),
        'preview_site_url':f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
        'matching_tab':request.GET.get('tab')=='matching'}
    try:
        overview = build_marketing_overview(site=site, include_operations=False)
        labels = {'meta_ads':'Meta 广告', 'paid_search':'付费搜索', 'organic':'自然搜索',
            'referral':'引荐', 'direct':'直接访问', 'website':'网站表单', 'whatsapp':'WhatsApp',
            'email':'邮件', 'other':'其他', 'unknown':'未知来源'}
        context['overview'] = overview
        context['sources'] = [{**row, 'label':labels.get(row['source'],row['source'])} for row in overview['sources']]
        matching = overview['matching_completeness']
        context['matching_rows'] = [{'label':label, **matching[key]} for key,label in (
            ('contactable','邮箱或电话'), ('browser_identifier','浏览器标识'),
            ('ad_click_identifier','广告点击标识'), ('external_lead_identifier','平台线索标识'))]
    except DatabaseError:
        context['load_error'] = True
    return render(request, 'console/v2/pages/marketing_sources.html', context,
                  status=503 if context.get('load_error') else 200)
