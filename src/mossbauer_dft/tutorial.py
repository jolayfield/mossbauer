"""Bundled teaching example and the next-step guide.

The example is an ordinary project of draft entries whose structures, states and experimental values come from a
bundled reference set; nothing is approved, written or computed for the learner.  The guide is derived from the
project's status scan alone and never writes.
"""
from __future__ import annotations

from pathlib import Path
import shlex

from . import project as J
from . import references as R
from .structure import read_structure

EXAMPLES = {
    "iron-basics": dict(
        title="Iron basics: oxidation and spin state in the isomer shift",
        refset="fe1_calib18",
        compounds=["Cp2Fe", "FeCl4_2m", "FeCl4_m"],
        lesson=(
            "Three iron compounds with literature Mössbauer data. [FeCl4]2- and [FeCl4]- share a ligand set but differ in "
            "oxidation state (high-spin Fe(II) vs Fe(III)): the isomer shift falls as the oxidation state rises. Ferrocene is "
            "low-spin Fe(II) and shows how covalency lowers delta further; B3LYP is known to over-predict its delta by about "
            "+0.15 mm/s, a reminder that a calibration has outliers."),
    ),
}

PAGE_LABELS = {"Project": "Project", "New prediction": "1  Structure", "Batch intake": "Batch intake",
               "Plan review": "2  Review & approve", "Inputs": "3  ORCA inputs", "Results": "4  Results",
               "Study": "Study comparison", "Calibration": "Calibration workbench", "States": "State families",
               "References": "Reference data", "Cluster setup": "Cluster setup"}
STEP_PAGES = {"add": ["New prediction", "Batch intake"], "approve": ["Plan review"], "inputs": ["Inputs"],
              "run": ["Results"], "analyze": ["Results"], "compare": ["Study"]}

STEPS = [
    ("add", "Add entries", "Project / 1  Structure / Batch intake", "mossbauer project add --project DIR --structure FILE --charge Q --multiplicity M",
     "Each entry is one compound in one electronic state. The app reads the structure, checks the electron count against the "
     "multiplicity and picks a method from its rules."),
    ("approve", "Review and approve plans", "2  Review & approve", "mossbauer project approve --project DIR --entry ID --by NAME",
     "A person reads each plan (functionals, basis sets, geometry step, calibration route and any open items) and seals it with "
     "their name. Nothing is written for the cluster until a plan is approved, and editing a sealed plan makes a new version."),
    ("inputs", "Write ORCA inputs", "3  ORCA inputs", "mossbauer project setup --project DIR --entry ID",
     "The approved plan becomes ORCA decks, a Slurm script and submit.sh in the version's inputs/ folder, stamped with the plan's "
     "hash so outputs can be matched back to it."),
    ("run", "Run ORCA and return the outputs", "4  Results", "mossbauer project status --json --project DIR",
     "This app does not submit jobs. Run submit.sh from inputs/ on your cluster (or ask your agent to), "
     "then fetch a compact bundle over SSH on Results, or put each finished <stem>.out in that version's outputs/ folder. "
     "The app checks that every output finished and was "
     "made from this version's decks."),
    ("analyze", "Analyze", "4  Results", "mossbauer project analyze --project DIR --entry ID",
     "rho(0) and the EFG are read from the outputs, the plan's calibration turns rho(0) into delta, and the result is saved with "
     "its calibration status (calibrated, project fit, exploratory or uncalibrated)."),
    ("compare", "Compare and report", "Study", "mossbauer project compare --project DIR",
     "The study view sets predictions beside experiment, with agreement statistics kept separate per protocol, calibration and "
     "status. A portable report bundles the table, the methods and the provenance."),
]
_STEP_OF = {"draft": "approve", "approved": "inputs", "inputs written": "run", "outputs found": "analyze", "analyzed": "compare"}


class ExampleError(J.ProjectError):
    pass


def list_examples() -> list[dict]:
    return [dict(id=k, title=v["title"], compounds=list(v["compounds"])) for k, v in EXAMPLES.items()]


