import copy
import pytest
from mossbauer_dft import plan as P, project as J, revision as V
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj  # noqa: F401


def test_preview_is_read_only_and_state_change_is_a_new_entry(proj, fe1):
    ref = J.add_entry(proj, fe1, -1, 4, name='sample', oxidation_state=1)
    original = (ref.path / 'plan.yaml').read_bytes()
    proposal = V.preview(proj, ref.entry_id, multiplicity=2, solvent='water')
    assert proposal['creates'] == 'entry' and any(c['field'] == 'inputs.multiplicity' for c in proposal['changes'])
    assert len(J.list_entries(proj)) == 1 and (ref.path / 'plan.yaml').read_bytes() == original
    new = V.save(proj, proposal)
    assert new.entry_id != ref.entry_id and new.version == 1
    assert (ref.path / 'plan.yaml').read_bytes() == original
    assert J.load_version(proj, new.entry_id)['approval'] is None


@pytest.mark.parametrize('drift', ['version', 'content', 'tampering'])
def test_preview_save_rejects_drift_without_writing(proj, fe1, drift):
    ref = J.add_entry(proj, fe1, -1, 4, name='sample', oxidation_state=1)
    proposal = V.preview(proj, ref.entry_id, overrides={'nprocs': 8})
    if drift == 'version':
        J.new_version(proj, ref.entry_id, solvent='water')
    elif drift == 'content':
        P.save_plan(P.update_plan(J.load_version(proj, ref.entry_id), solvent='water'), ref.path / 'plan.yaml')
    else:
        proposal['edits']['overrides']['nprocs'] = 4
    before = [v.version for v in J.list_versions(proj, ref.entry_id)]
    with pytest.raises(J.ProjectError, match='preview'):
        V.save(proj, proposal)
    assert [v.version for v in J.list_versions(proj, ref.entry_id)] == before


def test_cli_preview_does_not_write_and_hash_pin_rejects_changed_source(proj, fe1, capsys):
    import json
    from mossbauer_dft.cli import main
    ref = J.add_entry(proj, fe1, -1, 4, name='sample', oxidation_state=1)
    args = ['project', 'edit', '--project', str(proj), '--entry', ref.entry_id, '--multiplicity', '2']
    main(args + ['--preview'])
    proposal = json.loads(capsys.readouterr().out)
    assert proposal['creates'] == 'entry' and len(J.list_entries(proj)) == 1
    J.new_version(proj, ref.entry_id, solvent='water')
    with pytest.raises(SystemExit):
        main(args + ['--expect-hash', proposal['base_hash']])
    assert len(J.list_entries(proj)) == 1
