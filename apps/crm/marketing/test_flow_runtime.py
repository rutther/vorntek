import importlib
import uuid
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import override_settings, TestCase
from django.contrib.auth.models import Group
from django.db import connection
from console.access import GROUP_BY_ROLE, ROLE_SYSTEM_ADMIN
from console.test_flow_workspace import FlowWorkspaceTests
from marketing import models


@override_settings(SITEOS_META_RUNTIME_ENABLED=True, SITEOS_META_RUNTIME_ENVIRONMENT='test',
                   SITEOS_META_RUNTIME_LOADER_VERSION=2)
class FlowRuntimeTests(TestCase):
    models = FlowWorkspaceTests.models + tuple(getattr(models, name) for name in
        ('MarketingRuntimeBinding', 'MarketingRuntimeRevision', 'MarketingRuntimeCommand')
        if hasattr(models, name))
    @classmethod
    def setUpClass(cls):
        cls.created_models = []
        existing = set(connection.introspection.table_names())
        with connection.schema_editor() as editor:
            for model in cls.models:
                if model._meta.db_table not in existing:
                    editor.create_model(model)
                    cls.created_models.append(model)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        with connection.schema_editor() as editor:
            for model in reversed(cls.created_models):
                editor.delete_model(model)
    url = FlowWorkspaceTests.url
    create = FlowWorkspaceTests.create
    command = FlowWorkspaceTests.command

    def setUp(self):
        FlowWorkspaceTests.setUp(self)
        self.user.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])[0])
        self.form.capi_enabled = True
        self.form.save()
        self.created = self.create()
        self.created = self.command(self.created, 'save_config', nodeKey='dataset',
            values={'source': 'created', 'externalId': '123456789'})
        self.loop = models.MarketingLoop.objects.get(loop_key=self.created['id'])

    def runtime(self):
        self.assertTrue(hasattr(models, 'MarketingRuntimeBinding'), 'Runtime binding must be persisted')
        return importlib.import_module('marketing.flow_runtime')

    def act(self, action, **kwargs):
        module = self.runtime()
        current = module.runtime_snapshot(self.loop)
        data = {'action': action, 'requestId': str(uuid.uuid4()),
                'expectedVersion': current['version'], **kwargs}
        return module.runtime_command(loop=self.loop, actor=self.user, data=data)

    def prepared(self):
        state = self.act('prepare_integration')
        integration = models.MarketingIntegration.objects.get(pk=state['integration']['id'])
        integration.secret_ref = 'TEST_ONLY_TOKEN_REF'
        integration.save()
        self.act('save_draft', draft={'landingPaths': ['/'], 'leadPaths': ['/contact/'],
            'formId': self.form.pk, 'consentRequired': True})
        return integration

    def applied(self):
        integration = self.prepared()
        plan = self.runtime().runtime_snapshot(self.loop)
        with patch('marketing.flow_runtime.credential_available', return_value=True):
            state = self.act('apply', configHash=plan['configHash'],
                              expectedFlowVersion=plan['flowVersion'])
        return integration, state

    def test_new_integration_and_draft_do_not_send(self):
        integration = self.prepared()
        self.assertFalse(integration.enabled)
        self.assertEqual(integration.routing_mode, 'flow_scoped')
        self.assertEqual(integration.environment, 'test')
        state = self.runtime().runtime_snapshot(self.loop)
        self.assertEqual(state['runtimeState'], 'not_applied')
        self.assertIsNone(state['activeRevision'])

    def test_dependencies_inspect_current_and_draft_without_contacts_or_delivery_claims(self):
        self.prepared()
        self.form.notify_emails = 'synthetic-only@example.invalid'
        self.form.save()
        with patch('marketing.runtime_dependencies.email_delivery_configured', return_value=False):
            dependencies = self.runtime().runtime_snapshot(self.loop)['dependencies']
        self.assertEqual(dependencies[0]['form']['id'], self.form.pk)
        self.assertIn('/leads/forms/', dependencies[0]['form']['editUrl'])
        self.assertIn('邮件服务未配置', str(dependencies))
        self.assertNotIn('synthetic-only@example.invalid', str(dependencies))
        self.assertNotIn('已送达', str(dependencies))
        self.form.status = 'disabled'
        self.form.save()
        updated = self.runtime().runtime_snapshot(self.loop)['dependencies']
        self.assertIn('表单当前不可接收网站询盘', str(updated))

    def test_dependencies_do_not_expose_cross_site_form(self):
        self.prepared()
        from leads.models import LeadFormDefinition
        other_form = LeadFormDefinition.objects.create(site=self.other, code='private-form', name='Do not expose')
        binding = models.MarketingRuntimeBinding.objects.get(loop=self.loop)
        binding.draft_json = {**binding.draft_json, 'formId':other_form.pk}
        binding.save()
        dependencies = self.runtime().runtime_snapshot(self.loop)['dependencies']
        self.assertIsNone(dependencies[0]['form'])
        self.assertIn('所选表单不存在或不属于本站', str(dependencies))
        self.assertNotIn('Do not expose', str(dependencies))
        binding.draft_json['formId'] = 'invalid'
        binding.save()
        self.assertIsNone(self.runtime().runtime_snapshot(self.loop)['dependencies'][0]['form'])

    def test_dependencies_keep_active_form_when_draft_changes(self):
        self.applied()
        from leads.models import LeadFormDefinition
        next_form = LeadFormDefinition.objects.create(site=self.site, code='next-form', name='待应用表单')
        binding = models.MarketingRuntimeBinding.objects.get(loop=self.loop)
        binding.draft_json = {**binding.draft_json, 'formId':next_form.pk}
        binding.save()
        dependencies = self.runtime().runtime_snapshot(self.loop)['dependencies']
        self.assertEqual([d['key'] for d in dependencies], ['active','draft'])
        self.assertEqual([d['form']['id'] for d in dependencies], [self.form.pk,next_form.pk])

    def test_management_distinguishes_never_applied_from_disabled(self):
        integration = self.prepared()
        row = self.runtime().integration_runtime_summary(integration)['flows'][0]
        self.assertEqual(row['state'], 'not_applied')
        self.assertEqual(row['scopeLabel'], '待应用范围')
        self.assertEqual(row['formName'], '项目询盘')

    def test_apply_pause_resume_creates_new_generation(self):
        integration, state = self.applied()
        first = state['activeRevision']['key']
        self.assertEqual(state['runtimeState'], 'applied')
        paused = self.act('pause')
        self.assertEqual(paused['runtimeState'], 'paused')
        integration.refresh_from_db()
        self.assertTrue(integration.enabled, 'Binding pause must not disable shared integration')
        with patch('marketing.flow_runtime.credential_available', return_value=True):
            resumed = self.act('resume', configHash=paused['configHash'],
                               expectedFlowVersion=paused['flowVersion'])
        self.assertNotEqual(resumed['activeRevision']['key'], first)
        self.assertEqual(resumed['verificationState'], 'untested')

    def test_retry_and_conflict(self):
        module = self.runtime()
        original_ids = set(models.MarketingIntegration.objects.values_list('pk', flat=True))
        request = {'action':'prepare_integration', 'expectedVersion':0, 'requestId':str(uuid.uuid4())}
        first = module.runtime_command(loop=self.loop, actor=self.user, data=request)
        retry = module.runtime_command(loop=self.loop, actor=self.user, data=request)
        self.assertEqual(first, retry)
        current_ids = set(models.MarketingIntegration.objects.values_list('pk', flat=True))
        self.assertTrue(original_ids.issubset(current_ids), 'Existing independent integrations must be retained')
        self.assertEqual(len(current_ids - original_ids), 1)
        with self.assertRaises(module.RuntimeConflict):
            module.runtime_command(loop=self.loop, actor=self.user, data={**request, 'action':'pause'})
        with self.assertRaises(module.RuntimeConflict):
            self.act('save_draft', expectedVersion=0, draft={})

    def test_history_recovery_creates_a_new_generation_and_retry_is_idempotent(self):
        _, first = self.applied()
        original = first['activeRevision']
        self.act('save_draft', draft={**first['draft'], 'landingPaths':['/', '/contact/']})
        current = self.runtime().runtime_snapshot(self.loop)
        with patch('marketing.flow_runtime.credential_available', return_value=True):
            second = self.act('apply', configHash=current['configHash'],
                              expectedFlowVersion=current['flowVersion'])
            request = {'action':'rollback', 'requestId':str(uuid.uuid4()),
                'expectedVersion':second['version'], 'configHash':second['configHash'],
                'expectedFlowVersion':second['flowVersion'], 'targetRevision':original['key']}
            recovered = self.runtime().runtime_command(loop=self.loop, actor=self.user, data=request)
            retried = self.runtime().runtime_command(loop=self.loop, actor=self.user, data=request)
        self.assertEqual(recovered, retried)
        self.assertEqual(recovered['activeRevision']['scope'], original['scope'])
        self.assertNotIn(recovered['activeRevision']['key'],
                         [original['key'], second['activeRevision']['key']])
        self.assertEqual(recovered['runtimeState'], 'applied')
        self.assertEqual(recovered['verificationState'], 'untested')
        self.assertEqual(models.MarketingRuntimeRevision.objects.filter(binding__loop=self.loop).count(), 3)
        self.assertEqual(models.MarketingRuntimeRevision.objects.get(
            revision_key=original['key']).config_json, original['scope'])

    def test_invalid_history_recovery_preserves_draft_and_active_version(self):
        _, first = self.applied()
        self.act('save_draft', draft={**first['draft'], 'landingPaths':['/', '/contact/']})
        binding = models.MarketingRuntimeBinding.objects.get(loop=self.loop)
        before = (binding.version, binding.active_revision_key, dict(binding.draft_json))
        with patch('marketing.flow_runtime.credential_available', return_value=False):
            current = self.runtime().runtime_snapshot(self.loop)
            with self.assertRaises(ValidationError):
                self.act('rollback', configHash=current['configHash'],
                    expectedFlowVersion=current['flowVersion'], targetRevision=first['activeRevision']['key'])
        binding.refresh_from_db()
        self.assertEqual((binding.version, binding.active_revision_key, binding.draft_json), before)
        with patch('marketing.flow_runtime.credential_available', return_value=True):
            current = self.runtime().runtime_snapshot(self.loop)
            with self.assertRaises(ValidationError):
                self.act('rollback', configHash=current['configHash'],
                    expectedFlowVersion=current['flowVersion'], targetRevision=str(uuid.uuid4()))
        binding.refresh_from_db()
        self.assertEqual((binding.version, binding.active_revision_key, binding.draft_json), before)

    def test_disabled_integration_is_not_reported_as_running(self):
        integration, state = self.applied()
        integration.enabled = False
        integration.save()
        self.assertEqual(self.runtime().runtime_snapshot(self.loop)['runtimeState'], 'inactive')

    def test_paths_reject_widening_and_cross_origin(self):
        self.prepared()
        for path in ['//evil.test/', '/%2e%2e/private', '/a/../b', '/*', 'https://other.example.com/', '/a%2Fb']:
            with self.subTest(path=path), self.assertRaises(ValidationError):
                self.act('save_draft', draft={'landingPaths':[path], 'leadPaths':['/'],
                    'formId':self.form.pk, 'consentRequired':True})

    def test_cross_environment_asset_cannot_be_reused(self):
        models.MarketingIntegration.objects.create(site=self.site, provider=self.provider,
            name='Other environment', integration_type='pixel', public_id='123456789',
            environment='production', routing_mode='legacy_site')
        with self.assertRaises(ValidationError):
            self.act('prepare_integration')

    def test_apply_stale_plan_and_missing_credentials_do_not_activate(self):
        self.prepared()
        module = self.runtime()
        state = module.runtime_snapshot(self.loop)
        with self.assertRaises(module.RuntimeConflict):
            self.act('apply', configHash='stale', expectedFlowVersion=state['flowVersion'])
        with patch('marketing.flow_runtime.credential_available', return_value=False), self.assertRaises(ValidationError):
            self.act('apply', configHash=state['configHash'], expectedFlowVersion=state['flowVersion'])
        self.assertIsNone(module.runtime_snapshot(self.loop)['activeRevision'])

    def test_public_id_cannot_change_after_binding(self):
        integration = self.prepared()
        integration.public_id = '987654321'
        with self.assertRaises(ValidationError):
            integration.full_clean()
