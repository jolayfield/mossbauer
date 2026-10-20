"""Extra ways to get a Structure: SMILES / drawn molfile (RDKit), MOL/SDF/PDB text, and geometry checks.

Geometries built from SMILES or a 2D drawing put the Fe coordination sites (sigma donors, or eta-n ring centroids) on an
idealized polyhedron (CN 1-8), align separately embedded ligands onto them (choosing among the distinct geometric
isomers), then relax with UFF under Fe-ligand restraints: starting points for optimization only, never "as_given"
geometries for property jobs.  RDKit is included in the default installation; everything except `from_smiles` / `from_molblock` / `from_rdkit_text`
works without it.
"""
from __future__ import annotations
import math
import re
from dataclasses import dataclass, field
from pathlib import PurePath

from mossbauer_dft.structure import (MultipleStructuresError, Structure, cif_block_has_atoms, read_structure_text,
                                     split_cif_blocks, split_xyz_blocks, structure_from_cif_text)

METALS = {"Fe", "Co", "Ni", "Mn", "Cr", "Cu", "Zn", "Ru", "Os", "V", "Ti", "Mo", "W", "Pd", "Pt", "Rh", "Ir"}
# typical high-spin Fe-donor distances (A) used as embedding restraints; a rough guide, refined by the optimization
FE_DONOR_DIST = {"H": 1.55, "C": 2.05, "N": 2.10, "O": 2.05, "F": 1.95, "P": 2.30, "S": 2.32, "Cl": 2.28, "Br": 2.43, "I": 2.62, "Se": 2.45}
FE_PI_ACCEPTOR_C = 1.85                       # C of CO / CN- / CNR (triple-bonded carbon donors)
# Fe-(ring centroid) distance by hapticity (ferrocene 1.65, (nacnac)Fe(eta6-C6H6) 1.62, indenyl 1.74 A)
FE_HAPTIC_DIST = {2: 1.95, 3: 1.80, 4: 1.70, 5: 1.66, 6: 1.62, 7: 1.60}
COVALENT_R = {"H": 0.31, "B": 0.84, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57, "Si": 1.11, "P": 1.07, "S": 1.05, "Cl": 1.02,
              "Br": 1.20, "I": 1.39, "Se": 1.20, "Fe": 1.32, "Co": 1.26, "Ni": 1.24, "Cu": 1.32, "Zn": 1.22, "Mn": 1.39}
MAX_AUTO_SITES = 6                            # ionic SMILES are bonded to the metal up to this many sites


@dataclass
class BuildResult:
    structure: Structure
    charge: int                      # sum of formal charges in the SMILES / molfile
    warnings: list[str] = field(default_factory=list)
    source: str = ""
    polyhedron: str = ""
    isomer: str = ""


def _rdkit():
    try:
        from rdkit import Chem, RDLogger
        from rdkit.Chem import AllChem
    except ImportError as e:  # pragma: no cover - exercised only without rdkit
        raise ImportError("RDKit is missing or broken. Run mossbauer doctor and reinstall your downloaded wheel.") from e
    RDLogger.DisableLog("rdApp.*")
    return Chem, AllChem


_DONORS = ("N", "O", "P", "S", "Se")
_S3 = 3 ** -0.5
_SAP_Z, _SAP_R = math.cos(math.radians(59.26)), math.sin(math.radians(59.26))
POLYHEDRA = {  # unit vectors; the first shape per CN is the default
    1: {"terminal": [(0, 0, 1)]},
    2: {"linear": [(0, 0, 1), (0, 0, -1)], "bent": [(1, 0, 0), (math.cos(1.75), math.sin(1.75), 0)]},
    3: {"trigonal planar": [(1, 0, 0), (-0.5, 0.75 ** 0.5, 0), (-0.5, -(0.75 ** 0.5), 0)],
        "T-shaped": [(1, 0, 0), (-1, 0, 0), (0, 1, 0)]},
    4: {"tetrahedral": [(_S3, _S3, _S3), (_S3, -_S3, -_S3), (-_S3, _S3, -_S3), (-_S3, -_S3, _S3)],
        "square planar": [(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)]},
    5: {"trigonal bipyramidal": [(0, 0, 1), (0, 0, -1), (1, 0, 0), (-0.5, 0.75 ** 0.5, 0), (-0.5, -(0.75 ** 0.5), 0)],
        "square pyramidal": [(0, 0, 1), (1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)]},
    6: {"octahedral": [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)],
        "trigonal prismatic": [(0.8165 * math.cos(t), 0.8165 * math.sin(t), z)
                               for z in (0.5774, -0.5774) for t in (0.0, 2.0944, 4.1888)]},
    7: {"pentagonal bipyramidal": [(0, 0, 1), (0, 0, -1)] + [(math.cos(t), math.sin(t), 0) for t in (0, 1.2566, 2.5133, 3.7699, 5.0265)]},
    8: {"square antiprismatic": [(_SAP_R * math.cos(t), _SAP_R * math.sin(t), z) for z, off in ((_SAP_Z, 0.0), (-_SAP_Z, math.pi / 4))
                                 for t in (off, off + math.pi / 2, off + math.pi, off + 3 * math.pi / 2)],
        "cubic": [(x * _S3, y * _S3, z * _S3) for x in (1, -1) for y in (1, -1) for z in (1, -1)]},
}
# named positions used to label isomers on polyhedra whose vertices are not all equivalent
ROLES = {"trigonal bipyramidal": ["ax"] * 2 + ["eq"] * 3, "square pyramidal": ["apical"] + ["basal"] * 4,
         "pentagonal bipyramidal": ["ax"] * 2 + ["eq"] * 5, "T-shaped": ["trans"] * 2 + ["stem"]}
