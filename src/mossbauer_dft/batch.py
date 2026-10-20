"""Batch intake: many compound-states into a project at once (preview first, then create the ready rows as drafts).

Rows are plain dicts with the CSV columns below, one per compound in one electronic state.  Rows find their structure
through a reference (``<source>#<n>``) that stays fixed when the compound is renamed.  Nothing here approves a plan,
and the project keeps no record of a batch beyond the entries it creates and their family links.
Standard library + core package modules; importing the CLI does not import GUI dependencies.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import PurePath

from . import families as F
from . import plan as P
from . import project as J
from . import structure_sources as SS

SCHEMA = "1"
COLUMNS = ["structure", "compound", "charge", "multiplicity", "oxidation_state", "radical_ligand", "nitrosyl", "solvent",
           "temperature_K", "exp_delta", "exp_deq"]
EMPTY_ROW = {c: None for c in COLUMNS}
OPTIONS_TAG = "# mossbauer-batch options:"
OPTION_KEYS = {"geometry", "delta_functional", "deq_functional", "crosscheck_functional", "calibration_strategy", "nprocs",
               "highfield", "allow_unverified_calibration", "allow_protocol_mismatch", "user_refs", "refset_subset"}
INT_COLUMNS = ("charge", "multiplicity", "oxidation_state")
FLOAT_COLUMNS = ("temperature_K", "exp_delta", "exp_deq")
TRI_COLUMNS = ("radical_ligand", "nitrosyl")
STATES_HINT = ("The matched rule needs a broken-symmetry state: after creating this entry, add it on the States page "
               "(or `mossbauer project family`) into this compound's family. This draft stays as the state without broken symmetry.")


class BatchError(J.ProjectError):
    """A create run stopped after writing some entries; ``created`` lists them (they stay in the project)."""

    def __init__(self, message: str, created: list[str], rows: list[dict]):
        super().__init__(message)
        self.created, self.rows = created, rows


@dataclass
class Sources:
    structures: dict = field(default_factory=dict)   # reference -> SS.FoundStructure, in load order
    notes: list[str] = field(default_factory=list)   # skipped blocks and unreadable sources
    texts: dict = field(default_factory=dict)        # unique source name -> original text (for re-download)


def _unique_name(name: str, taken) -> str:
    if name not in taken:
        return name
    p = PurePath(name)
    k = 2
    while f"{p.stem}-{k}{p.suffix}" in taken:
        k += 1
    return f"{p.stem}-{k}{p.suffix}"


def load_sources(items, into: Sources | None = None) -> Sources:
    """[(filename, text)] -> Sources.  Each source gets a name unique within the batch (``si.cif``, ``si-2.cif``) so
    references never collide; a source that cannot be read becomes a note instead of stopping the batch."""
    src = into or Sources()
    for filename, text in items:
        name = _unique_name(PurePath(filename).name, src.texts)
        src.texts[name] = text
        try:
            found, notes = SS.find_structures(text, name)
        except ImportError:
            src.notes.append(f"{name}: needs RDKit to read this format (run mossbauer doctor and reinstall your downloaded wheel)")
            continue
        except Exception as e:  # noqa: BLE001 -- one unreadable source never hides the others
            src.notes.append(f"{name}: could not be read: {e}")
            continue
        src.notes += notes
        for f in found:
            src.structures[f.ref] = f
    return src


def rows_from_sources(src: Sources) -> list[dict]:
    """One starting row per structure, named from its source; charge only where the format carries it (molfile)."""
    return [dict(EMPTY_ROW, structure=ref, compound=f.name, charge=f.charge) for ref, f in src.structures.items()]


# ----------------------------------------------------------------------------- CSV (the saved form of a batch)
def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def write_csv(rows: list[dict], options: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"{OPTIONS_TAG} {json.dumps(options or {}, sort_keys=True, separators=(',', ':'))}"])
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in COLUMNS])
    return buf.getvalue()


def _parse(col: str, raw: str):
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        if col in INT_COLUMNS:
            return int(raw)
        if col in FLOAT_COLUMNS:
            return float(raw)
    except ValueError:
        return raw  # kept as typed; the preview blocks the row with the reason
    if col in TRI_COLUMNS:
        low = raw.lower()
        if low in ("yes", "y", "true", "1"):
            return True
        if low in ("no", "n", "false", "0"):
            return False
        return raw
    return raw


def read_csv(text: str) -> tuple[list[dict], dict | None]:
    """CSV text -> (rows, recorded options or None).  Tolerates a spreadsheet re-save: byte-order mark, the options line
    split into cells or quoted, trailing empty columns.  An unknown column refuses the whole file."""
    table = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if any(c.strip() for c in r)]
    options = None
    if table and table[0][0].strip().strip('"').startswith(OPTIONS_TAG):
        joined = ",".join(table.pop(0)).rstrip(", ").strip().strip('"')
        try:
            options = json.loads(joined[len(OPTIONS_TAG):].strip() or "{}")
        except json.JSONDecodeError as e:
            raise J.ProjectError(f"CSV options line is not valid JSON: {e}") from e
    if not table:
        raise J.ProjectError("CSV has no header row")
    header = [h.strip() for h in table[0]]
    for h in header:
        if h and h not in COLUMNS:
            raise J.ProjectError(f"CSV has unknown column {h!r}; expected columns: {', '.join(COLUMNS)}")
    rows = []
    for rec in table[1:]:
        row = dict(EMPTY_ROW)
        for h, v in zip(header, rec):
            if h:
                row[h] = _parse(h, v)
        rows.append(row)
    return rows, options


# ----------------------------------------------------------------------------- options and field mapping
def normalize_options(options: dict | None) -> dict:
    """Batch-wide method options as the override dict a one-at-a-time CLI entry would store: changed keys only."""
    opts = dict(options or {})
    if "broken_symmetry" in opts:
        raise J.ProjectError("broken symmetry is per structure and cannot be a batch option; set it per entry afterwards")
    unknown = set(opts) - OPTION_KEYS
    if unknown:
        raise J.ProjectError(f"unknown batch option(s): {', '.join(sorted(unknown))}")
    return {k: v for k, v in opts.items() if v not in (None, "", False)}


def _plan_kwargs(row: dict, options: dict, found) -> dict:
    """Mirror `cli._plan_kwargs`: hints, experimental {delta, deq, T_K} and overrides (R20)."""
    exp = {k: row.get(c) for k, c in (("delta", "exp_delta"), ("deq", "exp_deq"), ("T_K", "temperature_K")) if row.get(c) is not None}
    src, _, n = found.ref.partition("#")
    p = PurePath(src)
    fname = src if not n and p.suffix.lower() in (".xyz", ".cif") else f"{p.stem}_{n}.xyz" if n else f"{p.stem}.xyz"
    return dict(oxidation_state=row.get("oxidation_state"), radical_ligand=row.get("radical_ligand"), solvent=row.get("solvent") or None,
                temperature_K=row.get("temperature_K"), nitrosyl=row.get("nitrosyl"), experimental=exp, overrides=dict(options),
                structure_filename=fname)


def display_name(name) -> str:
    """A compound name with leading, trailing and repeated whitespace collapsed (how entries and families are named)."""
    return " ".join(str(name or "").split())


def _norm(name) -> str:
    return display_name(name).casefold()


def _label(row: dict) -> str:
    return J.state_label(row["charge"], row["multiplicity"])


def _match_index(root) -> dict:
    """(normalized compound name, state label) -> [entry ids] over the whole project."""
    idx: dict = {}
    for e in J.list_entries(root):
        nv = e.newest
        label = J.state_label(*J.state_of(nv.plan)) if nv and nv.plan else e.state_label
        idx.setdefault((_norm(e.display_name or e.entry_id), label), []).append(e.entry_id)
    return idx


def _same_geometry(root, entry_id: str, found) -> bool:
    try:
        return J.load_version(root, entry_id).get("_structure_xyz") == found.structure.to_xyz_text()
    except Exception:  # noqa: BLE001 -- an unreadable entry is never linked
        return False


def _link_families(root, rows: list[dict], src: Sources) -> list[dict]:
    """Rows sharing a structure reference become one state family, over created rows and rows skipped as already in the
    project.  A skipped row's entry joins only when it was built from this same geometry (a reused compound name with a
    different structure is left out, with a note)."""
    groups: dict = {}
    for row in rows:
        if row["outcome"] not in ("created", "skipped") or not row["entries"]:
            continue
        found = src.structures[row["structure"]]
        ids = row["entries"] if row["outcome"] == "created" else [e for e in row["entries"] if _same_geometry(root, e, found)]
        if row["outcome"] == "skipped" and len(ids) < len(row["entries"]):
            row["notes"].append("not linked into a family: the existing entry was built from a different geometry")
        g = groups.setdefault(row["structure"], dict(name=display_name(row["compound"]), entries=[]))
        g["entries"] += ids
    links = []
    for ref, g in groups.items():
        members = list(dict.fromkeys(g["entries"]))
        if len(members) > 1:
            links.append(dict(structure=ref, **F.link(root, g["name"], members)))
    return links


def _entry_dirs(root) -> set[str]:
    d = J._entries(root)
    return {p.name for p in d.iterdir() if p.is_dir()} if d.is_dir() else set()


# ----------------------------------------------------------------------------- preview and create
def row_problem(row: dict, src: Sources) -> str | None:
    if row.get("structure") not in src.structures:
        return f"structure {row.get('structure')!r} not found among the loaded sources"
    for c in ("charge", "multiplicity"):
        if row.get(c) is None:
            return f"{c} is missing"
    for c in INT_COLUMNS:
        if row.get(c) is not None and type(row[c]) is not int:
            return f"{c} must be an integer, got {row[c]!r}"
    for c in FLOAT_COLUMNS:
        if row.get(c) is not None and not isinstance(row[c], (int, float)):
            return f"{c} must be a number, got {row[c]!r}"
    for c in TRI_COLUMNS:
        if row.get(c) is not None and not isinstance(row[c], bool):
            return f"{c} must be yes, no or blank, got {row[c]!r}"
    if SS.is_placeholder(row.get("compound")):
        return "compound name not filled in"
    return None


def preview(root, rows: list[dict], src: Sources, options: dict | None, recorded_options: dict | None = None) -> dict:
    """Evaluate every row without writing: ready, ready-open-items, skipped (already in project) or blocked, with the
    reason, the draft plan's stats and warnings, and a hash that pins what was shown."""
    J.open_project(root)
    opts = normalize_options(options)
    warnings = []
    if recorded_options is not None and normalize_options(recorded_options) != opts:
        warnings.append("Batch method options differ from the ones recorded in the imported CSV; rows created now will "
                        "use the current options.")
    idx = _match_index(root)
    problems = [row_problem(r, src) for r in rows]
    keyed = {}
    for i, r in enumerate(rows):
        if problems[i] is None:
            keyed.setdefault(_norm(r["compound"]), []).append(i)
    out = []
    for i, r in enumerate(rows):
        row = dict(index=i, structure=r.get("structure"), compound=r.get("compound"), state=None, outcome="blocked", reason=None,
                   entries=[], stats={}, open_items=[], notes=[], warnings=[])
        out.append(row)
        if problems[i]:
            row["reason"] = problems[i]
            continue
        row["state"] = _label(r)
        same = keyed[_norm(r["compound"])]
        if len({rows[j]["structure"] for j in same}) > 1:
            row["reason"] = "compound name is used by rows with different structures; give each structure its own name"
            continue
        if sum(_label(rows[j]) == row["state"] for j in same) > 1:
            row["reason"] = "the same compound and state appear more than once in this batch"
            continue
        found = src.structures[r["structure"]]
        try:
            plan = P.make_plan(found.structure, r["charge"], r["multiplicity"], name="batch_preview", **_plan_kwargs(r, opts, found))
        except Exception as e:  # noqa: BLE001 -- parity, no Fe, bad hints: the plan builder's own message is the reason
            row["reason"] = str(e)
            continue
        st = found.structure
        row["stats"] = dict(formula=st.formula, n_atoms=len(st), n_fe=len(st.fe_indices), jobs=len(plan["jobs"]),
                            core_hours=plan["cost"].get("total_core_hours"))
        row["warnings"] = list(found.warnings)
        if len(st.fe_indices) > 1 and (r.get("exp_delta") is not None or r.get("exp_deq") is not None):
            row["warnings"].append(f"{len(st.fe_indices)} Fe atoms but a single experimental value: per-site comparison is ambiguous")
        existing = idx.get((_norm(r["compound"]), row["state"]))
        if existing:
            row.update(outcome="skipped", entries=list(existing),
                       reason=f"already in project as {', '.join(existing)}; to use a changed geometry, edit that entry with a "
                              "replacement structure")
            continue
        row["open_items"] = list(plan["open_items"])
        if plan["rule_evaluation"]["selected_rule"] in P.BS_RULES:
            row["notes"].append(STATES_HINT)
        row["outcome"] = "ready-open-items" if row["open_items"] else "ready"
    counts = {k: sum(r["outcome"] == k for r in out) for k in ("ready", "ready-open-items", "skipped", "blocked")}
    geoms = {ref: hashlib.sha256(src.structures[ref].structure.to_xyz_text().encode()).hexdigest()
             for ref in sorted({r.get("structure") for r in rows if r.get("structure") in src.structures})}
    pin = json.dumps(dict(rows=rows, options=opts, geometries=geoms,
                          outcomes=[(r["outcome"], r["reason"], r["entries"]) for r in out]), sort_keys=True, default=str)
    return dict(schema_version=SCHEMA, options=opts, rows=out, counts=counts, warnings=warnings, notes=list(src.notes),
                preview_hash=hashlib.sha256(pin.encode()).hexdigest())


