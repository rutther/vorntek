"""Upload markers survive rollback; recovery never touches active/referenced files."""
import json
import os
from io import StringIO
from pathlib import Path
import tempfile
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, transaction, DatabaseError, close_old_connections
from django.test import TestCase, TransactionTestCase

from console import crm_files, crm_upload_recovery
from leads.models import Company, CrmAttachment
from sitecore.models import Site


@skipUnless(connection.vendor == 'postgresql', 'Requires durable upload recovery and PG transaction locks')
class CrmUploadRecoveryTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(code='upload_recovery', name='Synthetic upload recovery')
        self.company = Company.objects.create(site=self.site, name='Synthetic upload', normalized_name='synthetic upload')
        self.directory = tempfile.TemporaryDirectory(prefix='vorntek-upload-recovery-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.storage = patch('console.crm_files.attachment_storage_root', return_value=self.root)
        self.storage.start()
        self.addCleanup(self.storage.stop)

    def test_missing_blob_under_indirect_parent_does_not_discard_marker(self):
        token = 'a' * 32
        normal_year = self.root / 'normal_year'
        (normal_year / '10').mkdir(parents=True)
        indirect_year = self.root / '2026'
        if os.name == 'nt':
            result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(indirect_year), str(normal_year)],
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, 'Native synthetic junction could not be created')
        else:
            indirect_year.symlink_to(normal_year, target_is_directory=True)
        journal = self.root / '.pending-uploads'
        journal.mkdir()
        marker = journal / f'{self.site.pk}-{token}.json'
        marker.write_text(json.dumps({'version': 1, 'site_id': self.site.pk, 'token': token,
            'created': True, 'storage_path': f'2026/10/upload-{token}.txt'}), encoding='utf-8')
        result = crm_files.reconcile_pending_uploads(site_id=self.site.pk)
        self.assertEqual(result['invalid'], 1, 'Missing leaf hid an indirect parent and discarded recovery intent')
        self.assertTrue(marker.exists())

    def upload(self, data=b'Synthetic upload'):
        return crm_files.store_crm_attachment(site=self.site, uploaded_file=SimpleUploadedFile('synthetic.txt', data),
            title='Synthetic', uploaded_by=None, target={'company': self.company})

    def markers(self):
        directory = self.root / '.pending-uploads'
        return sorted(directory.glob('*.json')) if directory.exists() else []

    def reconcile(self, **kwargs):
        function = getattr(crm_files, 'reconcile_pending_uploads', None)
        self.assertTrue(callable(function), 'No durable orphan recovery path exists')
        return function(site_id=self.site.pk, **kwargs)

    def test_outer_rollback_keeps_marker_and_recovers_only_unreferenced_owned_file(self):
        with self.captureOnCommitCallbacks(execute=False):
            with self.assertRaisesMessage(ValueError, 'Synthetic outer rollback'):
                with transaction.atomic():
                    row = self.upload()
                    raise ValueError('Synthetic outer rollback')
        self.assertFalse(CrmAttachment.objects.filter(site=self.site).exists())
        self.assertTrue((self.root / row.storage_path).is_file())
        self.assertEqual(len(self.markers()), 1, 'Rollback lost the persistent cleanup locator')
        result = self.reconcile()
        self.assertEqual(result['removed'], 1)
        self.assertFalse((self.root / row.storage_path).exists())
        self.assertEqual(self.markers(), [])

    def test_successful_commit_removes_marker_without_changing_blob_or_row(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            row = self.upload()
        self.assertEqual(len(self.markers()), 1, 'No crash-safe commit boundary marker exists')
        for callback in callbacks:
            callback()
        self.assertEqual(self.markers(), [])
        row.refresh_from_db()
        self.assertEqual((self.root / row.storage_path).read_bytes(), b'Synthetic upload')

    def test_failed_sql_and_failed_cleanup_keep_marker_for_later_retry(self):
        def fail_insert(**kwargs):
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1/0')
        original = Path.unlink
        def fail_unlink(path, *args, **kwargs):
            if path.name.endswith('.txt'):
                raise PermissionError('Synthetic private upload cleanup failure')
            return original(path, *args, **kwargs)
        with patch.object(CrmAttachment.objects, 'create', side_effect=fail_insert), patch.object(Path, 'unlink', new=fail_unlink):
            with self.assertRaises(DatabaseError) as error:
                self.upload()
        self.assertEqual(error.exception.__cause__.sqlstate, '22012')
        self.assertEqual(len(self.markers()), 1, 'Only a log remains; cleanup is not recoverable')
        result = self.reconcile()
        self.assertEqual(result['removed'], 1)
        self.assertEqual(self.markers(), [])

    def test_late_marker_recovery_preserves_committed_and_pending_purge_references(self):
        with self.captureOnCommitCallbacks(execute=False):
            row = self.upload()
        self.assertEqual(len(self.markers()), 1)
        result = self.reconcile()
        self.assertEqual(result['referenced'], 1)
        self.assertEqual((self.root / row.storage_path).read_bytes(), b'Synthetic upload')
        self.assertEqual(self.markers(), [])

    def test_invalid_marker_and_wrong_site_do_not_delete_any_file(self):
        directory = self.root / '.pending-uploads'
        directory.mkdir()
        token = 'a' * 32
        marker = directory / f'{self.site.pk}-{token}.json'
        marker.write_text(json.dumps({'version': 1, 'site_id': self.site.pk, 'token': token,
                                     'storage_path': '../outside.txt', 'created': True}), encoding='utf-8')
        kept = self.root / 'untouched.txt'
        kept.write_bytes(b'Not an owned pending upload')
        result = self.reconcile()
        self.assertEqual(result['invalid'], 1)
        self.assertTrue(marker.exists())
        self.assertEqual(kept.read_bytes(), b'Not an owned pending upload')
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_unconfirmed_file_creation_is_not_guessed_safe_to_delete(self):
        directory = self.root / '.pending-uploads'
        directory.mkdir()
        token = 'b' * 32
        relative = f'2026/10/upload-{token}.txt'
        path = self.root / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b'Cannot prove which writer created this')
        marker = directory / f'{self.site.pk}-{token}.json'
        marker.write_text(json.dumps({'version': 1, 'site_id': self.site.pk, 'token': token,
                                     'storage_path': relative, 'created': False}), encoding='utf-8')
        result = self.reconcile()
        self.assertEqual(result['uncertain'], 1)
        self.assertTrue(marker.exists())
        self.assertEqual(path.read_bytes(), b'Cannot prove which writer created this')

    def test_boolean_schema_version_does_not_authorize_blob_removal(self):
        with self.captureOnCommitCallbacks(execute=False):
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    row = self.upload()
                    raise ValueError('Synthetic rollback')
        marker = self.markers()[0]
        data = json.loads(marker.read_text(encoding='utf-8'))
        data['version'] = True
        marker.write_text(json.dumps(data), encoding='utf-8')
        result = self.reconcile()
        self.assertEqual(result['invalid'], 1)
        self.assertTrue((self.root / row.storage_path).is_file())
        self.assertTrue(marker.exists())

    def test_boolean_site_cannot_use_a_different_upload_lock_identity(self):
        with self.assertRaises(ValidationError):
            crm_files.reconcile_pending_uploads(site_id=True)

    def test_marker_removed_by_commit_during_recovery_is_skipped(self):
        with self.captureOnCommitCallbacks(execute=False):
            row = self.upload()
        marker = self.markers()[0]
        resolve = crm_upload_recovery.resolve_storage_asset_path
        def resolve_after_commit(relative, **kwargs):
            if relative.endswith(marker.name):
                marker.unlink()
            return resolve(relative, **kwargs)
        with patch.object(crm_upload_recovery, 'resolve_storage_asset_path', side_effect=resolve_after_commit):
            result = self.reconcile()
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['invalid'], 0)
        self.assertEqual((self.root / row.storage_path).read_bytes(), b'Synthetic upload')

    def test_command_can_preview_then_recover_an_upload_rollback(self):
        with self.captureOnCommitCallbacks(execute=False):
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    row = self.upload()
                    raise ValueError('Synthetic rollback')
        output = StringIO()
        call_command('retry_private_attachment_cleanup', site=self.site.code, stdout=output)
        self.assertIn('pending_uploads', output.getvalue(), 'Operator cannot discover pending upload recovery')
        self.assertTrue((self.root / row.storage_path).exists())
        output = StringIO()
        call_command('retry_private_attachment_cleanup', site=self.site.code, execute=True,
                     confirm='RETRY FILE CLEANUP', stdout=output)
        self.assertIn('upload_cleanup', output.getvalue())
        self.assertFalse((self.root / row.storage_path).exists())
        self.assertEqual(self.markers(), [])

    def test_new_private_files_request_owner_only_mode_at_os_boundary(self):
        actual_open = os.open
        requested = []
        def open_file(path, flags, mode=0o777, **kwargs):
            if flags & os.O_CREAT and str(path).startswith(str(self.root)):
                requested.append(mode)
            return actual_open(path, flags, mode, **kwargs)
        with patch.object(os, 'open', side_effect=open_file), self.captureOnCommitCallbacks(execute=False):
            self.upload()
        self.assertTrue(requested, 'New private files bypass the explicit owner-only creation boundary')
        self.assertEqual(set(requested), {0o600})

    def test_dangling_symlink_does_not_discard_a_pending_marker(self):
        token = 'c' * 32
        relative = f'2026/10/upload-{token}.txt'
        path = self.root / relative
        path.parent.mkdir(parents=True)
        try:
            path.symlink_to(self.root / 'missing-target.txt')
        except OSError as error:
            self.skipTest(f'Native file symlink unavailable: {type(error).__name__}; Linux coverage remains required')
        directory = self.root / '.pending-uploads'
        directory.mkdir()
        marker = directory / f'{self.site.pk}-{token}.json'
        marker.write_text(json.dumps({'version': 1, 'site_id': self.site.pk, 'token': token,
                                     'storage_path': relative, 'created': True}), encoding='utf-8')
        result = self.reconcile()
        self.assertEqual(result['invalid'], 1)
        self.assertTrue(marker.exists())
        self.assertTrue(path.is_symlink())

    def test_purge_reference_protects_upload_until_the_retirement_cleanup_runs(self):
        with self.captureOnCommitCallbacks(execute=False):
            row = self.upload()
            relative = row.storage_path
            crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=row.pk), reason='privacy_request')
        self.assertEqual(self.reconcile()['referenced'], 1)
        row.refresh_from_db()
        self.assertEqual(row.purge_storage_path, relative)
        self.assertEqual((self.root / relative).read_bytes(), b'Synthetic upload')

    def test_recovery_pages_markers_without_an_unbounded_cleanup_batch(self):
        with self.captureOnCommitCallbacks(execute=False):
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    self.upload(b'First synthetic page')
                    self.upload(b'Second synthetic page')
                    raise ValueError('Synthetic rollback')
        first = self.reconcile(limit=1)
        self.assertEqual(first['processed'], 1)
        self.assertEqual(first['removed'], 1)
        self.assertTrue(first['next_cursor'])
        self.assertEqual(len(self.markers()), 1)
        second = self.reconcile(limit=1, after=first['next_cursor'])
        self.assertEqual(second['removed'], 1)
        self.assertEqual(self.markers(), [])

    def test_uuid_file_collision_never_overwrites_or_cleans_existing_bytes(self):
        from django.utils import timezone
        token = 'd' * 32
        now = timezone.now()
        path = self.root / f'{now:%Y}/{now:%m}/upload-{token}.txt'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'Existing bytes belong to another writer')
        with patch.object(crm_upload_recovery.uuid, 'uuid4') as identifier:
            identifier.return_value.hex = token
            with self.assertRaises(FileExistsError):
                self.upload()
        self.assertEqual(path.read_bytes(), b'Existing bytes belong to another writer')
        self.assertEqual(self.markers(), [])

    def test_invalid_upload_cursor_rejects_the_entire_command_before_retirement_deletes(self):
        from django.core.management.base import CommandError
        with self.captureOnCommitCallbacks(execute=False):
            row = self.upload()
            relative = row.storage_path
            crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=row.pk), reason='privacy_request')
        caught = None
        try:
            call_command('retry_private_attachment_cleanup', site=self.site.code, execute=True,
                         confirm='RETRY FILE CLEANUP', upload_cursor='../invalid', stdout=StringIO())
        except Exception as error:
            caught = error
        row.refresh_from_db()
        self.assertEqual(row.purge_storage_path, relative)
        self.assertTrue((self.root / relative).exists())
        self.assertIsInstance(caught, CommandError)


