import tempfile
import unittest
from pathlib import Path

from scripts.git_test_context import has_git_metadata, requires_git_checkout


class GitTestContextTests(unittest.TestCase):
    def test_git_metadata_accepts_worktree_file_or_repository_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(has_git_metadata(root))

            (root / '.git').write_text('gitdir: elsewhere\n', encoding='utf-8')
            self.assertTrue(has_git_metadata(root))

            (root / '.git').unlink()
            (root / '.git').mkdir()
            self.assertTrue(has_git_metadata(root))

    def test_decorator_skips_only_without_git_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            @requires_git_checkout(root)
            class ArchiveOnlyTests(unittest.TestCase):
                pass

            self.assertTrue(ArchiveOnlyTests.__unittest_skip__)
            self.assertIn('Git checkout', ArchiveOnlyTests.__unittest_skip_why__)

            (root / '.git').mkdir()

            @requires_git_checkout(root)
            class CheckoutTests(unittest.TestCase):
                pass

            self.assertFalse(getattr(CheckoutTests, '__unittest_skip__', False))


if __name__ == '__main__':
    unittest.main()
