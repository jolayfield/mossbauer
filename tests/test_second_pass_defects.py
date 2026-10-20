"""Regressions for defects found during the 2026-10-18 ideation pass (decisions D46-D49)."""
import json

import pytest

from mossbauer_dft import harvest as H, report, study
from mossbauer_dft.cli import main
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _ready, _jobs, _place  # noqa: F401


def test_one_unparseable_output_is_rejected_not_fatal(proj, fe1, monkeypatch):  # D47
    ref = _ready(proj, fe1)
    stems = [s for s, j in _jobs(proj, ref).items() if j['kind'] == 'mossbauer_sp']
    for s in stems: _place(proj, ref, s)
    real = H.parse_orca_text
    def boom(text, name):
        if name == f'{stems[0]}.out': raise RuntimeError('parser crashed')
        return real(text, name)
    monkeypatch.setattr(H, 'parse_orca_text', boom)
    doc = H.harvest(ref.path / 'inputs/harvest-manifest.json', ref.path / 'outputs')
    assert [r['job'] for r in doc['rejected']] == [stems[0]]
    assert 'parser crashed' in doc['rejected'][0]['reason']
    assert set(doc['records']) == set(stems[1:])


def test_harvest_refuses_bundle_above_import_limit(proj, fe1, tmp_path, monkeypatch):  # D47
    ref = _ready(proj, fe1)
    monkeypatch.setattr(H, 'harvest', lambda m, o: dict(pad='y' * (H.MAX_BYTES + 1)))
    monkeypatch.setattr('sys.argv', ['harvest', str(ref.path / 'inputs/harvest-manifest.json'), str(ref.path / 'outputs'),
                                     str(tmp_path / 'b.json')])
    with pytest.raises(SystemExit, match='5 MB'):
        H.main()
    assert not (tmp_path / 'b.json').exists()


def test_shareable_study_json_has_no_local_paths(proj, fe1):  # D46
    doc = dict(rows=[dict(compound='x', entry='e', version=1, result_path='/Users/someone/private/results/analysis.json',
                          source_outputs=[dict(name='/Users/someone/private/a.out', sha256='a' * 64, size=1)])])
    text = json.dumps(report.public_snapshot(doc))
    assert '/Users/someone' not in text and 'analysis.json' in text


def test_guide_and_example_accept_the_same_project_spellings(tmp_path, capsys, _home):  # D49
    root = tmp_path / 'ex'
    main(['project', 'example', '--project', str(root)])
    capsys.readouterr()
    main(['project', 'guide', str(root)])
    positional = json.loads(capsys.readouterr().out)
    main(['project', 'guide', '--project', str(root)])
    assert json.loads(capsys.readouterr().out) == positional
    assert positional['next'] == 'approve'
