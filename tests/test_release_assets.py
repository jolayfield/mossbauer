import importlib.util
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def release_module():
    spec = importlib.util.spec_from_file_location('release_assets', ROOT / 'scripts/release_assets.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_and_public_docs_exclude_private_site_identifiers():
    forbidden = release_module().private_markers()
    if not forbidden:
        pytest.skip('MOSSBAUER_PRIVATE_MARKERS is not set')
    files = [p for p in (ROOT / 'src/mossbauer_dft').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [ROOT / 'docs/releases/README.md', ROOT / 'docs/releases/INSTALL.md']
    for path in files:
        data = path.read_bytes().lower()
        assert not any(term in data for term in forbidden), path


def test_release_asset_selection_uses_exact_wheel_and_no_source_archive(tmp_path):
    module = release_module()
    with pytest.raises(ValueError, match='wheel|Wheel'):
        module.prepare(ROOT / 'pyproject.toml', tmp_path / 'out', ROOT / 'docs/releases', '0.6.0')


def test_release_refuses_private_content_even_in_wheel_metadata(tmp_path):
    module = release_module()
    wheel = tmp_path / 'mossbauer_dft-0.6.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as z:
        z.writestr('mossbauer_dft-0.6.0.dist-info/METADATA', 'Name: mossbauer-dft\nVersion: 0.6.0\n\nprivate Cluster-X details')
    with pytest.raises(ValueError, match='private|Private'):
        module.prepare(wheel, tmp_path / 'out', ROOT / 'docs/releases', '0.6.0', markers=(b'cluster-x',))
    assert not (tmp_path / 'out').exists()


def test_private_markers_come_from_the_environment_only():
    module = release_module()
    assert module.private_markers({}) == ()
    assert module.private_markers({'MOSSBAUER_PRIVATE_MARKERS': ' Cluster-X, /home/Group\nimage-dir '}) == (
        b'cluster-x', b'/home/group', b'image-dir')


def test_release_can_require_configured_markers(tmp_path):
    module = release_module()
    with pytest.raises(ValueError, match='No private markers'):
        module.prepare(tmp_path / 'any.whl', tmp_path / 'out', ROOT / 'docs/releases', '0.6.0', markers=(), require_markers=True)


def test_selected_assets_are_allowlisted_and_checksummed(tmp_path):
    import hashlib
    module = release_module()
    wheel = tmp_path / 'mossbauer_dft-0.6.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as z:
        z.writestr('mossbauer_dft-0.6.0.dist-info/METADATA', 'Name: mossbauer-dft\nVersion: 0.6.0\n')
        for name in ('doctor.py', 'gui/views/cluster.py', 'handoff.py', 'data/sites/example.yaml',
                     'data/calibrations.yaml', 'data/training_set.csv'):
            z.writestr('mossbauer_dft/' + name, 'generic fixture')
    docs = tmp_path / 'docs'
    docs.mkdir()
    for name in ('README.md', 'INSTALL.md', 'LICENSE'):
        (docs / name).write_text('Public instructions: ' + wheel.name)
    (docs / 'private-notes.md').write_text('must never be selected')
    out = tmp_path / 'out'
    module.prepare(wheel, out, docs, '0.6.0')
    assert {p.name for p in out.iterdir()} == {wheel.name, 'README.md', 'INSTALL.md', 'LICENSE', 'SHA256SUMS'}
    for line in (out / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
