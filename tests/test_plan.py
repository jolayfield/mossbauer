import copy
import pytest
import yaml
from mossbauer_dft import plan as P
from mossbauer_dft import references as R
from mossbauer_dft.structure import Structure

XYZ = """7
Fe(I) thiolate-borate-like fragment (synthetic)
Fe 0.0 0.0 0.0
S  2.2 0.0 0.0
S  -1.1 1.9 0.0
S  -1.1 -1.9 0.0
C  3.0 1.3 0.0
H  3.9 1.3 0.0
H  2.7 2.2 0.0
"""


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSSBAUER_DFT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MOSSBAUER_DFT_SITE", raising=False)
    from mossbauer_dft.site import set_default_site
    set_default_site("example")


@pytest.fixture
def fe1():
    return Structure.from_xyz_text(XYZ)


def fe1_plan(s, **kw):
    # charge -1, quartet: 1 Fe(26)+3 S(48)+C(6)+3H = 83 electrons + 1 = 84 -> 3 unpaired is even-electron? check parity in make_plan
    return P.make_plan(s, -1, 4, name="cmpd", oxidation_state=1, **kw)


def test_fe1_s32_thiolate_rule_and_methods(fe1):
    p = fe1_plan(fe1)
    assert p["status"] == "draft" and p["approval"] is None
    assert p["rule_evaluation"]["selected_rule"] == "R01_FeI_lowcoordinate"
    m = p["methods"]
    assert m["deq"]["functional"] == "B3LYP" and m["deq"]["crosscheck_functional"] == "TPSSh"
    assert m["delta"]["functional"] == "B3LYP" and m["delta"]["fe_basis"] == "CP(PPP)"
    funcs = {j.get("functional") for j in p["jobs"] if j["kind"] == "mossbauer_sp"}
    assert funcs == {"B3LYP", "TPSSh"} and "MN15" not in funcs
    assert p["rule_evaluation"]["justification"] and p["rule_evaluation"]["citations"]
    assert any("MN15" in x for x in p["rule_evaluation"]["global_constraints"])


@pytest.mark.parametrize("key", ["deq_functional", "delta_functional", "crosscheck_functional"])
def test_mn15_never_allowed_for_properties(fe1, key):
    with pytest.raises(P.PlanError, match="MN15"):
        fe1_plan(fe1, overrides={key: "MN15"})


def test_mn15_allowed_for_geometry_only(fe1):
    p = fe1_plan(fe1, overrides={"geometry": {"mode": "optimize", "functional": "MN15", "basis": "def2-SVP"}})
    assert p["jobs"][0]["method"] == "MN15/def2-SVP"
    assert all(j.get("functional") != "MN15" for j in p["jobs"])


def test_radical_ligand_selects_R02_and_blocks_until_bs_spec(fe1):
    p = fe1_plan(fe1, radical_ligand=True)
    assert p["rule_evaluation"]["selected_rule"] == "R02_FeI_radical_or_BS"
    assert p["open_items"]
    with pytest.raises(P.PlanError, match="open items"):
        P.approve_plan(p)
    p2 = fe1_plan(fe1, radical_ligand=True, overrides={"broken_symmetry": {"kind": "flipspin", "flip_atoms": [1], "final_ms": 0.5}})
    assert not p2["open_items"]
    assert any(j.get("broken_symmetry") for j in p2["jobs"]) and any(j["id"].endswith("_hs") for j in p2["jobs"])


def test_other_rules(fe1):
    s = fe1
    assert P.make_plan(s, 0, 5, oxidation_state=2)["rule_evaluation"]["selected_rule"] == "R04_FeII_highspin"
    p = P.make_plan(s, -1, 6, oxidation_state=3)  # S=5/2
    assert p["rule_evaluation"]["selected_rule"] == "R06_FeIII"
    assert P.make_plan(s, -1, 4, oxidation_state=None)["rule_evaluation"]["selected_rule"] == "R99_default"
    assert P.make_plan(s, 0, 7, oxidation_state=1)["rule_evaluation"]["selected_rule"] == "R99_default"   # Fe(I) S=3 matches nothing


def test_parity_check(fe1):
    with pytest.raises(P.PlanError, match="parity"):
        P.make_plan(fe1, 0, 4, oxidation_state=1)


