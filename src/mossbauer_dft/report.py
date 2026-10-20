"""Offline study snapshots: explicit scientific fields, no live computation or assets."""
from __future__ import annotations

import csv
import io
import hashlib
import json
import math
import re
from collections import Counter
from html import escape
from pathlib import PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED

from . import project as J, study, trust

ROW_FIELDS = "entry compound version newest_version older_version job atom functional delta delta_exp delta_lo delta_hi abs_deq abs_deq_exp deq_signed energy_Eh rho0 s2 s2_ideal eta ok delta_note trust_flags nearest_refs delta_status delta_status_reasons training_membership flags warnings metrics_eligible delta_metrics_eligible deq_metrics_eligible plan_hash result_sha256 analyzed_at calibration delta_unit abs_deq_unit energy_unit".split()
FP_FIELDS = "functional basis fe_basis solvent program grid special_grid_int_acc special_grid_atoms ri".split()
MODEL_FIELDS = "alpha C beta sigma n r2 rmse mae loo_rmse loo_mae level verified names rho0 delta n_boot seed".split()


def _pick(value, fields):
    return {k: value[k] for k in fields if k in value} if isinstance(value, dict) else {}


def _path(value):
    # Portable filenames only; local directory names are not scientific evidence.
    return PurePosixPath(str(value).replace('\\', '/')).name


def _protocol(value):
    value = value or {}
    out = _pick(value, ['solvent', 'temperature_K', 'q_barn'])
    for key in ('fingerprint', 'output_fingerprint'):
        out[key] = _pick(value.get(key), FP_FIELDS)
    out['geometry'] = _pick(value.get('geometry'), ['mode', 'functional', 'basis', 'grid', 'freq', 'extra_keywords', 'as_given_label'])
    return out


def _portable_text(value):
    if isinstance(value, str):
        return re.sub(r"(?<![\w:])(?:/[A-Za-z0-9_.~-]+){2,}|[A-Za-z]:[\\\\][^\s'\"]+", lambda m: _path(m.group()), value)
    if isinstance(value, dict):
        return {k: _portable_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_portable_text(v) for v in value]
    return value


def public_snapshot(doc):
    """Copy an existing comparison using an allowlist, without resolving any source."""
    rows = []
    for row in doc.get('rows', []):
        out = _pick(row, ROW_FIELDS)
        out['result_file'] = _path(row.get('result_path', ''))
        out['source_outputs'] = [dict(_pick(p, ['sha256', 'size']), name=_path(p.get('name', ''))) if isinstance(p, dict) else dict(name=_path(p)) for p in row.get('source_outputs', [])]
        out['protocol'] = _protocol(row.get('protocol'))
        cal = row.get('calibration_snapshot') or {}
        out['calibration_snapshot_sha256'] = hashlib.sha256(json.dumps(cal, sort_keys=True).encode()).hexdigest()
        out['calibration_snapshot'] = dict(
            entry=_pick(cal.get('entry'), ['id', 'source', 'verified', 'n_training', 'training_entries', 'geometry_label']),
            model=_pick(cal.get('model'), MODEL_FIELDS), training_entries=cal.get('training_entries'))
        model = cal.get('model') or {}
        out['calibration_snapshot']['model']['fingerprint'] = _pick(model.get('fingerprint'), FP_FIELDS)
        out['calibration_snapshot']['model']['stats'] = _pick(model.get('stats'), ['n', 'r2', 'rmse', 'mae', 'loo_rmse', 'loo_mae'])
        out['calibration_snapshot']['reference_evidence'] = [_pick(r, ['id', 'structure_sha256', 'output_sha256', 'training_entry']) for r in (cal.get('entry') or {}).get('reference_evidence', [])]
        out['evidence_sources'] = []
        for source in row.get('evidence_sources', []):
            item = _pick(source, ['kind', 'sha256', 'parser_version'])
            item['file'] = _path(source.get('file', source.get('path', '')))
            item['remote_source'] = _pick(source.get('remote_source'), ['sha256', 'size'])
            out['evidence_sources'].append(item)
        prov = row.get('provenance') or {}
        inp = prov.get('inputs') or {}
        out['conditions'] = _pick(inp, ['charge', 'multiplicity'])
        out['conditions'].update(_pick(inp.get('hints'), ['solvent', 'temperature_K', 'oxidation_state', 'radical_ligand', 'nitrosyl']))
        out['citations'] = [_pick(c, ['doi', 'title', 'authors', 'year']) if isinstance(c, dict) else str(c)
                            for c in prov.get('citations', [])]
        rows.append(out)
    summaries = []
    for summary in doc.get('summaries', []):
        item = _pick(summary, ['cohort', 'property', 'unit', 'functional', 'calibration', 'n', 'mae', 'bias', 'rmse',
                               'validation_n', 'validation_mae', 'membership_unknown_n', 'delta_status', 'interpretation'])
        item['protocol'] = _protocol(summary.get('protocol'))
        summaries.append(item)
    return _portable_text(J._jsonable(dict(schema_version='study-report/1', units=study.UNITS, rows=rows, summaries=summaries,
                            selection=[_pick(s, ['entry', 'version']) for s in doc.get('selection', [])],
                            issues=[_pick(s, ['entry', 'version', 'reason']) for s in doc.get('issues', [])],
                            notes=list(doc.get('notes', [])))))


