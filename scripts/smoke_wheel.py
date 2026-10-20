"""Install the public wheel exactly as documented and verify it outside the checkout."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel-dir', type=Path, required=True)
    a = parser.parse_args()
    wheels = list(a.wheel_dir.glob('*.whl'))
    if len(wheels) != 1:
        raise SystemExit('Expected exactly one release wheel')
    wheel = wheels[0].resolve()
    checksum_file = a.wheel_dir / 'SHA256SUMS'
    expected = {}
    for line in checksum_file.read_text().splitlines():
        digest, name = line.split('  ', 1)
        expected[name] = digest
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if expected.get(wheel.name) != actual:
        raise SystemExit('Release wheel checksum does not match SHA256SUMS')
    print(f'Testing SHA-256 {actual}  {wheel.name}', flush=True)
    subprocess.run(['uv', 'tool', 'install', '--reinstall', '--python', '3.12', str(wheel)], check=True)
    tool_root = Path(subprocess.check_output(['uv', 'tool', 'dir'], text=True).strip()) / 'mossbauer-dft'
    python = tool_root / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    with tempfile.TemporaryDirectory(prefix='mossbauer-wheel-smoke-') as d:
        env = dict(os.environ, MOSSBAUER_DFT_HOME=str(Path(d) / 'home'))
        env.pop('PYTHONPATH', None)
        env.pop('MOSSBAUER_DFT_SITE', None)
        subprocess.run([str(python), str(Path(__file__).resolve()), '--installed'], cwd=d, env=env, check=True)


def installed():
    import importlib.metadata
    import io
    import json
    import socket
    import signal
    import time
    from urllib.request import urlopen
    import zipfile
    import mossbauer_dft
    from mossbauer_dft import __version__, doctor, project as J, plan as P, handoff as H
    from mossbauer_dft.data import available, available_refsets
    from mossbauer_dft.gui.views import cluster, inputs, new  # noqa: F401
    from mossbauer_dft.site import available_sites
    from mossbauer_dft.structure import Structure, read_structure
    from mossbauer_dft.structure_sources import from_smiles
    assert Path(mossbauer_dft.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert importlib.metadata.version('mossbauer-dft') == __version__
    assert all(available().values()) and available_refsets() and available_sites() == ['example']
    health = doctor.report()
    print(json.dumps(health, indent=2))
    assert health['ok'] and not health['site']['configured']
    structure = from_smiles('Cl[Fe-](Cl)(Cl)Cl').structure
    structure.comment = 'Mössbauer Δρ Ł'
    xyz = Path.cwd() / 'Ł-structure.xyz'
    structure.write_xyz(xyz)
    assert b'\r' not in xyz.read_bytes()
    for reader in (Structure.from_xyz, read_structure):
        assert reader(xyz).to_xyz_text() == structure.to_xyz_text()
    root = Path.cwd() / 'project'
    J.create_project(root)
    ref = J.add_entry(root, structure, -1, 6, name='wheel-smoke', oxidation_state=3)
    J.approve_versions(root, [ref.entry_id], 'wheel smoke', 'cli')
    J.write_version_inputs(root, ref.entry_id, 1, site='example')
    for path in (ref.path / 'inputs').iterdir():
        if path.suffix in ('.sh', '.slurm'):
            assert b'\r' not in path.read_bytes(), path.name
    with zipfile.ZipFile(io.BytesIO(H.export_inputs(root, ref.entry_id, 1, agent=True))) as z:
        assert {'AGENT.md', 'submit.sh', 'harvest.pyz'} <= set(z.namelist())
    P.require_approved(J.load_version(root, ref.entry_id))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, STREAMLIT_SERVER_PORT=str(port),
               STREAMLIT_SERVER_HEADLESS='true', STREAMLIT_BROWSER_GATHER_USAGE_STATS='false')
    with open('gui-smoke.log', 'w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-m', 'mossbauer_dft.cli', 'gui'], env=env, stdout=log, stderr=log,
                                   start_new_session=os.name != 'nt',
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(Path('gui-smoke.log').read_text())
                try:
                    with urlopen(f'http://127.0.0.1:{port}/_stcore/health', timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError('Installed GUI did not become healthy')
        finally:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    print('Installed wheel: version, default dependencies, data, Fe conversion, UTF-8 geometry roundtrip, approved export, LF scripts and GUI health passed')


if __name__ == '__main__':
    if sys.argv[1:] == ['--installed']:
        installed()
    else:
        main()
