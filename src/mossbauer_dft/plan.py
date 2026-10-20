"""Plan-then-approve workflow.

``make_plan`` turns a structure + charge/multiplicity + hints into a reviewable ``plan.yaml`` (status ``draft``):

* method recommendation by evaluating ``method_rules.yaml`` (matched rule, runners-up/conflicts, justification, citations);
* calibration strategy: published lookup (verified + protocol matched only, unless explicitly overridden), computing a
  reference set at the identical protocol (default), or user-supplied experimental references;
* job list (geometry opt -> optional freq -> property SPs; broken-symmetry variants; optional highfield_sp);
* core-hour estimate from a stated scaling model.

``approve_plan`` marks it approved and records approver, time and a content hash.  ``require_approved`` is the gate used by
``workflow.write_inputs`` and ``references.refset_setup``: it refuses a draft plan or one modified after approval.

Everything derived in the plan is a deterministic function of ``inputs`` (structure, charge, multiplicity, hints, overrides),
so editing means changing ``inputs`` and re-running ``make_plan`` (``update_plan``); any change resets the status to draft.
"""
from __future__ import annotations
import copy
import getpass
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import yaml

from . import __version__
from . import data as _data
from . import references as R
from .orca_input import BrokenSymmetry, MethodSpec, mossbauer_method
from .structure import Structure, read_structure, read_structure_text

SCHEMA = "1.0"


class PlanError(ValueError):
    pass


class NotApprovedError(PlanError):
    pass


# fraction of exact exchange for functionals relevant here (for the >40 % rule of R00)
EXACT_EXCHANGE = {"B3LYP": 0.20, "B3LYP*": 0.15, "TPSSH": 0.10, "PBE0": 0.25, "BP86": 0.0, "TPSS": 0.0, "RPBE": 0.0, "OLYP": 0.0,
                  "MN15": 0.44, "M06-2X": 0.54, "M06": 0.27, "BHANDHLYP": 0.50, "SVWN5": 0.0, "BLYP": 0.0, "PBE": 0.0, "O3LYP": 0.116}
KNOWN_FUNCTIONALS = ["B3LYP*", "B3LYP", "TPSSh", "TPSS", "BP86", "RPBE", "PBE0", "OLYP", "MN15", "M06-2X", "BHandHLYP"]


# ----------------------------------------------------------------------------- system description
@dataclass
class SystemInfo:
    structure: Structure
    charge: int
    multiplicity: int
    oxidation_state: int | None = None
    radical_ligand: bool | None = None
    solvent: str | None = None
    temperature_K: float | None = None
    nitrosyl: bool | None = None
    name: str = "molecule"

    @property
    def spin(self) -> Fraction:
        return Fraction(self.multiplicity - 1, 2)

    @property
    def n_fe(self) -> int:
        return len(self.structure.fe_indices)

    @property
    def n_atoms(self) -> int:
        return len(self.structure)

    def detect_nitrosyl(self) -> bool:
        """Fe-N-O unit: an N atom within 2.1 A of Fe and an O within 1.35 A of that N."""
        import math
        s = self.structure
        for i in s.fe_indices:
            for j, e in enumerate(s.symbols):
                if e.capitalize() == "N" and math.dist(s.coords[i], s.coords[j]) < 2.1:
                    for k, e2 in enumerate(s.symbols):
                        if e2.capitalize() == "O" and math.dist(s.coords[j], s.coords[k]) < 1.35:
                            return True
        return False

    @property
    def has_nitrosyl(self) -> bool:
        return self.detect_nitrosyl() if self.nitrosyl is None else bool(self.nitrosyl)


def _frac(x) -> Fraction:
    return Fraction(str(x))


def _spin_in(S: Fraction, lst) -> bool:
    return any(S == _frac(x) for x in lst)


# ----------------------------------------------------------------------------- rule matching
def _m_R01(s: SystemInfo):
    why = []
    if s.oxidation_state != 1:
        return False, ["oxidation state is not 1 (or not provided)"]
    if s.n_fe != 1:
        why.append("not mononuclear")
    if not _spin_in(s.spin, ["1/2", "3/2"]):
        why.append(f"spin S={s.spin} not in {{1/2, 3/2}}")
    if s.radical_ligand:
        why.append("radical ligand suspected (R02 applies instead)")
    if s.n_atoms > 150:
        why.append(">150 atoms")
    if s.has_nitrosyl:
        why.append("nitrosyl present")
    return (not why), why or ["Fe(I), S in {1/2, 3/2}, mononuclear, no suspected radical ligand, <=150 atoms"]


def _m_R02(s: SystemInfo):
    if not s.radical_ligand:
        return False, ["no radical/redox-active ligand suspected"]
    if s.oxidation_state not in (None, 0, 1, 2):
        return False, ["oxidation state outside 0..2"]
    return True, ["radical ligand suspected with ambiguous low Fe oxidation state"]