_SHORT = {"c1ccncc1": "py", "O": "H2O", "N": "NH3", "C#[O+]": "CO", "[C-]#[O+]": "CO", "[C-]#N": "CN", "C#N": "CN",
          "c1ccccc1": "C6H6", "[cH-]1cccc1": "Cp", "C1=CC=C[CH-]1": "Cp", "[SH-]": "SH", "C[S-]": "SMe", "CC#N": "MeCN",
          "C1CCOC1": "THF", "CN": "NH2Me", "[Cl-]": "Cl", "[Br-]": "Br", "[I-]": "I", "[F-]": "F", "[OH-]": "OH"}


def polyhedra_for(cn: int) -> list[str]:
    """Names of the idealized polyhedra for `cn` coordination sites (a haptic ring counts as one site; first = default)."""
    return list(POLYHEDRA.get(cn, {}))


def _is_metal(a) -> bool:
    return a.GetSymbol() in METALS


def _bond_order_sum(atom) -> float:
    return sum(b.GetBondTypeAsDouble() for b in atom.GetBonds() if b.GetBondType().name != "DATIVE") + atom.GetNumExplicitHs()


def _metal_sites(mol, m: int) -> list[list[int]]:
    """Coordination sites of metal m: connected groups of its neighbours (one atom = sigma donor, >= 2 bonded atoms = eta-n)."""
    nb = [a.GetIdx() for a in mol.GetAtomWithIdx(m).GetNeighbors()]
    left, sites = set(nb), []
    while left:
        stack, comp = [left.pop()], []
        while stack:
            i = stack.pop()
            comp.append(i)
            for n in mol.GetAtomWithIdx(i).GetNeighbors():
                if n.GetIdx() in left:
                    left.remove(n.GetIdx())
                    stack.append(n.GetIdx())
        sites.append(sorted(comp))
    return sorted(sites, key=lambda s: (-len(s), s[0]))


def _make_dative(mol):
    """Neutral donors drawn with a plain bond to a metal -> dative (ligand -> metal) so valences sanitize; eta-n ring atoms
    bonded to the metal (Cp, arenes, olefins) always become dative bonds."""
    Chem, _ = _rdkit()
    rw = Chem.RWMol(mol)
    fixed = 0
    pt = Chem.GetPeriodicTable()
    metal_nb = {n.GetIdx() for a in rw.GetAtoms() if _is_metal(a) for n in a.GetNeighbors()}
    for b in list(rw.GetBonds()):
        a, c = b.GetBeginAtom(), b.GetEndAtom()
        if _is_metal(c):
            a, c = c, a
        if not _is_metal(a) or _is_metal(c) or b.GetBondType() != Chem.BondType.SINGLE:
            continue
        haptic = any(n.GetIdx() in metal_nb for n in c.GetNeighbors() if not _is_metal(n))
        sigma = c.GetSymbol() in _DONORS and c.GetFormalCharge() == 0 and _bond_order_sum(c) > pt.GetDefaultValence(c.GetAtomicNum())
        if haptic or sigma:
            i, j = c.GetIdx(), a.GetIdx()
            rw.RemoveBond(i, j)
            rw.AddBond(i, j, Chem.BondType.DATIVE)
            fixed += 1
    return rw.GetMol(), fixed


def _pi_ring(rw, atoms) -> list[int] | None:
    """Smallest ring among `atoms` whose ring atoms all carry a multiple / aromatic bond or a negative charge (Cp-, arenes)."""
    Chem, _ = _rdkit()
    Chem.FastFindRings(rw)
    ri = rw.GetRingInfo()
    best = None
    for ring in ri.AtomRings():
        if not set(ring) <= set(atoms) or not 3 <= len(ring) <= 7:
            continue
        ok = all(rw.GetAtomWithIdx(i).GetFormalCharge() < 0 or rw.GetAtomWithIdx(i).GetIsAromatic()
                 or any(b.GetBondType() != Chem.BondType.SINGLE for b in rw.GetAtomWithIdx(i).GetBonds()) for i in ring)
        if ok and all(rw.GetAtomWithIdx(i).GetSymbol() == "C" for i in ring) and (best is None or len(ring) < len(best)):
            best = list(ring)
    return best


