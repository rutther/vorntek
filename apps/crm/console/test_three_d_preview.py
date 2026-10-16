"""Authenticated draft 3D preview must work without publishing the model."""
import base64
import hashlib
import json
from pathlib import Path
import struct
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from console.access import GROUP_BY_ROLE, ROLE_CONTENT_OPS, ROLE_MARKETING_OPS
from console.content_access import ASSETS_READ
from console.models import ContentAccessGrant
from sitecore.models import MediaAsset, Site, ThreeDViewProfile
from .test_file_lifecycle_contract import FileLifecycleFixture


@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ThreeDPreviewTests(FileLifecycleFixture, TestCase):
    def setUp(self):
        super().setUp()
        pack = json.loads((Path(__file__).parent/'data/content_examples.json').read_text(encoding='utf-8'))
        self.raw = base64.b64decode(next(x for x in pack['assets'] if x['key']=='geometry_triangle')['base64'])
        relative = 'models/2026/10/0123456789abcdef-synthetic.glb'
        self.path = self.root/relative
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(self.raw)
        self.asset = MediaAsset.objects.create(site=self.site, asset_type='model3d', title='Synthetic triangle',
            original_name='synthetic.glb', file_ext='.glb', file_size_bytes=len(self.raw),
            storage_path='storage/assets/'+relative, public_path='/assets/'+relative,
            sha256=hashlib.sha256(self.raw).hexdigest())
        self.profile = ThreeDViewProfile.objects.create(site=self.site, asset=self.asset,
            code='synthetic_preview', name='Synthetic preview', status='disabled')
        self.url = f'/admin/assets/3d/profiles/{self.profile.pk}/preview/'
        actor = get_user_model().objects.create_superuser('model-preview', 'synthetic@example.invalid', 'synthetic-only')
        self.client.force_login(actor)
        scope = patch('console.views._resolved_content_site_locale', return_value=(self.site, None))
        scope.start(); self.addCleanup(scope.stop)
        config = override_settings(SITEOS_ASSET_STORAGE_ROOTS=[str(self.root)])
        config.enable(); self.addCleanup(config.disable)

    def test_draft_preview_uses_authenticated_asset_not_unpublished_public_path(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'/admin/assets/{self.asset.pk}/file/')
        self.assertNotContains(response, '/assets/models/2026/10/0123456789abcdef-synthetic.glb')
        self.assertContains(response, 'model-viewer')
        self.assertIn("connect-src 'self'", response['Content-Security-Policy'])
        self.assertNotIn("'unsafe-eval'", response['Content-Security-Policy'])
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn('no-store', response['Cache-Control'])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, 'disabled')
        file_response = self.client.get(f'/admin/assets/{self.asset.pk}/file/')
        self.assertEqual(b''.join(file_response.streaming_content), self.raw)

    def test_rendered_viewer_dependencies_resolve_from_packaged_static_sources(self):
        from django.contrib.staticfiles.finders import find
        page = self.client.get(self.url)
        self.assertContains(page, 'type="module" src="/static/console/three-d-preview.js"')
        for name in ('three-d-preview.js', 'three-d-camera.js', 'three-d-preview.css'):
            self.assertTrue(Path(find('console/'+name)).is_file())
        module = Path(find('console/vendor/model-viewer/4.4.0/model-viewer.min.js'))
        self.assertEqual(hashlib.sha256(module.read_bytes()).hexdigest(),
                         '18b2e711035aa4705ee281530a55dc2338ff05eed3d70ae16d54105282a75ed5')

    def test_editor_and_slot_embed_private_profile_preview(self):
        from sitecore.models import ThreeDPlacement
        placement = ThreeDPlacement.objects.create(site=self.site, profile=self.profile,
            slot_code='homepage_equipment_preview', name='Synthetic slot', enabled=False)
        page = self.client.get(f'/admin/assets/3d/profiles/{self.profile.pk}/')
        self.assertContains(page, 'src="'+self.url+'"')
        page = self.client.get(f'/admin/assets/3d/placements/{placement.pk}/')
        self.assertContains(page, 'src="'+self.url+'"')

    def test_read_only_asset_operator_can_preview_but_not_edit(self):
        actor = get_user_model().objects.create_user('model-reader', password='synthetic-only')
        group, _ = Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_CONTENT_OPS])
        actor.groups.add(group)
        ContentAccessGrant.objects.create(user=actor, site=self.site, capability=ASSETS_READ, enabled=True)
        self.client.force_login(actor)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(f'/admin/assets/3d/profiles/{self.profile.pk}/').status_code, 403)

    def test_anonymous_marketing_and_foreign_site_are_denied(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        actor = get_user_model().objects.create_user('not-a-model-reader', password='synthetic-only')
        group, _ = Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_MARKETING_OPS])
        actor.groups.add(group); self.client.force_login(actor)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        actor.is_superuser=True; actor.save(update_fields=['is_superuser'])
        self.client.force_login(actor)
        other = Site.objects.create(code='foreign_model', name='Foreign synthetic site')
        foreign_asset = MediaAsset.objects.create(site=other, asset_type='model3d', original_name='foreign.glb',
            file_ext='.glb', file_size_bytes=len(self.raw), storage_path=self.asset.storage_path,
            public_path='/assets/models/2026/10/fedcba9876543210-foreign.glb', sha256=self.asset.sha256)
        foreign_profile = ThreeDViewProfile.objects.create(site=other, asset=foreign_asset,
            code='foreign_preview', name='Foreign synthetic preview', status='disabled')
        self.assertEqual(self.client.get(f'/admin/assets/3d/profiles/{foreign_profile.pk}/preview/').status_code, 404)

    def replace_document(self, document):
        chunk = json.dumps(document).encode()
        chunk += b' ' * (-len(chunk) % 4)
        raw = struct.pack('<4sIII4s', b'glTF', 2, 20+len(chunk), len(chunk), b'JSON') + chunk
        self.path.write_bytes(raw)
        self.asset.file_size_bytes=len(raw); self.asset.sha256=hashlib.sha256(raw).hexdigest()
        self.asset.save(update_fields=['file_size_bytes', 'sha256'])

    def test_external_dependencies_are_not_loaded_by_draft_preview(self):
        self.replace_document({'asset':{'version':'2.0'},'buffers':[{'uri':'https://example.invalid/private.bin'}]})
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '外部资源')
        self.assertNotContains(response, '<model-viewer')
        self.assertNotContains(response, 'https://example.invalid/private.bin')

    def test_missing_corrupt_and_decoder_dependent_files_show_failure_not_success(self):
        self.replace_document({'asset':{'version':'2.0'},'extensionsUsed':['KHR_draco_mesh_compression']})
        page = self.client.get(self.url)
        self.assertContains(page, '解码器')
        self.assertNotContains(page, '<model-viewer')
        self.path.write_bytes(b'not a model')
        page = self.client.get(self.url)
        self.assertContains(page, '文件不可用')
        self.assertNotContains(page, '<model-viewer')
        self.path.unlink()
        self.assertContains(self.client.get(self.url), '文件不可用')