def _cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return escape('' if value is None else str(value))


def _table(rows, fields):
    head = ''.join('<th>' + _cell(k) + '</th>' for k in fields)
    body = ''.join('<tr>' + ''.join('<td>' + _cell(row.get(k)) + '</td>' for k in fields) + '</tr>' for row in rows)
    return '<div class="table"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>'


def _methods(protocol):
    fp = protocol.get('output_fingerprint') or protocol.get('fingerprint') or {}
    geom = protocol.get('geometry') or {}
    method = f"{fp.get('functional') or 'unknown functional'}/{fp.get('basis') or 'unknown basis'}"
    fe = fp.get('fe_basis') or fp.get('basis') or 'unknown'
    geometry = (f"optimized at {geom.get('functional', 'unknown')}/{geom.get('basis', 'unknown')}"
                if geom.get('mode') == 'optimize' else f"as supplied ({geom.get('as_given_label', 'unspecified provenance')})")
    return (f"Saved single-point protocol: {method}; Fe basis {fe}; grid {fp.get('grid') or 'unknown'}; "
            f"RI treatment {fp.get('ri') or 'unknown'}. Geometry: {geometry}. "
            f"Solvent: {protocol.get('solvent') or fp.get('solvent') or 'gas phase'}. "
            f"Temperature: {protocol.get('temperature_K') if protocol.get('temperature_K') is not None else 'not specified'} K. "
            f"Program: {fp.get('program') or 'not recorded'}. Quadrupole moment: {protocol.get('q_barn')} barn.")


def _alerts(rows):
    """Graded alert sheet: every trust flag once per row, blocking before caution, in plain words a referee can check."""
    items = [(f['severity'] != trust.BLOCKING, r.get('entry'), r.get('version'), r.get('job'), f)
             for r in rows for f in (r.get('trust_flags') or [])]
    if not items:
        return '<h2>Alerts</h2><p>No trust flags were raised for the selected values.</p>'
    out = '<h2>Alerts</h2><p>Blocking: the value must not be read as a prediction. Caution: the value stands, with the stated limitation.</p>'
    table = [dict(severity=f['severity'], entry=e, version=v, job=j, value=f.get('property'), alert=f.get('text'))
             for _, e, v, j, f in sorted(items, key=lambda t: (t[0], str(t[1]), str(t[3])))]
    return out + _table(table, ['severity', 'entry', 'version', 'job', 'value', 'alert'])