def _connect_ionic(mol, warnings):
    """Bond fragments written separately to the metal ('[Fe+3].[Cl-].[Cl-]...', '[cH-]1cccc1.[cH-]1cccc1.[Fe+2]').

    For an isolated metal: neutral sigma donors, then anions (sigma), up to MAX_AUTO_SITES sites.  Ring carbanions (Cp-,
    indenyl-) and neutral carbocyclic arenes bind eta-n even when the metal already has bonds.  Every fragment still not
    bonded to a metal afterwards is removed (counter-ions), so the charge refers to the complex."""
    Chem, _ = _rdkit()
    rw = Chem.RWMol(mol)
    metals = [a.GetIdx() for a in rw.GetAtoms() if _is_metal(a)]
    joined, hapt = 0, 0
    if len(metals) == 1:
        m = metals[0]
        isolated = rw.GetAtomWithIdx(m).GetDegree() == 0
        others = [fr for fr in Chem.GetMolFrags(rw) if m not in fr]
        sigma_neutral, sigma_anion, pi = [], [], []
        for fr in others:
            atoms = [rw.GetAtomWithIdx(i) for i in fr]
            ring_an = next((a for a in atoms if a.GetSymbol() == "C" and a.GetFormalCharge() < 0 and a.IsInRing()), None)
            if ring_an is not None or (all(a.GetFormalCharge() == 0 for a in atoms) and not any(a.GetSymbol() in _DONORS for a in atoms)):
                ring = _pi_ring(rw, fr)
                if ring is not None:
                    pi.append(ring)
                    continue
            if not isolated:
                continue
            if all(a.GetFormalCharge() == 0 for a in atoms):
                d = next((a for a in atoms if a.GetSymbol() in _DONORS), None)
                if d is not None:
                    sigma_neutral.append(d)
            else:
                sigma_anion.append(next(a for a in atoms if a.GetFormalCharge() < 0))
        for ring in pi:
            if len(_metal_sites(rw, m)) >= MAX_AUTO_SITES:
                break
            for i in ring:
                rw.AddBond(i, m, Chem.BondType.DATIVE)
            hapt += 1
        for kind, a in [("dative", x) for x in sigma_neutral] + [("ionic", x) for x in sigma_anion]:
            if len(_metal_sites(rw, m)) >= MAX_AUTO_SITES:
                break
            if kind == "dative":
                rw.AddBond(a.GetIdx(), m, Chem.BondType.DATIVE)
            else:  # anion -> covalent bond, charge moves onto the metal
                rw.AddBond(a.GetIdx(), m, Chem.BondType.SINGLE)
                a.SetFormalCharge(a.GetFormalCharge() + 1)
                rw.GetAtomWithIdx(m).SetFormalCharge(rw.GetAtomWithIdx(m).GetFormalCharge() - 1)
            joined += 1
        if joined:
            warnings.append(f"Ionic SMILES: {joined} fragment(s) bonded to the metal (neutral donors datively, then anions, "
                            f"up to {MAX_AUTO_SITES} sites).")
        if hapt:
            warnings.append(f"{hapt} ring ligand(s) bound eta-n through the ring centroid (Cp-type anions and neutral arenes). "
                            "If a neutral arene is lattice solvent, remove it from the SMILES.")
    if metals:
        keep = set().union(*(fr for fr in Chem.GetMolFrags(rw) if any(i in fr for i in metals)))
        drop = sorted(set(range(rw.GetNumAtoms())) - keep, reverse=True)
        if drop:
            gone = Chem.MolFragmentToSmiles(rw, atomsToUse=sorted(drop))
            for i in drop:
                rw.RemoveAtom(i)
            warnings.append(f"Removed fragment(s) not bonded to a metal ({gone}); the charge now refers to the complex only.")
    return rw.GetMol()


def _sanitize(mol, warnings):
    Chem, _ = _rdkit()
    mol.UpdatePropertyCache(strict=False)
    mol = _connect_ionic(mol, warnings)
    mol, n = _make_dative(mol)
    if n:
        warnings.append(f"{n} metal-ligand bond(s) set as dative (ligand -> metal) so valences make sense.")
    Chem.SanitizeMol(mol)
    return mol


def _kabsch(P, Q):
    """Rotation R, translation t minimizing |R p + t - q| (rows of P, Q)."""
    import numpy as np
    pc, qc = P.mean(0), Q.mean(0)
    H = (P - pc).T @ (Q - qc)
    U, _, Vt = np.linalg.svd(H)
    dd = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, dd]) @ U.T
    return R, qc - R @ pc


def _embed_sub(mol, keep: list[int], seed: int):
    """ETKDG coordinates for the sub-molecule made of `keep` atoms (in that sorted order), or None."""
    import numpy as np
    Chem, AllChem = _rdkit()
    sub = Chem.RWMol(mol)
    for i in sorted(set(range(mol.GetNumAtoms())) - set(keep), reverse=True):
        sub.RemoveAtom(i)
    sub = sub.GetMol()
    sub.UpdatePropertyCache(strict=False)
    if sub.GetNumAtoms() == 1:
        return np.zeros((1, 3))
    for attempt in range(3):
        ps = AllChem.ETKDGv3()
        ps.randomSeed = seed + attempt
        ps.useRandomCoords = True
        if AllChem.EmbedMolecule(sub, ps) == 0:
            return np.array(sub.GetConformer().GetPositions())
    return None


def _site_distance(mol, site: list[int]) -> float:
    if len(site) > 1:
        return FE_HAPTIC_DIST.get(len(site), 1.7)
    a = mol.GetAtomWithIdx(site[0])
    if a.GetSymbol() == "C" and any(b.GetBondType().name == "TRIPLE" for b in a.GetBonds()):
        return FE_PI_ACCEPTOR_C
    return FE_DONOR_DIST.get(a.GetSymbol(), 2.1)


def _frag_name(mol, idx: list[int]) -> str:
    """Short ligand name for isomer labels: py, Cl, H2O, CO, ... or the ligand's SMILES."""
    Chem, _ = _rdkit()
    sub = Chem.RWMol(mol)
    for i in sorted(set(range(mol.GetNumAtoms())) - set(idx), reverse=True):
        sub.RemoveAtom(i)
    sub = sub.GetMol()
    sub.UpdatePropertyCache(strict=False)
    try:
        smi = Chem.MolToSmiles(Chem.RemoveHs(sub, sanitize=False))
    except Exception:  # noqa: BLE001
        smi = Chem.MolToSmiles(sub)
    if sub.GetNumHeavyAtoms() == 1 and sub.GetNumAtoms() == 1:
        return sub.GetAtomWithIdx(0).GetSymbol()
    return _SHORT.get(smi, smi if len(smi) <= 12 else f"L{sub.GetNumHeavyAtoms()}")