def _simple(ox, spins, mononuclear=True):
    def f(s: SystemInfo):
        if s.oxidation_state is None:
            return False, ["oxidation state not provided"]
        okox = s.oxidation_state in ox if not isinstance(ox, str) else True
        if not okox:
            return False, [f"oxidation state {s.oxidation_state} not in {sorted(ox)}"]
        if spins is not None:
            if s.n_fe != 1:
                return False, ["spin criteria evaluated for mononuclear complexes only"]
            if not _spin_in(s.spin, spins):
                return False, [f"spin S={s.spin} not in {spins}"]
        return True, [f"Fe({s.oxidation_state}), S={s.spin}"]
    return f


def _m_R07(s: SystemInfo):
    if s.oxidation_state in (4, 5, 6):
        return True, [f"Fe({s.oxidation_state})"]
    return False, ["oxidation state not in 4..6"]


def _m_R08(s: SystemInfo):
    return (True, ["Fe-N-O unit " + ("declared" if s.nitrosyl else "detected from geometry")]) if s.has_nitrosyl else (False, ["no nitrosyl"])


def _m_R09(s: SystemInfo):
    return (True, [f"{s.n_fe} Fe atoms"]) if s.n_fe >= 2 else (False, ["single Fe"])


def _m_R10(s: SystemInfo):
    return (True, [f"{s.n_atoms} atoms > 150"]) if s.n_atoms > 150 else (False, [f"{s.n_atoms} atoms <= 150"])


MATCHERS = {
    "R01_FeI_lowcoordinate": _m_R01, "R02_FeI_radical_or_BS": _m_R02,
    "R03_Fe0_organometallic": _simple({0}, ["0", "1"]), "R04_FeII_highspin": _simple({2}, ["2"]),
    "R05_FeII_lowspin_and_intermediate": _simple({2}, ["0", "1"]), "R06_FeIII": _simple({3}, ["1/2", "3/2", "5/2"]),
    "R07_highvalent_FeIV_to_FeVI": _m_R07, "R08_nitrosyl": _m_R08, "R09_polynuclear_FeS_and_oxo_dimers": _m_R09,
    "R10_large_systems": _m_R10,
}
BS_RULES = {"R02_FeI_radical_or_BS", "R09_polynuclear_FeS_and_oxo_dimers"}


@dataclass
class RuleEvaluation:
    selected: dict
    matched: list = field(default_factory=list)       # [{id, priority, reasons}]
    rejected: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    global_constraints: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def evaluate_rules(system: SystemInfo, rules: dict | None = None) -> RuleEvaluation:
    rules = rules or _data.load_method_rules()
    if rules is None:
        raise PlanError("method_rules.yaml is not bundled")
    by_id = {r["id"]: r for r in rules["rules"]}
    matched, rejected = [], []
    for rid, fn in MATCHERS.items():
        if rid not in by_id:
            continue
        ok, why = fn(system)
        (matched if ok else rejected).append(dict(id=rid, priority=by_id[rid]["priority"], reasons=why))
    warns = []
    if system.oxidation_state is None:
        warns.append("No Fe oxidation state given: oxidation-state-specific rules (R01-R07) cannot match; the fallback may be used. "
                     "Provide the oxidation state for a specific recommendation.")
    if system.radical_ligand is None and system.oxidation_state in (None, 0, 1, 2):
        warns.append("Radical-ligand status not given (treated as 'not suspected'). Redox-active ligands (alkynes, tetraazadienes, "
                     "N-heterocycles) call for rule R02 (broken-symmetry variants).")
    matched.sort(key=lambda m: -m["priority"])
    if matched:
        sel_id = matched[0]["id"]
        sel_reasons = matched[0]["reasons"]
    else:
        sel_id, sel_reasons = "R99_default", ["no specific rule matched"]
    conflicts = []
    for m in matched[1:]:
        conflicts.append(f"{m['id']} (priority {m['priority']}) also matches; {sel_id} wins by priority")
    for m in matched[:1]:
        for o in matched[1:]:
            if o["priority"] == m["priority"]:
                conflicts.append(f"TIE between {m['id']} and {o['id']} at priority {m['priority']}; review")
    sel = copy.deepcopy(by_id[sel_id])
    sel["match_reasons"] = sel_reasons
    gc = by_id["R00_global_constraints"]
    return RuleEvaluation(sel, matched, rejected, conflicts, list(gc["statement"]), warns)


def _first_functional(text: str) -> str | None:
    best, pos = None, 10 ** 9
    t = str(text)
    for f in KNOWN_FUNCTIONALS:
        for m in re.finditer(re.escape(f), t, flags=re.I):
            # avoid matching B3LYP inside B3LYP*
            if m.start() < pos:
                pos, best = m.start(), f
            break
    return best