def render(public, title='Mössbauer study report'):
    """Render an already sanitized snapshot; all text is escaped, including methods."""
    rows = public['rows']
    methods = list(dict.fromkeys(_methods(r['protocol']) for r in rows))
    sections = '<h2>Methods and conditions</h2><p>Protocols below describe the saved analyses. Review these methods and all caveats before publication.</p>'
    sections += ''.join('<p>' + escape(m) + '</p>' for m in methods)
    sections += '<h2>Results</h2><p>Shifts and quadrupole splittings: mm/s. Energies: Eh. Density: a.u. Temperature: K.</p>'
    sections += _table([dict(r, alerts=trust.label(r.get('trust_flags') or [])) for r in rows],
                       ['compound', 'entry', 'version', 'job', 'atom', 'functional', 'delta', 'delta_status', 'delta_exp',
                        'abs_deq', 'abs_deq_exp', 'energy_Eh', 'training_membership', 'flags', 'alerts'])
    sections += _alerts(rows)
    sections += '<h2>Agreement with experiment</h2><p>Separate protocols, calibrations and calibration statuses form separate cohorts; only calibrated and project-fit delta values are predictions. Unknown training membership does not establish held-out validation.</p>'
    sections += _table(public['summaries'], ['cohort', 'property', 'delta_status', 'unit', 'n', 'mae', 'bias', 'rmse', 'validation_n',
                                             'validation_mae', 'interpretation'])
    sections += '<h2>Warnings and omissions</h2>' + ''.join('<p>' + _cell(n) + '</p>' for n in public['notes'])
    sections += _table(public['issues'], ['entry', 'version', 'reason'])
    sections += '<h2>Source and calibration provenance</h2><p>Compact evidence records a remote hash; local raw bytes have not been verified. Source files are named without private directory paths.</p>'
    for row in rows:
        sections += '<details><summary>' + _cell(row['entry']) + ' v' + _cell(row['version']) + ' · ' + _cell(row.get('job')) + '</summary><pre>' + escape(json.dumps(row, indent=2, ensure_ascii=False)) + '</pre></details>'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>body{font:16px/1.6 system-ui,sans-serif;color:#172a36;background:#f7f8fa;max-width:1200px;margin:auto;padding:clamp(16px,4vw,48px)}h1,h2{line-height:1.2}h2{margin-top:2em}table{border-collapse:collapse;background:white;width:100%;font-size:14px}th,td{border-bottom:1px solid #d8dfe4;padding:10px;text-align:left;vertical-align:top}th{background:#e8eef2}.table{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f5;padding:16px}details{margin:12px 0}summary{cursor:pointer}p{max-width:85ch}</style><body><h1>' + escape(title) + '</h1><p>Offline snapshot of selected saved analyses. Scientific interpretation and publication require human review.</p>' + sections + '</body></html>'


SI_COLUMNS = [('compound', 'Compound'), ('state', 'State (q, M)'), ('functional', 'Functional'), ('atom', 'Fe'),
              ('delta', 'δ calc (mm/s)'), ('delta_pi', 'δ PI (mm/s)'), ('delta_exp', 'δ exp (mm/s)'),
              ('abs_deq', '|ΔEQ| calc (mm/s)'), ('abs_deq_exp', '|ΔEQ| exp (mm/s)'), ('eta', 'η'),
              ('delta_status', 'δ status'), ('alerts', 'Alerts')]


def _fmt(value, nd=2):
    return '' if value is None else f'{value:.{nd}f}' if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)


def si_rows(public):
    """One supporting-information row per saved value, from the shared snapshot only."""
    out = []
    for r in public['rows']:
        cond = r.get('conditions') or {}
        lo, hi = r.get('delta_lo'), r.get('delta_hi')
        out.append(dict(compound=r.get('compound'), state=f"{cond.get('charge', '?')}, {cond.get('multiplicity', '?')}",
                        functional=r.get('functional'), atom=r.get('atom'), delta=_fmt(r.get('delta')),
                        delta_pi=f'{_fmt(lo)} to {_fmt(hi)}' if lo is not None and hi is not None else '',
                        delta_exp=_fmt(r.get('delta_exp')), abs_deq=_fmt(r.get('abs_deq')), abs_deq_exp=_fmt(r.get('abs_deq_exp')),
                        eta=_fmt(r.get('eta')), delta_status=r.get('delta_status') or '',
                        alerts=trust.label(r.get('trust_flags') or [], plain=True)))
    return out


def si_csv(public):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([h for _, h in SI_COLUMNS])
    for r in si_rows(public):
        w.writerow([r[k] for k, _ in SI_COLUMNS])
    return buf.getvalue()


_TEX = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}',
        '~': r'\textasciitilde{}', '^': r'\textasciicircum{}', 'δ': r'$\delta$', 'Δ': r'$\Delta$', 'η': r'$\eta$',
        'ρ': r'$\rho$', '|': r'$|$', '⛔': '', '⚠': '', '−': '-', '–': '--', '—': '---', '·': r'$\cdot$',
        '°': r'$^\circ$', '±': r'$\pm$', 'α': r'$\alpha$', 'β': r'$\beta$', 'μ': r'$\mu$', 'σ': r'$\sigma$'}
_TEX.update({chr(0x2080 + i): f'$_{{{i}}}$' for i in range(10)})
_TEX.update({c: f'$^{{{d}}}$' for c, d in zip('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')})
_TEX.update({'⁺': '$^{+}$', '⁻': '$^{-}$', '₊': '$_{+}$', '₋': '$_{-}$'})


def _tex(text):
    """Escape for LaTeX; characters pdflatex cannot set without extra packages become '?' rather than a compile error."""
    return ''.join(_TEX.get(ch, ch if ord(ch) < 128 else '?') for ch in str(text))


