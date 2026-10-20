"""Plan -> inputs (``write_inputs``) and outputs -> results (``analyze``).  Used by both the CLI and the GUI."""
from __future__ import annotations
from dataclasses import asdict
import csv
import io
import json
import re
import warnings
from pathlib import Path

from . import plan as P
from . import references as R
from . import calcontext, trust as T
from .calibration import CalibrationWarning, FingerprintMismatchError
from .orca_input import BrokenSymmetry, build_input
from .orca_parse import parse_orca_output, parse_orca_text
from .site import SiteProfile
from .structure import Structure

SUBMIT_SCRIPT = "submit.sh"


STAMP_RE = re.compile(r"^#\s*mossbauer-dft:\s+entry=(\S+)\s+version=(\d+)\s+plan=([0-9a-f]+)\s*$")


def stamp_line(entry: str, version: int, plan_hash: str) -> str:
    """The provenance comment a project writes as the first line of each deck (entry, version, short plan hash)."""
    return f"# mossbauer-dft: entry={entry} version={version} plan={plan_hash[:12]}"


def write_inputs(plan_path, outdir, site: str | None = None, stamp: dict | None = None) -> dict:
    """Write ORCA decks, Slurm script and a submission-command file.  Refuses unless the plan is approved and unmodified.

    ``stamp`` (``{"entry": id, "version": N}``, passed by the project) prepends one ``stamp_line`` comment to every deck;
    without it the deck text is exactly ``build_input``'s.
    """
    plan = P.load_plan(plan_path)
    P.require_approved(plan)
    head = stamp_line(stamp["entry"], stamp["version"], plan["plan_hash"]) + "\n" if stamp else ""
    sp = SiteProfile.load(site)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    st = Structure.from_xyz_text(plan["_structure_xyz"])
    (outdir / "structure.xyz").write_text(plan["_structure_xyz"], encoding="utf-8", newline="\n")
    (outdir / "orca_job.slurm").write_text(sp.slurm_script(), encoding='utf-8', newline='\n')
    n = plan["resources"]["nprocs"]
    files, cmds, ids = ["structure.xyz", "orca_job.slurm"], ["#!/bin/bash",
        "# Submission commands generated from the approved plan (NOT executed automatically). Run from this directory on the cluster.",
        "set -e"], {}
    for j in plan["jobs"]:
        if j["kind"] == "opt":
            m = P.geometry_spec_from_plan(plan)
            txt = build_input("opt", m, charge=j["charge"], multiplicity=j["multiplicity"], xyz_file="structure.xyz")
        elif j["kind"] == "freq":
            m = P.geometry_spec_from_plan(plan)
            txt = build_input("freq", m, charge=j["charge"], multiplicity=j["multiplicity"], xyz_file=j["reads_xyz"])
        else:
            m = P.method_spec_from_plan(plan, "delta", j["functional"])
            if j.get("broken_symmetry"):
                m = m.with_(broken_symmetry=BrokenSymmetry(**j["broken_symmetry"]))
            txt = build_input(j["kind"], m, charge=j["charge"], multiplicity=j["multiplicity"], xyz_file=j["reads_xyz"])
        (outdir / f"{j['stem']}.inp").write_text(head + txt, encoding="utf-8", newline="\n")
        files.append(f"{j['stem']}.inp")
        cmd = sp.sbatch_command(j["stem"], nprocs=n)
        dep = [ids[d] for d in j["depends_on"] if d in ids]
        var = f"J_{j['id']}"
        if dep:
            cmd = cmd.replace("sbatch", "sbatch --dependency=afterok:" + ":".join(f"${{{d}}}" for d in dep), 1)
        cmds.append(f"{var}=$({cmd.replace('sbatch', 'sbatch --parsable', 1)})")
        ids[j["id"]] = var
    (outdir / SUBMIT_SCRIPT).write_text("\n".join(cmds) + "\n", encoding="utf-8", newline="\n")
    files.append(SUBMIT_SCRIPT)
    (outdir / "README_inputs.txt").write_text(
        f"Inputs for plan {plan['plan_hash'][:12]} approved by {plan['approval']['by']} at {plan['approval']['at']}\n"
        f"Site profile: {sp.name}.  Property SPs read the optimized geometry <stem>_opt.xyz written by the opt job.\n"
        + "\n".join(f"- {j['stem']}: {j['method']} ({', '.join(j['purpose'])}), est. {j.get('core_hours_est')} core-h" for j in plan["jobs"]) + "\n", encoding="utf-8", newline="\n")
    files.append("README_inputs.txt")
    metadata = dict(plan_hash=plan['plan_hash'], profile=profile_snapshot(sp))
    (outdir / 'site.json').write_text(json.dumps(metadata, indent=2) + '\n')
    files.append('site.json')
    return dict(outdir=str(outdir), files=files, commands=submit_commands(outdir), site=sp.name)


