"""Load a secret from an environment variable or a mounted Compose secret."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def secret_value(name: str, default: str = '') -> str:
    direct = os.getenv(name, '')
    filename = os.getenv(name + '_FILE', '')
    if direct and filename:
        raise ImproperlyConfigured(f'Set only {name} or {name}_FILE, not both.')
    if not filename:
        return direct or default
    try:
        value = Path(filename).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        raise ImproperlyConfigured(f'Cannot read {name}_FILE.') from None
    if not value:
        raise ImproperlyConfigured(f'{name}_FILE is empty.')
    return value
