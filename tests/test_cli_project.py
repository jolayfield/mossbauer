"""`mossbauer project` CLI group (U5: R18, R19, R21; KTD6, KTD11; F1-F3)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mossbauer_dft import plan as P
from mossbauer_dft.cli import main
from mossbauer_dft.structure import Structure
from test_plan import XYZ, _home  # noqa: F401
from test_project_status import _fake_out

CAL = "mcwilliams2017_b3lyp_def2tzvp"
STATE_KEYS = ("expected", "complete", "incomplete", "missing", "wrong_version", "unverified", "unmatched")


def _run(argv) -> str:
    """Run the CLI in-process and return what it printed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(argv)
    return buf.getvalue()


def _status(root) -> dict:
    return json.loads(_run(["project", "status", "--project", str(root), "--json"]))


def _xyz(tmp_path) -> Path:
    x = tmp_path / "fe1.xyz"
    x.write_text(XYZ)
    return x


def _version(doc, entry, n=None) -> dict:
    e = next(e for e in doc["entries"] if e["id"] == entry)
    return e["versions"][-1] if n is None else next(v for v in e["versions"] if v["version"] == n)


def _place_all(root, v, charge=-1, mult=4):
    """Fabricate a finished ORCA output for every expected job, echoing the deck the CLI wrote, into the reported outputs dir."""
    for stem in v["stems"]["expected"]:
        deck = (Path(root) / v["inputs_dir"] / f"{stem}.inp").read_text()
        (Path(root) / v["outputs_dir"] / f"{stem}.out").write_text(_fake_out(deck, charge, mult))


def e2e_flow(tmp_path) -> dict:
    """F1 -> F2 -> F3 with only the CLI: init, add, approve as an agent, setup, outputs placed, status, analyze, status."""
    root = tmp_path / "study"
    out = _run(["project", "init", str(root), "--name", "Fe study"])
    assert "Fe study" in out and (root / "project.yaml").is_file()
    out = _run(["project", "add", "--project", str(root), "--structure", str(_xyz(tmp_path)), "--charge", "-1",
                "--multiplicity", "4", "--name", "cmpd", "--ox-state", "1"])
    doc = _status(root)
    assert len(doc["entries"]) == 1
    eid = doc["entries"][0]["id"]
    assert eid in out and doc["entries"][0]["name"] == "cmpd" and doc["entries"][0]["state"] == "q-1 m4"
    assert _version(doc, eid)["status"] == "draft"

    out = _run(["project", "approve", "--project", str(root), "--entry", eid, "--via", "agent", "--on-behalf-of", "J. Layfield"])
    assert "sealed" in out and "approved by J. Layfield via agent" in out
    v = _version(_status(root), eid)
    assert v["status"] == "approved" and v["approval_line"] == "approved by J. Layfield via agent"
    assert v["approval"]["via"] == "agent" and v["approval"]["on_behalf_of"] == "J. Layfield"

    out = _run(["project", "setup", "--project", str(root), "--entry", eid])
    v = _version(_status(root), eid)
    assert v["status"] == "inputs written" and v["outputs_dir"] in out and "submission commands" in out
    assert v["submit_script"] == f"entries/{eid}/v1/inputs/submit.sh" and (root / v["submit_script"]).is_file()
    assert v["stems"]["missing"] == v["stems"]["expected"] and len(v["stems"]["expected"]) == 3

    _place_all(root, v)
    v = _version(_status(root), eid)
    assert v["status"] == "outputs found" and v["label"] == "outputs found, 3 of 3"
    assert v["stems"]["complete"] == v["stems"]["expected"] and v["stems"]["missing"] == [] and v["results"] is None

    csv = tmp_path / "r.csv"
    out = _run(["project", "analyze", "--project", str(root), "--entry", eid, "--calibration", CAL, "--csv", str(csv)])
    assert f"calibration: {CAL}" in out and "delta=" in out and csv.read_text().count("\n") >= 2
    doc = _status(root)
    v = _version(doc, eid)
    assert v["status"] == "analyzed" and v["results"] == f"entries/{eid}/v1/results/analysis.json"
    assert (root / v["results"]).is_file() and v["flags"] == []
    return dict(root=root, entry=eid, doc=doc)


