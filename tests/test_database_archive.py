import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('database_archive', ROOT/'scripts'/'database_archive.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class ArchiveGuardTests(unittest.TestCase):
    def base_env(self):
        return dict(PGHOST='127.0.0.1', PGPORT='5432', PGDATABASE='isolated_test', PGUSER='app_owner')

    def test_connection_requires_explicit_target_and_rejects_connection_strings(self):
        for key in self.base_env():
            env = self.base_env()
            env.pop(key)
            with self.assertRaises(archive.Refused):
                archive.connection_env(env)
        for key in ('PGDATABASE', 'PGUSER'):
            env = dict(self.base_env(), **{key: 'postgresql://invalid/other'})
            with self.assertRaises(archive.Refused):
                archive.connection_env(env)

    def test_libpq_service_options_and_hostaddr_cannot_override_target(self):
        env = archive.connection_env(dict(self.base_env(), PGSERVICE='other',
            PGOPTIONS='-c search_path=unsafe', PGHOSTADDR='192.0.2.1', PGDATABASE_EXTRA='ignored'))
        for key in ('PGSERVICE', 'PGOPTIONS', 'PGHOSTADDR', 'PGDATABASE_EXTRA'):
            self.assertNotIn(key, env)
        self.assertEqual(env['PGHOST'], '127.0.0.1')

    def test_password_file_ambiguity_refused_without_secret_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'password'
            path.write_text('synthetic-file-secret', encoding='utf-8')
            env = dict(self.base_env(), PGPASSWORD_FILE=str(path))
            self.assertEqual(archive.connection_env(env)['PGPASSWORD'], 'synthetic-file-secret')
            for key in ('PGPASSWORD', 'PGPASSFILE'):
                with self.assertRaises(archive.Refused) as result:
                    archive.connection_env(dict(env, **{key: 'synthetic-conflict'}))
                self.assertNotIn('synthetic', str(result.exception))

    def test_manifest_checks_size_hash_format_and_regular_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump = root/'database.dump'
            dump.write_bytes(b'synthetic-not-a-real-dump')
            manifest = dict(format='newcrown-database-archive-v1', postgres_major=17,
                            bytes=dump.stat().st_size, sha256=archive.sha256(dump))
            archive.private_json(root/'manifest.json', manifest)
            self.assertEqual(archive.validate_bundle(root), manifest)
            for key, value in [('bytes', 1), ('sha256', '0'*64), ('format', 'unknown'), ('postgres_major', '17')]:
                (root/'manifest.json').write_text(json.dumps(dict(manifest, **{key: value})))
                with self.assertRaises(archive.Refused):
                    archive.validate_bundle(root)
            with self.assertRaises(FileExistsError):
                archive.private_json(root/'manifest.json', manifest)

    def test_tool_failure_does_not_echo_private_database_diagnostics(self):
        class Result:
            returncode = 1
            stderr = b'synthetic private row and password'
        with patch.object(archive.subprocess, 'run', return_value=Result()):
            with self.assertRaises(archive.Refused) as result:
                archive.run_tool(['pg_restore'], {})
        self.assertNotIn('synthetic private', str(result.exception))

    def test_empty_check_includes_non_table_database_objects(self):
        class Connection:
            def execute(self, query):
                return self
            def fetchone(self):
                return (0,)
        with patch.object(Connection, 'execute', wraps=Connection().execute) as query:
            archive.assert_empty(Connection())
        statements = ' '.join(call.args[0] for call in query.call_args_list)
        for catalog in ('pg_type', 'pg_proc', 'pg_namespace', 'pg_extension', 'pg_largeobject_metadata', 'pg_subscription'):
            self.assertIn(catalog, statements)


if __name__ == '__main__':
    unittest.main()
