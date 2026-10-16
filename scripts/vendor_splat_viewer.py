"""Reproduce pinned self-hosted splat viewer bytes; never execute npm scripts."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
PACKAGES=(
    ('@playcanvas/supersplat-viewer','1.37.0',
     'https://registry.npmjs.org/@playcanvas/supersplat-viewer/-/supersplat-viewer-1.37.0.tgz',
     '8dy9mn9lFFL04PhWbwlHchzE/ky55FwuIVcz65Mf1+BN2UFMNGtGDV/BYPirI92ijJofUCUZS8eDT5OVGy/a1g==',
     {'package/public/index.js':'index.js','package/public/index.css':'index.css','package/LICENSE':'LICENSE'}),
    ('playcanvas','2.23.0','https://registry.npmjs.org/playcanvas/-/playcanvas-2.23.0.tgz',
     'DNV4AUMqSDkm74P0CdGEi0NJkmDzKzKEtHNW753LPVF0Xd+2YQeNxAprEcPqEpXTo6/o8LXXqpgspYXWYlhDTg==',
     {'package/LICENSE':'notices/playcanvas-LICENSE'}),
)


def main():
    files={};sources=[]
    for name,version,url,integrity,selected in PACKAGES:
        with urllib.request.urlopen(url,timeout=45) as response: raw=response.read(50*1024*1024+1)
        if len(raw)>50*1024*1024 or base64.b64encode(hashlib.sha512(raw).digest()).decode()!=integrity:
            raise ValueError('Pinned archive integrity differs')
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as package:
            metadata=json.loads(package.extractfile('package/package.json').read())
            if (metadata['name'],metadata['version'],metadata['license'])!=(name,version,'MIT'):
                raise ValueError('Unexpected package identity')
            for member_name,destination in selected.items():
                member=package.getmember(member_name)
                if not member.isfile() or member.size>5*1024*1024: raise ValueError('Unexpected vendor file')
                files[destination]=package.extractfile(member).read()
        sources.append({'package':name,'version':version,'url':url,'integrity':'sha512-'+integrity})
    destination=ROOT/'apps/crm/console/static/console/vendor/supersplat/1.37.0'
    for name,raw in files.items():
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_bytes()!=raw: raise ValueError('Refusing different existing vendor bytes')
        target.write_bytes(raw)
    manifest={'sources':sources,'scriptsExecuted':False,
              'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}}
    (destination/'provenance.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest))


if __name__=='__main__':main()
