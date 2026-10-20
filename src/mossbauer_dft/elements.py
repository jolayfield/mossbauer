"""Atomic numbers (H-Rn) for electron counting."""
_SYMS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd "
         "In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn").split()
Z = {s: i + 1 for i, s in enumerate(_SYMS)}


def atomic_numbers(symbols):
    try:
        return [Z[s.capitalize()] for s in symbols]
    except KeyError as e:
        raise ValueError(f"unsupported element {e}") from None
