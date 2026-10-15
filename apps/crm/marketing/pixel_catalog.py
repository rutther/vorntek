"""Read the site's deployed HTML inventory. No request-triggered external fetches."""
import json
import time
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from .runtime_contract import normalize_path, digest


def pixel_catalog(site):
    missing = {'available':False,'pages':[],'version':'',
               'message':'网站页面目录暂不可用，请稍后刷新；已选页面不会被清除。'}
    path = Path(getattr(settings,'SITEOS_PIXEL_CATALOG_PATH',
                        settings.BASE_DIR / '.runtime' / 'pixel-pages.json'))
    try:
        if path.stat().st_size > 5_000_000:
            return missing
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,dict):
            return missing
        if data.get('siteCode') != site.code or not 0 <= time.time()-data['generatedAt'] < 180:
            return missing
        pages = data['pages']
        if not isinstance(pages,list) or len(pages)>20000:
            return missing
        seen=set()
        for page in pages:
            if not isinstance(page,dict) or any(not isinstance(page.get(k),str) for k in ('path','title','locale')):
                return missing
            path=normalize_path(page['path'])
            if path.startswith(('/admin/','/api/')) or path in seen:
                return missing
            seen.add(path)
            if path != page['path'] or not isinstance(page['formCodes'],list) or any(not isinstance(c,str) for c in page['formCodes']):
                return missing
        return {'available':True,'pages':pages,'version':digest(pages)}
    except (OSError,ValueError,TypeError,KeyError,ValidationError):
        return missing


def validate_scope(site, scope):
    from leads.models import LeadFormDefinition
    catalog = pixel_catalog(site)
    if not catalog['available']:
        raise ValidationError(catalog.get('message','网站页面目录暂不可用，请稍后重试。'))
    pages={p['path']:p for p in catalog['pages']}
    form=LeadFormDefinition.objects.filter(site=site,pk=scope['formId'],status='active',channel='website').first()
    if not form:
        raise ValidationError('所选网站表单已不可用，请重新选择。')
    for path in scope['landingPaths']+scope['leadPaths']:
        if path not in pages:
            raise ValidationError(f'页面 {path} 不在当前发布的网站目录中，请核对后修改。')
    for path in scope['leadPaths']:
        if form.code not in pages[path]['formCodes']:
            raise ValidationError(f'页面 {path} 未发现所选询盘表单，请选择实际使用此表单的页面。')
    return catalog['version']
