from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.db import connection
from django.test import TestCase

from console.article_delivery import ArticleDeliveryError
from console.models import (
    WebsiteDeploymentOperation,
    WebsiteReleaseDeployment,
    WebsiteReleaseSelection,
)
from console.website_deployment import deploy_selected_website
from console.website_selection import select_website_candidate
from sitecore.models import Release, Site


class FakeServingStore:
    def __init__(self, manifest, *, current=''):
        self.manifest = manifest
        self.current_value = current
        self.deploy_calls = []
        self.fail_before_switch = False

    def current(self):
        return self.current_value

    def deploy(self, source, version, *, expected):
        self.deploy_calls.append((version, expected))
        if self.fail_before_switch:
            raise ArticleDeliveryError('website_deployment_copy_failed')
        if self.current_value != expected:
            raise ArticleDeliveryError('website_deployed_version_changed')
        source.verify(version)
        self.current_value = version
        return {'version': version, 'previous': expected, 'changed': version != expected}

    def verify(self, version, *, expected_manifest=None):
        if version != self.manifest['version']:
            raise ArticleDeliveryError('website_deployment_artifact_unavailable')
        if expected_manifest is not None and expected_manifest != self.manifest['source']:
            raise ArticleDeliveryError('website_deployment_source_mismatch')
        return self.manifest


