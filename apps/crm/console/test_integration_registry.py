"""Standalone resource management must not infer a wizard's ownership or origin."""
import uuid
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.db import connection
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse

from console import test_flow_workspace as fixtures
from console.access import GROUP_BY_ROLE, ROLE_SYSTEM_ADMIN
from console.flow_workspace_service import create_flow
from leads.models import LeadEventOutbox, LeadInboundEvent
from marketing.models import (MarketingIntegration, MarketingRuntimeBinding,
                              MarketingRuntimeRevision, MarketingRuntimeCommand)


@override_settings(SITEOS_META_RUNTIME_ENABLED=True)
class IntegrationRegistryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.created = []
        existing = set(connection.introspection.table_names())
        with connection.schema_editor() as editor:
            for model in (*fixtures.FlowWorkspaceTests.models, MarketingRuntimeBinding,
                          MarketingRuntimeRevision, MarketingRuntimeCommand,
                          LeadEventOutbox, LeadInboundEvent):
                if model._meta.db_table not in existing:
                    editor.create_model(model)
                    cls.created.append(model)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        with connection.schema_editor() as editor:
            for model in reversed(cls.created):
                editor.delete_model(model)

    def setUp(self):
        fixtures.FlowWorkspaceTests.setUp(self)
        self.list_url = '/admin/marketing/integrations/'
        self.new_url = self.list_url + 'new/'
        self.data = {'name': '网站演练接入', 'public_id': '123456789', 'environment': 'test'}

    def admin(self):
        self.user.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])[0])
        self.client.force_login(self.user)

    def item(self, **overrides):
        return MarketingIntegration.objects.create(site=self.site, provider=self.provider,
            integration_type='pixel', **{**self.data, **overrides})

    def linked_flow(self, item):
        loop = create_flow(site=self.site, actor=self.user, data={
            'name': '网站方案 A', 'environment': item.environment, 'requestId': str(uuid.uuid4())})
        MarketingRuntimeBinding.objects.create(loop=loop, integration=item)
        return loop

    def test_list_is_independent_site_scoped_and_read_only(self):
        item = self.item()
        MarketingIntegration.objects.create(site=self.other, provider=self.provider,
            name='Other site private integration', public_id='987654321', integration_type='pixel')
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, item.name)
        self.assertContains(response, '待保存凭据')
        self.assertNotContains(response, 'Other site private integration')
        self.assertNotContains(response, '返回第07')
        self.assertNotContains(response, 'href="/admin/marketing/integrations/new/"')
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)

    @override_settings(TIME_ZONE='Asia/Shanghai')
    def test_check_timestamp_is_consistent_on_registry_and_detail(self):
        item = self.item(config_json={'last_diagnostic': {
            'tested_at': '2026-10-08T01:00:00Z', 'overall': 'warn', 'checks': []}})
        self.admin()
        for url in (self.list_url, reverse('console:marketing_integration_edit', args=[item.pk])):
            response = self.client.get(url)
            self.assertContains(response, '10-08 09:00 (UTC+08:00)')
            self.assertContains(response, 'datetime="2026-10-08T01:00:00Z"')
            self.assertContains(response, 'title="2026-10-08 09:00 (UTC+08:00)"')

    def test_check_timestamp_does_not_guess_missing_timezone(self):
        from .meta_editor import connection_check_time
        legacy = connection_check_time({'checked_at': '2026-10-08T09:00:00'})
        self.assertEqual(legacy['label'], '10-08 09:00 (时区未记录)')
        for value in ({}, None, {'tested_at': 'unknown'}, {'checked_at': '2026-99-99T99:99:99'}):
            self.assertEqual(connection_check_time(value), {})

    def test_registry_places_site_context_below_the_heading(self):
        response = self.client.get(self.list_url)
        self.assertContains(response, '<h1>平台接入</h1>')
        self.assertContains(response, '当前站点：')
        self.assertNotContains(response, 'ir-eyebrow')

    def test_registry_website_link_uses_public_origin_not_internal_site_address(self):
        self.admin()
        self.site.base_url = 'http://127.0.0.1:25000'
        self.site.save(update_fields=['base_url'])
        with patch('console.marketing_integration_views.PUBLIC_PREVIEW_BASE_URL', 'https://public.example.test', create=True):
            for url in (self.list_url, self.new_url):
                response = self.client.get(url)
                self.assertContains(response, 'href="https://public.example.test/?lang=en"')
                self.assertNotContains(response, 'href="http://127.0.0.1:25000')

    def test_create_is_admin_only_and_defaults_are_safe(self):
        self.assertEqual(self.client.post(self.new_url, self.data).status_code, 403)
        self.admin()
        response = self.client.post(self.new_url, self.data)
        self.assertEqual(response.status_code, 302)
        item = MarketingIntegration.objects.get(public_id=self.data['public_id'])
        self.assertEqual(item.environment, 'test')
        self.assertEqual(item.routing_mode, 'flow_scoped')
        self.assertFalse(item.enabled)
        self.assertFalse(item.secret_ref)
        self.assertEqual(item.consent_category, 'marketing')
        self.assertEqual(MarketingRuntimeBinding.objects.count(), 0)
        self.assertEqual(response['Location'], reverse('console:marketing_integration_edit', args=[item.pk]))

    def test_repeat_create_reuses_asset_without_overwriting_saved_resource(self):
        self.admin()
        original = self.item(name='保留名称', enabled=True, secret_ref='TEST_REFERENCE', routing_mode='legacy_site')
        for _ in range(2):
            self.assertEqual(self.client.post(self.new_url, self.data).status_code, 302)
        original.refresh_from_db()
        self.assertEqual(MarketingIntegration.objects.count(), 1)
        self.assertEqual(original.name, '保留名称')
        self.assertEqual(original.secret_ref, 'TEST_REFERENCE')
        self.assertEqual(original.routing_mode, 'legacy_site')
        self.assertTrue(original.enabled)

    def test_cross_environment_or_site_cannot_reuse_or_leak_names(self):
        self.admin()
        self.item(environment='production', name='Do not expose resource name')
        response = self.client.post(self.new_url, self.data)
        self.assertContains(response, '其他站点或环境')
        self.assertNotContains(response, 'Do not expose resource name')
        self.assertEqual(MarketingIntegration.objects.count(), 1)

    def test_invalid_id_environment_and_injected_fields_do_not_create(self):
        self.admin()
        for override in ({'public_id': 'pending:abc'}, {'environment': 'staging'},
                         {'enabled': 'on'}, {'secret_ref': 'DO_NOT_SAVE'}, {'provider': 'google'}):
            response = self.client.post(self.new_url, {**self.data, **override})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(MarketingIntegration.objects.count(), 0)

    def test_direct_detail_does_not_infer_origin_from_one_association(self):
        item = self.item()
        self.linked_flow(item)
        response = self.client.get(reverse('console:marketing_integration_edit', args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'mi-progress')
        self.assertNotContains(response, '继续此引导')
        self.assertNotContains(response, '第 07')
        self.assertNotContains(response, '第07')
        self.assertContains(response, 'href="/admin/marketing/integrations/"')

    def test_valid_explicit_context_survives_save_but_is_not_shared_between_tabs(self):
        item = self.item()
        loop = self.linked_flow(item)
        url = reverse('console:marketing_integration_edit', args=[item.pk])
        scoped_url = f'{url}?origin=guide&flow={loop.loop_key}'
        self.assertContains(self.client.get(scoped_url), '继续此引导')
        self.assertNotContains(self.client.get(url), '继续此引导')
        response = self.client.post(scoped_url, {'_section': 'identity',
            '_version': item.updated_at.isoformat(), 'name': '改名不改返回位置'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'flow={loop.loop_key}', response['Location'])
        self.assertIn('origin=guide', response['Location'])

    def test_unrelated_context_and_arbitrary_return_url_are_ignored(self):
        item = self.item()
        other = self.item(public_id='987654321')
        loop = self.linked_flow(other)
        url = reverse('console:marketing_integration_edit', args=[item.pk])
        for query in (f'origin=guide&flow={loop.loop_key}', 'origin=guide&flow=bad',
                      f'origin=guide&flow={uuid.uuid4()}&next=https://evil.example/'):
            response = self.client.get(f'{url}?{query}')
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, '继续此引导')
            self.assertNotContains(response, 'evil.example')

    def test_prepare_integration_reuses_the_independently_created_object(self):
        self.admin()
        self.client.post(self.new_url, self.data)
        item = MarketingIntegration.objects.get(public_id=self.data['public_id'])
        loop = create_flow(site=self.site, actor=self.user, data={
            'name': '同资产方案', 'environment': 'test', 'requestId': str(uuid.uuid4())})
        loop.config_json['workspace_v1']['configs']['dataset'] = {'externalId': item.public_id}
        loop.save()
        from marketing.flow_runtime import runtime_command
        runtime_command(loop=loop, actor=self.user, data={'action': 'prepare_integration',
            'requestId': str(uuid.uuid4()), 'expectedVersion': 0})
        self.assertEqual(MarketingRuntimeBinding.objects.get(loop=loop).integration_id, item.pk)
        self.assertEqual(MarketingIntegration.objects.count(), 1)

    def test_ready_direct_detail_offers_resource_navigation_not_a_wizard(self):
        item = self.item(secret_ref='TEST_REGISTRY_TOKEN', config_json={'last_diagnostic': {
            'overall': 'pass', 'checks': [{'code': 'token', 'required': True, 'status': 'pass'},
                                        {'code': 'dataset_access', 'required': False, 'status': 'pass'}]}})
        with patch.dict('os.environ', {'TEST_REGISTRY_TOKEN': 'synthetic-only'}):
            response = self.client.get(reverse('console:marketing_integration_edit', args=[item.pk]))
        self.assertContains(response, 'data-connection-state="ready"')
        self.assertNotContains(response, '回流程')
        self.assertNotContains(response, 'synthetic-only')

    def test_filters_combine_and_pagination_retains_filters(self):
        for index in range(26):
            self.item(name=f'演练 {index}', public_id=f'90000000{index:03d}')
        self.item(name='正式业务记录', public_id='811111111', environment='production')
        response = self.client.get(self.list_url, {'q': '演练', 'environment': 'test'})
        self.assertEqual(response.context['page'].paginator.count, 26)
        self.assertContains(response, '下一页')
        self.assertNotContains(response, '正式业务记录')
        self.assertEqual(len(response.context['rows']), 25)
        last = self.client.get(self.list_url, {'q': '演练', 'environment': 'test', 'page': 2})
        self.assertEqual(len(last.context['rows']), 1)

    def test_unavailable_list_is_not_reported_as_zero_integrations(self):
        with patch('console.marketing_integration_views.MarketingIntegration.objects.filter', side_effect=DatabaseError('synthetic failure')):
            response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, '还没有平台接入', status_code=503)
        self.assertContains(response, '暂时无法读取接入', status_code=503)

    def test_different_site_context_and_changed_environment_are_rejected(self):
        item = self.item()
        loop = self.linked_flow(item)
        url = reverse('console:marketing_integration_edit', args=[item.pk])
        loop.site = self.other
        loop.save()
        self.assertNotContains(self.client.get(f'{url}?origin=guide&flow={loop.loop_key}'), '继续此引导')

    def test_existing_non_meta_uses_specialized_status_not_pixel_assumptions(self):
        from marketing.models import MarketingProvider
        google = MarketingProvider.objects.create(code='google', name='Google', enabled=True)
        MarketingIntegration.objects.create(site=self.site, provider=google,
            integration_type='data_manager', name='Google ADC example', public_id='12345',
            config_json={'auth_mode': 'adc'})
        response = self.client.get(self.list_url)
        self.assertContains(response, '查看专用配置')
        self.assertNotContains(response, '未保存主凭据')

    def test_other_adapters_keep_specialized_fields_and_return_to_registry(self):
        from marketing.models import MarketingProvider
        self.admin()
        cases = [('google', 'data_manager', 'Google Ads Customer ID'),
                 ('meta', 'leadgen', 'Webhook'), ('whatsapp', 'cloud_api', 'Webhook')]
        for platform, kind, field in cases:
            provider, _ = MarketingProvider.objects.get_or_create(code=platform, defaults={'name': platform})
            item = MarketingIntegration.objects.create(site=self.site, provider=provider,
                integration_type=kind, name='Synthetic specialized connection', public_id='adapter-' + kind)
            response = self.client.get(reverse('console:marketing_integration_edit', args=[item.pk]))
            self.assertContains(response, field)
            self.assertContains(response, 'href="/admin/marketing/integrations/">返回接入列表</a>')
            self.assertNotContains(response, 'href="/admin/marketing/">取消并返回</a>')
            self.assertNotContains(response, '继续此引导')

    def test_token_without_diagnostic_is_stored_but_not_checked(self):
        self.item(secret_ref='REGISTRY_FAKE_REFERENCE')
        response = self.client.get(self.list_url)
        self.assertContains(response, '未检查')
        self.assertEqual(response.context['rows'][0]['check_label'], '未检查')
        self.assertNotContains(response, 'REGISTRY_FAKE_REFERENCE')

    def test_diagnostic_redirect_preserves_only_valid_origin(self):
        item = self.item()
        loop = self.linked_flow(item)
        url = reverse('console:marketing_integration_edit', args=[item.pk])
        with patch('console.views.record_integration_diagnostic', return_value=({'overall': 'warn', 'summary': 'Synthetic'}, None)):
            response = self.client.post(f'{url}?origin=guide&flow={loop.loop_key}&next=https://evil.example/', {'_action': 'diagnose'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'flow={loop.loop_key}', response['Location'])
        self.assertNotIn('evil.example', response['Location'])
