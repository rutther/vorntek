"""Freeze enabled 3D placements and scoped files into immutable website input."""
import hashlib
import html
import io
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

from django.utils.html import json_script
from sitecore.models import ThreeDPlacement
from .article_delivery import ArticleDeliveryError
from .storage_security import resolve_storage_asset_path, StorageSecurityError
from .three_d_preview import inspect_model, ModelPreviewError, MAX_MODEL_BYTES

SLOTS={'homepage_hero','homepage_equipment_preview'}
STATIC=Path(__file__).parent/'static/console'
MODEL_TYPES={'.glb','.ply','.sog'}


def read_public_3d_asset(asset,site):
    public=asset.public_path
    if (asset.site_id!=site.pk or asset.status!='active' or asset.asset_type!='model3d'
            or asset.file_ext not in MODEL_TYPES
            or not re.fullmatch(r'/assets/models/\d{4}/\d{2}/[a-f0-9]{16}-[a-z0-9._-]+',public)
            or PurePosixPath(public).suffix!=asset.file_ext):
        raise ArticleDeliveryError('three_d_asset_unavailable')
    try:
        if asset.file_ext=='.glb':inspect_model(asset)
        path=resolve_storage_asset_path(asset.storage_path)
        if path.stat().st_size>MAX_MODEL_BYTES:raise ValueError('size')
        with path.open('rb') as stream:raw=stream.read(MAX_MODEL_BYTES+1)
        if (len(raw)>MAX_MODEL_BYTES or len(raw)!=asset.file_size_bytes
                or hashlib.sha256(raw).hexdigest()!=asset.sha256):raise ValueError('identity')
        if asset.file_ext=='.ply':
            header=raw[:65536].split(b'end_header',1)
            if (len(header)!=2 or not raw.startswith(b'ply\n')
                    and not raw.startswith(b'ply\r\n') or b'element vertex ' not in header[0]
                    or not any(value in header[0] for value in
                               (b'format ascii 1.0',b'format binary_little_endian 1.0'))):
                raise ValueError('PLY header')
        if asset.file_ext=='.sog':
            with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
                members=bundle.infolist()
                if (not members or len(members)>128 or sum(row.file_size for row in members)>512*1024*1024
                        or len({row.filename.casefold() for row in members})!=len(members)
                        or 'meta.json' not in bundle.namelist()
                        or any(not re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:json|webp)',row.filename)
                               or row.flag_bits&1 for row in members)):
                    raise ValueError('SOG archive')
                metadata=bundle.getinfo('meta.json')
                if metadata.file_size>2*1024*1024:raise ValueError('SOG metadata')
                todo=[json.loads(bundle.read(metadata))]
                while todo:
                    value=todo.pop()
                    if isinstance(value,dict):
                        if 'files' in value:
                            names=value['files']
                            if (not isinstance(names,list) or any(not isinstance(name,str)
                                    or not re.fullmatch(r'[A-Za-z0-9_.-]+\.webp',name)
                                    or name not in bundle.namelist() for name in names)):
                                raise ValueError('SOG external texture')
                        todo.extend(value.values())
                    elif isinstance(value,list):todo.extend(value)
        return raw
    except (OSError,StorageSecurityError,ModelPreviewError,ValueError,zipfile.BadZipFile,RuntimeError,RecursionError):
        raise ArticleDeliveryError('three_d_asset_unavailable') from None


def _profile_config(profile):
    config=profile.config_json or {}
    position=[float(getattr(profile,'camera_position_'+axis)) for axis in 'xyz']
    target=[float(getattr(profile,'camera_target_'+axis)) for axis in 'xyz']
    return {'src':profile.asset.public_path,'kind':'glb' if profile.asset.file_ext=='.glb' else 'splat',
            'title':profile.asset.title or profile.asset.original_name,'position':position,'target':target,
            'fov':float(profile.fov),'background':profile.background_color,'showUi':profile.show_ui,
            'interaction':profile.allow_interaction,'autoRotate':bool(config.get('autoRotate',True)),
            'duration':float(config.get('autoRotateDuration') or 20)}


