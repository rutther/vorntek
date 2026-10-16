"""Independent optional synthetic file/content pack; reuse real application writers.

The general-v1 pack/digest remains immutable. Neither import nor reset modifies it.
Reset verifies pristine owned rows/files, then retains their identities and bytes.
Outer transaction failures deliberately retain existing durable upload journals.
"""
import base64
import binascii
import hashlib
import json
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import CommandError
from django.db import connection, transaction

from console.audit import record_audit
from console.example_bundle import _fingerprint, _reset, _silent
from console.models import ExampleDataBundle
from console import crm_files
from console.forms import ArticleEditorForm, asset_mime_type, resolve_storage_path, store_uploaded_asset
from console.three_d_forms import ThreeDPlacementForm, ThreeDViewProfileForm
from leads.models import CrmAttachment, LeadSubmission, Opportunity
from marketing.models import MarketingIntegration
from sitecore.models import Article, MediaAsset, MediaAssetBinding, PageRoute, Site, SiteLocale, ThreeDPlacement, ThreeDViewProfile


DATA_FILE = Path(__file__).parent / 'data/content_examples.json'
OWNED_MODELS = (MediaAsset, MediaAssetBinding, CrmAttachment, Article, PageRoute, ThreeDViewProfile, ThreeDPlacement)
FILE_MODELS = {model._meta.label_lower:model for model in (MediaAsset, CrmAttachment)}


def load_content_bundle():
    payload = DATA_FILE.read_bytes()
    try:
        data = json.loads(payload)
        if (data['format'] != 'vorntek-content-examples-v1' or data['pack'] != 'vorntek-content-v1'
                or data['requires'] != 'vorntek-general-v1'):
            raise ValueError('unsupported pack')
        files = {}
        for section in ('assets','attachments','articles','profiles','placements'):
            rows = data[section]
            if not isinstance(rows,list) or not rows or len({row['key'] for row in rows}) != len(rows):
                raise ValueError('invalid section')
        for row in data['assets']+data['attachments']:
            if row['key'] in files or not re.fullmatch(r'[a-z][a-z0-9_-]{0,79}\.(png|glb|txt)',row['filename']):
                raise ValueError('invalid file identity')
            content = (base64.b64decode(row['base64'],validate=True) if 'base64' in row
                       else row['text'].encode('utf-8'))
            if not 0 < len(content) <= 5*1024*1024:
                raise ValueError('invalid file size')
            files[row['key']] = content
        if len(set(map(lambda content:hashlib.sha256(content).digest(),files.values()))) != len(files):
            raise ValueError('duplicate file content')
    except (KeyError, TypeError, ValueError, binascii.Error) as error:
        raise CommandError('Invalid optional synthetic content pack; no data was written.') from error
    return data, hashlib.sha256(payload).hexdigest(), files


def _validate_files(bundle):
    manifest = bundle.manifest_json
    owned = {(row['model'],str(row['pk'])) for row in manifest.get('resources',[])}
    entries = manifest.get('files')
    if not isinstance(entries,list) or not entries:
        raise CommandError('Example file ownership is missing; reset refused.')
    expected = {key for key in owned if key[0] in FILE_MODELS}
    seen = set()
    for entry in entries:
        key = (entry.get('model'),str(entry.get('pk')))
        model = FILE_MODELS.get(key[0])
        if model is None or key not in expected or key in seen:
            raise CommandError('Invalid example file ownership; reset refused.')
        seen.add(key)
        row = model.objects.select_for_update().filter(pk=key[1],site=bundle.site).first()
        if (row is None or row.storage_path != entry.get('storage_path') or row.sha256 != entry.get('sha256')
                or row.file_size_bytes != entry.get('size')):
            raise CommandError('Example file metadata is modified or missing; reset refused.')
        try:
            if model is MediaAsset:
                path = resolve_storage_path(row.storage_path)
            else:
                path = crm_files.crm_upload_recovery.resolve_cleanup_file(
                    root=crm_files.attachment_storage_root(),storage_path=row.storage_path)
            if path is None or path.stat().st_nlink != 1:
                raise ValueError('missing or shared file')
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
            if path.stat().st_size != entry['size'] or digest.hexdigest() != entry['sha256']:
                raise ValueError('changed file')
        except (OSError, ValueError, crm_files.StorageSecurityError) as error:
            raise CommandError('Example file is modified or missing; reset refused.') from error
    if seen != expected:
        raise CommandError('Incomplete example file ownership; reset refused.')


