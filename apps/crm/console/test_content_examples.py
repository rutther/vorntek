"""Optional examples exercise actual uploads/forms, not a simulated media catalogue."""
import base64
import hashlib
import json
import struct
import zlib
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from console.models import ExampleDataBundle
from console.test_file_lifecycle_contract import FileLifecycleFixture
from leads.models import CrmAttachment
from marketing.models import MarketingProvider, MarketingRuntimeBinding
from sitecore.models import Article, MediaAsset, MediaAssetBinding, Site, SiteLocale, ThreeDPlacement, ThreeDViewProfile


class ContentExamplesPlanTests(SimpleTestCase):
    def command(self, **options):
        self.assertIn('seed_content_examples', get_commands(), 'Content/file examples need their own optional command')
        output = StringIO()
        call_command('seed_content_examples', stdout=output, **options)
        return json.loads(output.getvalue())

    def test_default_is_database_free_plan_with_file_and_content_coverage(self):
        plan = self.command()
        self.assertEqual(plan['result'], 'PLAN ONLY')
        self.assertEqual(plan['pack'], 'vorntek-content-v1')
        self.assertEqual(plan['modules'], {'assets':2, 'attachments':2, 'articles':2, 'profiles':1, 'placements':1})
        self.assertIn('synthetic', plan['notice'].lower())

    def test_apply_requires_synthetic_acknowledgement_before_any_database(self):
        with self.assertRaisesRegex(CommandError, 'synthetic-only'):
            self.command(apply=True, owner='operator')

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True)
    def test_apply_refuses_live_external_delivery_before_any_database(self):
        with self.assertRaisesRegex(CommandError, 'disabled'):
            self.command(apply=True, synthetic_only=True, owner='operator')


class ContentExamplesFixture(FileLifecycleFixture):
    def setUp(self):
        super().setUp()
        self.site.code = 'siteos_demo'
        self.site.base_url = 'https://examples.example.invalid'
        self.site.save()
        self.actor = get_user_model().objects.create_superuser('content-examples', 'operator@example.invalid', 'synthetic-only')
        for code in ('en', 'zh'):
            SiteLocale.objects.create(site=self.site, locale_code=code, label=code, is_default=code == 'en')
        MarketingProvider.objects.get_or_create(code='meta', defaults={'name':'Meta', 'enabled':True})
        self.assets_root = self.root / 'assets'
        self.assets_root.mkdir()
        settings_scope = override_settings(SITEOS_ASSET_STORAGE_ROOTS=[str(self.assets_root)])
        settings_scope.enable()
        self.addCleanup(settings_scope.disable)
        call_command('seed_example_bundle', apply=True, synthetic_only=True,
                     site=self.site.code, owner=self.actor.username, stdout=StringIO())
        self.base = ExampleDataBundle.objects.get(pack_key='vorntek-general-v1')
        self.base_before = (self.base.source_digest, self.base.manifest_json)

    def command(self, **options):
        self.assertIn('seed_content_examples', get_commands(), 'Content/file examples need their own optional command')
        output = StringIO()
        call_command('seed_content_examples', apply=True, synthetic_only=True, site=self.site.code,
                     owner=self.actor.username, stdout=output, **options)
        return json.loads(output.getvalue())

    def files(self):
        return {path.relative_to(self.root).as_posix():path.read_bytes()
                for path in self.root.rglob('*') if path.is_file() and '.pending-' not in str(path)}


@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False, NEWCROWN_RUN_SCHEDULED_TASKS=False,
                   SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False)