def test_cli_only_end_to_end(tmp_path):
    e2e_flow(tmp_path)


def test_end_to_end_imports_nothing_from_gui(tmp_path):
    tests = Path(__file__).parent
    code = ("import sys\nfrom pathlib import Path\nimport test_cli_project as T\nT.e2e_flow(Path(sys.argv[1]))\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('streamlit', 'pandas') or m.startswith('mossbauer_dft.gui'))\n"
            "print('BAD', bad)\n")
    env = dict(os.environ, MOSSBAUER_DFT_HOME=str(tmp_path / "home"), PYTHONPATH=os.pathsep.join([str(tests), os.environ.get("PYTHONPATH", "")]))
    r = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, env=env, cwd=tests)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "BAD []"


def test_status_json_paths_are_relative_and_survive_a_move(tmp_path):
    flow = e2e_flow(tmp_path)
    moved = tmp_path / "elsewhere" / "study-copy"
    moved.parent.mkdir()
    shutil.move(str(flow["root"]), str(moved))
    doc = _status(moved)
    v = _version(doc, flow["entry"])
    assert v["status"] == "analyzed"
    for k in ("path", "inputs_dir", "outputs_dir", "submit_script", "results"):
        assert not Path(v[k]).is_absolute() and ".." not in Path(v[k]).parts
    assert (moved / v["path"]).is_dir() and (moved / v["inputs_dir"]).is_dir() and (moved / v["outputs_dir"]).is_dir()
    assert (moved / v["submit_script"]).is_file() and (moved / v["results"]).is_file()
    assert doc["project"]["root"] == str(moved.resolve())


def test_status_json_schema_and_job_state_lists(tmp_path):
    root = tmp_path / "study"
    _run(["project", "init", str(root)])
    _run(["project", "add", "--project", str(root), "--structure", str(_xyz(tmp_path)), "--charge", "-1", "--multiplicity", "4",
          "--name", "cmpd", "--ox-state", "1", "--deq-functional", "TPSSh", "--crosscheck-functional", "PBE0", "--highfield"])
    eid = _status(root)["entries"][0]["id"]
    _run(["project", "approve", "--project", str(root), "--all-drafts", "--by", "me"])
    _run(["project", "setup", "--project", str(root), "--entry", eid])
    v = _version(_status(root), eid)
    stems = v["stems"]["expected"]
    assert len(stems) >= 5
    ok, cut, other, noecho = stems[:4]
    od, ind = root / v["outputs_dir"], root / v["inputs_dir"]
    deck = lambda s: (ind / f"{s}.inp").read_text()  # noqa: E731
    (od / f"{ok}.out").write_text(_fake_out(deck(ok), -1, 4))
    (od / f"{cut}.out").write_text(_fake_out(deck(cut), -1, 4, terminated=False))
    (od / f"{other}.out").write_text(_fake_out(deck(other).replace("version=1 ", "version=9 "), -1, 4))
    (od / f"{noecho}.out").write_text(_fake_out(deck(noecho), -1, 4, echo=False))
    (od / "stray.out").write_text("not an ORCA output of this plan\n")
    (ind / f"{stems[4]}.out").write_text(_fake_out(deck(stems[4]), -1, 4))

    doc = _status(root)
    assert doc["schema_version"] == "1"
    v = _version(doc, eid)
    assert set(STATE_KEYS) <= set(v["stems"])
    s = v["stems"]
    assert (s["complete"], s["incomplete"], s["wrong_version"], s["unverified"]) == ([ok], [cut], [other], [noecho])
    assert s["missing"] == stems[4:] and s["unmatched"] == ["stray"]
    assert v["unmatched_files"] == [f"{v['outputs_dir']}/stray.out"]
    assert v["label"] == f"outputs found, 1 of {len(stems)}"
    jobs = {j["stem"]: j for j in v["jobs"]}
    assert jobs[other]["state"] == "wrong-version" and "version=9" in jobs[other]["reason"]
    assert jobs[ok]["file"] == f"{v['outputs_dir']}/{ok}.out"
    assert any(f"move it to outputs/{stems[4]}.out" in h for h in v["hints"])
    assert v["approval_line"] == "approved by me via cli (non-interactive)"


