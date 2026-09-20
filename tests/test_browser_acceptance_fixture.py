import csv
from io import StringIO
from pathlib import Path
import importlib.util
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'browser_acceptance_fixture', ROOT/'scripts'/'run_browser_acceptance_fixture.py'
)
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


class BrowserAcceptanceFixtureTests(unittest.TestCase):
    def test_runtime_guard_refuses_privileged_ports_and_unbounded_reviews(self):
        for port, seconds in ((80, 900), (8765, 29), (8765, 1801)):
            with self.subTest(port=port, seconds=seconds), self.assertRaises(ValueError):
                fixture.validate_runtime(port, seconds)
        fixture.validate_runtime(8765, 30)

    def test_upload_fixture_is_exact_synthetic_standard21_csv(self):
        decoded = fixture.fixture_csv().decode('utf-8')
        rows = list(csv.DictReader(StringIO(decoded)))
        self.assertEqual(tuple(rows[0]), fixture.STANDARD21_HEADERS)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['company'], 'Synthetic Browser Import Ltd')
        self.assertEqual(rows[0]['value'], '36.0')
        self.assertTrue(rows[0]['email'].endswith('@example.invalid'))

    def test_source_binds_loopback_and_preserves_acceptance_limits(self):
        source = (ROOT/'scripts'/'run_browser_acceptance_fixture.py').read_text(encoding='utf-8')
        self.assertIn("make_server(\n            '127.0.0.1'", source)
        self.assertIn("'NEWCROWN_ALLOW_EXTERNAL_IO': '0'", source)
        self.assertIn("'SITEOS_WHATSAPP_ALLOW_LIVE_SEND': '0'", source)
        self.assertIn("category='industrial'", source)
        self.assertIn('secrets.token_urlsafe(24)', source)
        self.assertIn("credential_path.unlink(missing_ok=True)", source)
        self.assertIn("membership_role='member'", source)
        self.assertIn("'not PostgreSQL acceptance'", source)
        self.assertNotIn('BROWSER_PASSWORD', source)
        self.assertNotIn('0.0.0.0', source)


if __name__ == '__main__':
    unittest.main()