@skipUnless(connection.vendor == 'postgresql', 'Requires committed uploads and independent PG lock observers')
class CrmUploadRecoveryConcurrencyTests(TransactionTestCase):
    def _fixture_teardown(self):
        # This single concurrency class runs in its own disposable full-SQL DB.
        return None

    def test_recovery_skips_active_upload_then_resolves_real_commit_or_rollback(self):
        site = Site.objects.create(code='upload_real_lock', name='Synthetic upload lock')
        company = Company.objects.create(site=site, name='Synthetic lock', normalized_name='synthetic lock')
        with tempfile.TemporaryDirectory(prefix='vorntek-upload-lock-') as directory, \
             patch('console.crm_files.attachment_storage_root', return_value=Path(directory)):
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                observer_pid = cursor.fetchone()[0]
            for commit in (False, True):
                staged, release = threading.Event(), threading.Event()
                shared = {}
                def store():
                    close_old_connections()
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()')
                            shared['pid'] = cursor.fetchone()[0]
                        try:
                            with transaction.atomic():
                                row = crm_files.store_crm_attachment(site=site, uploaded_file=SimpleUploadedFile(
                                    'active.txt', b'Synthetic active upload'), title='Synthetic', uploaded_by=None,
                                    target={'company': company})
                                shared['path'] = row.storage_path
                                staged.set()
                                if not release.wait(10):
                                    raise AssertionError('Observer did not release the upload')
                                if not commit:
                                    raise ValueError('Synthetic transaction rollback')
                        except ValueError:
                            if commit:
                                raise
                    finally:
                        close_old_connections()
                with self.subTest(commit=commit), ThreadPoolExecutor(max_workers=1) as executor:
                    task = executor.submit(store)
                    try:
                        self.assertTrue(staged.wait(10))
                        self.assertNotEqual(shared['pid'], observer_pid)
                        result = crm_files.reconcile_pending_uploads(site_id=site.pk)
                        self.assertEqual(result['busy'], 1)
                        self.assertTrue((Path(directory) / shared['path']).exists())
                    finally:
                        release.set()
                    task.result(timeout=15)
                    result = crm_files.reconcile_pending_uploads(site_id=site.pk)
                    if commit:
                        self.assertEqual(result['removed'], 0)
                        self.assertEqual((Path(directory) / shared['path']).read_bytes(), b'Synthetic active upload')
                    else:
                        self.assertEqual(result['removed'], 1)
                        self.assertFalse((Path(directory) / shared['path']).exists())
