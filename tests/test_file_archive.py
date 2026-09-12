import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('file_archive', ROOT/'scripts'/'file_archive.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class FileArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='newCrownFileTest-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base/'source'
        self.target = self.base/'target'
        self.bundle = self.base/'snapshot'
        self.source.mkdir()
        self.target.mkdir()
        (self.source/'assets').mkdir()
        (self.source/'empty').mkdir()
        (self.source/'assets'/'演示文件.txt').write_bytes(b'SYNTHETIC ONLY\r\n')
        (self.source/'duplicate.bin').write_bytes(b'SYNTHETIC ONLY\r\n')
        (self.source/'empty-file').write_bytes(b'')

    def args(self, operation, **kwargs):
        return argparse.Namespace(**dict(dict(operation=operation, volume='crm-files',
            root=self.source if operation == 'backup' else self.target,
            bundle=self.bundle, apply=True, writers_stopped=True, trusted_snapshot=True), **kwargs))

    def backup(self):
        return archive.execute(self.args('backup'))

    def test_plan_does_not_write_and_full_restore_preserves_bytes_and_empty_directories(self):
        result = archive.execute(self.args('backup', apply=False))
        self.assertEqual(result['result'], 'plan_only')
        self.assertFalse(self.bundle.exists())
        self.backup()
        self.assertEqual(len(list((self.bundle/'objects').iterdir())), 2)
        self.assertEqual(archive.execute(self.args('verify'))['files'], 3)
        archive.execute(self.args('restore', apply=False))
        self.assertEqual(list(self.target.iterdir()), [])
        restored = archive.execute(self.args('restore'))
        self.assertFalse(restored['services_started'])
        self.assertEqual(archive.inspect_tree(self.target), archive.inspect_tree(self.source))
        self.assertFalse((self.target/archive.MARKER).exists())

    def test_existing_backup_and_nonempty_target_are_not_overwritten(self):
        self.backup()
        with self.assertRaises(archive.Refused):
            self.backup()
        private = self.target/'existing.txt'
        private.write_bytes(b'SYNTHETIC preserve me')
        with self.assertRaises(archive.Refused):
            archive.execute(self.args('restore'))
        self.assertEqual(private.read_bytes(), b'SYNTHETIC preserve me')
        self.assertFalse((self.target/archive.MARKER).exists())

    def test_apply_requires_explicit_acknowledgements_and_matching_volume(self):
        with self.assertRaises(archive.Refused):
            archive.execute(self.args('backup', writers_stopped=False))
        self.assertFalse(self.bundle.exists())
        self.backup()
        for overrides in ({'writers_stopped': False}, {'trusted_snapshot': False}, {'volume': 'crm-runtime'}):
            with self.assertRaises(archive.Refused):
                archive.execute(self.args('restore', **overrides))
        self.assertEqual(list(self.target.iterdir()), [])

    def test_corrupt_object_is_refused_before_any_target_write(self):
        self.backup()
        next((self.bundle/'objects').iterdir()).write_bytes(b'corrupted synthetic payload')
        with self.assertRaises(archive.Refused):
            archive.execute(self.args('restore'))
        self.assertEqual(list(self.target.iterdir()), [])

    def test_path_traversal_reserved_and_case_collision_entries_are_refused(self):
        self.backup()
        path = self.bundle/'file-manifest.json'
        original = json.loads(path.read_text(encoding='utf-8'))
        for name in ('.', '..', '../outside', '/absolute', 'C:/outside', 'a\\b', 'CON.txt', 'bad.', './file'):
            manifest = json.loads(json.dumps(original))
            manifest['files'][0]['path'] = name
            path.write_text(json.dumps(manifest))
            with self.subTest(name=name), self.assertRaises(archive.Refused):
                archive.execute(self.args('restore'))
            self.assertEqual(list(self.target.iterdir()), [])
        manifest = json.loads(json.dumps(original))
        manifest['files'].append(dict(manifest['files'][0], path=manifest['files'][0]['path'].upper()))
        path.write_text(json.dumps(manifest))
        with self.assertRaises(archive.Refused):
            archive.execute(self.args('restore'))

    def test_source_change_invalidates_snapshot(self):
        real_copy = archive.create_file
        def changing_copy(path, source):
            real_copy(path, source)
            (self.source/'new-file').write_bytes(b'changed during copy')
        with patch.object(archive, 'create_file', side_effect=changing_copy):
            with self.assertRaises(archive.Refused):
                self.backup()
        self.assertFalse((self.bundle/'file-manifest.json').exists())

    def test_interrupted_restore_keeps_incomplete_marker_and_preserves_snapshot(self):
        self.backup()
        real_copy = archive.create_file
        count = 0
        def interrupted_copy(path, source):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('synthetic disk failure')
            real_copy(path, source)
        with patch.object(archive, 'create_file', side_effect=interrupted_copy):
            with self.assertRaises(OSError):
                archive.execute(self.args('restore'))
        self.assertTrue((self.target/archive.MARKER).is_file())
        self.assertEqual(archive.verify(self.bundle, 'crm-files')['format'], archive.FORMAT)
        with self.assertRaises(archive.Refused):
            archive.execute(self.args('restore'))

    def test_seeded_empty_directories_must_match_snapshot(self):
        self.backup()
        (self.target/'assets').mkdir()
        archive.execute(self.args('restore'))
        self.assertEqual(archive.inspect_tree(self.source), archive.inspect_tree(self.target))

    def test_nested_snapshot_destination_is_refused_without_writes(self):
        with self.assertRaises(archive.Refused):
            archive.execute(self.args('backup', bundle=self.source/'unsafe-backup'))
        self.assertFalse((self.source/'unsafe-backup').exists())

    def test_hardlink_source_is_refused(self):
        (self.source/'hardlink').hardlink_to(self.source/'duplicate.bin')
        with self.assertRaises(archive.Refused):
            self.backup()
        self.assertFalse(self.bundle.exists())

    def test_actual_cli_backup_verify_restore_and_redacted_output(self):
        for operation, root in [('backup', self.source), ('verify', self.source), ('restore', self.target)]:
            result = subprocess.run([sys.executable, str(ROOT/'scripts'/'file_archive.py'), operation,
                '--volume', 'crm-files', '--root', str(root), '--bundle', str(self.bundle),
                '--writers-stopped', '--trusted-snapshot', '--apply'],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('result', json.loads(result.stdout))
            self.assertNotIn('演示文件', result.stdout)
            self.assertNotIn('SYNTHETIC ONLY', result.stdout)
        self.assertEqual(archive.inspect_tree(self.target), archive.inspect_tree(self.source))


if __name__ == '__main__':
    unittest.main()
