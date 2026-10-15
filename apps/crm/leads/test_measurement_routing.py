import json
import uuid
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.db import connection
from console.test_public_lead_capture import PublicLeadCaptureVerticalTests as Fixture
from console.flow_workspace_service import create_flow, mutate_flow
from marketing.flow_runtime import runtime_command, runtime_snapshot
from marketing.public_config import public_measurement_config
from marketing import models as mm
from leads import models as lm


@override_settings(SITEOS_META_RUNTIME_ENABLED=True, SITEOS_META_RUNTIME_ENVIRONMENT='production',
                   SITEOS_META_RUNTIME_LOADER_VERSION=2)
class MeasurementRoutingTests(TestCase):
    unmanaged_models = Fixture.unmanaged_models + (mm.MarketingLoop, mm.MarketingLoopNode,
        mm.MarketingLoopEdge, mm.MarketingLoopTask, mm.MarketingLoopAssetBinding,
        mm.MarketingRuntimeBinding, mm.MarketingRuntimeRevision, mm.MarketingRuntimeCommand, mm.IntegrationCheck) + tuple(
        [lm.LeadMeasurementRoute] if hasattr(lm, 'LeadMeasurementRoute') else [])
    setUpClass = classmethod(lambda cls: cls.setup_tables())

    @classmethod
    def setup_tables(cls):
        cls.created_models = []
        existing = set(connection.introspection.table_names())
        with connection.schema_editor() as editor:
            for model in cls.unmanaged_models:
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

    valid_payload = Fixture.valid_payload

    def setUp(self):
        Fixture.setUp(self)
        self.provider = mm.MarketingProvider.objects.create(code='meta', name='Meta')
        self.legacy = mm.MarketingIntegration.objects.create(site=self.site, provider=self.provider,
            name='Legacy synthetic', integration_type='pixel', public_id='111111111', enabled=True,
            routing_mode='legacy_site', environment='production')
        self.loop = create_flow(site=self.site, actor=self.admin,
            data={'name':'New synthetic', 'environment':'production', 'requestId':str(uuid.uuid4())})
        self.loop = mutate_flow(site=self.site, actor=self.admin, key=self.loop.loop_key,
            data={'action':'save_config','nodeKey':'dataset','expectedVersion':1,
                  'requestId':str(uuid.uuid4()),'values':{'source':'created','externalId':'222222222'}})
        self.act('prepare_integration')
        self.act('save_draft', draft={'landingPaths':['/'], 'leadPaths':['/'],
            'formId':self.form.pk,'consentRequired':True})
        plan = runtime_snapshot(self.loop)
        with patch('marketing.flow_runtime.credential_available', return_value=True):
            self.act('apply', configHash=plan['configHash'], expectedFlowVersion=plan['flowVersion'])

    def act(self, action, **values):
        return runtime_command(loop=self.loop, actor=self.admin, data={'action':action,
            'expectedVersion':runtime_snapshot(self.loop)['version'], 'requestId':str(uuid.uuid4()), **values})

    def submit(self, payload):
        with patch('leads.notifications.send_submission_notification',return_value=False), patch('leads.services.dispatch_outbox_event',return_value=False):
            response = self.client.post(self.submit_url, data=json.dumps(payload), content_type='application/json',
                HTTP_HOST='example.test', HTTP_USER_AGENT='isolated synthetic test')
        self.assertEqual(response.status_code, 200, response.content[:500])
        return response.json()

    def payload(self):
        data = self.valid_payload(marketing=True)
        data['client_event_id'] = str(uuid.uuid4())
        data['measurement_context'] = public_measurement_config(site=self.site, path='/')['measurementContext']
        return data

    def test_lost_response_retry_gets_same_receipt_without_second_lead(self):
        payload = self.payload()
        first, retry = self.submit(payload), self.submit(payload)
        self.assertIn('measurement', first, 'Successful submission must return authoritative routing receipt')
        self.assertTrue(retry['duplicate'])
        self.assertEqual(first['measurement']['eventId'], payload['client_event_id'])
        self.assertEqual(retry['measurement'], first['measurement'])
        self.assertEqual(set(first['measurement']['pixelIds']), {'111111111','222222222'})
        self.assertEqual(lm.LeadSubmission.objects.count(), 1)
        self.assertEqual(lm.LeadEventOutbox.objects.count(), 2)

    def test_business_duplicate_does_not_emit_another_browser_event(self):
        payload = self.payload()
        self.submit(payload)
        payload['client_event_id'] = str(uuid.uuid4())
        second = self.submit(payload)
        self.assertFalse(second['measurement']['shouldTrack'])
        self.assertEqual(lm.LeadEventOutbox.objects.count(), 2)

    def test_same_attempt_survives_business_dedupe_window(self):
        from datetime import timedelta
        from django.utils import timezone
        payload = self.payload()
        first = self.submit(payload)
        lm.LeadSubmission.objects.update(submitted_at=timezone.now()-timedelta(days=2))
        retry = self.submit(payload)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['measurement']['eventId'], first['measurement']['eventId'])
        self.assertEqual(lm.LeadSubmission.objects.count(), 1)

    def test_snapshot_event_contract_survives_form_edit(self):
        self.submit(self.payload())
        self.form.submission_event_name = 'Contact'
        self.form.save()
        from leads.services import queue_submission_event
        queue_submission_event(lm.LeadSubmission.objects.get(), stage_key='submitted',event_name='Contact')
        self.assertEqual(lm.LeadEventOutbox.objects.count(), 2)

    def test_missing_context_routes_only_legacy(self):
        payload = self.payload()
        payload.pop('measurement_context')
        result = self.submit(payload)
        self.assertEqual(result['measurement']['pixelIds'], ['111111111'])
        self.assertEqual(lm.LeadEventOutbox.objects.count(), 1)

    def test_silent_deployment_never_authorizes_browser_tracking(self):
        payload = self.payload()
        with override_settings(NEWCROWN_ALLOW_EXTERNAL_IO=False):
            result = self.submit(payload)
        self.assertFalse(result['measurement']['shouldTrack'])
        self.assertEqual(result['measurement']['pixelIds'], [])
        self.assertEqual(result['measurement']['revisionKeys'], [])
        self.assertEqual(lm.LeadSubmission.objects.count(), 1)

    def test_pause_blocks_new_and_pending_without_retarget(self):
        payload = self.payload()
        self.submit(payload)
        self.act('pause')
        from leads.measurement_routing import integration_sendable
        submission = lm.LeadSubmission.objects.get()
        targets = list(lm.LeadMeasurementRoute.objects.filter(submission=submission).select_related('integration'))
        self.assertEqual([t.pixel_id for t in targets if integration_sendable(submission,t.integration,'submitted')], ['111111111'])
        result = self.submit(payload)
        self.assertEqual(result['measurement']['pixelIds'], ['111111111'])

    def test_new_pixel_never_receives_later_crm_stages(self):
        self.submit(self.payload())
        from leads.measurement_routing import select_meta_integrations
        submission = lm.LeadSubmission.objects.get()
        self.assertEqual([i.public_id for i in select_meta_integrations(submission,'qualified')], ['111111111'])

    def test_shared_pixel_routes_once_and_survives_one_flow_pause(self):
        # Catches routing per binding (duplicates) or pause disabling a shared integration.
        other = create_flow(site=self.site, actor=self.admin, data={
            'name':'Second synthetic flow', 'environment':'production', 'requestId':str(uuid.uuid4())})
        other = mutate_flow(site=self.site, actor=self.admin, key=other.loop_key, data={
            'action':'save_config', 'nodeKey':'dataset', 'expectedVersion':1,
            'requestId':str(uuid.uuid4()), 'values':{'source':'existing','externalId':'222222222'}})

        def other_action(action, **values):
            return runtime_command(loop=other, actor=self.admin, data={'action':action,
                'requestId':str(uuid.uuid4()), 'expectedVersion':runtime_snapshot(other)['version'], **values})

        other_action('prepare_integration')
        other_action('save_draft', draft={'landingPaths':['/'], 'leadPaths':['/'],
            'formId':self.form.pk, 'consentRequired':True})
        plan = runtime_snapshot(other)
        with patch('marketing.flow_runtime.credential_available', return_value=True):
            other_action('apply', configHash=plan['configHash'], expectedFlowVersion=plan['flowVersion'])
        response = self.submit(self.payload())
        self.assertEqual(response['measurement']['pixelIds'], ['111111111','222222222'])
        self.assertEqual(lm.LeadSubmission.objects.count(), 1)
        self.assertEqual(lm.LeadEventOutbox.objects.count(), 2)
        route = lm.LeadMeasurementRoute.objects.get(pixel_id='222222222')
        self.assertEqual(len(route.revision_keys), 2)
        from leads.measurement_routing import select_meta_integrations
        submission = lm.LeadSubmission.objects.get()
        self.act('pause')
        self.assertEqual({i.public_id for i in select_meta_integrations(submission,'submitted')},
            {'111111111','222222222'})
        other_action('pause')
        self.assertEqual([i.public_id for i in select_meta_integrations(submission,'submitted')], ['111111111'])
        self.assertTrue(mm.MarketingIntegration.objects.get(public_id='222222222').enabled)

    def test_withdrawn_consent_skips_already_queued_events(self):
        # Catches dispatch trusting an earlier route snapshot instead of current consent.
        self.submit(self.payload())
        submission = lm.LeadSubmission.objects.get()
        submission.consent_json = {**submission.consent_json, 'marketing':False}
        submission.save(update_fields=['consent_json'])
        from leads.services import dispatch_outbox_event
        for outbox in lm.LeadEventOutbox.objects.all():
            self.assertFalse(dispatch_outbox_event(outbox))
            outbox.refresh_from_db()
            self.assertEqual(outbox.status, 'skipped')
            self.assertEqual(outbox.attempts, 0)

    def test_denied_consent_accepts_inquiry_without_targets_or_later_retargeting(self):
        # Catches empty target snapshots falling back to all enabled integrations.
        payload = self.payload()
        payload['consent']['marketing'] = False
        response = self.submit(payload)
        self.assertFalse(response['measurement']['shouldTrack'])
        self.assertEqual(lm.LeadSubmission.objects.count(), 1)
        self.assertEqual(lm.LeadMeasurementRoute.objects.count(), 0)
        self.assertEqual(lm.LeadEventOutbox.objects.count(), 0)
        submission = lm.LeadSubmission.objects.get()
        submission.consent_json = {**submission.consent_json, 'marketing':True}
        submission.save(update_fields=['consent_json'])
        from leads.measurement_routing import select_meta_integrations
        self.assertEqual(select_meta_integrations(submission,'submitted'), [])

    def test_old_page_context_cannot_join_a_reapplied_generation(self):
        # Catches recomputing targets from current config instead of intersecting page consent.
        payload = self.payload()
        plan = runtime_snapshot(self.loop)
        with patch('marketing.flow_runtime.credential_available', return_value=True):
            self.act('apply', configHash=plan['configHash'], expectedFlowVersion=plan['flowVersion'])
        response = self.submit(payload)
        self.assertEqual(response['measurement']['pixelIds'], ['111111111'])
        self.assertEqual(lm.LeadEventOutbox.objects.count(), 1)

    def test_archived_flow_removes_new_pixel_without_disabling_legacy(self):
        # Catches archived flows remaining in effective runtime revisions.
        payload = self.payload()
        self.loop.status = 'archived'
        self.loop.save(update_fields=['status'])
        response = self.submit(payload)
        self.assertEqual(response['measurement']['pixelIds'], ['111111111'])
        self.assertEqual(lm.LeadEventOutbox.objects.count(), 1)

    def test_history_recovery_does_not_replay_or_retarget_existing_events(self):
        self.submit(self.payload())
        original = runtime_snapshot(self.loop)
        old_routes = list(lm.LeadMeasurementRoute.objects.order_by('pk').values())
        old_outbox = list(lm.LeadEventOutbox.objects.order_by('pk').values())
        self.act('save_draft', draft={**original['draft'], 'landingPaths':['/', '/contact/']})
        current = runtime_snapshot(self.loop)
        with patch('marketing.flow_runtime.credential_available', return_value=True), \
                patch('leads.services.dispatch_outbox_event') as dispatch:
            self.act('apply', configHash=current['configHash'], expectedFlowVersion=current['flowVersion'])
            current = runtime_snapshot(self.loop)
            recovered = self.act('rollback', configHash=current['configHash'],
                expectedFlowVersion=current['flowVersion'], targetRevision=original['activeRevision']['key'])
            dispatch.assert_not_called()
        self.assertNotEqual(recovered['activeRevision']['key'], original['activeRevision']['key'])
        self.assertEqual(recovered['activeRevision']['scope'], original['activeRevision']['scope'])
        self.assertEqual(list(lm.LeadMeasurementRoute.objects.order_by('pk').values()), old_routes)
        self.assertEqual(list(lm.LeadEventOutbox.objects.order_by('pk').values()), old_outbox)
