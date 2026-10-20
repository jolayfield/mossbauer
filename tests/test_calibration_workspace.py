import json
from pathlib import Path
import pytest
from mossbauer_dft import plan as P, project as J, calibration_workspace as C
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _ready, _fake_out  # noqa: F401
from test_refset import _set_rho


def workspace(root, structure):
    ref = _ready(root, structure)
    C.create(root, 'series', ref.entry_id)
    for i in range(4):
        C.add_reference(root, 'series', f'r{i}', ref.path / 'structure.xyz', -1, 4, 0.8 - 0.3*i)
    C.prepare(root, 'series')
    folder = C.path(root, 'series')
    for i in range(4):
        deck = (folder / f'inputs/r{i}/r{i}_sp.inp').read_text()
        text = _set_rho(_fake_out(deck, -1, 4), 14000.0 + i)
        (folder / f'outputs/r{i}_sp.out').write_text(text)
    return ref, folder


def test_project_fit_is_local_inspectable_and_preserves_reasons(proj, fe1):
    ref, folder = workspace(proj, fe1)
    fit = C.fit(proj, 'series', {'r3': 'not measured at matching temperature'}, n_boot=10)
    entry = fit['calibration']
    assert entry['verified'] is False and entry['n_training'] == 3
    assert entry['exclusion_reasons']['r3'] and len(entry['calibration']['per_point']) == 3
    assert len(entry['reference_evidence']) == 3 and entry['training_entries'] is None
    assert (proj / fit['path']).is_file() and len(C.fits(proj)) == 1
    assert not list(__import__('mossbauer_dft.references', fromlist=['R']).user_calibration_dir().glob('fit_series*'))
    assert all(r['state'] == 'complete' for r in C.status(proj, 'series')['references'])
    with pytest.raises(J.ProjectError, match='reason'):
        C.fit(proj, 'series', {'r3': ''}, n_boot=10)
    with pytest.raises(J.ProjectError, match='already prepared'):
        C.prepare(proj, 'series')


def test_reference_parity_and_safe_ids_and_approval_gate(proj, fe1):
    ref = J.add_entry(proj, fe1, -1, 4, name='a', oxidation_state=1)
    with pytest.raises(J.ProjectError):
        C.create(proj, '../escape', ref.entry_id)
    C.create(proj, 'series', ref.entry_id)
    with pytest.raises(J.ProjectError, match='parity'):
        C.add_reference(proj, 'series', 'r', ref.path / 'structure.xyz', -1, 3, 0.5)
    C.add_reference(proj, 'series', 'r', ref.path / 'structure.xyz', -1, 4, 0.5)
    with pytest.raises(P.NotApprovedError):
        C.prepare(proj, 'series')
    assert not (C.path(proj, 'series') / 'inputs').exists()


def test_mismatched_reference_protocol_is_rejected(proj, fe1):
    ref, folder = workspace(proj, fe1)
    output = folder / 'outputs/r0_sp.out'
    output.write_text(output.read_text().replace('B3LYP', 'PBE0'))
    with pytest.raises(J.ProjectError, match='protocol differs'):
        C.fit(proj, 'series', n_boot=10)
    assert C.fits(proj) == []


def test_cli_calibration_round_trip_and_explicit_analysis(proj, fe1, capsys):
    from mossbauer_dft.cli import main
    from test_project_status import _jobs, _place
    ref, folder = workspace(proj, fe1)
    main(['project', 'calibration', 'fit', '--project', str(proj), '--workspace', 'series', '--n-boot', '10'])
    fitted = json.loads(capsys.readouterr().out)
    for stem, job in _jobs(proj, ref).items():
        if job['kind'] == 'mossbauer_sp': _place(proj, ref, stem)
    result = J.analyze_version(proj, ref.entry_id, calibration=proj / fitted['path'])
    assert result['provenance']['calibration']['entry']['id'] == fitted['calibration']['id']
    assert result['provenance']['calibration']['entry']['verified'] is False
    assert C.plan_references(proj, 'series')[0]['xyz'].startswith('calibrations/series/')


def test_workspace_paths_and_exclusion_types_are_validated(proj, fe1):
    ref, folder = workspace(proj, fe1)
    with pytest.raises(J.ProjectError, match='reason'):
        C.fit(proj, 'series', ['r0'], n_boot=10)
    p = folder / 'workspace.json'
    doc = json.loads(p.read_text())
    doc['references'][0]['structure'] = '../../outside.xyz'
    p.write_text(json.dumps(doc))
    with pytest.raises(J.ProjectError, match='path'):
        C.plan_references(proj, 'series')
    assert C.fits(proj) == []
