"""Protected HTTP writes recheck separately committed authority changes."""
import io
import zipfile
from contextlib import nullcontext
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

import psycopg
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from console import marketing_views, views
from console.access import GROUP_BY_ROLE, ROLE_CONTENT_OPS, ROLE_SYSTEM_ADMIN
from console.content_access import ASSETS_READ, CONTENT_READ, CONTENT_WRITE, CONTENT_SET_PUBLISHED
from console.forms import ArticleEditorForm
from console.models import AuditLog, ContentAccessGrant
from console.privacy_forms import PrivacyRequestWorkflowForm
from console.privacy_versions import privacy_request_version_token
from leads.models import LeadFormDefinition, LeadSubmission, PrivacyRequest
from sitecore.models import Article, MediaAsset, Site, SiteLocale


@skipUnless(connection.vendor == 'postgresql', 'Requires separately committed full-SQL authority changes')
@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ContentPrivacyCommitAuthorityTests(TransactionTestCase):
    def _fixture_teardown(self):
        # Unique fixture identities in the owned driver database; no global flush.
        return None

    def exercise(self, kind, mode=None):
        token = uuid4().hex[:12]
        site = Site.objects.create(code='authority_' + token, name='Synthetic authority', default_locale='en')
        locale = SiteLocale.objects.create(site=site, locale_code='en', label='English', enabled=True, is_default=True)
        privacy = kind.startswith('privacy')
        role = ROLE_SYSTEM_ADMIN if privacy else ROLE_CONTENT_OPS
        group = Group.objects.get_or_create(name=GROUP_BY_ROLE[role])[0]
        actor = get_user_model().objects.create_user(username='authority_' + token, password='synthetic-only')
        actor.groups.add(group)
        grants = {}
        if not privacy:
            for capability in (CONTENT_READ, CONTENT_WRITE, CONTENT_SET_PUBLISHED):
                grants[capability] = ContentAccessGrant.objects.create(user=actor, site=site, locale=locale,
                    capability=capability, reason='Synthetic test')
            if mode == 'group_grant':
                grants[CONTENT_WRITE].enabled = False
                grants[CONTENT_WRITE].save(update_fields=['enabled'])
                grants[CONTENT_WRITE] = ContentAccessGrant.objects.create(group=group, site=site, locale=locale,
                    capability=CONTENT_WRITE, reason='Synthetic group test')
        client = Client(enforce_csrf_checks=True, raise_request_exception=False)
        client.force_login(actor)
        client.cookies['csrftoken'] = 'a' * 32
        session_key = client.session.session_key
        db = connection.settings_dict
        self.assertEqual(db['HOST'], 'db', 'Native authority mutations require the isolated DB service')
        self.assertRegex(db['NAME'], r'^acceptance_tests_[1-9][0-9]*$', 'Refuse any non-disposable database')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            request_pid = cursor.fetchone()[0]
        revoked_pids = []

        def revoke():
            with psycopg.connect(host=db['HOST'], port=db['PORT'], user=db['USER'], password=db['PASSWORD'],
                    dbname=db['NAME'], sslmode='disable') as other:
                other.execute("SET lock_timeout='4s'")
                revoked_pids.append(other.execute('SELECT pg_backend_pid()').fetchone()[0])
                if mode == 'role':
                    other.execute('DELETE FROM auth_user_groups WHERE user_id=%s', [actor.pk])
                elif mode == 'disabled':
                    other.execute('UPDATE auth_user SET is_active=false WHERE id=%s', [actor.pk])
                elif mode == 'session':
                    other.execute('DELETE FROM django_session WHERE session_key=%s', [session_key])
                elif mode == 'password':
                    other.execute('UPDATE auth_user SET password=%s WHERE id=%s', [make_password('synthetic-new'), actor.pk])
                else:
                    grant = grants[ASSETS_READ if mode == 'asset_grant' else CONTENT_SET_PUBLISHED if mode == 'publish_grant' else CONTENT_WRITE]
                    other.execute('UPDATE content_access_grant SET enabled=false WHERE id=%s', [grant.pk])

        status = 'published' if mode == 'publish_grant' else 'draft'
        data = {'title':'Synthetic incoming', 'slug':'incoming-' + token, 'body_markdown':'INCOMING BODY',
            'status':status, 'reading_minutes':3, 'content_key':'authority' + token}
        if mode == 'asset_grant':
            grants[ASSETS_READ] = ContentAccessGrant.objects.create(user=actor, site=site, capability=ASSETS_READ,
                reason='Synthetic sitewide asset read')
            ContentAccessGrant.objects.create(user=actor, site=site, locale=locale, capability=ASSETS_READ,
                reason='Synthetic residual locale grant must not replace sitewide authority')
            asset = MediaAsset.objects.create(site=site, asset_type='image', title='Synthetic cover',
                original_name='synthetic-cover.jpg', file_ext='.jpg', mime_type='image/jpeg', file_size_bytes=1, sha256='0'*64,
                public_path='/assets/images/2026/10/0000000000000000-synthetic-cover.jpg',
                storage_path='storage/assets/images/2026/10/0000000000000000-synthetic-cover.jpg')
            data['cover_asset'] = asset.pk
        item = None
        if privacy:
            form = LeadFormDefinition.objects.create(site=site, code='authority_' + token, name='Synthetic only', capi_enabled=False)
            lead = LeadSubmission.objects.create(site=site, form=form, full_name='Synthetic subject', email='synthetic@example.invalid',
                submitted_at=timezone.now(), stage_updated_at=timezone.now(), consent_json={'marketing':True})
            item = PrivacyRequest.objects.create(site=site, submission=lead, request_type='withdraw_consent', status='processing',
                requester_name='Synthetic subject', resolution='Original note', verified_at=timezone.now())
            data = {'confirm':'EXECUTE', 'version':privacy_request_version_token(item), '_action':'execute', 'resolution':'Synthetic note'}
            route = 'privacy_request_execute' if kind == 'privacy_api' else 'privacy_request_manage'
            url = reverse('console:' + route, args=[item.pk])
            form_class = PrivacyRequestWorkflowForm
        elif kind == 'edit':
            initial = ArticleEditorForm({**data, 'title':'Original title', 'body_markdown':'ORIGINAL BODY'},
                site=site, locale=locale, allow_publish=True)
            self.assertTrue(initial.is_valid(), initial.errors)
            item = initial.save()
            url = reverse('console:article_edit', args=[item.pk])
            form_class = ArticleEditorForm
        elif kind == 'import':
            packed = io.BytesIO()
            with zipfile.ZipFile(packed, 'w') as archive:
                for index in range(3):
                    archive.writestr(f'{index}.md', f'---\ntitle: Synthetic {index}\nslug: incoming-{token}-{index}\nstatus: {status}\ncontent_key: authority{token}{index}\n---\nSYNTHETIC BODY')
            data = {'import_file':SimpleUploadedFile('synthetic.zip', packed.getvalue()), 'default_status':status}
            url = reverse('console:article_import')
            form_class = ArticleEditorForm
        else:
            url = reverse('console:article_create')
            form_class = ArticleEditorForm
        original_valid = form_class.is_valid
        original_save = ArticleEditorForm.save
        original_get = marketing_views.get_object_or_404
        first = True

        def validated(form):
            nonlocal first
            valid = original_valid(form)
            if valid and first and mode:
                first = False
                revoke()
            return valid

        def saved(form, *args, **kwargs):
            nonlocal first
            result = original_save(form, *args, **kwargs)
            if first and mode:
                first = False
                transaction.on_commit(revoke)
            return result

        def fetched(*args, **kwargs):
            nonlocal first
            result = original_get(*args, **kwargs)
            if first and mode:
                first = False
                revoke()
            return result

        hook = nullcontext()
        if mode:
            if kind == 'import': hook = patch.object(ArticleEditorForm, 'save', saved)
            elif kind == 'privacy_api': hook = patch.object(marketing_views, 'get_object_or_404', fetched)
            else: hook = patch.object(form_class, 'is_valid', validated)
        with patch.object(views, '_resolved_content_site_locale', return_value=(site, locale)), \
                patch.object(views, 'default_site_locale', return_value=(site, locale)), \
                patch.object(marketing_views, 'default_site_locale', return_value=(site, locale)), hook:
            response = client.post(url, data, HTTP_X_CSRFTOKEN='a' * 32)
        if kind == 'import':
            self.assertEqual(response.status_code, 200, response.content[:700])
            result = response.context['import_result']
            self.assertEqual(result['created'], 1 if mode else 3, result)
            if mode:
                self.assertEqual(result['unexecuted'], 2)
                self.assertEqual(sum(row.status == '未执行' for row in result['entries']), 2)
            self.assertEqual(Article.objects.filter(route__site=site).count(), 1 if mode else 3)
        else:
            self.assertEqual(response.status_code, 403 if mode else 200 if kind == 'privacy_api' else 302, response.content[:700])
            if privacy:
                item.refresh_from_db(); lead.refresh_from_db()
                self.assertEqual(item.status, 'processing' if mode else 'completed')
                self.assertEqual(lead.consent_json['marketing'], bool(mode))
                self.assertEqual(AuditLog.objects.filter(entity_id=item.pk, action='privacy_request_executed').count(), 0 if mode else 1)
            elif kind == 'edit':
                item.refresh_from_db()
                self.assertEqual(item.title, 'Original title' if mode else 'Synthetic incoming')
                self.assertEqual(item.body_markdown, 'ORIGINAL BODY' if mode else 'INCOMING BODY')
            else:
                self.assertEqual(Article.objects.filter(route__site=site).count(), 0 if mode else 1)
        if mode:
            self.assertEqual(len(revoked_pids), 1)
            self.assertNotEqual(revoked_pids[0], request_pid)

    def test_article_create_and_edit_recheck_roles_sessions_and_exact_grants(self):
        for kind in ('create', 'edit'):
            for mode in ('role', 'disabled', 'session', 'password', 'write_grant', 'publish_grant', 'group_grant'):
                with self.subTest(kind=kind, mode=mode): self.exercise(kind, mode)

    def test_zip_import_stops_after_first_commit_when_authority_is_revoked(self):
        for mode in ('role', 'disabled', 'session', 'password', 'write_grant', 'publish_grant', 'group_grant'):
            with self.subTest(mode=mode): self.exercise('import', mode)

    def test_locale_asset_grant_cannot_replace_revoked_sitewide_cover_authority(self):
        for kind in ('create', 'edit'):
            with self.subTest(kind=kind): self.exercise(kind, 'asset_grant')

    def test_privacy_html_and_api_refuse_revoked_actor_before_mutation(self):
        for kind in ('privacy_html', 'privacy_api'):
            for mode in ('role', 'disabled', 'session', 'password'):
                with self.subTest(kind=kind, mode=mode): self.exercise(kind, mode)

    def test_unchanged_authority_keeps_all_five_http_paths_working(self):
        for kind in ('create', 'edit', 'import', 'privacy_html', 'privacy_api'):
            with self.subTest(kind=kind): self.exercise(kind)
