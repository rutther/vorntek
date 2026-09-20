from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.db import connection
from django.test import TestCase

from console.article_delivery import ArticleDeliveryError
from console.models import WebsiteReleaseSelection
from console.website_selection import (
    current_website_selection,
    select_website_candidate,
    website_candidate_for_selection,
)
from sitecore.models import Release, Site


class WebsiteSelectionTests(TestCase):
    unmanaged_models = (Site, Release, WebsiteReleaseSelection)

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
            code='selection-site',
            name='Selection site',
            default_locale='en',
            enabled=True,
            config_json={},
        )
        self.other_site = Site.objects.create(
            code='selection-other',
            name='Other site',
            default_locale='en',
            enabled=True,
            config_json={},
        )

    def candidate(self, marker: str, *, site=None) -> Release:
        version = marker * 64
        return Release.objects.create(
            site=site or self.site,
            release_key=f'website-candidate:{(site or self.site).pk}:{marker}',
            status='built',
            created_by='builder',
            notes='Immutable whole-site candidate.',
            snapshot_manifest={
                'kind': 'websiteCandidate',
                'scope': 'wholeSite',
                'version': version,
                'articleVersion': 'a' * 64,
                'baseSourceVersion': 'b' * 64,
                'fileCount': 1,
            },
        )

    @staticmethod
    def store_for(version: str):
        store = MagicMock()
        store.current.return_value = ''
        store.verify.return_value = {
            'kind': 'websiteRelease',
            'articleVersion': 'a' * 64,
            'baseSourceSha256': 'b' * 64,
            'files': {'index.html': version},
        }
        return store

    def select(self, candidate, *, expected='', token=None, actor='operator', reason=None):
        version = candidate.snapshot_manifest['version']
        store = self.store_for(version)
        with patch('console.website_selection.website_release_store', return_value=store):
            result = select_website_candidate(
                site=self.site,
                record_id=candidate.pk,
                expected_version=expected,
                request_token=token or uuid4(),
                actor=actor,
                reason=reason or 'Reviewed immutable candidate for controlled rollout.',
            )
        return result, store

    def test_selection_appends_receipt_without_activating_or_deploying(self):
        candidate = self.candidate('1')

        result, store = self.select(candidate)

        selection = WebsiteReleaseSelection.objects.get()
        self.assertEqual(result['selectionId'], selection.pk)
        self.assertTrue(result['changed'])
        self.assertFalse(result['replayed'])
        self.assertEqual(selection.release_id, candidate.pk)
        self.assertEqual(selection.version, '1' * 64)
        self.assertEqual(selection.previous_version, '')
        self.assertEqual(candidate.status, 'built')
        self.assertEqual(store.current.call_count, 2)
        store.verify.assert_called_once_with('1' * 64)
        self.assertFalse(hasattr(store, 'activate') and store.activate.called)

    def test_stale_expected_version_and_already_selected_version_fail_closed(self):
        first = self.candidate('2')
        self.select(first)
        second = self.candidate('3')

        with self.assertRaisesRegex(ArticleDeliveryError, 'website_selection_changed'):
            self.select(second, expected='')
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'website_candidate_already_selected'
        ):
            self.select(first, expected='2' * 64)
        self.assertEqual(WebsiteReleaseSelection.objects.count(), 1)

    def test_selection_supports_update_and_rollback_as_distinct_events(self):
        first = self.candidate('4')
        second = self.candidate('5')

        self.select(first)
        self.select(second, expected='4' * 64)
        rollback, _store = self.select(first, expected='5' * 64)

        chain = list(
            WebsiteReleaseSelection.objects.order_by('id').values_list(
                'version', 'previous_version'
            )
        )
        self.assertEqual(
            chain,
            [
                ('4' * 64, ''),
                ('5' * 64, '4' * 64),
                ('4' * 64, '5' * 64),
            ],
        )
        self.assertEqual(rollback['previous'], '5' * 64)
        self.assertEqual(current_website_selection(site=self.site).version, '4' * 64)

    def test_request_token_replay_is_idempotent_and_cannot_be_rebound(self):
        candidate = self.candidate('6')
        token = uuid4()
        first, _store = self.select(candidate, token=token)
        replay, _store = self.select(candidate, token=token)

        self.assertEqual(first['selectionId'], replay['selectionId'])
        self.assertTrue(replay['replayed'])
        self.assertFalse(replay['changed'])
        self.assertEqual(WebsiteReleaseSelection.objects.count(), 1)

        with self.assertRaisesRegex(
            ArticleDeliveryError, 'website_selection_request_reused'
        ):
            self.select(candidate, token=token, actor='other-operator')

    def test_cross_site_non_candidate_and_changed_artifact_are_rejected(self):
        other = self.candidate('7', site=self.other_site)
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_candidate_missing'):
            website_candidate_for_selection(site=self.site, record_id=other.pk)

        not_candidate = Release.objects.create(
            site=self.site,
            release_key='article-preview:selection-test',
            status='built',
            snapshot_manifest={'kind': 'articlePreview', 'version': '8' * 64},
        )
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_candidate_missing'):
            website_candidate_for_selection(site=self.site, record_id=not_candidate.pk)

        candidate = self.candidate('9')
        store = self.store_for('9' * 64)
        store.verify.return_value['articleVersion'] = 'f' * 64
        with patch('console.website_selection.website_release_store', return_value=store):
            with self.assertRaisesRegex(
                ArticleDeliveryError, 'website_candidate_record_mismatch'
            ):
                select_website_candidate(
                    site=self.site,
                    record_id=candidate.pk,
                    expected_version='',
                    request_token=uuid4(),
                    actor='operator',
                    reason='Reviewed immutable candidate for controlled rollout.',
                )
        self.assertFalse(WebsiteReleaseSelection.objects.exists())

    def test_selection_model_refuses_update_and_delete(self):
        candidate = self.candidate('c')
        self.select(candidate)
        selection = WebsiteReleaseSelection.objects.get()

        selection.reason = 'Changed reason must not be written.'
        with self.assertRaisesRegex(ValueError, 'append-only'):
            selection.save()
        with self.assertRaisesRegex(ValueError, 'append-only'):
            selection.delete()
