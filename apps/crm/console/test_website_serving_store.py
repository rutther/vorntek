from __future__ import annotations

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from .article_delivery import ArticleDeliveryError
from .website_serving_store import WebsiteServingStore


class FakeCandidateStore:
    def __init__(self, *, site_code='testSite', files=None):
        from .article_delivery import content_digest
        from .website_release_store import _sha

        self.contents = files or {
            'index.html': b'<!doctype html><link rel="stylesheet" href="/styles.css">',
            'styles.css': b'body { color: black; }',
        }
        payload = {
            'schemaVersion': 1,
            'kind': 'websiteRelease',
            'siteCode': site_code,
            'baseSourceSha256': 'a' * 64,
            'articleVersion': 'b' * 64,
            'files': {name: _sha(raw) for name, raw in sorted(self.contents.items())},
        }
        self.version = content_digest(payload)
        self.manifest = payload

    def verify(self, version):
        if version != self.version:
            raise ArticleDeliveryError('website_artifact_unavailable')
        return dict(self.manifest)

    def read_version_file(self, version, name):
        self.verify(version)
        return self.contents[name]


class MemoryPointerServingStore(WebsiteServingStore):
    """Exercise materialization/CAS on hosts that cannot create symlinks."""

    pointer = ''

    def current(self):
        return self.pointer

    def _replace_current(self, version):
        self._release_target(version)
        self.pointer = version


class WebsiteServingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vorntek-serving-')
        self.root = Path(self.temporary.name) / 'serving'
        WebsiteServingStore.initialize(self.root)
        self.store = MemoryPointerServingStore(self.root)
        self.store.pointer = ''

    def tearDown(self):
        self.temporary.cleanup()

    def deploy(self, candidate, *, expected=''):
        return self.store.deploy(candidate, candidate.version, expected=expected)

    def test_verified_candidate_is_materialized_and_switched_atomically(self):
        candidate = FakeCandidateStore()

        result = self.deploy(candidate)

        self.assertEqual(result['previous'], '')
        self.assertEqual(self.store.current(), candidate.version)
        deployed = self.store.verify(candidate.version, expected_manifest=candidate.manifest)
        self.assertEqual(deployed['files'], candidate.manifest['files'])

    def test_stale_switch_tampering_and_unsafe_pointer_fail_closed(self):
        first = FakeCandidateStore()
        self.deploy(first)
        second = FakeCandidateStore(files={
            'index.html': b'<!doctype html><p>second</p>',
        })

        with self.assertRaisesRegex(ArticleDeliveryError, 'website_deployed_version_changed'):
            self.deploy(second, expected='')

        deployed_file = self.root / 'releases' / first.version / 'index.html'
        deployed_file.write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_deployment_file_changed'):
            self.store.verify(first.version)

        real_store = WebsiteServingStore(self.root)
        try:
            os.symlink('../outside', self.root / 'current', target_is_directory=True)
        except OSError as error:
            if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                return
            raise
        with self.assertRaisesRegex(ArticleDeliveryError, 'website_serving_pointer_invalid'):
            real_store.current()

    def test_existing_release_can_be_used_for_audited_rollback(self):
        first = FakeCandidateStore(files={'index.html': b'<!doctype html><p>first</p>'})
        second = FakeCandidateStore(files={'index.html': b'<!doctype html><p>second</p>'})

        self.deploy(first)
        self.deploy(second, expected=first.version)
        rollback = self.deploy(first, expected=second.version)

        self.assertEqual(rollback['previous'], second.version)
        self.assertEqual(self.store.current(), first.version)

    def test_nonempty_unowned_store_and_public_file_set_changes_are_rejected(self):
        other = Path(self.temporary.name) / 'unowned'
        other.mkdir()
        (other / 'foreign.txt').write_text('foreign', encoding='utf-8')
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'website_serving_store_not_initialized'
        ):
            WebsiteServingStore.initialize(other)

        candidate = FakeCandidateStore()
        self.deploy(candidate)
        release = self.root / 'releases' / candidate.version
        (release / 'extra.txt').write_text('extra', encoding='utf-8')
        with self.assertRaisesRegex(
            ArticleDeliveryError, 'website_deployment_file_set_mismatch'
        ):
            self.store.verify(candidate.version)

    def test_posix_lock_reuses_stale_file_and_releases_advisory_lock(self):
        class FakeFcntl:
            LOCK_EX = 1
            LOCK_NB = 2
            LOCK_UN = 4

            def __init__(self):
                self.operations = []

            def flock(self, descriptor, operation):
                self.operations.append(operation)

        lock_api = FakeFcntl()
        lock_path = self.root / 'deploy.lock'
        lock_path.write_text('terminated-process', encoding='ascii')
        candidate = FakeCandidateStore()

        with patch('console.website_serving_store._fcntl', lock_api):
            self.deploy(candidate)

        self.assertEqual(self.store.current(), candidate.version)
        self.assertTrue(lock_path.is_file())
        self.assertEqual(lock_api.operations, [3, lock_api.LOCK_UN])

    def test_posix_lock_reports_live_contention_without_switching(self):
        class BusyFcntl:
            LOCK_EX = 1
            LOCK_NB = 2
            LOCK_UN = 4

            @staticmethod
            def flock(descriptor, operation):
                raise BlockingIOError

        candidate = FakeCandidateStore()
        with patch('console.website_serving_store._fcntl', BusyFcntl()):
            with self.assertRaisesRegex(
                ArticleDeliveryError,
                'website_deployment_in_progress',
            ):
                self.deploy(candidate)

        self.assertEqual(self.store.current(), '')
        self.assertFalse((self.root / 'releases' / candidate.version).exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink integration runs in CI/container acceptance.')
    def test_real_pointer_is_relative_and_atomically_replaceable(self):
        store = WebsiteServingStore(self.root)
        candidate = FakeCandidateStore()

        store.deploy(candidate, candidate.version, expected='')

        self.assertEqual(store.current(), candidate.version)
        self.assertEqual(
            os.readlink(self.root / 'current'),
            f'releases/{candidate.version}',
        )


if __name__ == '__main__':
    unittest.main()
