"""Report artifacts share the selected saved snapshot and escape untrusted text."""
import csv
import io
import json
from zipfile import ZipFile

import pytest

from mossbauer_dft import project as J, study, report
from mossbauer_dft.cli import main
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj  # noqa: F401
from test_study import saved


def test_report_snapshot_versions_hashes_and_privacy(proj, fe1, monkeypatch):
    ref = saved(proj, fe1)
    J.new_version(proj, ref.entry_id, overrides={'nprocs': 8})
    doc = study.compare(proj, [(ref.entry_id, 1)])
    for row in doc['rows']:
        row['compound'] = '<script>alert(1)</script>'
        row['private_key'] = 'SECRET_DO_NOT_EXPORT'
        row['provenance']['inputs']['overrides'] = {'api_key': 'SECRET_DO_NOT_EXPORT'}
        row['calibration_snapshot']['entry']['path'] = '/Users/private/cal.yaml'
        row['calibration'] = '/Users/private/cal.yaml'
        row['calibration_snapshot']['entry']['api_key'] = 'SECRET_DO_NOT_EXPORT'
    from mossbauer_dft import workflow
    monkeypatch.setattr(workflow, 'analyze', lambda *a, **k: pytest.fail('export must not recompute'))
    data, html = report.bundle(doc, '<script>title</script>')
    with ZipFile(io.BytesIO(data)) as z:
        assert set(z.namelist()) == {'index.html', 'study.json', 'results.csv', 'README.txt', 'si_table.csv', 'si_table.tex',
                                      'methods.txt', 'references.bib', 'MANIFEST.json'}
        payload = z.read('study.json').decode()
        public = json.loads(payload)
        rows = list(csv.DictReader(io.StringIO(z.read('results.csv').decode())))
        assert len(rows) == len(public['rows']) == len(doc['rows'])
        assert all(r['version'] == 1 and r['older_version'] for r in public['rows'])
        assert public['rows'][0]['result_sha256'] == doc['rows'][0]['result_sha256']
        assert public['rows'][0]['source_outputs'][0]['sha256']
        assert public['rows'][0]['protocol']['fingerprint']['basis']
        assert 'SECRET_DO_NOT_EXPORT' not in payload and '/Users/private' not in payload
        assert z.read('index.html').decode() == html
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert ' src=' not in html and ' href=' not in html
    assert 'mm/s' in html and 'human review' in html


def test_cli_report_no_overwrite(proj, fe1, tmp_path):
    ref = saved(proj, fe1)
    dest = tmp_path / 'report.zip'
    args = ['project', 'report', '--project', str(proj), '--select', f'{ref.entry_id}:1', '--out', str(dest)]
    main(args)
    before = dest.read_bytes()
    with pytest.raises((FileExistsError, SystemExit)):
        main(args)
    assert dest.read_bytes() == before


def test_empty_selection_retains_omissions():
    data, _ = report.bundle(dict(rows=[], summaries=[], selection=[], issues=[dict(entry='missing', version=1, reason='no saved analysis')]))
    with ZipFile(io.BytesIO(data)) as z:
        assert 'no saved analysis' in z.read('index.html').decode()
