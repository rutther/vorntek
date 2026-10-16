"""Owned asset-write journals; no file scan, worker, or schema extension."""
import json
import logging
import os
from pathlib import Path
import re

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from sitecore.models import MediaAsset
from .crm_upload_recovery import (
    MARKER_RE, _direct_directory, _lock, _write_marker, clear_upload_marker,
    confirm_created, private_file, resolve_cleanup_file, validate_upload_recovery_options,
)
from .storage_security import StorageSecurityError, resolve_storage_asset_path

logger=logging.getLogger(__name__)
JOURNAL_DIRECTORY='.pending-assets'
PATH_RE=re.compile(r'(images|videos|models|documents)/[0-9]{4}/(?:0[1-9]|1[0-2])/([a-f0-9]{16})-upload-([a-f0-9]{32})(\.[a-z0-9]+)\Z')


def begin_upload(*,root:Path,target:Path,site_id:int,token:str):
    validate_upload_recovery_options(site_id=site_id)
    relative=target.relative_to(root).as_posix()
    match=PATH_RE.fullmatch(relative)
    if not match or match[2]!=token[:16] or match[3]!=token:
        raise ValidationError('资产写入恢复路径无效。')
    _lock(site_id,token)
    journal=root/JOURNAL_DIRECTORY
    journal.mkdir(exist_ok=True,mode=0o700)
    _direct_directory(journal)
    marker=journal/f'{site_id}-{token}.json'
    data={'version':1,'site_id':site_id,'token':token,'storage_path':relative,'created':False}
    _write_marker(marker,data,exclusive=True)
    return marker,data


def clear_committed_marker(marker):
    try: clear_upload_marker(marker)
    except OSError: logger.warning('Committed asset upload recovery marker remains pending.')


@transaction.atomic
def reconcile_marker(*,root:Path,marker:Path,site_id:int,token:str,execute:bool):
    if not _lock(site_id,token,try_only=True): return 'busy'
    try:
        safe=resolve_storage_asset_path(marker,roots=[root])
        if safe.stat().st_size>4096: return 'invalid'
        data=json.loads(safe.read_text(encoding='utf-8'))
        relative=data.get('storage_path','') if isinstance(data,dict) else ''
        match=PATH_RE.fullmatch(relative) if isinstance(relative,str) else None
        if (not match or not isinstance(data,dict) or type(data.get('version')) is not int or data['version']!=1 or
                type(data.get('site_id')) is not int or data['site_id']!=site_id or data.get('token')!=token or
                match[2]!=token[:16] or match[3]!=token or not isinstance(data.get('created'),bool)):
            return 'invalid'
        from .forms import ASSET_DIR_BY_TYPE,classify_asset
        asset_type,_=classify_asset(Path(relative).name)
        if ASSET_DIR_BY_TYPE[asset_type]!=match[1]: return 'invalid'
        logical='storage/assets/'+relative
        if MediaAsset.objects.filter(storage_path__in=[logical,relative,str(root/relative)]).exists():
            if execute: clear_upload_marker(safe)
            return 'referenced'
        blob=resolve_cleanup_file(root=root,storage_path=relative)
        if blob is not None and not data['created']: return 'uncertain'
        if not execute: return 'pending'
        if blob is not None: blob.unlink(missing_ok=True)
        clear_upload_marker(safe)
        return 'removed'
    except FileNotFoundError: return 'skipped'
    except (UnicodeError,json.JSONDecodeError,ValidationError,StorageSecurityError): return 'invalid'
    except OSError: return 'failed'


def reconcile_pending_assets(*,root:Path,site_id:int,limit=100,after='',execute=False):
    validate_upload_recovery_options(site_id=site_id,limit=limit,after=after)
    result={key:0 for key in ('processed','removed','referenced','busy','invalid','uncertain','failed','skipped','pending')}
    result['next_cursor']=''
    journal=root/JOURNAL_DIRECTORY
    if not journal.exists(): return result
    _direct_directory(journal)
    import heapq
    with os.scandir(journal) as entries:
        names=heapq.nsmallest(limit+1,(entry.name for entry in entries if entry.name>after and
            (match:=MARKER_RE.fullmatch(entry.name)) and int(match[1])==site_id))
    for name in names[:limit]:
        outcome=reconcile_marker(root=root,marker=journal/name,site_id=site_id,token=MARKER_RE.fullmatch(name)[2],execute=execute)
        result['processed']+=1;result[outcome]+=1
    if len(names)>limit: result['next_cursor']=names[limit-1]
    return result
