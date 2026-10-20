"""Project workspace: a folder holding many entries (one compound in one electronic state), each with numbered plan versions.

Layout (all locations recorded inside a project are relative to it)::

    <project>/project.yaml                      marker: schema_version, name, created (never status)
    <project>/README.md                         generated layout notes for people and agents
    <project>/entries/<entry-id>/entry.yaml     name as typed, state label, highest version issued (never status)
    <project>/entries/<entry-id>/v<N>/          plan.yaml + structure.xyz, inputs/, outputs/, results/

Versions are copy-forward: an edit writes ``v<N+1>`` from the newest version and never touches an earlier one.  An edit that
changes charge, multiplicity or the broken-symmetry variant changes the electronic state and creates a new entry instead.
Status is never stored; ``scan_version``/``scan_project`` compute it from the folder on every call.  Only the standard library, PyYAML and the package's
non-GUI modules are used here, so the CLI works without the ``gui`` extra.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import data as _data
from . import plan as P
from . import workflow as W
from .orca_parse import parse_orca_text

SCHEMA = "1.0"
PROJECT_FILE = "project.yaml"
ENTRY_FILE = "entry.yaml"
ENTRIES_DIR = "entries"
MANIFEST = "manifest.json"
RECENTS_FILE = "recent_projects.json"
RESULTS_FILE = "analysis.json"
STATUSES = ("draft", "approved", "inputs written", "outputs found", "analyzed")
JOB_STATES = ("complete", "incomplete", "missing", "wrong-version", "unverified")
PROPERTY_KINDS = ("mossbauer_sp", "highfield_sp")
_VDIR = re.compile(r"v(\d+)$")

README_TEXT = """# Mossbauer-DFT project

This folder is a project managed by `mossbauer-dft`. Status is computed from the files here every time the project is
opened or refreshed, so files you add appear without an import step. Full reference: `docs/project-layout.md` in the
mossbauer-dft repository.

## Layout

- `project.yaml` marks the folder as a project (name, created time). It never records status.
- `entries/<entry-id>/` is one compound in one electronic state (charge, multiplicity, broken-symmetry variant);
  `entry.yaml` holds its display name and the highest version number issued.
- `entries/<entry-id>/v<N>/` is one plan version: `plan.yaml` and `structure.xyz` (always kept together; the
  structure is part of the plan's seal). An existing version is never overwritten; an edit creates the next version,
  and a change of charge or multiplicity creates a new entry.
- `v<N>/inputs/` holds the generated ORCA decks, `orca_job.slurm`, `submit.sh` and `manifest.json` (written last).
- `v<N>/outputs/` is where finished ORCA output files go, named `<stem>.out` after the job stems in that version's
  `plan.yaml`. The cluster script copies outputs back next to the decks in `inputs/`: move the `.out` files from
  `inputs/` to `outputs/` before analysis. Only `<stem>.out` files in `outputs/` count; `slurm-*.out` logs and other
  files are ignored. Never copy one version's outputs into another version's folder.
- `v<N>/results/analysis.json` holds the saved analysis for that version.

## For agents and scripts

- `mossbauer project status --json --project <this folder>` lists every entry and version with its status and the
  project-relative `inputs_dir`, `outputs_dir` and `submit_script`, plus which outputs are expected, complete,
  incomplete, missing, wrong_version, unverified or unmatched.
- Submit from `inputs/` with `bash submit.sh`; afterwards place `<stem>.out` files in that version's `outputs/`.
- Approve on a person's behalf only when they ask, and always name them:
  `mossbauer project approve --project <this folder> --entry <id> --version <N> --expect-hash <plan_hash>
  --via agent --on-behalf-of "<Name>"` (the version and hash you reviewed, from `status --json`).
- Many compounds at once: `mossbauer project batch template|preview|create --project <this folder> --structures FILE --csv sheet.csv`
  (preview writes nothing; create makes drafts only).
- Shared protocol: `mossbauer project preset list --project <this folder>`; use `--preset NAME` on add, batch and edit.
- New to the workflow? `mossbauer project guide --project <this folder>` lists the steps, which are done and what is next.
- This folder never holds credentials, passwords or tokens. Keep cluster access in your own SSH configuration.
"""


class ProjectError(ValueError):
    pass


class NotAProjectError(ProjectError):
    pass


class LegacyWorkdirError(NotAProjectError):
    """The folder is a pre-project work directory (``plan/plan.yaml``); import it with ``import_legacy``."""


class StateChangeError(ProjectError):
    """An edit changes the electronic state, so it must become a new entry, not a version."""


@dataclass(frozen=True)
class VersionRef:
    entry_id: str
    version: int
    path: Path


@dataclass
class VersionInfo:
    entry_id: str
    version: int
    path: Path
    plan: dict | None = None
    error: str | None = None

    @property
    def unreadable(self) -> bool:
        return self.plan is None


@dataclass
class EntryInfo:
    entry_id: str
    path: Path
    display_name: str | None
    state_label: str | None
    versions: list[VersionInfo] = field(default_factory=list)
    error: str | None = None

    @property
    def newest(self) -> VersionInfo | None:
        return self.versions[-1] if self.versions else None


# ----------------------------------------------------------------------------- small helpers
def _write_atomic(path: Path, text: str):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _dump(d: dict) -> str:
    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    if not isinstance(d, dict):
        raise ProjectError(f"{path.name} is not a mapping")
    return d


def _entries(root) -> Path:
    return Path(root) / ENTRIES_DIR


def _entry_dir(root, entry_id: str) -> Path:
    d = _entries(root) / entry_id
    if not (d / ENTRY_FILE).is_file():
        raise ProjectError(f"no entry {entry_id!r} in project {Path(root)}")
    return d


# ----------------------------------------------------------------------------- electronic state and entry ids
def state_of(plan: dict) -> tuple:
    """(charge, multiplicity, broken-symmetry spec) of a plan: what identifies an entry's electronic state."""
    inp = plan["inputs"]
    return int(inp["charge"]), int(inp["multiplicity"]), (plan.get("broken_symmetry") or {}).get("spec") or None


def _bs_tag(bs: dict | None) -> str:
    if not bs:
        return ""
    if bs.get("kind") == "brokensym":
        return f"bs{bs.get('na')}_{bs.get('nb')}"
    return "bs_flip" + "_".join(str(a) for a in bs.get("flip_atoms") or ())


def state_label(charge: int, multiplicity: int, bs: dict | None = None) -> str:
    return f"q{charge} m{multiplicity}" + (f" {_bs_tag(bs)}" if bs else "")


def _slug(name: str, charge: int, multiplicity: int, bs: dict | None) -> str:
    q = f"m{-charge}" if charge < 0 else str(charge)
    return P._safe(f"{P._safe(name)}_q{q}_m{multiplicity}" + (f"_{_bs_tag(bs)}" if bs else ""))


def _unique_id(root, base: str) -> str:
    eid, n = base, 1
    while (_entries(root) / eid).exists():
        n += 1
        eid = f"{base}_{n}"
    return eid


# ----------------------------------------------------------------------------- project open / create
def is_project(root) -> bool:
    return (Path(root) / PROJECT_FILE).is_file()


def is_legacy_workdir(root) -> bool:
    return (Path(root) / "plan" / "plan.yaml").is_file() and not is_project(root)


def _write_project_files(root: Path, name: str | None):
    _write_atomic(root / "README.md", README_TEXT)
    _write_atomic(root / PROJECT_FILE, _dump(dict(schema_version=SCHEMA, name=name or root.name, created=P._now())))


def create_project(root, name: str | None = None) -> dict:
    """Make ``root`` (created if needed) a project: ``project.yaml``, ``README.md`` and an empty ``entries/``."""
    root = Path(root)
    if is_project(root):
        raise ProjectError(f"{root} is already a project")
    if is_legacy_workdir(root):
        raise LegacyWorkdirError(f"{root} holds a single-plan work directory; import it as a project instead")
    _entries(root).mkdir(parents=True, exist_ok=True)
    _write_project_files(root, name)
    return open_project(root)


def set_example(root, example_id: str):
    """Mark a project as created from a bundled teaching example (``example`` key in ``project.yaml``)."""
    meta = open_project(root)
    _write_atomic(Path(root) / PROJECT_FILE, _dump(dict(meta, example=example_id)))


def open_project(root) -> dict:
    """Read ``project.yaml``.  Raises ``LegacyWorkdirError`` for a pre-project work dir, ``NotAProjectError`` otherwise."""
    root = Path(root)
    if not is_project(root):
        if is_legacy_workdir(root):
            raise LegacyWorkdirError(f"{root} is a single-plan work directory: import it as a project (originals are kept)")
        raise NotAProjectError(f"{root} is not a project (no {PROJECT_FILE})")
    return _read_yaml(root / PROJECT_FILE)


# ----------------------------------------------------------------------------- entries and versions
def _next_version(entry_dir: Path) -> int:
    try:
        issued = int(_read_yaml(entry_dir / ENTRY_FILE).get("last_version") or 0)
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        issued = 0
    return max([issued] + _version_numbers(entry_dir)) + 1


def _version_numbers(entry_dir: Path) -> list[int]:
    return sorted(int(m.group(1)) for p in entry_dir.iterdir() if p.is_dir() and (m := _VDIR.match(p.name)))


def _write_version(entry_dir: Path, plan: dict, n: int, populate=None) -> Path:
    """Assemble ``v<n>`` under a temporary name, then rename it into place (never over an existing folder)."""
    final = entry_dir / f"v{n}"
    if final.exists():
        raise ProjectError(f"{final} already exists")
    tmp = entry_dir / f".tmp-v{n}-{uuid.uuid4().hex[:8]}"
    try:
        tmp.mkdir()
        if populate:
            populate(tmp)
        else:
            P.save_plan(plan, tmp / "plan.yaml")
        (tmp / "outputs").mkdir(exist_ok=True)
        os.rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


def _write_entry_file(entry_dir: Path, name: str, label: str, last_version: int):
    _write_atomic(entry_dir / ENTRY_FILE, _dump(dict(schema_version=SCHEMA, name=name, state=label, last_version=last_version)))


def _new_entry(root, display_name: str, entry_id: str, plan: dict, populate=None) -> VersionRef:
    d = _entries(root) / entry_id
    d.mkdir(parents=True)  # claims the id; fails if taken
    try:
        _write_version(d, plan, 1, populate)
        _write_entry_file(d, display_name, state_label(*state_of(plan)), 1)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)  # this call created d; a half-made entry would block every retry
        raise
    return VersionRef(entry_id, 1, d / "v1")


