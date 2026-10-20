"""Explicit electronic-state relationships and cautious cross-entry energy comparisons."""
from __future__ import annotations

import copy
import json
import math
import shutil
from pathlib import Path

from . import plan as P, project as J, study

HARTREE_KJMOL = 2625.4996394799


def read(root):
    J.open_project(root)
    p = Path(root) / 'families.json'
    return json.loads(p.read_text()) if p.is_file() else dict(schema_version='1', families={})


def _save(root, doc):
    J._write_atomic(Path(root) / 'families.json', json.dumps(doc, indent=2))


def create(root, name, entries):
    if not str(name).strip(): raise J.ProjectError('family needs a name')
    doc = read(root)
    if name in doc['families']: raise J.ProjectError('family already exists')
    entries = list(dict.fromkeys(entries))
    if not entries: raise J.ProjectError('select at least one family member')
    for eid in entries: J.load_version(root, eid)
    doc['families'][name] = dict(name=name, entries=entries)
    _save(root, doc)
    return doc['families'][name]


def link(root, name, entries):
    """Create-or-extend: put ``entries`` in the one family that already holds any of them, else in a new family ``name``
    (suffixed ``-2``, ``-3`` when an unrelated family has that name).  When they sit in two or more families nothing
    changes and the families are reported."""
    entries = list(dict.fromkeys(entries))
    for eid in entries: J.load_version(root, eid)
    doc = read(root)
    holding = [n for n, f in doc['families'].items() if set(f['entries']) & set(entries)]
    if len(holding) > 1:
        return dict(status='ambiguous', family=None, families=holding, entries=entries)
    if holding:
        fam = holding[0]
        status = 'extended'
    else:
        fam, k = str(name).strip(), 2
        if not fam: raise J.ProjectError('family needs a name')
        while fam in doc['families']:
            fam, k = f'{name}-{k}', k + 1
        status = 'created'
    members = list(dict.fromkeys(doc['families'].get(fam, {}).get('entries', []) + entries))
    doc['families'][fam] = dict(name=fam, entries=members)
    _save(root, doc)
    return dict(status=status, family=fam, families=[fam], entries=members)


def preview(root, entry, candidates):
    if not isinstance(candidates, list) or not candidates:
        raise J.ProjectError('supply a nonempty list of candidate states')
    base = J.load_version(root, entry)
    version = int(J.version_dir(root, entry).name[1:])
    states, seen = [], set()
    for c in candidates:
        if not isinstance(c, dict) or set(c) - {'multiplicity', 'charge', 'broken_symmetry'} or 'multiplicity' not in c:
            raise J.ProjectError('each state needs multiplicity and optional charge/broken_symmetry')
        if type(c['multiplicity']) is not int or ('charge' in c and type(c['charge']) is not int):
            raise J.ProjectError('state charge and multiplicity must be integers')
        edits = dict(multiplicity=c['multiplicity'], charge=c.get('charge', base['inputs']['charge']))
        if 'broken_symmetry' in c: edits['overrides'] = dict(broken_symmetry=c['broken_symmetry'])
        proposed = P.update_plan(base, **copy.deepcopy(edits))
        state = json.dumps(J.state_of(proposed), sort_keys=True)
        if state in seen: raise J.ProjectError('duplicate candidate electronic state')
        seen.add(state)
        reuse = J.state_of(proposed) == J.state_of(base)
        states.append(dict(edits=edits, state=J.state_label(*J.state_of(proposed)), reuse_source=reuse,
                           jobs=0 if reuse else len(proposed['jobs']),
                           core_hours=0 if reuse else proposed['cost']['total_core_hours'], blockers=proposed['open_items']))
    return dict(entry=entry, base_version=version, base_hash=P.compute_hash(base), candidates=copy.deepcopy(candidates),
                states=states, additional_jobs=sum(s['jobs'] for s in states),
                additional_core_hours=sum(s['core_hours'] for s in states))


def generate(root, name, proposal):
    base = J.load_version(root, proposal['entry'])
    if P.compute_hash(base) != proposal['base_hash'] or int(J.version_dir(root, proposal['entry']).name[1:]) != proposal['base_version']:
        raise J.ProjectError('source changed since state preview; preview again')
    fresh = preview(root, proposal['entry'], proposal['candidates'])
    if fresh['states'] != proposal['states']: raise J.ProjectError('state preview no longer matches candidates')
    doc = read(root)
    if not str(name).strip(): raise J.ProjectError('family needs a name')
    members = list(doc['families'].get(name, {}).get('entries', []))
    members.append(proposal['entry'])
    created = []
    try:
        for state in proposal['states']:
            if state['reuse_source']: continue
            ref = J.edit_entry(root, proposal['entry'], base_version=proposal['base_version'], **state['edits'])
            created.append(ref)
            members.append(ref.entry_id)
        doc['families'][name] = dict(name=name, entries=list(dict.fromkeys(members)))
        _save(root, doc)
    except Exception:
        for ref in created: shutil.rmtree(ref.path.parent)
        raise
    return dict(family=doc['families'][name], created=[dict(entry=r.entry_id, version=r.version) for r in created])


def energies(root, name, tolerance_kjmol=1.0):
    if not math.isfinite(tolerance_kjmol) or tolerance_kjmol < 0: raise J.ProjectError('energy tolerance must be finite and nonnegative')
    family = read(root)['families'].get(name)
    if family is None: raise J.ProjectError('unknown family')
    all_choices = study.available(root)
    selected = {}
    for c in all_choices:
        if c['entry'] in family['entries']: selected[c['entry']] = c['version']
    doc = study.compare(root, list(selected.items()))
    groups, seen, excluded = {}, set(), list(doc['issues'])
    for row in doc['rows']:
        identity = row['entry'], row['version'], row.get('job')
        if identity in seen or row.get('kind') != 'mossbauer_sp': continue
        seen.add(identity)
        bad = [f for f in row['flags'] if f not in ('calibration training entry', 'ambiguous Fe-site experiment')]
        if bad or row.get('ok') is not True or not study._number(row.get('energy_Eh')) or not row.get('protocol'):
            excluded.append(dict(entry=row['entry'], version=row['version'], job=row.get('job'), reason='; '.join(bad) or 'energy unavailable'))
            continue
        prov = row['provenance']
        key = json.dumps(dict(formula=prov['system']['formula'], n_atoms=prov['system']['n_atoms'],
                              charge=prov['inputs']['charge'], protocol=row['protocol']), sort_keys=True)
        groups.setdefault(key, []).append(row)
    result = []
    for key, rows in sorted(groups.items()):
        minimum = min(r['energy_Eh'] for r in rows)
        present = set(r['entry'] for r in rows)
        complete = present == set(family['entries'])
        for row in rows:
            delta = (row['energy_Eh'] - minimum) * HARTREE_KJMOL
            result.append(dict(entry=row['entry'], version=row['version'], job=row['job'], functional=row.get('functional'),
                               energy_Eh=row['energy_Eh'], relative_energy_kjmol=delta, within_tolerance=delta <= tolerance_kjmol,
                               lowest_computed=complete and delta <= 1e-8, comparison_complete=complete,
                               missing_entries=sorted(set(family['entries']) - present), protocol=json.loads(key),
                               result_path=row['result_path'], result_sha256=row['result_sha256']))
    return dict(family=family, tolerance_kjmol=tolerance_kjmol, rows=result, excluded=excluded,
                notes=['Relative energies compare computed states only; they do not establish an experimental ground state.',
                       'No lowest state is designated unless every family member is represented in that compatible protocol cohort.'])
