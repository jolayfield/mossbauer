"""High-field property parsing.

DECLARED DEVIATION: no ORCA high-field (A / g / D) output exists in this project yet, so the fixtures below
are SYNTHETIC, written from the layout documented in the ORCA 6.1 manual (sec. 5.22).  Numbers are the manual's example
numbers, not a real iron calculation.  The D/E parser is therefore unverified against a real DFT ZFS block.
"""
import pytest
from mossbauer_dft.orca_parse import parse_orca_text

SYN = """
 Nucleus   0Fe: A  : Isotope=   57 I=  0.5 P= 17.2798 MHz/au**3
                Q  : Isotope=   57 I=  0.5 Q=  0.1600 barn
 Raw HFC matrix (all values in MHz):
 -----------------------------------
               695.8952           0.0000          -0.0000
                 0.0000         543.0617          -0.0000
                -0.0000          -0.0000         543.0617

 A(FC)         594.0062         594.0062         594.0062
 A(SD)         -50.9445         -50.9445         101.8890
 A(Tot)        543.0617         543.0617         695.8952  A(iso)=594.0062
 Raw EFG matrix (all values in a.u.**-3):
 ------------------------------------------------------
               -0.4291692      0.2161293      0.1620970
                0.2161293     -0.0207317     -0.8067988
                0.1620970     -0.8067988      0.4499009
 V(Tot)       -0.2400000     -0.8150000      1.0550000
 RHO(0)=  11821.083614207 a.u.**-3
Hyperfine and quadrupole coupling calculation done in   0.4 sec
"""


def _text():
    return ("-----------------------------------------\nELECTRIC AND MAGNETIC HYPERFINE STRUCTURE (1 nuclei)\n"
            "-----------------------------------------\n" + SYN +
            """
-------------------
ELECTRONIC G-MATRIX
-------------------
 g(tot)       2.0021275    2.0059858    2.0126431 iso=  2.0069188
 Delta-g     -0.0001917    0.0036665    0.0103238 iso=  0.0045995
-------------------------------------------- ZERO-FIELD SPLITTING
(2ND ORDER SPIN-ORBIT COUPLING CONTRIBUTION)
Direction X=1 Y=2 Z=0
D = -101.374638 cm-1
E/D = 0.000096
""")


def test_atensor_g_and_zfs():
    r = parse_orca_text(_text())
    (h,) = r.hyperfine
    assert h.a_iso_mhz == pytest.approx(594.0062) and h.components["A(SD)"][2] == pytest.approx(101.8890)
    assert h.raw_matrix_mhz[0][0] == pytest.approx(695.8952)
    assert r.g_tensor["g_iso"] == pytest.approx(2.0069188) and r.g_tensor["g_principal"][2] == pytest.approx(2.0126431)
    assert r.zfs[0]["D_cm1"] == pytest.approx(-101.374638) and r.zfs[0]["E_over_D"] == pytest.approx(0.000096)
    assert r.fe_sites[0].rho0 == pytest.approx(11821.083614207)
