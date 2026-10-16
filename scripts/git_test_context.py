import unittest
from pathlib import Path


def has_git_metadata(root):
    """Return whether *root* is a Git checkout, including linked worktrees."""
    return (Path(root) / '.git').exists()


def requires_git_checkout(root):
    """Skip a test cleanly when a source archive has no Git metadata."""
    return unittest.skipUnless(
        has_git_metadata(root),
        'requires a Git checkout; source archives intentionally omit .git metadata',
    )
