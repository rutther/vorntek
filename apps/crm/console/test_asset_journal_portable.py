"""Actual asset writes/DB rollback; advisory-lock cleanup is a native PG gate."""
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import get_commands
from django.db import transaction
from django.db import connection
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.test import override_settings
from unittest.mock import patch

from console import forms
from sitecore.models import MediaAsset, SiteLocale
from . import test_file_lifecycle_contract as fixtures


class AssetJournalPortableTests(fixtures.FileLifecycleFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.asset_root = self.root / 'assets'
        self.asset_root.mkdir()
        self.import_root = self.root / 'imports'
        self.import_root.mkdir()
        settings_guard = override_settings(SITEOS_ASSET_STORAGE_ROOTS=[str(self.asset_root)],
            SITEOS_ASSET_IMPORT_ROOTS=[str(self.import_root)])
        settings_guard.enable()
        self.addCleanup(settings_guard.disable)

    def store_asset(self, mode):
        args = dict(site=self.site, title='Synthetic journal example', alt_text='', caption='', created_by='synthetic')
        if mode == 'upload':
            return forms.store_uploaded_asset(uploaded_file=SimpleUploadedFile('sample.png', b'Synthetic image fixture'), **args)
        source = self.import_root / 'sample.png'
        source.write_bytes(b'Synthetic image fixture')
        return forms.store_local_asset(source_path=source, **args)

    def test_asset_outer_rollback_preserves_owned_marker_and_bytes_for_both_write_paths(self):
        for mode in ('upload', 'import'):
            with self.subTest(mode=mode):
                previous = set((self.asset_root / '.pending-assets').glob('*.json'))
                with self.assertRaisesRegex(ValueError, 'Synthetic asset rollback'):
                    with transaction.atomic():
                        asset = self.store_asset(mode)
                        raise ValueError('Synthetic asset rollback')
                self.assertFalse(MediaAsset.objects.filter(site=self.site).exists())
                markers = set((self.asset_root / '.pending-assets').glob('*.json')) - previous
                self.assertEqual(len(markers), 1, 'Asset rollback lost its durable file locator')
                marker = json.loads(markers.pop().read_text(encoding='utf-8'))
                self.assertEqual(marker['site_id'], self.site.pk)
                self.assertIs(marker['created'], True)
                self.assertEqual('storage/assets/' + marker['storage_path'], asset.storage_path)
                self.assertEqual((self.asset_root / marker['storage_path']).read_bytes(), b'Synthetic image fixture')

    def test_asset_commit_clears_journal_without_deleting_referenced_bytes(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            asset = self.store_asset('upload')
        self.assertEqual(len(list((self.asset_root / '.pending-assets').glob('*.json'))), 1)
        for callback in callbacks:
            callback()
        self.assertEqual(list((self.asset_root / '.pending-assets').glob('*.json')), [])
        asset.refresh_from_db()
        self.assertEqual((self.asset_root / asset.storage_path.removeprefix('storage/assets/')).read_bytes(), b'Synthetic image fixture')

    def test_asset_recovery_has_an_operator_command(self):
        self.assertIn('retry_asset_upload_cleanup', get_commands())

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
    def test_asset_http_sql_failure_retains_input_and_offers_retry_without_database_details(self):
        locale = SiteLocale.objects.create(site=self.site, locale_code='en', label='English', enabled=True, is_default=True)
        actor = get_user_model().objects.create_superuser('asset-recovery-http', 'synthetic@example.invalid', 'synthetic-only')
        client = Client(raise_request_exception=False)
        client.force_login(actor)
        source = self.import_root / 'sample.png'
        source.write_bytes(b'Synthetic image fixture')
        def fail_sql(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM synthetic_missing_failure_relation')
        for mode in ('upload', 'import_path'):
            data = {'title':'Preserved synthetic title', 'alt_text':'Preserved synthetic note'}
            if mode == 'upload':
                data['file'] = SimpleUploadedFile('sample.png', b'Synthetic image fixture')
            else:
                data['file_path'] = str(source)
            with self.subTest(mode=mode), patch('console.views._resolved_content_site_locale', return_value=(self.site, locale)), \
                    patch.object(MediaAsset.objects, 'create', side_effect=fail_sql):
                response = client.post(reverse('console:asset_' + mode), data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '未完成入库')
                self.assertContains(response, 'Preserved synthetic title')
                self.assertNotContains(response, 'synthetic_missing_failure_relation')
