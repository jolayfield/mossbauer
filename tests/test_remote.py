"""SSH return uses the existing all-record compact import, never credentials or a real cluster."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from mossbauer_dft import plan as P, project as J, remote
from mossbauer_dft.cli import main
from test_compact import bundle
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj  # noqa: F401


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def returned(proj, fe1, tmp_path):
    ref, doc, source = bundle(proj, fe1, tmp_path)
    for p in (ref.path / 'outputs').glob('*.out'):
        p.unlink()
    return ref, doc, source


def _fake_download(monkeypatch, source, hook=None):
    calls = []
    def download(command, destination):
        calls.append(command)
        shutil.copyfile(source, destination)
        if hook:
            hook()
    monkeypatch.setattr(remote, '_download', download)
    return calls


def test_ssh_arguments_keep_remote_shell_characters_in_one_filename(tmp_path):
    path = str(tmp_path / "output's $(touch SHOULD_NOT_EXIST); result.json")
    Path(path).write_bytes(b'{}')
    cmd = remote.ssh_command('person@cluster-alias', path)
    assert cmd[0] == 'ssh' and cmd[-2] == 'person@cluster-alias'
    assert 'BatchMode=yes' in cmd and 'StrictHostKeyChecking=yes' in cmd
    assert 'ForwardAgent=no' in cmd and 'ClearAllForwardings=yes' in cmd and 'PermitLocalCommand=no' in cmd
    assert shlex.split(cmd[-1])[-1] == path
    assert subprocess.run(['sh', '-c', cmd[-1]], capture_output=True, check=True).stdout == b'{}'
    assert not (Path.cwd() / 'SHOULD_NOT_EXIST').exists()


@pytest.mark.parametrize('host,path', [('-oProxyCommand=bad', '/x.json'), ('a;bad', '/x.json'),
    ('a b', '/x.json'), ('ssh://host', '/x.json'), ('a', 'relative.json'), ('a', '/dir/'),
    ('a', '/file\ncommand'), ('a', '/file\x00'), ('a', '/file\x7f')])
def test_bad_transport_inputs_never_connect(proj, returned, monkeypatch, host, path):
    calls = _fake_download(monkeypatch, returned[2])
    before = _tree(proj)
    with pytest.raises(remote.RemoteError):
        remote.fetch_results(proj, returned[0].entry_id, 1, host, path)
    assert not calls and _tree(proj) == before


def test_fetch_import_repeat_and_cli(proj, returned, monkeypatch, capsys):
    ref, _, source = returned
    calls = _fake_download(monkeypatch, source)
    args = ['project', 'fetch-results', '--project', str(proj), '--entry', ref.entry_id,
            '--version', '1', '--host', 'cluster', '--remote-path', '/work/results.json']
    main(args)
    result = json.loads(capsys.readouterr().out)
    assert len(result['imported']) == 2 and result['missing'] and 'not verified locally' in result['warning']
    result = remote.fetch_results(proj, ref.entry_id, 1, 'cluster', '/work/results.json')
    assert len(result['unchanged']) == 2 and not result['imported'] and len(calls) == 2
    assert all(j.state == 'complete' for j in J.scan_version(proj, ref.entry_id).jobs if j.kind == 'mossbauer_sp')


@pytest.mark.parametrize('fault', ['version', 'schema', 'json', 'nesting', 'missing', 'rejected'])
def test_bad_remote_bundle_writes_nothing(proj, returned, monkeypatch, fault):
    ref, doc, source = returned
    if fault == 'version': doc['version'] = 99
    if fault == 'schema': doc['format'] = 'other'
    if fault == 'missing': doc['missing'] = 5
    if fault == 'rejected': doc['rejected'] = ['not a rejection record']
    source.write_text('broken {' if fault == 'json' else '[' * 1500 + '0' + ']' * 1500 if fault == 'nesting' else json.dumps(doc))
    _fake_download(monkeypatch, source)
    before = _tree(proj)
    with pytest.raises((ValueError, J.ProjectError)):
        remote.fetch_results(proj, ref.entry_id, 1, 'cluster', '/results.json')
    assert _tree(proj) == before


def test_changed_plan_during_transfer_is_not_imported(proj, returned, monkeypatch):
    ref, _, source = returned
    def change():
        plan = J.load_version(proj, ref.entry_id)
        plan['inputs']['temperature_K'] = 99
        P.save_plan(plan, ref.path / 'plan.yaml')
    _fake_download(monkeypatch, source, change)
    with pytest.raises(remote.RemoteError, match='changed during transfer'):
        remote.fetch_results(proj, ref.entry_id, 1, 'cluster', '/results.json')
    assert not list((ref.path / 'outputs').iterdir())


def test_conflicts_need_explicit_replacement_and_keep_history(proj, returned, monkeypatch):
    ref, doc, source = returned
    _fake_download(monkeypatch, source)
    remote.fetch_results(proj, ref.entry_id, 1, 'cluster', '/results.json')
    next(iter(doc['records'].values()))['source']['sha256'] = 'a' * 64
    source.write_text(json.dumps(doc))
    before = _tree(proj)
    with pytest.raises(J.ProjectError, match='archive'):
        remote.fetch_results(proj, ref.entry_id, 1, 'cluster', '/results.json')
    assert _tree(proj) == before
    remote.fetch_results(proj, ref.entry_id, 1, 'cluster', '/results.json', replace=True)
    assert len(list((ref.path / 'evidence-history').rglob('*.json'))) == 1


def _local_process(monkeypatch, code):
    popen = subprocess.Popen
    processes = []
    def start(command, **kw):
        p = popen([sys.executable, '-c', code], **kw)
        processes.append(p)
        return p
    monkeypatch.setattr(remote.subprocess, 'Popen', start)
    return processes


@pytest.mark.parametrize('code,match', [
    ("import sys;sys.stderr.write('Permission denied');sys.exit(255)", 'Permission denied'),
    ('pass', 'empty'),
    ("import sys;sys.stdout.buffer.write(b'x'*5000001)", 'exceeds 5 MB'),
    ("import sys;sys.stderr.buffer.write(b'x'*20000)", 'excessive error'),
    ('import time;time.sleep(5)', 'timed out'),
])
def test_transport_errors_are_bounded_and_process_reaped(tmp_path, monkeypatch, code, match):
    processes = _local_process(monkeypatch, code)
    monkeypatch.setattr(remote, 'TIMEOUT', 0.5)
    with pytest.raises(remote.RemoteError, match=match):
        remote._download(['unused'], tmp_path / 'download')
    assert processes[0].poll() is not None
    assert (tmp_path / 'download').stat().st_size <= 5_000_000


def test_transport_success_streams_bytes(tmp_path, monkeypatch):
    processes = _local_process(monkeypatch, "import sys;sys.stdout.buffer.write(b'{\"ok\":true}')")
    remote._download(['unused'], tmp_path / 'download')
    assert (tmp_path / 'download').read_bytes() == b'{"ok":true}' and processes[0].returncode == 0


def test_ssh_not_installed_is_actionable(tmp_path, monkeypatch):
    def missing(*a, **kw):
        raise FileNotFoundError('ssh')
    monkeypatch.setattr(remote.subprocess, 'Popen', missing)
    with pytest.raises(remote.RemoteError, match='install ssh'):
        remote._download(['ssh'], tmp_path / 'download')


def test_fetch_pins_original_newest_version_during_transfer(proj, returned, monkeypatch):
    ref, _, source = returned
    def advance():
        newer = J.new_version(proj, ref.entry_id, overrides={'highfield': True})
        assert newer.entry_id == ref.entry_id and newer.version == 2
    _fake_download(monkeypatch, source, advance)
    outcome = remote.fetch_results(proj, ref.entry_id, None, 'cluster', '/results.json')
    assert len(outcome['imported']) == 2
    assert len(list((ref.path / 'outputs').glob('*.compact.json'))) == 2
    assert not list((J.version_dir(proj, ref.entry_id, 2) / 'outputs').glob('*'))
