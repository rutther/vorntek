"""Public/runtime protocol. Never accepts arbitrary event names or origins."""
import hashlib
import json
import re
from urllib.parse import urlsplit, unquote
from django.core.exceptions import ValidationError
from django.conf import settings

SCHEMA_VERSION = 2
FRESH_SECONDS = 60
CONTEXT_SECONDS = 900
LEGACY_EVENTS = ['PageView', 'ViewContent', 'Contact', 'FormStart', 'Lead']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(',', ':')).encode()).hexdigest()


def normalize_path(value):
    if not isinstance(value, str) or not value.startswith('/') or value.startswith('//'):
        raise ValidationError('页面范围必须是本站精确路径，例如 /contact/。')
    if len(value) > 500 or re.search(r'[\\?#*\x00-\x20]', value):
        raise ValidationError('页面路径不能含查询参数、通配符或空白。')
    decoded = unquote(value)
    if '%' in decoded or any(x in ('.', '..') for x in decoded.split('/')):
        raise ValidationError('页面路径包含不安全编码。')
    if re.search(r'%2f|%5c', value, re.I) or re.search(r'[\x00-\x20\x7f?#*]', decoded):
        raise ValidationError('页面路径不能包含编码分隔符。')
    if '//' in decoded or '\\' in decoded:
        raise ValidationError('页面路径不能包含重复分隔符。')
    return decoded.rstrip('/') + '/' if decoded != '/' else '/'


def site_origins(site):
    extra = (site.config_json or {}).get('measurement_origins', [])
    values = [site.base_url] + (extra if isinstance(extra, list) else [])
    origins = set()
    for value in values:
        parsed = urlsplit(str(value))
        if parsed.scheme in ('http', 'https') and parsed.netloc and not parsed.username:
            origins.add(f'{parsed.scheme}://{parsed.netloc.lower()}')
    configured = getattr(settings, 'SITEOS_META_RUNTIME_SITE_ORIGINS', {})
    aliases = configured.get(site.code, []) if isinstance(configured, dict) else []
    for value in aliases if isinstance(aliases, list) else []:
        if not isinstance(value, str) or re.search(r'[\\\x00-\x20\x7f*]', value):
            continue
        try:
            parsed = urlsplit(value)
            _ = parsed.port  # Reject malformed ports before matching Host.
            if (parsed.scheme in ('http', 'https') and parsed.hostname
                    and parsed.username is None and parsed.password is None
                    and parsed.path in ('', '/') and not parsed.query and not parsed.fragment):
                origins.add(f'{parsed.scheme}://{parsed.netloc.lower()}')
        except ValueError:
            continue
    return origins


def source_path(site, url):
    parsed = urlsplit(str(url))
    if f'{parsed.scheme}://{parsed.netloc.lower()}' not in site_origins(site):
        raise ValidationError('提交页面不属于当前受管站点。')
    return normalize_path(parsed.path or '/')


def normalize_draft(site, value, *, require_capi=True):
    from leads.models import LeadFormDefinition
    if not isinstance(value, dict) or set(value) - {'landingPaths', 'leadPaths', 'formId', 'consentRequired'}:
        raise ValidationError('接入草稿包含不支持的字段。')
    if value.get('consentRequired') is not True:
        raise ValidationError('必须获得营销同意后才发送。')
    result = {'consentRequired': True}
    for key in ('landingPaths', 'leadPaths'):
        paths = value.get(key)
        if not isinstance(paths, list) or not 1 <= len(paths) <= 30:
            raise ValidationError('请明确选择1至30个页面路径。')
        result[key] = sorted({normalize_path(path) for path in paths})
    try:
        if isinstance(value.get('formId'), bool):
            raise ValueError('Invalid form ID')
        form = LeadFormDefinition.objects.get(pk=int(value.get('formId')), site=site,
            status='active', channel='website')
    except (ValueError, TypeError, LeadFormDefinition.DoesNotExist):
        raise ValidationError('请选择本站处于启用状态的网站表单。')
    if form.submission_event_name != 'Lead':
        raise ValidationError('本接入要求表单的成功提交事件为 Lead。')
    if require_capi and not form.capi_enabled:
        raise ValidationError('请先为网站表单启用服务器回传，再应用接入。')
    result['formId'] = form.pk
    return result
