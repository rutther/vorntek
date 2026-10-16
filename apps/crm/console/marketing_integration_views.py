"""Independent platform assets; creation never applies a plan or sends an event."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from marketing.integration_registry import ensure_meta_pixel_integration
from marketing.models import MarketingIntegration, MarketingProvider, MarketingRuntimeBinding
from .access import MARKETING_ROLES, SYSTEM_ROLES, primary_role_label, user_has_any_role
from .audit import record_audit
from .meta_editor import connection_next_action, connection_check_time
from .navigation import build_navigation
from .payloads import PUBLIC_PREVIEW_BASE_URL, default_site_locale


class CreateIntegrationForm(forms.Form):
    name = forms.CharField(label='接入名称', max_length=200,
        widget=forms.TextInput(attrs={'placeholder': '例如：官网询盘 · Meta', 'autocomplete': 'off'}))
    public_id = forms.RegexField(label='Meta 数据集 ID', regex=r'^[0-9]{5,30}$', max_length=30,
        error_messages={'invalid': '请填写 Meta 提供的 5—30 位数据集数字 ID。'},
        widget=forms.TextInput(attrs={'inputmode': 'numeric', 'autocomplete': 'off',
                                    'aria-describedby': 'integration-id-help'}))
    environment = forms.ChoiceField(label='环境', choices=(('', '请选择环境'),
        ('production', '正式业务'), ('test', '演练测试')))

    def clean(self):
        cleaned = super().clean()
        if set(self.data) - {'csrfmiddlewaretoken', 'name', 'public_id', 'environment'}:
            raise ValidationError('请求包含不支持的字段。本页不接收凭据，也不会开启接入。')
        return cleaned


def _context(request):
    if not user_has_any_role(request.user, MARKETING_ROLES):
        raise PermissionDenied('当前账号没有查看平台接入的权限。')
    site, locale = default_site_locale(None)
    return {'site': site, 'nav_items': build_navigation('marketing_integrations', request.user),
            'resource_navigation': True,
            'console_role_label': primary_role_label(request.user),
            'preview_site_url': f'{PUBLIC_PREVIEW_BASE_URL}/?lang={locale.locale_code}',
            'can_create': user_has_any_role(request.user, SYSTEM_ROLES)}


@login_required
@require_GET
@never_cache
def marketing_integrations(request):
    context = _context(request)
    query = request.GET.get('q', '').strip()[:200]
    environment = request.GET.get('environment', '')
    if environment not in {'production', 'test'}:
        environment = ''
    try:
        items = MarketingIntegration.objects.filter(site=context['site']).select_related('provider')
        if query:
            items = items.filter(Q(name__icontains=query) | Q(public_id__icontains=query) |
                                 Q(provider__name__icontains=query))
        if environment:
            items = items.filter(environment=environment)
        page = Paginator(items.order_by('-updated_at', '-id'), 25).get_page(request.GET.get('page'))
        counts = dict(MarketingRuntimeBinding.objects.filter(integration__in=page.object_list,
            loop__site=context['site']).values('integration_id').annotate(count=Count('id')).values_list('integration_id', 'count'))
        rows = []
        for item in page.object_list:
            # The list reports stored credentials, not their plaintext or availability.
            credential_saved = bool(item.secret_ref)
            is_meta_pixel = item.provider.code == 'meta' and item.integration_type == 'pixel'
            diagnostic = (item.config_json or {}).get('last_diagnostic')
            state = connection_next_action(diagnostic, credential_saved) if is_meta_pixel else None
            if is_meta_pixel:
                label = {'missing': '待保存凭据', 'unchecked': '未检查', 'ready': '检查通过',
                         'review': '有提醒', 'blocked': '检查未通过'}[state['state']]
                tone = {'ready': 'good', 'review': 'warning', 'blocked': 'error',
                        'missing': 'warning', 'unchecked': 'neutral'}[state['state']]
            else:
                label, tone = '查看专用配置', 'neutral'
            rows.append({'item': item, 'credential_saved': credential_saved, 'check_label': label,
                'is_meta_pixel': is_meta_pixel, 'tone': tone, 'check_time': connection_check_time(diagnostic),
                'usage_count': counts.get(item.pk, 0) if is_meta_pixel and item.routing_mode == 'flow_scoped' else None,
                'type_label': {('meta', 'pixel'): '网站事件', ('meta', 'leadgen'): '即时表单',
                               ('whatsapp', 'cloud_api'): 'WhatsApp', ('google', 'data_manager'): 'Data Manager'}.get(
                                   (item.provider.code, item.integration_type), item.integration_type)})
        params = request.GET.copy()
        params.pop('page', None)
        return render(request, 'console/v2/pages/marketing_integrations.html', {**context,
            'rows': rows, 'page': page, 'query': query, 'environment': environment,
            'page_query': params.urlencode(), 'has_filters': bool(query or environment)})
    except DatabaseError:
        return render(request, 'console/v2/pages/marketing_integrations.html',
            {**context, 'query': query, 'environment': environment, 'load_error': True}, status=503)


@login_required
@require_http_methods(['GET', 'POST'])
@never_cache
def marketing_integration_create(request):
    context = _context(request)
    if not context['can_create']:
        raise PermissionDenied('仅系统管理员可以新建平台接入。')
    form = CreateIntegrationForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        provider = MarketingProvider.objects.filter(code='meta', enabled=True).first()
        if not provider:
            form.add_error(None, 'Meta 平台尚未启用，请由系统管理员核对平台设置。')
        else:
            try:
                with transaction.atomic():
                    item, created = ensure_meta_pixel_integration(site=context['site'], provider=provider,
                        **form.cleaned_data)
                    record_audit(actor=request.user,
                        action='marketing_integration_created' if created else 'marketing_integration_reused',
                        entity_table='marketing_integration', entity_id=item.pk, request=request,
                        after={'public_id': item.public_id, 'environment': item.environment,
                               'enabled': item.enabled, 'created': created})
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, '接入已创建。请保存凭据后检查连接；尚未开启发送。' if created
                    else '已打开同一资产的现有接入；名称、凭据和运行状态均未改变。')
                return redirect(reverse('console:marketing_integration_edit', args=[item.pk]))
    if form.errors:
        for field in form:
            if field.errors:
                described = field.field.widget.attrs.get('aria-describedby', '').split()
                field.field.widget.attrs['aria-describedby'] = ' '.join([*described, f'{field.auto_id}_error'])
    return render(request, 'console/v2/pages/marketing_integration_create.html', {**context, 'form': form})
