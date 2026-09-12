"""Verified SQL chain and conservative migration-ledger validation.

Existing partial legacy ledgers require a separately verified adoption process;
this module never guesses that missing ledger rows mean SQL was not applied.
"""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import CommandError


@dataclass(frozen=True)
class Migration:
    name: str
    checksum: str
    sql: str


def transaction_body(sql: str) -> str:
    lines = sql.strip().splitlines()
    if lines and lines[0].strip().upper() == 'BEGIN;':
        if lines[-1].strip().upper() != 'COMMIT;':
            raise CommandError('Unbalanced SQL transaction wrapper.')
        return '\n'.join(lines[1:-1])
    return sql


def load_chain(directory: Path) -> list[Migration]:
    expected = {}
    for line in (directory / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([a-f0-9]{64})\s+([0-9]{4}_[a-z0-9_]+\.sql)', line)
        if not match or match[2] in expected:
            raise CommandError('Invalid or duplicated SQL checksum entry.')
        expected[match[2]] = match[1]
    files = sorted(directory.glob('[0-9][0-9][0-9][0-9]_*.sql'))
    if not files or {p.name for p in files} != set(expected):
        raise CommandError('SQL files and checksum manifest differ.')
    if [int(p.name[:4]) for p in files] != list(range(1, len(files) + 1)):
        raise CommandError('SQL migration numbering must be consecutive.')
    chain = []
    for path in files:
        raw = path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if checksum != expected[path.name]:
            raise CommandError(f'SQL checksum mismatch: {path.name}')
        chain.append(Migration(path.name, checksum, transaction_body(raw.decode('utf-8'))))
    return chain


def pending_migrations(chain: list[Migration], recorded: dict[str, str]) -> list[Migration]:
    names = [m.name for m in chain]
    if set(recorded) - set(names):
        raise CommandError('Database contains migrations unknown to this release; do not downgrade.')
    if set(recorded) != set(names[:len(recorded)]):
        raise CommandError('Incomplete legacy ledger: verified adoption is required; no SQL was replayed.')
    for migration in chain[:len(recorded)]:
        if recorded[migration.name] != migration.checksum:
            raise CommandError(f'Applied migration checksum differs: {migration.name}')
    return chain[len(recorded):]
