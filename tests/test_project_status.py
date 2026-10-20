"""Status scanning, output classification and the provenance stamp (U2: R6-R9, R15, R16; KTD3, KTD4, KTD8)."""
import json
import re

import pytest
import yaml

from mossbauer_dft import plan as P
from mossbauer_dft import project as J
from mossbauer_dft import workflow as W
from mossbauer_dft.orca_input import build_input
from mossbauer_dft.structure import Structure
from orca_synthetic import property_output
from test_plan import XYZ, _home  # noqa: F401

TEMPLATE = property_output()


@pytest.fixture
def fe1():
    return Structure.from_xyz_text(XYZ)


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "study"
    J.create_project(root, name="Fe study")
    return root


def _add(root, s, freq=False, mult=4, name="cmpd"):
    ov = {"geometry": {"freq": True}} if freq else None
    return J.add_entry(root, s, -1, mult, name=name, oxidation_state=1, overrides=ov)


def _approve(root, ref):
    P.save_plan(P.approve_plan(J.load_version(root, ref.entry_id, ref.version), "tester"), ref.path / "plan.yaml")


def _ready(root, s, **kw):
    """Approved entry with inputs written through the project."""
    ref = _add(root, s, **kw)
    _approve(root, ref)
    J.write_version_inputs(root, ref.entry_id, ref.version)
    return ref


def _jobs(root, ref):
    return {j["stem"]: j for j in J.load_version(root, ref.entry_id, ref.version)["jobs"]}


def _fake_out(deck, charge, mult, *, terminated=True, echo=True):
    """An ORCA output built from the synthetic template, echoing ``deck`` as its input and reporting ``charge``/``mult``."""
    L = TEMPLATE.split("\n")
    s = next(i for i, l in enumerate(L) if "INPUT FILE" in l)
    e = next(i for i, l in enumerate(L) if "END OF INPUT" in l)
    lines = deck.rstrip("\n").split("\n")
    echoed = [L[s], L[s + 1], "NAME = job.inp"] + [f"|{i + 1:3d}> {x}" for i, x in enumerate(lines)] + \
             [f"|{len(lines) + 1:3d}>                          ****END OF INPUT****"]
    text = "\n".join(L[:s] + (echoed if echo else []) + L[e + 1:])
    text = re.sub(r"(Total Charge\s+Charge\s+\.\.\.\.\s+)-?\d+", lambda m: m.group(1) + str(charge), text)
    text = re.sub(r"(Multiplicity\s+Mult\s+\.\.\.\.\s+)\d+", lambda m: m.group(1) + str(mult), text)
    if re.search(r"^!.*\bOpt\b", deck, re.M):
        text += "\nGEOMETRY OPTIMIZATION CYCLE 1\nTHE OPTIMIZATION HAS CONVERGED\n"
    if not terminated:
        text = text[:text.index("****ORCA TERMINATED NORMALLY")]
    return text


def _place(root, ref, stem, *, deck=None, where="outputs", charge=None, mult=None, **kw):
    job = _jobs(root, ref)[stem]
    deck = deck if deck is not None else (ref.path / "inputs" / f"{stem}.inp").read_text()
    out = ref.path / where / f"{stem}.out"
    out.write_text(_fake_out(deck, job["charge"] if charge is None else charge, job["multiplicity"] if mult is None else mult, **kw))
    return out


def _unstamped(ref, stem):
    lines = (ref.path / "inputs" / f"{stem}.inp").read_text().split("\n")
    assert lines[0].startswith("# mossbauer-dft")
    return "\n".join(lines[1:])


def _by_stem(vs):
    return {j.stem: j for j in vs.jobs}


def _tree(d):
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


# ----------------------------------------------------------------------------- status ladder
def test_new_draft_reports_draft_with_zero_found(proj, fe1):
    ref = _add(proj, fe1)
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.status == "draft" and vs.flags == [] and not vs.approved
    assert vs.found == 0 and len(vs.expected) == 3 and {j.state for j in vs.jobs} == {"missing"}


