"""Session state shared by every page: the open project, the cached scan, the current entry version, navigation."""
from __future__ import annotations
import copy
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import streamlit as st

from mossbauer_dft import project as J

PAGES = ["Project", "New prediction", "Batch intake", "Plan review", "Inputs", "Results", "Study", "Calibration", "States", "References", "Cluster setup"]
from mossbauer_dft.tutorial import PAGE_LABELS as STEPS  # noqa: E402,F401
ss = st.session_state


def _initial_path() -> str | None:
    """``--workdir`` / ``MOSSBAUER_GUI_WORKDIR``: the project folder to open (an empty folder offers to create one)."""
    if "--workdir" in sys.argv:
        return sys.argv[sys.argv.index("--workdir") + 1]
    return os.environ.get("MOSSBAUER_GUI_WORKDIR") or None


def _root() -> Path | None:
    p = ss.get("project")
    return Path(p) if p else None


def _path_kind(p: Path) -> tuple[str, str]:
    """What a typed folder is: project | legacy | empty | new (missing, parent writable) | error | refused."""
    if not p.exists():
        if p.parent.is_dir() and os.access(p.parent, os.W_OK):
            return "new", f"{p} does not exist."
        return "error", f"{p} does not exist, and its parent folder is missing or not writable."
    if not p.is_dir():
        return "error", f"{p} is a file, not a folder."
    if not os.access(p, os.W_OK | os.X_OK):
        return "error", f"{p} is not writable: a project needs a folder you can write to."
    if J.is_project(p):
        return "project", ""
    if J.is_legacy_workdir(p):
        return "legacy", (f"{p} is a single-plan work directory from an earlier version (plan/plan.yaml). Importing copies its plan, "
                          "inputs and uploaded outputs into a one-entry project in the same folder (entries/<id>/v1/); "
                          "the original files are left in place.")
    if not [x for x in p.iterdir() if x.name != ".DS_Store"]:
        return "empty", f"{p} is empty."
    return "refused", (f"{p} is not empty, and it is neither a project (no project.yaml) nor a single-plan work directory "
                       "(no plan/plan.yaml). Choose an empty or new folder for a new project, or open an existing project folder.")


def _set_project(p: Path, entry: str | None = None, version: int | None = None):
    J.open_project(p)
    ss.update(project=str(p), _target=None, _scan=None, entry=None, version=None, _prev_sel=[], _overview_ids=[], _approve_results=None)
    J.add_recent(p)
    if entry:
        try:
            ss.version = int(J.version_dir(p, entry, version).name[1:])
            ss.entry = entry
        except J.ProjectError:
            try:
                ss.version, ss.entry = int(J.version_dir(p, entry).name[1:]), entry
            except J.ProjectError:
                pass


def _evaluate(path: str, entry: str | None = None, version: int | None = None):
    """Open ``path`` when it is a project; otherwise remember what it is so the Project page can offer the next step."""
    p = Path(path).expanduser()
    p = p.resolve() if p.exists() else p.absolute()
    kind, msg = _path_kind(p)
    if kind == "project":
        try:
            _set_project(p, entry, version)
            return
        except Exception as e:  # noqa: BLE001
            kind, msg = "error", f"Could not open {p}: {type(e).__name__}: {e}"
    ss._target = dict(path=str(p), kind=kind, msg=msg)


def _fail(path: str, msg: str):
    """Show ``msg`` as the Project page's error for ``path``."""
    ss._target = dict(path=path, kind="error", msg=msg)


def _invalidate():
    ss._scan = None


def _scan() -> list[J.EntryStatus]:
    """Status of every entry, computed from the folder once per open / refresh / app write, not on every rerun."""
    root = _root()
    c = ss.get("_scan")
    if not c or c["root"] != str(root):
        c = ss._scan = dict(root=str(root), rows=J.scan_project(root), at=datetime.now())
    return c["rows"]


@dataclass
class Current:
    root: Path
    entry: J.EntryStatus
    vs: J.VersionStatus
    plan: dict | None

    @property
    def newest(self) -> bool:
        return self.vs.version == self.entry.newest.version


def _current() -> Current | None:
    """The selected entry version (one accessor for every page), or None when no project / entry is selected."""
    root, eid = _root(), ss.get("entry")
    if root is None or not eid:
        return None
    e = next((x for x in _scan() if x.entry_id == eid), None)
    if e is None or not e.versions:
        return None
    vs = next((v for v in e.versions if v.version == ss.get("version")), e.newest)
    plan = None
    if vs.status is not None:
        try:
            plan = _load_plan(root, eid, vs.version)
        except Exception:  # noqa: BLE001
            plan = None
    return Current(root, e, vs, plan)


def _stamp(path: Path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _load_plan(root: Path, eid: str, version: int) -> dict:
    """``J.load_version``, parsed again only when plan.yaml or structure.xyz changed since the last call."""
    vd = J.version_dir(root, eid, version)
    key = (str(vd), _stamp(vd / "plan.yaml"), _stamp(vd / "structure.xyz"))
    c = ss.get("_plan_cache")
    if not c or c[0] != key:
        c = ss._plan_cache = (key, J.load_version(root, eid, version))
    return copy.deepcopy(c[1])


def _goto(page: str):
    ss.page = page


def _guide_now():
    """The guide for the open project, from the cached scan (None without a project)."""
    from mossbauer_dft import tutorial as T
    root = _root()
    if root is None:
        return None
    rows = _scan()
    key = (str(root), ss._scan["at"], _stamp(root / "project.yaml"), (root / "recorded" / "pack.json").is_file())
    c = ss.get("_guide_cache")
    if not c or c[0] != key:
        c = ss._guide_cache = (key, T.guide(root, rows))
    return c[1]


def _waiting(page: str) -> list[tuple[str, str]]:
    """(step title, entry id) for every entry whose next step is done on ``page``."""
    g = _guide_now()
    return [(s["title"], e) for s in (g["steps"] if g else []) if page in s["page_keys"] for e in s["entries"]]


def _cb_open_entry(entry_id: str, page: str):
    """Make ``entry_id`` (newest version) current and go to ``page``."""
    ss.entry, ss.version, ss.page = entry_id, None, page
    for e in _scan():
        if e.entry_id == entry_id and e.newest:
            ss.version = e.newest.version


def _cb_seal(eligible: list[dict]):
    """Seal the versions the preview showed; one that changed or gained a newer version since is skipped with the reason."""
    root = _root()
    who = (ss.get("approver") or "").strip()
    outs = J.approve_versions(root, [r["_ref"] for r in eligible], who, via="gui",
                              expected_hashes={r["entry"]: r["_hash"] for r in eligible})
    ss._approve_results = [dict(entry=o.entry_id, version=o.version, outcome=o.outcome, detail=o.reason or J.approval_line(o.approval) or "")
                           for o in outs]
    _invalidate()


def _apply_selection():
    """Clicking an overview row selects that entry and its newest version (read before the sidebar renders)."""
    try:
        rows = list(ss["overview"]["selection"]["rows"])
    except (KeyError, TypeError, AttributeError):
        rows = []
    ids = ss.get("_overview_ids") or []
    sel = [ids[i] for i in rows if 0 <= i < len(ids)]
    prev = ss.get("_prev_sel") or []
    if sel != prev:
        ss._approve_results = None
    new = [e for e in sel if e not in prev]
    ss._prev_sel = sel
    if new and _root() is not None:
        e = next((x for x in _scan() if x.entry_id == new[-1]), None)
        if e is not None and e.newest is not None:
            ss.entry, ss.version = e.entry_id, e.newest.version