class ContentExamplesLifecycleTests(ContentExamplesFixture, TestCase):
    def test_real_uploads_cover_bindings_and_disabled_model_are_openable(self):
        self.assertEqual(self.command()['result'], 'IMPORTED')
        self.assertEqual(MediaAsset.objects.count(), 2)
        self.assertEqual(CrmAttachment.objects.count(), 2)
        self.assertEqual(Article.objects.filter(config_json__synthetic_pack='vorntek-content-v1').count(), 2)
        image = MediaAsset.objects.get(asset_type='image')
        from console.forms import resolve_storage_path
        png = resolve_storage_path(image.storage_path).read_bytes()
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', png[16:24]), (480,240))
        compressed = b''
        position = 8
        while position < len(png):
            size, = struct.unpack('>I', png[position:position+4])
            kind, data = png[position+4:position+8], png[position+8:position+8+size]
            crc, = struct.unpack('>I', png[position+8+size:position+12+size])
            self.assertEqual(crc, zlib.crc32(kind+data))
            if kind == b'IDAT': compressed += data
            position += size+12
        self.assertEqual(len(zlib.decompress(compressed)), 240*(480*3+1))
        model = MediaAsset.objects.get(asset_type='model3d')
        glb = resolve_storage_path(model.storage_path).read_bytes()
        self.assertEqual(struct.unpack('<4sII', glb[:12]), (b'glTF',2,len(glb)))
        size, kind = struct.unpack('<II', glb[12:20])
        self.assertEqual(kind, 0x4e4f534a)
        scene = json.loads(glb[20:20+size])
        raw = base64.b64decode(scene['buffers'][0]['uri'].split(',')[1])
        self.assertEqual(struct.unpack('<9f',raw), (-1,-1,0,1,-1,0,0,1,0))
        articles = Article.objects.filter(config_json__synthetic_pack='vorntek-content-v1')
        for article in articles:
            self.assertEqual(article.status, 'draft')
            self.assertEqual(article.cover_image_url, image.public_path)
            self.assertTrue(MediaAssetBinding.objects.filter(asset=image, entity_id=article.pk,
                            entity_type='article', role='cover', enabled=True).exists())
        self.assertEqual(set(articles.values_list('route__locale__locale_code',flat=True)), {'en','zh'})
        self.assertFalse(ThreeDPlacement.objects.get().enabled)
        self.assertEqual(ThreeDViewProfile.objects.get().status, 'disabled')
        from console.crm_files import resolve_private_attachment_path
        for attachment in CrmAttachment.objects.all():
            content = resolve_private_attachment_path(attachment.storage_path).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), attachment.sha256)
            self.assertIn('synthetic',content.decode('utf-8').lower())
            self.assertTrue(attachment.submission_id or attachment.opportunity_id)
        self.assertEqual(MarketingRuntimeBinding.objects.count(), 0)
        self.base.refresh_from_db()
        self.assertEqual((self.base.source_digest,self.base.manifest_json),self.base_before)

    def test_repeat_preserves_later_edits_and_never_duplicates_rows_or_blobs(self):
        self.command()
        article = Article.objects.filter(config_json__synthetic_pack='vorntek-content-v1').first()
        article.body_markdown = 'Later independent operator work'
        article.save()
        original_files = self.files()
        original_ids = list(MediaAsset.objects.values_list('id',flat=True))
        self.assertEqual(self.command()['result'], 'ALREADY INSTALLED')
        self.assertEqual(self.files(), original_files)
        self.assertEqual(list(MediaAsset.objects.values_list('id',flat=True)), original_ids)
        article.refresh_from_db()
        self.assertEqual(article.body_markdown,'Later independent operator work')
        self.assertEqual(CrmAttachment.objects.count(),2)

    def test_pristine_reset_retains_ids_files_accounts_and_base_pack(self):
        self.command()
        before = self.files()
        ids = list(MediaAsset.objects.values_list('id',flat=True))
        actor_before = get_user_model().objects.filter(pk=self.actor.pk).values().get()
        result = self.command(reset=True)
        self.assertEqual(result['result'],'RESET')
        self.assertTrue(result['files_retained'])
        self.assertEqual(self.files(),before)
        self.assertEqual(list(MediaAsset.objects.values_list('id',flat=True)),ids)
        self.assertEqual(get_user_model().objects.filter(pk=self.actor.pk).values().get(),actor_before)
        self.base.refresh_from_db()
        self.assertEqual((self.base.source_digest,self.base.manifest_json),self.base_before)

    def test_reset_refuses_later_file_edits_without_changing_any_rows(self):
        self.command()
        attachment = CrmAttachment.objects.first()
        from console.crm_files import resolve_private_attachment_path
        path = resolve_private_attachment_path(attachment.storage_path)
        path.write_bytes(b'New operator-owned material, never overwrite')
        before = ExampleDataBundle.objects.get(pack_key='vorntek-content-v1').manifest_json
        with self.assertRaisesRegex(CommandError,'file.*modified|file.*missing'):
            self.command(reset=True)
        self.assertEqual(ExampleDataBundle.objects.get(pack_key='vorntek-content-v1').manifest_json,before)
        self.assertEqual(path.read_bytes(),b'New operator-owned material, never overwrite')

    def test_reset_refuses_modified_article(self):
        self.command()
        row = Article.objects.filter(config_json__synthetic_pack='vorntek-content-v1').first()
        row.title = 'Later work'
        row.save()
        with self.assertRaisesRegex(CommandError,'modified'):
            self.command(reset=True)

    def test_reset_refuses_non_example_reference_to_a_seed_asset(self):
        self.command()
        MediaAssetBinding.objects.create(site=self.site,asset=MediaAsset.objects.first(),
                                        entity_type='page_route',entity_id=999,role='later_work')
        with self.assertRaisesRegex(CommandError,'non-example'):
            self.command(reset=True)
        self.assertEqual(MediaAssetBinding.objects.filter(role='later_work').count(),1)

    def test_failure_after_upload_rolls_back_database_and_keeps_durable_recovery(self):
        self.assertIn('seed_content_examples',get_commands())
        with patch('console.content_examples.record_audit',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesRegex(RuntimeError,'synthetic failure'):
                self.command()
        self.assertFalse(ExampleDataBundle.objects.filter(pack_key='vorntek-content-v1').exists())
        self.assertEqual(MediaAsset.objects.count(),0)
        self.assertEqual(CrmAttachment.objects.count(),0)
        self.assertEqual(Article.objects.count(),4)
        self.assertEqual(len(list(self.assets_root.glob('.pending-assets/*.json'))),2)
        self.assertEqual(len(list(self.root.glob('.pending-uploads/*.json'))),2)

    def test_missing_base_pack_is_refused_without_any_upload(self):
        self.base.delete()
        with self.assertRaisesRegex(CommandError,'general'):
            self.command()
        self.assertEqual(self.files(),{})

    def test_non_administrator_cannot_import(self):
        self.actor.is_superuser = False
        self.actor.save()
        with self.assertRaisesRegex(CommandError,'administrator'):
            self.command()
        self.assertEqual(self.files(),{})

    def test_import_refuses_preexisting_private_blob_without_claiming_or_editing_it(self):
        self.assertIn('seed_content_examples',get_commands())
        from console import content_examples, crm_files
        from django.core.files.uploadedfile import SimpleUploadedFile
        data, _, files = content_examples.load_content_bundle()
        row = data['attachments'][0]
        old = crm_files.store_crm_attachment(site=self.site, uploaded_file=SimpleUploadedFile(
            row['filename'],files[row['key']],content_type='text/plain'),title='Independent operator file',
            uploaded_by=self.actor,target={'company':self.company})
        before = CrmAttachment.objects.filter(pk=old.pk).values().get()
        content = crm_files.resolve_private_attachment_path(old.storage_path).read_bytes()
        with self.assertRaisesRegex(CommandError,'existing.*attachment|existing.*file'):
            self.command()
        self.assertEqual(CrmAttachment.objects.filter(pk=old.pk).values().get(),before)
        self.assertEqual(crm_files.resolve_private_attachment_path(old.storage_path).read_bytes(),content)
        self.assertEqual(MediaAsset.objects.count(),0)
        self.assertFalse(ExampleDataBundle.objects.filter(pack_key='vorntek-content-v1').exists())

    def test_import_refuses_preexisting_public_asset_without_modifying_its_metadata(self):
        self.assertIn('seed_content_examples',get_commands())
        from console import content_examples
        from console.forms import store_uploaded_asset
        from django.core.files.uploadedfile import SimpleUploadedFile
        data, _, files = content_examples.load_content_bundle()
        row = data['assets'][0]
        old = store_uploaded_asset(site=self.site,uploaded_file=SimpleUploadedFile(row['filename'],files[row['key']]),
            title='Later operator asset',alt_text='',caption='',created_by=self.actor.username)
        before = MediaAsset.objects.filter(pk=old.pk).values().get()
        with self.assertRaisesRegex(CommandError,'existing.*asset'):
            self.command()
        self.assertEqual(MediaAsset.objects.filter(pk=old.pk).values().get(),before)
        self.assertEqual(MediaAsset.objects.count(),1)
        self.assertEqual(CrmAttachment.objects.count(),0)

    def preview(self):
        from console.article_preview import build_private_article_preview
        self.command()
        self.client.force_login(self.actor)
        article = Article.objects.filter(config_json__synthetic_pack='vorntek-content-v1',
                                         route__locale__locale_code='en').get()
        result = build_private_article_preview(site=self.site,article=article,actor=self.actor.username)
        self.image = MediaAsset.objects.get(site=self.site,asset_type='image')
        self.preview_prefix = '/admin/releases/articles/preview/'+result['version']+'/'
        return result

    @override_settings(SITEOS_ARTICLE_PUBLIC_ORIGIN='https://example.invalid')
    def test_uploaded_cover_is_served_through_the_real_reviewed_private_preview(self):
        with override_settings(SITEOS_ARTICLE_PREVIEW_ROOT=str(self.root/'previews'),
                               SITEOS_ARTICLE_PREVIEW_ASSET_ROOT=str(self.root/'static')):
            self.preview()
            html = self.client.get(self.preview_prefix+'articles/synthetic-workflow-guide/index.html')
            self.assertEqual(html.status_code,200)
            self.assertIn(self.preview_prefix+self.image.public_path.lstrip('/'),html.content.decode())
            response = self.client.get(self.preview_prefix+self.image.public_path.lstrip('/'))
            self.assertEqual(response.status_code,200,'Uploaded cover must not be a broken private-preview image')
            self.assertEqual(response['Content-Type'],'image/png')
            self.assertEqual(hashlib.sha256(response.content).hexdigest(),self.image.sha256)
            self.assertIn('no-store',response['Cache-Control'])

    @override_settings(SITEOS_ARTICLE_PUBLIC_ORIGIN='https://example.invalid')
    def test_private_preview_refuses_changed_upload_and_unreferenced_files(self):
        with override_settings(SITEOS_ARTICLE_PREVIEW_ROOT=str(self.root/'previews'),
                               SITEOS_ARTICLE_PREVIEW_ASSET_ROOT=str(self.root/'static')):
            self.preview()
            from console.forms import resolve_storage_path
            resolve_storage_path(self.image.storage_path).write_bytes(b'Changed after upload')
            response = self.client.get(self.preview_prefix+self.image.public_path.lstrip('/'))
            self.assertEqual(response.status_code,503)
            model = MediaAsset.objects.get(asset_type='model3d')
            self.assertEqual(self.client.get(self.preview_prefix+model.public_path.lstrip('/')).status_code,404)

    @override_settings(SITEOS_ARTICLE_PUBLIC_ORIGIN='https://example.invalid')
    def test_private_preview_never_serves_another_sites_uploaded_image(self):
        self.command()
        from console.content_examples import load_content_bundle
        from console.forms import store_uploaded_asset
        from django.core.files.uploadedfile import SimpleUploadedFile
        data, _, files = load_content_bundle()
        other = Site.objects.create(code='other_examples',name='Other synthetic site')
        row = data['assets'][0]
        asset = store_uploaded_asset(site=other,uploaded_file=SimpleUploadedFile(row['filename'],files[row['key']]),
            title='Other site private-preview scope',alt_text='',caption='',created_by=self.actor.username)
        article = Article.objects.get(config_json__synthetic_pack='vorntek-content-v1',route__locale__locale_code='en')
        article.body_markdown += '\n\n![Other site]('+asset.public_path+')'
        article.save()
        with override_settings(SITEOS_ARTICLE_PREVIEW_ROOT=str(self.root/'previews'),
                               SITEOS_ARTICLE_PREVIEW_ASSET_ROOT=str(self.root/'static')):
            self.preview()
            self.assertEqual(self.client.get(self.preview_prefix+asset.public_path.lstrip('/')).status_code,404)


@unittest.skipUnless(connection.vendor=='postgresql','Requires committed ordinary-role full-SQL PostgreSQL')
@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False, NEWCROWN_RUN_SCHEDULED_TASKS=False,
                   SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False)
