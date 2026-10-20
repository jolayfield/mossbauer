import itertools
import math

import pytest

from mossbauer_dft import structure_sources as SS
from mossbauer_dft.structure import Structure

pytest.importorskip("rdkit")


def _angles(s: Structure, fe: int, nbrs: list[int]) -> list[float]:
    out = []
    for a, b in itertools.combinations(nbrs, 2):
        u = [s.coords[a][k] - s.coords[fe][k] for k in range(3)]
        v = [s.coords[b][k] - s.coords[fe][k] for k in range(3)]
        cos = sum(x * y for x, y in zip(u, v)) / (math.hypot(*u) * math.hypot(*v))
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
    return sorted(out)


def _sphere(r):
    e = SS.fe_environment(r.structure)[0]
    return e, [n["atom"] for n in e["neighbours"]]


@pytest.mark.parametrize("smi,charge,cn,ideal", [
    ("Cl[Fe-](Cl)(Cl)Cl", -1, 4, [109.47] * 6),
    ("[Fe+3].[Cl-].[Cl-].[Cl-].[Cl-]", -1, 4, [109.47] * 6),                          # ionic notation is bonded for you
    ("O->[Fe+2](<-O)(<-O)(<-O)(<-O)<-O", 2, 6, [90.0] * 12 + [180.0] * 3),
    ("N#C[Fe-4](C#N)(C#N)(C#N)(C#N)C#N", -4, 6, [90.0] * 12 + [180.0] * 3),
    ("[S-](c1ccccc1)[Fe+2]([S-]c1ccccc1)([S-]c1ccccc1)[S-]c1ccccc1", -2, 4, [109.47] * 6),
])
def test_smiles_polyhedra(smi, charge, cn, ideal):
    r = SS.from_smiles(smi)
    e, nb = _sphere(r)
    assert r.charge == charge and e["cn"] == cn
    assert max(abs(a - b) for a, b in zip(_angles(r.structure, e["fe"], nb), ideal)) < 4.0
    assert not SS.close_contacts(r.structure, 0.9)
    assert any("starting point" in w for w in r.warnings)


def test_restrained_fe_donor_distances():
    r = SS.from_smiles("[S-](c1ccccc1)[Fe+2]([S-]c1ccccc1)([S-]c1ccccc1)[S-]c1ccccc1")
    e, _ = _sphere(r)
    assert all(abs(n["distance"] - SS.FE_DONOR_DIST["S"]) < 0.05 for n in e["neighbours"])


def test_plain_donor_bonds_become_dative_and_polyhedron_choice():
    td = SS.from_smiles("Cl[Fe](Cl)(n1ccccc1)n1ccccc1")
    sp = SS.from_smiles("Cl[Fe](Cl)(n1ccccc1)n1ccccc1", polyhedron="square planar")
    assert any("dative" in w for w in td.warnings)
    e, nb = _sphere(sp)
    assert e["cn"] == 4 and _angles(sp.structure, e["fe"], nb)[-2:] == pytest.approx([180, 180], abs=4)
    assert _angles(td.structure, *(_sphere(td)[0]["fe"], _sphere(td)[1]))[-1] < 140
    with pytest.raises(ValueError, match="not a CN-4"):
        SS.from_smiles("Cl[Fe-](Cl)(Cl)Cl", polyhedron="octahedral")


def test_chelates_nacnac_and_phen():
    r = SS.from_smiles("CC1=CC(C)=N(c2c(C)cccc2C)->[Fe](Cl)N1c1c(C)cccc1C")
    e, nb = _sphere(r)
    assert e["cn"] == 3 and sorted(n["element"] for n in e["neighbours"]) == ["Cl", "N", "N"]
    assert not SS.close_contacts(r.structure, 0.9)
    r = SS.from_smiles("Cl[Fe]1(Cl)<-n2cccc3ccc4cccn->1c4c32")
    e, nb = _sphere(r)
    assert e["cn"] == 4 and 75 < _angles(r.structure, e["fe"], nb)[0] < 90      # N-Fe-N bite angle


