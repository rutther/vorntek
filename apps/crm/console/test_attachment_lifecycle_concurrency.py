"""Real row-lock interleavings protect active files while retiring old refs."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from console import crm_files
from leads.models import Company, CrmAttachment
from sitecore.models import Site


@skipUnless(connection.vendor == 'postgresql', 'Requires committed attachment row locks')
class AttachmentLifecycleConcurrencyTests(TransactionTestCase):
    def _fixture_teardown(self):
        # Run this single test in its own full-SQL disposable database; a
        # managed-only flush cannot truncate the unmanaged relationship graph.
        return None

    def worker(self, action, metadata):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                metadata['pid'] = cursor.fetchone()[0]
                cursor.execute("SET lock_timeout = '8s'")
            metadata['ready'].set()
            return action()
        finally:
            close_old_connections()

    def wait_for_lock(self, pid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [pid])
                if cursor.fetchone()[0]:
                    return True
            time.sleep(.01)
        return False

    def upload(self, site, company, content, title='Synthetic original'):
        return crm_files.store_crm_attachment(site=site,
            uploaded_file=SimpleUploadedFile('synthetic.txt', content), title=title,
            uploaded_by=None, target={'company': company})

    def test_upload_retirement_and_cleanup_interleave_without_losing_active_bytes(self):
        proofs = []
        for order in ('upload_first', 'retire_first', 'two_cleanups'):
            with self.subTest(order=order), tempfile.TemporaryDirectory(prefix='vorntek-file-interleave-') as folder, \
                 patch('console.crm_files.attachment_storage_root', return_value=Path(folder)):
                site = Site.objects.create(code='file_' + order, name='Synthetic attachment concurrency')
                company = Company.objects.create(site=site, name='Synthetic', normalized_name='synthetic')
                content = ('Synthetic private bytes ' + order).encode()
                original = self.upload(site, company, content)
                original_path = Path(folder) / original.storage_path
                staged, release = threading.Event(), threading.Event()
                first_meta = {'ready': threading.Event()}
                second_meta = {'ready': threading.Event()}

                if order == 'upload_first':
                    create = CrmAttachment.objects.create
                    def staged_create(**kwargs):
                        if kwargs['title'] == 'Synthetic duplicate':
                            staged.set()
                            if not release.wait(10):
                                raise AssertionError('Uploader was not released')
                        return create(**kwargs)
                    with patch.object(CrmAttachment.objects, 'create', side_effect=staged_create), \
                         ThreadPoolExecutor(max_workers=2) as executor:
                        first = executor.submit(self.worker, lambda: self.upload(site, company, content,
                            'Synthetic duplicate'), first_meta)
                        try:
                            self.assertTrue(staged.wait(5))
                            second = executor.submit(self.worker, lambda: crm_files.retire_private_attachments(
                                CrmAttachment.objects.filter(pk=original.pk), reason='privacy_request'), second_meta)
                            self.assertTrue(second_meta['ready'].wait(5))
                            self.assertTrue(self.wait_for_lock(second_meta['pid']), 'Retirement did not wait for the referenced row')
                        finally:
                            release.set()
                        duplicate = first.result(timeout=15)
                        self.assertEqual(second.result(timeout=15), 1)
                    original.refresh_from_db()
                    self.assertEqual(original.purge_error_code, 'shared')
                    self.assertEqual(duplicate.storage_path, original.purge_storage_path)
                    self.assertEqual(original_path.read_bytes(), content)
                elif order == 'retire_first':
                    def staged_retire():
                        with transaction.atomic():
                            count = crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=original.pk),
                                                                        reason='privacy_request')
                            staged.set()
                            if not release.wait(10):
                                raise AssertionError('Retirement was not released')
                        return count
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        first = executor.submit(self.worker, staged_retire, first_meta)
                        try:
                            self.assertTrue(staged.wait(5))
                            second = executor.submit(self.worker, lambda: self.upload(site, company, content), second_meta)
                            self.assertTrue(second_meta['ready'].wait(5))
                            self.assertTrue(self.wait_for_lock(second_meta['pid']), 'Uploader did not wait for retirement commit')
                        finally:
                            release.set()
                        self.assertEqual(first.result(timeout=15), 1)
                        duplicate = second.result(timeout=15)
                    self.assertNotEqual(duplicate.storage_path, original.storage_path)
                    original.refresh_from_db()
                    # A competing SELECT FOR UPDATE can still own the old row
                    # during on_commit cleanup. skip_locked deliberately leaves
                    # durable intent; immediate unlink is not the contract.
                    if original_path.exists():
                        self.assertEqual(original.purge_storage_path, original_path.relative_to(folder).as_posix())
                        self.assertEqual(crm_files.process_private_attachment_cleanup(site_id=site.pk)['removed'], 1)
                        original.refresh_from_db()
                    self.assertEqual(original.purge_storage_path, '')
                    self.assertFalse(original_path.exists())
                    self.assertEqual((Path(folder) / duplicate.storage_path).read_bytes(), content)
                else:
                    unlink = Path.unlink
                    def denied(path, *args, **kwargs):
                        if path == original_path:
                            raise PermissionError('Synthetic initial cleanup denial')
                        return unlink(path, *args, **kwargs)
                    with patch.object(Path, 'unlink', new=denied):
                        crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=original.pk),
                                                            reason='privacy_request')
                    def staged_unlink(path, *args, **kwargs):
                        if path == original_path:
                            staged.set()
                            if not release.wait(10):
                                raise AssertionError('Cleanup was not released')
                        return unlink(path, *args, **kwargs)
                    with patch.object(Path, 'unlink', new=staged_unlink), ThreadPoolExecutor(max_workers=2) as executor:
                        first = executor.submit(self.worker, lambda: crm_files.process_private_attachment_cleanup(
                            site_id=site.pk), first_meta)
                        try:
                            self.assertTrue(staged.wait(5))
                            second = executor.submit(self.worker, lambda: crm_files.process_private_attachment_cleanup(
                                site_id=site.pk), second_meta)
                            self.assertEqual(second.result(timeout=5)['skipped'], 1)
                            self.assertTrue(original_path.exists())
                        finally:
                            release.set()
                        self.assertEqual(first.result(timeout=15)['removed'], 1)
                    original.refresh_from_db()
                    self.assertEqual(original.purge_storage_path, '')
                    self.assertFalse(original_path.exists())
                self.assertNotEqual(first_meta['pid'], second_meta['pid'])
                proofs.append({'order': order, 'independent_pids': [first_meta['pid'], second_meta['pid']]})
        print('REAL_ATTACHMENT_INTERLEAVINGS ' + json.dumps(proofs))