class ContentExamplesNativeRecoveryTests(ContentExamplesFixture, TransactionTestCase):
    def _fixture_teardown(self):
        # This one committed-recovery test owns a separately provisioned SQL
        # database. Django's managed-only TRUNCATE cannot flush unmanaged FKs;
        # the native runner removes the complete owned database after the group.
        return None

    def test_rollback_orphans_are_reconciled_without_removing_committed_retry_files(self):
        self.assertIn('seed_content_examples',get_commands())
        from console import asset_upload_recovery, crm_files
        with patch('console.content_examples.record_audit',side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaisesRegex(RuntimeError,'synthetic rollback'):
                self.command()
        self.assertEqual(MediaAsset.objects.count(),0)
        self.assertEqual(CrmAttachment.objects.count(),0)
        self.assertEqual(self.command()['result'],'IMPORTED')
        from console.forms import resolve_storage_path
        committed = {str(resolve_storage_path(row.storage_path)):resolve_storage_path(row.storage_path).read_bytes()
                     for row in MediaAsset.objects.all()}
        committed.update({str(crm_files.resolve_private_attachment_path(row.storage_path)):
                          crm_files.resolve_private_attachment_path(row.storage_path).read_bytes()
                          for row in CrmAttachment.objects.all()})
        self.assertEqual(len(list(self.assets_root.glob('.pending-assets/*.json'))),2)
        self.assertEqual(len(list(self.root.glob('.pending-uploads/*.json'))),2)
        result = asset_upload_recovery.reconcile_pending_assets(root=self.assets_root,site_id=self.site.pk,execute=True)
        self.assertEqual((result['removed'],result['invalid'],result['uncertain'],result['failed']),(2,0,0,0))
        result = crm_files.reconcile_pending_uploads(site_id=self.site.pk)
        self.assertEqual((result['removed'],result['invalid'],result['uncertain'],result['failed']),(2,0,0,0))
        self.assertEqual(len(self.files()),4)
        for path, content in committed.items():
            self.assertEqual(Path(path).read_bytes(),content)
        self.assertEqual(self.command(reset=True)['result'],'RESET')
