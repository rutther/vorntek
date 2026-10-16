"""Install an independent industrial content pack through normal product writers."""
import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import CommandError
from django.db import connection, transaction

from console.audit import record_audit
from console.content_examples import _save_form
from console.example_bundle import _fingerprint, _silent
from console.forms import ArticleEditorForm, asset_mime_type, store_uploaded_asset
from console.models import ExampleDataBundle
from console.three_d_forms import ThreeDViewProfileForm
from marketing.models import MarketingIntegration
from sitecore.models import Article, MediaAsset, MediaAssetBinding, PageRoute, Site, SiteLocale, ThreeDViewProfile

DATA_FILE = Path(__file__).parent/'data/industrial_examples.json'


def load_industrial_bundle():
    """No database access. Same tracked article source in checkout and image."""
    installed = DATA_FILE.with_name('industrial_articles.json')
    source = installed if installed.is_file() else Path(__file__).resolve().parents[3]/'docs/vorntekDemo/articles.json'
    try:
        raw = DATA_FILE.read_bytes()
        article_raw = source.read_bytes()
        data = json.loads(raw)
        article = json.loads(article_raw)
        if (data['format'] != 'vorntek-industrial-examples-v1' or data['pack'] != 'industrial-v1'
                or data['products'] != ['inspect','measure','assemble','convey','mark','connect']
                or [r['locale'] for r in article['versions']] != ['en','zh','ar']):
            raise ValueError('Unsupported industrial source')
        website = Path(settings.SITEOS_WEBSITE_SOURCE_ROOT).resolve(strict=True)
        files = {}
        for product in data['products']:
            files[f'{product}.png'] = f'assets/vorntek/{product}.png'
            files[f'{product}.glb'] = f'assets/vorntek/models/{product}.glb'
        for name in data['context_images']:
            files[name] = f'assets/vorntek/{name}'
        content = {}
        for name,relative in files.items():
            path = website/relative
            if (Path(name).name != name or path.is_symlink()
                    or not path.resolve(strict=True).is_relative_to(website)):
                raise ValueError('Invalid industrial resource')
            payload = path.read_bytes()
            if not 0 < len(payload) <= 5*1024*1024:
                raise ValueError('Invalid industrial resource size')
            content[name] = payload
        digest_input = {'metadata':hashlib.sha256(raw).hexdigest(),
                        'articles':hashlib.sha256(article_raw).hexdigest(),
                        'files':{k:hashlib.sha256(v).hexdigest() for k,v in content.items()}}
        digest = hashlib.sha256(json.dumps(digest_input,sort_keys=True).encode()).hexdigest()
        return data,article,content,digest
    except (OSError,KeyError,TypeError,ValueError) as error:
        raise CommandError('Industrial content inputs are missing or invalid; no data was written.') from error