def check_property_functional(func: str, what: str, oxidation_state=None, forbidden: list | None = None):
    """Hard rule: no MN15 (or any >40 % exact-exchange functional) for properties; also anything a rule forbids."""
    f = func.upper()
    if f in {x.upper() for x in (forbidden or [])} or f == "MN15" or EXACT_EXCHANGE.get(f, 0.0) > 0.40:
        raise PlanError(f"{func} must not be used for {what}: >40% exact exchange / explicitly forbidden "
                        f"(MN15 overestimates |DeltaEQ| of Fe(I) in project benchmarks). "
                        f"MN15 remains allowed for GEOMETRY only.")


# ----------------------------------------------------------------------------- methods
FE_BASIS_BY_RULE_TOKEN: dict[str, str | None] = {"CP(PPP)": "CP(PPP)", "def2-TZVP": None}


def _tok(x):
    t = str(x or "").split()
    return t[0] if t else None


def recommend_methods(sel: dict, system: SystemInfo, overrides: dict) -> dict:
    d, q = sel["delta"], sel["deltaEQ"]
    proto = d.get("single_point_protocol", {})
    if d.get("fe_basis") not in FE_BASIS_BY_RULE_TOKEN:
        raise PlanError(f"rule {sel['id']}: delta.fe_basis must be one of {sorted(FE_BASIS_BY_RULE_TOKEN)}, got {d.get('fe_basis')!r}")
    fe_basis = FE_BASIS_BY_RULE_TOKEN[d["fe_basis"]]
    basis = proto.get("basis_other") or "def2-TZVP"
    basis = "def2-TZVP" if "def2-TZVP" in basis else basis
    delta_f = overrides.get("delta_functional") or d["functional"]
    deq_f = overrides.get("deq_functional") or _first_functional(q["functional"]) or delta_f
    if sel["id"].startswith("R06") and not overrides.get("deq_functional") and system.n_fe == 1 and system.spin == Fraction(3, 2):
        deq_f = "B3LYP"
    cross = overrides.get("crosscheck_functional", _first_functional(q.get("crosscheck_functional", "")) if q.get("crosscheck_functional") else None)
    if cross == "none":
        cross = None
    if cross and cross.upper() == deq_f.upper():
        cross = None
    if not cross and not overrides.get("crosscheck_functional") and q.get("crosscheck_functional"):
        c2 = _first_functional(q["crosscheck_functional"])
        cross = c2 if c2 and c2.upper() != deq_f.upper() else None
    forb = list(q.get("forbidden", []))
    for f, what in ((delta_f, "the isomer-shift property calculation"), (deq_f, "the DeltaEQ property calculation")):
        check_property_functional(f, what, system.oxidation_state, forb)
    if cross:
        check_property_functional(cross, "the DeltaEQ cross-check calculation", system.oxidation_state, forb)
    return dict(
        delta=dict(functional=delta_f, basis=basis, fe_basis=fe_basis, grid="DefGrid3", special_grid_atoms=26, special_grid_int_acc=7,
                   rijcosx=False, scf_convergence="TightSCF", calibration_entry=_tok(d.get("calibration_entry")),
                   fallback_calibration_entry=_tok(d.get("fallback_calibration_entry")),
                   uncertainty_inflation_mm_s=float(d.get("uncertainty_inflation_mm_s") or 0.0)),
        deq=dict(functional=deq_f, crosscheck_functional=cross, forbidden=forb, source_text=q["functional"],
                 expected_error=q.get("expected_error_mm_s")),
        solvent=overrides.get("solvent", system.solvent),
    )


def delta_uncertainty_inflation(plan: dict) -> float:
    """Extra delta uncertainty (mm/s, added in quadrature to prediction intervals) of the plan's matched rule.  Plans saved
    before the value was recorded in ``methods.delta`` take it from their selected rule in the bundled rules."""
    d = plan["methods"]["delta"]
    if "uncertainty_inflation_mm_s" in d:
        return float(d["uncertainty_inflation_mm_s"] or 0.0)
    rid = (plan.get("rule_evaluation") or {}).get("selected_rule")
    rule = next((r for r in (_data.load_method_rules() or {}).get("rules", []) if r["id"] == rid), None)
    return float(((rule or {}).get("delta") or {}).get("uncertainty_inflation_mm_s") or 0.0)


def method_spec_from_plan(plan: dict, which: str = "delta", functional: str | None = None) -> MethodSpec:
    m = plan["methods"]["delta"]
    f = functional or (m["functional"] if which == "delta" else plan["methods"]["deq"]["functional"])
    return mossbauer_method(f, basis=m["basis"], fe_basis=m["fe_basis"], grid=m["grid"], special_grid_atoms=m["special_grid_atoms"],
                            special_grid_int_acc=m["special_grid_int_acc"], rijcosx=m["rijcosx"],
                            scf_convergence=m["scf_convergence"], solvent=plan["methods"].get("solvent"),
                            nprocs=plan["resources"]["nprocs"], maxcore=plan["resources"]["maxcore"])


