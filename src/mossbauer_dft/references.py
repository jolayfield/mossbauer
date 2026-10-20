"""Calibration sources: published-entry lookup, project fits, and reference-set (refset) workflow.

Three calibration strategies are supported by the planner:

``lookup``          use a published (or earlier project-fitted) calibration whose protocol matches the plan;
``compute_refset``  compute a reference set at the identical protocol, then ``refset fit`` (the default when no
                    verified, protocol-matched calibration exists);
``user_refs``       user-supplied experimental reference compounds (structure + experimental delta / DeltaEQ).

Only literature entries with ``verified: true`` are used by default; unverified ones need an explicit override
and always carry a loud caveat.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import data as _data
from .calibration import Calibration
from .orca_parse import Fingerprint, parse_orca_output
from .structure import Structure, read_structure


class ReferenceError_(RuntimeError):
    pass


# ----------------------------------------------------------------------------- calibration store
def user_calibration_dir() -> Path:
    return _data.user_home() / "calibrations"


def list_calibrations(include_user: bool = True) -> list[dict]:
    """All calibration entries: bundled literature entries (``source: literature``) + user project fits."""
    out = []
    cals = _data.load_calibrations() or {}
    for e in cals.get("calibrations", []):
        e = dict(e)
        e.setdefault("source", "literature")
        out.append(e)
    if include_user and user_calibration_dir().exists():
        for p in sorted(user_calibration_dir().glob("*.yaml")):
            with open(p) as f:
                e = yaml.safe_load(f)
            e["path"] = str(p)
            out.append(e)
    return out


def get_calibration_entry(entry_id: str) -> dict | None:
    return next((e for e in list_calibrations() if e.get("id") == entry_id), None)


def is_numeric_entry(e: dict) -> bool:
    return all(isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool) for k in ("alpha", "C", "beta"))


def entry_verified(e: dict) -> bool:
    """Literature: the ``verified`` flag.  Project fits are derived from the user's own data (not 'verified')."""
    return bool(e.get("verified")) if e.get("source", "literature") == "literature" else False


def _basis_token(text: str) -> str:
    t = str(text or "")
    if "CP(PPP)" in t:
        return "CP(PPP)"
    if "def2-TZVP" in t:
        return "def2-TZVP"
    return t.strip()


def entry_fingerprint(e: dict) -> Fingerprint:
    if e.get("source") == "project_fit":
        return Fingerprint.from_dict(e.get("fingerprint", {}))
    func = e["functional"][0] if isinstance(e.get("functional"), list) and e["functional"] else str(e.get("functional") or "")
    return Fingerprint(functional=func, basis=_basis_token(e.get("basis_other")),
                       fe_basis=_basis_token(e.get("basis_fe")), special_grid_int_acc="7" if "grid 7 on Fe" in str(e.get("grid")) else "",
                       special_grid_atoms="26" if "grid 7 on Fe" in str(e.get("grid")) else "", ri="NoRIJCOSX" if "no RI" in str(e.get("grid")) else "",
                       program=str(e.get("program") or "").replace("ORCA ", "ORCA "))


def _norm_geom(s: str) -> str:
    return re.sub(r"[^a-z0-9/]", "", str(s or "").lower())


def geometry_matches(entry_geometry: str, plan_geometry: str) -> bool:
    """Functional and basis of the plan's geometry level must both occur in the calibration's geometry text."""
    eg = _norm_geom(entry_geometry)
    if not eg or not plan_geometry:
        return False
    func, _, basis = plan_geometry.lower().partition("/")
    func = re.sub(r"[^a-z0-9]", "", func)
    basis = re.sub(r"[^a-z0-9]", "", basis)
    flat = re.sub(r"[^a-z0-9]", "", eg)
    return bool(func) and func in flat and bool(basis) and basis in flat


