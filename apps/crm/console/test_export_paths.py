from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase

from console.customer_pool_exports import _private_path


class PortableExportPathTests(SimpleTestCase):
    def test_relative_paths_and_current_root_legacy_paths_resolve(self):
        with tempfile.TemporaryDirectory(prefix='newCrownExportPath-') as directory:
            root = Path(directory).resolve()
            with patch('console.customer_pool_exports._export_root', return_value=root):
                self.assertEqual(_private_path('synthetic.xlsx'), root/'synthetic.xlsx')
                self.assertEqual(_private_path(str(root/'legacy.xlsx')), root/'legacy.xlsx')

    def test_traversal_other_server_and_non_export_files_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='newCrownExportPath-') as directory:
            root = Path(directory).resolve()
            with patch('console.customer_pool_exports._export_root', return_value=root):
                for value in ('', '../outside.xlsx', 'a/../../outside.xlsx', 'file.txt',
                              'a\\outside.xlsx', 'other:server.xlsx', str(root.parent/'outside.xlsx')):
                    with self.subTest(value=value):
                        self.assertIsNone(_private_path(value))
