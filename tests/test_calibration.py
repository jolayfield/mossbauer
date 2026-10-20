import math
import warnings
import numpy as np
import pytest
from mossbauer_dft.calibration import (Calibration, CalibrationWarning, FingerprintMismatchError, fit_calibration,
                                       t_quantile)
from mossbauer_dft.orca_parse import Fingerprint, parse_orca_text
from orca_synthetic import MN15, Site, orca_output

TPSSH = Fingerprint("TPSSh", "def2-TZVP", "CP(PPP)", "DefGrid3", "7", "26", "NoRIJCOSX", "", "ORCA 6.1.0")


def test_ols_recovers_known_line():
    rng = np.random.default_rng(1)
    x = np.linspace(11815, 11821, 12)
    y = -0.35 * (x - 11818) + 0.5 + rng.normal(0, 0.02, x.size)
    c = fit_calibration(x, y, C=11818.0, n_boot=500)
    assert c.alpha == pytest.approx(-0.35, abs=0.02) and c.beta == pytest.approx(0.5, abs=0.02)
    assert c.stats["loo_mae"] < 0.05 and c.stats["r2"] > 0.99
    assert c.stats["loo_mae"] >= c.stats["mae_fit"]


def test_two_point_line_predicts_and_flags_extrapolation():
    c = fit_calibration([11819.0, 11820.5], [0.70, 0.25], fingerprint=TPSSH)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CalibrationWarning)
        outside = c.predict(11818.6, fingerprint=TPSSH)
        inside = c.predict(11819.8, fingerprint=TPSSH)
    assert outside.delta == pytest.approx(0.82, abs=1e-9) and inside.delta == pytest.approx(0.46, abs=1e-9)
    assert outside.extrapolated and not inside.extrapolated
    assert math.isnan(inside.pi_analytic[0]) and any("n=2" in w for w in inside.warnings)
    assert "warning" in c.metadata


def test_prediction_intervals_cover_and_widen_with_extrapolation():
    rng = np.random.default_rng(3)
    x = np.linspace(11816, 11820, 9)
    y = -0.4 * (x - 11818) + 0.4 + rng.normal(0, 0.03, x.size)
    c = fit_calibration(x, y, fingerprint=TPSSH, n_boot=2000, seed=5)
    mid = c.predict(11818.0, fingerprint=TPSSH)
    with pytest.warns(CalibrationWarning):
        far = c.predict(11823.0, fingerprint=TPSSH)
    for p in (mid, far):
        assert p.pi_analytic[0] < p.delta < p.pi_analytic[1]
        assert p.pi_bootstrap[0] < p.delta < p.pi_bootstrap[1]
    assert (far.pi_analytic[1] - far.pi_analytic[0]) > (mid.pi_analytic[1] - mid.pi_analytic[0])
    # bootstrap reproducible
    c2 = fit_calibration(x, y, fingerprint=TPSSH, n_boot=2000, seed=5)
    assert c2.predict(11818.0, fingerprint=TPSSH).pi_bootstrap == mid.pi_bootstrap


def test_outlier_flag():
    x = np.linspace(11816, 11821, 8)
    y = -0.4 * (x - 11818) + 0.4
    y = y + np.array([0.01, -0.01, 0.0, 0.01, -0.01, 0.0, 0.01, 0.0])
    y[3] += 0.6                                  # gross outlier
    c = fit_calibration(x, y, names=list("abcdefgh"))
    flagged = [p["name"] for p in c.per_point if p["flags"]]
    assert flagged == ["d"]


def test_fingerprint_mismatch_refuses_or_warns():
    c = fit_calibration([11819.12, 11820.55, 11819.8], [0.68, 0.29, 0.5], fingerprint=TPSSH)
    mn15 = parse_orca_text(orca_output(MN15))
    with pytest.raises(FingerprintMismatchError):
        c.predict_result(mn15)
    with pytest.warns(CalibrationWarning, match="LOUD"):
        c.predict(11819.9, fingerprint=mn15.fingerprint, strict=False)
    # basis mismatch refused as well
    other = Fingerprint("TPSSh", "def2-SVP", "CP(PPP)", "DefGrid3", "7", "26", "NoRIJCOSX")
    with pytest.raises(FingerprintMismatchError):
        c.predict(11819.9, fingerprint=other)
    # soft mismatch (grid) only warns
    soft = Fingerprint("TPSSh", "def2-TZVP", "CP(PPP)", "DefGrid2", "7", "26", "NoRIJCOSX")
    with pytest.warns(CalibrationWarning, match="soft"):
        c.predict(11819.9, fingerprint=soft)
    # missing fingerprint warns
    with pytest.warns(CalibrationWarning, match="not supplied"):
        c.predict(11819.9)


def test_apply_to_parsed_output_and_roundtrip(tmp_path):
    rho, delta = [11818.40, 11819.10, 11819.85, 11820.60], [0.88, 0.66, 0.41, 0.17]
    c = fit_calibration(rho, delta, names=["r1", "r2", "r3", "r4"], fingerprint=TPSSH, n_boot=500)
    assert c.stats["n"] == 4
    (pred,) = c.predict_result(parse_orca_text(orca_output(sites=(Site(rho0=11819.5),))))
    assert pred.delta == pytest.approx(c.predict(11819.5, fingerprint=TPSSH).delta)
    path = tmp_path / "cal.yaml"
    c.save_yaml(path)
    c2 = Calibration.load_yaml(path)
    assert c2.alpha == pytest.approx(c.alpha) and c2.fingerprint == c.fingerprint
    assert c2.predict(11819.5, fingerprint=TPSSH).delta == pytest.approx(c.predict(11819.5, fingerprint=TPSSH).delta)


def test_t_quantile_and_validation():
    assert t_quantile(0.95, 2) == pytest.approx(4.303) and 1.96 < t_quantile(0.95, 60) < 2.02
    with pytest.raises(ValueError):
        fit_calibration([1.0], [1.0])
    with pytest.raises(ValueError):
        fit_calibration([1.0, 1.0], [0.1, 0.2])


def test_two_point_calibration_is_flagged():
    cal = Calibration.fit([11819.4, 11820.8], [0.64, 0.33], names=["a", "b"], n_boot=0)
    assert "two-point" in cal.metadata["warning"]
