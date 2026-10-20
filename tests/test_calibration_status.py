"""Calibration status of saved analyses, status-separated comparisons, and unsaved trial analyses (R1-R9; AE1-AE5)."""
import contextlib
import io
import json

import pytest

from mossbauer_dft import project as J, references as R, study as S
from mossbauer_dft.cli import main
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _ready, _place, _jobs  # noqa: F401

CAL = "mcwilliams2017_b3lyp_def2tzvp"   # verified literature entry, but its Fe basis does not match the default protocol


def _outputs(root, ref):
    for stem, job in _jobs(root, ref).items():
        if job["kind"] == "mossbauer_sp":
            _place(root, ref, stem)


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_protocol_mismatched_calibration_is_saved_as_exploratory(proj, fe1):  # AE1
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    res = J.analyze_version(proj, ref.entry_id, calibration=CAL)
    assert res["delta_status"]["status"] == "exploratory"
    assert any("not protocol-matched" in r for r in res["delta_status"]["reasons"])
    assert json.loads((ref.path / "results" / "analysis.json").read_text())["delta_status"] == res["delta_status"]


def test_no_calibration_is_uncalibrated(proj, fe1):  # AE2
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    assert J.analyze_version(proj, ref.entry_id)["delta_status"]["status"] == "uncalibrated"


@pytest.mark.parametrize("source,verified,matched,expected", [
    ("literature", True, True, "calibrated"), ("literature", False, True, "exploratory"),
    ("literature", True, False, "exploratory"), ("project_fit", False, True, "project-fit"),
    ("project_fit", False, False, "exploratory"), ("project_fit_solvent", False, True, "exploratory")])
def test_status_tiers(fe1, monkeypatch, source, verified, matched, expected):
    from mossbauer_dft import plan as P
    plan = P.make_plan(fe1, -1, 4, oxidation_state=1)
    soft = ["solvent: water vs -"] if source == "project_fit_solvent" else ([] if matched else ["grid: x vs y"])
    monkeypatch.setattr(R, "match_calibration", lambda *a: dict(hard=[], soft=soft, verified=verified, protocol_matched=matched))
    out = S.calibration_status(plan, object(), dict(id="c", source=source.replace("_solvent", "")))
    assert out["status"] == expected and out["reasons"]


def test_comparison_never_pools_statuses_and_labels_exploratory(proj, fe1):  # AE3
    a, b = _ready(proj, fe1, name="a"), _ready(proj, fe1, name="b")
    for ref in (a, b):
        _outputs(proj, ref)
        J.analyze_version(proj, ref.entry_id, calibration=CAL)
        p = ref.path / "results" / "analysis.json"
        d = json.loads(p.read_text())
        for r in d["rows"]:
            r.update(delta_exp=0.5, delta=0.6, delta_note="")  # the mismatched test calibration predicts no delta itself
        if ref is a:
            d["delta_status"] = dict(status="calibrated", reasons=["test"])
        p.write_text(json.dumps(d))
    doc = S.compare(proj)
    delta = [s for s in doc["summaries"] if s["property"] == "delta"]
    statuses = {s["delta_status"] for s in delta}
    assert statuses == {"calibrated", "exploratory"} and all(s["n"] == 1 for s in delta)
    assert all("not calibrated predictions" in s["interpretation"] for s in delta if s["delta_status"] == "exploratory")
    assert {r["delta_status"] for r in doc["rows"]} == {"calibrated", "exploratory"}


def test_legacy_analysis_compares_as_unknown(proj, fe1):  # AE4, R3
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    p = ref.path / "results" / "analysis.json"
    d = json.loads(p.read_text())
    del d["delta_status"]
    p.write_text(json.dumps(d))
    rows = S.compare(proj)["rows"]
    assert rows and all(r["delta_status"] == "unknown" for r in rows)


def test_report_carries_status(proj, fe1):  # R6
    from mossbauer_dft import report
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    import csv as _csv
    import zipfile
    doc = S.compare(proj)
    public = report.public_snapshot(doc)
    assert public["rows"] and all(r["delta_status"] == "exploratory" for r in public["rows"])
    data, _html = report.bundle(doc)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        csv_name = next(n for n in names if n.endswith(".csv"))
        header = next(_csv.reader(io.StringIO(z.read(csv_name).decode())))
        html_name = next(n for n in names if n.endswith(".html"))
        assert "delta_status" in header and "<th>delta_status</th>" in z.read(html_name).decode()


