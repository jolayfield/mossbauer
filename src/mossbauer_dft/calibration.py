"""Isomer-shift calibration:  delta = alpha * (rho0 - C) + beta.

* OLS fit with leave-one-out (LOO) errors, bootstrap prediction intervals and outlier flags.
* A fitted ``Calibration`` carries the method fingerprint it is valid for; applying it to a calculation
  whose functional/basis/Fe-basis differ raises ``FingerprintMismatchError`` (``strict=True``, default),
  or warns loudly (``strict=False``).  Softer mismatches (grid, RI, solvent, ORCA version) always warn.

``C`` is a fixed reference density that only serves numerical conditioning (default: the rounded mean of
the training rho0, which makes alpha and beta uncorrelated); beta is the shift at rho0 = C.
"""
from __future__ import annotations
import math
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from .orca_parse import Fingerprint, OrcaResult


class CalibrationWarning(UserWarning):
    pass


class FingerprintMismatchError(ValueError):
    pass


# two-sided Student-t quantiles (level -> {df: t}); df>=121 -> normal
_T = {
    0.90: [6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812, 1.796, 1.782, 1.771, 1.761, 1.753,
           1.746, 1.740, 1.734, 1.729, 1.725, 1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697],
    0.95: [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
           2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042],
    0.99: [63.657, 9.925, 5.841, 4.604, 4.032, 3.707, 3.499, 3.355, 3.250, 3.169, 3.106, 3.055, 3.012, 2.977, 2.947,
           2.921, 2.898, 2.878, 2.861, 2.845, 2.831, 2.819, 2.807, 2.797, 2.787, 2.779, 2.771, 2.763, 2.756, 2.750],
}
_Z = {0.90: 1.645, 0.95: 1.960, 0.99: 2.576}


def t_quantile(level: float, df: int) -> float:
    """Two-sided t critical value (levels 0.90, 0.95, 0.99; tabulated)."""
    if level not in _T:
        raise ValueError("level must be one of 0.90, 0.95, 0.99")
    if df < 1:
        return float("nan")
    if df <= 30:
        return _T[level][df - 1]
    # interpolate in 1/df between df=30 and infinity
    t30, z = _T[level][29], _Z[level]
    return z + (t30 - z) * (30.0 / df)


@dataclass
class Prediction:
    rho0: float
    delta: float
    se_mean: float                  # standard error of the fitted line at rho0
    pi_analytic: tuple[float, float]
    pi_bootstrap: tuple[float, float]
    level: float
    extrapolated: bool
    warnings: list = field(default_factory=list)

    @property
    def pi(self):
        """Preferred interval: bootstrap if finite, else analytic."""
        return self.pi_bootstrap if all(map(math.isfinite, self.pi_bootstrap)) else self.pi_analytic


