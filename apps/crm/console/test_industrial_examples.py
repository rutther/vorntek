"""Industrial content must be reproducible without a server database copy."""
import json
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from console.models import ExampleDataBundle
from console.test_file_lifecycle_contract import FileLifecycleFixture
from marketing.models import MarketingIntegration, MarketingProvider
from sitecore.models import Article, MediaAsset, SiteLocale, ThreeDPlacement, ThreeDViewProfile


class IndustrialPlanTests(SimpleTestCase):
    def command(self, **options):
        self.assertIn('seed_industrial_examples',get_commands())
        output = StringIO()
        call_command('seed_industrial_examples',stdout=output,**options)
        return json.loads(output.getvalue())

    def test_database_free_plan_reports_three_languages_and_six_models(self):
        result = self.command()
        self.assertEqual(result['result'],'PLAN ONLY')
        self.assertEqual(result['pack'],'industrial-v1')
        self.assertEqual(result['articles'],3)
        self.assertEqual(result['models'],6)
        self.assertRegex(result['source_digest'],r'^[a-f0-9]{64}$')

    def test_write_requires_synthetic_acknowledgement(self):
        with self.assertRaisesRegex(CommandError,'synthetic-only'):
            self.command(apply=True,owner='synthetic')

    @override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=True)
    def test_live_mode_refused_before_database_access(self):
        with self.assertRaisesRegex(CommandError,'disabled'):
            self.command(apply=True,synthetic_only=True,owner='synthetic')


@override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False,NEWCROWN_RUN_SCHEDULED_TASKS=False,
                   SITEOS_WHATSAPP_ALLOW_LIVE_SEND=False)
