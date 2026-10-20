"""Read-only study comparisons, derived from exact saved analyses (no GUI dependencies)."""
from __future__ import annotations

import csv
import copy
import hashlib
import io
import json
import math
from collections import defaultdict
from pathlib import Path

from . import plan as P, project as J, trust

UNITS = dict(delta='mm/s', delta_exp='mm/s', delta_lo='mm/s', delta_hi='mm/s', abs_deq='mm/s',
             abs_deq_exp='mm/s', deq_signed='mm/s', energy_Eh='Eh', rho0='a.u.', temperature_K='K')


def snapshot(plan: dict, calibration=None, calibration_entry: dict | None = None) -> dict:
    """Capture the inputs and calibration actually used; never resolve them again when comparing."""
    inp = plan['inputs']
    protocols = {}
    for job in plan['jobs']:
        if job['kind'] in J.PROPERTY_KINDS:
            protocols[job['stem']] = dict(fingerprint=P.method_spec_from_plan(plan, 'delta', job['functional']).fingerprint().to_dict(),
                                         geometry=plan['geometry'], solvent=inp['hints'].get('solvent'),
                                         temperature_K=inp['hints'].get('temperature_K'), q_barn=0.16)
    return dict(inputs=inp, system=plan['system'], geometry=plan['geometry'], methods=plan['methods'],
                protocols=protocols, citations=plan['rule_evaluation'].get('citations', []),
                rule=plan['rule_evaluation'],
                calibration=dict(entry=calibration_entry, model=calibration.to_dict() if calibration else None,
                                 training_entries=(calibration_entry or {}).get('training_entries')))


CALIBRATED_STATUSES = ("calibrated", "project-fit")


def calibration_status(plan: dict, calibration, calibration_entry: dict | None, from_file: bool = False) -> dict:
    """Whether the delta values of an analysis are calibrated predictions: ``calibrated`` (verified literature constants,
    protocol-matched), ``project-fit`` (fitted to this project's own protocol-matched references), ``exploratory``
    (unverified or protocol-mismatched constants, used only by override) or ``uncalibrated`` (rho(0) only)."""
    from . import references as R
    if calibration is None:
        return dict(status="uncalibrated", reasons=["no calibration available: only rho(0) is reported"])
    if not calibration_entry:
        return dict(status="exploratory", reasons=["calibration loaded without a calibration entry: provenance and protocol unknown"])
    import dataclasses
    fp = dataclasses.replace(P.method_spec_from_plan(plan, "delta").fingerprint(),
                             program=P.orca_program((plan.get("inputs") or {}).get("overrides")))
    m = R.match_calibration(calibration_entry, fp, P.geometry_label(plan))
    mismatch = list(m["hard"]) + list(m["soft"])
    if from_file and calibration_entry.get("source") != "project_fit":
        return dict(status="exploratory", reasons=["calibration file loaded by path: constants are not from the verified library"])
    if calibration_entry.get("source") == "project_fit":
        if m["protocol_matched"] and not any(x.startswith("solvent:") for x in m["soft"]):
            return dict(status="project-fit", reasons=[f"project fit {calibration_entry.get('id')} on this protocol (unverified by construction)"])
        return dict(status="exploratory", reasons=["project fit does not match this protocol: " + "; ".join(mismatch)])
    if m["verified"] and m["protocol_matched"]:
        return dict(status="calibrated", reasons=[f"verified literature calibration {calibration_entry.get('id')}, protocol-matched"])
    reasons = ([] if m["verified"] else ["calibration constants are unverified"]) + \
              ([] if m["protocol_matched"] else ["not protocol-matched: " + "; ".join(mismatch)])
    return dict(status="exploratory", reasons=reasons)


