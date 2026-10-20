"""Validated imports of portable cluster evidence; full outputs retain precedence."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

from . import harvest, plan as P, project as J


def manifest(plan, entry, version):
    from .structure import Structure
    return dict(schema_version='1', entry=entry, version=version, plan_hash=P.compute_hash(plan),
                n_atoms=plan['system']['n_atoms'], fe_indices=Structure.from_xyz_text(plan['_structure_xyz']).fe_indices, jobs=[dict(j, fingerprint=J._expected_fingerprint(plan, j).to_dict()) for j in plan['jobs']])


def portable_harvester():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('__main__.py', 'from mossbauer_dft.harvest import main\nmain()\n')
        for name in ('__init__.py', 'harvest.py', 'orca_parse.py', 'constants.py'):
            z.write(Path(__file__).with_name(name), 'mossbauer_dft/'+name)
    return buf.getvalue()


def load_record(path, plan, entry=None, version=None):
    doc = harvest.read_json(path)
    expected = manifest(plan, entry if entry is not None else doc['entry'], version if version is not None else doc['version'])
    parsed = harvest.validate(doc, expected)
    if len(parsed) != 1: raise ValueError('stored compact record must contain one job')
    return next(iter(parsed.values()))


def import_bundle(root, entry, version, source, replace=False):
    vd = J.version_dir(root, entry, version)
    version = int(vd.name[1:])
    plan = J.load_version(root, entry, version)
    if not P.plan_integrity(plan): raise J.ProjectError('plan integrity mismatch; cannot import evidence')
    doc = harvest.read_json(source)
    harvest.validate(doc, manifest(plan, entry, version))  # all records are checked before any write
    changes, unchanged = [], []
    for stem, record in doc['records'].items():
        raw = vd / 'outputs' / f'{stem}.out'
        if raw.is_file():
            if hashlib.sha256(raw.read_bytes()).hexdigest() != record['source']['sha256']:
                raise J.ProjectError('compact evidence conflicts with an existing raw output')
            unchanged.append(stem); continue
        dest = vd / 'outputs' / f'{stem}.compact.json'
        single = dict(doc, records={stem: record}, missing=[], rejected=[], census={})
        body = json.dumps(single, sort_keys=True, indent=2, allow_nan=False)
        old = dest.read_text() if dest.exists() else None
        if old == body:
            unchanged.append(stem); continue
        if old is not None and not replace:
            raise J.ProjectError('different compact evidence already exists; explicitly archive and replace it')
        changes.append((dest, old, body))
    applied = []
    try:
        for dest, old, body in changes:
            if old is not None:
                archive = vd / 'evidence-history' / hashlib.sha256(old.encode()).hexdigest() / dest.name
                archive.parent.mkdir(parents=True, exist_ok=True)
                J._write_atomic(archive, old)
            J._write_atomic(dest, body)
            applied.append((dest, old))
    except Exception:
        for dest, old in reversed(applied):
            if old is None: dest.unlink(missing_ok=True)
            else: J._write_atomic(dest, old)
        raise
    for stem, state in (doc.get('census') or {}).items():  # job-state records are status, not evidence: newest wins
        J._write_atomic(vd / 'outputs' / f'{stem}.state.json', json.dumps(state, sort_keys=True, indent=2))
    return dict(imported=[d.name.removesuffix('.compact.json') for d, _, _ in changes],
                job_states=sorted((doc.get('census') or {})), unchanged=unchanged,
                missing=doc.get('missing', []), rejected=doc.get('rejected', []),
                warning='Compact evidence is derived data; original raw-output bytes are identified by hash but are not verified locally.')
