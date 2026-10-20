"""A rule's uncertainty_inflation_mm_s widens the delta prediction intervals of analyses made under it (R08 nitrosyls)."""
import copy

from mossbauer_dft import plan as P, project as J, workflow as W
from mossbauer_dft.structure import Structure
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _ready  # noqa: F401
from test_calibration_status import _outputs

CPPP = "mcwilliams2017_b3lyp_cppp"
NITROSYL = Structure.from_xyz_text("4\n\nFe 0 0 0\nN 1.7 0 0\nO 2.85 0 0\nCl -2.2 0 0\n")


def test_R08_plan_records_its_inflation_and_other_rules_none(fe1):
    assert P.make_plan(NITROSYL, 0, 3, oxidation_state=1)["methods"]["delta"]["uncertainty_inflation_mm_s"] == 0.2
    assert P.make_plan(fe1, -1, 4, oxidation_state=1)["methods"]["delta"]["uncertainty_inflation_mm_s"] == 0.0


def test_plan_saved_before_the_field_falls_back_to_its_rule():
    plan = P.make_plan(NITROSYL, 0, 3, oxidation_state=1)
    del plan["methods"]["delta"]["uncertainty_inflation_mm_s"]
    assert P.delta_uncertainty_inflation(plan) == 0.2


def test_inflation_widens_analysis_intervals_in_quadrature(proj, fe1):
    ref = _ready(proj, fe1)
    _outputs(proj, ref)
    plan = J.load_version(proj, ref.entry_id, ref.version)
    assert P.delta_uncertainty_inflation(plan) == 0.0
    inflated = copy.deepcopy(plan)
    inflated["methods"]["delta"]["uncertainty_inflation_mm_s"] = 0.2
    out = ref.path / "outputs"
    base = [r for r in W.analyze(plan, out, calibration=CPPP)["rows"] if r["delta"] is not None]
    res = W.analyze(inflated, out, calibration=CPPP)
    wide = [r for r in res["rows"] if r["delta"] is not None]
    assert base and len(base) == len(wide)
    for b, w in zip(base, wide):
        assert w["delta"] == b["delta"]
        half_b, half_w = (b["delta_hi"] - b["delta_lo"]) / 2, (w["delta_hi"] - w["delta_lo"]) / 2
        assert half_w > half_b + 0.1
    assert any("0.2 mm/s" in n for n in res["notes"])