def geometry_spec_from_plan(plan: dict) -> MethodSpec:
    g = plan["geometry"]
    return MethodSpec(functional=g["functional"], basis=g["basis"], fe_basis=None, grid=g.get("grid", "DefGrid3"),
                      scf_convergence="TightSCF", solvent=plan["methods"].get("solvent"), extra_keywords=tuple(g.get("extra_keywords", [])),
                      nprocs=plan["resources"]["nprocs"], maxcore=plan["resources"]["maxcore"])


def geometry_label(plan: dict) -> str:
    g = plan["geometry"]
    return f"{g['functional']}/{g['basis']}" if g["mode"] == "optimize" else g.get("as_given_label", "as_given")


# ----------------------------------------------------------------------------- cost model
_NBF = {  # (def2-SVP, def2-TZVP) spherical basis functions per atom, approximate
    "H": (5, 6), "He": (5, 6), "B": (14, 31), "C": (14, 31), "N": (14, 31), "O": (14, 31), "F": (14, 31),
    "Si": (18, 43), "P": (18, 43), "S": (18, 43), "Cl": (18, 43), "Br": (32, 50), "I": (32, 50),
    "Fe": (31, 62), "K": (24, 50),
}
COST_MODEL = dict(
    description=("core_hours = a * (nbf/1000)^p * n_gradient_equivalents.  SP: 1 SCF + EFG/rho property (n=1, hybrid, NoRIJCOSX, "
                 "Fe grid 7).  opt: n_steps * 1.3 (gradient ~1.3 SP) at the geometry basis.  freq (numerical): 6*N_atoms gradients.  "
                 "highfield_sp: 3x SP (A/g/ZFS)."),
    a_core_hours_per_sp_at_1000bf=45.0, p=2.7, opt_steps=60, grad_over_sp=1.3, highfield_factor=3.0,
    nbf_source="per-element table of approximate def2-SVP/def2-TZVP function counts (+12 for CP(PPP) on Fe)",
    validity="UNVALIDATED order-of-magnitude placeholder (expected uncertainty about x3); not fitted to this cluster's timings. "
             "Override with inputs.overrides.cost.",
)


def estimate_nbf(structure: Structure, tz: bool, cppp: bool = False) -> int:
    n = 0
    for e in structure.symbols:
        e = e.capitalize()
        n += _NBF.get(e, (30, 60))[1 if tz else 0]
        if tz and cppp and e == "Fe":
            n += 12
    return n


def estimate_cost(plan: dict, structure: Structure) -> dict:
    cm = dict(COST_MODEL)
    cm.update(plan["inputs"]["overrides"].get("cost", {}))
    a, p = cm["a_core_hours_per_sp_at_1000bf"], cm["p"]
    nbf_tz = estimate_nbf(structure, True, bool(plan["methods"]["delta"]["fe_basis"]))
    nbf_sv = estimate_nbf(structure, False)
    sp = a * (nbf_tz / 1000.0) ** p
    gsv = a * (nbf_sv / 1000.0) ** p * cm["grad_over_sp"]
    per = {}
    for j in plan["jobs"]:
        k = j["kind"]
        per[j["id"]] = (cm["opt_steps"] * gsv if k == "opt" else 6 * len(structure) * gsv if k == "freq"
                        else cm["highfield_factor"] * sp if k == "highfield_sp" else sp)
    for j in plan["jobs"]:
        j["core_hours_est"] = round(per[j["id"]], 1)
    return dict(model=cm, nbf_est_tz=nbf_tz, nbf_est_svp=nbf_sv, per_job_core_hours={k: round(v, 1) for k, v in per.items()},
                total_core_hours=round(sum(per.values()), 1),
                refset_core_hours_note="Computing a reference set adds roughly (n_compounds x (opt + SP)) of comparable size per compound "
                                       "(see `calibration.refset.n_ready`).", )


# ----------------------------------------------------------------------------- calibration strategy
def orca_program(overrides: dict | None) -> str:
    """Program label a plan's calculations are fingerprinted with: ``ORCA <overrides.orca_version>`` (default 6.1)."""
    return "ORCA " + str((overrides or {}).get("orca_version", "6.1"))


