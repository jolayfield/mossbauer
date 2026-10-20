"""Study comparisons retain selected-version evidence and never pool incompatible protocols."""
import json

import pytest

from mossbauer_dft import project as J, study as S
from mossbauer_dft.cli import main
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _ready, _place, _jobs  # noqa: F401

CAL = 'mcwilliams2017_b3lyp_def2tzvp'


def saved(root, structure, name='compound'):
    ref = _ready(root, structure, name=name)
    for stem, job in _jobs(root, ref).items():
        if job['kind'] == 'mossbauer_sp':
            _place(root, ref, stem)
    J.analyze_version(root, ref.entry_id, calibration=CAL)
    return ref


def test_comparison_retains_source_and_older_selection(proj, fe1):
    ref = saved(proj, fe1)
    original = (ref.path / 'results/analysis.json').read_bytes()
    J.new_version(proj, ref.entry_id, overrides={'nprocs': 8})
    doc = S.compare(proj)
    assert len(doc['rows']) == 2
    assert all(r['entry'] == ref.entry_id and r['version'] == 1 and r['older_version'] for r in doc['rows'])
    assert all(r['result_path'].endswith('v1/results/analysis.json') and r['plan_hash'] for r in doc['rows'])
    assert all(r['result_sha256'] and r['source_outputs'] and r['calibration_snapshot'] for r in doc['rows'])
    assert doc['units']['delta'] == 'mm/s'
    assert (ref.path / 'results/analysis.json').read_bytes() == original
    assert S.compare(proj, [(ref.entry_id, 2)])['rows'] == []
    assert 'no saved analysis' in S.compare(proj, [(ref.entry_id, 2)])['issues'][0]['reason']


def test_stale_result_is_visible_but_excluded_from_metrics(proj, fe1):
    ref = saved(proj, fe1)
    p = next((ref.path / 'outputs').glob('*.out'))
    p.write_text(p.read_text() + '\nchanged')
    doc = S.compare(proj)
    assert all('stale' in r['flags'] and not r['metrics_eligible'] for r in doc['rows'])
    assert doc['summaries'] == []


def test_protocol_groups_unknown_membership_and_training_exclusion(proj, fe1):
    ref = saved(proj, fe1)
    p = ref.path / 'results/analysis.json'
    d = json.loads(p.read_text())
    for r in d['rows']:
        r['abs_deq_exp'] = 1.0
    p.write_text(json.dumps(d))
    doc = S.compare(proj)
    assert len(doc['summaries']) == 2  # B3LYP and TPSSh cannot be pooled
    assert all(g['validation_n'] == 0 and g['membership_unknown_n'] == 1 for g in doc['summaries'])
    d['provenance']['calibration']['training_entries'] = [ref.entry_id]
    p.write_text(json.dumps(d))
    assert S.compare(proj)['summaries'] == []
    assert all(r['training_membership'] == 'training' for r in S.compare(proj)['rows'])


def test_multiple_fe_cannot_reuse_one_experiment(proj, fe1):
    ref = saved(proj, fe1)
    p = ref.path / 'results/analysis.json'
    d = json.loads(p.read_text())
    d['provenance']['system']['n_fe'] = 2
    for r in d['rows']:
        r['abs_deq_exp'] = 1.0
    p.write_text(json.dumps(d))
    doc = S.compare(proj)
    assert all('ambiguous Fe-site experiment' in r['flags'] for r in doc['rows'])
    assert doc['summaries'] == []


def test_cli_compare_json_and_csv(proj, fe1, capsys, tmp_path):
    saved(proj, fe1)
    out = tmp_path / 'comparison.csv'
    main(['project', 'compare', '--project', str(proj), '--json', '--csv', str(out)])
    doc = json.loads(capsys.readouterr().out)
    assert len(doc['rows']) == 2 and 'result_sha256' in out.read_text()
    assert 'mm/s' in out.read_text()


def test_bad_saved_result_does_not_hide_other_entries(proj, fe1):
    a = saved(proj, fe1, 'good')
    b = saved(proj, fe1, 'bad')
    (b.path / 'results/analysis.json').write_text('{bad')
    doc = S.compare(proj)
    assert all(r['entry'] == a.entry_id for r in doc['rows'])
    assert any(i['entry'] == b.entry_id for i in doc['issues'])


def test_bad_provenance_does_not_hide_other_entries(proj, fe1):
    a = saved(proj, fe1, 'good')
    b = saved(proj, fe1, 'bad')
    p = b.path / 'results/analysis.json'
    d = json.loads(p.read_text()); d['provenance'] = ['invalid']
    p.write_text(json.dumps(d))
    doc = S.compare(proj)
    assert all(r['entry'] == a.entry_id for r in doc['rows'])
    assert doc['issues'][0]['entry'] == b.entry_id