def add_entry(root, structure, charge: int, multiplicity: int, *, name: str, **plan_kwargs) -> VersionRef:
    """New entry with a draft ``v1`` built by ``plan.make_plan``.  The entry id (a slug of name + state, unique in the
    project) is written as the plan's ``inputs.name`` so job stems never collide across entries."""
    open_project(root)
    bs = (plan_kwargs.get("overrides") or {}).get("broken_symmetry") or None
    eid = _unique_id(root, _slug(name, charge, multiplicity, bs))
    plan = P.make_plan(structure, charge, multiplicity, name=eid, **plan_kwargs)
    return _new_entry(root, name, eid, plan)


def _newest_plan(root, entry_id: str) -> tuple[Path, dict]:
    vd = version_dir(root, entry_id)
    return vd.parent, P.load_plan(vd / "plan.yaml")


def _derive(root, entry_id: str, edits: dict) -> tuple[Path, dict, dict]:
    d, plan = _newest_plan(root, entry_id)
    return d, plan, P.update_plan(plan, **copy.deepcopy(edits))


def _commit_version(d: Path, entry_id: str, new: dict) -> VersionRef:
    n = _next_version(d)
    path = _write_version(d, new, n)
    e = _read_yaml(d / ENTRY_FILE)
    _write_entry_file(d, e.get("name") or entry_id, e.get("state") or state_label(*state_of(new)), n)
    return VersionRef(entry_id, n, path)


def new_version(root, entry_id: str, **edits) -> VersionRef:
    """Copy-forward edit of the newest version into ``v<N+1>`` (a draft).  Refuses an edit that changes the electronic state."""
    d, plan, new = _derive(root, entry_id, edits)
    if state_of(new) != state_of(plan):
        raise StateChangeError("charge, multiplicity or broken-symmetry variant changed: this is a new entry, not a version")
    return _commit_version(d, entry_id, new)


def add_version(root, entry_id: str, plan: dict) -> VersionRef:
    """Store an already-built plan (e.g. from a newly loaded structure) as the next version of ``entry_id``.  Refuses a
    plan whose electronic state differs from the entry's newest version: that belongs in a new entry."""
    d, newest = _newest_plan(root, entry_id)
    if state_of(plan) != state_of(newest):
        raise StateChangeError("charge, multiplicity or broken-symmetry variant differs: this is a new entry, not a version")
    return _commit_version(d, entry_id, plan)


