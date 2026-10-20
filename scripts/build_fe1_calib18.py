"""Build data/refsets/fe1_calib18.yaml + structures from the curated training-set CSV and xyz directory.

usage: python scripts/build_fe1_calib18.py fe1_calib_trainingset.csv <dir with fe1_calib_*.xyz>
"""
import csv, shutil, sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1] / "src" / "mossbauer_dft" / "data" / "refsets"
csv_path, xyz_dir = Path(sys.argv[1]), Path(sys.argv[2])
FLAGS = {
    "Cp2Fe": ["b3lyp_outlier"],
    "FeO4_2m": ["high_leverage_single_Fe(VI)_point", "delta_T_unverified"],
    "lxylFeCO3": ["rdkit_built_geometry"], "lxylFeC6H6": ["rdkit_built_geometry"], "lxylFeInd": ["rdkit_built_geometry"],
}
FLAG_TEXT = {
    "b3lyp_outlier": "Ferrocene: B3LYP over-predicts delta by about +0.15 mm/s (McWilliams 2017 Table 9); consider excluding from the fit and using it as a check.",
    "high_leverage_single_Fe(VI)_point": "[FeO4]2-: the only Fe(VI) point, so it has very large leverage on the slope; fit with and without it.",
    "delta_T_unverified": "[FeO4]2-: no liquid-He data exist per Bochevarov 2010, so the 4.2 K label is unverified.",
    "hand_built_geometry": "Geometry is hand built from generic literature-typical bond lengths (or a COD structure with disorder removed / counter-cation dropped); starting point for optimization only.",
    "rdkit_built_geometry": "Geometry is an RDKit ETKDG + UFF embed with Fe-ligand distance restraints (no crystal structure retrieved); starting point for optimization only.",
}
def num(x):
    return None if x in ("", None) else float(x)
rows = list(csv.DictReader(open(csv_path, newline="")))
comps = []
for r in rows:
    sid = r["short_name"]
    src = xyz_dir / r["xyz_filename"]
    shutil.copy(src, ROOT / "structures" / "fe1_calib18" / f"{sid}.xyz")
    geo_hand = "hand built" in r["geometry_source"]
    flags = list(FLAGS.get(sid, []))
    if geo_hand and "rdkit_built_geometry" not in flags:
        flags.append("hand_built_geometry")
    comps.append(dict(
        id=sid, name=r["name"], set="training", formal_ox_state=r["fe_oxidation_state"], n_fe=1, spin_state=r["spin_state"], spin_S_as_printed=str(float(r["S"])),
        spin_S=float(r["S"]), multiplicity=int(r["multiplicity"]), charge=int(r["charge"]),
        delta_exp_mm_s=num(r["delta_mm_s"]), delta_T_K=num(r["delta_T_K"]), delta_T_note=r["delta_T_note"] or None,
        deq_abs_exp_mm_s=num(r["abs_dEQ_mm_s"]), exp_source_doi=r["source_doi"], source_table=r["source_table"],
        delta_reference=r["delta_reference"], geometry_source=r["geometry_source"], geometry_detail=r["geometry_detail"],
        structure="bundled", structure_file=f"structures/fe1_calib18/{sid}.xyz", flags=flags, notes=r["notes"] or None))
d = dict(
    schema_version="1.0", id="fe1_calib18",
    title="Project curated 18-compound 57Fe isomer-shift calibration set (fe1_calib18)",
    doi="10.1021/acsomega.7b00595; 10.1021/ct100398m; 10.1021/acs.jctc.1c00722; 10.1021/ic9008784",
    reference_delta="alpha-Fe (McWilliams entries stated; Bochevarov/Comas-Vila reference not stated in retrieved text)",
    structure_status="bundled",
    structure_status_note=("Starting geometries only (RDKit-built nacnac complexes, COD X-ray structures, hand-built ions); "
                           "they are re-optimised at the plan's geometry level by `mossbauer refset setup`. "
                           "Mixed T: 80 K (6 McWilliams entries) and nominal 4.2 K (12 others); second-order-Doppler scatter <= ~0.02-0.03 mm/s not corrected."),
    curation="Curated in-project (fe1_calib_trainingset_notes.md); held-out project validation targets are NOT in the set. No S- or P-ligated Fe(I), no Fe(0)/Fe(IV).",
    caveats=FLAG_TEXT, compounds=comps)
(ROOT / "fe1_calib18.yaml").write_text(yaml.safe_dump(d, sort_keys=False, width=140, allow_unicode=True))
print(len(comps), "compounds")
