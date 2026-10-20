"""Build an allowlisted, inspected public release directory. Standard library only."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import io
import os
from pathlib import Path
import re
import zipfile

MARKERS_ENV = 'MOSSBAUER_PRIVATE_MARKERS'


def private_markers(environ=os.environ):
    """Private site identifiers that must never be published. They are themselves private, so they come from the
    MOSSBAUER_PRIVATE_MARKERS environment variable (comma- or newline-separated; a CI secret), never from the repository."""
    text = environ.get(MARKERS_ENV, '')
    return tuple(m.strip().lower().encode('utf-8') for m in text.replace(',', '\n').splitlines() if m.strip())


def inspect_content(name, content, markers):
    inspected = name.encode('utf-8').lower() + b'\n' + content.lower()
    if any(marker in inspected for marker in markers):
        raise ValueError(f'Private site identifier in {name}; publication refused')


def prepare(wheel, outdir, docs, version, markers=None, require_markers=False):
    markers = private_markers() if markers is None else markers
    if require_markers and not markers:
        raise ValueError(f'No private markers configured; set {MARKERS_ENV} before publishing')
    wheel, outdir, docs = Path(wheel), Path(outdir), Path(docs)
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[a-z0-9.]+)?', version):
        raise ValueError('Invalid release version')
    expected = f'mossbauer_dft-{version}-py3-none-any.whl'
    if wheel.name != expected or not wheel.is_file() or wheel.is_symlink():
        raise ValueError(f'Expected wheel {expected}')
    content = wheel.read_bytes()
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = z.namelist()
        for name in names:
            if '..' in Path(name).parts or name.startswith('/') or '\\' in name:
                raise ValueError('Unsafe wheel path')
            if name.startswith('mossbauer_dft/data/sites/') and name != 'mossbauer_dft/data/sites/example.yaml':
                raise ValueError('Private or unexpected bundled site profile; publication refused')
            inspect_content(name, z.read(name), markers)
        metadata = BytesParser().parsebytes(z.read(f'mossbauer_dft-{version}.dist-info/METADATA'))
        if metadata['Name'] != 'mossbauer-dft' or metadata['Version'] != version:
            raise ValueError('Wheel metadata does not match the release version')
        roots = {'mossbauer_dft', f'mossbauer_dft-{version}.dist-info'}
        if any(name.split('/')[0] not in roots for name in names):
            raise ValueError('Unexpected content outside the wheel package')
        required = {'mossbauer_dft/doctor.py', 'mossbauer_dft/gui/views/cluster.py',
                    'mossbauer_dft/handoff.py', 'mossbauer_dft/data/sites/example.yaml',
                    'mossbauer_dft/data/calibrations.yaml', 'mossbauer_dft/data/training_set.csv'}
        if not required <= set(names):
            raise ValueError('Wheel is missing required app resources')
    selected = {expected: content}
    for name in ('README.md', 'INSTALL.md', 'LICENSE'):
        p = docs / name
        if p.is_symlink():
            raise ValueError('Public documentation must be an ordinary file')
        data = p.read_bytes()
        inspect_content(name, data, markers)
        selected[name] = data
    if expected.encode() not in selected['INSTALL.md']:
        raise ValueError('Install instructions do not match the release version')
    selected['SHA256SUMS'] = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n'
                                   for name, data in sorted(selected.items())).encode('ascii')
    outdir.mkdir(parents=True, exist_ok=False)
    for name, data in selected.items():
        (outdir / name).write_bytes(data)
    return sorted(selected)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--wheel', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--docs', type=Path, default=Path('docs/releases'))
    p.add_argument('--version', required=True)
    p.add_argument('--require-markers', action='store_true', help=f'refuse to run unless {MARKERS_ENV} is set')
    a = p.parse_args()
    print('\n'.join(prepare(a.wheel, a.out, a.docs, a.version, require_markers=a.require_markers)))


if __name__ == '__main__':
    main()