def _save_form(form):
    if not form.is_valid():
        raise CommandError('Synthetic content validation failed: '+str(form.errors.as_text()))
    return form.save()


@transaction.atomic
def apply_content_bundle(*, site_code, owner_name, reset=False):
    _silent()
    data, digest, files = load_content_bundle()
    site = Site.objects.select_for_update().filter(code=site_code,enabled=True).first()
    if site is None:
        raise CommandError('An existing enabled synthetic site is required.')
    actor = get_user_model().objects.filter(username=owner_name,is_active=True).first()
    from console.access import SYSTEM_ROLES, user_has_any_role
    if actor is None or not user_has_any_role(actor,SYSTEM_ROLES):
        raise CommandError('An existing active system administrator is required.')
    if MarketingIntegration.objects.filter(site=site,enabled=True).exists():
        raise CommandError('Site integrations must remain disabled for synthetic examples.')
    bundle = ExampleDataBundle.objects.select_for_update().filter(site=site,pack_key=data['pack']).first()
    if bundle and bundle.source_digest != digest:
        raise CommandError('Installed content example version differs; no data was overwritten.')
    if reset:
        if bundle is None:
            return {'result':'ALREADY RESET','resources':0,'files_retained':True}
        _validate_files(bundle)
        result = _reset(bundle,actor,owned_models=OWNED_MODELS)
        return {**result,'files_retained':True}
    if bundle:
        return {'result':'ALREADY INSTALLED','resources':len(bundle.manifest_json['resources'])}
    base = ExampleDataBundle.objects.select_for_update().filter(site=site,pack_key=data['requires'],state='active').first()
    if base is None:
        raise CommandError('Import the independent general example pack first.')
    # Target only rows tracked by the installed general pack, never a first
    # arbitrary customer row. Existing general rows are referenced, not edited.
    base_ids = {}
    for entry in base.manifest_json.get('resources',[]):
        base_ids.setdefault(entry['model'],[]).append(entry['pk'])
    targets = {
        'inquiry':LeadSubmission.objects.filter(site=site,pk__in=base_ids.get('leads.leadsubmission',[]),
                                               trashed_at__isnull=True).order_by('pk').first(),
        'opportunity':Opportunity.objects.filter(site=site,pk__in=base_ids.get('leads.opportunity',[])).order_by('pk').first(),
    }
    if any(targets.get(row['target']) is None for row in data['attachments']):
        raise CommandError('The general examples required by the attachments are missing.')
    locales = {row.locale_code:row for row in SiteLocale.objects.filter(site=site)}
    if any(row['locale'] not in locales for row in data['articles']):
        raise CommandError('Required example locales are missing.')
    if (PageRoute.objects.filter(site=site,path__in=[f"/{r['locale']}/articles/{r['slug']}/" for r in data['articles']]).exists()
            or Article.objects.filter(route__site=site,config_json__contentKey__in=[r['content_key'] for r in data['articles']]).exists()
            or ThreeDViewProfile.objects.filter(site=site,code__in=[r['key'] for r in data['profiles']]).exists()
            or ThreeDPlacement.objects.filter(site=site,slot_code__in=[r['slot'] for r in data['placements']]).exists()):
        raise CommandError('Untracked content identities conflict; no data was overwritten.')
    assets, created = {}, []
    for row in data['assets']:
        content = files[row['key']]
        sha = hashlib.sha256(content).hexdigest()
        # Same lock as the existing uploader: do not accidentally claim or
        # modify a concurrent, pre-existing deduplicated asset as seed-owned.
        if connection.vendor == 'postgresql':
            lock_id = int.from_bytes(hashlib.sha256(f'asset-content:{site.pk}:{sha}'.encode()).digest()[:8],'big',signed=True)
            with connection.cursor() as cursor: cursor.execute('SELECT pg_advisory_xact_lock(%s)',[lock_id])
        if MediaAsset.objects.filter(site=site,sha256=sha).exists():
            raise CommandError('Untracked existing example asset content conflicts; no data was overwritten.')
        asset = store_uploaded_asset(site=site,uploaded_file=SimpleUploadedFile(row['filename'],content,
                    content_type=asset_mime_type(row['filename'])),title=row['title'],alt_text=row.get('alt',''),
                    caption=data['notice'],created_by=actor.username)
        assets[row['key']] = asset
        created.append(asset)
    for row in data['attachments']:
        target = targets[row['target']]
        try:
            attachment = crm_files.store_crm_attachment(site=site,uploaded_file=SimpleUploadedFile(
                row['filename'],files[row['key']],content_type='text/plain; charset=utf-8'),
                title=row['title'],uploaded_by=actor,require_new_blob=True,
                target={'submission' if row['target']=='inquiry' else 'opportunity':target})
        except ValidationError as error:
            raise CommandError('; '.join(error.messages)) from error
        created.append(attachment)
    for row in data['articles']:
        article = _save_form(ArticleEditorForm({
            'title':row['title'],'slug':row['slug'],'content_key':row['content_key'],
            'status':'draft','body_markdown':row['body'],'excerpt':row['excerpt'],
            'cover_asset':assets[row['cover']].pk,'author_name':'Vorntek synthetic examples','reading_minutes':2,
        },site=site,locale=locales[row['locale']],allow_publish=False,allow_assets=True))
        article.config_json = {**article.config_json,'synthetic_pack':data['pack']}
        article.save(update_fields=['config_json','updated_at'])
        created.extend([article,article.route,*MediaAssetBinding.objects.filter(site=site,entity_type='article',entity_id=article.pk)])
    profiles = {}
    for row in data['profiles']:
        profile = _save_form(ThreeDViewProfileForm({
            'code':row['key'],'name':row['name'],'asset':assets[row['asset']].pk,'purpose':'general',
            'status':'disabled','background_color':'#f3f6fc',
            'camera_position_x':0,'camera_position_y':0,'camera_position_z':4,
            'camera_target_x':0,'camera_target_y':0,'camera_target_z':0,'fov':60,
            'allow_interaction':True,'auto_rotate':False,'notes':data['notice'],
        },site=site))
        profiles[row['key']] = profile
        created.append(profile)
    for row in data['placements']:
        created.append(_save_form(ThreeDPlacementForm({'slot_code':row['slot'],'name':row['name'],
            'profile':profiles[row['profile']].pk,'enabled':False,'notes':data['notice']},site=site)))
    resources = [{'model':type(obj)._meta.label_lower,'pk':str(obj.pk),'fingerprint':_fingerprint(obj)} for obj in created]
    owned_files = [{'model':type(obj)._meta.label_lower,'pk':str(obj.pk),'storage_path':obj.storage_path,
                    'sha256':obj.sha256,'size':obj.file_size_bytes} for obj in created if type(obj) in FILE_MODELS.values()]
    bundle = ExampleDataBundle.objects.create(site=site,pack_key=data['pack'],source_digest=digest,state='active',
        manifest_json={'version':1,'resources':resources,'files':owned_files,'relative_dates':[],
                       'reset_count':0,'notice':data['notice']})
    record_audit(actor=actor,action='example_bundle_imported',entity_table=bundle._meta.db_table,
                 entity_id=bundle.pk,after={'pack':data['pack'],'resources':len(resources),'files':len(owned_files)})
    return {'result':'IMPORTED','resources':len(resources),'files':len(owned_files)}