def replace_structure(root, entry_id: str, structure, **edits) -> VersionRef:
    """Next version of ``entry_id`` built from a new structure file (e.g. a refined geometry), carrying the newest
    version's name, hints and overrides plus ``edits``.  Refuses a change of electronic state, like ``add_version``."""
    from .structure import read_structure
    _, plan = _newest_plan(root, entry_id)
    base = copy.deepcopy(plan)
    base["_structure_xyz"] = read_structure(structure).to_xyz_text()
    base["inputs"]["structure_file"] = Path(structure).name
    return add_version(root, entry_id, P.update_plan(base, **copy.deepcopy(edits)))


def edit_entry(root, entry_id: str, base_version: int | None = None, **edits) -> VersionRef:
    """Apply an edit: a new version of the entry, or, when the electronic state changes, a new entry (``v1``) for that
    state carrying the same structure, hints and overrides.  The original entry is left untouched in that case.
    ``base_version`` is the version the editor was looking at: refused if a newer one has been written since."""
    newest = list_versions(root, entry_id)[-1].version if base_version is not None else None
    if newest != base_version:
        raise ProjectError(f"v{newest} of {entry_id} was written after v{base_version} was opened; refresh and edit v{newest}")
    d, plan, new = _derive(root, entry_id, edits)
    if state_of(new) == state_of(plan):
        return _commit_version(d, entry_id, new)
    name = _read_yaml(d / ENTRY_FILE).get("name") or entry_id
    eid = _unique_id(root, _slug(name, *state_of(new)))
    base = copy.deepcopy(plan)
    base["inputs"]["name"] = eid  # job stems follow the new entry id
    return _new_entry(root, name, eid, P.update_plan(base, **copy.deepcopy(edits)))


def list_versions(root, entry_id: str) -> list[VersionInfo]:
    """All versions of an entry in ascending order.  A version whose plan cannot be loaded is returned with ``plan=None``."""
    d = _entries(root) / entry_id
    return [_version_info(d, entry_id, n) for n in _version_numbers(d)]


def _version_info(entry_dir: Path, entry_id: str, n: int) -> VersionInfo:
    """``v<n>`` of an entry with its plan loaded, or ``plan=None`` and the error when it cannot be loaded."""
    vd = entry_dir / f"v{n}"
    try:
        plan = P.load_plan(vd / "plan.yaml")
        if not isinstance(plan, dict) or "inputs" not in plan:
            raise ProjectError("plan.yaml is not a plan")
        return VersionInfo(entry_id, n, vd, plan)
    except Exception as e:  # noqa: BLE001 -- one bad folder must not break the listing
        return VersionInfo(entry_id, n, vd, None, f"{type(e).__name__}: {e}")


def list_entries(root) -> list[EntryInfo]:
    """Every entry (sorted by id) with its versions."""
    open_project(root)
    ents = _entries(root)
    out = []
    for d in sorted(p for p in ents.iterdir() if p.is_dir() and not p.name.startswith(".")) if ents.is_dir() else []:
        name = label = err = None
        try:
            e = _read_yaml(d / ENTRY_FILE)
            name, label = e.get("name"), e.get("state")
        except Exception as ex:  # noqa: BLE001
            err = f"{type(ex).__name__}: {ex}"
        out.append(EntryInfo(d.name, d, name, label, list_versions(root, d.name), err))
    return out


def version_dir(root, entry_id: str, version: int | None = None) -> Path:
    """Folder of ``v<version>`` (default: newest)."""
    d = _entry_dir(root, entry_id)
    nums = _version_numbers(d)
    if version is None:
        if not nums:
            raise ProjectError(f"entry {entry_id!r} has no versions")
        version = nums[-1]
    if version not in nums:
        raise ProjectError(f"entry {entry_id!r} has no version {version}")
    return d / f"v{version}"


def load_version(root, entry_id: str, version: int | None = None) -> dict:
    return P.load_plan(version_dir(root, entry_id, version) / "plan.yaml")


# ----------------------------------------------------------------------------- inputs manifest
def write_manifest(inputs_dir, plan: dict) -> Path:
    """``inputs/manifest.json``: written last, so its presence means the inputs folder is complete for ``plan_hash``."""
    inputs_dir = Path(inputs_dir)
    from . import compact
    vd = inputs_dir.parent
    (inputs_dir / 'harvest.pyz').write_bytes(compact.portable_harvester())
    harvest_manifest = compact.manifest(plan, vd.parent.name, int(vd.name[1:])) if _VDIR.fullmatch(vd.name) else None
    if harvest_manifest is not None:
        _write_atomic(inputs_dir / 'harvest-manifest.json', json.dumps(harvest_manifest, indent=2))
    from .handoff import FIXED_FILES
    import hashlib
    allowed = FIXED_FILES | {j['stem'] + '.inp' for j in plan['jobs']}
    files = sorted(name for name in allowed if (inputs_dir / name).is_file() and not (inputs_dir / name).is_symlink())
    hashes = {name: hashlib.sha256((inputs_dir / name).read_bytes()).hexdigest() for name in files}
    man = dict(schema_version=SCHEMA, plan_hash=plan["plan_hash"], expected_stems=[j["stem"] for j in plan["jobs"]],
               files=files, sha256=hashes)
    path = inputs_dir / MANIFEST
    _write_atomic(path, json.dumps(man, indent=2) + "\n")
    return path


def write_version_inputs(root, entry_id: str, version: int | None = None, site: str | None = None) -> dict:
    """Write a version's ORCA inputs into its ``inputs/`` folder, each deck stamped with entry, version and short plan
    hash, then ``inputs/manifest.json`` last.  A previous manifest is removed first, so a half-rewritten folder never
    reads as "inputs written".  Refuses (``plan.NotApprovedError``) unless the version is approved and unmodified."""
    vd = version_dir(root, entry_id, version)
    inputs = vd / "inputs"
    from .site import SiteProfile
    site = SiteProfile.load(site)
    (inputs / MANIFEST).unlink(missing_ok=True)
    out = W.write_inputs(vd / "plan.yaml", inputs, site=site, stamp=dict(entry=entry_id, version=int(vd.name[1:])))
    write_manifest(inputs, P.load_plan(vd / "plan.yaml"))
    return out


