"""The explicit camera bridge must reproduce from the pinned upstream input."""
from pathlib import Path
import unittest

from scripts.vendor_splat_viewer import extended_viewer


class SplatViewerExtensionTests(unittest.TestCase):
    def test_checked_in_extension_is_reproducible_without_network_or_npm(self):
        root=Path(__file__).resolve().parents[1]/'apps/crm/console/static/console/vendor/supersplat/1.37.0'
        self.assertEqual(extended_viewer((root/'index.js').read_bytes()),
                         (root/'siteos-viewer.js').read_bytes())

    def test_changed_upstream_cannot_silently_receive_an_incompatible_extension(self):
        with self.assertRaisesRegex(ValueError,'different upstream'):
            extended_viewer(b'unknown viewer, not the pinned source')
