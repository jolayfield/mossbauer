"""The calibration behind each number (second-pass idea 5; decisions D57-D58)."""
import json

import pytest

from mossbauer_dft import calcontext as X, plan as P, references as R
from mossbauer_dft.calibration import Calibration
from mossbauer_dft.cli import main
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _add  # noqa: F401


def _fe1_cal():
    ready, _ = R.resolve_refset("fe1_calib18")
    names = [c["id"] for c in ready[:6]]
    rho = [11800.0 + 0.4 * i for i in range(6)]
    delta = [1.0 - 0.3 * i + (0.02 if i % 2 else -0.02) for i in range(6)]
    return Calibration.fit(rho, delta, names=names, n_boot=0), names


def test_domain_from_training_compounds_and_published_ranges():
    ready, _ = R.resolve_refset("fe1_calib18")
    dom = X.domain(None, [c["id"] for c in ready])
    assert dom["basis"] == "training compounds" and 4 not in dom["ox"] and {1, 2, 3} <= set(dom["ox"])
    lit = X.domain(dict(fe_oxidation_range="+2 to +4", spin_range="total spin up to 5/2"))
    assert lit == dict(ox=[2, 3, 4], spin=[0.0, 2.5], basis="published range", n=None)
    assert X.domain(dict(fe_oxidation_range=None))["basis"] == "not recorded"


def test_coverage_warns_before_approval_for_uncovered_oxidation_state(fe1, _home, monkeypatch):
    plan = P.make_plan(fe1, -1, 4, oxidation_state=1)
    monkeypatch.setattr(X, "plan_domain", lambda p: (dict(ox=[2, 3], spin=[0.0, 2.5], basis="training compounds", n=5), "fit_x"))
    texts = [c["text"] for c in X.coverage(plan) if c["level"] == "warning"]
    assert any("Fe(+1) is outside" in t and "fit_x" in t for t in texts)
    monkeypatch.setattr(X, "plan_domain", lambda p: (dict(ox=[1, 2], spin=[0.0, 0.5], basis="training compounds", n=5), "fit_y"))
    assert any("S = 3/2 is above" in c["text"] for c in X.coverage(plan))


def test_low_confidence_rule_is_surfaced(fe1, _home):
    plan = P.make_plan(fe1, -1, 4, oxidation_state=1, radical_ligand=True)
    if str(plan["rule_evaluation"].get("confidence", "")).lower().startswith("low"):
        assert any("confidence" in c["text"] for c in X.coverage(plan))


def test_nearest_references_carry_chemistry_and_flags():
    cal, names = _fe1_cal()
    near = X.nearest(cal, 11800.1, k=2)
    assert [n["name"] for n in near] == names[:2]
    assert near[0]["label"] and near[0]["ox"] is not None and "residual" in near[0]


def test_drop_one_refits_without_writing():
    cal, names = _fe1_cal()
    d = X.drop_one(cal, names[0])
    assert d["before"]["n"] == 6 and d["after"]["n"] == 5
    with pytest.raises(ValueError):
        X.drop_one(cal, "nope")


def test_analysis_rows_list_nearest_references(proj, fe1, tmp_path):
    from test_calibration_status import _outputs
    from test_project_status import _ready
    from mossbauer_dft import project as J
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    plan = J.load_version(proj, ref.entry_id)
    spec = P.method_spec_from_plan(plan, "delta")
    cal, _ = _fe1_cal()
    cal.fingerprint = spec.fingerprint()
    rows = J.analyze_version(proj, ref.entry_id)["rows"]
    rho = next(r["rho0"] for r in rows if r["rho0"] is not None)
    shifted = Calibration.fit([rho - 1 + 0.4 * i for i in range(6)], cal.delta, names=cal.names, fingerprint=spec.fingerprint(), n_boot=0)
    path = tmp_path / "cal.yaml"
    import yaml
    path.write_text(yaml.safe_dump(dict(calibration=shifted.to_dict())))
    res = J.trial_analysis(proj, ref.entry_id, calibration=str(path))
    delta_rows = [r for r in res["rows"] if r["delta"] is not None]
    assert delta_rows and all(len(r["nearest_refs"]) == 3 for r in delta_rows)


def test_cli_coverage_is_json(proj, fe1, capsys):
    ref = _add(proj, fe1, name="a")
    main(["project", "coverage", "--project", str(proj), "--entry", ref.entry_id])
    doc = json.loads(capsys.readouterr().out)
    assert doc["entry"] == ref.entry_id and "domain" in doc and isinstance(doc["warnings"], list)


def test_gui_plan_review_shows_coverage(proj, fe1, monkeypatch):
    from test_cli_gui import _app
    ref = _add(proj, fe1, name="a")
    monkeypatch.setattr(X, "plan_domain", lambda p: (dict(ox=[2, 3], spin=[0.0, 2.5], basis="training compounds", n=5), "fit_x"))
    at = _app(monkeypatch, proj, qp={"page": "Plan review", "entry": ref.entry_id, "version": "1"})
    assert any("Calibration coverage" in e.label for e in at.expander)
    assert any("Fe(+1) is outside" in w.value for w in at.warning)


def test_covered_entry_gets_a_positive_line(fe1, _home, monkeypatch):
    plan = P.make_plan(fe1, -1, 4, oxidation_state=1)
    monkeypatch.setattr(X, "plan_domain", lambda p: (dict(ox=[1, 2], spin=[0.0, 2.5], basis="training compounds", n=5), "fit_z"))
    cover = X.coverage(plan)
    assert any("within the coverage" in c["text"] for c in cover) and not any(c["level"] == "warning" and "outside" in c["text"] for c in cover)
