"""Portable file/DB boundary cases; native locking is verified separately."""
import json
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TestCase

from console import crm_files
from leads.models import Company, CrmAttachment
from sitecore.models import Site


class FileLifecycleFixture:
    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        with connection.schema_editor() as editor:
            for model in apps.get_models():
                if not model._meta.managed and not model._meta.proxy and model._meta.db_table not in existing:
                    editor.create_model(model)
                    cls.created_models.append(model)
                    existing.add(model._meta.db_table)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)

    def setUp(self):
        self.site = Site.objects.create(code='files_demo', name='Synthetic file contract')
        self.company = Company.objects.create(site=self.site, name='Synthetic organization')
        directory = tempfile.TemporaryDirectory(prefix='vorntek-file-contract-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        scoped_root = patch('console.crm_files.attachment_storage_root', return_value=self.root)
        scoped_root.start()
        self.addCleanup(scoped_root.stop)

    def upload(self):
        return crm_files.store_crm_attachment(site=self.site,
            uploaded_file=SimpleUploadedFile('synthetic.txt', b'Synthetic attachment only'),
            title='Synthetic example', uploaded_by=None, target={'company':self.company})


class FileLifecycleContractTests(FileLifecycleFixture, TestCase):
    def test_outer_rollback_preserves_owned_recovery_marker_without_a_committed_row(self):
        with self.assertRaisesRegex(ValueError, 'synthetic rollback'):
            with transaction.atomic():
                row = self.upload()
                relative = row.storage_path
                raise ValueError('synthetic rollback')
        self.assertEqual(CrmAttachment.objects.count(), 0)
        self.assertEqual((self.root/relative).read_bytes(), b'Synthetic attachment only')
        markers = list((self.root/'.pending-uploads').glob('*.json'))
        self.assertEqual(len(markers), 1)
        marker = json.loads(markers[0].read_text())
        self.assertEqual(marker['site_id'], self.site.pk)
        self.assertEqual(marker['storage_path'], relative)
        self.assertIs(marker['created'], True)

    def test_retirement_records_retry_intent_before_attempting_physical_cleanup(self):
        row = self.upload()
        relative = row.storage_path
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=row.pk), reason='privacy_request')
        row.refresh_from_db()
        self.assertEqual((row.status, row.storage_path, row.original_name), ('archived', '', ''))
        self.assertEqual(row.purge_storage_path, relative)
        self.assertEqual(len(callbacks), 1)
        self.assertTrue((self.root/relative).is_file())

    def test_cleanup_failure_preserves_a_retryable_safe_error_and_does_not_escape_callback(self):
        row = self.upload()
        relative = row.storage_path
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=row.pk), reason='retention_policy')
        original_unlink = Path.unlink
        def refuse_selected(path, *args, **kwargs):
            if path == self.root/relative:
                raise PermissionError('private host detail must not become an error message')
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', refuse_selected):
            for callback in callbacks:
                callback()
        row.refresh_from_db()
        self.assertEqual(row.purge_storage_path, relative)
        self.assertEqual(row.purge_error_code, 'filesystem')
        self.assertEqual(row.purge_attempts, 1)
        self.assertTrue((self.root/relative).is_file())

    def test_cleanup_preserves_active_shared_reference_then_resumes_after_last_retirement(self):
        first, second = self.upload(), self.upload()
        self.assertEqual(first.storage_path, second.storage_path)
        relative = first.storage_path
        with self.captureOnCommitCallbacks(execute=True):
            crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=first.pk), reason='privacy_request')
        first.refresh_from_db()
        self.assertEqual(first.purge_error_code, 'shared')
        self.assertEqual(first.purge_storage_path, relative)
        self.assertEqual((self.root/relative).read_bytes(), b'Synthetic attachment only')
        with self.captureOnCommitCallbacks(execute=True):
            crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=second.pk), reason='privacy_request')
        cleanup = getattr(crm_files, 'process_private_attachment_cleanup', None)
        self.assertTrue(callable(cleanup), 'Missing bounded cleanup retry service')
        cleanup(site_id=self.site.pk, limit=2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.purge_storage_path, second.purge_storage_path), ('', ''))
        self.assertFalse((self.root/relative).exists())

    def test_retry_command_previews_without_changes_and_requires_explicit_execution_confirmation(self):
        row = self.upload()
        relative = row.storage_path
        with self.captureOnCommitCallbacks(execute=False):
            crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=row.pk), reason='privacy_request')
        self.assertIn('retry_private_attachment_cleanup', get_commands())
        output = StringIO()
        call_command('retry_private_attachment_cleanup', site=self.site.code, stdout=output)
        self.assertIn('Preview only', output.getvalue())
        self.assertTrue((self.root/relative).is_file())
        with self.assertRaises(CommandError):
            call_command('retry_private_attachment_cleanup', site=self.site.code, execute=True, stdout=StringIO())
        for limit in (0, 101):
            with self.subTest(limit=limit), self.assertRaises(CommandError):
                call_command('retry_private_attachment_cleanup', site=self.site.code, limit=limit, stdout=StringIO())
        call_command('retry_private_attachment_cleanup', site=self.site.code, execute=True,
                     confirm='RETRY FILE CLEANUP', limit=2, stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.purge_storage_path, '')
        self.assertFalse((self.root/relative).exists())