# ----------------------------------------------------------------------------- status scanning (computed, never stored)
@dataclass
class JobStatus:
    """Classification of one expected job's output: complete, incomplete, missing, wrong-version or unverified."""
    stem: str
    kind: str
    state: str
    file: str | None = None      # version-relative path of the output that was classified
    reason: str = ""
    hint: str | None = None
    run: dict | None = None      # the job's own state record (started/finished), when one came back (D55)

    @property
    def run_label(self) -> str:
        """'failed', 'killed or running', 'finished' or '' from the job's state record."""
        if not self.run:
            return ""
        fin = self.run.get("finished")
        if fin:
            return "finished" if fin.get("rc") == "0" else "failed"
        return "killed or running" if self.run.get("started") else ""


@dataclass
class VersionStatus:
    entry_id: str
    version: int
    path: Path
    status: str | None           # one of STATUSES; None when the version folder is unreadable
    flags: list[str] = field(default_factory=list)   # modified, unreadable, partial, stale
    approved: bool = False       # sealed and unmodified
    approval: dict | None = None
    expected: list[str] = field(default_factory=list)
    jobs: list[JobStatus] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    has_results: bool = False
    error: str | None = None
    plan_hash: str | None = None  # hash of the plan as it is now (what an approver reviews); None when unreadable

    @property
    def found(self) -> int:
        return sum(j.state == "complete" for j in self.jobs)

    @property
    def approval_line(self) -> str | None:
        """"approved by NAME via CHANNEL" for an approved, unmodified version; None otherwise."""
        return approval_line(self.approval) if self.approved else None

    @property
    def label(self) -> str:
        if self.status is None:
            return "unreadable"
        return f"outputs found, {self.found} of {len(self.expected)}" if self.status == "outputs found" else self.status


@dataclass
class EntryStatus:
    entry_id: str
    path: Path
    display_name: str | None
    state_label: str | None
    versions: list[VersionStatus] = field(default_factory=list)
    error: str | None = None

    @property
    def newest(self) -> VersionStatus | None:
        return self.versions[-1] if self.versions else None

    @property
    def older_results(self) -> bool:
        """The newest version has no saved results while an older version does."""
        return bool(self.versions) and not self.newest.has_results and any(v.has_results for v in self.versions[:-1])


def _echoed_input(text: str) -> list[str] | None:
    """Lines of the input echoed in an ORCA output (``|  1> ...`` prefixes removed), or None when there is no echo."""
    L = text.split("\n")
    s = next((i for i, l in enumerate(L) if "INPUT FILE" in l), None)
    e = next((i for i, l in enumerate(L) if "END OF INPUT" in l), None)
    if s is None or e is None or e < s:
        return None
    return [m.group(1) for l in L[s:e] if (m := re.match(r"^\|\s*\d+>\s?(.*)$", l))]


def _expected_fingerprint(plan: dict, job: dict):
    if job["kind"] in ("opt", "freq"):
        return P.geometry_spec_from_plan(plan).fingerprint()
    return P.method_spec_from_plan(plan, "delta", job["functional"]).fingerprint()


def _run_state(vd: Path, stem: str) -> dict | None:
    """The job's state record: imported from a bundle (outputs/<stem>.state.json) or the file the Slurm script wrote,
    copied back next to the output (outputs/ or inputs/<stem>.state).  None when there is none or it cannot be read."""
    from . import harvest
    found = []
    for f, kind in ((vd / "outputs" / f"{stem}.state.json", "json"), (vd / "outputs" / f"{stem}.state", "text"),
                    (vd / "inputs" / f"{stem}.state", "text")):
        try:
            if not f.is_file():
                continue
            if kind == "json":
                rec = harvest.check_state(json.loads(f.read_text()))
            else:
                with open(f, errors="replace") as fh:
                    rec = harvest.parse_state(fh.read(harvest.STATE_MAX))
        except (OSError, ValueError):
            continue
        if rec:
            found.append(rec)
    # several copies (an adopted old run, a resubmitted new one): the run that started last wins (ISO times sort)
    return max(found, key=lambda r: (r.get("started") or {}).get("at", "")) if found else None


def _describe_run(run: dict) -> str:
    st, fin = run.get("started") or {}, run.get("finished")
    where = f"job {st.get('job', '?')} on {st.get('node', '?')}" if st else "job"
    if fin and fin.get("rc") != "0":
        return f"{where} failed (rc={fin.get('rc')}): {fin.get('reason', 'no reason recorded')}"
    if fin:
        return f"{where} finished normally at {fin.get('at', '?')}"
    return f"{where} started at {st.get('at', '?')} with no finish record: still running, or killed without notice (out of memory)"


def _classify(vd: Path, plan: dict, job: dict, entry_id: str, version: int) -> JobStatus:
    js = _classify_output(vd, plan, job, entry_id, version)
    js.run = _run_state(vd, job["stem"])
    if js.run and js.state in ("missing", "incomplete"):
        detail = _describe_run(js.run)
        js.reason = f"{js.reason}; {detail}" if js.reason else detail
    return js


def _output_failure(res, job: dict) -> str:
    if res.error_termination:
        return "ORCA reported error termination"
    if not res.normal_termination:
        return "ORCA did not terminate normally (still running, failed or truncated)"
    if not res.scf_converged:
        return "final SCF did not converge" if res.scf_converged is False else "final SCF convergence is not recorded"
    if job["kind"] == "opt" and res.geom_converged is not True:
        return "geometry optimization convergence is not confirmed"
    return ""


def _classify_output(vd: Path, plan: dict, job: dict, entry_id: str, version: int) -> JobStatus:
    stem = job["stem"]
    js = JobStatus(stem, job["kind"], "missing")
    out = vd / "outputs" / f"{stem}.out"
    if not out.is_file():
        packed = vd / 'outputs' / f'{stem}.compact.json'
        if packed.is_file():
            from . import compact
            js.file = f'outputs/{stem}.compact.json'
            try:
                res = compact.load_record(packed, plan, entry_id, version)
                failure = _output_failure(res, job)
                js.state = 'incomplete' if failure else 'complete'
                js.reason = (failure + '; ' if failure else '') + 'compact evidence; original raw output remains remote'
            except (OSError, ValueError, TypeError, KeyError) as ex:
                js.state, js.reason = 'unverified', f'invalid compact evidence: {ex}'
            return js
        if (vd / "inputs" / f"{stem}.out").is_file():
            js.hint = f"inputs/{stem}.out is not counted: move it to outputs/{stem}.out"
        return js
    js.file = f"outputs/{stem}.out"
    try:
        text = out.read_text(errors="replace")
        res = parse_orca_text(text, str(out))
    except Exception as e:  # noqa: BLE001 -- one unreadable output must not break the scan
        js.state, js.reason = "unverified", f"could not read output: {type(e).__name__}: {e}"
        return js
    echo = _echoed_input(text)
    stamps = [m for l in echo or [] if (m := W.STAMP_RE.match(l.strip()))]
    if stamps:
        e, v, h = stamps[0].groups()
        if (e, int(v), h) != (entry_id, version, plan["plan_hash"][:12]):
            js.state, js.reason = "wrong-version", f"stamped for entry={e} version={v} plan={h}"
            return js
    elif not echo:
        js.state, js.reason = "unverified", "no echoed input and no provenance stamp: cannot tell which plan produced it"
        return js
    else:
        bad = [f"{k}: expected {job[k]} vs output {got}" for k, got in (("charge", res.charge), ("multiplicity", res.multiplicity))
               if got is None or int(got) != int(job[k])]
        hard, soft = _expected_fingerprint(plan, job).compare(res.fingerprint)
        bad += [m.replace("calibration=", "expected=").replace("vs calculation=", "vs output=") for m in hard + soft]
        if bad:
            js.state, js.reason = "wrong-version", "; ".join(bad)
            return js
    js.reason = _output_failure(res, job)
    js.state = "incomplete" if js.reason else "complete"
    return js