def create(root, rows: list[dict], src: Sources, options: dict | None, expected_hash: str | None = None,
           recorded_options: dict | None = None) -> dict:
    """Create a draft entry for every ready row (never approved), then link same-structure rows into families over both
    created and already-present entries.  ``expected_hash`` pins the preview the caller showed."""
    pv = preview(root, rows, src, options, recorded_options)
    if expected_hash and pv["preview_hash"] != expected_hash:
        raise J.ProjectError("batch preview changed since it was shown; preview again")
    opts = pv["options"]
    created = []
    idx, known = _match_index(root), _entry_dirs(root)
    try:
        for row in pv["rows"]:
            if row["outcome"] not in ("ready", "ready-open-items"):
                continue
            r = rows[row["index"]]
            if _entry_dirs(root) - known:  # an entry appeared that this run did not write: re-read the project
                idx, known = _match_index(root), _entry_dirs(root)
            now = idx.get((_norm(r["compound"]), row["state"]))
            if now:  # appeared since the preview (another writer, a double click): never a suffixed duplicate
                row.update(outcome="skipped", entries=list(now), reason=f"already in project as {', '.join(now)}")
                continue
            found = src.structures[r["structure"]]
            ref = J.add_entry(root, found.structure, r["charge"], r["multiplicity"], name=display_name(r["compound"]),
                              **_plan_kwargs(r, opts, found))
            row.update(outcome="created", entries=[ref.entry_id], reason=None)
            created.append(ref.entry_id)
            idx.setdefault((_norm(r["compound"]), row["state"]), []).append(ref.entry_id)
            known.add(ref.entry_id)
    except Exception as e:
        raise BatchError(f"batch stopped after creating {len(created)} entr{'y' if len(created) == 1 else 'ies'}: "
                         f"{type(e).__name__}: {e}", created, pv["rows"]) from e
    try:
        links = _link_families(root, pv["rows"], src)
    except Exception as e:
        raise BatchError(f"created {len(created)} entr{'y' if len(created) == 1 else 'ies'} but linking families failed: "
                         f"{type(e).__name__}: {e}", created, pv["rows"]) from e
    pv.update(created=created, families=links)
    return pv
