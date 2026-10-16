"""Read template-declared public 3D positions without customer-specific enums."""
import json
from pathlib import Path, PurePosixPath
import re
from django.conf import settings
from .article_delivery import ArticleDeliveryError
from .article_release_store import _no_links

HOME_SLOTS={
    'homepage_hero':{'path':'index.html','label':'首页 Hero 背景'},
    'homepage_equipment_preview':{'path':'index.html','label':'首页 3D 展示区'},
}


def parse_slot_registry(raw):
    result=dict(HOME_SLOTS)
    if raw is None:return result
    try:
        if not isinstance(raw,bytes) or len(raw)>65536:raise ValueError()
        registry=json.loads(raw)
        if registry.get('schemaVersion')!=1 or not isinstance(registry.get('slots'),list) or len(registry['slots'])>120:raise ValueError()
        for row in registry['slots']:
            code=row['code'];path=row['path'];label=row['label']
            if not isinstance(code,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',code) or code in result:raise ValueError()
            if not isinstance(path,str) or len(path)>300 or not path.endswith('.html') or '\\' in path or '%' in path:raise ValueError()
            parsed=PurePosixPath(path)
            if parsed.is_absolute() or str(parsed)!=path or any(p.startswith('.') or ':' in p for p in parsed.parts):raise ValueError()
            if parsed.parts[0] in {'admin','api','assets','console','static','private','healthz'} or 'articles' in parsed.parts:raise ValueError()
            if not isinstance(label,str) or not 1<=len(label)<=180 or any(ord(c)<32 for c in label):raise ValueError()
            result[code]={'path':path,'label':label}
    except (ValueError,TypeError,KeyError,AttributeError):
        raise ArticleDeliveryError('three_d_slot_registry_invalid') from None
    return result


def slot_registry():
    root=Path(getattr(settings,'SITEOS_WEBSITE_SOURCE_ROOT','') or '.')
    if not root.is_absolute():return dict(HOME_SLOTS)
    file=root/'three-d-slots.json'
    _no_links(file)
    if not file.exists():return dict(HOME_SLOTS)
    if not file.is_file() or file.stat().st_size>65536 or file.stat().st_nlink!=1:
        raise ArticleDeliveryError('three_d_slot_registry_invalid')
    return parse_slot_registry(file.read_bytes())
