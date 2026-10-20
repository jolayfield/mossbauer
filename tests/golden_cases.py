"""The public golden decks in tests/data/golden: each name with the build_input call that must reproduce it."""
from mossbauer_dft.orca_input import BrokenSymmetry, HighFieldOptions, MethodSpec, build_input, mossbauer_method


def _mn15(**k):
    return MethodSpec(functional="MN15", fe_basis=None, **k)


CASES = {
    "syn_prop_tpssh": lambda: build_input("mossbauer_sp", mossbauer_method("TPSSh"), charge=0, multiplicity=4, xyz_file="syn_geom.xyz"),
    "syn_prop_b3lyp": lambda: build_input("mossbauer_sp", mossbauer_method("B3LYP"), charge=-1, multiplicity=2, xyz_file="syn_geom.xyz"),
    "syn_prop_mn15": lambda: build_input("mossbauer_sp", mossbauer_method("MN15"), charge=0, multiplicity=4, xyz_file="syn_geom.xyz"),
    "syn_prop_tpssh_flip": lambda: build_input("mossbauer_sp", mossbauer_method("TPSSh", broken_symmetry=BrokenSymmetry("flipspin", (3, 4), 1.5)),
                                               charge=0, multiplicity=6, xyz_file="syn_geom.xyz"),
    "cp2fe_prop_b3lyp": lambda: build_input("mossbauer_sp", mossbauer_method("B3LYP"), charge=0, multiplicity=1, xyz_file="Cp2Fe_opt.xyz"),
    "syn_highfield_tpssh": lambda: build_input("highfield_sp", mossbauer_method("TPSSh"), charge=0, multiplicity=4, xyz_file="syn_geom.xyz",
                                               highfield=HighFieldOptions()),
    "syn_sp_mn15_svp": lambda: build_input("sp", _mn15(basis="def2-SVP"), charge=0, multiplicity=4, xyz_file="syn_geom.xyz"),
    "syn_opt_mn15_svp": lambda: build_input("opt", _mn15(basis="def2-SVP"), charge=0, multiplicity=4, xyz_file="syn_start.xyz"),
    "syn_hopt_mn15_svp": lambda: build_input("opt", _mn15(basis="def2-SVP"), charge=0, multiplicity=4, xyz_file="syn_start.xyz",
                                             optimize_hydrogens=True),
    "syn_opt_mn15_tzvp": lambda: build_input("opt", _mn15(basis="def2-TZVP", rijcosx=True), charge=0, multiplicity=4, xyz_file="syn_start.xyz"),
}
