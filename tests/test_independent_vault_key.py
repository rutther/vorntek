"""Retain independent vault encryption and file-backed product configuration."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/crm'))
spec = importlib.util.spec_from_file_location('candidate_secret_store', ROOT / 'apps/crm/console/secret_store.py')
vault = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vault)


class IndependentVaultKeyTests(unittest.TestCase):
    def test_missing_vault_key_cannot_fall_back_to_signing_key(self):
        with patch.dict(os.environ, {'SITEOS_ADMIN_SECRET_KEY':'synthetic-signing-only'}, clear=True):
            with self.assertRaises(ValueError):
                vault.secret_vault_master_key()

    def test_same_vault_and_signing_key_is_refused_without_echo(self):
        secret = 'synthetic-reused-key-do-not-echo'
        with patch.dict(os.environ, {'SITEOS_ADMIN_SECRET_KEY':secret,
                                   'SITEOS_SECRET_VAULT_KEY':secret}, clear=True):
            with self.assertRaises(ValueError) as caught:
                vault.secret_vault_master_key()
        self.assertNotIn(secret, str(caught.exception))

    def test_independent_file_backed_vault_key_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'vault-key'
            key.write_text('synthetic-independent-vault-key\n', encoding='utf-8')
            with patch.dict(os.environ, {'SITEOS_ADMIN_SECRET_KEY':'synthetic-signing-only',
                                       'SITEOS_SECRET_VAULT_KEY_FILE':str(key)}, clear=True):
                self.assertEqual(vault.secret_vault_master_key(), 'synthetic-independent-vault-key')
