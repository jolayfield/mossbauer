import re
import yaml
import pytest
from mossbauer_dft import plan as P
from mossbauer_dft import references as R
from mossbauer_dft.structure import read_structure
from test_plan import _home  # noqa: F401  (autouse fixture isolating MOSSBAUER_DFT_HOME)

PREP = __import__("pathlib").Path(__file__).parent / "data" / "calib_prepared"


def test_fe1_calib18_definition():
    d = R.refset_definition("fe1_calib18")
    cs = d["compounds"]
    assert len(cs) == 18 and d["structure_status"] == "bundled"
    ready, skipped = R.resolve_refset("fe1_calib18")
    assert len(ready) == 18 and not skipped
    assert sum(c["delta_T_K"] == 80.0 for c in cs) == 6 and sum(c["delta_T_K"] == 4.2 for c in cs) == 12
    assert sum(c["deq_abs_exp_mm_s"] is not None for c in cs) == 9


def test_fe1_calib18_geometries_match_charge_and_multiplicity():
    for c in R.resolve_refset("fe1_calib18")[0]:
        s = read_structure(c["xyz_path"])
        assert len(s.fe_indices) == 1, c["id"]
        # electron count parity: (sum Z - charge) odd/even must equal (multiplicity - 1) parity
        from mossbauer_dft.elements import atomic_numbers
        n_e = sum(atomic_numbers(s.symbols)) - c["charge"]
        assert (n_e - (c["multiplicity"] - 1)) % 2 == 0, c["id"]


def test_fe1_calib18_flags():
    f = {c["id"]: c["flags"] for c in R.refset_definition("fe1_calib18")["compounds"]}
    assert "b3lyp_outlier" in f["Cp2Fe"]
    assert "high_leverage_single_Fe(VI)_point" in f["FeO4_2m"] and "delta_T_unverified" in f["FeO4_2m"]
    assert all("rdkit_built_geometry" in f[k] for k in ("lxylFeCO3", "lxylFeC6H6", "lxylFeInd"))
    caveats = R.refset_definition("fe1_calib18")["caveats"]
    assert all(k in caveats for fl in f.values() for k in fl)


def test_default_refset_falls_back_when_mcwilliams_structures_missing(tmp_path):
    assert R.default_refset_name() == "fe1_calib18"
    assert R.refset_definition()["id"] == "fe1_calib18"


def test_default_refset_prefers_mcwilliams_when_structures_available(tmp_path):
    store = tmp_path / "s"
    (tmp_path / "x.xyz").write_text("3\nc\nFe 0 0 0\nCl 2.2 0 0\nCl -2.2 0 0\n")
    R.import_structure("mcwilliams2017", "McW2", tmp_path / "x.xyz", charge=0, multiplicity=5, store=store)
    assert R.default_refset_name(store) == "mcwilliams2017"


def _norm(txt):
    txt = re.sub(r"\S+\.xyz", "X.xyz", txt)
    return [l.strip() for l in txt.splitlines() if l.strip()]


@pytest.fixture
def setup_dir(tmp_path):
    xyz = R.resolve_refset("fe1_calib18", ids=["FeSPh4"])[0][0]["xyz_path"]
    plan = P.make_plan(xyz, -2, 5, name="t", oxidation_state=2, solvent="water", overrides=dict(nprocs=8))
    path = tmp_path / "plan.yaml"
    P.save_plan(plan, path)
    plan = P.approve_plan(P.load_plan(path), "test")
    P.save_plan(plan, path)
    R.refset_setup(path, tmp_path / "rs")
    return tmp_path / "rs"


def test_setup_decks_equivalent_to_prepared_inputs(setup_dir):
    for c in ("FeSPh4", "Cp2Fe", "FeO4_2m", "lxylFeInd"):
        got = _norm((setup_dir / c / f"{c}_sp.inp").read_text())
        want = _norm((PREP / f"fe1_cal_prop_b3lyp_{c}.inp").read_text())
        assert got == want, c
    # TPSSh/B3LYP differ only in the functional keyword
    tp = _norm((PREP / "fe1_cal_prop_tpssh_FeSPh4.inp").read_text())
    b3 = _norm((PREP / "fe1_cal_prop_b3lyp_FeSPh4.inp").read_text())
    assert [a for a, b in zip(tp, b3) if a != b] == ["! UKS TPSSh def2-TZVP NoRIJCOSX DefGrid3 TightSCF CPCM(water)"]


def test_opt_deck_differs_only_by_documented_rijcosx(setup_dir):
    got = _norm((setup_dir / "FeSPh4" / "FeSPh4_opt.inp").read_text())
    want = _norm((PREP / "fe1_cal_opt_FeSPh4.inp").read_text())
    diff = [(a, b) for a, b in zip(got, want) if a != b]
    assert len(got) == len(want) and len(diff) == 1
    assert "NoRIJCOSX" in diff[0][0] and "RIJCOSX def2/J" in diff[0][1]


def test_manifest_carries_flags(setup_dir):
    m = yaml.safe_load((setup_dir / "refset_manifest.yaml").read_text())
    assert m["refset"] == "fe1_calib18" and len(m["compounds"]) == 18
    fl = {c["id"]: c["flags"] for c in m["compounds"]}
    assert "b3lyp_outlier" in fl["Cp2Fe"]