class IndustrialLifecycleTests(FileLifecycleFixture,TestCase):
    def setUp(self):
        super().setUp()
        self.actor=get_user_model().objects.create_superuser('industrial','operator@example.invalid','synthetic-only')
        for code in ('en','zh'):
            SiteLocale.objects.create(site=self.site,locale_code=code,label=code,is_default=code=='en')
        self.assets_root=self.root/'assets'; self.assets_root.mkdir()
        scope=override_settings(SITEOS_ASSET_STORAGE_ROOTS=[str(self.assets_root)])
        scope.enable(); self.addCleanup(scope.disable)

    def command(self, **options):
        self.assertIn('seed_industrial_examples',get_commands())
        output=StringIO()
        call_command('seed_industrial_examples',apply=True,synthetic_only=True,
                     site=self.site.code,owner=self.actor.username,stdout=output,**options)
        return json.loads(output.getvalue())

    def test_import_creates_actual_assets_drafts_and_disabled_profiles(self):
        before=get_user_model().objects.filter(pk=self.actor.pk).values().get()
        site_before=type(self.site).objects.filter(pk=self.site.pk).values().get()
        self.assertEqual(self.command()['result'],'IMPORTED')
        self.assertEqual(MediaAsset.objects.filter(asset_type='model3d').count(),6)
        self.assertEqual(MediaAsset.objects.filter(asset_type='image').count(),9)
        self.assertEqual(ThreeDViewProfile.objects.filter(status='disabled').count(),6)
        self.assertEqual(ThreeDPlacement.objects.count(),0)
        articles=Article.objects.filter(config_json__synthetic_pack='industrial-v1')
        self.assertEqual(set(articles.values_list('route__locale__locale_code',flat=True)),{'en','zh','ar'})
        self.assertEqual(set(articles.values_list('status',flat=True)),{'draft'})
        self.assertTrue(all('/assets/' in a.cover_image_url for a in articles))
        from console.forms import resolve_storage_path
        for asset in MediaAsset.objects.all():
            self.assertEqual(resolve_storage_path(asset.storage_path).stat().st_size,asset.file_size_bytes)
        self.assertEqual(SiteLocale.objects.get(site=self.site,locale_code='ar').direction,'rtl')
        self.assertEqual(get_user_model().objects.filter(pk=self.actor.pk).values().get(),before)
        self.assertEqual(type(self.site).objects.filter(pk=self.site.pk).values().get(),site_before)
        self.assertEqual(MarketingIntegration.objects.count(),0)

    def test_repeat_retains_later_edits_and_existing_data(self):
        self.command()
        row=Article.objects.filter(config_json__synthetic_pack='industrial-v1').first()
        row.body_markdown='Later operator work'; row.save()
        ids=list(MediaAsset.objects.values_list('pk',flat=True))
        self.assertEqual(self.command()['result'],'ALREADY INSTALLED')
        row.refresh_from_db(); self.assertEqual(row.body_markdown,'Later operator work')
        self.assertEqual(list(MediaAsset.objects.values_list('pk',flat=True)),ids)

    def test_unknown_article_identity_conflict_rolls_back_before_files(self):
        from console.forms import ArticleEditorForm
        form=ArticleEditorForm({'title':'Independent article','slug':'inspection-station-planning',
            'content_key':'independentArticle','status':'draft','body_markdown':'Original work','reading_minutes':2},
            site=self.site,locale=SiteLocale.objects.get(site=self.site,locale_code='en'),allow_publish=False)
        self.assertTrue(form.is_valid(),form.errors); old=form.save()
        with self.assertRaisesRegex(CommandError,'conflict'):
            self.command()
        self.assertEqual(MediaAsset.objects.count(),0)
        old.refresh_from_db(); self.assertEqual(old.body_markdown,'Original work')
        self.assertFalse(ExampleDataBundle.objects.filter(pack_key='industrial-v1').exists())

    def test_preexisting_asset_is_not_adopted_or_changed(self):
        from console.industrial_examples import load_industrial_bundle
        from console.forms import store_uploaded_asset
        from django.core.files.uploadedfile import SimpleUploadedFile
        _,_,files,_ = load_industrial_bundle()
        name,payload = next(iter(files.items()))
        asset=store_uploaded_asset(site=self.site,uploaded_file=SimpleUploadedFile(name,payload),
            title='Independent asset',alt_text='',caption='',created_by=self.actor.username)
        before=MediaAsset.objects.filter(pk=asset.pk).values().get()
        with self.assertRaisesRegex(CommandError,'asset conflicts'):
            self.command()
        self.assertEqual(MediaAsset.objects.filter(pk=asset.pk).values().get(),before)
        self.assertEqual(MediaAsset.objects.count(),1)
        self.assertFalse(ExampleDataBundle.objects.filter(pack_key='industrial-v1').exists())

    def test_import_refuses_changed_pack_digest(self):
        self.command()
        ExampleDataBundle.objects.filter(pack_key='industrial-v1').update(source_digest='0'*64)
        with self.assertRaisesRegex(CommandError,'version differs'):
            self.command()

    def test_active_integration_or_non_admin_refuses(self):
        provider,_=MarketingProvider.objects.get_or_create(code='meta',defaults={'name':'Meta'})
        integration=MarketingIntegration.objects.create(site=self.site,provider=provider,enabled=True)
        with self.assertRaisesRegex(CommandError,'integrations'):
            self.command()
        integration.delete()
        self.actor.is_superuser=False;self.actor.save()
        with self.assertRaisesRegex(CommandError,'administrator'):
            self.command()
        self.assertEqual(MediaAsset.objects.count(),0)

    def test_failure_rolls_back_rows_and_keeps_durable_file_journals(self):
        with patch('console.industrial_examples.record_audit',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesRegex(RuntimeError,'synthetic failure'):
                self.command()
        self.assertEqual(MediaAsset.objects.count(),0)
        self.assertEqual(Article.objects.count(),0)
        self.assertFalse(ExampleDataBundle.objects.exists())
        self.assertEqual(len(list(self.assets_root.glob('.pending-assets/*.json'))),15)
