"""Generated decks must stay token-equivalent to the frozen golden decks in tests/data/golden."""
from pathlib import Path
import pytest
from mossbauer_dft.orca_input import build_input, mossbauer_method
from golden_cases import CASES

GOLD = Path(__file__).parent / "data" / "golden"


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden_decks(name):
    assert CASES[name]().split() == (GOLD / f"{name}.inp").read_text().split()


def test_every_golden_deck_has_a_case():
    assert {p.stem for p in GOLD.glob("*.inp")} == set(CASES)


def test_eprnmr_block_after_coordinates():
    spec = mossbauer_method("TPSSh")
    t = build_input("mossbauer_sp", spec, charge=0, multiplicity=4, xyz_file="x.xyz")
    assert t.index("* xyzfile") < t.index("%eprnmr")


def test_mn15_uses_libxc_and_no_simple_keyword():
    t = build_input("mossbauer_sp", mossbauer_method("MN15"), charge=0, multiplicity=2, xyz_file="x.xyz")
    assert "hyb_mgga_x_mn15" in t and "mgga_c_mn15" in t
    assert "MN15" not in t.split("\n")[0]