def test_trial_never_writes_and_has_its_own_status(proj, fe1):  # AE5, R7-R9
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    J.analyze_version(proj, ref.entry_id)
    before = _tree(proj)
    trial = J.trial_analysis(proj, ref.entry_id, calibration=CAL)
    assert _tree(proj) == before
    assert trial["trial"] is True and all(r["trial"] for r in trial["rows"]) and trial["delta_status"]["status"] == "exploratory"
    assert J.load_results(proj, ref.entry_id)["delta_status"]["status"] == "uncalibrated"


def test_cli_analyze_trial_prints_json_and_writes_nothing(proj, fe1):
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    before = _tree(proj)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(["project", "analyze", "--project", str(proj), "--entry", ref.entry_id, "--trial", "--calibration", CAL])
    doc = json.loads(buf.getvalue())
    assert doc["trial"] is True and doc["delta_status"]["status"] == "exploratory" and _tree(proj) == before


def test_missing_entry_and_path_loaded_literature_are_exploratory(fe1, monkeypatch):
    from mossbauer_dft import plan as P
    plan = P.make_plan(fe1, -1, 4, oxidation_state=1)
    assert S.calibration_status(plan, object(), None)["status"] == "exploratory"
    monkeypatch.setattr(R, "match_calibration", lambda *a: dict(hard=[], soft=[], verified=True, protocol_matched=True))
    assert S.calibration_status(plan, object(), dict(id="c", verified=True), from_file=True)["status"] == "exploratory"


def test_unknown_cohort_label_and_row_note_downgrade(proj, fe1):
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    p = ref.path / "results" / "analysis.json"
    d = json.loads(p.read_text())
    for r in d["rows"]:
        r.update(delta_exp=0.5, delta=0.6, delta_note="")
    del d["delta_status"]
    p.write_text(json.dumps(d))
    unknown = [s for s in S.compare(proj)["summaries"] if s["property"] == "delta"]
    assert unknown and all("not recorded" in s["interpretation"] for s in unknown)
    d["delta_status"] = dict(status="calibrated", reasons=["t"])
    d["rows"][0]["delta_note"] = "program: calibration='ORCA 3' vs calculation='ORCA 6'"
    p.write_text(json.dumps(d))
    row = S.compare(proj)["rows"][0]
    assert row["delta_status"] == "exploratory" and any("this output" in r for r in row["delta_status_reasons"])


@pytest.mark.parametrize("hints,overrides,entry_id,expected", [
    ({}, dict(orca_version="3.0.3", geometry=dict(mode="as_given", as_given_label="BP86-D3BJ/ZORA-def2-TZVP")),
     "mcwilliams2017_b3lyp_cppp", "calibrated"),
    (dict(solvent="water"), {}, "fit_fe1_calib18_b3lyp_49ea7a81_noFeO4", "project-fit")])
def test_status_unmocked_uses_plan_orca_version(fe1, hints, overrides, entry_id, expected):
    """The status fingerprint carries the plan's ORCA version, as plan_calibration does (no match_calibration mock)."""
    from mossbauer_dft import plan as P
    plan = P.make_plan(fe1, -1, 4, oxidation_state=1, overrides=overrides, **hints)
    assert plan["calibration"]["lookup"]["id"] == entry_id and plan["calibration"]["lookup"]["protocol_matched"]
    out = S.calibration_status(plan, object(), R.get_calibration_entry(entry_id))
    assert out["status"] == expected, out["reasons"]


def test_analysis_with_matched_project_fit_is_saved_as_project_fit(proj, fe1):
    """End to end: a plan that looks up a protocol-matched project fit saves its analysis as project-fit."""
    from mossbauer_dft import plan as P
    ref = J.add_entry(proj, fe1, -1, 4, name="cmpd", oxidation_state=1, solvent="water")
    P.save_plan(P.approve_plan(J.load_version(proj, ref.entry_id, ref.version), "tester"), ref.path / "plan.yaml")
    J.write_version_inputs(proj, ref.entry_id, ref.version)
    _outputs(proj, ref)
    res = J.analyze_version(proj, ref.entry_id)
    assert res["delta_status"]["status"] == "project-fit", res["delta_status"]["reasons"]
