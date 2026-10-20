"""ORCA 6.x input-deck generator.

Job types: ``opt``, ``freq``, ``sp``, ``mossbauer_sp``, ``highfield_sp``.

The ``mossbauer_sp`` deck is the template that ran successfully on the project
cluster (ORCA 6.1.0)::

    ! UKS <functional> def2-TZVP NoRIJCOSX DefGrid3 TightSCF
    %method [libxc exchange/correlation] SpecialGridAtoms 26  SpecialGridIntAcc 7 end
    %pal nprocs 16 end
    %maxcore 3000
    %scf MaxIter 500 [FlipSpin ..  FinalMs ..] end
    %basis NewGTO Fe "CP(PPP)" end end
    * xyzfile <charge> <mult> <file.xyz>

    %eprnmr Nuclei = all Fe {rho, fgrad} end      <- must come AFTER the geometry

Syntax sources (ORCA 6.1 manual): Mossbauer parameters (sec. 5.25: ``nuclei = all Fe {fgrad, rho}``),
EPR (sec. 5.22: ``gtensor true``; hyperfine flags aiso/adip/aorb/fgrad/rho; ZFS ``DTensor ssandso``,
``DSOC cp|qro|pk|cvw``, ``DSS uno|direct``; UNO is recommended for DSS with unrestricted references),
broken symmetry (sec. 5.27: ``%scf BrokenSym NA,NB`` or ``FlipSpin <atoms, 0-based>`` + ``FinalMs``).
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from pathlib import Path

from .structure import Structure

JOB_TYPES = ("opt", "freq", "sp", "mossbauer_sp", "highfield_sp")

# Functionals without a native ORCA keyword -> libxc names (set in %method).
# MN15 has no simple-input keyword in ORCA 6.1 ("UNRECOGNIZED KEYWORD MN15").
LIBXC_FUNCTIONALS = {
    "MN15": ("hyb_mgga_x_mn15", "mgga_c_mn15"),
    "MN15-L": ("mgga_x_mn15_l", "mgga_c_mn15_l"),
}


@dataclass
class BrokenSymmetry:
    """Broken-symmetry specification (both mechanisms live in the %scf block).

    kind="flipspin": ``flip_atoms`` (0-based ORCA indices) + ``final_ms``.
        The charge/multiplicity of the deck is that of the HIGH-SPIN determinant.
    kind="brokensym": ``na``/``nb`` unpaired electrons on site A/B (A first in the coordinates).
    """
    kind: str = "flipspin"
    flip_atoms: tuple[int, ...] = ()
    final_ms: float | None = None
    na: int | None = None
    nb: int | None = None

    def scf_lines(self) -> list[str]:
        if self.kind == "flipspin":
            if not self.flip_atoms or self.final_ms is None:
                raise ValueError("flipspin needs flip_atoms and final_ms")
            ms = f"{self.final_ms:g}" if float(self.final_ms) != int(self.final_ms) else f"{float(self.final_ms):.1f}"
            return [f"FlipSpin {', '.join(str(a) for a in self.flip_atoms)}", f"FinalMs {ms}"]
        if self.kind == "brokensym":
            if self.na is None or self.nb is None:
                raise ValueError("brokensym needs na and nb")
            return [f"BrokenSym {self.na},{self.nb}"]
        raise ValueError(f"unknown broken-symmetry kind {self.kind!r}")


@dataclass
class MethodSpec:
    functional: str = "TPSSh"
    basis: str = "def2-TZVP"
    fe_basis: str | None = "CP(PPP)"       # None: no %basis NewGTO block (e.g. opt/freq)
    grid: str = "DefGrid3"
    special_grid_atoms: int | None = None   # element number for SpecialGridAtoms (26 = Fe)
    special_grid_int_acc: int | None = None
    rijcosx: bool = False
    aux_basis: str = "def2/J"               # used only when rijcosx
    scf_convergence: str = "TightSCF"
    max_iter: int = 500
    solvent: str | None = None              # CPCM solvent name, e.g. "THF"
    broken_symmetry: BrokenSymmetry | None = None
    uks: bool = True
    libxc: tuple[str, str] | None = None    # override (exchange, correlation)
    extra_keywords: tuple[str, ...] = ()
    nprocs: int = 16
    maxcore: int = 3000

    def libxc_pair(self):
        if self.libxc:
            return self.libxc
        return LIBXC_FUNCTIONALS.get(self.functional.upper())

    def with_(self, **kw) -> "MethodSpec":
        return replace(self, **kw)

    def fingerprint(self):
        from .orca_parse import Fingerprint
        return Fingerprint.from_method_spec(self)


@dataclass
class HighFieldOptions:
    """Extra properties for ``highfield_sp`` (A-tensor, g, ZFS D/E)."""
    nuclei_flags: tuple[str, ...] = ("aiso", "adip", "aorb", "fgrad", "rho")
    gtensor: bool = True
    dtensor: str | None = "ssandso"        # so | ss | ssandso | None
    dsoc: str = "cp"                       # cp | qro | pk | cvw  (qro/pk valid only for pure functionals)
    dss: str = "uno"                       # uno | direct  (uno recommended for UKS)
    soc_operator: str | None = "SOMF(1X)"  # simple-input keyword used in the ORCA manual's ZFS example
    relativistic: str | None = None        # e.g. "ZORA"; basis must then be a ZORA-recoupled set
    extra_eprnmr: tuple[str, ...] = ()


def _xyz_block(structure, xyz_file, charge, mult):
    if xyz_file:
        return [f"* xyzfile {charge} {mult} {xyz_file}"]
    if structure is None:
        raise ValueError("provide a Structure or an xyz_file name")
    return [f"* xyz {charge} {mult}"] + structure.orca_coord_lines() + ["*"]


def build_input(job_type: str, method: MethodSpec, *, charge: int, multiplicity: int,
                structure: Structure | None = None, xyz_file: str | None = None,
                highfield: HighFieldOptions | None = None,
                opt_max_iter: int = 200, optimize_hydrogens: bool = False,
                fe_nuclei: str = "all Fe", extra_blocks: str = "") -> str:
    """Return the text of an ORCA input.

    ``xyz_file`` (a name relative to the job directory) takes precedence over inline coordinates.
    ``multiplicity`` is the multiplicity of the deck's reference determinant (the high-spin one
    when a broken-symmetry spec is used).
    """
    if job_type not in JOB_TYPES:
        raise ValueError(f"job_type must be one of {JOB_TYPES}")
    if multiplicity < 1:
        raise ValueError("multiplicity must be >= 1")
    if structure is not None and job_type in ("mossbauer_sp", "highfield_sp") and not structure.fe_indices:
        raise ValueError("structure contains no Fe atom")
    m = method
    props = job_type in ("mossbauer_sp", "highfield_sp")
    hf = (highfield or HighFieldOptions()) if job_type == "highfield_sp" else None
    px = m.libxc_pair()
    # ---- simple input line -------------------------------------------------
    toks = ["UKS" if m.uks else "RKS"]
    if px is None:
        toks.append(m.functional)
    toks.append(m.basis)
    toks.append(f"RIJCOSX {m.aux_basis}" if m.rijcosx else "NoRIJCOSX")
    if job_type == "opt":
        toks.append("Opt")
    elif job_type == "freq":
        toks.append("Freq")
    toks.append(m.grid)
    toks.append(m.scf_convergence)
    if m.solvent:
        toks.append(f"CPCM({m.solvent})")
    if hf is not None:
        if hf.relativistic:
            toks.append(hf.relativistic)
        if hf.soc_operator:
            toks.append(hf.soc_operator)
        if hf.dss == "uno" or hf.dsoc == "qro":
            toks.append("UNO")
    toks += list(m.extra_keywords)
    out = ["! " + " ".join(toks)]
    # ---- %method -----------------------------------------------------------
    meth = []
    if px is not None:
        meth += ["method dft", f"exchange {px[0]}", f"correlation {px[1]}"]
    if props and m.special_grid_atoms is not None:
        meth.append(f"SpecialGridAtoms {m.special_grid_atoms}")
    if props and m.special_grid_int_acc is not None:
        meth.append(f"SpecialGridIntAcc {m.special_grid_int_acc}")
    if meth:
        out += ["%method"] + [f"  {x}" for x in meth] + ["end"]
    out.append(f"%pal nprocs {m.nprocs} end")
    out.append(f"%maxcore {m.maxcore}")
    scf = [f"MaxIter {m.max_iter}"]
    if m.broken_symmetry is not None:
        scf += m.broken_symmetry.scf_lines()
    if len(scf) == 1:
        out.append(f"%scf {scf[0]} end")
    else:
        out += ["%scf"] + [f"  {x}" for x in scf] + ["end"]
    if props and m.fe_basis:
        out += ["%basis", f'  NewGTO Fe "{m.fe_basis}" end', "end"]
    if job_type == "opt":
        if optimize_hydrogens:
            out += [f"%geom MaxIter {opt_max_iter}", "  OptimizeHydrogens true", "end"]
        else:
            out.append(f"%geom MaxIter {opt_max_iter} end")
    if extra_blocks:
        out.append(extra_blocks.rstrip("\n"))
    out += _xyz_block(structure, xyz_file, charge, multiplicity)
    # ---- property block: AFTER the coordinates (ORCA stops otherwise) -------
    if job_type == "mossbauer_sp":
        out += ["", "%eprnmr", f"  Nuclei = {fe_nuclei} {{rho, fgrad}}", "end"]
    elif job_type == "highfield_sp":
        e = ["", "%eprnmr"]
        if hf.gtensor:
            e.append("  gtensor true")
        if hf.dtensor:
            e.append(f"  DTensor {hf.dtensor}")
            e.append(f"  DSOC {hf.dsoc}")
            e.append(f"  DSS {hf.dss}")
        e.append(f"  Nuclei = {fe_nuclei} {{{', '.join(hf.nuclei_flags)}}}")
        e += [f"  {x}" for x in hf.extra_eprnmr]
        e.append("end")
        out += e
    return "\n".join(out) + "\n"


def mossbauer_method(functional: str, **kw) -> MethodSpec:
    """Project default for property single points: def2-TZVP + CP(PPP) on Fe, SpecialGridIntAcc 7."""
    base = dict(functional=functional, basis="def2-TZVP", fe_basis="CP(PPP)", grid="DefGrid3",
                special_grid_atoms=26, special_grid_int_acc=7, rijcosx=False)
    base.update(kw)
    return MethodSpec(**base)


def write_job(path_stem, text: str) -> Path:
    p = Path(f"{path_stem}.inp")
    p.write_text(text)
    return p