def _ligand_fragments(mol, metal: int, sites: list[list[int]], seed: int):
    """Ligands (fragments without the metal), each embedded on its own, with their coordination sites in local geometry."""
    import numpy as np
    Chem, _ = _rdkit()
    rw = Chem.RWMol(mol)
    rw.RemoveAtom(metal)
    old = [i for i in range(mol.GetNumAtoms()) if i != metal]           # lig index -> mol index
    frags = []
    for ids in Chem.GetMolFrags(rw.GetMol()):
        mol_idx = [old[i] for i in sorted(ids)]
        fsites = [s for s in sites if s[0] in mol_idx]
        xyz = _embed_sub(mol, mol_idx, seed)
        metal_xyz = None
        if len(fsites) >= 2:  # chelate: embed it together with the metal so the chelate ring stays closed
            keep = sorted(set(mol_idx) | {metal})
            full = _embed_sub(mol, keep, seed)
            if full is not None:
                mpos = keep.index(metal)
                metal_xyz, xyz = full[mpos], np.delete(full, mpos, axis=0)
        if xyz is None:
            raise ValueError("RDKit could not embed one of the ligands; check the SMILES or upload an xyz.")
        loc = {j: mol_idx.index(j) for s in fsites for j in s}
        site_pts, site_dirs, site_d = [], [], []
        for s in fsites:
            P = xyz[[loc[j] for j in s]]
            c = P.mean(0)
            if len(s) == 1:  # lone-pair direction: opposite the sum of the donor's bond vectors within the ligand
                nb = [mol_idx.index(n.GetIdx()) for n in mol.GetAtomWithIdx(s[0]).GetNeighbors() if n.GetIdx() != metal]
                v = -sum((xyz[j] - c) / np.linalg.norm(xyz[j] - c) for j in nb) if nb else np.zeros(3)
            else:            # eta-n: ring / pi-plane normal, on the side away from the rest of the ligand
                v = np.linalg.svd(P - c)[2][-1] if len(s) >= 3 else np.cross(P[1] - P[0], [0.3, 0.5, 0.8])
                rest = [i for i in range(len(mol_idx)) if i not in [loc[j] for j in s] and mol.GetAtomWithIdx(mol_idx[i]).GetSymbol() != "H"]
                if rest and np.dot(xyz[rest].mean(0) - c, v) > 0:
                    v = -v
            if metal_xyz is not None and np.linalg.norm(metal_xyz - c) > 1e-3:
                v = metal_xyz - c
            site_pts.append(c)
            site_dirs.append(v)
            site_d.append(_site_distance(mol, s))
        tot = sum(site_dirs) if site_dirs else np.zeros(3)
        fallback = tot if np.linalg.norm(tot) > 1e-3 else np.array([1.0, 0.0, 0.0])
        site_dirs = [v / np.linalg.norm(v) if np.linalg.norm(v) > 1e-3 else fallback / np.linalg.norm(fallback) for v in site_dirs]
        frags.append(dict(idx=mol_idx, xyz=xyz, sites=fsites, pts=np.array(site_pts).reshape(-1, 3), dirs=site_dirs, d=site_d,
                          metal_xyz=metal_xyz, name=_frag_name(mol, mol_idx), heavy=np.array(
                              [mol.GetAtomWithIdx(i).GetSymbol() != "H" for i in mol_idx])))
    return frags


def _candidates(f, verts):
    """All ways to put ligand f's sites on polyhedron vertices: list of (vertex tuple, rms, R, t), best first."""
    import itertools
    import numpy as np
    k = len(f["sites"])
    if k == 0:
        return []
    d = np.array(f["d"])[:, None]
    guess = f["metal_xyz"] if f["metal_xyz"] is not None else np.mean([p + u * dd for p, u, dd in zip(f["pts"], f["dirs"], d[:, 0])], axis=0)
    src = np.vstack([f["pts"], guess])
    if k == 1:                                   # only the site -> metal axis matters
        src = np.vstack([src, f["pts"][0] - f["dirs"][0] * d[0, 0]])
    out = []
    for perm in itertools.permutations(range(len(verts)), k):
        tgt = np.vstack([verts[list(perm)] * d, np.zeros(3)])
        if k == 1:
            tgt = np.vstack([tgt, tgt[0] * 2])
        R, t = _kabsch(src, tgt)
        rms = float(np.sqrt(((src[:k + 1] @ R.T + t - tgt[:k + 1]) ** 2).sum(1).mean()))
        out.append((perm, rms, R, t))
    out.sort(key=lambda c: c[1])
    best = out[0][1]
    return [c for c in out if c[1] <= best + 0.15]


def _clash(A, Ah, B, Bh) -> float:
    """Soft steric overlap between two rigid ligands (heavy-heavy < 2.9 A, contacts with H < 2.3 A)."""
    import numpy as np
    if not len(A) or not len(B):
        return 0.0
    D = np.linalg.norm(A[:, None, :] - B[None, :, :], axis=2)
    lim = np.where(Ah[:, None] & Bh[None, :], 2.9, 2.3)
    return float((np.clip(lim - D, 0, None) ** 2).sum())


def _rotate_about(xyz, axis, ang):
    import numpy as np
    a = axis / np.linalg.norm(axis)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    R = np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K
    return xyz @ R.T


