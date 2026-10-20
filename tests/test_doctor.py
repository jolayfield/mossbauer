import json
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('MOSSBAUER_DFT_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('MOSSBAUER_DFT_SITE', raising=False)


def test_doctor_checks_real_conversion_and_missing_site_is_guidance():
    from mossbauer_dft import doctor
    result = doctor.report()
    assert result['ok'] and result['rdkit']['ok'] and result['user_store']['ok']
    assert result['site']['configured'] is False
    assert 'site init' in result['site']['detail']
    assert result['viewer']['requires_network']


def test_doctor_broken_dependency_is_actionable(monkeypatch, capsys):
    from mossbauer_dft import doctor
    from mossbauer_dft.cli import main
    original = doctor.importlib.import_module
    def broken(name):
        if name == 'streamlit':
            raise ImportError('broken installation')
        return original(name)
    monkeypatch.setattr(doctor.importlib, 'import_module', broken)
    with pytest.raises(SystemExit) as exc:
        main(['doctor', '--json'])
    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert not result['ok']
    assert 'streamlit' in result['dependencies']
    assert not result['dependencies']['streamlit']['ok']
    assert 'install' in result['repair']


def test_doctor_invalid_config_fails_without_fallback(tmp_path):
    from mossbauer_dft import doctor
    p = tmp_path / 'home/config.yaml'
    p.parent.mkdir()
    p.write_text('default_site: nonexistent')
    result = doctor.report()
    assert not result['ok'] and not result['site']['ok']


def test_gui_launch_uses_active_python_and_preserves_exit(monkeypatch):
    from mossbauer_dft.cli import cmd_gui
    calls = []
    monkeypatch.setattr('subprocess.call', lambda args: calls.append(args) or 17)
    with pytest.raises(SystemExit) as exc:
        cmd_gui(SimpleNamespace(workdir='/tmp/project with spaces'))
    assert exc.value.code == 17
    assert calls[0][:4] == [sys.executable, '-m', 'streamlit', 'run']
    assert calls[0][-2:] == ['--workdir', '/tmp/project with spaces']
    assert calls[0][calls[0].index('--server.address') + 1] == '127.0.0.1'


def test_missing_gui_dependency_has_guidance_without_launch(monkeypatch):
    from mossbauer_dft import doctor
    from mossbauer_dft.cli import cmd_gui
    original = doctor.importlib.import_module
    def missing(name):
        if name == 'py3Dmol':
            raise ImportError('missing')
        return original(name)
    monkeypatch.setattr(doctor.importlib, 'import_module', missing)
    monkeypatch.setattr('subprocess.call', lambda args: pytest.fail('launched with a missing dependency'))
    with pytest.raises(SystemExit, match='doctor|install'):
        cmd_gui(SimpleNamespace(workdir=None))


def test_doctor_reports_unwritable_store(monkeypatch):
    from mossbauer_dft import doctor
    def denied(*args, **kwargs):
        raise PermissionError('read-only store')
    monkeypatch.setattr(doctor.tempfile, 'TemporaryFile', denied)
    result = doctor.report()
    assert not result['ok'] and not result['user_store']['ok']
    assert 'read-only store' in result['user_store']['error']
