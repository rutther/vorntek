import json
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from marketing.test_flow_runtime import FlowRuntimeTests as RuntimeFixture
from marketing.models import MarketingRuntimeBinding


class PixelCatalogTests(SimpleTestCase):
    def test_catalog_is_site_bound_and_fails_closed_when_stale(self):
        from marketing.pixel_catalog import pixel_catalog
        from types import SimpleNamespace
        site = SimpleNamespace(code='siteos_demo')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'catalog.json'
            doc = {'siteCode':site.code,'generatedAt':time.time(),'pages':[
                {'path':'/','title':'Home','locale':'en','formCodes':[]}]}
            path.write_text(json.dumps(doc))
            with self.settings(SITEOS_PIXEL_CATALOG_PATH=path):
                self.assertTrue(pixel_catalog(site)['available'])
                self.assertFalse(pixel_catalog(SimpleNamespace(code='foreign'))['available'])
                doc['generatedAt'] -= 600
                path.write_text(json.dumps(doc))
                self.assertFalse(pixel_catalog(site)['available'])


class PixelStepTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        self.catalog = {'available':True,'version':'test-catalog','pages':[
            {'path':'/','title':'首页','locale':'en','formCodes':[]},
            {'path':'/contact/','title':'联系','locale':'en','formCodes':['project-inquiry']}]}
        self.patcher = patch('marketing.pixel_catalog.pixel_catalog', return_value=self.catalog)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def payload(self, **changes):
        if not MarketingRuntimeBinding.objects.filter(loop=self.loop).exists():
            self.act('prepare_integration')
        self.loop.refresh_from_db()
        from console.flow_workspace_service import serialize
        return {'action':'save_pixel_step','requestId':str(uuid.uuid4()),
            'expectedVersion':serialize(self.loop)['version'],
            'expectedRuntimeVersion':MarketingRuntimeBinding.objects.get(loop=self.loop).version,
            'intent':'save','values':{'mode':'managed','consent':True},
            'scope':{'landingPaths':['/','/contact'],'leadPaths':['/contact/'],
                     'formId':self.form.pk,'consentRequired':True},**changes}

    def post(self, payload, expected=200):
        response = self.client.post(self.url+str(self.loop.loop_key)+'/',json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,expected,response.content[:600])
        return response

    def test_full_save_synchronizes_form_without_activation_or_capi(self):
        self.form.capi_enabled=False
        self.form.save()
        result=self.post(self.payload()).json()['loop']
        self.assertEqual(result['configs']['form']['formId'],str(self.form.pk))
        binding=MarketingRuntimeBinding.objects.get(loop=self.loop)
        self.assertEqual(binding.draft_json['landingPaths'],['/','/contact/'])
        self.assertIsNone(binding.active_revision_key)
        self.assertFalse(binding.integration.enabled)
        self.loop.refresh_from_db()
        self.assertTrue(any(c['code']=='form_invalid' for c in self.runtime().runtime_snapshot(self.loop)['blockingChecks']))

    def test_incomplete_draft_does_not_replace_complete_scope(self):
        self.post(self.payload())
        binding=MarketingRuntimeBinding.objects.get(loop=self.loop)
        before=binding.draft_json
        result=self.post(self.payload(intent='draft',scope={'landingPaths':[],'leadPaths':[],
            'formId':'','consentRequired':True})).json()['loop']
        binding.refresh_from_db()
        self.assertEqual(binding.draft_json,before)
        self.assertEqual(result['drafts']['pixel']['scope']['landingPaths'],[])

    def test_invalid_route_or_wrong_form_placement_rejected(self):
        data=self.payload(); data['scope']['leadPaths']=['/']
        self.post(data,400)
        data=self.payload(); data['scope']['landingPaths']=['//evil.example/']
        self.post(data,400)
        data=self.payload(); data['scope']['landingPaths']=['/removed/']
        self.post(data,400)

    def test_both_versions_and_idempotent_retry(self):
        data=self.payload(); data['expectedRuntimeVersion']=0
        self.post(data,409)
        data=self.payload(); data['expectedVersion']=0
        self.post(data,409)
        data=self.payload()
        first=self.post(data).json()['loop']
        second=self.post(data).json()['loop']
        self.assertEqual(first['version'],second['version'])

    def test_rollback_on_runtime_write_failure(self):
        before=self.payload()
        with patch.object(MarketingRuntimeBinding,'save',side_effect=ValidationError('injected')):
            self.post(before,400)
        self.loop.refresh_from_db()
        self.assertNotIn('pixel',self.loop.config_json['workspace_v1']['configs'])

    def test_step05_cannot_change_shared_form_independently(self):
        saved=self.post(self.payload()).json()['loop']
        from leads.models import LeadFormDefinition
        other=LeadFormDefinition.objects.create(site=self.site,code='other',name='Other',status='active',channel='website')
        self.command(saved,'save_config',expected=400,nodeKey='form',values={'formId':str(other.pk)})

    def test_catalog_failure_allows_draft_not_final_save(self):
        self.catalog['available']=False
        self.post(self.payload(),400)
        self.post(self.payload(intent='draft'))

    def test_active_scope_and_old_pixel_preserved_by_save(self):
        integration,active=self.applied()
        from marketing.models import MarketingIntegration, MarketingRuntimeRevision
        old=MarketingIntegration.objects.create(site=self.site,provider=self.provider,
            integration_type='pixel',public_id='987654321',name='Legacy',enabled=True,routing_mode='legacy_site',environment='test')
        paused=self.act('pause')
        count=MarketingRuntimeRevision.objects.count()
        result=self.post(self.payload()).json()['loop']
        binding=MarketingRuntimeBinding.objects.get(loop=self.loop)
        integration.refresh_from_db();old.refresh_from_db()
        self.assertEqual(str(binding.active_revision_key),active['activeRevision']['key'])
        self.assertTrue(binding.paused)
        self.assertTrue(integration.enabled)
        self.assertTrue(old.enabled)
        self.assertEqual(MarketingRuntimeRevision.objects.count(),count)
        self.assertEqual(result['savedRuntimeVersion'],binding.version)

    def test_cross_form_history_cannot_break_shared_reference(self):
        integration,active=self.applied()
        from leads.models import LeadFormDefinition
        other=LeadFormDefinition.objects.create(site=self.site,code='other',name='Other',status='active',channel='website',capi_enabled=True,submission_event_name='Lead')
        self.catalog['pages'][1]['formCodes'].append('other')
        payload=self.payload();payload['scope']['formId']=other.pk
        self.post(payload)
        self.loop.refresh_from_db()
        before=MarketingRuntimeBinding.objects.get(loop=self.loop).draft_json
        current=self.runtime().runtime_snapshot(self.loop)
        with self.assertRaisesMessage(ValidationError,'另一张表单'):
            self.act('rollback',targetRevision=active['activeRevision']['key'],configHash=current['configHash'],expectedFlowVersion=current['flowVersion'])
        self.assertEqual(MarketingRuntimeBinding.objects.get(loop=self.loop).draft_json,before)