RESUBMIT_SCRIPT = "resubmit_failed.sh"


def profile_snapshot(profile):
    return {k: v for k, v in asdict(profile.validate()).items() if k not in ('ssh_host', 'remote_dir', 'notes')}


def input_site(outdir, explicit=None):
    folder = Path(outdir)
    saved = folder / 'site.json'
    if saved.exists():
        metadata = json.loads(saved.read_text())
        manifest = folder / 'manifest.json'
        if manifest.exists() and json.loads(manifest.read_text()).get('plan_hash') != metadata.get('plan_hash'):
            raise ValueError('Recorded site does not match these inputs; regenerate inputs')
        profile = SiteProfile.from_dict(metadata['profile'])
        if explicit is not None and profile_snapshot(SiteProfile.load(explicit)) != profile_snapshot(profile):
            raise ValueError('Site differs from written scheduler script; regenerate inputs for the new site')
        return profile
    if explicit is not None:
        raise ValueError('Legacy inputs have no site snapshot; regenerate inputs before selecting a site for resubmission')
    previous = folder / 'resubmit.json'
    if previous.exists():
        record = json.loads(previous.read_text())
        if record.get('site_profile'):
            return SiteProfile.from_dict(record['site_profile'])
        if record.get('site'):
            return SiteProfile.load(record['site'])
    readme = folder / 'README_inputs.txt'
    if readme.exists():
        match = re.search(r'^Site profile: (.+?)\.  Property', readme.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return SiteProfile.load(match[1])
    return SiteProfile.load()


def resubmit_script(plan: dict, outdir, stems, site: str | None = None, resources: dict | None = None) -> dict:
    """Write ``resubmit_failed.sh``: the named jobs plus every job depending on them (in plan order), chained with
    ``afterok`` among themselves.  A dependency outside the set is assumed complete and dropped.  ``resources`` gives
    per-stem ``time``/``mem_gb`` overrides.  Not executed automatically."""
    sp = input_site(outdir, site)
    by_id = {j["id"]: j for j in plan["jobs"]}
    wanted = {j["id"] for j in plan["jobs"] if j["stem"] in set(stems)}
    grew = True
    while grew:
        more = {j["id"] for j in plan["jobs"] if j["id"] not in wanted and any(d in wanted for d in j["depends_on"])}
        grew, wanted = bool(more), wanted | more
    n = plan["resources"]["nprocs"]
    cmds, ids, chosen = ["#!/bin/bash",
        "# Resubmission of failed jobs and their dependents (NOT executed automatically). Run from this directory on the cluster.",
        "set -e"], {}, []
    for j in plan["jobs"]:
        if j["id"] not in wanted:
            continue
        r = (resources or {}).get(j["stem"]) or {}
        cmd = sp.sbatch_command(j["stem"], nprocs=n, mem_gb=r.get("mem_gb"), time=r.get("time"))
        dep = [ids[d] for d in j["depends_on"] if d in ids]
        if dep:
            cmd = cmd.replace("sbatch", "sbatch --dependency=afterok:" + ":".join(f"${{{d}}}" for d in dep), 1)
        var = f"J_{j['id']}"
        cmds.append(f"{var}=$({cmd.replace('sbatch', 'sbatch --parsable', 1)})")
        ids[j["id"]], _ = var, chosen.append(j["stem"])
    path = Path(outdir) / RESUBMIT_SCRIPT
    path.write_text("\n".join(cmds) + "\n", encoding="utf-8", newline="\n")
    return dict(path=str(path), stems=chosen, commands=cmds[3:], site=sp.name, site_profile=profile_snapshot(sp))


def submit_commands(outdir) -> list[str]:
    """The submission commands in ``outdir/submit.sh`` (header lines dropped)."""
    return (Path(outdir) / SUBMIT_SCRIPT).read_text().splitlines()[3:]


def zip_inputs(outdir) -> bytes:
    from .handoff import archive, verified_files
    return archive(verified_files(outdir))


# ----------------------------------------------------------------------------- analysis
def resolve_calibration(plan: dict | None, calibration=None):
    """Return (Calibration | None, label).  ``calibration``: path to a calibration YAML, an entry id, or None -> plan's strategy."""
    from .calibration import Calibration
    if calibration:
        p = Path(str(calibration))
        if p.exists():
            import yaml
            d = yaml.safe_load(p.read_text())
            return (Calibration.from_dict(d["calibration"] if "calibration" in d else d), str(p))
        e = R.get_calibration_entry(str(calibration))
        if e is None:
            raise ValueError(f"unknown calibration {calibration!r}")
        return R.calibration_from_entry(e), e["id"]
    if plan is None:
        return None, "none"
    c = plan["calibration"]
    if c["strategy"] == "lookup":
        e = R.get_calibration_entry(c["lookup"]["id"])
        return R.calibration_from_entry(e), e["id"]
    if c["strategy"] == "compute_refset":
        # look for a project fit that matches the plan protocol
        spec = P.method_spec_from_plan(plan, "delta")
        for e in R.list_calibrations():
            if e.get("source") == "project_fit":
                m = R.match_calibration(e, spec.fingerprint(), P.geometry_label(plan))
                if m["protocol_matched"]:
                    return R.calibration_from_entry(e), e["id"]
    return None, "none (no fitted calibration yet: run `mossbauer refset fit`)"


def _find_outputs(plan, outputs):
    outs = {}
    paths = []
    for o in ([outputs] if isinstance(outputs, (str, Path)) else list(outputs)):
        o = Path(o)
        paths += sorted(o.glob("*.out")) if o.is_dir() else [o]
    for p in paths:
        outs[p.name.removesuffix('.compact.json') if p.name.endswith('.compact.json') else p.stem] = p
    return outs


def analyze(plan_path_or_dict, outputs, calibration=None, level: float = 0.95, q_barn: float = 0.16, include_unmatched: bool = False) -> dict:
    """Parse ORCA outputs and report delta (calibrated, with prediction interval), DeltaEQ (signed), eta, <S^2>, Fe spin.

    ``outputs``: directory, file or list of ``.out`` files.  With a plan, only outputs whose stem is a plan job stem are used
    and results are grouped by job purpose (others are listed in ``notes``; ``include_unmatched=True`` analyzes them as ad-hoc property jobs); experimental values come from ``plan.inputs.experimental``.
    """
    plan = None
    if plan_path_or_dict is not None:
        plan = P.load_plan(plan_path_or_dict) if not isinstance(plan_path_or_dict, dict) else plan_path_or_dict
    cal, cal_label = resolve_calibration(plan, calibration)
    outs = _find_outputs(plan, outputs)
    stems = {j["stem"]: j for j in plan["jobs"]} if plan else {}
    exp = (plan or {}).get("inputs", {}).get("experimental", {}) or {}
    oxidation_state = ((plan or {}).get("inputs", {}).get("hints") or {}).get("oxidation_state")
    rows, notes = [], []
    inflation = P.delta_uncertainty_inflation(plan) if plan else 0.0
    if inflation and cal is not None:
        notes.append(f"delta prediction intervals include the matched method rule's extra uncertainty of {inflation:g} mm/s "
                     "(added in quadrature)")
    for stem, path in outs.items():
        if plan and stem not in stems:
            if not include_unmatched:
                notes.append(f"{stem}: skipped - not a job stem of this plan (rename the output, or analyze with --all to treat it as an ad-hoc property job)")
                continue
        job = stems.get(stem, {})
        if job.get("kind") in ("opt", "freq"):
            continue
        if path.name.endswith('.compact.json'):
            from .compact import load_record
            res = load_record(path, plan) if plan is not None else None
            if res is None:
                raise ValueError('compact analysis requires its plan')
        else:
            res = parse_orca_output(path)
        base = dict(job=stem, kind=job.get("kind", "mossbauer_sp"), purpose=",".join(job.get("purpose", [])), functional=res.fingerprint.functional,
                    ok=res.ok, s2=res.s2, s2_ideal=res.s2_ideal, output_fingerprint=res.fingerprint.to_dict(),
                    energy_Eh=res.final_energy, broken_symmetry=bool(job.get("broken_symmetry")))
        common = []  # flags that concern every value from this output
        if not res.ok:
            notes.append(f"{stem}: calculation not OK (termination/SCF) - values unreliable")
            common.append(T.flag("calculation_not_ok", T.BLOCKING, "all", "calculation not OK (termination/SCF): values unreliable"))
        if (sc := T.spin_contamination(res.s2, res.s2_ideal, bool(job.get("broken_symmetry")))):
            common.append(sc)
        common += T.rule_flags(res.fingerprint.functional, oxidation_state)
        for s in res.fe_sites:
            r = dict(base, atom=s.index, rho0=s.rho0, vzz_au=s.vzz, eta=s.eta, deq_signed=s.deq(q_barn), abs_deq=s.abs_deq(q_barn),
                     deq_orca=s.deq_orca_mm_s, fe_spin_mulliken=s.mulliken_spin, fe_spin_loewdin=s.loewdin_spin,
                     delta=None, delta_lo=None, delta_hi=None, delta_note="")
            delta_flags = []
            if cal is not None and s.rho0 is not None and "delta" in (job.get("purpose") or ["delta"]):
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always", CalibrationWarning)
                    try:
                        pr = cal.predict(s.rho0, fingerprint=res.fingerprint, level=level, sigma_floor=inflation)
                        lo, hi = pr.pi
                        if not (-1.5 <= pr.delta <= 2.5):
                            pr.warnings.append(f"IMPLAUSIBLE delta={pr.delta:.2f} mm/s (outside -1.5..2.5): rho(0) scale of this calculation "
                                               "does not match the calibration (different ORCA version/basis/grid?); do not use")
                        r.update(delta=pr.delta, delta_lo=lo, delta_hi=hi)
                        msgs = pr.warnings + [str(x.message) for x in w if x.message not in pr.warnings]
                        delta_flags = [T.classify(m) for m in msgs]
                        r["nearest_refs"] = calcontext.nearest(cal, s.rho0)
                        near = r["nearest_refs"][0] if r["nearest_refs"] else None
                        if near and near["flags"]:
                            delta_flags.append(T.classify(f"nearest calibration point {near['name']} is flagged "
                                                          f"({', '.join(near['flags'])}): it weighs heavily on this prediction"))
                    except FingerprintMismatchError as e:
                        delta_flags = [T.classify(f"calibration refused: {e}")]
            elif cal is None and "delta" in (job.get("purpose") or ["delta"]):
                delta_flags = [T.classify("no calibration available: only rho(0) reported")]
            r["trust_flags"] = delta_flags + common
            r["delta_note"] = T.note(r["trust_flags"])
            if exp.get("delta") is not None:
                r["delta_exp"] = exp["delta"]
            if exp.get("deq") is not None:
                r["abs_deq_exp"] = exp["deq"]
            rows.append(r)
    # lowest-energy flag across BS/HS variants of the same functional
    by_f = {}
    for r in rows:
        by_f.setdefault(r["functional"], []).append(r)
    for f, rr in by_f.items():
        es = [(r["energy_Eh"], r) for r in rr if r["energy_Eh"] is not None]
        for r in rr:
            r["lowest_energy_state"] = bool(es) and r is min(es, key=lambda t: t[0])[1] if len({r2["job"] for r2 in rr}) > 1 else None
    return dict(calibration=cal_label, level=level, rows=rows, notes=notes)


def results_csv(result: dict) -> str:
    if not result["rows"]:
        return ""
    keys = []
    for r in result["rows"]:
        for k in r:
            if k not in keys:
                keys.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=keys)
    w.writeheader()
    w.writerows(result["rows"])
    return buf.getvalue()
