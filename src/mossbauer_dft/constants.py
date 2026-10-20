"""Physical constants and the EFG -> quadrupole-splitting conversion.

ORCA prints Delta-EQ with Q(57Fe) = 0.16 b and converts frequency to velocity
with 11.6 MHz per mm/s (verified against ORCA 6.1.0 output, whose printed MHz
and mm/s values differ by a factor of 11.6000).  The exact value from the 14.4125 keV gamma energy
is 11.6248 MHz per mm/s.  The default ("orca") reproduces ORCA; pass
``convention="exact"`` for the CODATA-based value.
"""
import math

# CODATA 2018
HARTREE_HZ = 6.579683920502e15          # Eh / h  [Hz]
BOHR_M = 5.29177210903e-11              # a0 [m]
BARN_M2 = 1.0e-28                       # 1 barn [m^2]
SPEED_OF_LIGHT_MM_S = 299792458.0e3     # c [mm/s]
PLANCK_EV_S = 4.135667696e-15           # h [eV s]
E_GAMMA_57FE_EV = 14412.497             # 57Fe 14.4 keV Moessbauer gamma

Q_FE57_BARN_DEFAULT = 0.16              # Q(57Fe) used by ORCA [barn]

MHZ_PER_MM_S_ORCA = 11.6                # ORCA's rounded conversion
MHZ_PER_MM_S_EXACT = (E_GAMMA_57FE_EV / PLANCK_EV_S) / SPEED_OF_LIGHT_MM_S / 1.0e6

CONVENTIONS = {"orca": MHZ_PER_MM_S_ORCA, "exact": MHZ_PER_MM_S_EXACT}


def e2qQ_mhz_per_au(q_barn: float = Q_FE57_BARN_DEFAULT) -> float:
    """e^2 q Q / h in MHz for Vzz = 1 a.u. (Eh / (e a0^2))."""
    return q_barn * BARN_M2 / BOHR_M**2 * HARTREE_HZ / 1.0e6


def mm_s_per_au(q_barn: float = Q_FE57_BARN_DEFAULT, convention: str = "orca") -> float:
    """Delta-EQ (mm/s) per a.u. of Vzz when eta = 0:  (1/2) e Q Vzz."""
    return 0.5 * e2qQ_mhz_per_au(q_barn) / CONVENTIONS[convention]


def quadrupole_splitting(vzz: float, eta: float, q_barn: float = Q_FE57_BARN_DEFAULT,
                         convention: str = "orca") -> float:
    """Signed Delta-EQ [mm/s] = (e Q Vzz / 2) sqrt(1 + eta^2/3).

    Vzz in a.u. (principal value of largest |V|); sign follows Vzz.
    """
    return mm_s_per_au(q_barn, convention) * vzz * math.sqrt(1.0 + eta * eta / 3.0)