def test_counter_ions_removed():
    r = SS.from_smiles("[Na+].[Na+].Cl[Fe-2](Cl)(Cl)Cl")
    assert r.charge == -2 and r.structure.formula == "Cl4Fe"
    r = SS.from_smiles("[Fe+2].O.O.O.O.O.O.[Cl-].[Cl-]")
    assert r.charge == 2 and _sphere(r)[0]["cn"] == 6
    with pytest.raises(ValueError):
        SS.from_smiles("not a smiles")
    with pytest.raises(ValueError, match="not supported"):
        SS.from_smiles("Cl[Fe-7](Cl)(Cl)(Cl)(Cl)(Cl)(Cl)(Cl)(Cl)(Cl)Cl")      # 10 sites


def _fe_c(s):
    import numpy as np
    X = np.array(s.coords)
    fe = s.fe_indices[0]
    d = np.linalg.norm(X - X[fe], axis=1)
    return sorted(float(d[i]) for i in range(len(s)) if s.symbols[i] == "C" and d[i] < 2.4)


NACNAC = "CC1=C(C)C(C)=N(c2c(C)cccc2C)->[Fe{q}]N1c1c(C)cccc1C"


@pytest.mark.parametrize("cid,smi,n_c,lo,hi", [
    ("Cp2Fe", "[cH-]1cccc1.[cH-]1cccc1.[Fe+2]", 10, 1.95, 2.12),                 # ferrocene Fe-C 2.05
    ("lxylFeC6H6", NACNAC.format(q="") + ".c1ccccc1", 6, 2.03, 2.22),             # eta6-benzene Fe-C 2.13-2.14
    ("lxylFeInd", NACNAC.format(q="+") + ".[cH-]1ccc2ccccc12", 5, 2.03, 2.20),     # eta5-indenyl Fe-C 2.09-2.14
])
def test_haptic_ligands_match_bundled_geometries(cid, smi, n_c, lo, hi):
    from pathlib import Path
    from mossbauer_dft import references as R
    c = next(x for x in R.refset_definition("fe1_calib18")["compounds"] if x["id"] == cid)
    ref = Structure.from_xyz(Path(R.__file__).parent / "data" / "refsets" / c["structure_file"])
    r = SS.from_smiles(smi)
    assert r.structure.formula == ref.formula and r.charge == c["charge"]
    fec = _fe_c(r.structure)
    assert len(fec) >= n_c and lo < fec[0] and fec[n_c - 1] < hi
    assert abs(sum(fec[:n_c]) / n_c - sum(_fe_c(ref)[:n_c]) / n_c) < 0.08
    assert not SS.close_contacts(r.structure, 0.9)


@pytest.mark.parametrize("smi,poly,labels", [
    ("Cl[Fe](Cl)(n1ccccc1)n1ccccc1", "square planar", {"py trans, Cl trans", "py cis, Cl cis"}),
    ("Cl[Fe](Cl)(Cl)(<-O)(<-O)<-O", None, {"mer-H2O3, mer-Cl3", "fac-H2O3, fac-Cl3"}),
    ("Cl[Fe]%10%11%12%13Cl.c1ccn%10c(c1)-c1ccccn1%11.c1ccn%12c(c1)-c1ccccn1%13", None, {"Cl cis", "Cl trans"}),
    ("Cl[Fe]%10%11(N#CC)(N#CC)Cl.c1ccn%10c(c1)-c1ccccn1%11", None, {"MeCN trans, Cl cis", "MeCN cis, Cl cis", "MeCN cis, Cl trans"}),
    ("Cl[Fe-](Cl)(Cl)(Cl)n1ccccc1", None, {"py eq", "py ax"}),
    ("O->[Fe+2](<-O)(<-O)(<-O)(<-O)<-O", None, {"only arrangement"}),
])
def test_geometric_isomers(smi, poly, labels):
    o = SS.coordination_options(smi, polyhedron=poly)
    assert set(o["isomers"]) == labels