def match_calibration(entry: dict, sp_fp: Fingerprint, geometry_label: str | None) -> dict:
    """Is ``entry`` usable for a property SP with fingerprint ``sp_fp`` at geometry level ``geometry_label``?

    Returns ``dict(hard, soft, geometry_match, numeric, verified, protocol_matched)``.
    ``protocol_matched`` = numeric constants available, no hard fingerprint mismatch (functional, basis, Fe basis)
    and the geometry protocol matches.
    """
    res = dict(id=entry.get("id"), source=entry.get("source", "literature"), verified=entry_verified(entry),
               numeric=is_numeric_entry(entry), hard=[], soft=[], geometry_match=False, protocol_matched=False)
    if not res["numeric"]:
        res["hard"].append("no usable numeric constants (alpha/C/beta missing or multi-law)")
        return res
    hard, soft = entry_fingerprint(entry).compare(sp_fp)
    res["hard"], res["soft"] = list(hard), list(soft)
    egeom = entry.get("geometry") if entry.get("source") != "project_fit" else entry.get("geometry_label")
    res["geometry_match"] = (_norm_geom(egeom) == _norm_geom(geometry_label)) if entry.get("source") == "project_fit" \
        else geometry_matches(egeom, geometry_label)
    if not res["geometry_match"]:
        res["soft"].append(f"geometry protocol differs: calibration={egeom!r} vs plan={geometry_label!r}")
    sol = entry.get("solvation")
    if sol and not sp_fp.solvent and "toluene" in str(sol).lower():
        res["soft"].append(f"solvation differs: calibration used {sol!r}; plan is gas phase")
    pe = re.search(r"(\d+)\.?(\d*)", entry_fingerprint(entry).program or "")
    pp = re.search(r"(\d+)\.?(\d*)", sp_fp.program or "")
    res["program_match"] = bool(pe and pp and pe.group(1) == pp.group(1))
    if not res["program_match"]:
        res["soft"].append(f"program differs: calibration={entry_fingerprint(entry).program or '?'!r} vs plan={sp_fp.program or '?'!r} "
                           "(different ORCA major versions can give rho(0) on a different scale; check the C offset)")
    res["protocol_matched"] = not res["hard"] and res["geometry_match"] and res["program_match"]
    if res["source"] == "project_fit" and any(x.startswith("solvent:") for x in res["soft"]):
        res["protocol_matched"] = False   # solvation model shifts rho(0): project fits need the same solvent
    return res


def calibration_from_entry(entry: dict) -> Calibration:
    """Build a Calibration object from a bundled/literature entry (analytic prediction intervals only)."""
    if entry.get("source") == "project_fit":
        return Calibration.from_dict(entry["calibration"])
    sd = entry.get("SD")
    m = re.match(r"\s*([0-9.]+)", str(sd)) if sd is not None else None
    sd_v = float(m.group(1)) if m else float(entry.get("MAE") or 0.05) * 1.25
    n = int(entry.get("n_training") or 5)
    stats = dict(n=n, dof=max(n - 2, 1), resid_sd=sd_v, xbar=0.0, sxx=float("inf"), rho_min=float(entry["C"]) - 500.0, rho_max=float(entry["C"]) + 500.0)
    cal = Calibration(float(entry["alpha"]), float(entry["beta"]), float(entry["C"]), [], [], [],
                      entry_fingerprint(entry), stats, [], dict(source=entry["id"], doi=entry.get("doi"),
                                                                note="literature constants; interval uses published training SD, n_boot=0; rho range assumed C+-500 a0^-3 (extrapolation warning outside)"), n_boot=0)
    return cal


# ----------------------------------------------------------------------------- reference sets
PRIMARY_REFSET = "mcwilliams2017"
FALLBACK_REFSET = "fe1_calib18"


def default_refset_name(store: Path | None = None) -> str:
    """Default refset for ``compute_refset``: the McWilliams set when at least one of its structures is available
    (bundled or imported into ``store``), otherwise the bundled, structure-complete project set ``fe1_calib18``."""
    try:
        ready, _ = resolve_refset(PRIMARY_REFSET, store)
    except ReferenceError_:
        ready = []
    return PRIMARY_REFSET if ready else FALLBACK_REFSET


def refset_definition(name: str | None = None) -> dict:
    name = name or default_refset_name()
    d = _data.load_refset(name)
    if d is None:
        raise ReferenceError_(f"no bundled reference set {name!r}; available: {_data.available_refsets()}")
    return d


def refset_summary(d: dict) -> dict:
    cs = d["compounds"]
    ok = [c for c in cs if c.get("structure") != "missing"]
    return dict(id=d["id"], n=len(cs), with_structure=len(ok), structure_status=d.get("structure_status"))


