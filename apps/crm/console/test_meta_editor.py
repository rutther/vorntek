from unittest.mock import patch
from unittest import skipUnless
from django.conf import settings
from django.test import TestCase, override_settings
from django.db import connection
from django.urls import reverse
from console.test_flow_workspace import FlowWorkspaceTests
from marketing.models import MarketingIntegration, MarketingProvider
from leads.models import LeadEventOutbox, LeadInboundEvent
import re


@override_settings(SITEOS_META_RUNTIME_ENABLED=False)
class MetaEditorTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.created = []
        existing = set(connection.introspection.table_names())
        with connection.schema_editor() as editor:
            for model in (*FlowWorkspaceTests.models, LeadEventOutbox, LeadInboundEvent):
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
        FlowWorkspaceTests.setUp(self)
        self.item = MarketingIntegration.objects.create(
            site=self.site, provider=MarketingProvider.objects.get_or_create(code='meta', defaults={'name': 'Meta'})[0],
            integration_type='pixel', name='Test integration', public_id='123456789',
            enabled=False, consent_category='marketing', config_json={'notes': 'keep', 'test_event_code': 'TEST123'})
        self.url = reverse('console:marketing_integration_edit', args=[self.item.pk])

    def post(self, section, **values):
        return self.client.post(self.url, {'_section': section, '_version': self.item.updated_at.isoformat(), **values})

    def test_credentials_submit_does_not_change_other_settings(self):
        with patch.dict('os.environ', {'TEST_META_EDITOR_TOKEN': 'test-only'}):
            response = self.post('credentials', secret_ref='TEST_META_EDITOR_TOKEN', name='tampered', enabled='on')
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.secret_ref, 'TEST_META_EDITOR_TOKEN')
        self.assertEqual(self.item.name, 'Test integration')
        self.assertFalse(self.item.enabled)
        self.assertEqual(self.item.config_json, {'notes': 'keep', 'test_event_code': 'TEST123'})

    @skipUnless(connection.vendor == 'postgresql', 'Requires real encrypted credential write rejection')
    def test_missing_vault_key_renders_credential_error_without_saving_or_echoing_token(self):
        import os
        before = MarketingIntegration.objects.filter(pk=self.item.pk).values().get()
        with patch.dict(os.environ, {'SITEOS_SECRET_VAULT_KEY': ''}):
            try:
                response = self.post('credentials', primary_secret='synthetic-must-not-echo-token')
            except ValueError as exc:
                self.fail(f'Vault configuration refusal must render a form error, not escape: {exc}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SITEOS_SECRET_VAULT_KEY')
        self.assertNotContains(response, 'synthetic-must-not-echo-token')
        self.assertEqual(MarketingIntegration.objects.filter(pk=self.item.pk).values().get(), before)

    @skipUnless(connection.vendor == 'postgresql', 'Requires real encrypted credential write rejection')
    def test_reused_signing_key_renders_credential_error_without_changing_integration(self):
        import os
        before = MarketingIntegration.objects.filter(pk=self.item.pk).values().get()
        with patch.dict(os.environ, {'SITEOS_SECRET_VAULT_KEY': settings.SECRET_KEY}):
            try:
                response = self.post('credentials', primary_secret='synthetic-must-not-echo-token')
            except ValueError as exc:
                self.fail(f'Vault configuration refusal must render a form error, not escape: {exc}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'synthetic-must-not-echo-token')
        self.assertEqual(MarketingIntegration.objects.filter(pk=self.item.pk).values().get(), before)

    def test_empty_optional_config_does_not_block_name_save(self):
        self.item.config_json = {}
        self.item.save()
        response = self.post('identity', name='Saved with empty extensions')
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Saved with empty extensions')
        self.assertEqual(self.item.config_json, {})

    def test_empty_optional_config_does_not_block_first_credential(self):
        self.item.config_json = {}
        self.item.save()
        with patch.dict('os.environ', {'TEST_META_EDITOR_TOKEN': 'test-only'}):
            response = self.post('credentials', secret_ref='TEST_META_EDITOR_TOKEN')
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.secret_ref, 'TEST_META_EDITOR_TOKEN')
        self.assertFalse(self.item.enabled)
        self.assertEqual(self.item.config_json, {})

    def test_clearing_test_code_removes_it_without_losing_extensions(self):
        response = self.post('testing', api_version='v26.0', test_event_code='')
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertNotIn('test_event_code', self.item.config_json)
        self.assertEqual(self.item.config_json['notes'], 'keep')

    def test_stale_edit_is_rejected_without_overwriting(self):
        self.item.name = 'Changed by colleague'
        self.item.save()
        response = self.client.post(self.url, {'_section': 'identity', '_version': 'stale', 'name': 'Overwrite'})
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Changed by colleague')
        self.assertContains(response, '刷新')

    def test_invalid_token_is_never_echoed(self):
        response = self.post('credentials', secret_ref='x' * 256, primary_secret='must-never-echo')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'must-never-echo')

    def test_token_help_is_connected_to_visible_instruction(self):
        response = self.client.get(self.url)
        body = response.content.decode()
        token = re.search(r'<input[^>]+id="id_primary_secret"[^>]*>', body).group()
        references = re.search(r'aria-describedby="([^"]+)"', token).group(1).split()
        for reference in references:
            self.assertIn(f'id="{reference}"', body)

    def test_diagnostic_is_outside_credential_form(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'id="mi-diagnostic-form"')
        self.assertContains(response, 'id="mi-credentials-form"')
        self.assertNotContains(response, '重试并刷新状态')
        self.assertContains(response, '访客同意要求')

    def test_global_state_requires_confirmation_even_without_runtime_flag(self):
        response = self.post('state', enabled='on')
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertFalse(self.item.enabled)

    def test_rendered_version_can_be_submitted_in_local_timezone(self):
        page = self.client.get(self.url).content.decode()
        version = re.search(r'name="_version" value="([^"]+)"', page).group(1)
        response = self.client.post(self.url, {'_section': 'identity', '_version': version, 'name': 'New name'})
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'New name')

    def test_unknown_section_cannot_mutate(self):
        response = self.post('unknown', name='tamper', enabled='on')
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Test integration')

    def test_advanced_cannot_override_system_managed_fields(self):
        response = self.post('advanced', config_json='{"test_event_code":"injected"}')
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.config_json['test_event_code'], 'TEST123')

    def test_global_state_can_enable_only_with_credential_and_confirmation(self):
        self.item.secret_ref = 'TEST_META_EDITOR_TOKEN'
        self.item.save()
        with patch.dict('os.environ', {'TEST_META_EDITOR_TOKEN': 'test-only'}):
            response = self.post('state', enabled='on', confirm_global_change='on')
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertTrue(self.item.enabled)

    def test_clear_cannot_remove_credentials_while_enabled(self):
        self.item.secret_ref = 'TEST_META_EDITOR_TOKEN'
        self.item.enabled = True
        self.item.save()
        with patch.dict('os.environ', {'TEST_META_EDITOR_TOKEN': 'test-only'}):
            response = self.post('clear', confirm_global_change='on')
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.secret_ref, 'TEST_META_EDITOR_TOKEN')

    def test_replacing_credential_invalidates_old_diagnostic(self):
        self.item.config_json['last_diagnostic'] = {'overall': 'pass', 'summary': 'Old check'}
        self.item.save()
        with patch.dict('os.environ', {'TEST_META_EDITOR_TOKEN': 'test-only'}):
            response = self.post('credentials', secret_ref='TEST_META_EDITOR_TOKEN')
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertNotIn('last_diagnostic', self.item.config_json)

    def test_saved_credential_is_a_saved_state_not_an_empty_prompt(self):
        self.item.secret_ref = 'TEST_META_EDITOR_TOKEN'
        self.item.save()
        with patch.dict('os.environ', {'TEST_META_EDITOR_TOKEN': 'never-display-this'}):
            response = self.client.get(self.url)
        body = response.content.decode()
        self.assertIn('data-credential-saved="true"', body)
        self.assertIn('凭据已保存', body)
        self.assertIn('更换凭据', body)
        disclosure = re.search(r'<details[^>]+id="mi-credential-edit"[^>]*>', body).group()
        self.assertNotIn(' open', disclosure)
        self.assertNotIn('never-display-this', body)
        self.assertNotIn('香港服务器', body)

    def test_missing_credential_is_required_and_input_is_open(self):
        response = self.client.get(self.url)
        body = response.content.decode()
        self.assertIn('data-credential-saved="false"', body)
        disclosure = re.search(r'<details[^>]+id="mi-credential-edit"[^>]*>', body).group()
        self.assertIn(' open', disclosure)
        self.assertIn('必填', body)
        self.assertIn('meta-generate-access-token-20261007.svg', body)
        self.assertIn('如何获取？看图操作', body)

    def test_invalid_credential_opens_editor_and_shows_inline_error(self):
        response = self.post('credentials', primary_secret='', secret_ref='')
        self.assertContains(response, 'id="mi-token-error"')
        body = response.content.decode()
        disclosure = re.search(r'<details[^>]+id="mi-credential-edit"[^>]*>', body).group()
        self.assertIn(' open', disclosure)

    def test_diagnostic_success_and_failure_have_distinct_visible_states(self):
        self.item.secret_ref = 'TEST_META_EDITOR_TOKEN'
        for overall, label in [('pass', '连接检查通过'), ('fail', '连接检查未通过')]:
            self.item.config_json['last_diagnostic'] = {'overall': overall, 'summary': 'Test summary', 'checks': [
                {'code':'token','status':overall,'required':True},
                {'code':'dataset_access','status':overall,'required':False}]}
            self.item.save()
            with patch.dict('os.environ', {'TEST_META_EDITOR_TOKEN':'fake-only'}):
                response = self.client.get(self.url)
            self.assertContains(response, label)

    def test_name_validation_failure_marks_its_section_unsaved_without_token_instruction(self):
        response = self.post('identity', name='')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-error-section="identity"')
        self.assertNotContains(response, '安全起见，输入过的 Token 不会回显，需要重新填写。')
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Test integration')

    def test_connection_next_action_distinguishes_pass_warning_failure_and_missing(self):
        from console.meta_editor import connection_next_action
        checks = [{'code': 'token', 'status': 'pass', 'required': True},
                  {'code': 'dataset_access', 'status': 'pass', 'required': False},
                  {'code': 'enabled', 'status': 'warn', 'required': False}]
        diagnostic = {'overall': 'warn', 'checks': checks}
        self.assertEqual(connection_next_action(diagnostic, True)['state'], 'ready')
        checks[1]['status'] = 'warn'
        self.assertEqual(connection_next_action(diagnostic, True)['state'], 'review')
        checks[0]['status'] = 'fail'
        self.assertEqual(connection_next_action(diagnostic, True)['state'], 'blocked')
        self.assertEqual(connection_next_action(diagnostic, False)['state'], 'missing')
        self.assertEqual(connection_next_action(None, True)['state'], 'unchecked')
        self.assertFalse(connection_next_action({'overall':'pass','checks':[]}, True)['can_return'])

    def test_success_offers_stable_resource_navigation_without_inventing_origin(self):
        self.item.secret_ref = 'TEST_META_EDITOR_TOKEN'
        self.item.config_json['last_diagnostic'] = {'overall':'warn','checks':[
            {'code':'token','status':'pass','required':True},
            {'code':'dataset_access','status':'pass','required':False},
            {'code':'enabled','status':'warn','required':False}]}
        self.item.save()
        flows = {'flows':[{'name':'Current flow','url':'/admin/marketing/guided-loop/#/flow/test?node=server','state':'not_applied'}]}
        with patch.dict('os.environ', {'TEST_META_EDITOR_TOKEN':'fake-only'}), patch('marketing.flow_runtime.integration_runtime_summary', return_value=flows):
            body = self.client.get(self.url).content.decode()
        self.assertIn('data-connection-state="ready"', body)
        self.assertRegex(body, r'<a[^>]+class="mi-button mi-primary mi-continue"[^>]+href="/admin/marketing/integrations/"')
        self.assertNotIn('返回第07步继续', body)
        self.assertNotIn('aria-current="step"', body)
        self.item.refresh_from_db()
        self.assertFalse(self.item.enabled)

    def test_metadata_warning_offers_review_not_success_and_multiple_flows_are_not_autoselected(self):
        self.item.secret_ref = 'TEST_META_EDITOR_TOKEN'
        self.item.config_json['last_diagnostic'] = {'overall':'warn','checks':[
            {'code':'token','status':'pass','required':True},
            {'code':'dataset_access','status':'warn','required':False}]}
        self.item.save()
        flows = {'flows':[{'name':'A','url':'/flow/a'}, {'name':'B','url':'/flow/b'}]}
        with patch.dict('os.environ', {'TEST_META_EDITOR_TOKEN':'fake-only'}), patch('marketing.flow_runtime.integration_runtime_summary', return_value=flows):
            body = self.client.get(self.url).content.decode()
        self.assertIn('data-connection-state="review"',body)
        self.assertIn('仍有待核对项',body)
        self.assertNotIn('mi-continue',body)
        self.assertNotIn('选择要返回的流程',body)
        self.assertNotIn('aria-current="step"',body)
        self.assertNotIn('连接检查通过',body)