def test_approved_with_matching_manifest_reports_inputs_written(proj, fe1):
    ref = _add(proj, fe1)
    _approve(proj, ref)
    assert J.scan_version(proj, ref.entry_id).status == "approved"
    J.write_version_inputs(proj, ref.entry_id, 1)
    man = json.loads((ref.path / "inputs" / "manifest.json").read_text())
    plan = J.load_version(proj, ref.entry_id)
    assert man["plan_hash"] == plan["approval"]["plan_hash"] and man["expected_stems"] == [j["stem"] for j in plan["jobs"]]
    assert {f"{s}.inp" for s in man["expected_stems"]} | {"submit.sh"} <= set(man["files"])
    for s in man["expected_stems"]:
        first = (ref.path / "inputs" / f"{s}.inp").read_text().split("\n")[0]
        assert first == f"# mossbauer-dft: entry={ref.entry_id} version=1 plan={plan['plan_hash'][:12]}"
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.status == "inputs written" and vs.approved and vs.found == 0


def test_manifest_from_an_older_seal_is_not_inputs_written(proj, fe1):
    ref = _ready(proj, fe1)
    plan = J.load_version(proj, ref.entry_id)
    plan["resources"]["nprocs"] = 8  # re-sealed with different content: the manifest names the old seal
    P.save_plan(P.approve_plan(plan, "tester"), ref.path / "plan.yaml")
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.status == "approved" and vs.approved and "modified" not in vs.flags


def test_three_of_four_outputs_found(proj, fe1):  # AE1
    ref = _ready(proj, fe1, freq=True)
    stems = list(_jobs(proj, ref))
    assert len(stems) == 4
    for s in stems[:3]:
        _place(proj, ref, s)
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.status == "outputs found" and (vs.found, len(vs.expected)) == (3, 4) and vs.label == "outputs found, 3 of 4"
    st = _by_stem(vs)
    assert [st[s].state for s in stems] == ["complete"] * 3 + ["missing"]
    assert st[stems[0]].file == f"outputs/{stems[0]}.out"


def test_truncated_output_is_incomplete_not_complete(proj, fe1):
    ref = _ready(proj, fe1)
    s = list(_jobs(proj, ref))[1]
    _place(proj, ref, s, terminated=False)
    vs = J.scan_version(proj, ref.entry_id)
    assert _by_stem(vs)[s].state == "incomplete" and vs.found == 0 and vs.status == "outputs found"


@pytest.mark.parametrize("representation", ["raw", "compact"])
@pytest.mark.parametrize("failure,reason", [
    ("scf", "SCF"), ("unknown_scf", "SCF"), ("error", "error termination"),
    ("geometry", "optimization"), ("unknown_geometry", "optimization"),
])
def test_failed_calculation_stays_in_run_queue_and_can_be_resubmitted(proj, fe1, tmp_path, representation, failure, reason):
    from mossbauer_dft import compact, harvest, tutorial

    ref = _ready(proj, fe1)
    jobs = _jobs(proj, ref)
    stem = next(s for s, job in jobs.items() if job["kind"] == ("opt" if "geometry" in failure else "mossbauer_sp"))
    for s in jobs:
        _place(proj, ref, s)
    out = ref.path / "outputs" / f"{stem}.out"
    text = out.read_text()
    if failure == "scf":
        text += "\nSCF NOT CONVERGED AFTER 100 CYCLES\n"
    elif failure == "unknown_scf":
        text = re.sub(r"^.*SCF CONVERGED AFTER.*$", "", text, flags=re.M)
    elif failure == "error":
        text += "\nORCA finished by error termination\n"
    elif failure == "geometry":
        text = text.replace("THE OPTIMIZATION HAS CONVERGED", "Maximum optimization cycles reached")
    else:
        text = text.replace("THE OPTIMIZATION HAS CONVERGED", "").replace("GEOMETRY OPTIMIZATION CYCLE 1", "")
    out.write_text(text)
    if representation == "compact":
        doc = harvest.harvest(ref.path / "inputs" / "harvest-manifest.json", ref.path / "outputs")
        assert not doc["rejected"] and not doc["missing"]
        source = tmp_path / "return.json"
        source.write_text(json.dumps(doc))
        for p in (ref.path / "outputs").glob("*.out"):
            p.unlink()
        compact.import_bundle(proj, ref.entry_id, 1, source)
    before = _tree(ref.path)
    vs = J.scan_version(proj, ref.entry_id)
    job = _by_stem(vs)[stem]
    assert job.state == "incomplete"
    assert reason in job.reason
    assert vs.found == len(jobs) - 1
    assert J.failed_jobs(vs) == [stem]
    assert tutorial.guide(proj)["next"] == "run"
    assert _tree(ref.path) == before  # scans and guidance never rewrite evidence
    result = J.analyze_version(proj, ref.entry_id)
    assert tutorial.guide(proj)["next"] == "run"  # a saved analysis cannot hide an unfinished optimization
    if jobs[stem]["kind"] == "mossbauer_sp":
        assert stem not in {row["job"] for row in result["rows"]}
    J.write_resubmit(proj, ref.entry_id)
    resubmit = json.loads((ref.path / "inputs" / "resubmit.json").read_text())
    assert resubmit["requested"] == [stem]