_ROMAN = {0: "0", 1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI"}


def _oxidation(text) -> int | None:
    try:
        return int(str(text).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _compounds(example: dict) -> tuple[dict, list[dict]]:
    d = R.refset_definition(example["refset"])
    by_id = {c["id"]: c for c in d["compounds"]}
    return d, [by_id[c] for c in example["compounds"]]


def _notes(example_id: str, example: dict, d: dict, compounds: list[dict], refs: list, pack: dict | None = None) -> str:
    run = ("No calculations or outputs are included; running ORCA is part of the exercise." if pack is None else
           f"This copy comes with a recorded real run by {pack.get('recorded_by')} on {str(pack.get('recorded_at'))[:10]}"
           + (f" ({pack['site']})" if pack.get("site") else "") + (f", {pack['program']}" if pack.get("program") else "")
           + ". The draft plans are exactly the plans that were run. After you approve a plan without changing it, Results "
           "offers to import its recorded evidence (checked like any cluster return). Change a plan and it needs its own run.")
    lines = [f"# {example['title']}", "", example["lesson"], "",
             f"Created by `mossbauer project example --example {example_id}`. Structures, charges, multiplicities and "
             f"experimental values come from the bundled reference set `{d['id']}` ({d.get('title', '')}). The entries are "
             f"drafts: review and approve each plan yourself. {run}", "", "## Compounds", ""]
    caveats = d.get("caveats") or {}
    for c, ref in zip(compounds, refs):
        exp = f"delta {c['delta_exp_mm_s']} mm/s at {c.get('delta_T_K')} K"
        if c.get("deq_abs_exp_mm_s") is not None:
            exp += f", |dEQ| {c['deq_abs_exp_mm_s']} mm/s"
        lines.append(f"- **{c['name']}** (`{ref.entry_id}`): {c.get('spin_state', '')} Fe({_ROMAN.get(_oxidation(c.get('formal_ox_state')), '?')}), "
                     f"charge {c['charge']}, multiplicity {c['multiplicity']}; experiment: {exp}; source DOI "
                     f"{c.get('exp_source_doi') or d.get('doi')}.")
        for f in c.get("flags") or []:
            if f in caveats:
                lines.append(f"  - Caveat ({f}): {caveats[f]}")
        if c.get("notes"):
            lines.append(f"  - Note: {c['notes']}")
    return "\n".join(lines) + "\n\n" + "## Next step\n\nRun `mossbauer project guide --project .` or open the Project page.\n"


def create_example(root, example_id: str = "iron-basics", recorded_pack=None) -> list[J.VersionRef]:
    """Create the bundled example as a new project in ``root`` (new or empty folder): draft entries only.
    ``recorded_pack`` (a pack file from ``recorded.export_pack``) makes the drafts the exact plans that were run and keeps
    the recorded evidence for import after each plan is approved unchanged (D61)."""
    if example_id not in EXAMPLES:
        raise ExampleError(f"no example {example_id!r} (available: {', '.join(EXAMPLES)})")
    pack = None
    if recorded_pack is not None:
        from . import recorded
        pack = recorded.read_pack(recorded_pack)
        if pack["meta"].get("example") != example_id:
            raise ExampleError(f"the pack records example {pack['meta'].get('example')!r}, not {example_id!r}")
    root = Path(root)
    if J.is_project(root):
        raise ExampleError(f"{root} is already a project; create the example in a new or empty folder")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ExampleError(f"{root} is not empty; create the example in a new or empty folder")
    ex = EXAMPLES[example_id]
    d, compounds = _compounds(ex)
    paths = [R._resolve_structure_path(d, c) for c in compounds]
    missing = [c["id"] for c, p in zip(compounds, paths) if p is None]
    if missing:
        raise ExampleError(f"bundled structures missing for {', '.join(missing)}")
    structures = [read_structure(p) for p in paths]
    if pack is not None:
        by_compound = {c["id"]: next((e for e in pack["plans"] if e.startswith(c["id"] + "_")), None) for c in compounds}
        if None in by_compound.values() or len(pack["plans"]) != len(compounds):
            raise ExampleError("the pack's entries are not this example's compounds")
        from .structure import Structure
        for c, s in zip(compounds, structures):
            plan = pack["plans"][by_compound[c["id"]]]
            got = Structure.from_xyz_text(plan["_structure_xyz"])
            if (plan["inputs"]["charge"], plan["inputs"]["multiplicity"]) != (int(c["charge"]), int(c["multiplicity"])) \
                    or sorted(got.symbols) != sorted(s.symbols):
                raise ExampleError(f"the pack's plan for {c['id']} is not this example's compound (charge, multiplicity or atoms differ)")
    J.create_project(root, name=ex["title"])
    J.set_example(root, example_id)
    if pack is not None:
        from . import recorded
        refs = recorded.install(root, pack)
        order = [by_compound[c["id"]] for c in compounds]
        refs = sorted(refs, key=lambda r: order.index(r.entry_id))
        J._write_atomic(root / "EXAMPLE.md", _notes(example_id, ex, d, compounds, refs, pack["meta"]))
        return refs
    refs = []
    for c, s, p in zip(compounds, structures, paths):
        exp = {k: v for k, v in (("delta", c.get("delta_exp_mm_s")), ("deq", c.get("deq_abs_exp_mm_s")),
                                 ("T_K", c.get("delta_T_K"))) if v is not None}
        refs.append(J.add_entry(root, s, int(c["charge"]), int(c["multiplicity"]), name=c["id"],
                                oxidation_state=_oxidation(c.get("formal_ox_state")), temperature_K=c.get("delta_T_K"),
                                experimental=exp, structure_filename=p.name))
    J._write_atomic(root / "EXAMPLE.md", _notes(example_id, ex, d, compounds, refs))
    return refs


def guide(root, entries: list | None = None) -> dict:
    """The workflow steps with done / next / later and the entries at each step.  Reads the status scan only
    (``entries``: a ``project.scan_project`` result the caller already has)."""
    meta = J.open_project(root)
    at = {k: [] for k, *_ in STEPS}
    unreadable = []
    for e in J.scan_project(root) if entries is None else entries:
        vs = e.newest
        if vs is None or vs.status is None:
            unreadable.append(e.entry_id)
        else:
            step = _STEP_OF[vs.status]
            if not vs.approved:
                step = "approve"
            elif vs.status == "outputs found" and vs.found != len(vs.expected):
                step = "run"
            elif vs.status == "analyzed" and (vs.flags or vs.found != len(vs.expected)):
                step = "analyze" if vs.found == len(vs.expected) else "run"
            at[step].append(e.entry_id)
    order = [k for k, *_ in STEPS]
    if not any(at.values()) and not unreadable:
        first = "add"
    else:
        first = next((k for k in order[1:] if at[k]), None)
    steps = []
    for k, title, page, cli, why in STEPS:
        i, f = order.index(k), order.index(first) if first else -1
        status = "done" if 0 <= i < f else "next" if i == f else "later"
        if k == "run" and (Path(root) / "recorded" / "pack.json").is_file():
            why += (" This example also has a recorded real run: once a plan is approved unchanged, Results offers to "
                    "import its recorded evidence instead (labelled as recorded, checked like any return).")
        pages = STEP_PAGES[k]
        steps.append(dict(id=k, title=title, status=status, entries=at[k], n=len(at[k]),
                          page=" / ".join(PAGE_LABELS[p] for p in pages), page_keys=pages,
                          cli=cli.replace("DIR", shlex.quote(str(Path(root)))), why=why))
    notes = [f"{len(unreadable)} entr{'y' if len(unreadable) == 1 else 'ies'} could not be read ({', '.join(unreadable)}); "
             "see Project for the error"] if unreadable else []
    return dict(project=meta.get("name"), example=meta.get("example"), next=first, steps=steps, notes=notes)
