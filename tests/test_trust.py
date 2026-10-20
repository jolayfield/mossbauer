"""Typed trust flags (second-pass idea 1; decisions D50-D51)."""
import json

from mossbauer_dft import project as J, report, study as S, trust as T
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _ready, _place, _jobs  # noqa: F401
from test_calibration_status import CAL, _outputs


def test_messages_classify_by_severity():
    assert T.classify("rho0=1.000 is outside the calibration range [2, 3] (extrapolation)")["severity"] == T.CAUTION
    assert T.classify("calibration refused: METHOD MISMATCH")["code"] == "calibration_refused"
    assert T.classify("IMPLAUSIBLE delta=4.00 mm/s")["severity"] == T.BLOCKING
    assert T.classify("something nobody classified")["severity"] == T.BLOCKING  # conservative default


def test_spin_contamination_ignores_broken_symmetry():
    assert T.spin_contamination(4.25, 3.75, False)["code"] == "spin_contamination"
    assert T.spin_contamination(3.78, 3.75, False) is None
    assert T.spin_contamination(4.10, 3.75, True) is None


def test_fe1_deq_rule_only_for_fe1_and_avoided_functionals():
    assert T.rule_flags("MN15", 1)[0]["property"] == "deq"
    assert T.rule_flags("MN15", 2) == [] and T.rule_flags("TPSSh", 1) == []


def test_note_stays_authoritative_for_edited_or_old_rows():
    caution = [T.classify("x (extrapolation)"), T.flag("spin_contamination", T.CAUTION, "all", "spin")]
    assert T.for_row(dict(delta_note="x (extrapolation)", trust_flags=caution)) == caution
    edited = T.for_row(dict(delta_note="hand-written concern", trust_flags=caution))
    assert [f["code"] for f in edited] == ["unclassified", "spin_contamination"]
    assert T.for_row(dict(delta_note="")) == []


def test_analysis_rows_carry_flags_matching_the_note(proj, fe1):
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    res = J.analyze_version(proj, ref.entry_id)  # no calibration: rho(0) only
    assert res["rows"]
    for row in res["rows"]:
        assert row["delta_note"] == T.note(row["trust_flags"])
        assert not T.blocked(row["trust_flags"], "delta")
    delta_rows = [r for r in res["rows"] if "delta" in r["purpose"].split(",")]
    assert delta_rows and all("no_calibration" in [f["code"] for f in r["trust_flags"]] for r in delta_rows)


def test_caution_keeps_calibrated_status_but_blocking_demotes(proj, fe1):
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    p = ref.path / "results" / "analysis.json"
    d = json.loads(p.read_text())
    d["delta_status"] = dict(status="calibrated", reasons=["t"])
    ext = T.classify("rho0=1 is outside the calibration range [2, 3] (extrapolation)")
    for r in d["rows"]:
        r.update(delta_exp=0.5, delta=0.6, trust_flags=[ext], delta_note=ext["text"])
    p.write_text(json.dumps(d))
    rows = S.compare(proj)["rows"]
    assert all(r["delta_status"] == "calibrated" and r["delta_metrics_eligible"] for r in rows)
    d["rows"][0]["trust_flags"] = [T.classify("IMPLAUSIBLE delta=4 mm/s")]
    d["rows"][0]["delta_note"] = d["rows"][0]["trust_flags"][0]["text"]
    p.write_text(json.dumps(d))
    first = S.compare(proj)["rows"][0]
    assert first["delta_status"] == "exploratory" and not first["delta_metrics_eligible"]


def test_report_grades_alerts_blocking_first(proj, fe1):
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    p = ref.path / "results" / "analysis.json"
    d = json.loads(p.read_text())
    refused = T.classify("calibration refused: METHOD MISMATCH")
    d["rows"][0].update(trust_flags=[refused], delta_note=refused["text"])
    p.write_text(json.dumps(d))
    doc = S.compare(proj)
    _, html = report.bundle(doc)
    assert "<h2>Alerts</h2>" in html and "blocking" in html and "calibration refused" in html
    public = report.public_snapshot(doc)
    assert all("trust_flags" in r for r in public["rows"])
