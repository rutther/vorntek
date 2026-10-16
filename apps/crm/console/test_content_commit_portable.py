"""Real persisted content boundaries; independent PG concurrency is separate."""
from datetime import timedelta
import io
from unittest.mock import patch
from uuid import uuid4
import zipfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console.access import GROUP_BY_ROLE, ROLE_CONTENT_OPS
from console.article_imports import ArticleImportZipForm
from console.article_delivery import ArticleDeliveryError
from console.article_preview import build_private_article_preview, article_preview_store
from console.category_forms import CategoryEditorForm
from console.content_access import CONTENT_READ, CONTENT_WRITE
from console.forms import ArticleEditorForm
from console.models import ContentAccessGrant
from console.marketing_services import execute_privacy_action
from leads.models import LeadFormDefinition, LeadSubmission, PrivacyRequest
from sitecore.models import Article, Category, PageRoute, SiteLocale
from .test_file_lifecycle_contract import FileLifecycleFixture


class ContentCommitPortableTests(FileLifecycleFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.locale = SiteLocale.objects.create(site=self.site, locale_code='en', label='English', enabled=True, is_default=True)

    def payload(self, **overrides):
        return {'title':'Synthetic guide', 'slug':'synthetic-guide', 'status':'draft', 'reading_minutes':3,
            'body_markdown':'SYNTHETIC ORIGINAL', 'content_key':'syntheticGuide', **overrides}

    def article(self):
        route = PageRoute.objects.create(site=self.site, locale=self.locale, path='/en/articles/synthetic-guide/', route_type='article', status='draft')
        return Article.objects.create(route=route, title='Synthetic original', slug='synthetic-guide',
            body_markdown='SYNTHETIC ORIGINAL', status='draft', config_json={'contentKey':'syntheticGuide'})

    def editor(self, data, article=None, allow_publish=True):
        return ArticleEditorForm(data, site=self.site, locale=self.locale, article=article, allow_publish=allow_publish)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
    def test_article_picker_does_not_offer_categories_the_form_will_reject(self):
        actor=get_user_model().objects.create_superuser('category-picker','synthetic@example.invalid','synthetic-only')
        published=Category.objects.create(site=self.site,locale=self.locale,code='allowed',slug='allowed',name='Allowed',status='published')
        draft=Category.objects.create(site=self.site,locale=self.locale,code='draft',slug='draft',name='Draft',status='draft')
        Category.objects.create(site=self.site,locale=self.locale,code='old',slug='old',name='Archived',status='archived')
        child=Category.objects.create(site=self.site,locale=self.locale,parent=draft,code='allowed-child',slug='allowed-child',name='Allowed child',status='published')
        other_locale=SiteLocale.objects.create(site=self.site,locale_code='zh',label='Chinese',enabled=True)
        Category.objects.create(site=self.site,locale=other_locale,code='foreign',slug='foreign',name='Foreign locale',status='published')
        row=self.article()
        client=Client();client.force_login(actor)
        def ids(nodes):
            return {value for node in nodes for value in ({node['id']} | ids(node['children']))}
        with patch('console.views._resolved_content_site_locale',return_value=(self.site,self.locale)):
            for url in (reverse('console:article_create'),reverse('console:article_edit',args=[row.pk])):
                with self.subTest(url=url):
                    response=client.get(url)
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(ids(response.context['category_tree']),{published.pk,child.pk})

    def test_existing_draft_category_binding_survives_an_ordinary_body_edit(self):
        category=Category.objects.create(site=self.site,locale=self.locale,code='existing',slug='existing',name='Existing draft',status='draft')
        row=self.article();row.category=category;row.save(update_fields=['category'])
        form=self.editor(self.payload(category=str(category.pk),body_markdown='UPDATED SYNTHETIC BODY'),row)
        self.assertTrue(form.is_valid(),form.errors)
        saved=form.save()
        self.assertEqual((saved.category_id,saved.body_markdown),(category.pk,'UPDATED SYNTHETIC BODY'))

    def test_new_article_cannot_select_an_unpublished_category(self):
        category=Category.objects.create(site=self.site,locale=self.locale,code='unavailable',slug='unavailable',name='Unpublished',status='draft')
        form=self.editor(self.payload(category=str(category.pk)))
        self.assertFalse(form.is_valid())
        self.assertIn('category',form.errors)

    def packed(self, entries, **options):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            for name, body in entries:
                archive.writestr(name, body)
        return ArticleImportZipForm({'default_status':'draft', **options},
            {'import_file':SimpleUploadedFile('synthetic.zip', data.getvalue())},
            site=self.site, locale=self.locale, allow_publish=True)

    def test_stale_article_cannot_undo_an_intervening_publication(self):
        row = self.article()
        form = self.editor(self.payload(body_markdown='STALE BODY'), row, allow_publish=False)
        self.assertTrue(form.is_valid(), form.errors)
        Article.objects.filter(pk=row.pk).update(status='published', body_markdown='FINAL PUBLISHED', updated_at=timezone.now()+timedelta(seconds=1))
        PageRoute.objects.filter(pk=row.route_id).update(status='published', updated_at=timezone.now()+timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            form.save()
        row.refresh_from_db()
        self.assertEqual((row.status, row.body_markdown, row.route.status), ('published', 'FINAL PUBLISHED', 'published'))

    def test_publisher_cannot_overwrite_a_newer_draft(self):
        row = self.article()
        form = self.editor(self.payload(body_markdown='STALE BODY'), row)
        self.assertTrue(form.is_valid(), form.errors)
        Article.objects.filter(pk=row.pk).update(body_markdown='NEWER BODY', updated_at=timezone.now()+timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            form.save()
        row.refresh_from_db()
        self.assertEqual(row.body_markdown, 'NEWER BODY')

    def test_stale_category_cannot_undo_an_intervening_publication(self):
        row = Category.objects.create(site=self.site, locale=self.locale, code='guide', slug='guide', name='Synthetic original', status='draft')
        form = CategoryEditorForm({'name':'STALE NAME', 'code':'guide', 'slug':'guide', 'status':'draft', 'sort_order':0},
            site=self.site, locale=self.locale, category=row, allow_publish=False)
        self.assertTrue(form.is_valid(), form.errors)
        Category.objects.filter(pk=row.pk).update(name='Published name', status='published', updated_at=timezone.now()+timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            form.save()
        row.refresh_from_db()
        self.assertEqual((row.name, row.status), ('Published name', 'published'))

    def test_normal_edit_keeps_public_hyphen_slug_and_immutable_language_identity(self):
        row = self.article()
        form = self.editor(self.payload(content_key='attemptedReplacement', body_markdown='Updated body'), row)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual((saved.slug, saved.route.path), ('synthetic-guide', '/en/articles/synthetic-guide/'))
        self.assertEqual(saved.config_json['contentKey'], 'syntheticGuide')

    def test_two_validated_new_forms_cannot_allocate_one_language_identity_twice(self):
        first = self.editor(self.payload(slug='first-guide'))
        second = self.editor(self.payload(slug='second-guide'))
        self.assertTrue(first.is_valid(), first.errors)
        self.assertTrue(second.is_valid(), second.errors)
        first.save()
        with self.assertRaises(ValidationError):
            second.save()
        self.assertEqual(Article.objects.filter(route__site=self.site).count(), 1)
        self.assertEqual(PageRoute.objects.filter(site=self.site, route_type='article').count(), 1)

    def test_publication_requires_a_language_identity_but_drafts_do_not(self):
        self.assertTrue(self.editor(self.payload(content_key='')).is_valid())
        form = self.editor(self.payload(content_key='', status='published'))
        self.assertFalse(form.is_valid())
        self.assertIn('content_key', form.errors)

    def test_zip_preview_performs_validation_without_writing_rows(self):
        form = self.packed([('guide.md', '---\ntitle: Preview guide\nslug: preview-guide\ncontent_key: previewGuide\n---\nSynthetic body')], dry_run='on')
        self.assertTrue(form.is_valid(), form.errors)
        result = form.save()
        self.assertEqual((result.get('planned'), result['created'], result['updated']), (1, 0, 0))
        self.assertFalse(Article.objects.filter(route__site=self.site).exists())

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
    def test_stale_category_http_keeps_input_and_reports_conflict_without_500(self):
        actor = get_user_model().objects.create_superuser('category-http', 'synthetic@example.invalid', 'synthetic-only')
        row = Category.objects.create(site=self.site, locale=self.locale, code='example', slug='example', name='Original category', status='draft')
        client = Client(raise_request_exception=False)
        client.force_login(actor)
        save = CategoryEditorForm.save
        def save_after_publication(form):
            Category.objects.filter(pk=row.pk).update(name='Published category', status='published', updated_at=timezone.now()+timedelta(seconds=1))
            return save(form)
        with patch('console.views._resolved_content_site_locale', return_value=(self.site,self.locale)), \
                patch.object(CategoryEditorForm, 'save', save_after_publication):
            response = client.post(reverse('console:category_edit', args=[row.pk]),
                {'name':'Incoming category', 'code':'example', 'slug':'example', 'status':'draft', 'sort_order':0})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '刷新')
        self.assertContains(response, 'Incoming category')
        row.refresh_from_db()
        self.assertEqual((row.name,row.status), ('Published category','published'))

    def test_zip_default_preserves_existing_article_without_explicit_overwrite(self):
        row = self.article()
        form = self.packed([('guide.md', '---\ntitle: Incoming guide\nslug: synthetic-guide\ncontent_key: syntheticGuide\n---\nDO NOT OVERWRITE')])
        self.assertTrue(form.is_valid(), form.errors)
        result = form.save()
        row.refresh_from_db()
        self.assertEqual((result['updated'], result['skipped'], row.body_markdown), (0, 1, 'SYNTHETIC ORIGINAL'))

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
    def test_editor_preview_link_opens_a_readable_control_page_not_a_post_only_action(self):
        actor = get_user_model().objects.create_superuser('preview-link', 'synthetic@example.invalid', 'synthetic-only')
        row = self.article()
        client = Client()
        client.force_login(actor)
        with patch('console.views._resolved_content_site_locale', return_value=(self.site,self.locale)):
            response = client.get(reverse('console:article_edit', args=[row.pk]))
            self.assertEqual(response.status_code, 200)
            target = response.context['preview_url']
            self.assertTrue(target)
            self.assertEqual(client.get(target).status_code, 200)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
    def test_zip_preview_message_distinguishes_validation_from_actual_import(self):
        actor = get_user_model().objects.create_superuser('zip-preview', 'synthetic@example.invalid', 'synthetic-only')
        client = Client()
        client.force_login(actor)
        form = self.packed([('preview.md','---\ntitle: Synthetic preview\nslug: preview-only\ncontent_key: previewOnly\n---\nSynthetic body')])
        with patch('console.views._resolved_content_site_locale', return_value=(self.site,self.locale)):
            response = client.post(reverse('console:article_import'),
                {'default_status':'draft','dry_run':'on','import_file':form.files['import_file']})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('预演完成' in str(message) for message in response.context['messages']))
        self.assertFalse(Article.objects.filter(route__site=self.site).exists())

    def test_saved_draft_has_a_private_single_article_preview_without_publication(self):
        row = self.article()
        row.config_json = {}
        row.save(update_fields=['config_json'])
        with override_settings(SITEOS_ARTICLE_PREVIEW_ROOT=self.root/'previews', SITEOS_ARTICLE_PUBLIC_ORIGIN='https://example.invalid'):
            try:
                result = build_private_article_preview(site=self.site, article=row, actor='synthetic')
            except TypeError:
                self.fail('Selected draft preview is not implemented')
            self.assertEqual((result['scope'], result['articleCount']), ('singleArticle', 1))
            self.assertEqual(article_preview_store(self.site, initialize=False).current(), '')
            self.assertTrue(any(name.endswith('synthetic-guide/index.html') for name in result['files']))
        row.refresh_from_db()
        self.assertEqual((row.status, row.route.status, row.config_json), ('draft', 'draft', {}))
        from console.website_candidate import _reviewed_preview
        with self.assertRaisesRegex(ArticleDeliveryError, 'reviewed_article_preview_missing'):
            _reviewed_preview(site=self.site, record_id=result['recordId'])

    def test_privacy_execution_keeps_the_operator_note_and_system_summary(self):
        actor = get_user_model().objects.create_superuser('privacy-note', 'synthetic@example.invalid', 'synthetic-only')
        form = LeadFormDefinition.objects.create(site=self.site, code='privacy-note', name='Synthetic only', capi_enabled=False)
        lead = LeadSubmission.objects.create(site=self.site, form=form, full_name='Synthetic subject', email='synthetic@example.invalid',
            submitted_at=timezone.now(), stage_updated_at=timezone.now(), consent_json={'marketing':True})
        item = PrivacyRequest.objects.create(site=self.site, submission=lead, request_type='withdraw_consent', status='processing',
            requester_name='Synthetic subject', verified_at=timezone.now())
        try:
            result = execute_privacy_action(privacy_request=item, actor=actor, operator_note='Synthetic verified instruction')
        except TypeError:
            self.fail('Privacy execution discarded the operator note contract')
        item.refresh_from_db()
        lead.refresh_from_db()
        self.assertEqual(item.status, 'completed')
        self.assertEqual(item.request_json['execution_note'], 'Synthetic verified instruction')
        self.assertIn('Synthetic verified instruction', item.resolution)
        self.assertIn(item.request_json['execution_summary'], result['resolution'])
        self.assertFalse(lead.consent_json['marketing'])

    def test_zip_rejects_wrong_locale_and_duplicate_identity_before_any_write(self):
        wrong = self.packed([('wrong.md', '---\ntitle: Wrong language\nlocale: zh\nslug: wrong-language\ncontent_key: wrongLanguage\n---\nSynthetic')])
        self.assertTrue(wrong.is_valid(), wrong.errors)
        self.assertEqual(wrong.save()['failed'], 1)
        duplicate = self.packed([(f'{i}.md', f'---\ntitle: Example {i}\nslug: example-{i}\ncontent_key: duplicateIdentity\n---\nSynthetic') for i in (1, 2)])
        self.assertFalse(duplicate.is_valid())
        self.assertFalse(Article.objects.filter(route__site=self.site).exists())

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
    def test_article_http_rechecks_revoked_roles_sessions_and_grants_after_validation(self):
        for mode in ('role', 'disabled', 'session', 'password', 'write_grant'):
            token = uuid4().hex[:12]
            actor = get_user_model().objects.create_user(username='content-'+token, password='synthetic-only')
            actor.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_CONTENT_OPS])[0])
            grants = {key:ContentAccessGrant.objects.create(user=actor, site=self.site, locale=self.locale, capability=key)
                for key in (CONTENT_READ, CONTENT_WRITE)}
            client = Client(raise_request_exception=False)
            client.force_login(actor)
            session_key = client.session.session_key
            original_valid = ArticleEditorForm.is_valid
            def valid_then_revoke(form):
                valid = original_valid(form)
                if valid:
                    if mode == 'role': actor.groups.clear()
                    elif mode == 'disabled': get_user_model().objects.filter(pk=actor.pk).update(is_active=False)
                    elif mode == 'session':
                        from django.contrib.sessions.models import Session
                        Session.objects.filter(session_key=session_key).delete()
                    elif mode == 'password':
                        actor.set_password('synthetic-updated'); actor.save(update_fields=['password'])
                    else: ContentAccessGrant.objects.filter(pk=grants[CONTENT_WRITE].pk).update(enabled=False)
                return valid
            with self.subTest(mode=mode), patch('console.views._resolved_content_site_locale', return_value=(self.site,self.locale)), \
                    patch.object(ArticleEditorForm, 'is_valid', valid_then_revoke):
                response = client.post(reverse('console:article_create'), self.payload(slug='guide-'+token, content_key='guide'+token))
                self.assertEqual(response.status_code, 403)
                self.assertFalse(Article.objects.filter(route__site=self.site).exists())