def _resolve_structure_path(d: dict, c: dict, workdir: Path | None = None) -> Path | None:
    cand = []
    if c.get("structure_file"):
        sf = Path(c["structure_file"])
        cand.append(sf if sf.is_absolute() else _data.REFSETS_DIR / sf)   # relative paths are relative to data/refsets
    cand.append(_data.REFSETS_DIR / "structures" / f"{c['id']}.xyz")
    cand.append(_data.REFSETS_DIR / "structures" / str(d.get("id")) / f"{c['id']}.xyz")
    if workdir:
        cand.append(Path(workdir) / "structures" / f"{c['id']}.xyz")
    return next((p for p in cand if p.exists()), None)


def import_structure(refset: str, compound_id: str, path, charge: int, multiplicity: int | None, store: Path) -> Path:
    """Import a user-supplied xyz/CIF for a reference compound into ``store`` (a user refset directory).

    Writes ``<store>/structures/<id>.xyz`` and ``<store>/overrides.yaml`` (charge, multiplicity, structure file).
    """
    d = refset_definition(refset)
    c = next((c for c in d["compounds"] if c["id"] == compound_id), None)
    if c is None:
        raise ReferenceError_(f"{compound_id!r} is not in refset {refset!r}")
    s = read_structure(path)
    if not s.fe_indices:
        raise ReferenceError_("structure contains no Fe atom")
    store = Path(store)
    (store / "structures").mkdir(parents=True, exist_ok=True)
    out = store / "structures" / f"{compound_id}.xyz"
    s.comment = f"{refset}:{compound_id} {c['name']} (imported from {Path(path).name})"
    s.write_xyz(out)
    ov_path = store / "overrides.yaml"
    ov = yaml.safe_load(ov_path.read_text()) if ov_path.exists() else {}
    ov = ov or {}
    ov[compound_id] = dict(charge=int(charge), multiplicity=int(multiplicity) if multiplicity else c.get("multiplicity"),
                           structure_file=str(out.resolve()), source=Path(path).name)
    ov_path.write_text(yaml.safe_dump(ov, sort_keys=False))
    return out


def resolve_refset(refset: str | None, store: Path | None = None, subset: str = "all", ids: list[str] | None = None,
                   include_polynuclear: bool = False) -> tuple[list[dict], list[dict]]:
    """Return (ready, skipped) compound dicts, each ready one with resolved xyz path, charge, multiplicity."""
    d = refset_definition(refset)
    refset = d["id"]
    ov = {}
    if store and (Path(store) / "overrides.yaml").exists():
        ov = yaml.safe_load((Path(store) / "overrides.yaml").read_text()) or {}
    ready, skipped = [], []
    for c in d["compounds"]:
        c = dict(c)
        c.update({k: v for k, v in ov.get(c["id"], {}).items() if v is not None})
        why = []
        if ids and c["id"] not in ids:
            continue
        if subset in ("training", "test") and c["set"] != subset:
            continue
        sp = _resolve_structure_path(d, c, store)
        if sp is None:
            why.append("structure missing")
        if c.get("charge") is None:
            why.append("charge missing")
        if c.get("multiplicity") is None:
            why.append("multiplicity missing")
        if c.get("n_fe", 1) > 1 and not include_polynuclear:
            why.append("polynuclear (needs broken-symmetry spec)")
        if why:
            c["skip_reason"] = "; ".join(why)
            skipped.append(c)
        else:
            c["xyz_path"] = str(sp)
            ready.append(c)
    return ready, skipped


