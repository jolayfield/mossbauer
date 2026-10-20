"""Project-local reference preparation, fit diagnostics and immutable calibration snapshots."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

from . import plan as P, project as J, references as R
from .elements import atomic_numbers
from .orca_input import build_input
from .orca_parse import parse_orca_output
from .site import SiteProfile
from .structure import read_structure


def path(root, name):
    J.open_project(root)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
        raise J.ProjectError('workspace/reference id must contain only letters, digits, underscores or hyphens')
    return Path(root) / 'calibrations' / name


def read(root, name):
    folder = path(root, name)
    doc = json.loads((folder / 'workspace.json').read_text())
    if not isinstance(doc, dict) or doc.get('schema_version') != '1' or not isinstance(doc.get('references'), list):
        raise J.ProjectError('invalid calibration workspace')
    seen = set()
    for ref in doc['references']:
        if not isinstance(ref, dict):
            raise J.ProjectError('invalid reference record')
        rid = ref.get('id', '')
        path(root, rid)
        if rid in seen or ref.get('structure') != f'references/{rid}.xyz':
            raise J.ProjectError('invalid or duplicate reference structure path')
        seen.add(rid)
        source = (folder / ref['structure']).resolve()
        if not source.is_relative_to(folder.resolve()):
            raise J.ProjectError('reference structure escapes its workspace')
    return doc


def create(root, name, entry, version=None):
    vd = J.version_dir(root, entry, version)
    folder = path(root, name)
    folder.mkdir(parents=True, exist_ok=False)
    doc = dict(schema_version='1', name=name, entry=entry, version=int(vd.name[1:]), references=[])
    J._write_atomic(folder / 'workspace.json', json.dumps(doc, indent=2))
    return doc


def add_reference(root, name, ref_id, structure, charge, multiplicity, delta_exp, deq_exp=None, training_entry=None):
    folder = path(root, name)
    path(root, ref_id)  # same safe-id grammar
    doc = read(root, name)
    if (folder / 'inputs').exists():
        raise J.ProjectError('reference inputs already prepared; create a new workspace to change references')
    if any(r['id'] == ref_id for r in doc['references']):
        raise J.ProjectError('reference id already exists')
    if not math.isfinite(float(delta_exp)) or (deq_exp is not None and not math.isfinite(float(deq_exp))):
        raise J.ProjectError('experimental values must be finite')
    s = read_structure(structure)
    if len(s.fe_indices) != 1 or multiplicity < 1 or (sum(atomic_numbers(s.symbols)) - charge - multiplicity + 1) % 2:
        raise J.ProjectError('reference requires one Fe and a charge/multiplicity with valid electron parity')
    if training_entry is not None:
        J.load_version(root, training_entry)
    (folder / 'references').mkdir(exist_ok=True)
    xyz = folder / 'references' / f'{ref_id}.xyz'
    xyz.write_text(s.to_xyz_text())
    ref = dict(id=ref_id, name=ref_id, structure=f'references/{ref_id}.xyz', charge=int(charge),
               multiplicity=int(multiplicity), delta_exp=float(delta_exp), deq_abs_exp=deq_exp,
               training_entry=training_entry, structure_sha256=hashlib.sha256(xyz.read_bytes()).hexdigest())
    doc['references'].append(ref)
    J._write_atomic(folder / 'workspace.json', json.dumps(doc, indent=2))
    return ref


def import_bundled(root, name, refset=None, ids=None):
    ready, skipped = R.resolve_refset(refset, ids=ids)
    for c in ready:
        add_reference(root, name, c['id'], c['xyz_path'], int(c['charge']), int(c['multiplicity']),
                      c['delta_exp_mm_s'], c.get('deq_abs_exp_mm_s'))
    return dict(imported=[c['id'] for c in ready], skipped=skipped)


def plan_references(root, name):
    folder = path(root, name)
    return [dict(name=r['id'], xyz=J.rel_path(root, folder / r['structure']), charge=r['charge'],
                 multiplicity=r['multiplicity'], delta_exp=r['delta_exp']) for r in read(root, name)['references']]


def prepare(root, name, entry=None, version=None, site=None):
    folder = path(root, name)
    doc = read(root, name)
    entry = entry or doc['entry']
    vd = J.version_dir(root, entry, version)
    plan = J.load_version(root, entry, int(vd.name[1:]))
    P.require_approved(plan)
    if not doc['references']:
        raise J.ProjectError('add reference compounds before preparation')
    if (folder / 'inputs').exists():
        raise J.ProjectError('inputs already prepared; existing calculations are never overwritten')
    fp, opt = P.method_spec_from_plan(plan, 'delta'), P.geometry_spec_from_plan(plan)
    manifest = dict(refset=name, plan_hash=plan['plan_hash'], geometry_label=P.geometry_label(plan),
                    fingerprint=fp.fingerprint().to_dict(), compounds=[], target_entry=entry, target_version=int(vd.name[1:]))
    sp = SiteProfile.load(site)
    tmp = Path(tempfile.mkdtemp(prefix='.prepare-', dir=folder))
    commands = ['#!/bin/bash', 'set -e']
    try:
        for r in doc['references']:
            source = folder / r['structure']
            if hashlib.sha256(source.read_bytes()).hexdigest() != r['structure_sha256']:
                raise J.ProjectError('reference structure changed since entry; create a new workspace')
            cd = tmp / r['id']; cd.mkdir()
            shutil.copy2(source, cd / f"{r['id']}.xyz")
            (cd / 'orca_job.slurm').write_text(sp.slurm_script(), encoding='utf-8', newline='\n')
            n = plan['resources']['nprocs']
            opt_stem, stem = f"{r['id']}_opt", f"{r['id']}_sp"
            geometry = f"{r['id']}.xyz" if plan['geometry']['mode'] == 'as_given' else f'{opt_stem}.xyz'
            commands.append(f"cd {r['id']}")
            if plan['geometry']['mode'] != 'as_given':
                (cd / f'{opt_stem}.inp').write_text(build_input('opt', opt.with_(nprocs=n), charge=r['charge'],
                                                              multiplicity=r['multiplicity'], xyz_file=f"{r['id']}.xyz"))
                commands.append('J1=$(' + sp.sbatch_command(opt_stem, nprocs=n).replace('sbatch', 'sbatch --parsable', 1) + ')')
            (cd / f'{stem}.inp').write_text(build_input('mossbauer_sp', fp.with_(nprocs=n), charge=r['charge'],
                                                     multiplicity=r['multiplicity'], xyz_file=geometry))
            cmd = sp.sbatch_command(stem, nprocs=n)
            if plan['geometry']['mode'] != 'as_given':
                cmd = cmd.replace('sbatch', 'sbatch --dependency=afterok:$J1', 1)
            commands.extend([cmd, 'cd ..'])
            manifest['compounds'].append(dict(r, dir=r['id'], sp_stem=stem, opt_stem=opt_stem))
        (tmp / 'submit_refset.sh').write_text('\n'.join(commands) + '\n', encoding='utf-8', newline='\n')
        (tmp / 'refset_manifest.yaml').write_text(yaml.safe_dump(manifest, sort_keys=False))
        os.rename(tmp, folder / 'inputs')
    finally:
        if tmp.exists(): shutil.rmtree(tmp)
    (folder / 'outputs').mkdir(exist_ok=True)
    return manifest


def fit(root, name, exclusions=None, n_boot=5000):
    folder = path(root, name)
    exclusions = exclusions or {}
    doc = read(root, name)
    ids = {r['id'] for r in doc['references']}
    if not isinstance(exclusions, dict) or set(exclusions) - ids or any(not isinstance(v, str) or not v.strip() for v in exclusions.values()):
        raise J.ProjectError('each excluded reference must exist and have a written reason')
    manifest = yaml.safe_load((folder / 'inputs/refset_manifest.yaml').read_text())
    prepared = manifest.get('compounds', [])
    if {r.get('id') for r in prepared} != ids or len(prepared) != len(ids):
        raise J.ProjectError('prepared references differ from the workspace')
    for r in prepared:
        if r.get('dir') != r['id'] or r.get('sp_stem') != f"{r['id']}_sp" or r.get('opt_stem') != f"{r['id']}_opt":
            raise J.ProjectError('invalid prepared reference path')
    from .orca_parse import Fingerprint
    expected = Fingerprint.from_dict(manifest['fingerprint'])
    for r in manifest['compounds']:
        output = folder / 'outputs' / f"{r['sp_stem']}.out"
        if r['id'] in exclusions or not output.is_file(): continue
        parsed = parse_orca_output(output)
        hard, soft = expected.compare(parsed.fingerprint)
        if hard or [x for x in soft if not x.startswith('program:')]:
            raise J.ProjectError(f"reference {r['id']} protocol differs from prepared inputs: {'; '.join(hard + soft)}")
    entry = R.refset_fit(folder / 'inputs', outputs_dir=folder / 'outputs', install=False,
                        exclude=list(exclusions), n_boot=n_boot)
    used = set(entry['calibration']['names'])
    entry['exclusion_reasons'] = exclusions
    # Unknown membership stays unknown unless every used reference was explicitly linked.
    linked = [r.get('training_entry') for r in doc['references'] if r['id'] in used]
    entry['training_entries'] = sorted(set(linked)) if linked and all(linked) else None
    entry['reference_evidence'] = [dict(r, output_sha256=hashlib.sha256(
        (folder / 'outputs' / f"{r['id']}_sp.out").read_bytes()).hexdigest()) for r in doc['references'] if r['id'] in used]
    body = yaml.safe_dump(entry, sort_keys=False)
    digest = hashlib.sha256(body.encode()).hexdigest()
    (folder / 'fits').mkdir(exist_ok=True)
    target = folder / 'fits' / f'{digest[:16]}.yaml'
    J._write_atomic(target, body)
    R.refset_fit_csv(folder / 'inputs', entry)
    return dict(path=J.rel_path(root, target), sha256=digest, calibration=entry)


def workspaces(root):
    base = Path(root) / 'calibrations'
    return [p.name for p in sorted(base.iterdir()) if (p / 'workspace.json').is_file()] if base.is_dir() else []


def fits(root):
    return [J.rel_path(root, f) for name in workspaces(root) for f in sorted((path(root, name) / 'fits').glob('*.yaml'))]


def status(root, name):
    folder = path(root, name)
    doc = read(root, name)
    rows = []
    for r in doc['references']:
        output = folder / 'outputs' / f"{r['id']}_sp.out"
        state = 'missing'
        if output.is_file():
            state = 'complete' if parse_orca_output(output).ok else 'incomplete / SCF failed'
        rows.append(dict(r, output=J.rel_path(root, output), state=state))
    return dict(workspace=name, prepared=(folder / 'inputs').is_dir(), references=rows,
                fits=[f for f in fits(root) if f.startswith(f'calibrations/{name}/')])