def available(root) -> list[dict]:
    """Every version with a saved analysis, including unreadable ones so omissions are visible."""
    return [dict(entry=e.entry_id, name=e.display_name, version=v.version, newest=e.newest.version,
                 label=f'{e.display_name or e.entry_id} · {e.state_label} · v{v.version}'
                       + (' (older version)' if v.version != e.newest.version else ''))
            for e in J.scan_project(root) for v in e.versions if v.has_results]


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _collect(root, entry, version, newest) -> list[dict]:
    vd = J.version_dir(root, entry, version)
    saved = J.load_results(root, entry, version)
    if not saved or not isinstance(saved.get('rows'), list):
        raise J.ProjectError('no saved analysis or invalid rows')
    vs = J.scan_version(root, entry, version)
    prov = saved.get('provenance') or {}
    flags = list(vs.flags)
    if saved.get('plan_hash') != vs.plan_hash:
        flags.append('plan changed since analysis')
    if not prov:
        flags.append('legacy analysis: protocol provenance unknown')
    if vs.status is None:
        flags.append('unreadable plan')
    cal = prov.get('calibration') or {}
    training = cal.get('training_entries')
    if training is not None and (not isinstance(training, list) or any(not isinstance(e, str) for e in training)):
        raise J.ProjectError('invalid calibration training membership')
    membership = 'unknown' if training is None else 'training' if entry in training else 'held-out'
    if membership == 'training':
        flags.append('calibration training entry')
    if (prov.get('system') or {}).get('n_fe', 1) != 1:
        flags.append('ambiguous Fe-site experiment')
    result_path = vd / 'results' / J.RESULTS_FILE
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    name = J._read_yaml(vd.parent / J.ENTRY_FILE).get('name') or entry
    rows = []
    for original in saved['rows']:
        if not isinstance(original, dict):
            raise J.ProjectError('analysis row is not a mapping')
        row_flags = list(dict.fromkeys(flags))
        protocol = copy.deepcopy((prov.get('protocols') or {}).get(original.get('job')))
        if not protocol:
            row_flags.append('protocol unknown')
        elif original.get('output_fingerprint'):
            protocol['output_fingerprint'] = original['output_fingerprint']
        if original.get('ok') is not True:
            row_flags.append('calculation unreliable')
        tflags = trust.for_row(original)
        delta_blocked, deq_blocked = trust.blocked(tflags, 'delta'), trust.blocked(tflags, 'deq')
        row = dict(original, trust_flags=tflags)
        status = (saved.get('delta_status') or {}).get('status', 'unknown')
        reasons = list((saved.get('delta_status') or {}).get('reasons') or ['analysis saved before calibration status was recorded'])
        if status in CALIBRATED_STATUSES and delta_blocked:  # this output itself did not get a usable calibrated prediction
            blocking = '; '.join(f['text'] for f in tflags if f['severity'] == trust.BLOCKING and f['property'] in ('delta', 'all'))
            status, reasons = 'exploratory', reasons + [f'this output: {blocking}']
        row.update(entry=entry, compound=name, version=version, newest_version=newest, older_version=version != newest,
                   delta_status=status, delta_status_reasons=reasons,
                   plan_hash=saved.get('plan_hash'), result_path=J.rel_path(root, result_path), result_sha256=digest,
                   analyzed_at=saved.get('analyzed_at'), calibration=saved.get('calibration'),
                   calibration_snapshot=cal, protocol=protocol, source_outputs=saved.get('outputs', []),
                   training_membership=membership, flags=row_flags, warnings=list(saved.get('notes') or []),
                   provenance=prov, metrics_eligible=not row_flags and membership != 'training',
                   evidence_sources=saved.get('evidence_sources', []),
                   delta_metrics_eligible=not row_flags and membership != 'training' and not delta_blocked,
                   deq_metrics_eligible=not row_flags and membership != 'training' and not deq_blocked,
                   delta_unit='mm/s', abs_deq_unit='mm/s', energy_unit='Eh')
        rows.append(row)
    return rows


