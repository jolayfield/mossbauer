import pytest
from mossbauer_dft.orca_parse import parse_orca_text, fingerprint_from_input_text
from orca_synthetic import B3LYP, MN15, OPT_SVP, Site, orca_output


def parse(text):
    return parse_orca_text(text, "syn")


def test_property_output_all_fields():
    site = Site(eigs=(-0.315, -0.742, 1.057), rho0=11821.407315962, mulliken=(0.437125, 2.981406), loewdin=(0.318842, 2.795263))
    r = parse(orca_output(charge=0, mult=6, s=1.5, s2=3.794518, energy=-2718.302159846270, scf_cycles=37, sites=(site,),
                          extra_deck=["%scf", "  FlipSpin 4, 5", "  FinalMs 1.5", "end"]))
    assert r.normal_termination and r.scf_converged and r.scf_cycles == 37 and r.ok
    assert r.final_energy == pytest.approx(-2718.302159846270, abs=1e-9)
    assert r.s2 == pytest.approx(3.794518) and r.s2_ideal == pytest.approx(3.75)
    assert r.multiplicity == 6 and r.charge == 0                    # high-spin reference of a FlipSpin run
    (s,) = r.fe_sites
    assert s.index == 0 and s.element == "Fe"
    assert s.rho0 == pytest.approx(11821.407315962, abs=1e-9)
    assert s.v_eigs_orca == pytest.approx((-0.315, -0.742, 1.057))
    assert s.vzz == pytest.approx(1.057) and s.vzz_sign == 1
    assert s.eta == pytest.approx(site.eta, abs=2e-6) and s.eta_orca == pytest.approx(site.eta, abs=1e-6)
    assert s.deq_orca_mm_s == pytest.approx(site.deq_mhz / 11.6, abs=1e-6) and s.q_orca_barn == pytest.approx(0.16)
    assert s.e2qQ_mhz == pytest.approx(site.e2qq_mhz, abs=1e-6)
    assert s.deq() == pytest.approx(s.deq_orca_mm_s, abs=2e-4)     # recomputed with Q = 0.16 b
    assert s.deq(q_barn=0.08) == pytest.approx(s.deq() / 2)         # configurable Q
    assert s.mulliken_spin == pytest.approx(2.981406) and s.loewdin_spin == pytest.approx(2.795263)
    assert s.mulliken_charge == pytest.approx(0.437125)
    assert s.v_matrix[0][1] == pytest.approx(site.matrix()[0][1], abs=1e-7)
    assert abs(s.trace_residual) < 1e-5


def test_negative_vzz_sign():
    s = parse(orca_output(MN15, sites=(Site(eigs=(0.27, 1.49, -1.76)),))).fe_sites[0]
    assert s.vzz_sign == -1 and s.deq() < 0 and s.deq_orca_mm_s < 0
    assert s.deq() == pytest.approx(s.deq_orca_mm_s, abs=2e-3)


def test_dimer_two_fe_sites():
    sites = (Site(index=0, rho0=11817.642901377, mulliken=(0.794, 3.688214), loewdin=(0.081, 3.561902)),
             Site(index=6, rho0=11817.642925804, mulliken=(0.794, 3.688240), loewdin=(0.081, 3.561877)))
    r = parse(orca_output(mult=9, s=4.0, s2=20.41, sites=sites))
    assert [s.index for s in r.fe_sites] == [0, 6]
    assert r.fe_sites[0].rho0 == pytest.approx(11817.642901377, abs=1e-9)
    assert r.fe_sites[1].rho0 == pytest.approx(11817.642925804, abs=1e-9)
    assert r.fe_sites[1].mulliken_spin == pytest.approx(3.688240)
    assert r.fe_sites[1].loewdin_spin == pytest.approx(3.561877)
    assert r.multiplicity == 9 and len(r.summary()) == 2


def test_fingerprints():
    t = parse(orca_output()).fingerprint
    assert (t.functional, t.basis, t.fe_basis, t.grid, t.special_grid_int_acc, t.special_grid_atoms, t.ri) == \
        ("TPSSh", "def2-TZVP", "CP(PPP)", "DefGrid3", "7", "26", "NoRIJCOSX")
    assert t.program == "ORCA 6.1.0"
    m = parse(orca_output(MN15)).fingerprint
    assert m.functional == "MN15" and m.fe_basis == "CP(PPP)"
    assert parse(orca_output(B3LYP)).fingerprint.functional == "B3LYP"
    hard, soft = t.compare(m)
    assert any("functional" in h for h in hard) and not any("basis:" in h for h in hard)


def test_fingerprint_from_input_variants():
    fp = fingerprint_from_input_text("! UKS PBE0 def2-SVP RIJCOSX def2/J DefGrid2 TightSCF CPCM(THF)\n")
    assert (fp.functional, fp.basis, fp.ri, fp.grid, fp.solvent) == ("PBE0", "def2-SVP", "RIJCOSX", "DefGrid2", "THF")


def test_optimization_output():
    r = parse(orca_output(OPT_SVP, mult=2, s=0.5, s2=0.791833, energy=-2433.618204715093, opt=True, eprnmr=False, sites=()))
    assert r.is_optimization and r.geom_converged is True and r.normal_termination
    assert r.s2 == pytest.approx(0.791833) and r.fe_sites == []
    assert r.final_energy == pytest.approx(-2433.618204715093, abs=1e-9)


def test_failure_modes_detected():
    txt = orca_output()
    bad = parse_orca_text(txt.replace("ORCA TERMINATED NORMALLY", "ORCA finished by error termination in SCF"))
    assert not bad.normal_termination and bad.error_termination and not bad.ok
    nc = parse_orca_text(txt.replace("SCF CONVERGED AFTER", "SCF NOT CONVERGED AFTER"))
    assert nc.scf_converged is False and not nc.ok and any("converge" in w for w in nc.warnings)
    empty = parse_orca_text("garbage")
    assert empty.fe_sites == [] and empty.scf_converged is None and not empty.ok
