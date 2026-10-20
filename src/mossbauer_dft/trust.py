"""Typed trust flags for analysis rows (decisions D50-D51).

A flag is ``{code, severity, property, text}``. ``severity`` is ``caution`` (shown, keeps the row's calibration status and
its place in agreement metrics) or ``blocking`` (the value must not be read as a prediction). ``property`` says which value
the flag concerns: ``delta``, ``deq`` or ``all``. Analysis computes flags once; Results, Study, reports and the CLI read them.
"""
from __future__ import annotations

CAUTION, BLOCKING = "caution", "blocking"

SPIN_CONTAMINATION_REL = 0.10
SPIN_CONTAMINATION_MIN = 0.10

# method_rules.yaml R00: no MN15 (or other >40% exact-exchange functionals) for DeltaEQ of Fe(I); M06-2X and SVWN5 were the
# worst performers in Bochevarov 2010.
FE1_DEQ_AVOID = {"MN15", "M062X", "M06-2X", "BHANDHLYP", "SVWN5", "SVWN"}

_RULES = [
    ("calibration refused", "calibration_refused", BLOCKING),
    ("IMPLAUSIBLE", "implausible", BLOCKING),
    ("METHOD MISMATCH", "method_mismatch", BLOCKING),
    ("LOUD:", "method_mismatch", BLOCKING),
    ("(extrapolation)", "extrapolated", CAUTION),
    ("prediction interval undefined", "two_point_line", CAUTION),
    ("two-point line", "two_point_line", CAUTION),
    ("calibration points: intervals are very uncertain", "few_calibration_points", CAUTION),
    ("fingerprint difference (soft)", "soft_fingerprint_difference", CAUTION),
    ("fingerprint not supplied", "no_fingerprint", CAUTION),
    ("no calibration available", "no_calibration", CAUTION),
    ("nearest calibration point", "near_flagged_reference", CAUTION),
]


def flag(code: str, severity: str, prop: str, text: str) -> dict:
    return dict(code=code, severity=severity, property=prop, text=text)


def classify(text: str, prop: str = "delta") -> dict:
    for needle, code, severity in _RULES:
        if needle in text:
            return flag(code, severity, prop, text)
    return flag("unclassified", BLOCKING, prop, text)


def from_note(note) -> list[dict]:
    """Flags for a free-text ``delta_note`` (analyses saved before typed flags, or a hand-edited note)."""
    return [classify(part.strip()) for part in str(note or "").split("; ") if part.strip()]


def note(flags, prop: str = "delta") -> str:
    """The legacy ``delta_note`` text: every flag concerning ``prop`` alone (``all`` flags are not part of the note)."""
    return "; ".join(f["text"] for f in flags if f.get("property") == prop)


def for_row(row: dict) -> list[dict]:
    """A saved row's flags. The note is authoritative for delta concerns: when it no longer matches the typed flags (an
    older analysis, or a note edited by hand) the delta flags are re-derived from it, other flags are kept."""
    typed = row.get("trust_flags")
    text = str(row.get("delta_note") or "")
    if not isinstance(typed, list):
        return from_note(text)
    if note(typed) == text:
        return typed
    return from_note(text) + [f for f in typed if f.get("property") != "delta"]


def blocked(flags, prop: str) -> bool:
    return any(f.get("severity") == BLOCKING and f.get("property") in (prop, "all") for f in flags)


def spin_contamination(s2, s2_ideal, broken_symmetry: bool) -> dict | None:
    if broken_symmetry or s2 is None or s2_ideal is None:
        return None
    dev = abs(s2 - s2_ideal)
    if dev > max(SPIN_CONTAMINATION_MIN, SPIN_CONTAMINATION_REL * s2_ideal):
        return flag("spin_contamination", CAUTION, "all",
                    f"spin contamination: <S^2>={s2:.3f} vs ideal {s2_ideal:.3f} (deviation {dev:.3f})")
    return None


def rule_flags(functional, oxidation_state) -> list[dict]:
    fn = str(functional or "").upper().replace(" ", "")
    if oxidation_state == 1 and fn in FE1_DEQ_AVOID:
        return [flag("rule_avoided_functional", CAUTION, "deq",
                     f"{functional} is avoided for DeltaEQ of Fe(I) (method rule R00): treat |DeltaEQ| as unreliable")]
    return []


def label(flags, *, plain: bool = False) -> str:
    """Short table text, blocking first: '⛔ calibration refused · ⚠ extrapolated', or with ``plain`` (for files that
    leave the app) 'blocking: calibration refused · caution: extrapolated'."""
    block, caution = ("blocking: ", "caution: ") if plain else ("⛔ ", "⚠ ")
    order = sorted(flags, key=lambda f: f.get("severity") != BLOCKING)
    return " · ".join((block if f.get("severity") == BLOCKING else caution) + str(f.get("code", "")).replace("_", " ") for f in order)
