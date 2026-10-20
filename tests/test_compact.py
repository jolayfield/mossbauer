import copy
import json
import subprocess
import sys
import pytest
from mossbauer_dft import project as J, compact as C, harvest as H
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _ready, _jobs, _place  # noqa: F401


def bundle(root, structure, tmp_path):
    ref = _ready(root, structure)
    for stem, job in _jobs(root, ref).items():
        if job['kind'] == 'mossbauer_sp': _place(root, ref, stem)
    manifest = ref.path / 'inputs/harvest-manifest.json'
    doc = H.harvest(manifest, ref.path / 'outputs')
    source = tmp_path / 'bundle.json'; source.write_text(json.dumps(doc))
    return ref, doc, source


def test_full_and_compact_analysis_have_identical_scientific_rows(proj, fe1, tmp_path):
    ref, doc, source = bundle(proj, fe1, tmp_path)
    assert len(doc['records']) == 2 and len(doc['missing']) == 1
    raw = J.analyze_version(proj, ref.entry_id)
    for p in (ref.path / 'outputs').glob('*.out'): p.unlink()
    outcome = C.import_bundle(proj, ref.entry_id, 1, source)
    assert len(outcome['imported']) == 2
    saved = J.analyze_version(proj, ref.entry_id)
    assert saved['rows'] == raw['rows']
    assert all(s['kind'] == 'compact' for s in saved['evidence_sources'])
    assert any('not verified locally' in n for n in saved['notes'])
    assert C.import_bundle(proj, ref.entry_id, 1, source)['imported'] == []


@pytest.mark.parametrize('fault', ['identity', 'traversal', 'state', 'efg', 'hash', 'site'])
def test_invalid_bundle_is_rejected_without_any_output_mutation(proj, fe1, tmp_path, fault):
    ref, doc, source = bundle(proj, fe1, tmp_path)
    before = {p.name:p.read_bytes() for p in (ref.path / 'outputs').iterdir()}
    stem = next(iter(doc['records'])); record = doc['records'][stem]
    if fault == 'identity': doc['plan_hash'] = '0'*64
    if fault == 'traversal': doc['records']['../escape'] = doc['records'].pop(stem)
    if fault == 'state': record['parsed']['multiplicity'] = 99
    if fault == 'efg': record['parsed']['fe_sites'][0]['v_eigs_orca'] = ['bad', 1, 2]
    if fault == 'site': record['parsed']['fe_sites'][0]['index'] = 1
    if fault == 'hash': record['source']['sha256'] = 'not-a-hash'
    source.write_text(json.dumps(doc))
    with pytest.raises((ValueError, J.ProjectError)): C.import_bundle(proj, ref.entry_id, 1, source)
    assert {p.name:p.read_bytes() for p in (ref.path / 'outputs').iterdir()} == before


def test_portable_harvester_uses_no_site_packages(proj, fe1, tmp_path):
    ref, doc, source = bundle(proj, fe1, tmp_path)
    out = tmp_path / 'portable.json'
    run = subprocess.run([sys.executable, '-S', str(ref.path / 'inputs/harvest.pyz'),
                          str(ref.path / 'inputs/harvest-manifest.json'), str(ref.path / 'outputs'), str(out)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(out.read_text())['records'] == doc['records']


def test_replacement_is_explicit_and_archives_old_evidence(proj, fe1, tmp_path):
    ref, doc, source = bundle(proj, fe1, tmp_path)
    for p in (ref.path / 'outputs').glob('*.out'): p.unlink()
    C.import_bundle(proj, ref.entry_id, 1, source)
    stem = next(iter(doc['records']))
    doc['records'][stem]['source']['sha256'] = 'a'*64
    source.write_text(json.dumps(doc))
    with pytest.raises(J.ProjectError, match='archive'): C.import_bundle(proj, ref.entry_id, 1, source)
    C.import_bundle(proj, ref.entry_id, 1, source, replace=True)
    assert len(list((ref.path / 'evidence-history').rglob('*.json'))) == 1


def test_cli_harvest_import_analyze_round_trip(proj, fe1, tmp_path, capsys):
    from mossbauer_dft.cli import main
    ref, doc, source = bundle(proj, fe1, tmp_path)
    destination = tmp_path / 'cli-bundle.json'
    args = ['--project', str(proj), '--entry', ref.entry_id, '--version', '1']
    main(['project', 'harvest'] + args + ['--out', str(destination)])
    capsys.readouterr()
    for p in (ref.path / 'outputs').glob('*.out'): p.unlink()
    main(['project', 'import-results'] + args + ['--bundle', str(destination)])
    imported = json.loads(capsys.readouterr().out)
    assert len(imported['imported']) == 2
    main(['project', 'analyze'] + args)
    result = J.load_results(proj, ref.entry_id)
    assert all(s['kind'] == 'compact' and s['remote_source']['sha256'] for s in result['evidence_sources'])


@pytest.mark.parametrize('field,value', [('fingerprint', []), ('hyperfine', [None]), ('warnings', None), ('charge', True)])
def test_malformed_auxiliary_fields_are_rejected_before_import(proj, fe1, tmp_path, field, value):
    ref, doc, source = bundle(proj, fe1, tmp_path)
    before = {p.name: p.read_bytes() for p in (ref.path / 'outputs').iterdir()}
    record = next(iter(doc['records'].values()))
    record['parsed'][field] = value
    source.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        C.import_bundle(proj, ref.entry_id, 1, source)
    assert {p.name: p.read_bytes() for p in (ref.path / 'outputs').iterdir()} == before