def _isomers(frags, verts, shape: str, n_atoms: int, limit: int = 20000):
    """Distinct arrangements of the ligands on the polyhedron, best first: chelate fit, then steric clash."""
    import numpy as np
    order = sorted([f for f in frags if f["sites"]], key=lambda f: (-len(f["sites"]), -len(f["idx"])))
    cands = [_candidates(f, verts) for f in order]
    angle = np.degrees(np.arccos(np.clip(verts @ verts.T, -1, 1))).round().astype(int)
    roles = ROLES.get(shape)
    # one entry per site: ligand type, ligand instance; chelate types are marked so labels skip their internal angles
    types = [f["name"] + ("*" if len(f["sites"]) > 1 else "") for f in order for _ in f["sites"]]
    owner = [n for n, f in enumerate(order) for _ in f["sites"]]
    seen, leaves = {}, [0]

    def sig(choice):  # invariant under the polyhedron's symmetry (mirror images are not distinguished)
        vs = [v for c in choice for v in c[0]]
        pairs = sorted((*sorted((types[a], types[b])), int(angle[vs[a], vs[b]]), owner[a] == owner[b])
                       for a in range(len(vs)) for b in range(a + 1, len(vs)))
        rl = tuple(sorted((types[a], roles[vs[a]]) for a in range(len(vs)))) if roles else ()
        return tuple(pairs), rl

    def dfs(i, used, choice):
        if leaves[0] > limit:
            return
        if i == len(order):
            leaves[0] += 1
            key = sig(choice)
            worst = max((c[1] for c in choice), default=0.0)
            if key not in seen or worst < seen[key][0] - 1e-6:
                seen[key] = (worst, list(choice))
            return
        for c in cands[i]:
            if used.isdisjoint(c[0]):
                choice.append(c)
                dfs(i + 1, used | set(c[0]), choice)
                choice.pop()

    dfs(0, frozenset(), [])
    isomers = []
    for key, (worst, choice) in seen.items():
        pos = np.zeros((n_atoms, 3))
        placed = []
        for f, (perm, _, R, t) in zip(order, choice):
            xyz = f["xyz"] @ R.T + t
            if len(f["sites"]) == 1 and len(f["idx"]) > 1:   # spin the ligand about its Fe-site axis to avoid clashes
                axis = verts[perm[0]]
                best = None
                for step in range(12):
                    trial = _rotate_about(xyz, axis, step * math.pi / 6)
                    score = sum(_clash(trial, f["heavy"], q, qh) for q, qh in placed)
                    if best is None or score < best[0] - 1e-9:
                        best = (score, trial)
                xyz = best[1]
            placed.append((xyz, f["heavy"]))
            pos[f["idx"]] = xyz
        clash = sum(_clash(placed[a][0], placed[a][1], placed[b][0], placed[b][1])
                    for a in range(len(placed)) for b in range(a + 1, len(placed)))
        isomers.append(dict(pos=pos, worst=worst, clash=clash, key=key, choice=choice, order=order))
    isomers.sort(key=lambda x: (round(x["worst"] / 0.1), round(x["clash"], 2)))
    labels = _isomer_labels(isomers, types, angle, roles)
    for iso, lab in zip(isomers, labels):
        iso["label"] = lab
    return isomers


def _isomer_labels(isomers, types, angle, roles) -> list[str]:
    """cis/trans, fac/mer or axial/equatorial descriptions of what differs between the isomers."""
    if len(isomers) < 2:
        return ["only arrangement"] * len(isomers)
    out = []
    for iso in isomers:
        vs = [v for c in iso["choice"] for v in c[0]]
        parts = []
        by_type = {}
        for a, t in enumerate(types):
            by_type.setdefault(t, []).append(a)
        mono = {t: idx for t, idx in by_type.items() if not t.endswith("*") and len(idx) < len(types)}
        if roles:  # name the positions of the least common ligand(s)
            fewest = min((len(i) for i in mono.values()), default=0)
            for t, idx in mono.items():
                if len(idx) != fewest:
                    continue
                rs = sorted(roles[vs[a]] for a in idx)
                rv = [v for v, r in enumerate(roles) if r == rs[0]]
                role_angles = {int(angle[x, y]) > 150 for n, x in enumerate(rv) for y in rv[n + 1:]}
                same = len(idx) == 2 and rs[0] == rs[1] and role_angles == {True, False}   # the role allows both cis and trans
                parts.append(f"{t} " + "+".join(rs) + ((" trans" if angle[vs[idx[0]], vs[idx[1]]] > 150 else " cis") if same else ""))
        for t, idx in ([] if roles else mono.items()):
            if len(idx) < 2:
                continue
            angs = [int(angle[vs[a], vs[b]]) for n, a in enumerate(idx) for b in idx[n + 1:]]
            if len(idx) == 2:
                parts.append(f"{t} {'trans' if angs[0] > 150 else 'cis'}")
            elif len(idx) == 3:
                parts.append(f"{'mer' if max(angs) > 150 else 'fac'}-{t}3")
        out.append(", ".join(parts))
    for n, lab in enumerate(out):  # make labels unique (e.g. enantiomer-like pairs that the description cannot tell apart)
        if not lab or out.count(lab) > 1:
            out[n] = (lab + " " if lab else "") + f"(arrangement {n + 1})"
    return out


def _analyse(mol, polyhedron: str | None, seed: int):
    """Sites, ligands and isomers for every candidate polyhedron of a single-metal complex (H already added)."""
    import numpy as np
    metal = next(a.GetIdx() for a in mol.GetAtoms() if _is_metal(a))
    sites = _metal_sites(mol, metal)
    cn = len(sites)
    shapes = POLYHEDRA.get(cn)
    if not shapes:
        raise ValueError(f"{cn} coordination sites is not supported by the builder (1-8); upload an xyz/CIF instead.")
    if polyhedron and polyhedron not in shapes:
        raise ValueError(f"'{polyhedron}' is not a CN-{cn} polyhedron; choose one of: {', '.join(shapes)}")
    frags = _ligand_fragments(mol, metal, sites, seed)
    fits = {}
    for name in ([polyhedron] if polyhedron else list(shapes)):
        v = np.array(shapes[name], dtype=float)
        v /= np.linalg.norm(v, axis=1)[:, None]
        fits[name] = (v, _isomers(frags, v, name, mol.GetNumAtoms()))
    default = next(iter(fits))
    alt = min(fits, key=lambda n: fits[n][1][0]["worst"])
    name = alt if fits[default][1][0]["worst"] > 0.5 and fits[alt][1][0]["worst"] < fits[default][1][0]["worst"] - 0.2 else default
    return dict(metal=metal, sites=sites, cn=cn, shapes=list(shapes), shape=name, verts=fits[name][0], isomers=fits[name][1],
                chosen=bool(polyhedron))