def test_edit_creates_draft_version_and_old_version_keeps_results(tmp_path):
    flow = e2e_flow(tmp_path)
    root, eid = flow["root"], flow["entry"]
    out = _run(["project", "edit", "--project", str(root), "--entry", eid, "--nprocs", "8"])
    assert f"{eid} v2" in out and "new version" in out
    doc = _status(root)
    e = next(e for e in doc["entries"] if e["id"] == eid)
    assert [v["version"] for v in e["versions"]] == [1, 2] and e["newest_version"] == 2
    assert _version(doc, eid, 2)["status"] == "draft" and _version(doc, eid, 2)["results"] is None
    assert _version(doc, eid, 1)["status"] == "analyzed" and (root / _version(doc, eid, 1)["results"]).is_file()
    assert e["older_results"] is True
    table = _run(["project", "status", "--project", str(root)])
    line = next(l for l in table.splitlines() if l.startswith(eid))
    assert "v2" in line and "draft" in line and "older version has results" in line


def test_edit_that_changes_state_creates_a_new_entry(tmp_path):
    root = tmp_path / "study"
    _run(["project", "init", str(root)])
    _run(["project", "add", "--project", str(root), "--structure", str(_xyz(tmp_path)), "--charge", "-1", "--multiplicity", "4",
          "--name", "cmpd", "--ox-state", "1"])
    eid = _status(root)["entries"][0]["id"]
    out = _run(["project", "edit", "--project", str(root), "--entry", eid, "--multiplicity", "2"])
    doc = _status(root)
    assert len(doc["entries"]) == 2
    new = next(e for e in doc["entries"] if e["id"] != eid)
    assert "new entry" in out and new["id"] in out and new["state"] == "q-1 m2"
    assert [v["version"] for v in next(e for e in doc["entries"] if e["id"] == eid)["versions"]] == [1]


def test_approve_all_drafts_reports_skipped_with_reason(tmp_path, monkeypatch):
    root = tmp_path / "study"
    x = _xyz(tmp_path)
    _run(["project", "init", str(root)])
    for name, extra in (("good", []), ("radical", ["--radical-ligand", "yes"])):
        _run(["project", "add", "--project", str(root), "--structure", str(x), "--charge", "-1", "--multiplicity", "4",
              "--name", name, "--ox-state", "1", *extra])
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    out = _run(["project", "approve", "--project", str(root), "--all-drafts", "--by", "me"])
    doc = _status(root)
    good = next(e for e in doc["entries"] if e["name"] == "good")
    bad = next(e for e in doc["entries"] if e["name"] == "radical")
    assert good["versions"][-1]["status"] == "approved" and bad["versions"][-1]["status"] == "draft"
    assert f"{good['id']} v1: sealed" in out
    assert f"{bad['id']} v1: skipped" in out and "open items" in out


def test_approve_agent_requires_on_behalf_of(tmp_path):
    root = tmp_path / "study"
    _run(["project", "init", str(root)])
    _run(["project", "add", "--project", str(root), "--structure", str(_xyz(tmp_path)), "--charge", "-1", "--multiplicity", "4",
          "--name", "cmpd", "--ox-state", "1"])
    eid = _status(root)["entries"][0]["id"]
    with pytest.raises(SystemExit, match="on-behalf-of"):
        main(["project", "approve", "--project", str(root), "--entry", eid, "--via", "agent"])
    assert _version(_status(root), eid)["status"] == "draft"


