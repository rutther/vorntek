"""Exercise the opt-in renderer without touching host/container configuration."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HttpsProxyRendererTests(unittest.TestCase):
    def command(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / 'scripts/render_https_proxy.py'), *arguments],
            capture_output=True, text=True,
        )

    def test_operator_can_generate_explicit_proxy_configuration_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'website.conf'
            result = self.command('--edge-proxy-ip', '192.0.2.10', '--output', str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            original = output.read_bytes()
            self.assertGreater(len(original), 500)
            repeated = self.command('--edge-proxy-ip', '192.0.2.11', '--output', str(output))
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(output.read_bytes(), original)

    def test_missing_or_unsafe_peer_never_creates_a_configuration(self):
        for peer in ('0.0.0.0', '::', '192.0.2.0/24', 'example.invalid',
                     '224.0.0.1', 'fe80::1%eth0', '192.0.2.1; include x;'):
            with self.subTest(peer=peer), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'website.conf'
                result = self.command('--edge-proxy-ip', peer, '--output', str(output))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
        self.assertNotEqual(self.command().returncode, 0)

    def test_stdout_plan_leaves_source_configuration_unchanged(self):
        source = (ROOT / 'deploy/nginx.conf').read_bytes()
        result = self.command('--edge-proxy-ip', '2001:db8::1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(len(result.stdout), 500)
        self.assertEqual((ROOT / 'deploy/nginx.conf').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
