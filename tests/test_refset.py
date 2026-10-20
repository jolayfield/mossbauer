import numpy as np
import pytest
import yaml
from mossbauer_dft import plan as P
from mossbauer_dft import references as R
from mossbauer_dft.calibration import Calibration
from test_plan import XYZ, fe1, fe1_plan, _home  # noqa: F401
from mossbauer_dft.structure import Structure, structure_from_cif_text


def test_bundled_data_wired():
    from mossbauer_dft import data
    assert all(data.available().values())
    cal = {e["id"]: e for e in R.list_calibrations(include_user=False)}
    ver = {k for k, e in cal.items() if e.get("verified")}
    assert ver == {"mcwilliams2017_b3lyp_cppp", "mcwilliams2017_b3lyp_def2tzvp"}
    assert len(data.load_training_set()) == 136 and data.load_method_rules()["rules"]


def test_refset_definition_declares_missing_structures():
    d = R.refset_definition("mcwilliams2017")
    assert len(d["compounds"]) == 35 and d["structure_status"] == "missing"
    assert all(c["structure"] == "missing" for c in d["compounds"])
    ready, skipped = R.resolve_refset("mcwilliams2017")
    assert not ready and len(skipped) == 35


def _xyz(rng, charge_shift=0.0):
    return f"3\nc\nFe 0 0 0\nCl 2.2 0 {rng}\nCl -2.2 0 0\n"


def test_refset_setup_import_fit_roundtrip(tmp_path, fe1):
    from mossbauer_dft.orca_parse import parse_orca_text
    store = tmp_path / "store"
    ids = ["McW2", "McW3", "McW4", "McW5", "McW7", "McW8", "McW9"]
    for i in ids:
        (tmp_path / f"{i}.xyz").write_text("3\nc\nFe 0 0 0\nCl 2.2 0 0\nCl -2.2 0 0\n")
        c = next(c for c in R.refset_definition("mcwilliams2017")["compounds"] if c["id"] == i)
        R.import_structure("mcwilliams2017", i, tmp_path / f"{i}.xyz", charge=0, multiplicity=c["multiplicity"], store=store)
    plan_path = tmp_path / "plan" / "plan.yaml"
    P.save_plan(fe1_plan(fe1), plan_path)
    with pytest.raises(P.NotApprovedError):
        R.refset_setup(plan_path, tmp_path / "rs", store=store)
    P.approve_file(plan_path, "t")
    man = R.refset_setup(plan_path, tmp_path / "rs", store=store)
    assert len(man["compounds"]) == 7 and {s["id"] for s in man["skipped"]} >= {"McW1", "McW6"}
    sp = (tmp_path / "rs" / "McW2" / "McW2_sp.inp").read_text()
    assert "xyzfile 0 5 McW2_opt.xyz" in sp and "B3LYP" in sp and (tmp_path / "rs" / "McW2" / "McW2_opt.inp").exists()
    assert "afterok" in (tmp_path / "rs" / "submit_refset.sh").read_text()
    # fabricate outputs with a known linear law: delta = -0.30 (rho - 14000) + 0.9
    from orca_synthetic import B3LYP, orca_output
    base = orca_output(B3LYP)
    import re
    m = re.search(r"Electron density at Fe.*|rho\(0\).*", base)
    assert "RHO" in base.upper()
    rng = np.random.default_rng(1)
    for k, c in enumerate(man["compounds"]):
        rho = 13990.0 + 3.0 * k + rng.normal(0, 0.2)
        txt = _set_rho(base, rho)
        (tmp_path / "rs" / c["dir"] / f"{c['sp_stem']}.out").write_text(txt)
        c["_rho"] = rho
    # overwrite manifest delta_exp with synthetic law so fit is checkable
    mp = tmp_path / "rs" / "refset_manifest.yaml"
    mm = yaml.safe_load(mp.read_text())
    for c in mm["compounds"]:
        k = [x["id"] for x in man["compounds"]].index(c["id"])
        c["delta_exp"] = -0.30 * (man["compounds"][k]["_rho"] - 14000) + 0.9
    mp.write_text(yaml.safe_dump(mm))
    e = R.refset_fit(tmp_path / "rs", n_boot=300)
    assert e["alpha"] == pytest.approx(-0.30, abs=1e-6) and e["stats"]["n"] == 7 and e["stats"]["loo_mae"] < 1e-6
    assert e["fingerprint"]["functional"].upper() == "B3LYP" and e["source"] == "project_fit"
    assert (R.user_calibration_dir() / f"{e['id']}.yaml").exists()
    # a later plan with the same protocol finds the fitted calibration by lookup
    p = fe1_plan(fe1)
    assert p["calibration"]["strategy"] == "lookup" and p["calibration"]["lookup"]["source"] == "project_fit"
    assert p["calibration"]["lookup"]["id"] == e["id"] and p["calibration"]["lookup"]["protocol_matched"]


def _set_rho(text, rho):
    import re
    out, n = [], 0
    for ln in text.splitlines():
        if re.search(r"\brho\b|RHO", ln) and re.search(r"\d+\.\d{4,}", ln) and n < 1 and "Nucleus" not in ln:
            pass
        out.append(ln)
    # replace the first floating value on the 'Electron density' line(s)
    txt = "\n".join(out)
    from mossbauer_dft.orca_parse import parse_orca_text
    r = parse_orca_text(txt)
    old = r.fe_sites[0].rho0
    return txt.replace(f"{old:.9f}", f"{rho:.9f}") if f"{old:.9f}" in txt else _replace_any(txt, old, rho)


def _replace_any(txt, old, rho):
    import re
    for fmt in ("{:.9f}", "{:.8f}", "{:.7f}", "{:.6f}", "{:.5f}", "{:.4f}"):
        s = fmt.format(old)
        if s in txt:
            return txt.replace(s, f"{rho:.{len(s.split('.')[1])}f}")
    raise AssertionError("could not locate rho0 text")


def test_fit_on_synthetic_data_statistics():
    rng = np.random.default_rng(0)
    rho = np.linspace(13900, 14100, 12)
    delta = -0.3 * (rho - 14000) + 0.9 + rng.normal(0, 0.03, rho.size)
    cal = Calibration.fit(rho, delta, n_boot=800, seed=3)
    assert cal.alpha == pytest.approx(-0.3, abs=0.002) and cal.stats["loo_mae"] < 0.06
    pr = cal.predict(14000.0)
    assert pr.pi[0] < pr.delta < pr.pi[1] and pr.pi_analytic[0] < 0.9 < pr.pi_analytic[1]


def test_cif_light_parser():
    cif = """data_x
_cell_length_a 10
_cell_length_b 10
_cell_length_c 10
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_symmetry_equiv_pos_as_xyz
x,y,z
-x,-y,-z
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Fe1 Fe 0.10(1) 0.0 0.0
Cl1 Cl 0.30 0.0 0.0
"""
    s = structure_from_cif_text(cif)
    assert s.symbols.count("Fe") == 2 and s.symbols.count("Cl") == 2
    assert s.coords[0] == pytest.approx((1.0, 0.0, 0.0))
