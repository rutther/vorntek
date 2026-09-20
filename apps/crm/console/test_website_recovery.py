from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from console.article_delivery import ArticleDeliveryError
from console.models import (
    WebsiteDeploymentOperation,
    WebsiteReleaseDeployment,
    WebsiteReleaseSelection,
)
from console.website_recovery import reconcile_website_serving_cache
from sitecore.models import Release, Site


class WebsiteRecoveryTests(TestCase):
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
        self.version = '1' * 64
        self.site = Site.objects.create(
            code='recovery-site',
            name='Recovery site',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.release = Release.objects.create(
            site=self.site,
            release_key='website-candidate:recovery-site:1',
            status='built',
            snapshot_manifest={
                'kind': 'websiteCandidate',
                'scope': 'wholeSite',
                'version': self.version,
                'articleVersion': 'a' * 64,
                'baseSourceVersion': 'b' * 64,
                'fileCount': 1,
            },
        )
        self.selection = WebsiteReleaseSelection.objects.create(
            request_token=uuid4(),
            site=self.site,
            release=self.release,
            version=self.version,
            previous_version='',
            selected_by='selector',
            reason='Selected verified candidate for deployment.',
        )
        token = uuid4()
        self.operation = WebsiteDeploymentOperation.objects.create(
            request_token=token,
            site=self.site,
            selection=self.selection,
            release=self.release,
            version=self.version,
            previous_version='',
            deployed_by='operator',
            reason='Deployed verified candidate before recovery.',
            status='activated',
            completed_at=timezone.now(),
        )
        self.receipt = WebsiteReleaseDeployment.objects.create(
            operation=self.operation,
            request_token=token,
            site=self.site,
            selection=self.selection,
            release=self.release,
            version=self.version,
            previous_version='',
            deployed_by='operator',
            reason='Deployed verified candidate before recovery.',
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
        self.source.verify.return_value = self.source_manifest
        self.serving = MagicMock()
        self.serving.current.return_value = ''

    def reconcile(self, *, apply=False, deployment_id=None, serving_version=None):
        with (
            patch(
                'console.website_recovery.review_website_candidate',
                return_value=(self.release, self.release.snapshot_manifest),
            ),
            patch(
                'console.website_recovery.website_release_store',
                return_value=self.source,
            ),
            patch(
                'console.website_recovery.website_serving_store',
                return_value=self.serving,
            ),
        ):
            return reconcile_website_serving_cache(
                site=self.site,
                apply=apply,
                expected_deployment_id=deployment_id,
                expected_serving_version=serving_version,
            )

    def test_plan_verifies_durable_evidence_without_switch_or_database_write(self):
        result = self.reconcile()

        self.assertEqual(result['result'], 'plan_only')
        self.assertEqual(result['action'], 'rebuild_required')
        self.assertEqual(result['deploymentId'], self.receipt.pk)
        self.serving.deploy.assert_not_called()
        self.assertEqual(WebsiteReleaseDeployment.objects.count(), 1)
        self.assertEqual(WebsiteDeploymentOperation.objects.count(), 1)

    def test_apply_rebuilds_baseline_without_appending_a_fake_deployment(self):
        result = self.reconcile(
            apply=True,
            deployment_id=self.receipt.pk,
            serving_version='',
        )

        self.assertEqual(result['action'], 'rebuilt')
        self.assertTrue(result['changed'])
        self.serving.deploy.assert_called_once_with(
            self.source,
            self.version,
            expected='',
        )
        self.assertEqual(WebsiteReleaseDeployment.objects.count(), 1)
        self.assertEqual(WebsiteDeploymentOperation.objects.count(), 1)

    def test_matching_cache_is_verified_idempotently(self):
        self.serving.current.return_value = self.version

        result = self.reconcile(
            apply=True,
            deployment_id=self.receipt.pk,
            serving_version=self.version,
        )

        self.assertEqual(result['action'], 'verified')
        self.assertFalse(result['changed'])
        self.serving.deploy.assert_not_called()
        self.serving.verify.assert_called_once_with(
            self.version,
            expected_manifest=self.source_manifest,
        )

    def test_stale_expectations_and_prepared_operation_fail_before_switch(self):
        with self.assertRaisesRegex(
            ArticleDeliveryError,
            'website_reconcile_deployment_changed',
        ):
            self.reconcile(
                apply=True,
                deployment_id=self.receipt.pk + 1,
                serving_version='',
            )
        self.serving.deploy.assert_not_called()

        WebsiteDeploymentOperation.objects.create(
            request_token=uuid4(),
            site=self.site,
            selection=self.selection,
            release=self.release,
            version=self.version,
            previous_version=self.version,
            deployed_by='operator',
            reason='Prepared operation must block cache reconstruction.',
        )
        with self.assertRaisesRegex(
            ArticleDeliveryError,
            'website_reconcile_deployment_pending',
        ):
            self.reconcile()
        self.serving.deploy.assert_not_called()

    def test_ledger_mismatch_is_not_treated_as_recoverable_cache_loss(self):
        WebsiteDeploymentOperation.objects.filter(pk=self.operation.pk).update(
            deployed_by='different-operator'
        )

        with self.assertRaisesRegex(
            ArticleDeliveryError,
            'website_reconcile_ledger_mismatch',
        ):
            self.reconcile()
        self.source.verify.assert_not_called()
        self.serving.deploy.assert_not_called()
