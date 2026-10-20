import json
import pytest
from mossbauer_dft import families as F, project as J, plan as P
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _approve, _jobs, _place  # noqa: F401


def test_states_are_previewed_then_independently_saved(proj, fe1):
    ref = J.add_entry(proj, fe1, -1, 4, name='compound', oxidation_state=1)
    proposal = F.preview(proj, ref.entry_id, [{'multiplicity': 2}, {'multiplicity': 4}])
    assert proposal['additional_jobs'] > 0 and len(J.list_entries(proj)) == 1
    doc = F.generate(proj, 'Spin series', proposal)
    assert len(doc['created']) == 1 and len(doc['family']['entries']) == 2
    assert all(J.load_version(proj, e)['approval'] is None for e in doc['family']['entries'])
    with pytest.raises(P.PlanError): F.preview(proj, ref.entry_id, [{'multiplicity': 3}])
    assert len(J.list_entries(proj)) == 2
    proposal = F.preview(proj, ref.entry_id, [{'multiplicity': 6}])
    J.new_version(proj, ref.entry_id, solvent='water')
    with pytest.raises(J.ProjectError, match='source changed'): F.generate(proj, 'new', proposal)


def test_energy_cohorts_do_not_crown_incomplete_or_incompatible_family(proj, fe1):
    refs = [J.add_entry(proj, fe1, -1, m, name='compound', oxidation_state=1) for m in [2, 4]]
    F.create(proj, 'states', [r.entry_id for r in refs])
    for i, ref in enumerate(refs):
        _approve(proj, ref); J.write_version_inputs(proj, ref.entry_id)
        for stem, job in _jobs(proj, ref).items():
            if job['kind'] == 'mossbauer_sp': _place(proj, ref, stem)
        J.analyze_version(proj, ref.entry_id)
        p = ref.path / 'results/analysis.json'
        d = json.loads(p.read_text())
        for row in d['rows']: row['energy_Eh'] = -100 + i*0.0001
        p.write_text(json.dumps(d))
    doc = F.energies(proj, 'states')
    assert len(doc['rows']) == 4 and all(r['comparison_complete'] for r in doc['rows'])
    assert sum(r['lowest_computed'] for r in doc['rows']) == 2
    assert all(r['within_tolerance'] for r in doc['rows'])
    (refs[1].path / 'results/analysis.json').unlink()
    doc = F.energies(proj, 'states')
    assert all(not r['lowest_computed'] and not r['comparison_complete'] for r in doc['rows'])
    with pytest.raises(J.ProjectError): F.energies(proj, 'states', -1)


def test_cli_state_preview_requires_matching_hash_to_generate(proj, fe1, capsys):
    from mossbauer_dft.cli import main
    ref = J.add_entry(proj, fe1, -1, 4, name='compound', oxidation_state=1)
    args = ['--project', str(proj), '--entry', ref.entry_id, '--candidates', '[{"multiplicity": 2}]']
    main(['project', 'family', 'preview'] + args)
    preview = json.loads(capsys.readouterr().out)
    with pytest.raises(SystemExit): main(['project', 'family', 'generate'] + args)
    main(['project', 'family', 'generate'] + args + ['--expect-hash', preview['base_hash']])
    assert len(json.loads(capsys.readouterr().out)['family']['entries']) == 2
