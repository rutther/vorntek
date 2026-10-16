"""Reproduce the pinned upstream browser artifact; never execute package scripts."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = '4.4.0'
URL = f'https://registry.npmjs.org/@google/model-viewer/-/model-viewer-{VERSION}.tgz'
INTEGRITY = '8z1SB5sPlzKoak5mdx/D31brBakPYm3Qi31EfAucHAUrtrHHCbabypkKvSwReYhUpPaxyJ46oT47uWE/hrs1Zw=='
LOCK_URL = 'https://raw.githubusercontent.com/google/model-viewer/v4.4.0/package-lock.json'
LOCK_SHA256 = 'b2d6d2dca131156404459fdb6af60abfeb617535065c6788e71c16f8f6f2f062'


def main():
    with urllib.request.urlopen(URL, timeout=45) as response:
        raw = response.read(30 * 1024 * 1024 + 1)
    if len(raw) > 30 * 1024 * 1024 or base64.b64encode(hashlib.sha512(raw).digest()).decode() != INTEGRITY:
        raise ValueError('Pinned registry archive integrity differs')
    destination = ROOT/'apps/crm/console/static/console/vendor/model-viewer'/VERSION
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as package:
        metadata = json.loads(package.extractfile('package/package.json').read())
        if (metadata['name'], metadata['version'], metadata['license']) != ('@google/model-viewer', VERSION, 'Apache-2.0'):
            raise ValueError('Unexpected upstream package identity')
        for source, name in [('package/dist/model-viewer.min.js', 'model-viewer.min.js'), ('package/LICENSE', 'LICENSE')]:
            member = package.getmember(source)
            if not member.isfile() or member.size > 2 * 1024 * 1024:
                raise ValueError('Unexpected upstream artifact')
            files[name] = package.extractfile(member).read()
    with urllib.request.urlopen(LOCK_URL, timeout=45) as response:
        lock_raw = response.read(5 * 1024 * 1024 + 1)
    if hashlib.sha256(lock_raw).hexdigest() != LOCK_SHA256:
        raise ValueError('Pinned upstream release lock differs')
    lock = json.loads(lock_raw)['packages']
    notices = []
    entries = [(name,lock['node_modules/'+name]) for name in
               ('three','lit','lit-html','lit-element','@lit/reactive-element','@monogrid/gainmap-js','meshoptimizer')]
    # The Three.js embedded fflate banner is 0.8.2, not the root lock's newer
    # separate dependency. Preserve the embedded component's own MIT notice.
    entries.append(('fflate',{'version':'0.8.2','resolved':'https://registry.npmjs.org/fflate/-/fflate-0.8.2.tgz',
        'integrity':'sha512-cPJU47OaAoCbg0pBvzsgpTPhmhqI5eJjh/JIu8tPj5q+T7iLvW/JAYUqmE7KOB4R1ZyEhzBaIQpQpardBF5z8A=='}))
    for name,entry in entries:
        if not entry['resolved'].startswith('https://registry.npmjs.org/') or not entry['integrity'].startswith('sha512-'):
            raise ValueError('Unexpected upstream dependency source')
        with urllib.request.urlopen(entry['resolved'], timeout=45) as response:
            dependency = response.read(30 * 1024 * 1024 + 1)
        if base64.b64encode(hashlib.sha512(dependency).digest()).decode() != entry['integrity'][7:]:
            raise ValueError('Dependency archive integrity differs')
        found = []
        with tarfile.open(fileobj=io.BytesIO(dependency), mode='r:gz') as package:
            for member in package.getmembers():
                parts = member.name.split('/')
                if (len(parts)==2 and parts[0]=='package' and member.isfile() and member.size<100_000
                        and parts[1].lower().startswith(('license','notice'))):
                    output_name = 'notices/'+name.replace('/','-').replace('@','')+'-'+parts[1]
                    files[output_name] = package.extractfile(member).read()
                    found.append(output_name)
        if not found:
            raise ValueError('Dependency license notice missing: '+name)
        notices.append({'package':name,'version':entry['version'],'source':entry['resolved'],
                        'archiveIntegrity':entry['integrity'],'noticeFiles':found})
    # Narrow writes of verified upstream bytes, not general archive extraction.
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes() != data:
            raise ValueError('Refusing to overwrite a different vendor artifact')
        target.write_bytes(data)
    manifest = {'package': '@google/model-viewer', 'version': VERSION, 'source': URL,
                'archiveIntegrity': 'sha512-'+INTEGRITY,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                'upstreamLockSha256':LOCK_SHA256,'dependencyNotices':notices,'scriptsExecuted': False}
    (destination/'provenance.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
