"""Package a clean exact HEAD as source archives and checksum-bound metadata."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

if __package__:
    from . import release_manifest
else:
    import release_manifest


def package_release(root: Path, ref: str, output: Path) -> dict:
    root=root.resolve(strict=True)
    if root != release_manifest.ROOT.resolve():
        raise ValueError('Use this packager from its own source checkout.')
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=root)
    commit=git('rev-parse','--verify',ref+'^{commit}').decode().strip()
    if commit != git('rev-parse','HEAD').decode().strip():
        raise ValueError('Check out the exact ref as HEAD before packaging.')
    manifest=release_manifest.build_manifest(require_clean=True)
    version=manifest['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?',version):
        raise ValueError('Invalid release version.')
    output=output.resolve()
    if output.is_relative_to(root):
        raise ValueError('Release output must be outside the source checkout.')
    # mkdir without exist_ok deliberately refuses even a partially filled output.
    output.mkdir(parents=True,exist_ok=False)
    prefix=f'vorntek-{version}'
    archives={prefix+'.zip':git('archive','--format=zip','--prefix='+prefix+'/',commit),
              prefix+'.tar.gz':gzip.compress(git('archive','--format=tar','--prefix='+prefix+'/',commit),mtime=0)}
    # A source archive must not acquire ignored runtime/credential paths through
    # broad filesystem copies; git archive supplies only the reviewed commit.
    files={**archives,'release-manifest.json':(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()}
    for name,payload in files.items():
        with (output/name).open('xb') as stream:
            stream.write(payload)
    sums=''.join(hashlib.sha256(payload).hexdigest()+'  '+name+'\n' for name,payload in sorted(files.items()))
    with (output/'SHA256SUMS').open('x',encoding='ascii',newline='\n') as stream:
        stream.write(sums)
    return {'version':version,'git_commit':commit,'zip':prefix+'.zip','tar_gz':prefix+'.tar.gz',
            'output':str(output),'files':len(files)+1}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(package_release(release_manifest.ROOT,args.ref,args.output),indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