def test_isomer_choice_changes_geometry():
    smi = "Cl[Fe-](Cl)(Cl)(Cl)(n1ccccc1)n1ccccc1"
    labels = SS.coordination_options(smi)["isomers"]
    for n, lab in enumerate(labels):
        r = SS.from_smiles(smi, isomer=n)
        e, nb = _sphere(r)
        ns = [a for a in nb if r.structure.symbols[a] == "N"]
        ang = _angles(r.structure, e["fe"], ns)[0]
        assert r.isomer == lab and (ang > 165 if "trans" in lab else ang < 100)
    with pytest.raises(ValueError, match="does not exist"):
        SS.from_smiles(smi, isomer=5)


def test_pi_acceptor_and_high_cn():
    r = SS.from_smiles("[O+]#[C-]->[Fe](<-[C-]#[O+])(<-[C-]#[O+])(<-[C-]#[O+])<-[C-]#[O+]")
    assert all(abs(n["distance"] - SS.FE_PI_ACCEPTOR_C) < 0.05 for n in _sphere(r)[0]["neighbours"] if n["element"] == "C")
    for smi, cn, shape in [("Cl[Fe-3](Cl)(Cl)(Cl)(Cl)(Cl)Cl", 7, "pentagonal bipyramidal"),
                           ("Cl[Fe-4](Cl)(Cl)(Cl)(Cl)(Cl)(Cl)Cl", 8, "square antiprismatic")]:
        r = SS.from_smiles(smi)
        assert _sphere(r)[0]["cn"] == cn and r.polyhedron == shape and not SS.close_contacts(r.structure, 1.5)


def test_molblock_2d_drawing_with_plain_metal_bonds():
    from rdkit import Chem
    from rdkit.Chem import AllChem
    m = Chem.RWMol(Chem.MolFromSmiles("Cl[Fe]1(Cl)<-n2ccccc2-c2cccc[n]->12"))
    for b in m.GetBonds():
        if b.GetBondType() == Chem.BondType.DATIVE:
            b.SetBondType(Chem.BondType.SINGLE)          # what a drawing editor produces
    AllChem.Compute2DCoords(m)
    r = SS.from_molblock(Chem.MolToMolBlock(m, kekulize=False))
    assert r.structure.formula == "C10H8Cl2FeN2" and _sphere(r)[0]["cn"] == 4


def test_read_any_and_checks():
    xyz = "2\nx\nFe 0 0 0\nCl 0 0 2.2\n"
    r = SS.read_any(xyz, "a.xyz")
    assert r.structure.formula == "ClFe" and SS.fe_environment(r.structure)[0]["cn"] == 1
    assert SS.close_contacts(Structure(["Fe", "Fe"], [(0, 0, 0), (0, 0, 0.2)])) == [(0, 1, 0.2)]
    assert "odd" in SS.electron_parity_hint(r.structure, 0)            # 26 + 17 = 43 electrons
    sdf = SS.from_smiles("Cl[Fe-](Cl)(Cl)Cl")
    from rdkit import Chem
    mol = Chem.MolFromXYZBlock(sdf.structure.to_xyz_text())
    assert SS.read_any(Chem.MolToMolBlock(mol), "x.mol").structure.formula == "Cl4Fe"
    assert SS.polyhedra_for(4) == ["tetrahedral", "square planar"] and SS.polyhedra_for(9) == []
    assert SS.coordination_options("CCO") == {}                                     # no metal: nothing to choose


def test_multiplicity_parity_both_cases():
    fe = Structure(["Fe"], [(0.0, 0.0, 0.0)])                                  # 26 electrons (even)
    assert SS.multiplicity_allowed(fe, 0, 1) and SS.multiplicity_allowed(fe, 0, 5)
    assert not SS.multiplicity_allowed(fe, 0, 2) and not SS.multiplicity_allowed(fe, 0, 4)
    assert SS.multiplicity_allowed(fe, 1, 4) and not SS.multiplicity_allowed(fe, 1, 3)   # Fe+ : 25 electrons (odd)
    assert "even" in SS.electron_parity_hint(fe, 0).split("->")[0]
