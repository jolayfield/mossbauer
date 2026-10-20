"""Minimal molecular-structure container (xyz I/O only; no external deps)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Structure:
    symbols: list[str]
    coords: list[tuple[float, float, float]]
    comment: str = ""

    def __post_init__(self):
        if len(self.symbols) != len(self.coords):
            raise ValueError("symbols and coords differ in length")

    def __len__(self):
        return len(self.symbols)

    @property
    def fe_indices(self) -> list[int]:
        """0-based indices of Fe atoms (ORCA numbering is also 0-based)."""
        return [i for i, s in enumerate(self.symbols) if s.capitalize() == "Fe"]

    @property
    def formula(self) -> str:
        from collections import Counter
        c = Counter(s.capitalize() for s in self.symbols)
        order = sorted(c, key=lambda e: (e not in ("C",), e not in ("H",), e))
        return "".join(f"{e}{c[e] if c[e] > 1 else ''}" for e in order)

    @classmethod
    def from_xyz_text(cls, text: str) -> "Structure":
        lines = text.strip("\n").split("\n")
        n = int(lines[0].split()[0])
        comment = lines[1] if len(lines) > 1 else ""
        sym, xyz = [], []
        for ln in lines[2:2 + n]:
            p = ln.split()
            sym.append(p[0])
            xyz.append((float(p[1]), float(p[2]), float(p[3])))
        if len(sym) != n:
            raise ValueError(f"xyz declares {n} atoms but {len(sym)} were found")
        return cls(sym, xyz, comment)

    @classmethod
    def from_xyz(cls, path) -> "Structure":
        return cls.from_xyz_text(Path(path).read_text(encoding="utf-8"))

    def to_xyz_text(self) -> str:
        out = [str(len(self)), self.comment]
        out += [f"{s:<2s} {x:14.8f} {y:14.8f} {z:14.8f}" for s, (x, y, z) in zip(self.symbols, self.coords)]
        return "\n".join(out) + "\n"

    def write_xyz(self, path) -> None:
        Path(path).write_text(self.to_xyz_text(), encoding="utf-8", newline="\n")

    def orca_coord_lines(self) -> list[str]:
        return [f"{s:<2s} {x:14.8f} {y:14.8f} {z:14.8f}" for s, (x, y, z) in zip(self.symbols, self.coords)]


# ----------------------------------------------------------------------------
# light CIF reader
# ----------------------------------------------------------------------------
import math as _math
import re as _re


def _num(s: str) -> float:
    return float(_re.sub(r"\(.*?\)", "", s.strip()))


def _cif_tokens(line: str) -> list[str]:
    return _re.findall(r"'[^']*'|\"[^\"]*\"|\S+", line)


def _parse_symop(op: str):
    """'x,-y+1/2,z+1/4' -> function(frac xyz) -> frac xyz (no eval)."""
    comps = op.replace(" ", "").lower().strip("'\"").split(",")
    if len(comps) != 3:
        raise ValueError(f"bad symmetry operator {op!r}")
    parsed = []
    for c in comps:
        coef, const = [0.0, 0.0, 0.0], 0.0
        for sign, term in _re.findall(r"([+-]?)([^+-]+)", c):
            sg = -1.0 if sign == "-" else 1.0
            if term in ("x", "y", "z"):
                coef["xyz".index(term)] += sg
            elif _re.fullmatch(r"\d+/\d+", term):
                a, b = term.split("/")
                const += sg * int(a) / int(b)
            else:
                const += sg * float(term)
        parsed.append((coef, const))
    return lambda p: tuple(sum(c[i] * p[i] for i in range(3)) + k for c, k in parsed)


def structure_from_cif_text(text: str, apply_symmetry: bool = True) -> "Structure":
    """Very small CIF reader: first data block, cell + _atom_site loop (+ symmetry operators).

    Limitations (by design, 'light'): no disorder resolution, no bond-based molecule assembly, no occupancy
    filtering beyond dropping atoms with occupancy < 0.5.  Symmetry-generated duplicates (< 0.1 A) are merged.
    Hydrogens are kept as given.  Inspect the result before use.
    """
    lines = []
    for ln in text.splitlines():
        if ln.startswith(";"):
            continue
        lines.append(ln.split(" #")[0].rstrip())
    cell = {}
    for ln in lines:
        m = _re.match(r"\s*_cell_(length_[abc]|angle_(?:alpha|beta|gamma))\s+(\S+)", ln)
        if m:
            cell[m.group(1)] = _num(m.group(2))
    if len(cell) < 6:
        raise ValueError("CIF: unit-cell parameters missing")
    # loops
    loops, i = [], 0
    while i < len(lines):
        if lines[i].strip().lower() == "loop_":
            i += 1
            hdr = []
            while i < len(lines) and lines[i].strip().startswith("_"):
                hdr.append(lines[i].strip().lower())
                i += 1
            toks = []
            while i < len(lines) and not lines[i].strip().startswith(("_", "loop_", "data_")):
                toks += _cif_tokens(lines[i])
                i += 1
            loops.append((hdr, toks))
        else:
            i += 1
    atoms = next(((h, t) for h, t in loops if "_atom_site_fract_x" in h), None)
    if atoms is None:
        raise ValueError("CIF: no _atom_site loop with fractional coordinates")
    h, t = atoms
    n = len(h)
    rows = [t[k:k + n] for k in range(0, len(t) - n + 1, n)]
    col = {name: j for j, name in enumerate(h)}
    ops = []
    if apply_symmetry:
        for hh, tt in loops:
            for key in ("_space_group_symop_operation_xyz", "_symmetry_equiv_pos_as_xyz"):
                if key in hh:
                    nn, j = len(hh), hh.index(key)
                    ops = [tt[k + j].strip("'\"") for k in range(0, len(tt) - nn + 1, nn)]
    ops = [_parse_symop(o) for o in ops] or [_parse_symop("x,y,z")]
    a, b, c = cell["length_a"], cell["length_b"], cell["length_c"]
    al, be, ga = (_math.radians(cell[f"angle_{k}"]) for k in ("alpha", "beta", "gamma"))
    vol = a * b * c * _math.sqrt(1 - _math.cos(al) ** 2 - _math.cos(be) ** 2 - _math.cos(ga) ** 2
                                 + 2 * _math.cos(al) * _math.cos(be) * _math.cos(ga))
    ax = (a, 0.0, 0.0)
    bx = (b * _math.cos(ga), b * _math.sin(ga), 0.0)
    cx = (c * _math.cos(be), c * (_math.cos(al) - _math.cos(be) * _math.cos(ga)) / _math.sin(ga), vol / (a * b * _math.sin(ga)))

    def cart(p):
        return tuple(p[0] * ax[k] + p[1] * bx[k] + p[2] * cx[k] for k in range(3))

    syms, xyz, fr = [], [], []
    for r in rows:
        if "_atom_site_occupancy" in col and r[col["_atom_site_occupancy"]] not in (".", "?"):
            if _num(r[col["_atom_site_occupancy"]]) < 0.5:
                continue
        el = r[col["_atom_site_type_symbol"]] if "_atom_site_type_symbol" in col else r[col["_atom_site_label"]]
        el = _re.match(r"[A-Za-z]{1,2}", _re.sub(r"[+-]?\d*[+-]?$", "", el) or el).group(0)
        el = el[0].upper() + (el[1:].lower() if len(el) > 1 and el[:2].capitalize() in _ELEMENTS else "")
        p0 = tuple(_num(r[col[f"_atom_site_fract_{k}"]]) for k in "xyz")
        for op in ops:
            p = tuple(v % 1.0 for v in op(p0))
            dup = False
            for q, e2 in zip(fr, syms):
                d = [abs((p[k] - q[k] + 0.5) % 1.0 - 0.5) for k in range(3)]
                if e2 == el and _math.dist(cart(d), (0, 0, 0)) < 0.1:
                    dup = True
                    break
            if not dup:
                fr.append(p)
                syms.append(el)
    xyz = [cart(p) for p in fr]
    return Structure(syms, xyz, "from CIF (symmetry expanded, not assembled into molecules)")


_ELEMENTS = {"He", "Li", "Be", "Ne", "Na", "Mg", "Al", "Si", "Cl", "Ar", "Ca", "Sc", "Ti", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
             "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Mo", "Ru", "Rh", "Pd", "Ag", "Cd", "Sn", "Sb", "Te", "Xe", "Pt", "Au", "Hg", "Pb", "Bi"}


class MultipleStructuresError(ValueError):
    """A single-structure input held several structures; the first one is never taken silently."""

    def __init__(self, count: int, filename: str = ""):
        self.count = count
        hint = ("an ORCA trajectory holds every optimization step: supply the final geometry file instead"
                if filename.lower().endswith("_trj.xyz") else
                "use batch intake (GUI page 'Batch intake' or `mossbauer project batch`) for several compounds")
        super().__init__(f"{filename or 'input'} holds {count} structures; {hint}")


def split_xyz_blocks(text: str) -> list[str]:
    """Concatenated xyz frames -> one text per frame.  Blank lines between frames are skipped; a non-integer atom-count
    line or a short last frame raises with its 1-based line number."""
    lines = text.split("\n")
    blocks, i = [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        tok = lines[i].split()[0]
        if not tok.isdigit():
            raise ValueError(f"xyz line {i + 1}: expected an atom count, found {lines[i].strip()[:40]!r}")
        n = int(tok)
        block = lines[i:i + 2 + n]
        if len(block) < 2 + n or any(not ln.strip() for ln in block[2:]):
            raise ValueError(f"xyz line {i + 1}: frame declares {n} atoms but the text ends early")
        blocks.append("\n".join(block) + "\n")
        i += 2 + n
    return blocks


_CIF_DATA = _re.compile(r"^data_(\S*)", _re.IGNORECASE)


def split_cif_blocks(text: str) -> list[tuple[str, str]]:
    """CIF text -> [(block name, block text)], cut at ``data_`` lines outside ``;`` text fields."""
    blocks, name, cur, in_text = [], None, [], False
    for ln in text.splitlines():
        if ln.startswith(";"):
            in_text = not in_text
        m = None if in_text else _CIF_DATA.match(ln)
        if m:
            if name is not None:
                blocks.append((name, "\n".join(cur) + "\n"))
            name, cur = m.group(1), [ln]
        elif name is not None:
            cur.append(ln)
    if name is not None:
        blocks.append((name, "\n".join(cur) + "\n"))
    return blocks


def cif_block_has_atoms(text: str) -> bool:
    return "_atom_site_fract_x" in text.lower()


def count_structures(text: str, filename: str) -> int:
    """How many structures a single-structure reader would see in ``text`` (xyz frames or CIF blocks with atoms)."""
    if filename.lower().endswith(".cif"):
        return sum(cif_block_has_atoms(t) for _, t in split_cif_blocks(text)) or 1
    return _count_complete_frames(text) or 1


def _count_complete_frames(text: str) -> int:
    """Well-formed xyz frames before any trailing text that is not a frame (lenient count for the single-structure guard)."""
    lines, i, n_frames = text.split("\n"), 0, 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        tok = lines[i].split()[0]
        if not tok.isdigit():
            break
        n = int(tok)
        if len(lines) < i + 2 + n or any(len(ln.split()) < 4 for ln in lines[i + 2:i + 2 + n]):
            break
        n_frames += 1
        i += 2 + n
    return n_frames


def read_structure(path) -> "Structure":
    """Read .xyz or .cif by extension."""
    p = Path(path)
    return read_structure_text(p.read_text(encoding="utf-8"), p.name)


def read_structure_text(text: str, filename: str = "structure.xyz") -> "Structure":
    """One structure from xyz or cif text; raises ``MultipleStructuresError`` when the text holds several."""
    n = count_structures(text, filename)
    if n > 1:
        raise MultipleStructuresError(n, filename)
    if filename.lower().endswith(".cif"):
        blocks = [t for _, t in split_cif_blocks(text) if cif_block_has_atoms(t)]
        return structure_from_cif_text(blocks[0] if blocks else text)  # header blocks never lend their cell to the atoms
    return Structure.from_xyz_text(text)
