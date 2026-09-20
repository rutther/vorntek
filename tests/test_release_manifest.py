import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'release_manifest', ROOT / 'scripts' / 'release_manifest.py'
)
release_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_manifest)


class ReleaseManifestTests(unittest.TestCase):
    def test_manifest_binds_version_tree_migrations_website_and_locks(self):
        manifest = release_manifest.build_manifest()

        self.assertEqual(manifest['format'], 'vorntek-source-release-v1')
        self.assertEqual(manifest['version'], (ROOT / 'VERSION').read_text().strip())
        self.assertRegex(manifest['git_commit'], r'^[a-f0-9]{40}$')
        self.assertEqual(manifest['migrations']['count'], 29)
        self.assertEqual(
            manifest['migrations']['tip'],
            '0029_customer_pool_row_reference_integrity.sql',
        )
        self.assertEqual(manifest['website_tree']['tracked_files'], 27)
        for key in (
            manifest['source_tree']['git_tree_sha256'],
            manifest['website_tree']['git_tree_sha256'],
            manifest['migrations']['checksums_sha256'],
            manifest['compose_sha256'],
            manifest['crm_requirements_lock_sha256'],
        ):
            self.assertRegex(key, r'^[a-f0-9]{64}$')

    def test_output_is_exclusive_and_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'release.json'
            payload = json.dumps({'format': release_manifest.FORMAT}).encode()

            release_manifest.write_exclusive(output, payload)

            self.assertEqual(json.loads(output.read_text()), {'format': release_manifest.FORMAT})
            with self.assertRaises(release_manifest.Refused):
                release_manifest.write_exclusive(output, payload)