def refset_setup(plan_path, outdir, refset: str | None = None, store: Path | None = None, subset: str = "all",
                 ids: list[str] | None = None, site: str | None = None, allow_partial: bool = True) -> dict:
    """Write ORCA inputs for every reference compound at the plan's protocol (geometry opt, then property SP).

    The plan must be approved.  Layout::

        <outdir>/<id>/<id>_opt.inp, <id>_sp.inp, <id>.xyz   +  <outdir>/orca_job.slurm, submit_refset.sh, refset_manifest.yaml
    """
    from . import plan as P
    from .orca_input import build_input
    from .site import SiteProfile
    plan = P.load_plan(plan_path)
    P.require_approved(plan)
    refset = refset or default_refset_name(store)
    ready, skipped = resolve_refset(refset, store, subset, ids)
    if not ready:
        raise ReferenceError_("no reference compound is ready (" + "; ".join(f"{c['id']}: {c['skip_reason']}" for c in skipped[:5]) + ")")
    if skipped and not allow_partial:
        raise ReferenceError_(f"{len(skipped)} compounds are not ready: " + ", ".join(c["id"] for c in skipped))
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    spec_sp = P.method_spec_from_plan(plan, "delta")
    spec_opt = P.geometry_spec_from_plan(plan)
    site_p = SiteProfile.load(site)
    (outdir / "orca_job.slurm").write_text(site_p.slurm_script(), encoding='utf-8', newline='\n')
    manifest = dict(refset=refset, plan_hash=plan["plan_hash"], geometry_label=P.geometry_label(plan),
                    sp_method=plan["methods"]["delta"], fingerprint=spec_sp.fingerprint().to_dict(), site=site_p.name,
                    created=_now(), compounds=[], skipped=[dict(id=c["id"], reason=c["skip_reason"]) for c in skipped])
    cmds = ["#!/bin/bash", "# Reference-set submission (display/inspect; run from this directory). Opt -> SP dependency chain.", "set -e"]
    geom_mode = plan["geometry"]["mode"]
    for c in ready:
        cd = outdir / c["id"]
        cd.mkdir(exist_ok=True)
        s = read_structure(c["xyz_path"])
        s.write_xyz(cd / f"{c['id']}.xyz")
        stem_o, stem_s = f"{c['id']}_opt", f"{c['id']}_sp"
        ch, mu = int(c["charge"]), int(c["multiplicity"])
        sp_in = f"{c['id']}.xyz" if geom_mode == "as_given" else f"{stem_o}.xyz"
        if geom_mode != "as_given":
            (cd / f"{stem_o}.inp").write_text(build_input("opt", spec_opt.with_(nprocs=plan["resources"]["nprocs"]), charge=ch,
                                                          multiplicity=mu, xyz_file=f"{c['id']}.xyz"))
        (cd / f"{stem_s}.inp").write_text(build_input("mossbauer_sp", spec_sp.with_(nprocs=plan["resources"]["nprocs"]), charge=ch,
                                                      multiplicity=mu, xyz_file=sp_in))
        (cd / "orca_job.slurm").write_text(site_p.slurm_script(), encoding='utf-8', newline='\n')
        n = plan["resources"]["nprocs"]
        cmds.append(f"cd {c['id']}")
        if geom_mode != "as_given":
            cmds.append(f"J1=$({site_p.sbatch_command(stem_o, nprocs=n).replace('sbatch', 'sbatch --parsable', 1)})")
            cmds.append(f"{site_p.sbatch_command(stem_s, nprocs=n).replace('sbatch', 'sbatch --dependency=afterok:$J1', 1)}")
        else:
            cmds.append(site_p.sbatch_command(stem_s, nprocs=n))
        cmds.append("cd ..")
        manifest["compounds"].append(dict(id=c["id"], name=c["name"], charge=ch, multiplicity=mu, delta_exp=c["delta_exp_mm_s"],
                                          deq_abs_exp=c["deq_abs_exp_mm_s"], delta_T_K=c.get("delta_T_K"), flags=list(c.get("flags") or []),
                                          opt_stem=None if geom_mode == "as_given" else stem_o, sp_stem=stem_s, dir=c["id"]))
    for fn in ("submit_refset.sh",):
        (outdir / fn).write_text("\n".join(cmds) + "\n", encoding="utf-8", newline="\n")
    # each compound dir needs its own script copy (done above); top-level copy is for convenience
    (outdir / "refset_manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    return manifest


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def refset_fit(refset_dir, outputs_dir=None, install: bool = True, level: float = 0.95, n_boot: int = 5000,
               exclude: list[str] | None = None, min_points: int = 3) -> dict:
    """Parse finished SP outputs, fit delta = alpha(rho0 - C) + beta (OLS, LOO, bootstrap) and save a calibration entry.

    Outputs are searched in ``<refset_dir>/<id>/<id>_sp.out`` (or ``outputs_dir/<id>_sp.out``).  All outputs must
    share the same method fingerprint (functional/basis/Fe basis); incomplete or non-converged runs are excluded
    and listed.  The fitted entry (id ``fit_<refset>_<func>_<hash>``) is written to ``<refset_dir>/calibration.yaml`` and,
    with ``install=True``, to the user calibration store so that later plans can look it up.
    """
    refset_dir = Path(refset_dir)
    man = yaml.safe_load((refset_dir / "refset_manifest.yaml").read_text())
    rows, excluded = [], []
    for c in man["compounds"]:
        if exclude and c["id"] in exclude:
            excluded.append((c["id"], "user-excluded"))
            continue
        cands = [refset_dir / c["dir"] / f"{c['sp_stem']}.out"]
        if outputs_dir:
            cands.insert(0, Path(outputs_dir) / f"{c['sp_stem']}.out")
        p = next((x for x in cands if x.exists()), None)
        if p is None:
            excluded.append((c["id"], "output not found"))
            continue
        r = parse_orca_output(p)
        if not r.ok or len(r.fe_sites) != 1 or r.fe_sites[0].rho0 is None:
            excluded.append((c["id"], "not OK (termination/SCF) or not exactly one Fe with rho(0)"))
            continue
        rows.append((c, r))
    if len(rows) < max(2, min_points):
        raise ReferenceError_(f"only {len(rows)} usable outputs (need >= {max(2, min_points)}); excluded: {excluded}")
    fps = {json.dumps({k: v for k, v in r.fingerprint.to_dict().items() if k in Fingerprint.HARD}, sort_keys=True) for _, r in rows}
    if len(fps) != 1:
        raise ReferenceError_("outputs do not share one functional/basis/Fe-basis fingerprint: " + "; ".join(sorted(fps)))
    fp = rows[0][1].fingerprint
    names = [c["id"] for c, _ in rows]
    rho = [r.fe_sites[0].rho0 for _, r in rows]
    dl = [c["delta_exp"] for c, _ in rows]
    cal = Calibration.fit(rho, dl, names=names, fingerprint=fp, n_boot=n_boot,
                          metadata=dict(refset=man["refset"], plan_hash=man["plan_hash"], geometry=man["geometry_label"]))
    sig = hashlib.sha256((json.dumps(cal.to_dict()["fingerprint"], sort_keys=True) + man["geometry_label"] + man["refset"]
                          + ",".join(names)).encode()).hexdigest()[:8]
    eid = f"fit_{man['refset']}_{re.sub(r'[^a-z0-9]', '', fp.functional.lower())}_{sig}"
    deq_rows = []
    for c, r in rows:
        s = r.fe_sites[0]
        deq_rows.append(dict(id=c["id"], deq_calc=s.abs_deq(), deq_exp=c["deq_abs_exp"]))
    entry = dict(id=eid, source="project_fit", verified=False, short=f"project fit on {man['refset']} ({len(rows)} compounds)",
                 created_utc=_now(), refset=man["refset"], geometry_label=man["geometry_label"], fingerprint=fp.to_dict(),
                 alpha=cal.alpha, C=cal.C, beta=cal.beta, n_training=len(rows), stats=cal.to_dict()["stats"],
                 excluded=[dict(id=i, reason=w) for i, w in excluded], deq_check=deq_rows,
                 flagged=[dict(id=c["id"], flags=list(c.get("flags") or [])) for c, _ in rows if c.get("flags")], calibration=cal.to_dict(),
                 note="Fitted on this project's own calculations; valid only for this exact protocol (fingerprint + geometry level).")
    (refset_dir / "calibration.yaml").write_text(yaml.safe_dump(entry, sort_keys=False))
    if install:
        user_calibration_dir().mkdir(parents=True, exist_ok=True)
        (user_calibration_dir() / f"{eid}.yaml").write_text(yaml.safe_dump(entry, sort_keys=False))
    return entry


def refset_fit_csv(refset_dir, entry: dict) -> Path:
    """Per-point table (rho0, delta_exp, fit, LOO) next to the calibration."""
    p = Path(refset_dir) / "calibration_points.csv"
    pp = entry["calibration"]["per_point"]
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pp[0].keys()))
        w.writeheader()
        for r in pp:
            w.writerow({**r, "flags": ";".join(r["flags"])})
    return p


def training_set_rows() -> list[dict]:
    return _data.load_training_set() or []