def test_successful_final_scf_and_geometry_advance_the_guide(proj, fe1):
    from mossbauer_dft import tutorial

    ref = _ready(proj, fe1, freq=True)
    for stem in _jobs(proj, ref):
        out = _place(proj, ref, stem)
        out.write_text(out.read_text() + "\nSCF NOT CONVERGED AFTER 100 CYCLES\nSCF CONVERGED AFTER 5 CYCLES\n")
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.found == 4 and {j.state for j in vs.jobs} == {"complete"}
    assert J.failed_jobs(vs) == []
    assert tutorial.guide(proj)["next"] == "analyze"


def test_slurm_logs_and_non_out_files_are_ignored(proj, fe1):
    ref = _ready(proj, fe1)
    (ref.path / "outputs" / "slurm-12345.out").write_text("slurm log\n")
    (ref.path / "outputs" / f"{list(_jobs(proj, ref))[1]}.gbw").write_bytes(b"\0\1")
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.unmatched == [] and vs.status == "inputs written" and {j.state for j in vs.jobs} == {"missing"}


def test_unmatched_out_is_listed_and_matched_still_counted(proj, fe1):  # AE2
    ref = _ready(proj, fe1)
    s = list(_jobs(proj, ref))[1]
    _place(proj, ref, s)
    (ref.path / "outputs" / "someone_else_sp.out").write_text(TEMPLATE)
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.unmatched == ["someone_else_sp.out"] and vs.found == 1 and _by_stem(vs)[s].state == "complete"


def test_output_left_in_inputs_is_missing_with_move_hint(proj, fe1):
    ref = _ready(proj, fe1)
    s = list(_jobs(proj, ref))[1]
    _place(proj, ref, s, where="inputs")
    vs = J.scan_version(proj, ref.entry_id)
    j = _by_stem(vs)[s]
    assert j.state == "missing" and j.hint and "outputs/" in j.hint and f"inputs/{s}.out" in j.hint
    assert vs.found == 0 and vs.status == "inputs written" and vs.hints == [j.hint]


# ----------------------------------------------------------------------------- provenance (KTD4)
def test_output_stamped_for_v1_in_v2_is_wrong_version(proj, fe1):
    r1 = _ready(proj, fe1)
    s = list(_jobs(proj, r1))[1]
    v1_out = _place(proj, r1, s)
    r2 = J.new_version(proj, r1.entry_id, overrides={"nprocs": 8})
    _approve(proj, r2)
    J.write_version_inputs(proj, r2.entry_id, 2)
    assert s in _jobs(proj, r2)
    (r2.path / "outputs" / f"{s}.out").write_text(v1_out.read_text())
    j = _by_stem(J.scan_version(proj, r2.entry_id, 2))[s]
    assert j.state == "wrong-version" and "version=1" in j.reason
    assert _by_stem(J.scan_version(proj, r1.entry_id, 1))[s].state == "complete"


def test_unstamped_outputs_matching_every_job_kind_are_complete(proj, fe1):
    ref = _ready(proj, fe1, freq=True)
    jobs = _jobs(proj, ref)
    assert {j["kind"] for j in jobs.values()} == {"opt", "freq", "mossbauer_sp"}
    for s in jobs:
        _place(proj, ref, s, deck=_unstamped(ref, s))
    vs = J.scan_version(proj, ref.entry_id)
    assert {j.stem: j.state for j in vs.jobs} == {s: "complete" for s in jobs} and vs.found == 4


def test_unstamped_output_with_other_multiplicity_is_wrong_version(proj, fe1):
    ref = _ready(proj, fe1)
    s = list(_jobs(proj, ref))[1]
    _place(proj, ref, s, deck=_unstamped(ref, s), mult=2)
    j = _by_stem(J.scan_version(proj, ref.entry_id))[s]
    assert j.state == "wrong-version" and "multiplicity" in j.reason


