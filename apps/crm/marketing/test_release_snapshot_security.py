from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from unittest.mock import patch

from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase

from marketing.management.commands.export_release_snapshot import Command
from marketing.models import (
    CanonicalEvent,
    MarketingIntegration,
    MarketingProvider,
    ProviderEventMapping,
    TrackingRule,
)
from sitecore.models import Release, Site


class ReleaseSnapshotSecurityTests(TestCase):
    unmanaged_models = (
        Site,
        MarketingProvider,
        MarketingIntegration,
        CanonicalEvent,
        TrackingRule,
        ProviderEventMapping,
        Release,
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
        self.test_root = Path(__file__).resolve().parent / '.test-release-snapshot'
        self.test_root.mkdir(exist_ok=True)
        self.root = (self.test_root / secrets.token_hex(12)).resolve()
        self.root.mkdir()
        for site_code in ('alpha', 'beta'):
            (self.root / site_code / 'apps' / 'web').mkdir(parents=True)
        self.alpha = self._site('alpha')
        self.beta = self._site('beta')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _site(self, code: str) -> Site:
        return Site.objects.create(
            code=code,
            name=code.title(),
            base_url=f'https://{code}.example.invalid',
            default_locale='en',
            enabled=True,
            config_json={},
        )

    def _output(self, site_code: str) -> Path:
        return self.root / site_code / 'apps' / 'web' / 'release' / 'current'

    def _snapshot_patches(self, *, articles=None):
        empty_articles = {'articles': []} if articles is None else articles
        return (
            patch.object(Command, 'build_site_snapshot', return_value={}),
            patch.object(Command, 'build_routes_snapshot', return_value={}),
            patch.object(Command, 'build_pages_snapshot', return_value={}),
            patch.object(Command, 'build_articles_snapshot', return_value=empty_articles),
            patch.object(Command, 'build_assets_snapshot', return_value={}),
            patch.object(Command, 'build_navigation_snapshot', return_value={}),
            patch.object(Command, 'build_components_snapshot', return_value={}),
            patch.object(Command, 'build_ctas_snapshot', return_value={}),
            patch.object(Command, 'build_marketing_snapshot', return_value={}),
        )

    def _run_command(self, *, site_code='alpha', release_id='preview:alpha', output_dir=None, articles=None):
        contexts = self._snapshot_patches(articles=articles)
        with self.settings(SITEOS_PREVIEW_WORKSPACE_ROOT=str(self.root)):
            with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], contexts[6], contexts[7], contexts[8]:
                return Command().handle(
                    output_dir=str(output_dir or self._output(site_code)),
                    release_id=release_id,
                    site_code=site_code,
                    created_by='tester',
                )

    def test_marketing_snapshot_contains_only_integrations_and_events_used_by_site(self):
        provider = MarketingProvider.objects.create(code='meta', name='Meta', enabled=True, capabilities={})
        alpha_event = CanonicalEvent.objects.create(code='alpha_event', name='Alpha event', description='')
        beta_event = CanonicalEvent.objects.create(code='beta_event', name='Beta event', description='')
        ProviderEventMapping.objects.create(
            provider=provider,
            canonical_event=alpha_event,
            provider_event_name='AlphaEvent',
            enabled=True,
            config_json={},
        )
        ProviderEventMapping.objects.create(
            provider=provider,
            canonical_event=beta_event,
            provider_event_name='BetaEvent',
            enabled=True,
            config_json={},
        )
        alpha_integration = MarketingIntegration.objects.create(
            site=self.alpha,
            provider=provider,
            name='Alpha pixel',
            integration_type='pixel',
            public_id='123456',
            consent_category='marketing',
            enabled=True,
            config_json={},
        )
        beta_integration = MarketingIntegration.objects.create(
            site=self.beta,
            provider=provider,
            name='Beta pixel',
            integration_type='pixel',
            public_id='654321',
            consent_category='marketing',
            enabled=True,
            config_json={},
        )
        TrackingRule.objects.create(
            integration=alpha_integration,
            canonical_event=alpha_event,
            scope_type='global',
            scope_value='*',
            priority=1,
            enabled=True,
            config_json={},
        )
        TrackingRule.objects.create(
            integration=beta_integration,
            canonical_event=beta_event,
            scope_type='global',
            scope_value='*',
            priority=1,
            enabled=True,
            config_json={},
        )

        snapshot = Command().build_marketing_snapshot(self.alpha, '2026-08-30T00:00:00+00:00')

        pixels = snapshot['integrations']['metaPixels']
        self.assertEqual([pixel['id'] for pixel in pixels], [alpha_integration.id])
        self.assertEqual(snapshot['eventMappings']['meta'], {'alpha_event': 'AlphaEvent'})
        self.assertNotIn('654321', str(snapshot))
        self.assertNotIn('BetaEvent', str(snapshot))

    def test_release_key_owned_by_other_site_is_rejected_without_moving_row(self):
        release = Release.objects.create(
            site=self.beta,
            release_key='shared-preview',
            status='draft',
            snapshot_manifest={},
        )

        with self.assertRaises(CommandError) as raised:
            self._run_command(release_id='shared-preview')

        release.refresh_from_db()
        self.assertEqual(release.site_id, self.beta.id)
        self.assertEqual(release.status, 'draft')
        self.assertNotIn(str(self.root), str(raised.exception))

    def test_same_site_release_is_updated_in_place_after_successful_export(self):
        release = Release.objects.create(
            site=self.alpha,
            release_key='preview:alpha',
            status='draft',
            snapshot_manifest={},
        )

        self._run_command()

        release.refresh_from_db()
        self.assertEqual(release.site_id, self.alpha.id)
        self.assertEqual(release.status, 'exported')
        self.assertEqual(release.snapshot_manifest['siteCode'], 'alpha')
        self.assertTrue((self._output('alpha') / 'manifest.json').is_file())

    def test_output_must_be_explicit_and_inside_the_requested_site_tree(self):
        with self.assertRaises(CommandError) as raised:
            self._run_command(output_dir=self._output('beta'))

        self.assertEqual(Release.objects.count(), 0)
        self.assertNotIn(str(self._output('beta')), str(raised.exception))

    def test_release_key_and_article_filename_traversal_are_rejected(self):
        with self.assertRaises(CommandError):
            self._run_command(release_id='../escape')
        self.assertEqual(Release.objects.count(), 0)

        articles = {'articles': [{'slug': '../../escape'}]}
        with self.assertRaises(CommandError) as raised:
            self._run_command(release_id='preview:alpha', articles=articles)

        self.assertFalse(Release.objects.filter(release_key='preview:alpha').exists())
        self.assertFalse((self.root / 'escape.json').exists())
        self.assertNotIn('../../escape', str(raised.exception))