@transaction.atomic
def apply_industrial_bundle(*, site_code: str, owner_name: str) -> dict:
    _silent()
    data, article_source, files, digest = load_industrial_bundle()
    site = Site.objects.select_for_update().filter(code=site_code,enabled=True).first()
    if site is None:
        raise CommandError('An existing enabled synthetic site is required.')
    from console.access import SYSTEM_ROLES, user_has_any_role
    actor = get_user_model().objects.filter(username=owner_name,is_active=True).first()
    if actor is None or not user_has_any_role(actor,SYSTEM_ROLES):
        raise CommandError('An existing active system administrator is required.')
    if MarketingIntegration.objects.filter(site=site,enabled=True).exists():
        raise CommandError('Site integrations must remain disabled for synthetic examples.')
    bundle = ExampleDataBundle.objects.select_for_update().filter(site=site,pack_key=data['pack']).first()
    if bundle:
        if bundle.source_digest != digest:
            raise CommandError('Installed industrial example version differs; no data was overwritten.')
        return {'result':'ALREADY INSTALLED','resources':len(bundle.manifest_json['resources'])}
    locale_codes = [row['locale'] for row in article_source['versions']]
    if (Article.objects.filter(route__site=site,config_json__contentKey=data['content_key']).exists()
            or PageRoute.objects.filter(site=site,path__in=[f'/{code}/articles/{data["slug"]}/' for code in locale_codes]).exists()
            or ThreeDViewProfile.objects.filter(site=site,code__in=['industrial_'+p for p in data['products']]).exists()):
        raise CommandError('Untracked industrial identities conflict; no data was overwritten.')
    created, assets = [], {}
    # Content lock matches the normal uploader. Refuse to adopt another user's
    # deduplicated asset rather than relabeling it as owned by this pack.
    for name,payload in files.items():
        sha = hashlib.sha256(payload).hexdigest()
        if connection.vendor == 'postgresql':
            key = int.from_bytes(hashlib.sha256(f'asset-content:{site.pk}:{sha}'.encode()).digest()[:8],'big',signed=True)
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)',[key])
        if MediaAsset.objects.filter(site=site,sha256=sha).exists():
            raise CommandError('Untracked existing industrial asset conflicts; no data was overwritten.')
        asset = store_uploaded_asset(site=site,uploaded_file=SimpleUploadedFile(name,payload,
            content_type=asset_mime_type(name)),title='Vorntek · '+name,
            alt_text='Vorntek industrial equipment concept',caption=data['notice'],created_by=actor.username)
        assets[name] = asset
        created.append(asset)
    locales = {}
    for code in locale_codes:
        locale,new = SiteLocale.objects.get_or_create(site=site,locale_code=code,defaults={
            'label':{'en':'English','zh':'中文','ar':'العربية'}[code],
            'direction':'rtl' if code == 'ar' else 'ltr','is_default':False,'enabled':True,'sort_order':30})
        if not locale.enabled:
            raise CommandError('Industrial article locale is disabled; enable it explicitly before import.')
        locales[code] = locale
        if new:
            created.append(locale)
    for row in article_source['versions']:
        article = _save_form(ArticleEditorForm({'title':row['title'],'slug':data['slug'],
            'content_key':data['content_key'],'status':'draft','body_markdown':row['body'],
            'excerpt':row['excerpt'],'cover_asset':assets['inspection-application.png'].pk,
            'author_name':article_source['author'],'reading_minutes':4},
            site=site,locale=locales[row['locale']],allow_publish=False,allow_assets=True))
        article.config_json = {**article.config_json,'synthetic_pack':data['pack']}
        article.save(update_fields=['config_json','updated_at'])
        created.extend([article,article.route,*MediaAssetBinding.objects.filter(site=site,entity_type='article',entity_id=article.pk)])
    for product in data['products']:
        created.append(_save_form(ThreeDViewProfileForm({'code':'industrial_'+product,
            'name':'Vorntek · '+product,'asset':assets[product+'.glb'].pk,'purpose':'general',
            'status':'disabled','background_color':'#f3f6fc',
            'camera_position_x':3,'camera_position_y':2,'camera_position_z':4,
            'camera_target_x':0,'camera_target_y':0.8,'camera_target_z':0,'fov':40,
            'allow_interaction':True,'auto_rotate':False,'notes':data['notice']},site=site)))
    resources = [{'model':type(obj)._meta.label_lower,'pk':str(obj.pk),'fingerprint':_fingerprint(obj)} for obj in created]
    owned_files = [{'model':type(obj)._meta.label_lower,'pk':str(obj.pk),'storage_path':obj.storage_path,
                    'sha256':obj.sha256,'size':obj.file_size_bytes} for obj in assets.values()]
    bundle = ExampleDataBundle.objects.create(site=site,pack_key=data['pack'],source_digest=digest,state='active',
        manifest_json={'version':1,'resources':resources,'files':owned_files,'relative_dates':[],
                       'notice':data['notice']})
    record_audit(actor=actor,action='example_bundle_imported',entity_table=bundle._meta.db_table,
                 entity_id=bundle.pk,after={'pack':data['pack'],'resources':len(resources),'files':len(owned_files)})
    return {'result':'IMPORTED','resources':len(resources),'files':len(owned_files)}