def test_project_import_reports_created_entry(tmp_path):
    legacy = tmp_path / "old"
    P.save_plan(P.make_plan(Structure.from_xyz_text(XYZ), -1, 4, name="legacy cmpd", oxidation_state=1), legacy / "plan" / "plan.yaml")
    out = _run(["project", "import", str(legacy)])
    doc = _status(legacy)
    assert len(doc["entries"]) == 1
    e = doc["entries"][0]
    assert f"{e['id']} v1" in out and e["name"] == "legacy cmpd" and e["versions"][0]["status"] == "draft"
    assert (legacy / "plan" / "plan.yaml").is_file()


def test_errors_name_the_project_subcommand(tmp_path):
    with pytest.raises(SystemExit, match=r"^mossbauer project status: NotAProjectError"):
        main(["project", "status", "--project", str(tmp_path / "nowhere")])
    _run(["project", "init", str(tmp_path / "s")])
    with pytest.raises(SystemExit, match=r"^mossbauer project setup: ProjectError: no entry 'nope'"):
        main(["project", "setup", "--project", str(tmp_path / "s"), "--entry", "nope"])


def test_list_prints_one_line_per_entry(tmp_path):
    root = tmp_path / "study"
    x = _xyz(tmp_path)
    _run(["project", "init", str(root)])
    for n in ("a", "b"):
        _run(["project", "add", "--project", str(root), "--structure", str(x), "--charge", "-1", "--multiplicity", "4", "--name", n,
              "--ox-state", "1"])
    lines = [l for l in _run(["project", "list", "--project", str(root)]).splitlines() if l.strip()]
    assert len(lines) == 2 and all("q-1 m4" in l and "v1" in l for l in lines)


def test_cli_edit_unset_and_structure_and_status_plan_hash(tmp_path):
    root = tmp_path / "study"
    _run(["project", "init", str(root)])
    _run(["project", "add", "--project", str(root), "--structure", str(_xyz(tmp_path)), "--charge", "-1", "--multiplicity", "4",
          "--name", "cmpd", "--ox-state", "1", "--deq-functional", "TPSSh", "--highfield"])
    eid = _status(root)["entries"][0]["id"]
    _run(["project", "edit", "--project", str(root), "--entry", eid, "--unset", "deq_functional", "--unset", "highfield"])
    from mossbauer_dft import project as J
    ov = J.load_version(root, eid, 2)["inputs"]["overrides"]
    assert not ov.get("deq_functional") and not ov.get("highfield")
    _run(["project", "edit", "--project", str(root), "--entry", eid, "--structure", str(_xyz(tmp_path))])
    v = _version(_status(root), eid)
    assert v["version"] == 3 and v["plan_hash"] == P.compute_hash(J.load_version(root, eid, 3))


def test_unset_nprocs_and_calibration_strategy_return_to_defaults(tmp_path):
    root = tmp_path / "study"
    _run(["project", "init", str(root)])
    _run(["project", "add", "--project", str(root), "--structure", str(_xyz(tmp_path)), "--charge", "-1", "--multiplicity", "4",
          "--name", "cmpd", "--ox-state", "1", "--nprocs", "8", "--calibration-strategy", "compute_refset"])
    eid = _status(root)["entries"][0]["id"]
    _run(["project", "edit", "--project", str(root), "--entry", eid, "--unset", "nprocs", "--unset", "calibration_strategy"])
    from mossbauer_dft import project as J
    plan = J.load_version(root, eid, 2)
    assert plan["resources"]["nprocs"] == 16 and plan["resources"]["maxcore"] == 3000
    assert J.load_version(root, eid, 1)["inputs"]["overrides"]["calibration_strategy"] == "compute_refset"
    assert plan["inputs"]["overrides"].get("calibration_strategy") is None  # cleared: the rule chooses again
