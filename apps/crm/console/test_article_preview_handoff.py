"""Normal preview entry and persisted reuse must hand off the correct workflow."""
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.urls import reverse

from console.access import GROUP_BY_ROLE, ROLE_CONTENT_OPS
from console.article_preview import build_private_article_preview
from console.content_access import ASSETS_READ, CONTENT_READ, RELEASES_READ, RELEASES_PREVIEW_BUILD
from console.models import ContentAccessGrant
from sitecore.models import Article, PageRoute, Release, SiteLocale, MediaAsset
from .test_file_lifecycle_contract import FileLifecycleFixture


@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)
class ArticlePreviewHandoffTests(FileLifecycleFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.locale = SiteLocale.objects.create(site=self.site, locale_code='en', label='English',
                                              enabled=True, is_default=True)
        route = PageRoute.objects.create(site=self.site, locale=self.locale,
            path='/en/articles/synthetic-guide/', route_type='article', status='published')
        self.article = Article.objects.create(route=route, title='Synthetic guide', slug='synthetic-guide',
            body_markdown='SYNTHETIC FIRST', status='published', config_json={'contentKey':'syntheticGuide'})
        actor = get_user_model().objects.create_superuser('preview-handoff', 'test@example.invalid', 'synthetic-only')
        self.client.force_login(actor)
        scope = patch('console.views._resolved_content_site_locale', return_value=(self.site, self.locale))
        scope.start(); self.addCleanup(scope.stop)
        configured = override_settings(SITEOS_ARTICLE_PREVIEW_ROOT=self.root/'previews',
                                       SITEOS_ARTICLE_PUBLIC_ORIGIN='https://example.invalid')
        configured.enable(); self.addCleanup(configured.disable)

    def test_release_entry_opens_explicit_preview_workflow_without_writing(self):
        before = Release.objects.count()
        response = self.client.get(reverse('console:releases'))
        action = next(item for item in response.context['page_payload']['actions']
                      if item['href'] == reverse('console:article_release_preview'))
        self.assertEqual(action['kind'], 'link')
        page = self.client.get(action['href'])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '生成文章预览版本')
        self.assertEqual(Release.objects.count(), before)

    @override_settings(SITEOS_PREVIEW_WORKSPACE_ROOT='')
    def test_primary_entry_builds_real_preview_without_a_legacy_astro_workspace(self):
        # A regression to the legacy POST makes the normal install unusable.
        with patch.dict(os.environ, {'SITEOS_PREVIEW_WORKSPACE_ROOT': ''}):
            response = self.client.get(reverse('console:releases'))
            action = next(item for item in response.context['page_payload']['actions']
                          if item['tone'] == 'primary')
            self.assertEqual(action['kind'], 'link')
            self.assertEqual(action['href'], reverse('console:article_release_preview'))
            page = self.client.get(action['href'])
            self.assertEqual(page.status_code, 200)
            built = self.client.post(action['href'], {'workspace': '1'})
        self.assertEqual(built.status_code, 200)
        record_id = built.context['article_preview_result']['recordId']
        record = Release.objects.get(pk=record_id, site=self.site)
        self.assertEqual(record.status, 'built')
        self.assertEqual(record.snapshot_manifest['scope'], 'sitePublished')
        self.assertContains(built, reverse('console:website_candidate_build', args=[record_id]))
        self.assertFalse(Release.objects.filter(snapshot_manifest__kind='websiteCandidate').exists())
        self.article.refresh_from_db()
        self.assertEqual(self.article.status, 'published')

    @override_settings(SITEOS_PREVIEW_WORKSPACE_ROOT='')
    def test_three_d_entries_open_the_same_workflow_without_a_dead_build_post(self):
        asset = MediaAsset.objects.create(site=self.site, asset_type='model3d', status='active',
            title='Synthetic geometry', original_name='synthetic.glb', file_ext='.glb',
            storage_path='storage/assets/models/2026/10/1234567890abcdef-synthetic.glb',
            public_path='/assets/models/2026/10/1234567890abcdef-synthetic.glb',
            file_size_bytes=12, sha256='a' * 64)
        before = Release.objects.count()
        with patch.dict(os.environ, {'SITEOS_PREVIEW_WORKSPACE_ROOT': ''}):
            for route, args in [('assets_three_d', ()), ('three_d_asset_detail', (asset.pk,))]:
                with self.subTest(route=route):
                    page = self.client.get(reverse('console:' + route, args=args))
                    self.assertEqual(page.status_code, 200)
                    self.assertContains(page, 'href="' + reverse('console:article_release_preview') + '"')
                    self.assertNotContains(page, 'action="' + reverse('console:release_build_preview') + '"')
        self.assertEqual(Release.objects.count(), before)

    @override_settings(SITEOS_PREVIEW_WORKSPACE_ROOT='')
    def test_managed_entry_does_not_infer_content_read_from_executor_grant(self):
        actor = get_user_model().objects.create_user('asset-executor', password='synthetic-only')
        group, _ = Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_CONTENT_OPS])
        actor.groups.add(group)
        for capability in (ASSETS_READ, RELEASES_PREVIEW_BUILD):
            ContentAccessGrant.objects.create(user=actor, site=self.site, capability=capability, enabled=True)
        self.client.force_login(actor)
        with patch.dict(os.environ, {'SITEOS_PREVIEW_WORKSPACE_ROOT': ''}):
            page = self.client.get(reverse('console:assets_three_d'))
            self.assertNotContains(page, 'href="' + reverse('console:article_release_preview') + '"')
            self.assertEqual(self.client.get(reverse('console:article_release_preview')).status_code, 403)

    @override_settings(SITEOS_PREVIEW_WORKSPACE_ROOT='C:/explicit-custom-source')
    def test_explicit_custom_frontend_keeps_its_distinct_post_executor(self):
        page = self.client.get(reverse('console:releases'))
        actions = page.context['page_payload']['actions']
        managed = next(item for item in actions if item['tone'] == 'primary')
        custom = next(item for item in actions if item['href'] == reverse('console:release_build_preview'))
        self.assertEqual(managed['kind'], 'link')
        self.assertEqual((custom['kind'], custom['tone']), ('post', 'secondary'))

    def test_reused_preview_offers_its_exact_candidate_action_not_the_newest_record(self):
        first = build_private_article_preview(site=self.site)
        self.article.body_markdown = 'SYNTHETIC LATER'
        self.article.save(update_fields=['body_markdown'])
        newer = build_private_article_preview(site=self.site)
        self.article.body_markdown = 'SYNTHETIC FIRST'
        self.article.save(update_fields=['body_markdown'])
        response = self.client.post(reverse('console:article_release_preview'), {'workspace':'1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['article_preview_result']['recordId'], first['recordId'])
        self.assertNotEqual(first['recordId'], newer['recordId'])
        self.assertContains(response, 'action="' + reverse('console:website_candidate_build', args=[first['recordId']]) + '"')
        self.assertNotContains(response, 'action="' + reverse('console:website_candidate_build', args=[newer['recordId']]) + '"')
        self.assertEqual(Release.objects.filter(site=self.site, status='built').count(), 2)

    def test_preview_only_operator_does_not_receive_candidate_mutation_control(self):
        actor = get_user_model().objects.create_user('preview-only', password='synthetic-only')
        group, _ = Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_CONTENT_OPS])
        actor.groups.add(group)
        for capability in (CONTENT_READ, RELEASES_READ, RELEASES_PREVIEW_BUILD):
            ContentAccessGrant.objects.create(user=actor, site=self.site, capability=capability, enabled=True)
        self.client.force_login(actor)
        response = self.client.post(reverse('console:article_release_preview'), {'workspace':'1'})
        self.assertEqual(response.status_code, 200)
        result = response.context['article_preview_result']
        self.assertNotContains(response, 'action="' + reverse('console:website_candidate_build', args=[result['recordId']]) + '"')

    def test_single_draft_preview_does_not_offer_whole_site_candidate_action(self):
        self.article.status = 'draft'; self.article.save(update_fields=['status'])
        response = self.client.post(reverse('console:article_release_preview'),
                                    {'workspace':'1', 'article':str(self.article.pk)})
        self.assertEqual(response.status_code, 200)
        result = response.context['article_preview_result']
        self.assertNotContains(response, 'action="' + reverse('console:website_candidate_build', args=[result['recordId']]) + '"')

    def test_incomplete_translations_keep_the_candidate_action_unavailable(self):
        SiteLocale.objects.create(site=self.site, locale_code='zh', label='Chinese', enabled=True)
        response = self.client.post(reverse('console:article_release_preview'), {'workspace':'1'})
        self.assertEqual(response.status_code, 200)
        result = response.context['article_preview_result']
        self.assertEqual(result['missingTranslations'], [{'contentKey':'syntheticGuide', 'languages':['zh']}])
        self.assertNotContains(response, 'action="' + reverse('console:website_candidate_build', args=[result['recordId']]) + '"')