def _document(profile):
    title=html.escape(profile.asset.title or profile.asset.original_name)
    config=_profile_config(profile)
    style=''
    if config['kind']=='splat':style='<link rel="stylesheet" href="/assets/3d/vendor/supersplat/1.37.0/index.css">'
    csp="default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' blob: data:; img-src 'self' blob: data:; worker-src blob:; base-uri 'none'; object-src 'none'; form-action 'none'"
    body=(f'<model-viewer id="model" alt="{title}" interaction-prompt="none"></model-viewer>'
          if config['kind']=='glb' else '<div id="splat-viewer"></div>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}"><title>{title}</title>'
            f'{style}<link rel="stylesheet" href="/assets/3d/public-viewer.css"></head><body>{body}'
            '<p id="model-status" role="status" aria-live="polite">Loading 3D… / 正在加载3D…</p>'
            '<div id="model-controls" hidden><button type="button" id="reset-view">Reset view / 恢复视角</button></div>'
            +str(json_script(config,'model-config'))
            +'<script type="module" src="/assets/3d/public-viewer.js"></script></body></html>').encode()


def _vendor_files(name,version):
    root=STATIC/'vendor'/name/version
    manifest=json.loads((root/'provenance.json').read_text(encoding='utf-8'))
    result={}
    for name,digest in manifest['files'].items():
        path=root/name
        if '..' in PurePosixPath(name).parts or path.is_symlink():raise ArticleDeliveryError('three_d_runtime_unavailable')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest:raise ArticleDeliveryError('three_d_runtime_unavailable')
        public_name=path.relative_to(STATIC).as_posix()
        if path.suffix.lower() not in {'.js','.css','.json'}:public_name+='.txt'
        result['assets/3d/'+public_name]=data
    result['assets/3d/'+(root/'provenance.json').relative_to(STATIC).as_posix()]=(root/'provenance.json').read_bytes()
    return result


def build_three_d_publication(site):
    rows=ThreeDPlacement.objects.select_related('profile','profile__asset').filter(
        site=site,enabled=True,profile__status='active').order_by('sort_order','slot_code')
    files={};slots={}
    try:
        for slot in rows:
            profile=slot.profile
            if slot.slot_code not in SLOTS or profile.site_id!=site.pk:
                raise ArticleDeliveryError('three_d_slot_unavailable')
            raw=read_public_3d_asset(profile.asset,site)
            files[profile.asset.public_path.lstrip('/')]=raw
            path=f'assets/3d/profiles/{profile.pk}/index.html'
            files[path]=_document(profile)
            title=html.escape(slot.name,quote=True)
            slots[slot.slot_code]=(f'<iframe title="{title}" src="/{path}" loading="lazy" '
                                   'style="display:block;width:100%;height:420px;border:0" '
                                   'referrerpolicy="no-referrer"></iframe>')
            files.update(_vendor_files('model-viewer','4.4.0') if profile.asset.file_ext=='.glb'
                         else _vendor_files('supersplat','1.37.0'))
        if slots:
            for name in ('public-viewer.js','public-viewer.css','three-d-camera.js',
                         'three-d-load-state.js','three-d-preview-config.js'):
                files['assets/3d/'+name]=(STATIC/name).read_bytes()
    except (OSError,ValueError) as error:
        if isinstance(error,ArticleDeliveryError):raise
        raise ArticleDeliveryError('three_d_runtime_unavailable') from None
    return {'files':files,'slots':slots}


def inject_home_slots(document,slots):
    for slot,fragment in slots.items():
        if slot not in SLOTS or not isinstance(fragment,str):raise ArticleDeliveryError('three_d_slot_unavailable')
        marker='<!--siteos:slot:'+slot+'-->'
        if document.count(marker)!=1:raise ArticleDeliveryError('three_d_slot_anchor_missing')
        if slot=='homepage_hero':
            pattern=re.escape(marker)+r'<img class="hero-image"[^>]*>'
            document,count=re.subn(pattern,lambda match:fragment,document,count=1)
            if count!=1:raise ArticleDeliveryError('three_d_slot_anchor_missing')
            continue
        document=document.replace(marker,fragment)
    return document
