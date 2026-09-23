from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings


VERSION_PATTERN = re.compile(
    r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
    r'(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?'
    r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$'
)


def _version_candidates(base_dir: Path) -> tuple[Path, Path]:
    """Support both a source checkout and the shallow ``/app`` image layout."""
    return base_dir / 'VERSION', base_dir.parent.parent / 'VERSION'


@lru_cache(maxsize=1)
def release_version() -> str:
    override = str(os.getenv('VORNTEK_VERSION') or '').strip()
    if override:
        if not VERSION_PATTERN.fullmatch(override):
            raise RuntimeError('VORNTEK_VERSION must be a semantic version.')
        return override
    base_dir = Path(settings.BASE_DIR).resolve()
    for candidate in _version_candidates(base_dir):
        if candidate.is_file():
            value = candidate.read_text(encoding='ascii').strip()
            if not VERSION_PATTERN.fullmatch(value):
                raise RuntimeError('VERSION must contain one semantic version.')
            return value
    raise RuntimeError('Release VERSION file is missing.')