def test_approval_gate(fe1, tmp_path):
    from mossbauer_dft import workflow as W
    pth = tmp_path / "plan" / "plan.yaml"
    p = fe1_plan(fe1)
    P.save_plan(p, pth)
    with pytest.raises(P.NotApprovedError):
        W.write_inputs(pth, tmp_path / "in")
    assert not (tmp_path / "in").exists() or not list((tmp_path / "in").glob("*.inp"))
    plan = P.approve_file(pth, "tester")
    assert plan["approval"]["by"] == "tester" and plan["approval"]["plan_hash"] == plan["plan_hash"]
    r = W.write_inputs(pth, tmp_path / "in")
    assert any(f.endswith("_opt.inp") for f in r["files"]) and (tmp_path / "in" / "orca_job.slurm").exists()
    deck = (tmp_path / "in" / "cmpd_sp_b3lyp.inp").read_text()
    assert "! UKS B3LYP def2-TZVP NoRIJCOSX DefGrid3 TightSCF" in deck and 'NewGTO Fe "CP(PPP)"' in deck
    assert deck.index("%eprnmr") > deck.index("* xyzfile")
    assert r["site"] == "example"
    # tamper after approval -> refused
    d = yaml.safe_load(pth.read_text())
    d["methods"]["deq"]["functional"] = "TPSSh"
    pth.write_text(yaml.safe_dump(d))
    with pytest.raises(P.NotApprovedError, match="changed after approval"):
        W.write_inputs(pth, tmp_path / "in2")


def test_update_resets_to_draft(fe1, tmp_path):
    pth = tmp_path / "plan.yaml"
    P.save_plan(fe1_plan(fe1), pth)
    P.approve_file(pth)
    p = P.update_plan(P.load_plan(pth), overrides={"nprocs": 8})
    assert p["status"] == "draft" and p["resources"]["nprocs"] == 8


def test_calibration_default_is_refset_when_geometry_differs(fe1):
    p = fe1_plan(fe1)
    c = p["calibration"]
    assert c["strategy"] == "compute_refset" and "lookup" not in c
    cand = {x["id"]: x for x in c["candidates"]}
    assert cand["mcwilliams2017_b3lyp_cppp"]["verified"] and not cand["mcwilliams2017_b3lyp_cppp"]["protocol_matched"]
    assert any("geometry" in s for s in cand["mcwilliams2017_b3lyp_cppp"]["soft"])


def test_orca_major_version_blocks_literature_lookup(fe1):
    ov = {"geometry": {"mode": "as_given", "as_given_label": "BP86-D3BJ/ZORA-def2-TZVP"}}
    p = fe1_plan(fe1, overrides=ov)      # plan uses ORCA 6.x, McWilliams used ORCA 3.0.3
    assert p["calibration"]["strategy"] == "compute_refset"
    c = {x["id"]: x for x in p["calibration"]["candidates"]}["mcwilliams2017_b3lyp_cppp"]
    assert c["geometry_match"] and not c["program_match"] and not c["protocol_matched"]


def test_calibration_lookup_when_protocol_matches(fe1):
    ov = {"geometry": {"mode": "as_given", "as_given_label": "BP86-D3BJ/ZORA-def2-TZVP"}, "orca_version": "3.0.3"}
    p = fe1_plan(fe1, overrides=ov)
    assert p["calibration"]["strategy"] == "lookup" and p["calibration"]["lookup"]["id"] == "mcwilliams2017_b3lyp_cppp"
    assert p["calibration"]["lookup"]["verified"] is True and not [j for j in p["jobs"] if j["kind"] == "opt"]


def test_lookup_requires_overrides_for_mismatch_and_unverified(fe1, monkeypatch):
    # literature-lookup logic only: hide bundled project fits (e.g. fe1_calib18 CPCM fits)
    _orig = P.R.list_calibrations
    monkeypatch.setattr(P.R, "list_calibrations", lambda *a, **k: [e for e in _orig(*a, **k) if e.get("source") != "project_fit"])
    with pytest.raises(P.PlanError):
        fe1_plan(fe1, overrides={"calibration_strategy": "lookup"})
    p = fe1_plan(fe1, overrides={"calibration_strategy": "lookup", "allow_protocol_mismatch": True})
    assert any("LOUD CAVEAT" in c and "NOT protocol-matched" in c for c in p["calibration"]["caveats"])
    # unverified entries are unusable (no numeric constants) or need the explicit override
    with pytest.raises(P.PlanError):
        fe1_plan(fe1, overrides={"calibration_strategy": "lookup", "delta_functional": "TPSSh", "allow_protocol_mismatch": True})
    p = fe1_plan(fe1, overrides={"calibration_strategy": "lookup", "delta_functional": "TPSSh",
                                 "allow_protocol_mismatch": True, "allow_unverified_calibration": True})
    assert any("UNVERIFIED" in c for c in p["calibration"]["caveats"])


