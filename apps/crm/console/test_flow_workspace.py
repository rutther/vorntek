"""HTTP contracts: persistence, scope, real-vs-recorded state, and safe retries."""
import json
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import Client, TestCase

from console.access import GROUP_BY_ROLE, ROLE_MARKETING_OPS, ROLE_SALES, ROLE_SYSTEM_ADMIN
from console.models import AuditLog
from leads.models import LeadFormDefinition
from marketing.models import (MarketingIntegration, MarketingLoop, MarketingLoopNode,
    MarketingLoopEdge, MarketingLoopTask, MarketingLoopAssetBinding, MarketingProvider)
from sitecore.models import Site, SiteLocale


class FlowWorkspaceTests(TestCase):
    models = (Site, SiteLocale, MarketingProvider, MarketingIntegration,
              LeadFormDefinition, MarketingLoop, MarketingLoopNode, MarketingLoopEdge,
              MarketingLoopTask, MarketingLoopAssetBinding, AuditLog)
    url = '/admin/api/marketing/flows/'

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

    def setUp(self):
        self.site = Site.objects.create(code='siteos_demo', name='Vorntek Example',
            base_url='http://127.0.0.1:25000', default_locale='en', enabled=True)
        SiteLocale.objects.create(site=self.site, locale_code='en', label='English',
            direction='ltr', is_default=True, enabled=True, sort_order=1)
        self.other = Site.objects.create(code='other_site', name='Other',
            base_url='https://other.example.com', default_locale='en', enabled=True)
        # Installation/upgrade contracts retain the real provider and disabled
        # integration seed. Reuse it without updating or deleting either row.
        self.provider, _ = MarketingProvider.objects.get_or_create(
            code='meta', defaults={'name': 'Meta', 'enabled': True})
        self.user = get_user_model().objects.create_user(username='flow-editor', password='local-only-test')
        self.user.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_MARKETING_OPS])[0])
        self.sales = get_user_model().objects.create_user(username='flow-sales', password='local-only-test')
        self.sales.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SALES])[0])
        self.form = LeadFormDefinition.objects.create(site=self.site, code='project-inquiry',
            name='项目询盘', status='active', channel='website')
        # Full production SQL seeds disabled Google/WhatsApp integrations.
        # Recording a dataset or doing a preflight must neither create nor
        # enable an integration, regardless of those pre-existing rows.
        self.existing_integrations = dict(MarketingIntegration.objects.values_list('pk', 'enabled'))
        self.client.force_login(self.user)

    def create(self, name='示例实操流程', environment='test', request_id=None):
        response = self.client.post(self.url, json.dumps({'action':'create', 'name':name,
            'environment':environment, 'requestId':request_id or str(uuid.uuid4())}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content[:500])
        return response.json()['loop']

    def command(self, loop, action, expected=200, request_id=None, **values):
        response = self.client.post(self.url + loop['id'] + '/', json.dumps({
            'action':action, 'expectedVersion':loop['version'],
            'requestId':request_id or str(uuid.uuid4()), **values}), content_type='application/json')
        self.assertEqual(response.status_code, expected, response.content[:500])
        return response.json().get('loop') if expected == 200 else response

    def test_empty_catalog_is_real_and_creation_is_idempotent(self):
        # A compatible upgrade may already contain other sites' real loops.
        existing_loop_count = MarketingLoop.objects.count()
        data = self.client.get(self.url)
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.json()['loops'], [])
        self.assertEqual(data.json()['definitions']['form']['fields'][0]['options'],
                         [[str(self.form.pk), '项目询盘']])
        rid = str(uuid.uuid4())
        one = self.create(request_id=rid)
        two = self.create(request_id=rid)
        self.assertEqual(one['id'], two['id'])
        self.assertEqual(MarketingLoop.objects.count(), existing_loop_count + 1)
        self.assertEqual(MarketingLoop.objects.filter(site=self.site).count(), 1)
        self.assertEqual(one['configs'], {})
        self.assertEqual(one['history'], [])
        self.assertEqual(one['nextTask'], 'dataset')
        self.assertTrue(one['code'].startswith('ncMetaTstWebLeadSiteosDemo'))

    def test_valid_csrf_wrong_media_and_invalid_json_are_bounded_without_changes(self):
        loop = self.create()
        csrf_client = Client(enforce_csrf_checks=True, raise_request_exception=False)
        csrf_client.force_login(self.user)
        csrf_client.get('/admin/marketing/guided-loop/')
        token = csrf_client.cookies['csrftoken'].value
        before = (MarketingLoop.objects.count(), AuditLog.objects.count(),
                  MarketingLoop.objects.get(loop_key=loop['id']).config_json)
        for url in (self.url, self.url + loop['id'] + '/'):
            for body, media in (
                ('action=create', 'application/x-www-form-urlencoded'),
                (b'--synthetic--\r\n', 'multipart/form-data; boundary=synthetic'),
                ('', 'application/json'), ('{', 'application/json'), ('[]', 'application/json'),
                (b'\xff', 'application/json'), ('{"note":"' + 'x' * 16000 + '"}', 'application/json'),
            ):
                with self.subTest(url=url, media=media, size=len(body)):
                    response = csrf_client.post(url, body, content_type=media, HTTP_X_CSRFTOKEN=token)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()['code'], 'invalid')
        self.assertEqual((MarketingLoop.objects.count(), AuditLog.objects.count(),
                          MarketingLoop.objects.get(loop_key=loop['id']).config_json), before)
        self.assertEqual(csrf_client.post(self.url, json.dumps({
            'action': 'create', 'name': 'Valid JSON after rejection', 'environment': 'test',
            'requestId': str(uuid.uuid4()),
        }), content_type='application/json', HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_record_id_continues_without_claiming_connected(self):
        one, two = self.create(), self.create(name='第二个流程')
        saved = self.command(one, 'save_config', nodeKey='dataset',
            values={'source':'created','externalId':'123456789','alias':'用户在Meta创建'})
        self.assertEqual(saved['nextTask'], 'website')
        self.assertIsNone(saved['check'])
        self.assertEqual(dict(MarketingIntegration.objects.values_list('pk', 'enabled')), self.existing_integrations)
        self.assertFalse(MarketingLoopNode.objects.filter(status='healthy').exists())
        self.assertFalse(MarketingLoop.objects.filter(status='running').exists())
        self.assertEqual(MarketingLoopTask.objects.get(loop__loop_key=one['id'],task_key='dataset').status, 'waiting')
        all_loops = self.client.get(self.url).json()['loops']
        self.assertEqual(next(l for l in all_loops if l['id']==two['id'])['configs'], {})
        self.assertEqual(next(l for l in all_loops if l['id']==one['id'])['configs']['dataset']['externalId'],'123456789')

    def test_creation_note_round_trips_and_participates_in_retry_identity(self):
        payload = {'action':'create','name':'独立闭环备注','environment':'test',
                   'requestId':str(uuid.uuid4()),'note':'仅用于隔离测试的业务范围说明'}
        response = self.client.post(self.url,json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content[:500])
        item = response.json()['loop']
        self.assertEqual(item['note'],payload['note'])
        self.assertEqual(self.client.get(self.url+item['id']+'/').json()['loop']['note'],payload['note'])
        retry = self.client.post(self.url,json.dumps(payload),content_type='application/json')
        self.assertEqual(retry.json()['loop']['id'],item['id'])
        payload['note']='不同的业务范围'
        self.assertEqual(self.client.post(self.url,json.dumps(payload),content_type='application/json').status_code,409)
        for invalid in ['字'*1001, {'unexpected':'object'}]:
            payload.update(requestId=str(uuid.uuid4()),note=invalid)
            self.assertEqual(self.client.post(self.url,json.dumps(payload),content_type='application/json').status_code,400)

    def test_blank_destination_does_not_suggest_a_specific_site(self):
        loop = self.create()
        data = self.client.get(self.url).json()
        field = next(f for f in data['definitions']['website']['fields'] if f['key'] == 'url')
        self.assertNotRegex(field.get('placeholder', ''), r'https?://|\b[a-z0-9-]+\.(?:com|org|net)\b')
        self.assertNotIn('website', loop['configs'])
        saved = self.command(loop, 'save_config', nodeKey='website',
            values={'url':'https://vorntek.com/contact'})
        reloaded = self.client.get(self.url).json()['loops'][0]
        self.assertEqual(reloaded['configs']['website']['url'], 'https://vorntek.com/contact')
        self.assertEqual(saved['configs']['website']['url'], 'https://vorntek.com/contact')

    def test_draft_survives_new_session_without_completing(self):
        loop = self.create()
        saved = self.command(loop,'save_draft',nodeKey='website', values={'url':'unfinished'})
        new_client = Client()
        new_client.force_login(self.user)
        reloaded = new_client.get(self.url).json()['loops'][0]
        self.assertEqual(reloaded['drafts'], {'website':{'url':'unfinished'}})
        self.assertNotIn('website', reloaded['configs'])
        self.assertEqual(saved['revision'], loop['revision'])

    def test_stale_write_rejected_and_retry_does_not_duplicate(self):
        loop = self.create()
        rid = str(uuid.uuid4())
        saved = self.command(loop, 'rename', name='更新名称', request_id=rid)
        retry = self.command(loop, 'rename', name='更新名称', request_id=rid)
        self.assertEqual(saved['version'], retry['version'])
        response = self.command(loop, 'rename', expected=409, name='陈旧覆盖')
        self.assertEqual(response.json()['current']['name'], '更新名称')
        self.command(saved, 'rename', expected=409, request_id=rid, name='重复ID不同内容')

    def test_archive_restore_copy_preserve_structure_not_assets(self):
        self.user.groups.add(Group.objects.get_or_create(name=GROUP_BY_ROLE[ROLE_SYSTEM_ADMIN])[0])
        loop = self.create()
        loop = self.command(loop,'filter',enabled=True)
        loop = self.command(loop,'save_config',nodeKey='dataset',values={'source':'created','externalId':'123456789'})
        copied = self.command(loop, 'duplicate')
        self.assertNotEqual(copied['id'], loop['id'])
        self.assertTrue(copied['filterEnabled'])
        self.assertEqual(copied['configs'], {})
        self.assertEqual(copied['history'], [])
        archived = self.command(loop,'archive')
        self.command(archived,'save_draft',expected=400,nodeKey='website',values={'url':'abc'})
        restored = self.command(archived,'restore')
        self.assertFalse(restored['archived'])
        self.assertEqual(restored['configs']['dataset']['externalId'], '123456789')

    def test_manual_observation_versions_not_health(self):
        loop = self.create()
        recorded = self.command(loop,'evidence',nodeKey='dataset',result='observed',
            note='用户截图显示数据集已经创建，尚未核对事件。',reference='本次对话截图1')
        self.assertEqual(recorded['history'][0]['kind'], 'manual')
        self.assertEqual(recorded['history'][0]['actor'], 'flow-editor')
        changed = self.command(recorded,'save_config',nodeKey='website',values={'url':'https://example.com/'})
        self.assertLess(changed['history'][0]['revision'], changed['revision'])
        renamed = self.command(changed,'rename',name='修改中文名称')
        self.assertEqual(renamed['revision'], changed['revision'])
        self.command(renamed,'evidence',expected=400,nodeKey='dataset',result='pass',note='伪造验证通过结果',reference='')
        self.command(renamed,'simulate',expected=400,result='pass')

    def test_legacy_endpoints_cannot_mutate_new_workspace(self):
        loop = self.create()
        base = '/admin/marketing/guided-loop/meta/website-form/'+loop['id']+'/'
        self.assertEqual(self.client.get(base).status_code, 404)
        self.assertEqual(self.client.post(base+'rename/', {'name':'绕过版本检查'}).status_code, 404)
        self.assertEqual(self.client.get(self.url).json()['loops'][0]['name'],loop['name'])

    def test_copy_retry_after_receipt_cache_eviction_does_not_reset_target(self):
        loop = self.create()
        rid = str(uuid.uuid4())
        copied = self.command(loop,'duplicate',request_id=rid)
        copied = self.command(copied,'filter',enabled=True)
        source = MarketingLoop.objects.get(loop_key=loop['id'])
        state = source.config_json
        state['workspace_v1']['receipts'] = {}
        source.config_json = state
        source.save(update_fields=['config_json'])
        retry = self.command(loop,'duplicate',request_id=rid)
        self.assertTrue(retry['filterEnabled'])
        self.assertEqual(retry['version'],copied['version'])

    def test_malformed_enum_and_dataset_source_changes_are_rejected(self):
        response = self.client.post(self.url,json.dumps({'action':'create','requestId':str(uuid.uuid4()),
            'name':'错误枚举','environment':[]}),content_type='application/json')
        self.assertEqual(response.status_code,400)
        loop = self.create()
        self.command(loop,'evidence',expected=400,nodeKey='dataset',result=[],note='不应成为服务器错误',reference='')
        loop = self.command(loop,'save_config',nodeKey='dataset',values={'source':'created','externalId':'123456789'})
        self.command(loop,'save_config',expected=400,nodeKey='dataset',values={'source':'existing','externalId':'123456789'})
        self.command(loop,'save_config',nodeKey='dataset',values={'source':'created','externalId':'123456789','alias':'用户备注'})
        self.assertEqual(MarketingLoopAssetBinding.objects.get(loop__loop_key=loop['id']).observed_name,'用户备注')

    def test_dataset_scope_and_existing_id_are_protected(self):
        integration = MarketingIntegration.objects.create(site=self.site, provider=self.provider,
            name='已接入数据集',integration_type='pixel',public_id='2114538626159599',enabled=True)
        test = self.create(environment='test')
        self.command(test,'save_config',expected=400,nodeKey='dataset',values={
            'source':'created','externalId':'2114538626159599'})
        prod = self.create(environment='production')
        self.assertEqual(prod['sources'][0]['id'], '2114538626159599')
        saved = self.command(prod,'save_config',nodeKey='dataset',values={
            'source':'existing','externalId':'2114538626159599'})
        self.command(saved,'save_config',expected=400,nodeKey='dataset',values={
            'source':'created','externalId':'999999999'})
        integration.refresh_from_db()
        self.assertEqual(integration.public_id, '2114538626159599')

    def test_preflight_reads_internal_configuration_only(self):
        loop = self.create()
        checked = self.command(loop,'preflight')
        check = checked['history'][0]
        self.assertEqual(check['kind'], 'preflight')
        self.assertEqual(check['result'], 'pending')
        self.assertTrue(check['checks'])
        self.assertIsNone(checked['check'])
        self.assertEqual(dict(MarketingIntegration.objects.values_list('pk', 'enabled')), self.existing_integrations)

    def test_read_write_scope_role_csrf_and_malformed_body(self):
        loop = self.create()
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.post(self.url+'%s/'%loop['id'],{}).status_code,403)
        self.client.force_login(self.user)
        from console.flow_workspace_service import create_flow
        foreign = create_flow(site=self.other, actor=self.user, data={
            'action': 'create', 'name': 'Foreign scoped fixture', 'environment': 'test',
            'requestId': str(uuid.uuid4()),
        })
        # Build a genuine foreign object; production correctly forbids moving
        # an existing loop's immutable identity between sites.
        self.command({'id': str(foreign.loop_key), 'version': 1},
                     'rename', expected=404, name='跨站点写入')
        self.assertEqual(MarketingLoop.objects.get(loop_key=loop['id']).site_id, self.site.pk)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url,'{}',content_type='application/json').status_code,403)
        self.assertEqual(self.client.post(self.url,'[]',content_type='application/json').status_code,400)
        self.client.logout()
        self.assertIn(self.client.get(self.url).status_code, (302,401))

    def test_validation_rejects_unknown_fields_foreign_form_and_unsafe_url(self):
        loop = self.create()
        self.command(loop,'save_config',expected=400,nodeKey='website',values={'url':'javascript:alert(1)'})
        self.command(loop,'save_draft',expected=400,nodeKey='dataset',values={'token':'must-not-be-stored'})
        self.command(loop,'save_config',expected=400,nodeKey='form',values={'formId':'999999'})
        saved = self.command(loop,'save_config',nodeKey='form',values={'formId':str(self.form.pk)})
        self.assertEqual(saved['configs']['form']['formId'],str(self.form.pk))

    def test_positions_bounded_without_invalidating_evidence(self):
        loop = self.create()
        moved = self.command(loop,'positions',positions={'website':{'x':300,'y':-30}})
        self.assertEqual(moved['positions']['website'],{'x':77,'y':65})
        self.assertEqual(moved['revision'],loop['revision'])
        self.command(moved,'positions',expected=400,positions={'bad':{'x':1,'y':2}})

    def test_shell_uses_existing_crm_login_and_no_anonymous_business_store(self):
        response = self.client.get('/admin/marketing/guided-loop/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'flow-bootstrap')
        self.assertContains(response,'flow-editor')
        self.assertContains(response,'flow-workspace/app.js')

    def test_navigation_exposes_persistent_plans_and_guidance_without_hiding_operations(self):
        response = self.client.get('/admin/marketing/guided-loop/')
        for href in ('/admin/marketing/plans/', '/admin/marketing/guided-loop/',
                     '/admin/marketing/events/', '/admin/marketing/forms/'):
            self.assertContains(response, 'href="' + href + '"')
        self.assertContains(response, 'nc-guide-primary-nav')
        self.assertNotContains(response, 'New Crown')
