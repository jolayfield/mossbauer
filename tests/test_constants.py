import math
from mossbauer_dft import constants as C


def test_constant_values():
    assert math.isclose(C.mm_s_per_au(), 1.6203, abs_tol=2e-3)       # ~1.62 mm/s per a.u.
    assert math.isclose(C.MHZ_PER_MM_S_EXACT, 11.6248, abs_tol=2e-3)
    assert C.Q_FE57_BARN_DEFAULT == 0.16


def test_q_scaling_and_sign():
    a = C.quadrupole_splitting(-1.0, 0.0, 0.16)
    b = C.quadrupole_splitting(-1.0, 0.0, 0.08)
    assert a < 0 and math.isclose(a, 2 * b)
    assert C.quadrupole_splitting(1.0, 0.0, 0.16, "exact") != C.quadrupole_splitting(1.0, 0.0, 0.16, "orca")