def si_latex(public):
    """A booktabs table; every cell is escaped and non-ASCII text mapped (or replaced by '?'), so it compiles with pdflatex."""
    cols = 'l' * len(SI_COLUMNS)
    lines = [r'% generated by mossbauer-dft from study.json; requires \usepackage{booktabs}', r'\begin{tabular}{' + cols + '}',
             r'\toprule', ' & '.join(_tex(h) for _, h in SI_COLUMNS) + r' \\', r'\midrule']
    lines += [' & '.join(_tex(r[k]) for k, _ in SI_COLUMNS) + r' \\' for r in si_rows(public)]
    lines += [r'\bottomrule', r'\end{tabular}', '']
    return '\n'.join(lines)


def methods_text(public):
    """Standalone methods paragraphs: the saved protocols, the calibrations by identity, and the caveat to review."""
    rows = public['rows']
    paras = list(dict.fromkeys(_methods(r['protocol']) for r in rows))
    cals = {}
    reported_delta = [r for r in rows if r.get('delta') is not None]
    for r in reported_delta:
        snap = r.get('calibration_snapshot') or {}
        model = snap.get('model') or {}
        if model.get('alpha') is not None:
            c = cals.setdefault(r.get('calibration'), dict(model=model, entry=snap.get('entry') or {}, statuses=set()))
            c['statuses'].add(r.get('delta_status') or 'unknown')
    for cid, c in cals.items():
        model, entry = c['model'], c['entry']
        paras.append(f"Isomer shifts were obtained from ρ(0) with δ = α(ρ(0) − C) + β, α = {model['alpha']:.4f} mm/s·a0³, "
                     f"C = {model['C']:.2f} a0⁻³, β = {model['beta']:.4f} mm/s (calibration {cid}; "
                     f"{'verified literature constants' if entry.get('verified') else 'not independently verified'}; "
                     f"status of the reported values: {', '.join(sorted(c['statuses']))}).")
    paras.append('Values flagged in the alerts table carry the stated limitations; blocking alerts mean the value is not a '
                 'calibrated prediction. Review before publication.')
    return '\n\n'.join(paras) + '\n'


def bibtex(public):
    """BibTeX for every DOI cited by the saved protocols (method-rule citations); keys are derived from the DOI."""
    seen, out = set(), []
    for r in public['rows']:
        for c in r.get('citations') or []:
            doi = (c.get('doi') if isinstance(c, dict) else str(c)).strip()
            if not doi or doi in seen or not re.fullmatch(r'10\.\d{4,9}/\S+', doi):
                continue
            seen.add(doi)
            key = re.sub(r'[^A-Za-z0-9]+', '_', doi)
            fields = [f'  doi = {{{doi}}}']
            if isinstance(c, dict):
                for k in ('title', 'year'):
                    if c.get(k):
                        fields.append(f"  {k} = {{{_tex(c[k])}}}")
                if c.get('authors'):
                    a = c['authors']
                    fields.append(f"  author = {{{_tex(' and '.join(a) if isinstance(a, list) else a)}}}")
            out.append(f'@article{{{key},\n' + ',\n'.join(fields) + '\n}\n')
    return '\n'.join(out) or '% no DOI citations in the selected analyses\n'


MANIFEST = 'MANIFEST.json'


def bundle(doc, title='Mössbauer study report'):
    """Return one ZIP whose HTML, JSON, CSV, SI and methods files share exactly one snapshot, with a hash manifest."""
    public = public_snapshot(doc)
    html = render(public, title)
    members = {
        'index.html': html,
        'study.json': json.dumps(public, indent=2, ensure_ascii=False, allow_nan=False),
        'results.csv': study.comparison_csv(public),
        'si_table.csv': si_csv(public),
        'si_table.tex': si_latex(public),
        'methods.txt': methods_text(public),
        'references.bib': bibtex(public),
        'README.txt': ('Open index.html in any browser. No network connection is required.\n'
                       'CSV and JSON contain the same selected saved analyses. Raw outputs and private paths are omitted.\n'
                       'si_table.csv / si_table.tex, methods.txt and references.bib are drafts for supporting information.\n'
                       'Check this archive with: mossbauer report verify <this zip>\n'
                       'Review warnings, units, methods, training membership and provenance before publication.\n'),
    }
    manifest = dict(schema_version='study-report-manifest/1',
                    members={k: hashlib.sha256(v.encode()).hexdigest() for k, v in members.items()})
    buf = io.BytesIO()
    with ZipFile(buf, 'w', ZIP_DEFLATED) as z:
        for name, text in members.items():
            z.writestr(name, text)
        z.writestr(MANIFEST, json.dumps(manifest, indent=2))
    return buf.getvalue(), html


