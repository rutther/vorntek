"""Retirement must keep enough restricted state to retry a physical deletion."""
from pathlib import Path
from io import StringIO
import tempfile
import os
import subprocess
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from console import crm_files
from leads.models import Company, CrmAttachment
from sitecore.models import Site


@skipUnless(connection.vendor == 'postgresql', 'Requires real attachment retirement SQL and callbacks')
class CrmAttachmentRecoveryTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(code='attachment_recovery', name='Synthetic file recovery')
        self.company = Company.objects.create(site=self.site, name='Synthetic company', normalized_name='synthetic company')
        self.directory = tempfile.TemporaryDirectory(prefix='vorntek-attachment-recovery-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root_patch = patch('console.crm_files.attachment_storage_root', return_value=self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.row = crm_files.store_crm_attachment(
            site=self.site, uploaded_file=SimpleUploadedFile('private.txt', b'Synthetic private file'),
            title='Synthetic', uploaded_by=None, target={'company': self.company})
        self.storage_path = self.row.storage_path
        self.path = self.root / self.storage_path

    def retire(self):
        return crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=self.row.pk), reason='privacy_request')

    def test_retirement_keeps_restricted_cleanup_path_until_physical_success(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.assertEqual(self.retire(), 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.storage_path, '')
        self.assertEqual(self.row.original_name, '')
        self.assertEqual(self.row.status, 'archived')
        self.assertEqual(getattr(self.row, 'purge_storage_path', ''), self.storage_path,
                         'Physical deletion intent was lost after logical retirement')
        self.assertTrue(self.path.is_file())
        self.assertEqual(len(callbacks), 1)

    def test_unlink_failure_remains_pending_without_raising_after_commit(self):
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == self.path:
                raise PermissionError('Synthetic host/path detail must not become persisted error text')
            return original_unlink(path, *args, **kwargs)

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.retire()
        errors = []
        with patch.object(Path, 'unlink', new=unlink):
            for callback in callbacks:
                try:
                    callback()
                except OSError as error:
                    errors.append(type(error).__name__)
        self.row.refresh_from_db()
        self.assertEqual(errors, [], 'A committed logical operation became an unhandled cleanup error')
        self.assertEqual(getattr(self.row, 'purge_storage_path', ''), self.storage_path)
        self.assertEqual(getattr(self.row, 'purge_error_code', ''), 'filesystem')
        self.assertEqual(getattr(self.row, 'purge_attempts', 0), 1)
        self.assertTrue(self.path.is_file())

    def test_retirement_rollback_preserves_original_file_and_row(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaisesMessage(ValueError, 'Synthetic rollback'):
                with transaction.atomic():
                    self.retire()
                    raise ValueError('Synthetic rollback')
        self.row.refresh_from_db()
        self.assertEqual(callbacks, [])
        self.assertEqual(self.row.storage_path, self.storage_path)
        self.assertEqual(self.row.status, 'active')
        self.assertEqual(getattr(self.row, 'purge_storage_path', ''), '')
        self.assertEqual(self.path.read_bytes(), b'Synthetic private file')

    def test_shared_reference_is_retained_and_later_cleanup_is_retryable(self):
        other = CrmAttachment.objects.create(site=self.site, company=self.company, storage_path=self.storage_path,
                                             sha256=self.row.sha256, original_name='another.txt')
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.retire()
        for callback in callbacks:
            callback()
        self.row.refresh_from_db()
        self.assertTrue(self.path.is_file())
        self.assertEqual(getattr(self.row, 'purge_storage_path', ''), self.storage_path)
        self.assertEqual(getattr(self.row, 'purge_error_code', ''), 'shared')
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=other.pk), reason='privacy_request')
        for callback in callbacks:
            callback()
        retry = getattr(crm_files, 'process_private_attachment_cleanup', None)
        self.assertTrue(callable(retry), 'No bounded retry path exists for retained cleanup state')
        retry(site_id=self.site.pk, limit=10)
        self.row.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.row.purge_storage_path, '')
        self.assertEqual(other.purge_storage_path, '')
        self.assertFalse(self.path.exists())

    def test_cleanup_database_failure_keeps_retry_intent_after_file_is_gone(self):
        with self.captureOnCommitCallbacks(execute=False):
            self.retire()

        def fail_save(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1/0')

        with patch.object(CrmAttachment, 'save', side_effect=fail_save):
            with self.assertRaises(DatabaseError) as caught:
                crm_files.process_private_attachment_cleanup(site_id=self.site.pk)
        self.assertEqual(caught.exception.__cause__.sqlstate, '22012')
        self.row.refresh_from_db()
        self.assertEqual(self.row.purge_storage_path, self.storage_path)
        self.assertFalse(self.path.exists())
        result = crm_files.process_private_attachment_cleanup(site_id=self.site.pk)
        self.row.refresh_from_db()
        self.assertEqual(result['removed'], 1)
        self.assertEqual(self.row.purge_storage_path, '')

    def test_cleanup_wrong_site_leaves_pending_file_untouched(self):
        other_site = Site.objects.create(code='attachment_recovery_other', name='Synthetic other')
        with self.captureOnCommitCallbacks(execute=False):
            self.retire()
        self.assertEqual(crm_files.process_private_attachment_cleanup(site_id=other_site.pk)['processed'], 0)
        self.row.refresh_from_db()
        self.assertEqual(self.row.purge_storage_path, self.storage_path)
        self.assertTrue(self.path.exists())

    def test_invalid_cleanup_path_is_not_marked_complete(self):
        with self.captureOnCommitCallbacks(execute=False):
            self.retire()
        CrmAttachment.objects.filter(pk=self.row.pk).update(purge_storage_path='../outside-synthetic.txt')
        result = crm_files.process_private_attachment_cleanup(site_id=self.site.pk)
        self.row.refresh_from_db()
        self.assertEqual(result['pending'], 1)
        self.assertEqual(self.row.purge_storage_path, '../outside-synthetic.txt')
        self.assertEqual(self.row.purge_error_code, 'invalid_path')
        self.assertTrue(self.path.exists())

    def test_indirect_cleanup_path_never_deletes_a_different_active_file(self):
        self._assert_indirect_cleanup_rejected(missing=False)

    def test_indirect_missing_cleanup_path_preserves_retry_intent(self):
        self._assert_indirect_cleanup_rejected(missing=True)

    def _assert_indirect_cleanup_rejected(self, *, missing):
        normal = self.root / 'normal'
        normal.mkdir()
        protected = normal / 'protected.txt'
        protected.write_bytes(b'Synthetic normal referenced file')
        active = CrmAttachment.objects.create(site=self.site, company=self.company, title='Synthetic normal',
            storage_path='normal/protected.txt', status='active')
        indirect = self.root / 'indirect'
        if os.name == 'nt':
            outcome = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(indirect), str(normal)],
                                     capture_output=True, check=False)
            self.assertEqual(outcome.returncode, 0, 'Native synthetic junction could not be created')
        else:
            indirect.symlink_to(normal, target_is_directory=True)
        with self.captureOnCommitCallbacks(execute=False):
            self.retire()
        relative = 'indirect/missing.txt' if missing else 'indirect/protected.txt'
        CrmAttachment.objects.filter(pk=self.row.pk).update(purge_storage_path=relative)
        result = crm_files.process_private_attachment_cleanup(site_id=self.site.pk)
        self.row.refresh_from_db()
        active.refresh_from_db()
        self.assertEqual(result['pending'], 1, 'Indirect storage path was treated as successful removal')
        self.assertEqual(self.row.purge_storage_path, relative)
        self.assertEqual(self.row.purge_error_code, 'invalid_path')
        self.assertEqual(protected.read_bytes(), b'Synthetic normal referenced file')
        self.assertEqual(active.storage_path, 'normal/protected.txt')

    def test_cleanup_command_requires_explicit_execute_and_confirmation(self):
        with self.captureOnCommitCallbacks(execute=False):
            self.retire()
        self.assertIn('retry_private_attachment_cleanup', get_commands(), 'No authorized retry entry exists')
        output = StringIO()
        call_command('retry_private_attachment_cleanup', site=self.site.code, stdout=output)
        self.assertTrue(self.path.exists())
        self.assertNotIn(self.storage_path, output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        with self.assertRaises(CommandError):
            call_command('retry_private_attachment_cleanup', site=self.site.code, execute=True, stdout=StringIO())
        self.assertTrue(self.path.exists())
        call_command('retry_private_attachment_cleanup', site=self.site.code, execute=True,
                     confirm='RETRY FILE CLEANUP', limit=1, stdout=output)
        self.row.refresh_from_db()
        self.assertEqual(self.row.purge_storage_path, '')
        self.assertFalse(self.path.exists())

    def test_cleanup_command_limit_is_bounded_and_rejects_unknown_site(self):
        self.assertIn('retry_private_attachment_cleanup', get_commands(), 'No bounded retry entry exists')
        for limit in (0, 101):
            with self.subTest(limit=limit), self.assertRaises(CommandError):
                call_command('retry_private_attachment_cleanup', site=self.site.code, limit=limit, stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('retry_private_attachment_cleanup', site='unknown_synthetic_site', stdout=StringIO())
        self.assertTrue(self.path.exists())

    def test_upload_cleanup_failure_does_not_replace_original_sql_exception(self):
        def fail_insert(**kwargs):
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1/0')

        captured = None
        with patch.object(CrmAttachment.objects, 'create', side_effect=fail_insert), \
             patch.object(Path, 'unlink', side_effect=PermissionError('Synthetic cleanup failure')):
            try:
                crm_files.store_crm_attachment(site=self.site,
                    uploaded_file=SimpleUploadedFile('other.txt', b'Other synthetic file'),
                    title='Fails SQL', uploaded_by=None, target={'company': self.company})
            except Exception as error:
                captured = error
        self.assertIsNotNone(captured)
        self.assertEqual(getattr(captured.__cause__, 'sqlstate', None), '22012',
                         'Filesystem cleanup masked the original PostgreSQL failure')
        self.assertEqual(CrmAttachment.objects.filter(site=self.site).count(), 1)