class WebsiteDeploymentTests(TestCase):
    unmanaged_models = (
        Site,
        Release,
        WebsiteReleaseSelection,
        WebsiteDeploymentOperation,
        WebsiteReleaseDeployment,
    )

    @classmethod
    def setUpClass(cls):
        existing = set(connection.introspection.table_names())
        cls.created_models = []
        try:
            with connection.schema_editor() as editor:
                for model in cls.unmanaged_models:
                    if model._meta.db_table not in existing:
                        editor.create_model(model)
                        cls.created_models.append(model)
                        existing.add(model._meta.db_table)
            super().setUpClass()
        except Exception:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            with connection.schema_editor() as editor:
                for model in reversed(cls.created_models):
                    editor.delete_model(model)

    def setUp(self):
        self.site = Site.objects.create(
            code='deploy-site',
            name='Deployment site',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.release = Release.objects.create(
            site=self.site,
            release_key='website-candidate:deploy-site:1',
            status='built',
            created_by='builder',
            notes='Immutable whole-site candidate.',
            snapshot_manifest={
                'kind': 'websiteCandidate',
                'scope': 'wholeSite',
                'version': '1' * 64,
                'articleVersion': 'a' * 64,
                'baseSourceVersion': 'b' * 64,
                'fileCount': 1,
            },
        )
        self.selection = WebsiteReleaseSelection.objects.create(
            request_token=uuid4(),
            site=self.site,
            release=self.release,
            version='1' * 64,
            previous_version='',
            selected_by='selector',
            reason='Reviewed candidate selection for deployment.',
        )
        self.source_manifest = {
            'schemaVersion': 1,
            'kind': 'websiteRelease',
            'siteCode': self.site.code,
            'baseSourceSha256': 'b' * 64,
            'articleVersion': 'a' * 64,
            'files': {'index.html': 'c' * 64},
        }
        self.source = MagicMock()
        self.source.current.return_value = ''
        self.source.verify.return_value = self.source_manifest
        self.serving_manifest = {
            'version': '1' * 64,
            'source': self.source_manifest,
        }
        self.serving = FakeServingStore(self.serving_manifest)

    def deploy(self, *, token=None, selection_id=None, expected='', actor='operator'):
        with (
            patch('console.website_selection.website_release_store', return_value=self.source),
            patch('console.website_deployment.website_release_store', return_value=self.source),
            patch('console.website_deployment.website_serving_store', return_value=self.serving),
        ):
            return deploy_selected_website(
                site=self.site,
                expected_selection_id=selection_id or self.selection.pk,
                expected_deployed_version=expected,
                request_token=token or uuid4(),
                actor=actor,
                reason='Approved selected website for controlled deployment.',
            )

    def test_current_selection_is_verified_switched_and_receipted_once(self):
        result = self.deploy()

        operation = WebsiteDeploymentOperation.objects.get()
        receipt = WebsiteReleaseDeployment.objects.get()
        operation.refresh_from_db()
        self.assertEqual(operation.status, 'activated')
        self.assertEqual(receipt.operation_id, operation.pk)
        self.assertEqual(receipt.selection_id, self.selection.pk)
        self.assertEqual(receipt.version, '1' * 64)
        self.assertEqual(receipt.previous_version, '')
        self.assertEqual(result['deploymentId'], receipt.pk)
        self.assertEqual(self.serving.current(), '1' * 64)
        self.assertEqual(self.serving.deploy_calls, [('1' * 64, '')])
        self.assertEqual(self.release.status, 'built')

    def test_activated_request_replay_is_idempotent_and_cannot_be_rebound(self):
        token = uuid4()
        first = self.deploy(token=token)
        replay = self.deploy(token=token)

        self.assertEqual(first['deploymentId'], replay['deploymentId'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(WebsiteReleaseDeployment.objects.count(), 1)
        self.assertEqual(len(self.serving.deploy_calls), 1)
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'website_candidate_already_deployed'
        ):
            self.deploy(expected='1' * 64)
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_deployment_request_reused'):
            self.deploy(token=token, actor='different-operator')

    def test_crash_after_pointer_switch_resumes_same_operation_and_receipt(self):
        token = uuid4()
        with patch('console.website_deployment._finalize', side_effect=RuntimeError('synthetic')):
            with self.assertRaisesRegex(
                ArticleDeliveryError, 'website_deployment_recovery_required'
            ):
                self.deploy(token=token)

        operation = WebsiteDeploymentOperation.objects.get()
        self.assertEqual(operation.status, 'prepared')
        self.assertFalse(WebsiteReleaseDeployment.objects.exists())
        self.assertEqual(self.serving.current(), '1' * 64)

        resumed = self.deploy(token=token)
        operation.refresh_from_db()
        self.assertEqual(operation.status, 'activated')
        self.assertEqual(WebsiteReleaseDeployment.objects.count(), 1)
        self.assertEqual(resumed['operationId'], operation.pk)
        self.assertEqual(len(self.serving.deploy_calls), 1)

    def test_failure_before_switch_is_terminal_and_allows_a_new_request(self):
        token = uuid4()
        self.serving.fail_before_switch = True
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_deployment_copy_failed'):
            self.deploy(token=token)

        operation = WebsiteDeploymentOperation.objects.get()
        self.assertEqual(operation.status, 'failed')
        self.assertEqual(operation.error_code, 'website_deployment_copy_failed')
        self.assertFalse(WebsiteReleaseDeployment.objects.exists())
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_deployment_request_failed'):
            self.deploy(token=token)

        self.serving.fail_before_switch = False
        result = self.deploy()
        self.assertEqual(result['version'], '1' * 64)

    def test_stale_selection_and_deployed_precondition_fail_before_switch(self):
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_deployment_selection_changed'):
            self.deploy(selection_id=self.selection.pk + 100)
        self.assertFalse(WebsiteDeploymentOperation.objects.exists())

        with self.assertRaisesRegex(ArticleDeliveryError, 'website_deployment_changed'):
            self.deploy(expected='f' * 64)
        self.assertFalse(WebsiteDeploymentOperation.objects.exists())
        self.assertEqual(self.serving.deploy_calls, [])

    def test_update_and_rollback_follow_selection_and_deployment_chains(self):
        first = self.deploy()
        second_release = Release.objects.create(
            site=self.site,
            release_key='website-candidate:deploy-site:2',
            status='built',
            created_by='builder',
            snapshot_manifest={
                'kind': 'websiteCandidate',
                'scope': 'wholeSite',
                'version': '2' * 64,
                'articleVersion': 'd' * 64,
                'baseSourceVersion': 'e' * 64,
                'fileCount': 1,
            },
        )
        second_selection = WebsiteReleaseSelection.objects.create(
            request_token=uuid4(),
            site=self.site,
            release=second_release,
            version='2' * 64,
            previous_version='1' * 64,
            selected_by='selector',
            reason='Select the reviewed updated website candidate.',
        )
        second_manifest = {
            'schemaVersion': 1,
            'kind': 'websiteRelease',
            'siteCode': self.site.code,
            'baseSourceSha256': 'e' * 64,
            'articleVersion': 'd' * 64,
            'files': {'index.html': 'f' * 64},
        }
        self.source.verify.return_value = second_manifest
        self.serving.manifest = {'version': '2' * 64, 'source': second_manifest}
        second = self.deploy(
            selection_id=second_selection.pk,
            expected='1' * 64,
        )

        rollback_selection = WebsiteReleaseSelection.objects.create(
            request_token=uuid4(),
            site=self.site,
            release=self.release,
            version='1' * 64,
            previous_version='2' * 64,
            selected_by='selector',
            reason='Rollback to the previously verified website candidate.',
        )
        self.source.verify.return_value = self.source_manifest
        self.serving.manifest = self.serving_manifest
        rollback = self.deploy(
            selection_id=rollback_selection.pk,
            expected='2' * 64,
        )

        chain = list(
            WebsiteReleaseDeployment.objects.order_by('id').values_list(
                'version', 'previous_version', 'selection_id'
            )
        )
        self.assertEqual(
            chain,
            [
                ('1' * 64, '', self.selection.pk),
                ('2' * 64, '1' * 64, second_selection.pk),
                ('1' * 64, '2' * 64, rollback_selection.pk),
            ],
        )
        self.assertEqual(first['previous'], '')
        self.assertEqual(second['previous'], '1' * 64)
        self.assertEqual(rollback['previous'], '2' * 64)
        self.assertEqual(self.serving.current(), '1' * 64)

    def test_prepared_deployment_blocks_selection_and_other_deployment(self):
        operation = WebsiteDeploymentOperation.objects.create(
            request_token=uuid4(),
            site=self.site,
            selection=self.selection,
            release=self.release,
            version='1' * 64,
            previous_version='',
            deployed_by='operator',
            reason='Prepared operation awaiting safe recovery.',
        )
        other = Release.objects.create(
            site=self.site,
            release_key='website-candidate:deploy-site:2',
            status='built',
            created_by='builder',
            snapshot_manifest={
                'kind': 'websiteCandidate',
                'scope': 'wholeSite',
                'version': '2' * 64,
                'articleVersion': 'a' * 64,
                'baseSourceVersion': 'b' * 64,
                'fileCount': 1,
            },
        )
        other_source = MagicMock()
        other_source.current.return_value = ''
        other_source.verify.return_value = {
            **self.source_manifest,
            'files': {'index.html': 'd' * 64},
        }
        with patch('console.website_selection.website_release_store', return_value=other_source):
            with self.assertRaisesRegex(ArticleDeliveryError, 'website_deployment_in_progress'):
                select_website_candidate(
                    site=self.site,
                    record_id=other.pk,
                    expected_version='1' * 64,
                    request_token=uuid4(),
                    actor='selector',
                    reason='Select another reviewed candidate safely.',
                )
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_deployment_in_progress'):
            self.deploy(token=uuid4())
        self.assertEqual(WebsiteDeploymentOperation.objects.get().pk, operation.pk)

    def test_receipt_model_refuses_update_and_delete(self):
        self.deploy()
        receipt = WebsiteReleaseDeployment.objects.get()
        receipt.reason = 'Changed reason must never be written.'
        with self.assertRaisesRegex(ValueError, 'append-only'):
            receipt.save()
        with self.assertRaisesRegex(ValueError, 'append-only'):
            receipt.delete()