def plan_calibration(methods: dict, geom_label: str | None, overrides: dict, system: SystemInfo) -> dict:
    spec = mossbauer_method(methods["delta"]["functional"], basis=methods["delta"]["basis"], fe_basis=methods["delta"]["fe_basis"],
                            special_grid_atoms=26, special_grid_int_acc=7, solvent=methods.get("solvent"))
    import dataclasses
    fp = dataclasses.replace(spec.fingerprint(), program=orca_program(overrides))
    ids = [methods["delta"]["calibration_entry"], methods["delta"]["fallback_calibration_entry"]]
    cands, seen = [], set()
    for e in R.list_calibrations():          # project fits first (they are the most specific), then rule entries
        if e.get("source") == "project_fit":
            seen.add(e["id"])
            cands.append((e, R.match_calibration(e, fp, geom_label)))
    for i in ids:
        e = R.get_calibration_entry(i) if i else None
        if e and e["id"] not in seen:
            seen.add(e["id"])
            cands.append((e, R.match_calibration(e, fp, geom_label)))
    for e in R.list_calibrations():          # any other numeric entry for the same functional: listed, usable only with overrides
        if e.get("source", "literature") == "literature" and e["id"] not in seen and R.is_numeric_entry(e):
            m = R.match_calibration(e, fp, geom_label)
            if not m["hard"]:
                seen.add(e["id"])
                cands.append((e, m))
    # rank project fits: exact soft-protocol match (solvent/grid/RI) first, then the user's own installed fits
    # (they carry a 'path') before bundled ones, then entries flagged recommended (e.g. the noFeO4 variants)
    pf = [(e, m) for e, m in cands if e.get("source") == "project_fit"]
    rest = [(e, m) for e, m in cands if e.get("source") != "project_fit"]
    pf.sort(key=lambda em: (len(em[1]["soft"]), 0 if em[0].get("path") else 1,
                            0 if em[0].get("recommended", True) else 1))
    cands = pf + rest
    requested = overrides.get("calibration_strategy") or "auto"  # None/"" = unset (cleared override)
    if requested not in ("auto", "lookup", "compute_refset", "user_refs"):
        raise PlanError("calibration_strategy must be auto | lookup | compute_refset | user_refs")
    allow_unv = bool(overrides.get("allow_unverified_calibration"))
    allow_mm = bool(overrides.get("allow_protocol_mismatch"))
    caveats, chosen, strategy = [], None, requested

    def acceptable(e, m, strict=True):
        if not m["numeric"]:
            return False
        if strict:
            # a different solvation model shifts rho(0) systematically: project fits are only auto-selected
            # when the solvent matches (use calibration_strategy=lookup + allow_protocol_mismatch to force)
            if e.get("source") == "project_fit" and any(s.startswith("solvent:") for s in m["soft"]):
                return False
            return m["protocol_matched"] and (m["verified"] or e.get("source") == "project_fit")
        return True

    if requested in ("auto", "lookup"):
        for e, m in cands:
            if acceptable(e, m):
                chosen = (e, m)
                break
        if chosen is None and requested == "lookup":
            # explicit lookup without a strictly acceptable entry: needs overrides
            for e, m in cands:
                if not m["numeric"]:
                    continue
                if (not m["verified"] and e.get("source") != "project_fit" and not allow_unv):
                    continue
                if m["hard"]:
                    continue  # functional/basis mismatch never allowed
                if (not m["geometry_match"] or (e.get("source") == "project_fit" and not m["protocol_matched"])) and not allow_mm:
                    continue
                chosen = (e, m)
                break
            if chosen is None:
                raise PlanError("lookup requested but no calibration is usable: need a verified, protocol-matched entry "
                                "(overrides: allow_protocol_mismatch for a geometry-level mismatch, allow_unverified_calibration for "
                                "unverified literature entries; functional/basis mismatches are never allowed). Candidates: "
                                + "; ".join(f"{m['id']}: hard={m['hard']} soft={m['soft']} verified={m['verified']}" for _, m in cands))
        elif chosen is None:
            strategy = "compute_refset"
    out = dict(requested=requested, strategy=strategy, candidates=[dict(m, **{"entry_summary": e.get("short")}) for e, m in cands],
               caveats=caveats)
    if chosen is not None:
        e, m = chosen
        strategy = "lookup"
        out["strategy"] = "lookup"
        out["lookup"] = dict(id=e["id"], source=e.get("source", "literature"), verified=m["verified"], alpha=e["alpha"], C=e["C"],
                             beta=e["beta"], doi=e.get("doi"), geometry_match=m["geometry_match"], soft_mismatches=m["soft"],
                             protocol_matched=m["protocol_matched"])
        if not m["protocol_matched"]:
            caveats.append("LOUD CAVEAT: this calibration is NOT protocol-matched (" + "; ".join(m["soft"] + m["hard"]) +
                           "); predicted delta carries an extra, unquantified systematic error.")
        if not m["verified"] and e.get("source") != "project_fit":
            caveats.append("LOUD CAVEAT: calibration constants are UNVERIFIED (not read from the primary source); use for exploration only.")
    elif strategy == "compute_refset":
        rname = overrides.get("refset") or R.default_refset_name()
        ready, skipped = R.resolve_refset(rname, None, overrides.get("refset_subset") or "all")
        rd = R.refset_definition(rname)
        out["refset"] = dict(id=rd["id"], doi=rd["doi"], n_total=len(rd["compounds"]), n_ready=len(ready), structure_status=rd["structure_status"],
                             note=rd["structure_status_note"],
                             reason="no verified, protocol-matched calibration exists for this protocol "
                                    "(" + ("requested" if requested == "compute_refset" else "auto") + ")")
        out["next_steps"] = ["mossbauer refset import ... (structures + charge/multiplicity)", "mossbauer refset setup plan.yaml --out refset/",
                             "run the jobs", "mossbauer refset fit refset/", "mossbauer analyze plan.yaml ..."]
    elif strategy == "user_refs":
        refs = overrides.get("user_refs") or []
        if not refs:
            out["open_items"] = ["user_refs strategy chosen but no reference compounds supplied"]
        for r in refs:
            for k in ("name", "xyz", "charge", "multiplicity", "delta_exp"):
                if k not in r:
                    raise PlanError(f"user reference {r.get('name')!r} lacks {k!r}")
        out["user_refs"] = refs
        if len(refs) == 1:
            caveats.append("a single reference compound fixes only the offset (beta); the slope alpha would have to be taken from the literature")
        elif len(refs) == 2:
            caveats.append("two references define an exactly determined line: no residual, LOO error or prediction interval")
    return out


