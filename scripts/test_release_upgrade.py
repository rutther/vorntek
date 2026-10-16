"""Run the isolated PostgreSQL lifecycle against an actual public source baseline."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PUBLIC_BASELINE='6d3eefe7e5e124c31d175a1bfc971b58dd197082'


def preserved_original_rows(before: dict, after: dict) -> bool:
    """Django may append new model metadata; every old row must still exist."""
    if set(before)!=set(after):
        return False
    for table,rows in before.items():
        if table in {'auth_permission','django_content_type'}:
            if not Counter(rows)<=Counter(after[table]):
                return False
        elif Counter(rows)!=Counter(after[table]):
            return False
    return True


def validate_migration_prefix(baseline: Path, candidate: Path) -> tuple[int,int]:
    def chain(directory):
        files=sorted(directory.glob('[0-9][0-9][0-9][0-9]_*.sql'))
        entries=[]
        for line in (directory/'SHA256SUMS').read_text().splitlines():
            if line.strip():
                match=re.fullmatch(r'([0-9a-f]{64})\s+([0-9]{4}_[a-z0-9_]+\.sql)',line)
                if not match:
                    raise ValueError('Invalid migration manifest.')
                entries.append((match[2],match[1]))
        actual=[(p.name,hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]
        if not actual or [n for n,_ in actual] != [n for n,_ in entries]:
            raise ValueError('Migration manifest does not cover the exact file set.')
        if actual != entries:
            raise ValueError('Migration checksum differs.')
        return actual
    old,new=chain(baseline),chain(candidate)
    if len(new)<=len(old):
        raise ValueError('Candidate must extend the historical chain.')
    if new[:len(old)] != old:
        raise ValueError('Candidate migration prefix differs from the real baseline.')
    return len(old),len(new)-len(old)


def validate_baseline_checkout(baseline: Path, candidate: Path) -> tuple[int,int]:
    baseline=baseline.resolve(strict=True);candidate=candidate.resolve(strict=True)
    if baseline == candidate or (baseline/'apps/crm/.env').exists():
        raise ValueError('A separate clean baseline checkout without application .env is required.')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=baseline,text=True).strip()
    if revision != PUBLIC_BASELINE:
        raise ValueError('Baseline is not the frozen public main revision.')
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=baseline).strip():
        raise ValueError('Baseline checkout is dirty.')
    return validate_migration_prefix(baseline/'apps/crm/db/migrations',candidate/'apps/crm/db/migrations')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-checkout',type=Path,required=True)
    parser.add_argument('--candidate-checkout',type=Path,default=ROOT)
    parser.add_argument('--pg-bin',type=Path,required=True)
    args=parser.parse_args()
    validate_baseline_checkout(args.baseline_checkout,args.candidate_checkout)
    return subprocess.call([sys.executable,str(args.candidate_checkout/'scripts/test_postgres_install.py'),
        '--pg-bin',str(args.pg_bin),'--http','--baseline-checkout',str(args.baseline_checkout)])


if __name__=='__main__':
    raise SystemExit(main())