def _assemble(mol, polyhedron: str | None, isomer: int | None, seed: int, warnings):
    """Ideal polyhedron around one metal + separately embedded, rigidly aligned ligands.

    Returns (positions, metal-ligand distance restraints, inter-site restraints, shape, isomer label)."""
    import itertools
    import numpy as np
    A = _analyse(mol, polyhedron, seed)
    isos = A["isomers"]
    if isomer is not None and not 0 <= isomer < len(isos):
        raise ValueError(f"isomer {isomer} does not exist; this complex has {len(isos)} arrangement(s) on the {A['shape']} sites")
    iso = isos[isomer or 0]
    pos, m = iso["pos"], A["metal"]
    restr = []
    for s in A["sites"]:
        if len(s) == 1:
            restr.append((m, s[0], _site_distance(mol, s)))
        else:  # eta-n: each ring atom at sqrt(d_centroid^2 + r_atom^2)
            c = pos[s].mean(0)
            dc = _site_distance(mol, s)
            restr += [(m, j, float(math.hypot(dc, np.linalg.norm(pos[j] - c)))) for j in s]
    pairs = [(a, b, float(np.linalg.norm(pos[a] - pos[b]))) for s1, s2 in itertools.combinations(A["sites"], 2) for a in s1 for b in s2]
    if iso["worst"] > 0.6:
        warnings.append(f"A chelating ligand fits the {A['shape']} sites poorly (RMS {iso['worst']:.2f} A); check the geometry.")
    if len(A["shapes"]) > 1:
        how = "chosen" if A["chosen"] else "default" if A["shape"] == A["shapes"][0] else "default did not fit the chelate"
        warnings.append(f"Fe coordination built as {A['shape']} ({A['cn']} sites, {how}); alternatives: "
                        f"{', '.join(x for x in A['shapes'] if x != A['shape'])}.")
    if len(isos) > 1:
        warnings.append(f"{len(isos)} distinct ligand arrangements; built '{iso['label']}'"
                        + (" (lowest steric clash)" if isomer is None else "") + ".")
    if any(len(s) > 1 for s in A["sites"]):
        warnings.append("eta-n ligands placed with the ring centroid on the polyhedron vertex (ring rotation is not optimized).")
    return pos, restr, pairs, A["shape"], iso["label"] if len(isos) > 1 else ""


def coordination_options(smiles: str | None = None, molblock: str | None = None, seed: int = 7, polyhedron: str | None = None) -> dict:
    """What the builder would do: dict(cn, shapes, shape, isomers=[labels]) for the GUI pickers; empty dict if not applicable."""
    Chem, _ = _rdkit()
    mol = Chem.MolFromSmiles(smiles, sanitize=False) if smiles else Chem.MolFromMolBlock(molblock or "", sanitize=False, removeHs=False)
    if mol is None or mol.GetNumAtoms() == 0:
        return {}
    mol = _sanitize(mol, [])
    mol = Chem.AddHs(mol)
    if sum(_is_metal(a) for a in mol.GetAtoms()) != 1 or not any(_is_metal(a) and a.GetDegree() for a in mol.GetAtoms()):
        return {}
    A = _analyse(mol, polyhedron, seed)
    return dict(cn=A["cn"], shapes=A["shapes"], shape=A["shape"], isomers=[i["label"] for i in A["isomers"]],
                haptic=[len(s) for s in A["sites"] if len(s) > 1])


def _relax(mol, restraints, pair_restraints, warnings):
    Chem, AllChem = _rdkit()
    if not AllChem.UFFHasAllMoleculeParams(mol):
        warnings.append("UFF lacks parameters for the metal centre; ligands relaxed by UFF with the Fe-ligand geometry restrained.")
    ff = AllChem.UFFGetMoleculeForceField(mol, ignoreInterfragInteractions=False)
    if ff is None:
        warnings.append("No force field could be set up: geometry is the unrelaxed assembly.")
        return
    for i, j, d in restraints:
        ff.UFFAddDistanceConstraint(i, j, False, d - 0.02, d + 0.02, 1.0e5)
    for i, j, d in pair_restraints:  # keeps the chosen polyhedron
        ff.UFFAddDistanceConstraint(i, j, False, d - 0.15, d + 0.15, 2.0e3)
    # UFF has no van der Waals terms for an untyped metal: add flat-bottomed walls so ligand atoms cannot fall onto it
    dmat = Chem.GetDistanceMatrix(mol)
    for m in {i for i, _, _ in restraints}:
        bound = {j for i, j, _ in restraints if i == m}
        for j in range(mol.GetNumAtoms()):
            if j == m or j in bound:
                continue
            topo = int(dmat[m][j])
            lo = (2.35 if mol.GetAtomWithIdx(j).GetSymbol() == "H" else 2.75) if topo == 2 else 2.9 if topo == 3 else 3.1
            ff.UFFAddDistanceConstraint(m, j, False, lo, 100.0, 1.0e3)
    ff.Initialize()
    try:
        ff.Minimize(maxIts=5000)
    except RuntimeError:
        warnings.append("Force-field relaxation failed; geometry is the unrelaxed template assembly.")


