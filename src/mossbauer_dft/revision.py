"""Pure scientific edit previews and source-pinned saves."""
from __future__ import annotations

import copy
from . import plan as P, project as J


def _changes(before, after, prefix=''):
    if isinstance(before, dict) and isinstance(after, dict):
        out = []
        for key in sorted(set(before) | set(after)):
            out += _changes(before.get(key), after.get(key), f'{prefix}.{key}' if prefix else key)
        return out
    return [] if before == after else [dict(field=prefix, before=before, after=after)]


def preview(root, entry: str, **edits) -> dict:
    vd = J.version_dir(root, entry)
    source = J.load_version(root, entry)
    proposed = P.update_plan(source, **copy.deepcopy(edits))
    fields = ('inputs', 'methods', 'geometry', 'calibration', 'broken_symmetry', 'jobs', 'cost', 'open_items', 'rule_evaluation')
    return dict(entry=entry, base_version=int(vd.name[1:]), base_hash=P.compute_hash(source), edits=copy.deepcopy(edits),
                creates='entry' if J.state_of(source) != J.state_of(proposed) else 'version',
                changes=_changes({k: source[k] for k in fields}, {k: proposed[k] for k in fields}),
                jobs_before=len(source['jobs']), jobs_after=len(proposed['jobs']),
                cost_before=source['cost']['total_core_hours'], cost_after=proposed['cost']['total_core_hours'],
                blockers=list(proposed['open_items']), proposed=proposed)


def save(root, proposal: dict) -> J.VersionRef:
    """Reject source drift and preview tampering; edit through the normal copy-forward boundary."""
    source = J.load_version(root, proposal['entry'])
    version = int(J.version_dir(root, proposal['entry']).name[1:])
    if version != proposal['base_version'] or P.compute_hash(source) != proposal['base_hash']:
        raise J.ProjectError('plan changed since preview; refresh and preview the changes again')
    fresh = preview(root, proposal['entry'], **proposal['edits'])
    if fresh['changes'] != proposal['changes'] or fresh['creates'] != proposal['creates']:
        raise J.ProjectError('preview no longer matches proposed edits; preview again')
    return J.edit_entry(root, proposal['entry'], base_version=version, **proposal['edits'])