# ----------------------------------------------------------------------------- jobs
def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_") or "mol"


def build_jobs(plan: dict, system: SystemInfo, sel_id: str) -> list[dict]:
    g, m, name = plan["geometry"], plan["methods"], _safe(system.name)
    jobs, geom_stem = [], None
    if g["mode"] == "optimize":
        jobs.append(dict(id="opt", kind="opt", stem=f"{name}_opt", method=f"{g['functional']}/{g['basis']}", charge=system.charge,
                         multiplicity=system.multiplicity, reads_xyz="structure.xyz", depends_on=[], purpose=["geometry"]))
        geom_stem = f"{name}_opt"
        if g.get("freq"):
            jobs.append(dict(id="freq", kind="freq", stem=f"{name}_freq", method=f"{g['functional']}/{g['basis']}", charge=system.charge,
                             multiplicity=system.multiplicity, reads_xyz=f"{geom_stem}.xyz", depends_on=["opt"], purpose=["verify minimum"]))
    reads = f"{geom_stem}.xyz" if geom_stem else "structure.xyz"
    dep = ["opt"] if geom_stem else []
    funcs = []
    for f, purpose in ((m["delta"]["functional"], ["delta", "deq"] if m["deq"]["functional"].upper() == m["delta"]["functional"].upper() else ["delta"]),
                       (m["deq"]["functional"], ["deq"]), (m["deq"]["crosscheck_functional"], ["deq-crosscheck"])):
        if not f:
            continue
        hit = next((x for x in funcs if x[0].upper() == f.upper()), None)
        if hit:
            hit[1].extend(p for p in purpose if p not in hit[1])
        else:
            funcs.append([f, list(purpose)])
    bs = plan["broken_symmetry"]
    variants = [("hs", None)]
    if bs["needed"] and bs["spec"]:
        variants.append(("bs", bs["spec"]))
    for f, purpose in funcs:
        for tag, spec in variants:
            sfx = f"{_safe(f).lower()}" + ("" if tag == "hs" and not bs["needed"] else f"_{tag}")
            jobs.append(dict(id=f"sp_{sfx}", kind="mossbauer_sp", stem=f"{name}_sp_{sfx}", method=f"{f}/{m['delta']['basis']}"
                             + (f"+{m['delta']['fe_basis']}(Fe)" if m['delta']['fe_basis'] else ""), functional=f, charge=system.charge,
                             multiplicity=system.multiplicity, reads_xyz=reads, depends_on=list(dep), purpose=purpose, broken_symmetry=spec))
    if plan["inputs"]["overrides"].get("highfield"):
        f = m["deq"]["functional"]
        jobs.append(dict(id="highfield_sp", kind="highfield_sp", stem=f"{name}_highfield_{_safe(f).lower()}", method=f"{f}/{m['delta']['basis']}",
                         functional=f, charge=system.charge, multiplicity=system.multiplicity, reads_xyz=reads, depends_on=list(dep),
                         purpose=["A-tensor", "g", "ZFS (high-field / spin-Hamiltonian)"], note="syntax per ORCA 6.1 manual; not yet run on the cluster"))
    return jobs