def _embed(mol, warnings, seed: int = 7, polyhedron: str | None = None, isomer: int | None = None):
    """Single metal: polyhedron template + aligned ligands + restrained UFF.  Otherwise plain ETKDG + restrained UFF.

    Returns (mol, shape, isomer label)."""
    import numpy as np
    from rdkit.Geometry import Point3D
    Chem, AllChem = _rdkit()
    mol = Chem.AddHs(mol)
    metals = [a.GetIdx() for a in mol.GetAtoms() if _is_metal(a)]
    if len(metals) == 1:
        shape, label = "", ""
        if mol.GetAtomWithIdx(metals[0]).GetDegree():
            pos, restr, pairs, shape, label = _assemble(mol, polyhedron, isomer, seed, warnings)
        else:
            pos, restr, pairs = np.zeros((mol.GetNumAtoms(), 3)), [], []
        conf = Chem.Conformer(mol.GetNumAtoms())
        for i, p in enumerate(pos):
            conf.SetAtomPosition(i, Point3D(*map(float, p)))
        conf.Set3D(True)
        mol.RemoveAllConformers()
        mol.AddConformer(conf, assignId=True)
        _relax(mol, restr, pairs, warnings)
        return mol, shape, label
    ps = AllChem.ETKDGv3()
    ps.randomSeed = seed
    ps.useRandomCoords = True
    if AllChem.EmbedMolecule(mol, ps) != 0:
        raise ValueError("RDKit could not embed a 3D geometry (several metal centres); upload an xyz/CIF instead.")
    restr = [(b.GetBeginAtomIdx(), b.GetEndAtomIdx(), FE_DONOR_DIST.get(
        (b.GetEndAtom() if _is_metal(b.GetBeginAtom()) else b.GetBeginAtom()).GetSymbol(), 2.1))
        for b in mol.GetBonds() if _is_metal(b.GetBeginAtom()) != _is_metal(b.GetEndAtom())]
    warnings.append("Several metal centres: no polyhedron template; plain ETKDG embed with restrained metal-ligand bonds.")
    _relax(mol, restr, [], warnings)
    return mol, "", ""


def _to_structure(mol, comment: str) -> Structure:
    conf = mol.GetConformer()
    syms = [a.GetSymbol() for a in mol.GetAtoms()]
    xyz = [tuple(float(v) for v in conf.GetAtomPosition(i)) for i in range(mol.GetNumAtoms())]
    return Structure(syms, xyz, comment)


def _finish(mol, label: str, warnings: list[str], shape: str = "", isomer: str = "") -> BuildResult:
    Chem, _ = _rdkit()
    charge = Chem.GetFormalCharge(mol)
    warnings.append("Built geometry (idealized Fe polyhedron + ETKDG ligands + restrained UFF): use it only as a starting point "
                    "for the plan's geometry optimization, never as an as_given geometry.")
    return BuildResult(_to_structure(mol, f"built by RDKit from {label}"), charge, warnings, label, shape, isomer)


def from_smiles(smiles: str, seed: int = 7, polyhedron: str | None = None, isomer: int | None = None) -> BuildResult:
    """SMILES -> 3D Structure.  Use '->' for dative bonds, e.g. 'O->[Fe+2](<-O)(<-O)(<-O)(<-O)<-O'; write Cp / arenes as separate
    fragments ('[cH-]1cccc1.[cH-]1cccc1.[Fe+2]') to bind them eta-n.  `isomer` indexes `coordination_options(...)['isomers']`."""
    Chem, _ = _rdkit()
    smiles = smiles.strip()
    if not smiles:
        raise ValueError("empty SMILES")
    warnings: list[str] = []
    mol = Chem.MolFromSmiles(smiles, sanitize=False)
    if mol is None:
        raise ValueError(f"RDKit could not parse SMILES {smiles!r}")
    mol = _sanitize(mol, warnings)
    mol, shape, iso = _embed(mol, warnings, seed, polyhedron, isomer)
    return _finish(mol, f"SMILES {smiles}", warnings, shape, iso)


def from_molblock(molblock: str, seed: int = 7, polyhedron: str | None = None, isomer: int | None = None) -> BuildResult:
    """Molfile (e.g. from the drawing editor; 2D or 3D).  2D input is built in 3D; 3D input is used exactly as given."""
    Chem, _ = _rdkit()
    warnings: list[str] = []
    mol = Chem.MolFromMolBlock(molblock, sanitize=False, removeHs=False)
    if mol is None or mol.GetNumAtoms() == 0:
        raise ValueError("empty or unreadable molfile")
    if mol.GetNumConformers() and mol.GetConformer().Is3D():
        # real coordinates: no bond surgery, no fragment removal, no added H (files often omit metal-ligand bonds)
        if not any(a.GetSymbol() == "H" for a in mol.GetAtoms()):
            warnings.append("The file contains no hydrogens; add them before using this geometry.")
        return BuildResult(_to_structure(mol, "from molfile (3D, as given)"), Chem.GetFormalCharge(mol), warnings, "molfile")
    mol = _sanitize(mol, warnings)
    mol.RemoveAllConformers()
    mol, shape, iso = _embed(mol, warnings, seed, polyhedron, isomer)
    return _finish(mol, "drawing", warnings, shape, iso)


def from_rdkit_text(text: str, filename: str) -> BuildResult:
    """MOL / SDF (first record) / PDB text -> Structure, keeping the file's 3D coordinates."""
    Chem, _ = _rdkit()
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext in ("mol", "sdf"):
        records = _sdf_records(text)
        if len(records) > 1:
            raise MultipleStructuresError(len(records), filename)
        return from_molblock(records[0] if records else text)
    if ext == "pdb":
        mol = Chem.MolFromPDBBlock(text, sanitize=False, removeHs=False, proximityBonding=False)
        if mol is None:
            raise ValueError("unreadable PDB")
        return BuildResult(_to_structure(mol, f"from {filename}"), 0, ["PDB has no reliable charge information: set the charge yourself."], filename)
    raise ValueError(f"unsupported file type .{ext}")


def read_any(text: str, filename: str) -> BuildResult:
    """xyz / cif via the dependency-free readers, mol/sdf/pdb via RDKit."""
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext in ("xyz", "cif"):
        s = read_structure_text(text, filename)
        w = ["CIF: symmetry-expanded unit-cell contents, not assembled into molecules; check the atom count."] if ext == "cif" else []
        return BuildResult(s, 0, w, filename)
    return from_rdkit_text(text, filename)


