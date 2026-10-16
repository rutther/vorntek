"""System-admin recovery acts only on previously retired private attachments."""
from pathlib import Path
import tempfile
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings

from console import crm_files
from console.access import GROUP_BY_ROLE, ROLE_SALES, ROLE_SYSTEM_ADMIN
from console.models import AuditLog
from leads.models import Company, CrmAttachment
from sitecore.models import Site, SiteLocale


@skipUnless(connection.vendor == 'postgresql', 'Requires full-SQL physical recovery HTTP')
@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class AttachmentCleanupHttpTests(TestCase):
    page = '/admin/system/data-governance/'

    def setUp(self):
        self.site = Site.objects.create(code='siteos_demo', name='Synthetic cleanup HTTP', default_locale='en')
        SiteLocale.objects.create(site=self.site, locale_code='en', label='English', enabled=True, is_default=True)
        self.admin = get_user_model().objects.create_user(username='cleanup-admin', password='synthetic-only')
        self.group = Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])[0]
        self.admin.groups.add(self.group)
        self.client = Client(enforce_csrf_checks=True, raise_request_exception=False)
        self.client.force_login(self.admin)
        directory = tempfile.TemporaryDirectory(prefix='vorntek-cleanup-http-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        root_patch = patch('console.crm_files.attachment_storage_root', return_value=self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.company = Company.objects.create(site=self.site, name='Synthetic', normalized_name='synthetic')
        self.row = crm_files.store_crm_attachment(site=self.site,
            uploaded_file=SimpleUploadedFile('private.txt', b'Synthetic retired file'), title='Synthetic',
            uploaded_by=self.admin, target={'company': self.company})
        self.private_path = self.row.storage_path
        self.path = self.root / self.private_path
        with self.captureOnCommitCallbacks(execute=False):
            crm_files.retire_private_attachments(CrmAttachment.objects.filter(pk=self.row.pk), reason='privacy_request')
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        self.csrf = self.client.cookies['csrftoken'].value

    def post(self, data):
        return self.client.post(self.page, data, HTTP_X_CSRFTOKEN=self.csrf)

    def test_get_shows_pending_physical_state_without_deleting_or_exposing_paths(self):
        response = self.client.get(self.page)
        self.assertContains(response, '附件物理清理')
        summary = response.context['data_governance'].get('file_cleanup', {})
        self.assertEqual(summary.get('pending_retired'), 1)
        self.assertTrue(self.path.exists())
        self.assertNotContains(response, self.private_path)
        self.assertNotContains(response, str(self.root))

    def test_confirmed_retry_clears_retired_file_and_preserves_active_attachment(self):
        active = crm_files.store_crm_attachment(site=self.site,
            uploaded_file=SimpleUploadedFile('active.txt', b'Synthetic active file'), title='Active',
            uploaded_by=self.admin, target={'company': self.company})
        active_path = self.root / active.storage_path
        response = self.post({'_action': 'retry_attachment_cleanup', 'confirmed': 'on'})
        self.assertEqual(response.status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.purge_storage_path, '')
        self.assertFalse(self.path.exists())
        self.assertTrue(active_path.exists())
        audit = AuditLog.objects.filter(action='private_attachment_cleanup_requested').get()
        self.assertEqual(audit.actor_user_id, self.admin.pk)
        self.assertNotIn(self.private_path, str(audit.metadata_json))
        self.assertEqual(self.client.get(self.page).context['data_governance']['file_cleanup']['pending_retired'], 0)

    def test_missing_confirmation_and_csrf_cannot_delete_retired_file(self):
        response = self.post({'_action': 'retry_attachment_cleanup'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '请确认仅重试')
        self.assertTrue(self.path.exists())
        response = self.client.post(self.page, {'_action': 'retry_attachment_cleanup', 'confirmed': 'on'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.path.exists())

    def test_filesystem_failure_remains_pending_with_actionable_safe_feedback(self):
        unlink = Path.unlink
        def deny(path, *args, **kwargs):
            if path == self.path:
                raise PermissionError('Private host/path must not leak')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', new=deny):
            response = self.post({'_action': 'retry_attachment_cleanup', 'confirmed': 'on'})
        self.assertEqual(response.status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.purge_error_code, 'filesystem')
        response = self.client.get(self.page)
        self.assertContains(response, '文件系统访问失败')
        self.assertNotContains(response, 'Private host/path')
        self.assertTrue(self.path.exists())

    def test_revoked_admin_role_cannot_retry_using_previous_page(self):
        self.admin.groups.remove(self.group)
        response = self.post({'_action': 'retry_attachment_cleanup', 'confirmed': 'on'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.path.exists())
        self.assertFalse(AuditLog.objects.filter(action='private_attachment_cleanup_requested').exists())

    def test_sales_role_cannot_read_or_retry_cleanup(self):
        sales = get_user_model().objects.create_user(username='cleanup-sales', password='synthetic-only')
        sales.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SALES])[0])
        self.client.force_login(sales)
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.assertEqual(self.post({'_action': 'retry_attachment_cleanup', 'confirmed': 'on'}).status_code, 403)
        self.assertTrue(self.path.exists())