def _interpretation(prop: str, status) -> str:
    if prop != 'delta' or status in CALIBRATED_STATUSES:
        return 'descriptive agreement; validation requires known held-out membership'
    if status == 'unknown':
        return 'not calibrated predictions: calibration status not recorded (analysis saved before it existed); re-analyze to classify'
    return 'not calibrated predictions: exploratory or uncalibrated values'


def _summaries(rows) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        if not row['metrics_eligible']:
            continue
        for prop, experiment in [('delta', 'delta_exp'), ('abs_deq', 'abs_deq_exp')]:
            if prop == 'delta' and not row['delta_metrics_eligible']:
                continue
            if prop == 'abs_deq' and not row.get('deq_metrics_eligible', True):
                continue
            if _number(row.get(prop)) and _number(row.get(experiment)):
                # The entire protocol, constants and uncertainty model identify a cohort.
                cohort = dict(protocol=row['protocol'], calibration=row['calibration_snapshot'],
                              calibration_id=row['calibration'] if prop == 'delta' else None, property=prop)
                if prop == 'delta':  # abs_deq cohorts (and their ids) are unchanged: no calibration is involved
                    cohort['delta_status'] = row['delta_status']
                key = json.dumps(cohort, sort_keys=True)
                groups[key].append(row)
    out = []
    for key, cohort in sorted(groups.items()):
        meta = json.loads(key)
        prop = meta['property']
        experiment = 'delta_exp' if prop == 'delta' else 'abs_deq_exp'
        errors = [r[prop] - r[experiment] for r in cohort]
        held = [r[prop] - r[experiment] for r in cohort if r['training_membership'] == 'held-out']
        out.append(dict(cohort=hashlib.sha256(key.encode()).hexdigest()[:12], property=prop, unit='mm/s',
                        functional=cohort[0].get('functional'), protocol=meta['protocol'],
                        calibration=cohort[0]['calibration'] if prop == 'delta' else None,
                        n=len(errors), mae=sum(abs(v) for v in errors) / len(errors), bias=sum(errors) / len(errors),
                        rmse=math.sqrt(sum(v*v for v in errors) / len(errors)), validation_n=len(held),
                        validation_mae=sum(abs(v) for v in held) / len(held) if held else None,
                        membership_unknown_n=sum(r['training_membership'] == 'unknown' for r in cohort),
                        delta_status=meta.get('delta_status'), interpretation=_interpretation(prop, meta.get('delta_status'))))
    return out


def compare(root, selections: list[tuple[str, int]] | None = None) -> dict:
    """Select exact entry/version pairs, or the newest saved analysis per entry. Never re-analyze."""
    J.open_project(root)
    choices = available(root)
    newest = {e.entry_id: e.newest.version for e in J.list_entries(root) if e.newest}
    if selections is None:
        defaults = {}
        for choice in choices:
            defaults[choice['entry']] = choice['version']
        selections = list(defaults.items())
    rows, issues = [], []
    for entry, version in dict.fromkeys((str(e), int(v)) for e, v in selections):
        try:
            rows += _collect(root, entry, version, newest[entry])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as ex:
            issues.append(dict(entry=entry, version=version, reason=str(ex)))
    return dict(schema_version='1', units=UNITS, rows=rows, summaries=_summaries(rows), issues=issues,
                selection=[dict(entry=e, version=v) for e, v in selections],
                notes=['Error summaries are descriptive agreement, not a universal method ranking.',
                       'Training membership unknown is not evidence of held-out validation.',
                       'Stale, partial, modified and ambiguous multi-Fe analyses remain visible but are excluded from summaries.',
                       'Delta summaries never pool calibration statuses; exploratory, uncalibrated and unknown cohorts are not calibrated predictions.'])


def comparison_csv(doc: dict) -> str:
    """A portable table including provenance and units; nested values are JSON cells."""
    if not doc['rows']:
        return ''
    keys = list(dict.fromkeys(k for row in doc['rows'] for k in row))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys)
    writer.writeheader()
    for row in doc['rows']:
        writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    return buf.getvalue()
