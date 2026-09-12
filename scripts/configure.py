"""Create local runtime configuration without printing or overwriting secrets."""
import argparse
import os
import secrets
from pathlib import Path


def configure(root: Path):
    root = root.resolve()
    if not (root / 'compose.yaml').is_file():
        raise SystemExit('Run this against the Vorntek project root.')
    secret_dir = root / '.secrets'
    if secret_dir.exists() or (root / '.env').exists():
        raise SystemExit('Configuration already exists; refusing to replace credentials.')
    secret_dir.mkdir(mode=0o700)
    for name in ('db_admin_password', 'db_password', 'django_key', 'vault_key'):
        # The private parent directory is owner-only. Files must be readable by
        # the unprivileged container user after Compose bind-mounts them.
        descriptor = os.open(secret_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(secrets.token_hex(32) + '\n')
        # A restrictive caller umask must not make the bind-mounted file
        # unreadable to the container UID; the host parent remains mode 0700.
        os.chmod(secret_dir / name, 0o644)
    with (root / '.env').open('x', encoding='utf-8', newline='\n') as stream:
        stream.write((root / '.env.example').read_text(encoding='utf-8'))
    print('Created local configuration and four unique secrets. No secret values printed.')
    print('Protect .secrets and keep it out of Git and backups intended for publication.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    configure(parser.parse_args().root)