def adopt_outputs(root, entry_id: str, version: int | None = None) -> dict:
    """Copy outputs the cluster left in ``inputs/`` (the Slurm script copies back to its submit dir) into ``outputs/``,
    with their job-state files.  Never overwrites: a different file already in outputs/ is reported and left alone.
    The copy is checked by hash; the originals stay where they are."""
    import shutil
    vd = version_dir(root, entry_id, version)
    plan = P.load_plan(vd / "plan.yaml")
    (vd / "outputs").mkdir(exist_ok=True)
    adopted, skipped = [], []
    for job in plan["jobs"]:
        for name in (f"{job['stem']}.out", f"{job['stem']}.state"):
            src, dst = vd / "inputs" / name, vd / "outputs" / name
            if not src.is_file():
                continue
            digest = hashlib.sha256(src.read_bytes()).hexdigest()
            if dst.exists():
                if hashlib.sha256(dst.read_bytes()).hexdigest() != digest:
                    skipped.append(dict(file=name, reason="outputs/ already has a different file (an earlier run?); not "
                                                    "overwritten: move that file aside, then adopt again"))
                continue
            shutil.copy2(src, dst)
            if hashlib.sha256(dst.read_bytes()).hexdigest() != digest:
                dst.unlink(missing_ok=True)
                raise ProjectError(f"copy of {name} did not verify; nothing kept")
            adopted.append(name)
    return dict(entry=entry_id, version=int(vd.name[1:]), adopted=adopted, skipped=skipped)


def inputs_written(vs: VersionStatus) -> bool:
    """True when this approved, unmodified version's decks are in inputs/ (manifest sealed with this plan)."""
    if not vs.approved or vs.status is None:
        return False
    man = _read_manifest(vs.path / "inputs")
    return bool(man) and man.get("plan_hash") == (vs.approval or {}).get("plan_hash")


def failed_jobs(vs: VersionStatus, include_unreturned: bool = False) -> list[str]:
    """Stems to run again: outputs without confirmed termination/convergence, or jobs whose state record says they failed.
    ``include_unreturned`` adds jobs with no output at all (never run, still running, or killed without notice)."""
    out = []
    for j in vs.jobs:
        if j.state == "complete":
            continue
        if j.state == "incomplete" or j.run_label == "failed" or (include_unreturned and j.state == "missing"):
            out.append(j.stem)
    return out


def write_resubmit(root, entry_id: str, version: int | None = None, *, stems: list[str] | None = None,
                   include_unreturned: bool = False, resources: dict | None = None, site: str | None = None) -> dict:
    """Write ``inputs/resubmit_failed.sh`` for the failed jobs (or ``stems``) and every job that depends on them, with
    optional per-stem ``resources`` ({stem: {"time": "HH:MM:SS", "mem_gb": N}}), recorded in ``inputs/resubmit.json``.
    Like submit.sh it is displayed, never run by the app.  Needs approved, written inputs."""
    vs = scan_version(root, entry_id, version)
    if not inputs_written(vs):
        raise ProjectError(f"{entry_id} v{vs.version}: inputs are not written for this approved plan (evidence imported "
                           "without decks?); write inputs first, then resubmit")
    plan = P.load_plan(vs.path / "plan.yaml")
    chosen = list(stems) if stems is not None else failed_jobs(vs, include_unreturned)
    unknown = sorted(set(chosen) - {j["stem"] for j in plan["jobs"]})
    if unknown:
        raise ProjectError(f"not jobs of this plan: {', '.join(unknown)}")
    if not chosen:
        raise ProjectError(f"{entry_id} v{vs.version}: no failed jobs to resubmit")
    for stem, r in (resources or {}).items():
        if stem not in {j["stem"] for j in plan["jobs"]}:
            raise ProjectError(f"resource override for {stem!r}, which is not a job of this plan")
        mem = r.get("mem_gb")
        if mem is not None and not (isinstance(mem, (int, float)) and math.isfinite(mem) and mem >= 1):
            raise ProjectError(f"memory for {stem} must be a number of GB, at least 1")
        if r.get("time") is not None and not re.fullmatch(r"(\d+-)?\d{1,3}:\d{2}(:\d{2})?", str(r["time"])):
            raise ProjectError(f"walltime for {stem} must look like HH:MM:SS (or D-HH:MM:SS)")
    res = W.resubmit_script(plan, vs.path / "inputs", chosen, site=site, resources=resources or {})
    _write_atomic(vs.path / "inputs" / "resubmit.json", json.dumps(dict(
        created_at=P._now(), requested=chosen, stems=res["stems"], resources=resources or {}, site=res['site'], site_profile=res['site_profile']), indent=2) + "\n")
    return dict(res, entry=entry_id, version=vs.version)


def _read_manifest(inputs_dir: Path) -> dict | None:
    try:
        m = json.loads((inputs_dir / MANIFEST).read_text())
        return m if isinstance(m, dict) else None
    except (OSError, ValueError):
        return None


def _seal(plan: dict) -> tuple[dict | None, str | None, bool]:
    """(approval record, sealed hash, intact): the seal of an approved plan and whether its content still matches it."""
    appr = plan.get("approval") if plan.get("status") == "approved" else None
    seal = appr.get("plan_hash") if isinstance(appr, dict) else None
    return appr, seal, bool(seal) and seal == P.compute_hash(plan)