def _sdf_records(text: str) -> list[str]:
    """SDF text -> molblocks (the line break after each ``$$$$`` is dropped; a blank title line is kept)."""
    parts = text.replace("\r\n", "\n").replace("\r", "\n").split("$$$$")
    return [part[1:] if k and part.startswith("\n") else part for k, part in enumerate(parts) if part.strip()]


# ----------------------------------------------------------------------------
# batch intake: one source (file or pasted text) -> several found structures
# ----------------------------------------------------------------------------
PLACEHOLDER_PREFIX = "unnamed-"
_NOT_A_NAME = re.compile(r"^(?:[-+]?\d*\.\d+(?:[eE][-+]?\d+)?|.*\b(?:energy\b|E\s*=|coordinates from\b|from CIF\b|from molfile\b|built by\b).*)$",
                         re.IGNORECASE)


@dataclass
class FoundStructure:
    structure: Structure
    ref: str                          # "<source>#<n>" (1-based), or "<source>" when the source holds one structure
    name: str                         # proposed compound name
    placeholder: bool = False         # name is an "unnamed-..." stand-in the researcher must replace
    warnings: list[str] = field(default_factory=list)
    charge: int | None = None         # formal charge from a molfile; None when the format carries none


def is_placeholder(name: str) -> bool:
    return not (name or "").strip() or name.strip().lower().startswith(PLACEHOLDER_PREFIX)


def _proposed(raw: str, source: str, i: int) -> tuple[str, bool]:
    raw = (raw or "").strip()
    if raw and not _NOT_A_NAME.match(raw) and raw.lower() not in ("global", "shelx", "?", "."):
        return raw, False
    return f"{PLACEHOLDER_PREFIX}{PurePath(source).stem}-{i}", True


def find_structures(text: str, filename: str) -> tuple[list[FoundStructure], list[str]]:
    """Every structure in one source, in order, with proposed names and references.  Returns (found, notes); notes say
    what was skipped (e.g. a CIF header block with no atoms).  mol/sdf/pdb need RDKit."""
    ext = filename.lower().rsplit(".", 1)[-1]
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # uploads from Windows
    notes: list[str] = []
    items: list[tuple[str, Structure, list[str], int | None]] = []
    if ext == "cif":
        for name, block in split_cif_blocks(text) or [("", text)]:
            if not cif_block_has_atoms(block):
                notes.append(f"{filename}: CIF block data_{name} has no atom sites; skipped")
                continue
            items.append((name, structure_from_cif_text(block),
                          ["CIF: symmetry-expanded unit-cell contents, not assembled into molecules; check the atom count."], None))
    elif ext in ("mol", "sdf"):
        for rec in _sdf_records(text):
            r = from_molblock(rec)
            items.append((rec.split("\n", 1)[0], r.structure, r.warnings, r.charge))
    elif ext == "pdb":
        r = from_rdkit_text(text, filename)
        items.append((PurePath(filename).stem, r.structure, r.warnings, None))
    else:
        for block in split_xyz_blocks(text):
            st = Structure.from_xyz_text(block)
            items.append((st.comment, st, [], None))
    found = []
    for i, (raw, st, warn, charge) in enumerate(items, 1):
        name, ph = _proposed(raw, filename, i)
        ref = filename if len(items) == 1 else f"{filename}#{i}"
        found.append(FoundStructure(st, ref, name, ph, list(warn), charge))
    return found, notes


# ----------------------------------------------------------------------------
# geometry checks shown next to the 3D view
# ----------------------------------------------------------------------------
def fe_environment(s: Structure, scale: float = 1.25, max_d: float = 3.0) -> list[dict]:
    """For each Fe: neighbours within scale*(r_cov sum) (capped at max_d A), sorted by distance (0-based indices)."""
    out = []
    for i in s.fe_indices:
        nb = []
        for j, (e, p) in enumerate(zip(s.symbols, s.coords)):
            if j == i:
                continue
            d = math.dist(s.coords[i], p)
            cut = min(max_d, scale * (COVALENT_R["Fe"] + COVALENT_R.get(e.capitalize(), 1.5)))
            if d <= cut:
                nb.append(dict(atom=j, element=e.capitalize(), distance=round(d, 3)))
        nb.sort(key=lambda r: r["distance"])
        out.append(dict(fe=i, cn=len(nb), neighbours=nb))
    return out


def close_contacts(s: Structure, min_d: float = 0.7) -> list[tuple[int, int, float]]:
    """Atom pairs closer than min_d A (duplicated atoms, broken CIF expansion, bad embeds)."""
    bad = []
    for i in range(len(s)):
        for j in range(i + 1, len(s)):
            d = math.dist(s.coords[i], s.coords[j])
            if d < min_d:
                bad.append((i, j, round(d, 3)))
    return bad


def electron_count(s: Structure, charge: int) -> int | None:
    """Total electron count (sum of Z minus charge), or None for unsupported elements."""
    from mossbauer_dft.elements import atomic_numbers
    try:
        return sum(atomic_numbers(s.symbols)) - int(charge)
    except ValueError:
        return None


def multiplicity_allowed(s: Structure, charge: int, multiplicity: int) -> bool | None:
    """Parity rule used by make_plan: (electrons - (2S+1 - 1)) must be even.  None if the count is unknown."""
    ne = electron_count(s, charge)
    return None if ne is None else (ne - (int(multiplicity) - 1)) % 2 == 0


def electron_parity_hint(s: Structure, charge: int) -> str | None:
    """Odd/even electron count -> which multiplicities are possible (2S+1 even for odd electrons)."""
    from mossbauer_dft.elements import atomic_numbers
    try:
        ne = sum(atomic_numbers(s.symbols)) - int(charge)
    except ValueError:
        return None
    return f"{ne} electrons ({'odd' if ne % 2 else 'even'}) -> multiplicity must be {'even (2, 4, 6)' if ne % 2 else 'odd (1, 3, 5)'}"