def verify(source) -> dict:
    """Re-check a report ZIP without the project: member hashes, CSV/JSON agreement, every δ re-derived from its recorded ρ(0)
    and calibration constants, and the status rules. Raw outputs are not in the archive, so their contents cannot be
    re-checked; source hashes only identify them."""
    from zipfile import BadZipFile
    checks = []

    def check(name, ok, detail=''):
        checks.append(dict(check=name, ok=bool(ok), detail=detail))
    try:
        z = ZipFile(source)
    except (BadZipFile, OSError) as ex:
        return dict(ok=False, checks=[dict(check='archive readable', ok=False, detail=str(ex))])
    try:
        with z:
            return _verify(z, checks, check)
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError, BadZipFile, csv.Error) as ex:
        check('archive is a well-formed study report', False, f'{type(ex).__name__}: {ex}')
        return dict(ok=False, checks=checks)


def _finite_number(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('non-finite report number')
    return value


def _csv_matches_rows(text, rows):
    actual = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    expected = csv.DictReader(io.StringIO(study.comparison_csv(dict(rows=rows)), newline=""))
    columns = actual.fieldnames or []
    if len(columns) != len(set(columns)) or set(columns) != set(expected.fieldnames or []):
        return False
    actual_rows = list(actual)
    if any(None in row or None in row.values() for row in actual_rows):
        return False
    return (Counter(tuple(sorted(row.items())) for row in actual_rows) ==
            Counter(tuple(sorted(row.items())) for row in expected))


def _verify(z, checks, check):
    names = set(z.namelist())
    if MANIFEST not in names:
        check('manifest present', False, 'no MANIFEST.json (made before verifiable reports?)')
        return dict(ok=False, checks=checks)
    manifest = json.loads(z.read(MANIFEST), parse_float=_finite_number, parse_constant=_finite_number)
    listed = manifest.get('members') or {}
    bad = [n for n, h in listed.items() if n not in names or hashlib.sha256(z.read(n)).hexdigest() != h]
    extra = sorted(names - set(listed) - {MANIFEST})
    check('member hashes match the manifest', not bad, ', '.join(bad))
    check('no unlisted members', not extra, ', '.join(extra))
    public = json.loads(z.read('study.json'), parse_float=_finite_number, parse_constant=_finite_number)
    rows = public.get('rows') or []
    check('CSV and JSON have the same rows', _csv_matches_rows(z.read('results.csv').decode(), rows),
          'columns and cell values must match, including repeated rows; order may differ')
    mismatch, recomputed = [], 0
    for r in rows:
        model = (r.get('calibration_snapshot') or {}).get('model') or {}
        for value in (r.get('delta'), r.get('rho0'), model.get('alpha'), model.get('C'), model.get('beta')):
            if value is not None and type(value) not in (int, float):
                raise ValueError('δ, ρ(0) and calibration constants must be numbers')
        if r.get('delta') is None or r.get('rho0') is None or model.get('alpha') is None:
            continue
        expected = model['alpha'] * (r['rho0'] - model['C']) + model['beta']
        recomputed += 1
        if not math.isfinite(expected) or abs(expected - r['delta']) > 1e-6 * max(1.0, abs(expected)):
            mismatch.append(f"{r.get('entry')} v{r.get('version')} {r.get('job')}: {r['delta']} vs {expected:.6f}")
    check(f'δ re-derived from ρ(0) and calibration constants ({recomputed} values)', not mismatch, '; '.join(mismatch))
    wrong = [f"{r.get('entry')} {r.get('job')}" for r in rows
             if r.get('delta_status') in study.CALIBRATED_STATUSES and trust.blocked(r.get('trust_flags') or [], 'delta')]
    check('no calibrated status despite a blocking alert', not wrong, ', '.join(wrong))
    leaked = [r.get('entry') for r in rows if r.get('delta_metrics_eligible') and r.get('training_membership') == 'training']
    check('no training-set row counted as validation', not leaked, ', '.join(map(str, leaked)))
    return dict(ok=all(c['ok'] for c in checks), checks=checks,
                note='Raw outputs are not in the archive: source hashes identify them but their contents are not re-checked.')
