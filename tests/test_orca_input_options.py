import pytest
from mossbauer_dft.orca_input import (BrokenSymmetry, HighFieldOptions, MethodSpec, build_input, mossbauer_method)
from mossbauer_dft.structure import Structure

XYZ = "3\nfe test\nFe 0.0 0.0 0.0\nCl 0.0 0.0 2.2\nCl 0.0 2.2 0.0\n"


def test_inline_coordinates_and_opt_freq():
    s = Structure.from_xyz_text(XYZ)
    t = build_input("opt", MethodSpec(functional="TPSSh", basis="def2-SVP", fe_basis=None), charge=0,
                    multiplicity=4, structure=s)
    assert t.splitlines()[0] == "! UKS TPSSh def2-SVP NoRIJCOSX Opt DefGrid3 TightSCF"
    assert "* xyz 0 4\nFe" in t and t.rstrip().endswith("*")
    f = build_input("freq", MethodSpec(functional="B3LYP", basis="def2-SVP", fe_basis=None), charge=0, multiplicity=4, structure=s)
    assert " Freq " in f.splitlines()[0]


def test_cpcm_and_rijcosx():
    spec = mossbauer_method("TPSSh", rijcosx=True, solvent="THF")
    t = build_input("mossbauer_sp", spec, charge=0, multiplicity=4, xyz_file="a.xyz")
    assert t.splitlines()[0] == "! UKS TPSSh def2-TZVP RIJCOSX def2/J DefGrid3 TightSCF CPCM(THF)"


def test_brokensym_keyword_and_flipspin():
    spec = mossbauer_method("B3LYP", broken_symmetry=BrokenSymmetry("brokensym", na=5, nb=5))
    t = build_input("mossbauer_sp", spec, charge=0, multiplicity=11, xyz_file="a.xyz")
    assert "BrokenSym 5,5" in t and "FlipSpin" not in t
    with pytest.raises(ValueError):
        BrokenSymmetry("flipspin", (1,)).scf_lines()


def test_highfield_block():
    t = build_input("highfield_sp", mossbauer_method("TPSSh"), charge=0, multiplicity=4, xyz_file="a.xyz")
    tail = t[t.index("%eprnmr"):]
    for key in ("gtensor true", "DTensor ssandso", "DSOC cp", "DSS uno",
                "Nuclei = all Fe {aiso, adip, aorb, fgrad, rho}"):
        assert key in tail
    assert t.index("* xyzfile") < t.index("%eprnmr")
    assert " UNO" in t.splitlines()[0]          # DSS uno needs UNO in the simple-input line


def test_requires_fe_and_valid_args():
    s = Structure.from_xyz_text("1\nx\nNa 0 0 0\n")
    with pytest.raises(ValueError):
        build_input("mossbauer_sp", mossbauer_method("TPSSh"), charge=0, multiplicity=2, structure=s)
    with pytest.raises(ValueError):
        build_input("bogus", mossbauer_method("TPSSh"), charge=0, multiplicity=2, xyz_file="a")
    with pytest.raises(ValueError):
        build_input("sp", mossbauer_method("TPSSh"), charge=0, multiplicity=0, xyz_file="a")


def test_structure_roundtrip():
    s = Structure.from_xyz_text(XYZ)
    assert s.fe_indices == [0] and s.formula == "Cl2Fe"
    assert Structure.from_xyz_text(s.to_xyz_text()).coords == s.coords