def test_unstamped_output_differing_only_in_basis_is_wrong_version(proj, fe1):
    ref = _ready(proj, fe1)
    s = list(_jobs(proj, ref))[1]
    deck = _unstamped(ref, s)
    assert "def2-TZVP" in deck
    _place(proj, ref, s, deck=deck.replace("def2-TZVP", "def2-SVP"))
    j = _by_stem(J.scan_version(proj, ref.entry_id))[s]
    assert j.state == "wrong-version" and "basis" in j.reason


def test_output_without_echoed_input_is_unverified(proj, fe1):
    ref = _ready(proj, fe1)
    s = list(_jobs(proj, ref))[1]
    _place(proj, ref, s, echo=False)
    vs = J.scan_version(proj, ref.entry_id)
    assert _by_stem(vs)[s].state == "unverified" and vs.found == 0


# ----------------------------------------------------------------------------- flags, entry rows, rescans
def test_edit_after_analysis_shows_new_draft_with_older_results_flag(proj, fe1):  # R9
    ref = _ready(proj, fe1)
    (ref.path / "results").mkdir()
    (ref.path / "results" / "analysis.json").write_text("{}\n")
    [row] = J.scan_project(proj)
    assert row.newest.status == "analyzed" and not row.older_results
    J.edit_entry(proj, ref.entry_id, overrides={"deq_functional": "TPSSh", "nprocs": 8})
    [row] = J.scan_project(proj)
    assert (row.entry_id, row.display_name, row.state_label) == (ref.entry_id, "cmpd", "q-1 m4")
    assert row.newest.version == 2 and row.newest.status == "draft" and row.older_results
    assert [v.status for v in row.versions] == ["analyzed", "draft"]


def test_hand_edited_approved_plan_keeps_status_with_modified_flag(proj, fe1):
    ref = _ready(proj, fe1)
    other = _add(proj, fe1, mult=2)
    _approve(proj, other)
    assert J.scan_version(proj, ref.entry_id).approved
    for r in (ref, other):
        p = r.path / "plan.yaml"
        d = yaml.safe_load(p.read_text())
        d["resources"]["nprocs"] = 4
        p.write_text(yaml.safe_dump(d, sort_keys=False))
    a, b = J.scan_version(proj, ref.entry_id), J.scan_version(proj, other.entry_id)
    assert (a.status, a.flags, a.approved) == ("inputs written", ["modified"], False)
    assert (b.status, b.flags, b.approved) == ("draft", ["modified"], False)


def test_unreadable_version_is_flagged_and_does_not_break_the_overview(proj, fe1):
    bad, good = _add(proj, fe1), _add(proj, fe1, mult=2)
    (bad.path / "plan.yaml").write_text("schema: [unclosed\n  - :")
    rows = {r.entry_id: r for r in J.scan_project(proj)}
    assert rows[bad.entry_id].newest.flags == ["unreadable"] and rows[bad.entry_id].newest.status is None
    assert rows[good.entry_id].newest.status == "draft"


def test_output_added_after_a_scan_appears_on_the_next_scan(proj, fe1):  # R8
    ref = _ready(proj, fe1)
    before = _tree(proj)
    first = J.scan_project(proj)
    assert J.scan_project(proj) == first and _tree(proj) == before  # deterministic, nothing stored
    _place(proj, ref, list(_jobs(proj, ref))[0])
    [row] = J.scan_project(proj)
    assert row.newest.found == 1 and row.newest.status == "outputs found" and first[0].newest.found == 0


# ----------------------------------------------------------------------------- the stamp in write_inputs
def test_stamp_adds_one_line_and_unstamped_decks_are_unchanged(tmp_path, proj, fe1):
    ref = _add(proj, fe1)
    _approve(proj, ref)
    pp = ref.path / "plan.yaml"
    plain, stamped = tmp_path / "plain", tmp_path / "stamped"
    W.write_inputs(pp, plain)
    W.write_inputs(pp, stamped, stamp=dict(entry="e1", version=7))
    plan = P.load_plan(pp)
    line = f"# mossbauer-dft: entry=e1 version=7 plan={plan['plan_hash'][:12]}\n"
    a, b = _tree(plain), _tree(stamped)
    assert a.keys() == b.keys() and "manifest.json" not in a
    for k in a:
        assert b[k] == (line.encode() + a[k] if k.endswith(".inp") else a[k])
    opt = next(j for j in plan["jobs"] if j["kind"] == "opt")
    gen = build_input("opt", P.geometry_spec_from_plan(plan), charge=opt["charge"], multiplicity=opt["multiplicity"], xyz_file="structure.xyz")
    assert (plain / f"{opt['stem']}.inp").read_text() == gen
