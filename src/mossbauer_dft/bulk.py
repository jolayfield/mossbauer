"""Series verbs: one selection of entries, every per-entry step (second-pass idea 2; decisions D52-D53).

Each verb acts on the newest version of each named entry, one entry at a time, through the same library call the single-entry
command uses, and reports one outcome per entry: ``done``, ``skipped`` (not eligible, with the reason) or ``failed`` (with the
error). A failure never stops the others, and nothing is rolled back: every write is an ordinary per-entry write, so running
the verb again is the recovery path (as for batch intake, D32). Approval stays per plan and human; nothing is submitted.
"""
from __future__ import annotations

import hashlib
import json

from . import presets as PR, project as J, revision

VERBS = ("setup", "analyze", "reanalyze")


def outcome(entry: str, version, result: str, detail: str = "") -> dict:
    return dict(entry=entry, version=version, outcome=result, detail=detail)


def eligibility(vs: J.VersionStatus | None, verb: str) -> str | None:
    """None when the newest version can take ``verb``; otherwise the reason it cannot."""
    if vs is None or vs.status is None:
        return "no readable newest version"
    if verb == "setup":
        if not vs.approved:
            return "plan is not approved (or was modified after approval)"
        return None
    props = [j for j in vs.jobs if j.kind in J.PROPERTY_KINDS]
    if not any(j.state == "complete" for j in props):
        return "no complete property output yet"
    if verb == "reanalyze" and not vs.has_results:
        return "no saved analysis to refresh"
    return None


def eligible(root, verb: str, entries=None) -> list[str]:
    """Entries (all, or those named) whose newest version can take ``verb`` now."""
    wanted = set(entries) if entries is not None else None
    return [e.entry_id for e in J.scan_project(root)
            if (wanted is None or e.entry_id in wanted) and eligibility(e.newest, verb) is None]


def run(root, verb: str, entries, *, site: str | None = None, calibration=None, level: float = 0.95) -> list[dict]:
    """Run ``verb`` (setup, analyze or reanalyze) for each entry; one outcome per entry, in the order given."""
    if verb not in VERBS:
        raise J.ProjectError(f"unknown bulk action {verb!r}; choose from {', '.join(VERBS)}")
    out = []
    for entry in dict.fromkeys(entries):
        try:
            vs = J.scan_version(root, entry)
        except J.ProjectError as ex:
            out.append(outcome(entry, None, "failed", str(ex)))
            continue
        why = eligibility(vs, verb)
        if why:
            out.append(outcome(entry, vs.version, "skipped", why))
            continue
        try:
            if verb == "setup":
                r = J.write_version_inputs(root, entry, vs.version, site)
                out.append(outcome(entry, vs.version, "done", f"{len(r['files'])} files written to inputs/"))
            else:
                r = J.analyze_version(root, entry, vs.version, calibration, level)
                out.append(outcome(entry, vs.version, "done",
                                   f"{len(r['rows'])} rows; delta status {r['delta_status']['status']}"))
        except Exception as ex:  # noqa: BLE001 -- one entry's failure is reported, the others still run
            out.append(outcome(entry, vs.version, "failed", f"{type(ex).__name__}: {ex}"))
    return out


def preview_preset(root, entries, name: str) -> dict:
    """Revision previews of applying preset ``name`` to each entry's newest version, plus one pin over all of them.
    Entries the preset would not change, or that cannot take it, are listed with the reason and left out of the pin."""
    proposals, skipped = [], []
    for entry in dict.fromkeys(entries):
        try:
            p = PR.preview_apply(root, entry, name)
        except Exception as ex:  # noqa: BLE001
            skipped.append(outcome(entry, None, "skipped", f"{type(ex).__name__}: {ex}"))
            continue
        if not p["changes"]:
            skipped.append(outcome(entry, p["base_version"], "skipped", "already matches the preset"))
            continue
        proposals.append(p)
    return dict(preset=name, proposals=proposals, skipped=skipped, pin=pin(proposals))


def pin(proposals) -> str:
    """Hash of every previewed (entry, source plan, edit): changes if any plan or the preset changed since the preview."""
    blob = json.dumps(sorted((p["entry"], PR.apply_pin(p)) for p in proposals))
    return hashlib.sha256(blob.encode()).hexdigest()


def apply_preset(root, preview: dict, expect_pin: str | None = None) -> list[dict]:
    """Save each previewed revision as a new draft version (``revision.save`` re-checks each plan for drift).
    ``expect_pin``, when given, must equal a fresh preview's pin, so the CLI cannot apply something nobody reviewed."""
    if expect_pin is not None:
        fresh = preview_preset(root, [p["entry"] for p in preview["proposals"]], preview["preset"])
        if fresh["pin"] != expect_pin or not expect_pin:
            raise J.ProjectError("plans or preset changed since the preview; preview again")
        preview = fresh
    out = []
    for p in preview["proposals"]:
        try:
            ref = revision.save(root, p)
            out.append(outcome(ref.entry_id, ref.version, "done",
                               "new draft version" if ref.entry_id == p["entry"] else f"new entry {ref.entry_id}"))
        except Exception as ex:  # noqa: BLE001
            out.append(outcome(p["entry"], p["base_version"], "failed", f"{type(ex).__name__}: {ex}"))
    return out


def summary(outcomes) -> str:
    counts = {k: sum(o["outcome"] == k for o in outcomes) for k in ("done", "skipped", "failed")}
    return ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "nothing to do"