def _scan(vi: VersionInfo) -> VersionStatus:
    vd = vi.path
    vs = VersionStatus(vi.entry_id, vi.version, vd, None, has_results=(vd / "results" / RESULTS_FILE).is_file())
    if vi.unreadable:
        vs.flags, vs.error = ["unreadable"], vi.error
        return vs
    plan = vi.plan
    try:
        vs.plan_hash = P.compute_hash(plan)
        vs.approval, seal, vs.approved = _seal(plan)
        if seal and not vs.approved:
            vs.flags.append("modified")
        man = _read_manifest(vd / "inputs")
        inputs_written = bool(seal) and man is not None and man.get("plan_hash") == seal
        jobs = plan.get("jobs") or []
        vs.expected = [j["stem"] for j in jobs]
        expected = set(vs.expected)
        vs.jobs = [_classify(vd, plan, j, vi.entry_id, vi.version) for j in jobs]
        vs.hints = [j.hint for j in vs.jobs if j.hint]
        od = vd / "outputs"
        vs.unmatched = sorted(p.name for p in od.glob("*.out") if p.is_file() and not p.name.startswith("slurm-")
                              and p.stem not in expected) if od.is_dir() else []
    except Exception as e:  # noqa: BLE001 -- a malformed plan flags this version only
        return VersionStatus(vi.entry_id, vi.version, vd, None, ["unreadable"], has_results=vs.has_results, error=f"{type(e).__name__}: {e}")
    analyzed = False
    if vs.has_results:
        try:  # a bad results file flags the results only: the plan stays readable and Analyze can replace the file
            vs.flags += _result_flags(_read_result(vd), _used_outputs(vd, vs.jobs))
            analyzed = True
        except Exception as e:  # noqa: BLE001
            vs.flags.append("results unreadable")
            vs.error = f"{RESULTS_FILE}: {type(e).__name__}: {e}"
    vs.status = ("analyzed" if analyzed else "outputs found" if any(j.file for j in vs.jobs)
                 else "inputs written" if inputs_written else "approved" if vs.approved else "draft")
    return vs


def scan_version(root, entry_id: str, version: int | None = None) -> VersionStatus:
    """Status of one version (default: newest), computed from its folder alone.  Nothing is written."""
    vd = version_dir(root, entry_id, version)
    return _scan(_version_info(vd.parent, entry_id, int(vd.name[1:])))


def scan_project(root) -> list[EntryStatus]:
    """One row per entry (sorted by id) with every version's status; the overview's data."""
    return [EntryStatus(e.entry_id, e.path, e.display_name, e.state_label, [_scan(v) for v in e.versions], e.error)
            for e in list_entries(root)]


# ----------------------------------------------------------------------------- saved analysis
def _file_record(path: Path) -> dict:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return dict(name=path.name, size=path.stat().st_size, sha256=h.hexdigest())


def _used_outputs(vd: Path, jobs: list[JobStatus]) -> list[dict]:
    """Name, size and SHA-256 of every complete property output: what an analysis uses now."""
    return sorted((_file_record(vd / j.file) for j in jobs if j.state == "complete" and j.kind in PROPERTY_KINDS),
                  key=lambda o: o["name"])


def _jsonable(v):
    """Numpy scalars to Python values and NaN/inf to None, recursively, so the file is strict JSON."""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _read_result(vd: Path) -> dict:
    d = json.loads((vd / "results" / RESULTS_FILE).read_text())
    if not isinstance(d, dict):
        raise ProjectError(f"{RESULTS_FILE} is not a mapping")
    return d


def _result_flags(saved: dict, current: list[dict]) -> list[str]:
    """*Partial* when the result covers fewer than all expected property outputs; *stale* when the outputs it
    used differ by name or SHA-256 from the complete property outputs now present (never by modification time)."""
    flags = []
    used = saved.get("outputs") or []
    if int(saved.get("complete_property", len(used))) < int(saved.get("expected_property", 0)):
        flags.append("partial")
    if {(o.get("name"), o.get("sha256")) for o in used} != {(o["name"], o["sha256"]) for o in current}:
        flags.append("stale")
    return flags


