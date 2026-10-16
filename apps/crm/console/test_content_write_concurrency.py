"""Real read/write-only requests cannot undo a concurrent authorized publish."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import threading
from unittest import skipUnless
from unittest.mock import patch
import zipfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from console.access import GROUP_BY_ROLE, ROLE_CONTENT_OPS, ROLE_SYSTEM_ADMIN
from console.category_forms import CategoryEditorForm
from console.content_access import ASSETS_READ, CONTENT_READ, CONTENT_WRITE, CONTENT_SET_PUBLISHED, effective_content_capabilities
from console.forms import ArticleEditorForm
from console.models import ContentAccessGrant
from sitecore.models import Article, Category, MediaAsset, MediaAssetBinding, Site, SiteLocale


@skipUnless(connection.vendor == 'postgresql', 'Requires committed content writes and independent publishing connection')
@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ContentWriteConcurrencyTests(TransactionTestCase):
    def _fixture_teardown(self):
        # The test owns a fresh full-SQL database, destroyed by its driver.
        return None

    def test_native_zip_and_category_requests_cannot_overwrite_a_concurrent_publication(self):
        # Other real-connection tests retain their committed synthetic fixtures
        # until this owned database is destroyed. Reuse the canonical site and
        # locale instead of depending on module execution order or deleting it.
        site, _ = Site.objects.get_or_create(code='siteos_demo',
            defaults={'name': 'Synthetic content race', 'default_locale': 'en'})
        locale, _ = SiteLocale.objects.get_or_create(site=site, locale_code='en',
            defaults={'label': 'English', 'enabled': True, 'is_default': True})
        admin = get_user_model().objects.create_user(username='content-publisher', password='synthetic-only')
        admin.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])[0])
        editor = get_user_model().objects.create_user(username='content-draft-editor', password='synthetic-only')
        editor.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_CONTENT_OPS])[0])
        for capability in (CONTENT_READ, CONTENT_WRITE):
            ContentAccessGrant.objects.create(user=editor, site=site, locale=locale, capability=capability,
                granted_by_user=admin, reason='Synthetic race')
        ContentAccessGrant.objects.create(user=editor, site=site, capability=ASSETS_READ,
            granted_by_user=admin, reason='Synthetic cover selection')
        self.assertNotIn(CONTENT_SET_PUBLISHED, effective_content_capabilities(editor, site=site, locale=locale))
        publisher, reader = Client(raise_request_exception=False), Client(raise_request_exception=False)
        publisher.force_login(admin)
        reader.force_login(editor)
        available_category = Category.objects.create(site=site, locale=locale, code='available', slug='available',
            name='Synthetic available', status='published')
        asset = MediaAsset.objects.create(site=site, asset_type='image', title='Synthetic cover',
            original_name='synthetic-cover.jpg', file_ext='.jpg', mime_type='image/jpeg', file_size_bytes=1, sha256='0' * 64,
            public_path='/assets/images/2026/10/0000000000000000-synthetic-cover.jpg',
            storage_path='storage/assets/images/2026/10/0000000000000000-synthetic-cover.jpg')
        proofs = []
        for mode in ('native', 'zip', 'category'):
            with self.subTest(mode=mode):
                slug = 'synthetic' + mode
                if mode == 'category':
                    record = Category.objects.create(site=site, locale=locale, code=slug, slug=slug,
                        name='Synthetic original', status='draft')
                    url = reverse('console:category_edit', args=[record.pk])
                    old_payload = {'name': 'Old reader category', 'code': slug, 'slug': slug, 'status': 'draft', 'sort_order': 0}
                    published_payload = {**old_payload, 'name': 'Published category', 'status': 'published'}
                    form_class = CategoryEditorForm
                else:
                    initial = {'title': 'Synthetic original', 'slug': slug, 'status': 'draft', 'reading_minutes': 3,
                        'body_markdown': 'Original bytes', 'content_key': mode + 'Race'}
                    form = ArticleEditorForm(initial, site=site, locale=locale, allow_publish=True)
                    self.assertTrue(form.is_valid(), form.errors)
                    record = form.save()
                    url = reverse('console:article_edit', args=[record.pk])
                    old_payload = {**initial, 'title': 'Old reader title', 'body_markdown': 'OLD READER BYTES',
                        'category': available_category.pk, 'cover_asset': asset.pk}
                    published_payload = {**initial, 'title': 'Published by admin', 'body_markdown': 'FINAL PUBLISHED BYTES', 'status': 'published'}
                    form_class = ArticleEditorForm
                    if mode == 'zip':
                        packed = io.BytesIO()
                        with zipfile.ZipFile(packed, 'w') as archive:
                            archive.writestr(slug + '.md', f'---\ntitle: Old reader title\nslug: {slug}\nstatus: draft\n---\nOLD READER BYTES')
                        old_payload = {'import_file': SimpleUploadedFile('synthetic.zip', packed.getvalue()),
                            'default_status': 'draft', 'overwrite_existing': 'on'}
                staged, release = threading.Event(), threading.Event()
                metadata = {}
                original_save = form_class.save

                def held_save(form, *args, **kwargs):
                    if threading.get_ident() == metadata.get('thread') and not form.allow_publish:
                        staged.set()
                        if not release.wait(8):
                            raise RuntimeError('Synthetic content release timed out')
                    return original_save(form, *args, **kwargs)

                def old_request():
                    close_old_connections()
                    try:
                        metadata['thread'] = threading.get_ident()
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()')
                            metadata['pid'] = cursor.fetchone()[0]
                            cursor.execute("SET lock_timeout = '8s'")
                        target = reverse('console:article_import') if mode == 'zip' else url
                        return reader.post(target, old_payload)
                    finally:
                        close_old_connections()

                with patch.object(form_class, 'save', held_save), ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(old_request)
                    try:
                        self.assertTrue(staged.wait(5), 'Old request did not pass initial draft validation')
                        response = publisher.post(url, published_payload)
                        self.assertEqual(response.status_code, 302, response.content[:300])
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()')
                            publisher_pid = cursor.fetchone()[0]
                        record.refresh_from_db()
                        self.assertEqual(record.status, 'published')
                    finally:
                        release.set()
                    old_response = pending.result(timeout=10)
                self.assertNotEqual(metadata['pid'], publisher_pid)
                self.assertEqual(old_response.status_code, 200, old_response.content[:300])
                record.refresh_from_db()
                self.assertEqual(record.status, 'published')
                self.assertContains(old_response, '刷新')
                if mode == 'native':
                    self.assertInHTML(
                        f'<input type="hidden" name="category" id="id_category" value="{available_category.pk}">',
                        old_response.content.decode(),
                    )
                    self.assertContains(old_response, 'OLD READER BYTES')
                if mode == 'category':
                    self.assertEqual(record.name, 'Published category')
                else:
                    self.assertEqual(record.title, 'Published by admin')
                    self.assertEqual(record.body_markdown, 'FINAL PUBLISHED BYTES')
                    self.assertIsNotNone(record.published_at)
                    self.assertEqual(record.route.status, 'published')
                    self.assertIsNone(record.category_id)
                    self.assertEqual(MediaAssetBinding.objects.filter(entity_type='article', entity_id=record.pk).count(), 0)
                proofs.append({'mode': mode, 'pids': [metadata['pid'], publisher_pid], 'response': old_response.status_code})
        print('REAL_CONTENT_PUBLISH_INTERLEAVINGS ' + json.dumps(proofs))
