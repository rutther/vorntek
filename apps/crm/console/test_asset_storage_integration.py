from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings

from sitecore.models import MediaAsset

from console.content_access import ASSETS_READ
from console.forms import (
    AssetImportPathForm,
    _managed_asset_target,
    store_local_asset,
    store_uploaded_asset,
)
from console.storage_security import StorageSecurityError, resolve_storage_asset_path
from console.views import asset_file


class AssetStorageIntegrationTests(SimpleTestCase):
    def setUp(self):
        # Candidate-local temporary roots avoid reading or mutating production
        # assets and make the required allowlists explicit in every test.
        self.storage_directory = TemporaryDirectory(dir=settings.BASE_DIR)
        self.import_directory = TemporaryDirectory(dir=settings.BASE_DIR)
        self.outside_directory = TemporaryDirectory(dir=settings.BASE_DIR)
        self.storage_root = Path(self.storage_directory.name).resolve()
        self.import_root = Path(self.import_directory.name).resolve()
        self.outside_root = Path(self.outside_directory.name).resolve()
        self.import_file = self.import_root / 'source.png'
        self.import_file.write_bytes(b'synthetic-import')
        self.outside_file = self.outside_root / 'outside.png'
        self.outside_file.write_bytes(b'synthetic-outside')

    def tearDown(self):
        self.storage_directory.cleanup()
        self.import_directory.cleanup()
        self.outside_directory.cleanup()

    def storage_settings(self):
        return override_settings(
            SITEOS_ASSET_STORAGE_ROOTS=[str(self.storage_root)],
            SITEOS_ASSET_IMPORT_ROOTS=[str(self.import_root)],
        )

    def test_import_form_accepts_only_existing_file_under_explicit_import_root(self):
        with self.storage_settings():
            form = AssetImportPathForm({'file_path': str(self.import_file)})

            self.assertTrue(form.is_valid(), form.errors.as_text())
            self.assertEqual(form.cleaned_data['file_path'], self.import_file)

    def test_import_form_rejects_outside_file_without_disclosing_host_paths(self):
        with self.storage_settings():
            form = AssetImportPathForm({'file_path': str(self.outside_file)})

            self.assertFalse(form.is_valid())
            rendered = form.errors.as_text()
            self.assertIn('文件不在允许的导入目录中或不可用', rendered)
            self.assertNotIn(str(self.import_root), rendered)
            self.assertNotIn(str(self.outside_file), rendered)

    @override_settings(SITEOS_ASSET_IMPORT_ROOTS=[])
    def test_import_form_fails_closed_when_import_allowlist_is_empty(self):
        form = AssetImportPathForm({'file_path': str(self.import_file)})

        self.assertFalse(form.is_valid())
        self.assertIn('文件不在允许的导入目录中或不可用', form.errors.as_text())

    def test_direct_local_store_cannot_bypass_import_allowlist(self):
        with self.storage_settings():
            with self.assertRaises(StorageSecurityError) as raised:
                store_local_asset(
                    site=object(),
                    source_path=self.outside_file,
                    title='',
                    alt_text='',
                    caption='',
                    created_by='test',
                )

        self.assertEqual(raised.exception.code, 'outside_allowlist')

    def test_local_import_copies_from_import_root_into_storage_root(self):
        created = SimpleNamespace(id=2)
        with self.storage_settings():
            with (
                patch.object(MediaAsset.objects, 'filter') as filtered,
                patch.object(MediaAsset.objects, 'create', return_value=created) as create,
            ):
                filtered.return_value.first.return_value = None
                result = store_local_asset(
                    site=object(),
                    source_path=self.import_file,
                    title='Imported synthetic file',
                    alt_text='',
                    caption='',
                    created_by='test',
                )

            storage_path = create.call_args.kwargs['storage_path']
            stored_file = resolve_storage_asset_path(storage_path)

        self.assertIs(result, created)
        self.assertEqual(create.call_args.kwargs['mime_type'], 'image/png')
        self.assertEqual(stored_file.read_bytes(), b'synthetic-import')
        self.assertEqual(stored_file.parents[3], self.storage_root)

    def test_upload_writes_only_to_explicit_storage_root(self):
        uploaded = SimpleUploadedFile('sample.png', b'synthetic-upload', content_type='text/html')
        created = SimpleNamespace(id=1)
        with self.storage_settings():
            with (
                patch.object(MediaAsset.objects, 'filter') as filtered,
                patch.object(MediaAsset.objects, 'create', return_value=created) as create,
            ):
                filtered.return_value.first.return_value = None
                result = store_uploaded_asset(
                    site=object(),
                    uploaded_file=uploaded,
                    title='Synthetic',
                    alt_text='',
                    caption='',
                    created_by='test',
                )

            storage_path = create.call_args.kwargs['storage_path']
            stored_mime_type = create.call_args.kwargs['mime_type']
            stored_file = resolve_storage_asset_path(storage_path)

        self.assertIs(result, created)
        self.assertEqual(stored_mime_type, 'image/png')
        self.assertTrue(stored_file.is_file())
        self.assertEqual(stored_file.read_bytes(), b'synthetic-upload')
        self.assertEqual(stored_file.parents[3], self.storage_root)

        asset = SimpleNamespace(storage_path=storage_path, mime_type=stored_mime_type)
        request = RequestFactory().get('/admin/assets/1/file/')
        request.user = SimpleNamespace(is_authenticated=True)
        with self.storage_settings():
            with (
                patch(
                    'console.views.content_site_scope',
                    return_value=(object(), object(), frozenset({ASSETS_READ})),
                ),
                patch('console.views.get_object_or_404', return_value=asset),
            ):
                response = asset_file(request, 1)
                b''.join(response.streaming_content)
                response.close()

        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertNotEqual(response['Content-Type'], 'text/html')

    def test_existing_asset_with_outside_storage_path_fails_closed(self):
        uploaded = SimpleUploadedFile('sample.png', b'synthetic-upload', content_type='image/png')
        existing = SimpleNamespace(storage_path=str(self.outside_file))
        with self.storage_settings():
            with patch.object(MediaAsset.objects, 'filter') as filtered:
                filtered.return_value.first.return_value = existing
                with self.assertRaises(StorageSecurityError) as raised:
                    store_uploaded_asset(
                        site=object(),
                        uploaded_file=uploaded,
                        title='',
                        alt_text='',
                        caption='',
                        created_by='test',
                    )

        self.assertEqual(raised.exception.code, 'outside_allowlist')
        self.assertEqual(self.outside_file.read_bytes(), b'synthetic-outside')

    def test_generated_write_target_rejects_filename_traversal(self):
        with self.storage_settings():
            with self.assertRaises(StorageSecurityError) as raised:
                _managed_asset_target(
                    directory_name='images',
                    year='2026',
                    month='08',
                    filename='../escape.png',
                )

        self.assertEqual(raised.exception.code, 'invalid_storage_destination')

    def test_generated_write_target_rejects_symlink_or_reparse_parent(self):
        linked_images = self.storage_root / 'images'
        try:
            linked_images.symlink_to(self.outside_root, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f'platform cannot create a directory symlink/reparse point: {type(exc).__name__}')

        with self.storage_settings():
            with self.assertRaises(StorageSecurityError) as raised:
                _managed_asset_target(
                    directory_name='images',
                    year='2026',
                    month='08',
                    filename='sample.png',
                )

        self.assertEqual(raised.exception.code, 'indirect_path_not_allowed')

    def test_asset_file_reads_allowlisted_storage_and_sets_private_headers(self):
        stored_file = self.storage_root / 'images' / 'sample.png'
        stored_file.parent.mkdir()
        stored_file.write_bytes(b'synthetic-response')
        asset = SimpleNamespace(
            storage_path='storage/assets/images/sample.png',
            mime_type='text/html',
        )
        request = RequestFactory().get('/admin/assets/1/file/')
        request.user = SimpleNamespace(is_authenticated=True)

        with self.storage_settings():
            with (
                patch(
                    'console.views.content_site_scope',
                    return_value=(object(), object(), frozenset({ASSETS_READ})),
                ),
                patch('console.views.get_object_or_404', return_value=asset),
            ):
                response = asset_file(request, 1)
                body = b''.join(response.streaming_content)
                response.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, b'synthetic-response')
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_asset_file_outside_allowlist_is_generic_404(self):
        asset = SimpleNamespace(storage_path=str(self.outside_file), mime_type='image/png')
        request = RequestFactory().get('/admin/assets/1/file/')
        request.user = SimpleNamespace(is_authenticated=True)

        with self.storage_settings():
            with (
                patch(
                    'console.views.content_site_scope',
                    return_value=(object(), object(), frozenset({ASSETS_READ})),
                ),
                patch('console.views.get_object_or_404', return_value=asset),
            ):
                with self.assertRaises(Http404) as raised:
                    asset_file(request, 1)

        self.assertEqual(str(raised.exception), '资产文件不可用。')
        self.assertNotIn(str(self.outside_file), str(raised.exception))
