"""Saved analysis per version (U4: R3, R14, R17; KTD3 stale/partial flags, KTD7, KTD8)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from mossbauer_dft import project as J
from mossbauer_dft import references as R
from mossbauer_dft import workflow as W
from test_plan import XYZ, _home  # noqa: F401
from test_project_status import _approve, _jobs, _place, _ready, _unstamped, fe1, proj  # noqa: F401

CAL = "mcwilliams2017_b3lyp_def2tzvp"
FOUR = {"deq_functional": "TPSSh", "crosscheck_functional": "PBE0", "highfield": True}


def _ready_ov(root, s, overrides):
    ref = J.add_entry(root, s, -1, 4, name="cmpd", oxidation_state=1, overrides=overrides)
    _approve(root, ref)
    J.write_version_inputs(root, ref.entry_id, ref.version)
    return ref


def _props(root, ref):
    return [s for s, j in _jobs(root, ref).items() if j["kind"] in ("mossbauer_sp", "highfield_sp")]


def _strict(text):
    def bad(c):
        raise ValueError(f"non-standard JSON constant {c}")
    return json.loads(text, parse_constant=bad)


def _saved(ref):
    return _strict((ref.path / "results" / "analysis.json").read_text())


def test_analysis_saves_and_loads_rows_calibration_and_level(proj, fe1):
    ref = _ready(proj, fe1)
    props = _props(proj, ref)
    assert len(props) == 2
    outs = [_place(proj, ref, s) for s in props]
    J.analyze_version(proj, ref.entry_id, calibration=CAL, level=0.9)
    direct = W.analyze(J.load_version(proj, ref.entry_id), outs, calibration=CAL, level=0.9)
    res = J.load_results(proj, ref.entry_id)
    assert res["rows"] == json.loads(json.dumps(direct["rows"])) and len(res["rows"]) == 2
    assert res["calibration"] == CAL and res["level"] == 0.9 and res["q_barn"] == 0.16
    e = R.get_calibration_entry(CAL)
    assert res["calibration_constants"] == dict(id=CAL, alpha=float(e["alpha"]), C=float(e["C"]), beta=float(e["beta"]))
    assert res["plan_hash"] == J.load_version(proj, ref.entry_id)["plan_hash"]
    assert res["outputs"] == [dict(name=o.name, size=o.stat().st_size, sha256=hashlib.sha256(o.read_bytes()).hexdigest())
                              for o in sorted(outs)]
    assert W.results_csv(res) == W.results_csv(direct)
    assert _saved(ref) == res
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.status == "analyzed" and vs.has_results and vs.flags == []


def test_nan_and_numpy_floats_save_as_strict_json(proj, fe1, monkeypatch):
    ref = _ready(proj, fe1)
    for s in _props(proj, ref):
        _place(proj, ref, s)
    real = W.analyze

    def numpy_analyze(*a, **kw):
        r = real(*a, **kw)
        r["rows"][0].update(delta=np.float64(0.42), delta_lo=np.float64("nan"), delta_hi=float("nan"), atom=np.int64(0),
                            ok=np.bool_(True), eta=np.float32(0.25))
        return r
    monkeypatch.setattr(W, "analyze", numpy_analyze)
    J.analyze_version(proj, ref.entry_id)
    row = _saved(ref)["rows"][0]
    assert (row["delta"], row["delta_lo"], row["delta_hi"], row["atom"], row["ok"], row["eta"]) == (0.42, None, None, 0, True, 0.25)
    assert J.load_results(proj, ref.entry_id)["rows"][0]["delta_lo"] is None


def test_new_changed_or_removed_output_marks_result_stale(proj, fe1):
    ref = _ready_ov(proj, fe1, {"highfield": True})
    props = _props(proj, ref)
    assert len(props) == 3
    a, b = (_place(proj, ref, s) for s in props[:2])
    J.analyze_version(proj, ref.entry_id)
    assert "stale" not in J.scan_version(proj, ref.entry_id).flags
    _place(proj, ref, props[2])  # a new complete .out after analysis
    assert "stale" in J.scan_version(proj, ref.entry_id).flags
    J.analyze_version(proj, ref.entry_id)
    assert J.scan_version(proj, ref.entry_id).flags == []
    a.write_text(a.read_text() + "\n")  # content changed
    assert "stale" in J.scan_version(proj, ref.entry_id).flags
    J.analyze_version(proj, ref.entry_id)
    b.unlink()  # removed
    assert "stale" in J.scan_version(proj, ref.entry_id).flags


def test_copy_with_reset_mtimes_is_not_stale(proj, fe1, tmp_path):  # AE6
    ref = _ready(proj, fe1)
    for s in _props(proj, ref):
        _place(proj, ref, s)
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    saved = J.load_results(proj, ref.entry_id)
    copy = tmp_path / "synced" / "study"
    shutil.copytree(proj, copy)
    for p in copy.rglob("*"):
        os.utime(p, (0, 0))
    vs = J.scan_version(copy, ref.entry_id)
    assert vs.status == "analyzed" and vs.flags == []
    assert J.load_results(copy, ref.entry_id) == saved


def test_three_of_four_property_outputs_is_partial_then_stale(proj, fe1):
    ref = _ready_ov(proj, fe1, FOUR)
    props = _props(proj, ref)
    assert len(props) == 4 and {_jobs(proj, ref)[s]["kind"] for s in props} == {"mossbauer_sp", "highfield_sp"}
    for s in props[:3]:
        _place(proj, ref, s)
    J.analyze_version(proj, ref.entry_id)
    res = J.load_results(proj, ref.entry_id)
    assert (res["complete_property"], res["expected_property"]) == (3, 4) and len(res["outputs"]) == 3
    assert J.scan_version(proj, ref.entry_id).flags == ["partial"]
    _place(proj, ref, props[3])
    assert set(J.scan_version(proj, ref.entry_id).flags) == {"partial", "stale"}
    J.analyze_version(proj, ref.entry_id)
    assert J.scan_version(proj, ref.entry_id).flags == []


def test_wrong_version_output_is_excluded_and_named_in_notes(proj, fe1):
    ref = _ready(proj, fe1)
    good, bad = _props(proj, ref)
    _place(proj, ref, good)
    _place(proj, ref, bad, deck=_unstamped(ref, bad), mult=2)
    assert {j.stem: j.state for j in J.scan_version(proj, ref.entry_id).jobs}[bad] == "wrong-version"
    J.analyze_version(proj, ref.entry_id)
    res = J.load_results(proj, ref.entry_id)
    assert {r["job"] for r in res["rows"]} == {good} and [o["name"] for o in res["outputs"]] == [f"{good}.out"]
    assert any(bad in n and "wrong-version" in n for n in res["notes"])
    assert "partial" in J.scan_version(proj, ref.entry_id).flags


def test_analysis_refused_without_a_complete_property_output(proj, fe1):
    ref = _ready(proj, fe1)
    opt = next(s for s, j in _jobs(proj, ref).items() if j["kind"] == "opt")
    _place(proj, ref, opt)
    _place(proj, ref, _props(proj, ref)[0], terminated=False)
    with pytest.raises(J.ProjectError, match="property output"):
        J.analyze_version(proj, ref.entry_id)
    assert not (ref.path / "results" / "analysis.json").exists()


def test_v1_results_stay_loadable_after_v2_and_v2_has_none(proj, fe1):  # R14
    r1 = _ready(proj, fe1)
    for s in _props(proj, r1):
        _place(proj, r1, s)
    J.analyze_version(proj, r1.entry_id)
    saved = J.load_results(proj, r1.entry_id)
    r2 = J.new_version(proj, r1.entry_id, overrides={"nprocs": 8})
    assert J.load_results(proj, r1.entry_id) is None and J.load_results(proj, r1.entry_id, 2) is None
    assert J.load_results(proj, r1.entry_id, 1) == saved
    assert not J.scan_version(proj, r2.entry_id, 2).has_results and J.scan_version(proj, r1.entry_id, 1).status == "analyzed"


def test_fresh_process_reopens_saved_result_without_analyze(proj, fe1, tmp_path):  # AE5, R3, R17
    ref = _ready(proj, fe1)
    for s in _props(proj, ref):
        _place(proj, ref, s)
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    saved = J.load_results(proj, ref.entry_id)
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    script = (
        "import json, sys\n"
        "from mossbauer_dft import project as J, workflow as W\n"
        "def boom(*a, **k): raise AssertionError('re-ran analysis')\n"
        "W.analyze = W.resolve_calibration = boom\n"
        "r = J.load_results(sys.argv[1], sys.argv[2])\n"
        "vs = J.scan_version(sys.argv[1], sys.argv[2])\n"
        "print(json.dumps(dict(result=r, status=vs.status, flags=vs.flags)))\n")
    env = dict(os.environ, MOSSBAUER_DFT_HOME=str(empty_home))
    out = subprocess.run([sys.executable, "-c", script, str(proj), ref.entry_id], env=env, capture_output=True, text=True,
                         check=True, cwd=Path(__file__).parent)
    got = json.loads(out.stdout)
    assert got["result"] == saved and (got["status"], got["flags"]) == ("analyzed", [])
    assert got["result"]["calibration_constants"]["alpha"] == float(R.get_calibration_entry(CAL)["alpha"])


@pytest.mark.parametrize("body", ["{not json", "[]", '{"complete_property": null, "outputs": []}'])
def test_unreadable_results_flag_only_the_results_and_reanalysis_replaces_them(proj, fe1, body):
    """Review #8: a bad analysis.json keeps the plan's status and approval and does not block Analyze."""
    ref = _ready(proj, fe1)
    for s in _props(proj, ref):
        _place(proj, ref, s)
    (ref.path / "results").mkdir(exist_ok=True)
    (ref.path / "results" / "analysis.json").write_text(body)
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.status == "outputs found" and vs.approved and "results unreadable" in vs.flags and "unreadable" not in vs.flags
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.status == "analyzed" and vs.flags == []
