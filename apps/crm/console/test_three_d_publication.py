"""Public candidates freeze only explicitly enabled site-owned 3D slots."""
import base64
import hashlib
import importlib
import io
import json
from pathlib import Path
import zipfile

from django.test import TestCase, override_settings
from sitecore.models import Article, MediaAsset, PageRoute, SiteLocale, ThreeDPlacement, ThreeDViewProfile
from .article_delivery import ArticleDeliveryError
from .test_file_lifecycle_contract import FileLifecycleFixture

try:
    publication=importlib.import_module('console.three_d_publication')
except ModuleNotFoundError:
    publication=None


class ThreeDPublicationTests(FileLifecycleFixture,TestCase):
    def setUp(self):
        super().setUp()
        self.assertIsNotNone(publication,'Whole-site candidates have no real 3D publication input')
        pack=json.loads((Path(__file__).parent/'data/content_examples.json').read_text(encoding='utf-8'))
        self.raw=base64.b64decode(next(row for row in pack['assets'] if row['key']=='geometry_triangle')['base64'])
        relative='models/2026/10/0123456789abcdef-synthetic.glb'
        self.path=self.root/relative;self.path.parent.mkdir(parents=True);self.path.write_bytes(self.raw)
        self.asset=MediaAsset.objects.create(site=self.site,asset_type='model3d',title='Synthetic <geometry>',
            original_name='synthetic.glb',file_ext='.glb',file_size_bytes=len(self.raw),
            storage_path='storage/assets/'+relative,public_path='/assets/'+relative,
            sha256=hashlib.sha256(self.raw).hexdigest())
        self.profile=ThreeDViewProfile.objects.create(site=self.site,asset=self.asset,
            code='synthetic_public',name='Synthetic geometry',status='active')
        self.slot=ThreeDPlacement.objects.create(site=self.site,profile=self.profile,
            slot_code='homepage_equipment_preview',name='Synthetic slot',enabled=True)
        settings=override_settings(SITEOS_ASSET_STORAGE_ROOTS=[str(self.root)])
        settings.enable();self.addCleanup(settings.disable)

    def test_enabled_slot_freezes_real_model_viewer_and_camera_without_activating(self):
        result=publication.build_three_d_publication(self.site)
        self.assertEqual(result['files'][self.asset.public_path.lstrip('/')],self.raw)
        document=result['files'][f'assets/3d/profiles/{self.profile.pk}/index.html'].decode()
        self.assertIn('Synthetic &lt;geometry&gt;',document)
        self.assertIn('/assets/3d/public-viewer.js',document)
        self.assertIn(self.asset.public_path,document)
        self.assertIn('"position": [8.0, 5.0, -8.0]',document)
        self.assertIn('/assets/3d/profiles/',result['slots']['homepage_equipment_preview'])
        self.assertNotIn('/admin/',document)
        self.assertIn('assets/3d/vendor/model-viewer/4.4.0/model-viewer.min.js',result['files'])
        self.slot.refresh_from_db();self.assertTrue(self.slot.enabled)
        self.assertEqual(result,publication.build_three_d_publication(self.site))

    def test_disabled_slot_or_profile_does_not_publish_any_model(self):
        self.slot.enabled=False;self.slot.save(update_fields=['enabled'])
        self.assertEqual(publication.build_three_d_publication(self.site),{'files':{},'slots':{}})
        self.slot.enabled=True;self.slot.save(update_fields=['enabled'])
        self.profile.status='disabled';self.profile.save(update_fields=['status'])
        self.assertEqual(publication.build_three_d_publication(self.site),{'files':{},'slots':{}})

    def test_changed_model_blocks_candidate_instead_of_publishing_a_broken_slot(self):
        self.path.write_bytes(b'changed model')
        with self.assertRaisesRegex(ArticleDeliveryError,'three_d_asset_unavailable'):
            publication.build_three_d_publication(self.site)

    def test_unsafe_or_foreign_asset_cannot_be_frozen(self):
        self.asset.public_path='/assets/../secret.glb'
        with self.assertRaises(ArticleDeliveryError):
            publication.read_public_3d_asset(self.asset,self.site)
        self.asset.public_path='/assets/models/2026/10/0123456789abcdef-synthetic.glb'
        self.asset.site_id=self.site.pk+999
        with self.assertRaises(ArticleDeliveryError):
            publication.read_public_3d_asset(self.asset,self.site)

    def test_ply_slot_freezes_self_hosted_splat_runtime(self):
        raw=b'ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n'
        path=self.root/'models/2026/10/fedcba9876543210-synthetic.ply';path.write_bytes(raw)
        self.asset.file_ext='.ply';self.asset.original_name='synthetic.ply'
        self.asset.storage_path='storage/assets/models/2026/10/fedcba9876543210-synthetic.ply'
        self.asset.public_path='/assets/models/2026/10/fedcba9876543210-synthetic.ply'
        self.asset.file_size_bytes=len(raw);self.asset.sha256=hashlib.sha256(raw).hexdigest();self.asset.save()
        result=publication.build_three_d_publication(self.site)
        self.assertEqual(result['files'][self.asset.public_path.lstrip('/')],raw)
        self.assertIn('assets/3d/vendor/supersplat/1.37.0/index.js',result['files'])
        self.assertNotIn('assets/3d/vendor/model-viewer/4.4.0/model-viewer.min.js',result['files'])

    def test_missing_home_slot_marker_refuses_silent_omission(self):
        with self.assertRaisesRegex(ArticleDeliveryError,'three_d_slot_anchor_missing'):
            publication.inject_home_slots('<main>No slot markers</main>',{'homepage_equipment_preview':'<iframe></iframe>'})
        result=publication.inject_home_slots('<main><!--siteos:slot:homepage_equipment_preview--></main>',
            {'homepage_equipment_preview':'<iframe title="Synthetic"></iframe>'})
        self.assertEqual(result,'<main><iframe title="Synthetic"></iframe></main>')

    def test_sog_external_texture_reference_is_refused_before_publication(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as bundle:
            bundle.writestr('meta.json',json.dumps({'version':2,'means':{'files':['https://example.invalid/texture.webp']}}))
            bundle.writestr('local.webp',b'synthetic image')
        raw=stream.getvalue();path=self.root/'models/2026/10/fedcba9876543210-synthetic.sog';path.write_bytes(raw)
        self.asset.file_ext='.sog';self.asset.public_path='/assets/models/2026/10/fedcba9876543210-synthetic.sog'
        self.asset.storage_path='storage/assets/models/2026/10/fedcba9876543210-synthetic.sog'
        self.asset.file_size_bytes=len(raw);self.asset.sha256=hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ArticleDeliveryError,'three_d_asset_unavailable'):
            publication.read_public_3d_asset(self.asset,self.site)

    def test_whole_site_candidate_contains_enabled_model_and_saved_camera(self):
        from .article_preview import build_private_article_preview
        from .website_candidate import build_website_candidate, website_release_store
        locale=SiteLocale.objects.create(site=self.site,locale_code='en',label='English',is_default=True)
        route=PageRoute.objects.create(site=self.site,locale=locale,path='/en/articles/guide/',
                                      route_type='article',status='published',page_title='Synthetic guide')
        Article.objects.create(route=route,title='Synthetic guide',slug='guide',body_markdown='Synthetic only',
                               status='published',config_json={'contentKey':'guide'})
        self.site.config_json={'articleDelivery':{'baseRouteMode':'shared','brandLogoUrl':'/assets/vorntek/vorntekLogo.png'}}
        self.site.save(update_fields=['config_json'])
        image_root=Path(__file__).resolve().parents[1]/'website-source'
        source=image_root if image_root.is_dir() else Path(__file__).resolve().parents[2]/'website'
        with override_settings(SITEOS_ARTICLE_PREVIEW_ROOT=self.root/'previews',
                SITEOS_ARTICLE_RELEASE_ROOT=self.root/'article-releases',
                SITEOS_WEBSITE_RELEASE_ROOT=self.root/'website-releases',
                SITEOS_WEBSITE_SOURCE_ROOT=source,SITEOS_ARTICLE_PUBLIC_ORIGIN='https://example.invalid'):
            preview=build_private_article_preview(site=self.site)
            result=build_website_candidate(site=self.site,preview_record_id=preview['recordId'])
            store=website_release_store(self.site,initialize=False)
            manifest=store.verify(result['version'])
            self.assertIn(self.asset.public_path.lstrip('/'),manifest['files'])
            self.assertEqual(store.current(),'')
            home=(store.root/'releases'/result['version']/'index.html').read_text(encoding='utf-8')
            self.assertIn(f'/assets/3d/profiles/{self.profile.pk}/index.html',home)
            self.profile.camera_position_x=2;self.profile.save(update_fields=['camera_position_x'])
            second=build_website_candidate(site=self.site,preview_record_id=preview['recordId'])
            self.assertNotEqual(result['version'],second['version'])
            self.assertIn(self.asset.public_path.lstrip('/'),store.verify(result['version'])['files'])
