"""The calibration behind each number: its chemical coverage, the nearest references to a prediction, and drop-one refits
(second-pass idea 5; decisions D57-D58). Everything here reads existing data and writes nothing."""
from __future__ import annotations

import re
from fractions import Fraction
from functools import lru_cache

from . import references as R


@lru_cache(maxsize=1)
def compound_meta() -> dict:
    """Bundled reference compounds by id: name, formal oxidation state and spin S (None when not recorded)."""
    out = {}
    for name in ("fe1_calib18", "mcwilliams2017"):
        try:
            d = R.refset_definition(name)
        except Exception:  # noqa: BLE001 -- a missing bundled set just means less context
            continue
        for c in d.get("compounds", []):
            out.setdefault(c["id"], dict(name=c.get("name") or c["id"], ox=_ox(c.get("formal_ox_state")),
                                         spin=_num(c.get("spin_S")), refset=d["id"]))
    return out


def _ox(value):
    m = re.fullmatch(r"\s*([+-]?\d+)\s*", str(value)) if value is not None else None
    return int(m.group(1)) if m else None


def _num(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _range_text(text):
    """'+2 to +4' -> {2, 3, 4}; '0 to +6 (...)' -> {0..6}; None when unparseable."""
    nums = [int(x) for x in re.findall(r"[+-]?\d+", str(text or "").split("(")[0])]
    return set(range(min(nums), max(nums) + 1)) if len(nums) >= 2 else ({nums[0]} if nums else None)


def _spin_max(text):
    fracs = [float(Fraction(x)) for x in re.findall(r"\d+/\d+|\d+(?:\.\d+)?", str(text or ""))]
    return max(fracs) if fracs else None


def domain(entry: dict | None, names=None) -> dict:
    """What a calibration was fitted on: oxidation states, spins, and where that came from.
    From its training compounds when they are known bundled references, else from the entry's recorded ranges."""
    meta = compound_meta()
    known = [meta[n] for n in (names or []) if n in meta]
    if known and len(known) == len(names):
        return dict(ox=sorted({k["ox"] for k in known if k["ox"] is not None}),
                    spin=sorted({k["spin"] for k in known if k["spin"] is not None}), basis="training compounds",
                    n=len(known))
    if entry and (entry.get("fe_oxidation_range") or entry.get("spin_range")):
        ox = _range_text(entry.get("fe_oxidation_range"))
        smax = _spin_max(entry.get("spin_range"))
        return dict(ox=sorted(ox) if ox else [], spin=[0.0, smax] if smax is not None else [], basis="published range",
                    n=entry.get("n_training"))
    return dict(ox=[], spin=[], basis="not recorded", n=None)


def plan_domain(plan: dict) -> tuple[dict, str]:
    """The domain of the calibration a plan will use (lookup entry, or the reference set it fits), with a label."""
    c = plan.get("calibration") or {}
    if c.get("strategy") == "lookup" and (c.get("lookup") or {}).get("id"):
        e = R.get_calibration_entry(c["lookup"]["id"])
        names = (e or {}).get("calibration", {}).get("names") if (e or {}).get("source") == "project_fit" else None
        return domain(e, names), c["lookup"]["id"]
    if c.get("strategy") == "compute_refset" and c.get("refset"):
        rid = c["refset"]["id"]
        try:
            ready, _ = R.resolve_refset(rid)
        except Exception:  # noqa: BLE001
            return domain(None), rid
        return domain(None, [x["id"] for x in ready]), rid
    return domain(None), c.get("strategy") or "none"


def coverage(plan: dict) -> list[dict]:
    """Warnings to read before approving: entry chemistry outside the calibration's coverage, and low-confidence or
    extrapolating method rules. Each is {level: 'warning'|'info', text}."""
    out = []
    hints = (plan.get("inputs") or {}).get("hints") or {}
    ox = hints.get("oxidation_state")
    mult = (plan.get("inputs") or {}).get("multiplicity")
    spin = (int(mult) - 1) / 2 if mult else None
    dom, label = plan_domain(plan)
    if dom["basis"] == "not recorded":
        out.append(dict(level="info", text=f"Calibration {label}: its chemical coverage is not recorded, so this entry "
                                           "cannot be checked against it."))
    else:
        if ox is not None and dom["ox"] and ox not in dom["ox"]:
            out.append(dict(level="warning", text=f"Fe({ox:+d}) is outside the oxidation states calibration {label} was "
                            f"fitted on ({', '.join(f'{o:+d}' for o in dom['ox'])}; {dom['basis']}): the predicted δ "
                            "extrapolates in chemistry, not only in ρ(0). Consider a project fit with similar references."))
        if spin is not None and dom["spin"] and spin > max(dom["spin"]):
            out.append(dict(level="warning", text=f"S = {Fraction(spin).limit_denominator()} is above the spins in calibration "
                            f"{label} (up to {Fraction(max(dom['spin'])).limit_denominator()}; {dom['basis']})."))
        if ox is None:
            out.append(dict(level="info", text="Oxidation state not given: coverage of the calibration is not checked."))
        elif not out:
            out.append(dict(level="info", text=f"Fe({ox:+d})" + (f", S = {Fraction(spin).limit_denominator()}" if spin is not None else "")
                            + f" are within the coverage of calibration {label} ({dom['basis']})."))
    rule = plan.get("rule_evaluation") or {}
    conf = str(rule.get("confidence") or "")
    if conf.lower().startswith("low"):
        out.append(dict(level="warning", text=f"Method rule {rule.get('selected_rule')} confidence: {conf}"))
    notes = rule.get("notes")
    for n in ([notes] if isinstance(notes, str) else notes or []):
        if "extrapolat" in str(n).lower():
            out.append(dict(level="warning", text=f"Method rule {rule.get('selected_rule')}: {n}"))
    return out


def nearest(cal, rho0, k: int = 3) -> list[dict]:
    """The ``k`` calibration points closest in ρ(0), with their residual, diagnostics and chemistry (when known)."""
    if cal is None or rho0 is None or not getattr(cal, "per_point", None):
        return []
    meta = compound_meta()
    pts = sorted(cal.per_point, key=lambda p: abs(p["rho0"] - rho0))[:k]
    return [dict(name=p["name"], label=(meta.get(p["name"]) or {}).get("name", p["name"]), d_rho0=round(p["rho0"] - rho0, 4),
                 delta_exp=p["delta_exp"], residual=p.get("residual"), loo_error=p.get("loo_error"),
                 flags=list(p.get("flags") or []), ox=(meta.get(p["name"]) or {}).get("ox"),
                 spin=(meta.get(p["name"]) or {}).get("spin")) for p in pts]


def drop_one(cal, name: str) -> dict:
    """Refit the same points without ``name`` (preview only): slope, MAE and LOO before and after."""
    from .calibration import Calibration
    keep = [i for i, n in enumerate(cal.names) if n != name]
    if len(keep) == len(cal.names):
        raise ValueError(f"{name!r} is not a calibration point")
    if len(keep) < 3:
        raise ValueError("a refit needs at least three remaining points")
    new = Calibration.fit([cal.rho[i] for i in keep], [cal.delta[i] for i in keep], names=[cal.names[i] for i in keep],
                          fingerprint=cal.fingerprint, C=cal.C, n_boot=0)

    def summary(c):
        s = c.stats
        return dict(alpha=c.alpha, beta=c.beta, n=s["n"], mae=s.get("mae_fit"), loo_mae=s.get("loo_mae"), r2=s.get("r2"))
    return dict(dropped=name, before=summary(cal), after=summary(new))