@dataclass
class Calibration:
    alpha: float
    beta: float
    C: float
    names: list
    rho: list
    delta: list
    fingerprint: Fingerprint = field(default_factory=Fingerprint)
    stats: dict = field(default_factory=dict)
    per_point: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    n_boot: int = 5000
    seed: int = 0

    # ---------------------------------------------------------------- fitting
    @classmethod
    def fit(cls, rho, delta, names=None, fingerprint: Fingerprint | None = None, C: float | None = None,
            n_boot: int = 5000, seed: int = 0, metadata: dict | None = None) -> "Calibration":
        rho = np.asarray(rho, float)
        delta = np.asarray(delta, float)
        n = len(rho)
        if n < 2 or n != len(delta):
            raise ValueError("need >= 2 (rho, delta) pairs of equal length")
        if np.ptp(rho) == 0:
            raise ValueError("all rho0 values are identical; cannot fit a slope")
        names = list(names) if names is not None else [f"pt{i}" for i in range(n)]
        if C is None:
            C = float(round(rho.mean()))
        x = rho - C
        A = np.vstack([x, np.ones(n)]).T
        coef, *_ = np.linalg.lstsq(A, delta, rcond=None)
        alpha, beta = float(coef[0]), float(coef[1])
        fit = A @ coef
        res = delta - fit
        dof = n - 2
        sxx = float(((x - x.mean()) ** 2).sum())
        s = math.sqrt((res ** 2).sum() / dof) if dof > 0 else float("nan")
        ss_tot = float(((delta - delta.mean()) ** 2).sum())
        r2 = 1.0 - float((res ** 2).sum()) / ss_tot if ss_tot > 0 else float("nan")
        h = 1.0 / n + (x - x.mean()) ** 2 / sxx                     # leverage
        # LOO
        loo_pred = np.full(n, np.nan)
        if n >= 3:
            for i in range(n):
                m = np.ones(n, bool)
                m[i] = False
                if np.ptp(x[m]) == 0:
                    continue
                a, b = np.polyfit(x[m], delta[m], 1)
                loo_pred[i] = a * x[i] + b
        loo_err = delta - loo_pred
        # outlier diagnostics
        with np.errstate(divide="ignore", invalid="ignore"):
            if dof > 1:
                s_i = np.sqrt(((res ** 2).sum() - res ** 2 / (1 - h)) / (dof - 1))
                ext_stud = res / (s_i * np.sqrt(1 - h))
            else:
                ext_stud = np.full(n, np.nan)
            cook = (res ** 2 / ((2 * s ** 2) if dof > 0 else np.nan)) * h / (1 - h) ** 2 if dof > 0 else np.full(n, np.nan)
        flags = []
        for i in range(n):
            f = []
            if np.isfinite(ext_stud[i]) and abs(ext_stud[i]) > 2.5:
                f.append("studentized_residual>2.5")
            if np.isfinite(cook[i]) and cook[i] > 1.0:
                f.append("cooks_distance>1")
            if h[i] > 0.9 and n > 3:
                f.append("high_leverage")
            flags.append(f)
        per_point = [dict(name=names[i], rho0=float(rho[i]), delta_exp=float(delta[i]), delta_fit=float(fit[i]),
                          residual=float(res[i]), loo_pred=None if np.isnan(loo_pred[i]) else float(loo_pred[i]),
                          loo_error=None if np.isnan(loo_err[i]) else float(loo_err[i]),
                          leverage=float(h[i]), ext_studentized=None if not np.isfinite(ext_stud[i]) else float(ext_stud[i]),
                          cooks_d=None if not np.isfinite(cook[i]) else float(cook[i]), flags=flags[i]) for i in range(n)]
        stats = dict(n=n, dof=dof, resid_sd=s, r2=r2, mae_fit=float(np.abs(res).mean()),
                     rmse_fit=float(math.sqrt((res ** 2).mean())), max_abs_resid=float(np.abs(res).max()),
                     loo_mae=float(np.nanmean(np.abs(loo_err))) if n >= 3 else float("nan"),
                     loo_rmse=float(math.sqrt(np.nanmean(loo_err ** 2))) if n >= 3 else float("nan"),
                     loo_max=float(np.nanmax(np.abs(loo_err))) if n >= 3 else float("nan"),
                     se_alpha=s / math.sqrt(sxx) if dof > 0 else float("nan"),
                     se_beta=s / math.sqrt(n) if dof > 0 else float("nan"),
                     rho_min=float(rho.min()), rho_max=float(rho.max()), sxx=sxx, xbar=float(x.mean()))
        cal = cls(alpha, beta, float(C), names, [float(v) for v in rho], [float(v) for v in delta],
                  fingerprint or Fingerprint(), stats, per_point, dict(metadata or {}), n_boot, seed)
        cal.metadata.setdefault("created_utc", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        if n == 2:
            cal.metadata.setdefault("warning", "exactly-determined two-point line: no residual, LOO or prediction interval is defined")
        elif n < 5:
            cal.metadata.setdefault("warning", f"only {n} calibration points: intervals are very uncertain")
        cal._boot = None
        return cal

    # --------------------------------------------------------------- bootstrap
    def _bootstrap_params(self):
        """(n_boot, 2) array of (alpha, beta) from paired resampling plus the residual pool."""
        if getattr(self, "_boot", None) is not None:
            return self._boot
        rng = np.random.default_rng(self.seed)
        x = np.asarray(self.rho) - self.C
        y = np.asarray(self.delta)
        n = len(x)
        out = []
        tries = 0
        while len(out) < self.n_boot and tries < 20 * self.n_boot:
            tries += 1
            idx = rng.integers(0, n, n)
            if np.ptp(x[idx]) == 0:
                continue
            a, b = np.polyfit(x[idx], y[idx], 1)
            out.append((a, b))
        params = np.array(out)
        if n >= 3:
            pool = np.array([p["loo_error"] for p in self.per_point if p["loo_error"] is not None])
        else:
            pool = np.array([])
        self._boot = (params, pool, rng)
        return self._boot

    # ---------------------------------------------------------------- applying
    def check_fingerprint(self, fp: Fingerprint | None, strict: bool = True) -> list[str]:
        """Compare with a calculation's fingerprint; raise (strict) or warn on a hard mismatch."""
        msgs = []
        if fp is None:
            m = "calculation fingerprint not supplied: cannot verify that the calibration applies to this method/basis/grid"
            warnings.warn(m, CalibrationWarning, stacklevel=3)
            return [m]
        hard, soft = self.fingerprint.compare(fp)
        if hard:
            m = "METHOD MISMATCH between calibration and calculation (" + "; ".join(hard) + ")"
            if strict:
                raise FingerprintMismatchError(m)
            warnings.warn("LOUD: " + m + " -- the predicted shift is NOT meaningful", CalibrationWarning, stacklevel=3)
            msgs.append(m)
        for s in soft:
            m = "fingerprint difference (soft): " + s
            warnings.warn(m, CalibrationWarning, stacklevel=3)
            msgs.append(m)
        return msgs

    def predict(self, rho0, fingerprint: Fingerprint | None = None, level: float = 0.95, strict: bool = True,
                sigma_floor: float = 0.0) -> list[Prediction] | Prediction:
        """Predict isomer shift(s) with uncertainty.

        Intervals are PREDICTION intervals (a new compound's shift), not confidence intervals of the line.
        analytic: t-based, needs n>=3.  bootstrap: paired resampling of the calibration set; each draw's line
        is evaluated at rho0 and perturbed by a residual sampled from the leave-one-out errors (n>=3).
        ``sigma_floor`` (mm/s) is added in quadrature to the analytic sd and to the bootstrap draws.
        """
        scalar = np.isscalar(rho0)
        arr = np.atleast_1d(np.asarray(rho0, float))
        wmsgs = self.check_fingerprint(fingerprint, strict=strict)
        st = self.stats
        n, dof = st["n"], st["dof"]
        params, pool, rng = self._bootstrap_params()
        out = []
        for r in arr:
            x0 = r - self.C
            d = self.alpha * x0 + self.beta
            w = list(wmsgs)
            ext = bool(r < st["rho_min"] or r > st["rho_max"])
            if ext:
                m = f"rho0={r:.3f} is outside the calibration range [{st['rho_min']:.3f}, {st['rho_max']:.3f}] (extrapolation)"
                warnings.warn(m, CalibrationWarning, stacklevel=2)
                w.append(m)
            if dof > 0:
                se_mean = st["resid_sd"] * math.sqrt(1.0 / n + (x0 - st["xbar"]) ** 2 / st["sxx"])
                sd_pred = math.sqrt(st["resid_sd"] ** 2 + se_mean ** 2 + sigma_floor ** 2)
                tq = t_quantile(level, dof)
                pa = (d - tq * sd_pred, d + tq * sd_pred)
            else:
                se_mean = float("nan")
                pa = (float("nan"), float("nan"))
                w.append("n=2: prediction interval undefined (exactly determined line)")
            if len(params) and len(pool):
                noise = rng.choice(pool, len(params))
                if sigma_floor:
                    noise = noise + rng.normal(0, sigma_floor, len(params))
                draws = params[:, 0] * x0 + params[:, 1] + noise
                lo, hi = np.quantile(draws, [(1 - level) / 2, 1 - (1 - level) / 2])
                pb = (float(lo), float(hi))
            else:
                pb = (float("nan"), float("nan"))
            out.append(Prediction(float(r), float(d), se_mean, pa, pb, level, ext, w))
        return out[0] if scalar else out

    def predict_result(self, result: OrcaResult, **kw) -> list[Prediction]:
        """Predict delta for every Fe in a parsed ORCA output (fingerprint taken from the output itself)."""
        if not result.ok:
            warnings.warn(f"{result.path or 'result'}: calculation not OK (termination/SCF); prediction may be unreliable", CalibrationWarning, stacklevel=2)
        rhos = [s.rho0 for s in result.fe_sites if s.rho0 is not None]
        if not rhos:
            raise ValueError("no rho(0) found in result")
        return self.predict(rhos, fingerprint=result.fingerprint, **kw)

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return dict(model="delta = alpha*(rho0 - C) + beta", alpha=self.alpha, beta=self.beta, C=self.C,
                    fingerprint=self.fingerprint.to_dict(), names=self.names, rho0=self.rho, delta=self.delta,
                    stats=_clean(self.stats), per_point=_clean(self.per_point), metadata=_clean(self.metadata),
                    n_boot=self.n_boot, seed=self.seed)

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        return cls(alpha=d["alpha"], beta=d["beta"], C=d["C"], names=list(d.get("names", [])), rho=list(d["rho0"]),
                   delta=list(d["delta"]), fingerprint=Fingerprint.from_dict(d.get("fingerprint", {})),
                   stats=dict(d.get("stats", {})), per_point=list(d.get("per_point", [])),
                   metadata=dict(d.get("metadata", {})), n_boot=int(d.get("n_boot", 5000)), seed=int(d.get("seed", 0)))

    def save_yaml(self, path) -> None:
        import yaml
        with open(path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, sort_keys=False)

    @classmethod
    def load_yaml(cls, path) -> "Calibration":
        import yaml
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f))


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return float(o)
    if isinstance(o, np.integer):
        return int(o)
    return o


def fit_calibration(rho, delta, **kw) -> Calibration:
    return Calibration.fit(rho, delta, **kw)