# ----------------------------------------------------------------------------- plan assembly
def compute_hash(plan: dict) -> str:
    p = {k: v for k, v in plan.items() if k not in ("status", "approval", "plan_hash")}
    blob = json.dumps(p, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_plan(structure: Structure | str | Path, charge: int, multiplicity: int, *, name: str | None = None, oxidation_state: int | None = None,
              radical_ligand: bool | None = None, solvent: str | None = None, temperature_K: float | None = None,
              nitrosyl: bool | None = None, experimental: dict | None = None, overrides: dict | None = None,
              structure_filename: str | None = None) -> dict:
    """Build a draft plan (a plain, YAML-serializable dict).  ``structure`` may be a Structure or an xyz/cif path."""
    if isinstance(structure, (str, Path)):
        structure_filename = structure_filename or Path(structure).name
        name = name or Path(structure).stem
        structure = read_structure(structure)
    overrides = dict(overrides or {})
    if not structure.fe_indices:
        raise PlanError("structure contains no Fe atom")
    if multiplicity < 1:
        raise PlanError("multiplicity must be >= 1")
    # electron-count parity check
    from .elements import atomic_numbers
    nel = sum(atomic_numbers(structure.symbols)) - charge
    if (nel - (multiplicity - 1)) % 2 != 0:
        raise PlanError(f"charge {charge} / multiplicity {multiplicity} inconsistent with {nel} electrons (parity)")
    system = SystemInfo(structure, charge, multiplicity, oxidation_state, radical_ligand, solvent, temperature_K, nitrosyl, name or "molecule")
    ev = evaluate_rules(system)
    sel = ev.selected
    methods = recommend_methods(sel, system, overrides)
    g_over = overrides.get("geometry", {}) or {}
    mode = g_over.get("mode", "optimize")
    geometry = dict(mode=mode, functional=g_over.get("functional", "MN15"), basis=g_over.get("basis", "def2-SVP"), freq=bool(g_over.get("freq", False)),
                    extra_keywords=list(g_over.get("extra_keywords", [])),
                    basis_note="project default: MN15/def2-SVP (gas phase); literature calibrations used BP86-D3BJ/ZORA-def2-TZVP geometries",
                    **({"as_given_label": g_over["as_given_label"]} if "as_given_label" in g_over else {}))
    if mode not in ("optimize", "as_given"):
        raise PlanError("geometry.mode must be optimize | as_given")
    glabel = f"{geometry['functional']}/{geometry['basis']}" if mode == "optimize" else geometry.get("as_given_label", "as_given")
    cal = plan_calibration(methods, glabel, overrides, system)
    bs_needed = sel["id"] in BS_RULES or bool(overrides.get("broken_symmetry"))
    bs_spec = overrides.get("broken_symmetry")
    open_items = list(cal.get("open_items", []))
    if bs_needed and not bs_spec:
        open_items.append("broken-symmetry spec required by the matched rule: give inputs.overrides.broken_symmetry "
                          "(kind flipspin: flip_atoms [0-based], final_ms; or kind brokensym: na, nb). Approval is blocked until set. "
                          "NOTE: the deck's multiplicity is that of the HIGH-SPIN determinant.")
    if bs_spec:
        BrokenSymmetry(**bs_spec).scf_lines()  # validates
    res = dict(nprocs=int(overrides.get("nprocs") or 16), maxcore=int(overrides.get("maxcore") or 3000))
    plan = dict(
        schema_version=SCHEMA, tool_version=__version__, status="draft", created=_now(),
        inputs=dict(name=system.name, structure_file=structure_filename or "structure.xyz", charge=charge, multiplicity=multiplicity,
                    hints=dict(oxidation_state=oxidation_state, radical_ligand=radical_ligand, solvent=solvent, temperature_K=temperature_K,
                               nitrosyl=nitrosyl), experimental=experimental or {}, overrides=overrides),
        system=dict(name=system.name, formula=structure.formula, n_atoms=len(structure), n_fe=system.n_fe, spin_S=str(system.spin),
                    n_electrons=nel, nitrosyl_detected=system.detect_nitrosyl(), structure_sha256=hashlib.sha256(structure.to_xyz_text().encode()).hexdigest()),
        rule_evaluation=dict(selected_rule=sel["id"], priority=sel["priority"], match_reasons=sel["match_reasons"],
                             justification=sel.get("justification"), confidence=sel.get("confidence"), citations=sel.get("citations", []),
                             evidence=sel.get("evidence", []), geometry_recommendation=sel.get("geometry"), solvation_recommendation=sel.get("solvation"),
                             notes=sel.get("notes"), matched=ev.matched, rejected=[dict(id=r["id"], reasons=r["reasons"]) for r in ev.rejected],
                             conflicts=ev.conflicts, global_constraints=ev.global_constraints, warnings=ev.warnings),
        methods=methods, geometry=geometry, calibration=cal,
        broken_symmetry=dict(needed=bs_needed, spec=bs_spec),
        resources=res, jobs=[], cost={}, open_items=open_items, approval=None,
    )
    plan["jobs"] = build_jobs(plan, system, sel["id"])
    plan["cost"] = estimate_cost(plan, structure)
    if plan["calibration"]["strategy"] == "lookup" and plan["calibration"]["lookup"]["verified"] is False and \
            plan["calibration"]["lookup"]["source"] != "project_fit":
        pass
    plan["_structure_xyz"] = structure.to_xyz_text()
    plan["plan_hash"] = compute_hash(plan)
    return plan


def update_plan(plan: dict, **edits) -> dict:
    """Re-derive the plan after editing ``inputs`` (hints/overrides/charge/multiplicity).  Resets status to draft."""
    inp = copy.deepcopy(plan["inputs"])
    ov = dict(inp.get("overrides", {}))
    ov.update(edits.pop("overrides", {}) or {})
    hints = dict(inp["hints"])
    for k in list(edits):
        if k in hints:
            hints[k] = edits.pop(k)
    st = read_structure_text(plan["_structure_xyz"], "structure.xyz")
    return make_plan(st, edits.get("charge", inp["charge"]), edits.get("multiplicity", inp["multiplicity"]), name=inp["name"],
                     structure_filename=inp["structure_file"], experimental=edits.get("experimental", inp.get("experimental")),
                     overrides=ov, **hints)


# ----------------------------------------------------------------------------- persistence + approval
def save_plan(plan: dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = copy.deepcopy(plan)
    xyz = body.pop("_structure_xyz", None)
    body["plan_hash"] = compute_hash(plan)
    if xyz is not None:
        (path.parent / "structure.xyz").write_text(xyz, encoding="utf-8", newline="\n")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(body, f, sort_keys=False, allow_unicode=True, width=110)
    return path


def load_plan(path) -> dict:
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        plan = yaml.safe_load(f)
    sx = path.parent / "structure.xyz"
    if sx.exists():
        plan["_structure_xyz"] = sx.read_text(encoding="utf-8")
    return plan


def plan_integrity(plan: dict) -> bool:
    return plan.get("plan_hash") == compute_hash(plan)


def approve_plan(plan: dict, approver: str | None = None, via: str | None = None, on_behalf_of: str | None = None) -> dict:
    """Seal a plan: hash, ``status: approved`` and an ``approval`` record (by, at, plan_hash, plus the channel ``via``
    and ``on_behalf_of`` when given).  ``approval`` is outside the hash, so seals made before ``via`` existed stay valid."""
    if plan.get("open_items"):
        raise PlanError("cannot approve: unresolved open items: " + " | ".join(plan["open_items"]))
    plan = copy.deepcopy(plan)
    plan["plan_hash"] = compute_hash(plan)
    plan["status"] = "approved"
    plan["approval"] = dict(by=approver or getpass.getuser(), at=_now(), plan_hash=plan["plan_hash"])
    plan["approval"].update({k: v for k, v in (("via", via), ("on_behalf_of", on_behalf_of)) if v})
    return plan


def require_approved(plan: dict):
    """The gate: draft, unapproved, or post-approval-modified plans are refused."""
    if plan.get("status") != "approved" or not plan.get("approval"):
        raise NotApprovedError("plan status is %r: review it and run `mossbauer approve plan.yaml` (or use the GUI button) before writing inputs"
                               % plan.get("status"))
    if plan["approval"].get("plan_hash") != compute_hash(plan):
        raise NotApprovedError("plan content changed after approval (hash mismatch): re-approve the edited plan")


def approve_file(path, approver: str | None = None, via: str | None = None, on_behalf_of: str | None = None) -> dict:
    plan = load_plan(path)
    plan = approve_plan(plan, approver, via, on_behalf_of)
    save_plan(plan, path)
    return plan


def summarize(plan: dict) -> str:
    re_, m, c = plan["rule_evaluation"], plan["methods"], plan["calibration"]
    L = [f"Plan [{plan['status'].upper()}]  {plan['system']['name']}  {plan['system']['formula']}  charge {plan['inputs']['charge']}  "
         f"mult {plan['inputs']['multiplicity']} (S={plan['system']['spin_S']})",
         f"Rule: {re_['selected_rule']} (priority {re_['priority']}); {'; '.join(re_['match_reasons'])}",
         f"delta: {m['delta']['functional']}/{m['delta']['basis']}" + (f" + {m['delta']['fe_basis']} on Fe" if m['delta']['fe_basis'] else "") +
         f"   DeltaEQ: {m['deq']['functional']}" + (f" (cross-check {m['deq']['crosscheck_functional']})" if m['deq']['crosscheck_functional'] else ""),
         f"Geometry: {plan['geometry']['mode']} {geometry_label(plan)}",
         f"Calibration strategy: {c['strategy']}" + (f" -> {c['lookup']['id']}" if 'lookup' in c else ""),
         "Jobs: " + ", ".join(j["stem"] for j in plan["jobs"]),
         f"Estimated cost: {plan['cost']['total_core_hours']} core-h (UNVALIDATED model)"]
    for k in ("conflicts", "warnings"):
        for x in re_[k]:
            L.append(f"[{k[:-1]}] {x}")
    for x in c.get("caveats", []):
        L.append(f"[caveat] {x}")
    for x in plan["open_items"]:
        L.append(f"[OPEN] {x}")
    return "\n".join(L)