def analyze_version(root, entry_id: str, version: int | None = None, calibration=None, level: float = 0.95,
                    q_barn: float = 0.16) -> dict:
    """Analyze a version (default: newest) from its complete property outputs and save ``results/analysis.json``
    atomically.  Wrong-version, unverified and incomplete outputs are excluded and named in the notes.
    Refuses (``ProjectError``) until at least one property output is complete.  Returns the saved result."""
    vd, result = _build_analysis(root, entry_id, version, calibration, level, q_barn)
    (vd / "results").mkdir(exist_ok=True)
    _write_atomic(vd / "results" / RESULTS_FILE, json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def trial_analysis(root, entry_id: str, version: int | None = None, calibration=None, level: float = 0.95,
                   q_barn: float = 0.16) -> dict:
    """The same analysis with a chosen calibration, returned and never written: marked ``trial`` and carrying its own
    calibration status, so it cannot replace the saved analysis or reach a comparison or report."""
    _, result = _build_analysis(root, entry_id, version, calibration, level, q_barn)
    result["trial"] = True
    for row in result["rows"]:
        row.update(trial=True, delta_status=result["delta_status"]["status"])
    result["notes"].insert(0, "TRIAL analysis: not saved; not used in comparisons or reports.")
    return result


def _build_analysis(root, entry_id: str, version, calibration, level: float, q_barn: float) -> tuple[Path, dict]:
    vs = scan_version(root, entry_id, version)
    if vs.status is None:
        raise ProjectError(f"{entry_id} v{vs.version} is unreadable: {vs.error}")
    vd = vs.path
    plan = P.load_plan(vd / "plan.yaml")
    props = [j for j in vs.jobs if j.kind in PROPERTY_KINDS]
    done = [j for j in props if j.state == "complete"]
    if not done:
        raise ProjectError(f"{entry_id} v{vs.version}: no complete property output in outputs/ yet; nothing to analyze")
    excluded = [f"{j.file}: excluded - {j.state}: {j.reason}" for j in vs.jobs if j.file and j.state != "complete"]
    cal, label = W.resolve_calibration(plan, calibration)
    from . import study
    from . import references as R
    calibration_entry = R.get_calibration_entry(label)
    if calibration and Path(str(calibration)).is_file():
        calibration_entry = _read_yaml(Path(str(calibration)))
        if calibration_entry.get('source') == 'project_fit':
            expected = P.method_spec_from_plan(plan, 'delta').fingerprint()
            hard, soft = R.entry_fingerprint(calibration_entry).compare(expected)
            if hard or any(x.startswith('solvent:') for x in soft) or R._norm_geom(calibration_entry.get('geometry_label')) != R._norm_geom(P.geometry_label(plan)):
                raise ProjectError('project calibration does not match the target method, solvent or geometry protocol')
    provenance = study.snapshot(plan, cal, calibration_entry)
    delta_status = study.calibration_status(plan, cal, calibration_entry,
                                            from_file=bool(calibration) and Path(str(calibration)).is_file())
    for protocol in provenance['protocols'].values():
        protocol['q_barn'] = q_barn
    r = W.analyze(plan, [vd / j.file for j in done], calibration=calibration, level=level, q_barn=q_barn)
    result = _jsonable(dict(
        schema_version=SCHEMA, analyzed_at=P._now(), entry=entry_id, version=vs.version, plan_hash=plan["plan_hash"],
        provenance=provenance,
        calibration=r["calibration"],
        calibration_constants=dict(id=label, alpha=float(cal.alpha), C=float(cal.C), beta=float(cal.beta)) if cal else None,
        level=level, q_barn=q_barn, delta_status=delta_status, rows=r["rows"], notes=excluded + list(r["notes"]),
        outputs=_used_outputs(vd, done), expected_property=len(props), complete_property=len(done)))
    result['evidence_sources'] = [dict(file=j.file, kind='compact' if j.file.endswith('.compact.json') else 'raw') for j in done]
    for evidence in result['evidence_sources']:
        if evidence['kind'] == 'compact':
            document = json.loads((vd / evidence['file']).read_text())
            evidence['parser_version'] = document['parser_version']
            evidence['remote_source'] = next(iter(document['records'].values()))['source']
    if any(j.file.endswith('.compact.json') for j in done):
        result['notes'].append('Compact evidence was used; source hashes identify remote raw files, whose bytes are not verified locally.')
    return vd, result


def load_results(root, entry_id: str, version: int | None = None) -> dict | None:
    """The saved analysis of a version (default: newest), or None when it has none.  Nothing is re-run: rows, notes and
    the calibration constants come from the file, so it reads the same on any machine."""
    vd = version_dir(root, entry_id, version)
    return _read_result(vd) if (vd / "results" / RESULTS_FILE).is_file() else None


# ----------------------------------------------------------------------------- status --json document
STATUS_SCHEMA = "1"


def rel_path(root, path) -> str:
    """Project-relative POSIX path: valid after the project is moved, synced or shared."""
    return Path(path).relative_to(Path(root)).as_posix()


def _version_doc(root, vs) -> dict:
    by = {s: [j.stem for j in vs.jobs if j.state == s] for s in JOB_STATES}
    inputs, outputs = vs.path / "inputs", vs.path / "outputs"
    res = vs.path / "results" / RESULTS_FILE
    return dict(
        version=vs.version, path=rel_path(root, vs.path), status=vs.status, label=vs.label, flags=list(vs.flags),
        approved=vs.approved, plan_hash=vs.plan_hash, approval_line=vs.approval_line, approval=vs.approval,
        inputs_dir=rel_path(root, inputs), outputs_dir=rel_path(root, outputs),
        submit_script=rel_path(root, inputs / W.SUBMIT_SCRIPT) if (inputs / W.SUBMIT_SCRIPT).is_file() else None,
        results=rel_path(root, res) if res.is_file() else None,
        found=vs.found, stems=dict(expected=list(vs.expected), complete=by["complete"], incomplete=by["incomplete"],
                                   missing=by["missing"], wrong_version=by["wrong-version"], unverified=by["unverified"],
                                   unmatched=[Path(f).stem for f in vs.unmatched]),
        unmatched_files=[rel_path(root, outputs / f) for f in vs.unmatched],
        jobs=[dict(stem=j.stem, kind=j.kind, state=j.state, file=rel_path(root, vs.path / j.file) if j.file else None,
                   reason=j.reason or None, hint=j.hint) for j in vs.jobs],
        hints=list(vs.hints), error=vs.error)


def status_document(root) -> dict:
    """The ``project status --json`` document (schema_version "1"): every entry and version with its computed status,
    approval, project-relative paths and per-state job stem lists.  What an agent reads to decide what to submit and
    where outputs go."""
    meta = open_project(root)
    ents = []
    for e in scan_project(root):
        ents.append(dict(id=e.entry_id, name=e.display_name, state=e.state_label, path=rel_path(root, e.path), error=e.error,
                         newest_version=e.newest.version if e.newest else None, older_results=e.older_results,
                         versions=[_version_doc(root, v) for v in e.versions]))
    return dict(schema_version=STATUS_SCHEMA, project=dict(name=meta.get("name"), root=str(Path(root).resolve())), entries=ents)


# ----------------------------------------------------------------------------- batch approval
def approval_line(approval: dict | None) -> str | None:
    """Who approved and through which channel: the person an agent acted for, else the approver.
    Seals made before channels were recorded have no ``via`` and read "approved by NAME"."""
    if not isinstance(approval, dict):
        return None
    line = f"approved by {approval.get('on_behalf_of') or approval.get('by')}"
    return line + (f" via {approval['via']}" if approval.get("via") else "")


@dataclass
class ApprovalOutcome:
    entry_id: str
    version: int | None
    outcome: str                 # sealed | already-approved | skipped
    reason: str | None = None    # why it was skipped: open items, modified, unreadable
    approval: dict | None = None


def _save_plan_atomic(plan: dict, vd: Path):
    """Re-save ``vd/plan.yaml`` via a temporary folder and a rename; the structure is unchanged by approval."""
    tmp = vd / f".tmp-approve-{uuid.uuid4().hex[:8]}"
    try:
        P.save_plan(plan, tmp / "plan.yaml")
        os.replace(tmp / "plan.yaml", vd / "plan.yaml")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def approval_blocker(vi: VersionInfo) -> ApprovalOutcome | None:
    """Why a version cannot be sealed now, or None when it is eligible: unreadable, modified after approval or open items
    are *skipped* with the reason; an approved, unmodified version is *already-approved*.  Nothing is written."""
    if vi.unreadable:
        return ApprovalOutcome(vi.entry_id, vi.version, "skipped", f"unreadable: {vi.error}")
    appr, seal, intact = _seal(vi.plan)
    if seal:
        if intact:
            return ApprovalOutcome(vi.entry_id, vi.version, "already-approved", approval=appr)
        return ApprovalOutcome(vi.entry_id, vi.version, "skipped", "modified after approval (hash mismatch)", appr)
    try:
        P.approve_plan(vi.plan, "-")  # dry run on a copy: the open-items refusal and its message stay in plan.py
    except P.PlanError as e:
        return ApprovalOutcome(vi.entry_id, vi.version, "skipped", str(e))
    return None


def _approve_one(root, item, approver, via, on_behalf_of, expected_hash=None) -> ApprovalOutcome:
    eid, n = (item.entry_id, item.version) if isinstance(item, VersionRef) else (str(item), None)
    try:
        vd = version_dir(root, eid, n)
    except ProjectError as e:
        return ApprovalOutcome(eid, n, "skipped", f"unreadable: {e}")
    vi = _version_info(vd.parent, eid, int(vd.name[1:]))
    if expected_hash:  # the caller reviewed a specific version and plan content: seal exactly that or nothing
        newest = list_versions(root, eid)[-1].version
        if newest != vi.version:
            return ApprovalOutcome(eid, vi.version, "skipped", f"a newer version v{newest} exists since review; refresh and review it")
        if vi.unreadable or not P.compute_hash(vi.plan).startswith(expected_hash):
            return ApprovalOutcome(eid, vi.version, "skipped", "plan changed since review (hash mismatch); refresh and review it")
    blocked = approval_blocker(vi)
    if blocked:
        return blocked
    sealed = P.approve_plan(vi.plan, approver, via, on_behalf_of)  # open items were refused by approval_blocker
    _save_plan_atomic(sealed, vd)
    return ApprovalOutcome(eid, vi.version, "sealed", approval=sealed["approval"])


def approve_versions(root, items, approver: str | None, via: str | None, on_behalf_of: str | None = None,
                     expected_hashes: dict[str, str] | None = None) -> list[ApprovalOutcome]:
    """Approve several versions in one action.  ``items`` are ``VersionRef``s or entry ids (newest version).
    ``expected_hashes`` maps an entry id to the plan hash (or a prefix of it) the approver reviewed: that item is sealed
    only if the version is still the entry's newest and its plan still has that hash, else skipped.  Each
    plan is sealed individually with its own hash, approver and time; one outcome per item, in order.  An approved,
    unmodified version is reported as already-approved and its seal is never rewritten; open items, a seal broken by
    later edits, or an unreadable plan are skipped with the reason.  Other versions are not touched."""
    open_project(root)
    exp = expected_hashes or {}
    return [_approve_one(root, it, approver, via, on_behalf_of,
                         exp.get(it.entry_id if isinstance(it, VersionRef) else str(it))) for it in items]


# ----------------------------------------------------------------------------- legacy import
_README_HASH = re.compile(r"^Inputs for plan ([0-9a-f]{12})\b")


def _legacy_inputs_match(inputs_dir: Path, plan: dict) -> bool:
    try:
        P.require_approved(plan)
        first = (inputs_dir / "README_inputs.txt").read_text(encoding="utf-8").splitlines()[0]
    except (P.NotApprovedError, OSError, IndexError):
        return False
    m = _README_HASH.match(first)
    return bool(m) and m.group(1) == plan["plan_hash"][:12]


def import_legacy(root) -> VersionRef:
    """Turn a pre-project work directory (``plan/plan.yaml``, ``inputs/``, ``uploaded_outputs/``) into a one-entry project
    in the same folder.  Everything is copied (originals stay); the plan and structure are copied byte-for-byte, so
    ``inputs.name`` and the seal are unchanged.  ``inputs/manifest.json`` is written only for an approved plan whose
    ``README_inputs.txt`` names the sealed hash."""
    root = Path(root)
    if not is_legacy_workdir(root):
        raise ProjectError(f"{root} is not a single-plan work directory (needs plan/plan.yaml and no {PROJECT_FILE})")
    src_plan = root / "plan" / "plan.yaml"
    plan = P.load_plan(src_plan)
    name = plan["inputs"]["name"]
    eid = P._safe(name)
    ed = _entries(root) / eid
    if ed.is_dir() and all(c.name.startswith(".tmp-") for c in ed.iterdir()):
        shutil.rmtree(ed)  # left by an import that was killed mid-copy: nothing but temporary folders
    if ed.exists():
        raise ProjectError(f"{ed} already exists")

    def populate(v: Path):
        shutil.copy2(src_plan, v / "plan.yaml")
        if (root / "plan" / "structure.xyz").is_file():
            shutil.copy2(root / "plan" / "structure.xyz", v / "structure.xyz")
        if (root / "inputs").is_dir():
            shutil.copytree(root / "inputs", v / "inputs")
            if _legacy_inputs_match(v / "inputs", plan):
                write_manifest(v / "inputs", plan)
        if (root / "uploaded_outputs").is_dir():
            shutil.copytree(root / "uploaded_outputs", v / "outputs")

    ref = _new_entry(root, name, eid, plan, populate)
    _write_project_files(root, None)  # project.yaml last: an interrupted import still opens as a legacy work dir
    return ref


# ----------------------------------------------------------------------------- recent projects (per-user app state)
def _recents_path() -> Path:
    return _data.user_home() / RECENTS_FILE


def _read_recents() -> list[dict]:
    try:
        d = json.loads(_recents_path().read_text())
        return [p for p in d.get("projects", []) if isinstance(p, dict) and p.get("path")]
    except (OSError, ValueError, AttributeError):
        return []


def _write_recents(items: list[dict]) -> None:
    f = _recents_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(f, json.dumps(dict(schema_version=SCHEMA, projects=[dict(path=p["path"], opened=p.get("opened")) for p in items]),
                                indent=2) + "\n")


def add_recent(root) -> None:
    """Record ``root`` (absolute path) as the most recently opened project.  Missing projects are never pruned."""
    path = str(Path(root).resolve())
    _write_recents([dict(path=path, opened=P._now())] + [p for p in _read_recents() if p["path"] != path])


def remove_recent(root) -> None:
    """Drop ``root`` from the recent-projects list (an explicit user action; nothing is pruned automatically)."""
    path = str(Path(root).resolve())
    _write_recents([p for p in _read_recents() if p["path"] != path])


def recent_projects() -> list[dict]:
    """Recent projects, newest first: ``path``, ``opened`` and ``exists`` (False when the folder or its marker is gone)."""
    return [dict(path=p["path"], opened=p.get("opened"), exists=is_project(p["path"])) for p in _read_recents()]
