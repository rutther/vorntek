"""The synthetic compatibility runner must never tune production settings."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('synthetic_application_runner', ROOT / 'scripts/run_application_tests.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CompatibilityRunnerTests(unittest.TestCase):
    def test_synthetic_sqlite_uses_fast_test_only_password_hashing(self):
        configuration = SimpleNamespace(DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3'}})
        runner.configure_compatibility_settings(configuration)
        self.assertEqual(configuration.PASSWORD_HASHERS, ['django.contrib.auth.hashers.MD5PasswordHasher'])

    def test_non_sqlite_refused_before_password_configuration_changes(self):
        configuration = SimpleNamespace(DATABASES={'default':{'ENGINE':'django.db.backends.postgresql'}},
                                        PASSWORD_HASHERS=['production-hasher'])
        with self.assertRaises(RuntimeError):
            runner.configure_compatibility_settings(configuration)
        self.assertEqual(configuration.PASSWORD_HASHERS, ['production-hasher'])
