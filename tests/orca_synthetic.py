"""Synthetic ORCA 6.1.0 output excerpts for parser and workflow tests.

Every value is invented. The text uses the section headers and line formats that ORCA 6.1.0 prints (and that
``mossbauer_dft.orca_parse`` reads), with EFG eigenvalues, eta, e**2qQ and Delta-EQ computed consistently from
the chosen tensor, so tests can assert parsed values against the parameters given here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from mossbauer_dft.constants import MHZ_PER_MM_S_ORCA, Q_FE57_BARN_DEFAULT, e2qQ_mhz_per_au

# A fixed proper rotation (det = +1) for the principal axes, so the raw EFG matrix is not diagonal.
_C, _S = math.cos(math.radians(35.0)), math.sin(math.radians(35.0))
_R = ((_C, -_S, 0.0), (_S * 0.8, _C * 0.8, -0.6), (_S * 0.6, _C * 0.6, 0.8))


@dataclass(frozen=True)
class Site:
    index: int = 0
    eigs: tuple = (-0.20, -0.90, 1.10)       # V(Tot) as printed: |Vxx| <= |Vyy| <= |Vzz|, trace 0
    rho0: float = 11820.250000000
    mulliken: tuple = (0.512000, 3.010000)  # (charge, spin)
    loewdin: tuple = (0.231000, 2.870000)

    def matrix(self):
        return [[sum(_R[i][k] * self.eigs[k] * _R[j][k] for k in range(3)) for j in range(3)] for i in range(3)]

    @property
    def eta(self):
        vxx, vyy, vzz = self.eigs
        return (vxx - vyy) / vzz

    @property
    def e2qq_mhz(self):
        return self.eigs[2] * e2qQ_mhz_per_au(Q_FE57_BARN_DEFAULT)

    @property
    def deq_mhz(self):
        return 0.5 * self.e2qq_mhz * math.sqrt(1 + self.eta ** 2 / 3)


TPSSH = ["! UKS TPSSh def2-TZVP NoRIJCOSX DefGrid3 TightSCF", "%method", "  SpecialGridAtoms 26", "  SpecialGridIntAcc 7",
         "end", "%pal nprocs 8 end", "%maxcore 3000", "%scf", "  MaxIter 500", "end", "%basis", '  NewGTO Fe "CP(PPP)" end',
         "end"]
B3LYP = [TPSSH[0].replace("TPSSh", "B3LYP")] + TPSSH[1:]
MN15 = ["! UKS def2-TZVP NoRIJCOSX DefGrid3 TightSCF", "%method", "  method dft", "  exchange hyb_mgga_x_mn15",
        "  correlation mgga_c_mn15", "  SpecialGridAtoms 26", "  SpecialGridIntAcc 7", "end", "%pal nprocs 8 end",
        "%maxcore 3000", "%scf", "  MaxIter 500", "end", "%basis", '  NewGTO Fe "CP(PPP)" end', "end"]
OPT_SVP = ["! UKS def2-SVP NoRIJCOSX Opt DefGrid3 TightSCF", "%method", "  method dft", "  exchange hyb_mgga_x_mn15",
           "  correlation mgga_c_mn15", "end", "%pal nprocs 8 end", "%maxcore 3000", "%scf MaxIter 500 end",
           "%geom MaxIter 200 end"]
EPRNMR = ["", "%eprnmr", "  Nuclei = all Fe {rho, fgrad}", "end"]


def _f(x, w=12, d=7):
    return f"{x:{w}.{d}f}"


def _pops(title, rows):
    bar = "-" * len(title)
    return [bar, title, bar] + [f"{i:4d} {el:2s}:{_f(q, 12, 6)}{_f(s, 12, 6)}" for i, el, q, s in rows]


def _nucleus(site: Site):
    m = site.matrix()
    out = [" -----------------------------------------------------------",
           f" Nucleus {site.index:3d}Fe: A  : Isotope=   57 I=  0.5 P= 17.2798 MHz/au**3 ",
           "                Q  : Isotope=   57 I=  0.5 Q=  0.1600 barn      ",
           "                HFC: iso  = NO dip= NO orb= NO gauge= NO    ",
           "                EFG: fgrad=YES rho=YES                      ",
           " -----------------------------------------------------------", "",
           " Raw EFG matrix (all values in a.u.**-3):", " ------------------------------------------------------"]
    out += ["          " + "".join(_f(v, 15) for v in row) for row in m]
    out += ["", " V(Tot)  " + "".join(_f(v, 15) for v in site.eigs), " Orientation: "]
    out += [f"     {ax}  " + "".join(_f(_R[i][k], 15) for k in range(3)) for i, ax in enumerate("XYZ")]
    out += ["", " Note: Tensor is right-handed", "",
            "Moessbauer quadrupole splitting parameter (proper coordinate system)",
            f" e**2qQ  = {site.e2qq_mhz:12.6f} MHz = {site.e2qq_mhz / MHZ_PER_MM_S_ORCA:10.6f} mm/s",
            f" eta     = {site.eta:12.6f}",
            f" Delta-EQ=(1/2{{e**2qQ}}*sqrt(1+1/3*eta**2) = {site.deq_mhz:12.6f} MHz = {site.deq_mhz / MHZ_PER_MM_S_ORCA:10.6f} mm/s",
            f" RHO(0)= {site.rho0:15.9f} a.u.**-3", ""]
    return out


def orca_output(deck=TPSSH, *, stem="syn_prop", charge=0, mult=4, xyz="syn_geom.xyz", sites=(Site(),), s=1.5, s2=3.812000,
                energy=-2601.234567890123, scf_cycles=24, opt=False, eprnmr=True, terminated=True, extra_deck=()):
    """A synthetic ORCA output excerpt. ``deck`` is the method part of the echoed input (before the coordinate line);
    ``extra_deck`` lines go after it (for example ``FlipSpin``/``FinalMs`` inside a ``%scf`` block)."""
    lines = list(deck) + list(extra_deck) + [f"* xyzfile {charge} {mult} {xyz}"] + (EPRNMR if eprnmr else [])
    echo = [f"|{i + 1:3d}> {x}" for i, x in enumerate(lines)] + [f"|{len(lines) + 1:3d}>                          ****END OF INPUT****"]
    fe = [(x.index, "Fe", *x.mulliken) for x in sites]
    lo = [(x.index, "Fe", *x.loewdin) for x in sites]
    ligands = [(len(sites) + k, "S", -0.050000 * (k + 1), 0.010000 * (k + 1)) for k in range(2)]
    text = ["[mossbauer-dft test fixture] Synthetic ORCA 6.1.0 output excerpt. All values are invented.", "",
            "                         Program Version 6.1.0  -  RELEASE   -", "[... excerpt gap ...]",
            "                                       INPUT FILE", "=" * 80, f"NAME = {stem}.inp"] + echo + [
            "[... excerpt gap ...]", "General Settings:", f" Integral files         IntName         .... {stem}",
            " Hartree-Fock type      HFTyp           .... UHF", f" Total Charge           Charge          .... {charge:4d}",
            f" Multiplicity           Mult            .... {mult:4d}", "", "[... excerpt gap ...]",
            "               *****************************************************",
            "               *                     SUCCESS                       *",
            f"               *           SCF CONVERGED AFTER {scf_cycles:3d} CYCLES          *",
            "               *****************************************************", "", "[... excerpt gap ...]",
            f"Expectation value of <S**2>     : {s2:12.6f}", f"Ideal value S*(S+1) for S={s:<3}   : {s * (s + 1):12.6f}",
            f"Deviation                       : {s2 - s * (s + 1):12.6f}", "[... excerpt gap ...]"]
    text += _pops("MULLIKEN ATOMIC CHARGES AND SPIN POPULATIONS", fe + ligands) + ["[... excerpt gap ...]"]
    text += _pops("LOEWDIN ATOMIC CHARGES AND SPIN POPULATIONS", lo + ligands) + ["[... excerpt gap ...]"]
    text += ["-------------------------   --------------------", f"FINAL SINGLE POINT ENERGY {energy:22.12f}",
             "-------------------------   --------------------", "[... excerpt gap ...]"]
    if eprnmr and sites:
        text += ["-----------------------------------------", f"ELECTRIC AND MAGNETIC HYPERFINE STRUCTURE ({len(sites)} nuclei)",
                 "-----------------------------------------", "", f"Number of nuclei to compute EFG          : {len(sites):5d}",
                 f"Number of nuclei to compute Rho(0)       : {len(sites):5d}", ""]
        for x in sites:
            text += _nucleus(x)
        text += ["[... excerpt gap ...]"]
    if terminated:
        text += ["                             ****ORCA TERMINATED NORMALLY****", "[... excerpt gap ...]"]
    if opt:
        text += ["                    ***********************HURRAY********************",
                 "                    ***        THE OPTIMIZATION HAS CONVERGED     ***",
                 "                    *************************************************", "",
                 "                                *** OPTIMIZATION RUN DONE ***"]
    return "\n".join(text) + "\n"


def property_output(**kw):
    """The single-site TPSSh property output used as the workflow tests' template."""
    return orca_output(**kw)
