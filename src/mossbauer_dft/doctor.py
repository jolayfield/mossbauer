"""Installation diagnostics; importing this module needs only the standard library."""
from __future__ import annotations

import importlib
from importlib import metadata
import platform
import sys
import tempfile

from . import __version__

REQUIRED = {'numpy': 'numpy', 'yaml': 'PyYAML', 'streamlit': 'streamlit', 'pandas': 'pandas',
            'plotly': 'plotly', 'rdkit': 'rdkit', 'py3Dmol': 'py3Dmol', 'streamlit_ketcher': 'streamlit-ketcher'}
REPAIR = (f'Reinstall the downloaded wheel: uv tool install --reinstall --python 3.12 '
          f'./mossbauer_dft-{__version__}-py3-none-any.whl; '
          f'or python -m pip install --force-reinstall ./mossbauer_dft-{__version__}-py3-none-any.whl. '
          'Then run mossbauer doctor.')


def dependencies() -> dict:
    checks = {}
    for module, distribution in REQUIRED.items():
        try:
            importlib.import_module(module)
            try:
                version = metadata.version(distribution)
            except metadata.PackageNotFoundError:
                version = 'unknown (import succeeded)'
            checks[module] = dict(ok=True, version=version)
        except Exception as exc:
            checks[module] = dict(ok=False, error=f'{type(exc).__name__}: {exc}')
    return checks


def report() -> dict:
    checks = dependencies()
    result = dict(version=__version__, python=sys.version.split()[0], platform=platform.platform(),
                  dependencies=checks)
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
        molecule = Chem.AddHs(Chem.MolFromSmiles('O'))
        if AllChem.EmbedMolecule(molecule, randomSeed=42) != 0 or 'V2000' not in Chem.MolToMolBlock(molecule):
            raise ValueError('RDKit 3D conversion failed')
        result['rdkit'] = dict(ok=True, detail='Water SMILES converted to a 3D molfile')
    except Exception as exc:
        result['rdkit'] = dict(ok=False, error=f'{type(exc).__name__}: {exc}')
    from .data import user_home
    store = user_home()
    try:
        store.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=store) as probe:
            probe.write(b'mossbauer doctor')
            probe.flush()
        result['user_store'] = dict(ok=True, path=str(store))
    except OSError as exc:
        result['user_store'] = dict(ok=False, path=str(store), error=str(exc))
    from .site import NoDefaultSiteError, resolve_site
    try:
        selected = resolve_site()
        result['site'] = dict(ok=True, configured=True, name=selected)
    except NoDefaultSiteError as exc:
        result['site'] = dict(ok=True, configured=False, detail=str(exc))
    except Exception as exc:
        result['site'] = dict(ok=False, configured=True, detail=str(exc))
    result['viewer'] = dict(requires_network=True, detail='The 3D viewer loads 3Dmol.js from its CDN; other local workflows remain available offline.')
    install_ok = all(c['ok'] for c in checks.values()) and result['rdkit']['ok'] and sys.version_info >= (3, 10)
    repairs = []
    if not install_ok:
        repairs.append(REPAIR)
    if not result['user_store']['ok']:
        repairs.append('Set MOSSBAUER_DFT_HOME to a writable directory and run mossbauer doctor again.')
    if not result['site']['ok']:
        repairs.append('Fix the selected profile on Cluster setup or with mossbauer site init/default.')
    result['repair'] = ' '.join(repairs)
    result['ok'] = install_ok and result['user_store']['ok'] and result['site']['ok']
    return result
