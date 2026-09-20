"""Print locked Python dependency/license evidence from the current interpreter.

No network or private configuration is read. Metadata is not a legal compatibility
decision and a host-wheel inventory is not a Linux image-layer inventory.
"""
import hashlib
from importlib import metadata
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _is_license_evidence(relative):
    """Accept declared license files and conventional wheel license filenames."""
    name = Path(relative).name.casefold()
    return name.startswith(('license', 'licence', 'copying', 'notice', 'authors'))


def inventory():
    lock_path = ROOT/'apps/crm/requirements.lock'
    lock_bytes = lock_path.read_bytes()
    packages = []
    for line in lock_bytes.decode('utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, expected = line.split('==', 1)
        dist = metadata.distribution(name)
        if dist.version != expected:
            raise RuntimeError(f'Installed version does not match lockfile: {name}')
        info = dist.metadata
        declaration = info.get('License-Expression') or info.get('License') or '; '.join(
            c.removeprefix('License :: ') for c in info.get_all('Classifier', []) if c.startswith('License :: '))
        if len(declaration) > 160:
            declaration = 'Full license text in package metadata; inspect distribution'
        evidence = []
        declared = info.get_all('License-File', [])
        for file in dist.files or []:
            normalized = str(file).replace('\\', '/')
            if '.dist-info/' not in normalized:
                continue
            relative = normalized.split('.dist-info/', 1)[1]
            if (any(relative == item or relative.endswith('/' + item) for item in declared)
                    or _is_license_evidence(relative)):
                raw = dist.locate_file(file).read_bytes()
                evidence.append({'path_in_dist_info': relative, 'sha256': hashlib.sha256(raw).hexdigest()})
        packages.append({'name': name, 'version': dist.version, 'license_metadata': declaration,
                         'declared_license_files': declared, 'license_file_evidence': evidence})
    return {'scope': 'installed locked Python distributions; not license approval or image verification',
            'requirements_lock_sha256': hashlib.sha256(lock_bytes).hexdigest(),
            'packages': packages}


if __name__ == '__main__':
    print(json.dumps(inventory(), ensure_ascii=False, indent=2))
