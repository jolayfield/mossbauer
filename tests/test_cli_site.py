import json

import pytest

from mossbauer_dft.cli import main
from mossbauer_dft.site import SiteProfile, save_profile


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('MOSSBAUER_DFT_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('MOSSBAUER_DFT_SITE', raising=False)


def test_default_resolution_requires_choice_and_honors_precedence(monkeypatch):
    from mossbauer_dft.site import resolve_site, set_default_site
    with pytest.raises(ValueError, match='site init'):
        resolve_site()
    for name in ('a', 'b', 'c'):
        save_profile(SiteProfile(name=name, partition='compute', sif='/images/orca.sif'))
    set_default_site('a')
    assert resolve_site() == 'a'
    monkeypatch.setenv('MOSSBAUER_DFT_SITE', 'b')
    assert resolve_site() == 'b'
    assert resolve_site('c') == 'c'
    monkeypatch.setenv('MOSSBAUER_DFT_SITE', 'missing')
    with pytest.raises(FileNotFoundError):
        resolve_site()


def test_cli_create_validate_list_and_default(tmp_path, capsys):
    main(['site', 'init', 'personal', '--partition', 'compute', '--sif', '/images/orca.sif', '--account', 'chem'])
    assert SiteProfile.load('personal').account == 'chem'
    main(['site', 'default', 'personal'])
    capsys.readouterr()
    main(['site', 'list', '--json'])
    out = json.loads(capsys.readouterr().out)
    assert out['default'] == 'personal'
    assert next(r for r in out['profiles'] if r['name'] == 'personal')['origin'] == 'user'
    main(['site', 'validate', 'personal'])
    assert 'valid' in capsys.readouterr().out.lower()
    with pytest.raises(SystemExit):
        main(['site', 'init', 'personal', '--partition', 'compute', '--sif', '/images/orca.sif'])


def test_invalid_config_is_not_silently_ignored(tmp_path):
    from mossbauer_dft.data import user_home
    from mossbauer_dft.site import resolve_site
    user_home().mkdir(parents=True)
    (user_home() / 'config.yaml').write_text('default_site: [not, a, name]')
    with pytest.raises(ValueError):
        resolve_site()


from test_project_status import proj, fe1, _ready  # noqa: E402,F401


def test_resubmit_keeps_original_profile_after_default_and_profile_edits(proj, fe1):
    from mossbauer_dft import project as J
    from mossbauer_dft.site import set_default_site
    a = SiteProfile(name='a', partition='compute', sif='/images/orca.sif', account='original')
    save_profile(a)
    save_profile(SiteProfile(name='b', partition='other', sif='/images/orca.sif', account='other'))
    set_default_site('a')
    ref = _ready(proj, fe1)
    stem = J.load_version(proj, ref.entry_id)['jobs'][0]['stem']
    set_default_site('b')
    a.account = 'changed'
    save_profile(a, replace=True)
    result = J.write_resubmit(proj, ref.entry_id, stems=[stem])
    assert '--account=original' in '\n'.join(result['commands'])
    assert result['site'] == 'a'
    assert J.load_version(proj, ref.entry_id)['plan_hash'] == json.loads((ref.path / 'inputs/site.json').read_text())['plan_hash']


def test_invalid_site_does_not_remove_written_manifest(proj, fe1):
    from mossbauer_dft import project as J
    from mossbauer_dft.site import set_default_site
    save_profile(SiteProfile(name='a', partition='compute', sif='/images/orca.sif'))
    set_default_site('a')
    ref = _ready(proj, fe1)
    path = ref.path / 'inputs/manifest.json'
    before = path.read_bytes()
    with pytest.raises(FileNotFoundError):
        J.write_version_inputs(proj, ref.entry_id, site='missing')
    assert path.read_bytes() == before


def test_resubmit_cannot_mix_a_new_profile_with_old_scheduler_script(proj, fe1):
    from mossbauer_dft import project as J
    from mossbauer_dft.site import set_default_site
    for name in ('a', 'b'):
        save_profile(SiteProfile(name=name, partition=name, sif='/images/orca.sif'))
    set_default_site('a')
    ref = _ready(proj, fe1)
    stem = J.load_version(proj, ref.entry_id)['jobs'][0]['stem']
    with pytest.raises(ValueError, match='regenerate'):
        J.write_resubmit(proj, ref.entry_id, stems=[stem], site='b')