def test_user_refs_strategy(fe1):
    refs = [{"name": "ref1", "xyz": "r1.xyz", "charge": 0, "multiplicity": 2, "delta_exp": 0.5}]
    p = fe1_plan(fe1, overrides={"calibration_strategy": "user_refs", "user_refs": refs})
    assert p["calibration"]["strategy"] == "user_refs" and any("single reference" in c for c in p["calibration"]["caveats"])
    with pytest.raises(P.PlanError):
        fe1_plan(fe1, overrides={"calibration_strategy": "user_refs", "user_refs": [{"name": "x"}]})


def test_cost_model_stated(fe1):
    p = fe1_plan(fe1)
    assert p["cost"]["total_core_hours"] > 0 and "UNVALIDATED" in p["cost"]["model"]["validity"] and "core_hours" in p["cost"]["model"]["description"]
    assert all("core_hours_est" in j for j in p["jobs"])


def test_nitrosyl_detection_selects_R08():
    s = Structure.from_xyz_text("4\n\nFe 0 0 0\nN 1.7 0 0\nO 2.85 0 0\nCl -2.2 0 0\n")
    p = P.make_plan(s, 0, 3, oxidation_state=1)
    assert p["rule_evaluation"]["selected_rule"] == "R08_nitrosyl"


def test_bundled_fe1_calib18_fit_selected_only_for_matching_solvent(fe1):
    # CPCM(water) Fe(I) plan -> bundled project fit (B3LYP, noFeO4 variant preferred)
    p = fe1_plan(fe1, solvent="water")
    c = p["calibration"]
    assert c["strategy"] == "lookup" and c["lookup"]["source"] == "project_fit"
    assert c["lookup"]["id"].startswith("fit_fe1_calib18_b3lyp") and c["lookup"]["id"].endswith("_noFeO4")
    # gas-phase plan: the CPCM fit is not protocol-matched -> compute a reference set
    g = fe1_plan(fe1)
    assert g["calibration"]["strategy"] == "compute_refset"
    cand = {x["id"]: x for x in g["calibration"]["candidates"]}
    assert not cand[c["lookup"]["id"]]["protocol_matched"]


def _large_system():
    lines = ["Fe 0 0 0"] + [f"H {3 + 1.5 * (i % 10):.1f} {1.5 * ((i // 10) % 5):.1f} {1.5 * (i // 50):.1f}" for i in range(150)]
    return Structure.from_xyz_text(f"{len(lines)}\n\n" + "\n".join(lines) + "\n")


def test_R10_large_system_uses_def2tzvp_on_fe_and_its_matching_calibration():
    # "(no CP(PPP))" in R10's description must not select CP(PPP) on Fe
    p = P.make_plan(_large_system(), 0, 5, overrides=dict(orca_version="3.0.3",
                    geometry=dict(mode="as_given", as_given_label="BP86-D3BJ/ZORA-def2-TZVP")))
    assert p["rule_evaluation"]["selected_rule"] == "R10_large_systems"
    d = p["methods"]["delta"]
    assert d["fe_basis"] is None and d["basis"] == "def2-TZVP"
    lk = p["calibration"]["lookup"]
    assert lk["id"] == "mcwilliams2017_b3lyp_def2tzvp" and lk["protocol_matched"] and lk["verified"]


def test_every_rule_calibration_entry_agrees_with_its_fe_basis(fe1):
    system = P.SystemInfo(fe1, -1, 4, 1)
    for rule in P._data.load_method_rules()["rules"]:
        d = rule.get("delta") or {}
        if not d.get("calibration_entry"):
            continue
        fe_basis = P.recommend_methods(rule, system, {})["delta"]["fe_basis"] or "def2-TZVP"
        entry = R.get_calibration_entry(P._tok(d["calibration_entry"]))
        assert R.entry_fingerprint(entry).fe_basis == fe_basis, rule["id"]


def test_rule_fe_basis_must_be_explicit(fe1):
    rule = dict(id="RX", delta=dict(functional="B3LYP", fe_basis="CP(PPP) or def2-TZVP"), deltaEQ=dict(functional="B3LYP"))
    with pytest.raises(P.PlanError, match="fe_basis"):
        P.recommend_methods(rule, P.SystemInfo(fe1, -1, 4, 1), {})


def test_plan_engine_never_recommends_mn15_for_deq(fe1):
    plan = P.make_plan(fe1, -1, 4, oxidation_state=1)
    assert plan["methods"]["deq"]["functional"] in ("B3LYP", "TPSSh") and "MN15" in plan["methods"]["deq"]["forbidden"]
    with pytest.raises(P.PlanError, match="MN15 must not be used"):
        P.make_plan(fe1, -1, 4, oxidation_state=1, overrides=dict(deq_functional="MN15"))
