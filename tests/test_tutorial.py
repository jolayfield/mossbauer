"""Bundled teaching example and the next-step guide (guided-example plan: R1-R9; AE1-AE5)."""
import contextlib
import io
import json

import pytest

from mossbauer_dft import plan as P, project as J, references as R, tutorial as T
from mossbauer_dft.cli import main
from mossbauer_dft.structure import read_structure
from test_plan import _home  # noqa: F401


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def ex(tmp_path):
    root = tmp_path / "example"
    return root, T.create_example(root)


def test_example_is_drafts_with_reference_set_values(ex):  # AE1, R1-R4
    root, refs = ex
    assert J.open_project(root)["example"] == "iron-basics" and len(refs) == 3
    d = R.refset_definition("fe1_calib18")
    by_id = {c["id"]: c for c in d["compounds"]}
    for ref in refs:
        vs = J.scan_version(root, ref.entry_id)
        assert vs.status == "draft" and not vs.approved
        inp = J.load_version(root, ref.entry_id)["inputs"]
        c = by_id[inp["structure_file"][:-4]]
        assert (inp["charge"], inp["multiplicity"]) == (c["charge"], c["multiplicity"])
        assert inp["experimental"]["delta"] == c["delta_exp_mm_s"]
    assert not list(root.rglob("*.out")) and not list(root.rglob("*.inp"))   # nothing computed or written for the learner
    notes = (root / "EXAMPLE.md").read_text()
    assert "10.1021/acsomega.7b00595" in notes and "b3lyp_outlier" in notes and "No calculations or outputs" in notes


def test_example_plans_equal_hand_added_plans(ex, tmp_path):
    root, refs = ex
    other = tmp_path / "hand"
    J.create_project(other)
    d = R.refset_definition("fe1_calib18")
    c = next(c for c in d["compounds"] if c["id"] == "FeCl4_m")
    p = R._resolve_structure_path(d, c)
    ref = J.add_entry(other, read_structure(p), -1, 6, name="FeCl4_m", oxidation_state=3, temperature_K=4.2,
                      experimental=dict(delta=0.36, T_K=4.2), structure_filename=p.name)
    a = J.load_version(root, "FeCl4_m_qm1_m6")
    b = J.load_version(other, ref.entry_id)
    assert a["inputs"] == b["inputs"] and a["methods"] == b["methods"] and a["jobs"] == b["jobs"]


def test_guide_on_new_example_points_to_approval_and_writes_nothing(ex):  # AE2, R5-R6
    root, _ = ex
    before = _tree(root)
    g = T.guide(root)
    assert _tree(root) == before
    assert [s["id"] for s in g["steps"]] == ["add", "approve", "inputs", "run", "analyze", "compare"]
    st = {s["id"]: s for s in g["steps"]}
    assert g["next"] == "approve" and st["add"]["status"] == "done" and st["approve"]["n"] == 3
    assert all(st[k]["status"] == "later" for k in ("inputs", "run", "analyze", "compare"))
    assert all(s["why"] and s["page"] and s["cli"].startswith("mossbauer ") for s in g["steps"])
    assert "does not submit jobs" in st["run"]["why"] and "outputs/" in st["run"]["why"]  # R7


def test_guide_tracks_mixed_progress(ex):  # AE3
    root, refs = ex
    ref = refs[0]
    P.save_plan(P.approve_plan(J.load_version(root, ref.entry_id), "tester"), ref.path / "plan.yaml")
    J.write_version_inputs(root, ref.entry_id)
    g = T.guide(root)
    st = {s["id"]: s for s in g["steps"]}
    assert g["next"] == "approve" and st["approve"]["n"] == 2 and st["run"]["entries"] == [ref.entry_id]
    for r in refs[1:]:
        P.save_plan(P.approve_plan(J.load_version(root, r.entry_id), "tester"), r.path / "plan.yaml")
    assert T.guide(root)["next"] == "inputs"


def test_empty_project_guide_starts_with_add(tmp_path):
    J.create_project(tmp_path / "p")
    g = T.guide(tmp_path / "p")
    assert g["next"] == "add" and g["example"] is None and g["steps"][0]["status"] == "next"


@pytest.mark.parametrize("kind", ["project", "nonempty"])
def test_example_refuses_existing_project_or_nonempty_folder(tmp_path, kind):  # AE4
    root = tmp_path / "x"
    if kind == "project":
        J.create_project(root)
    else:
        root.mkdir()
        (root / "notes.txt").write_text("mine")
    before = _tree(root)
    with pytest.raises(T.ExampleError):
        T.create_example(root)
    assert _tree(root) == before


def test_unknown_example_is_refused(tmp_path):
    with pytest.raises(T.ExampleError, match="iron-basics"):
        T.create_example(tmp_path / "x", "nope")
    assert not (tmp_path / "x").exists()


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(argv)
    return json.loads(buf.getvalue())


def test_cli_example_list_create_and_guide(tmp_path):  # AE5
    assert [e["id"] for e in _run(["project", "example", "--list"])] == ["iron-basics"]
    root = tmp_path / "cls"
    out = _run(["project", "example", str(root)])
    assert len(out["entries"]) == 3 and J.is_project(root)
    g = _run(["project", "guide", "--project", str(root)])
    assert [s["id"] for s in g["steps"]] == ["add", "approve", "inputs", "run", "analyze", "compare"] and g["next"] == "approve"


def test_guide_keeps_partial_outputs_at_run(tmp_path):
    from mossbauer_dft.structure import Structure
    from test_plan import XYZ
    from test_project_status import _ready, _jobs, _place
    root = tmp_path / "study"
    J.create_project(root)
    ref = _ready(root, Structure.from_xyz_text(XYZ))
    _place(root, ref, next(iter(_jobs(root, ref))))
    assert T.guide(root)["next"] == "run"
    for stem in _jobs(root, ref):
        _place(root, ref, stem)
    assert T.guide(root)["next"] == "analyze"
    plan = J.load_version(root, ref.entry_id)
    plan["inputs"]["temperature_K"] = 99
    P.save_plan(plan, ref.path / "plan.yaml")
    assert T.guide(root)["next"] == "approve"


def test_guide_unreadable_entries_never_claim_finished(ex):
    root, refs = ex
    for ref in refs:
        (ref.path / "plan.yaml").write_text("invalid: [")
    g = T.guide(root)
    assert g["next"] is None and g["notes"]
    assert all(s["status"] == "later" for s in g["steps"])


def test_guide_quotes_project_path_for_cli(tmp_path):
    import shlex
    root = tmp_path / "learner's study"
    J.create_project(root)
    assert shlex.split(T.guide(root)["steps"][0]["cli"])[4] == str(root)
