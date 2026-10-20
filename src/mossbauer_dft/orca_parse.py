"""ORCA 6.x output parser (stdlib only: usable on a cluster login node).

Parses rho(0) and the EFG (eigenvalues, Vzz sign, eta, ORCA's Delta-EQ) per Fe nucleus, final energy,
<S**2>, Mulliken/Loewdin spin populations, SCF convergence, termination, the method fingerprint
(taken from the echoed input), and -- where present -- hyperfine A tensors, g tensor and D/E.

Verified against ORCA 6.1.0 Moessbauer (rho, fgrad) outputs.  The A-tensor and g-tensor parsers follow
the layout documented in the ORCA 6.1 manual; the D/E parser is tolerant and UNVERIFIED against a
real DFT ZFS output (no such output exists in this project yet).
"""
from __future__ import annotations
import math
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .constants import Q_FE57_BARN_DEFAULT, quadrupole_splitting

_F = r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?"
_FLOATS3 = re.compile(rf"({_F})\s+({_F})\s+({_F})")

# ----------------------------------------------------------------------------
# method fingerprint
# ----------------------------------------------------------------------------
_KNOWN_FUNCTIONALS = {k.lower(): k for k in [
    "B3LYP", "B3LYP/G", "TPSSh", "TPSS", "PBE0", "PBE", "BP86", "BP", "BLYP", "B97-D3", "B97-D3BJ",
    "M06", "M06L", "M06-L", "M062X", "M06-2X", "wB97X-D3", "wB97X-V", "wB97M-V", "wB97X-D4", "r2SCAN",
    "r2SCAN0", "r2SCAN-3c", "TPSS0", "PW6B95", "B2PLYP", "RI-B2PLYP", "O3LYP", "OLYP", "BHLYP", "B1LYP",
    "CAM-B3LYP", "LC-BLYP", "revPBE", "RPBE", "HFS", "LDA", "PWPB95", "B3PW91", "X3LYP", "MN15", "MN15-L",
    "SCAN", "r2SCANh", "B97M-V", "B97M-D3BJ", "PBE38", "TPSSh-D3BJ", "B3LYP-D3BJ"]}
_BASIS_RE = re.compile(r"^(ma-|zora-|dkh-|x2c/)?(def2-|def2$|cc-p|aug-cc|6-31|6-311|sto-|pc-|ano-|sarc|jorge|"
                       r"epr-|iglo|dyall|x2c-)", re.I)
_SIMPLE_NONFUNC = {"uks", "uhf", "rks", "rhf", "ks", "hf", "tightscf", "verytightscf", "normalscf", "loosescf",
                   "opt", "freq", "numfreq", "sp", "autoaux", "uno", "somf(1x)", "norijcosx", "rijcosx",
                   "rij", "nori", "grid4", "grid5", "slowconv", "verysloconv", "kdiis", "soscf", "nopop",
                   "moread", "printbasis", "normalprint", "largeprint", "smallprint", "defgrid1",
                   "defgrid2", "defgrid3", "zora", "dkh", "dkh2", "x2c", "nofinalgrid", "finalgrid"}


@dataclass(frozen=True)
class Fingerprint:
    """Method fingerprint of a calculation: what a calibration is only valid for."""
    functional: str = ""
    basis: str = ""
    fe_basis: str = ""          # "" -> same as basis
    grid: str = ""              # DefGridN
    special_grid_int_acc: str = ""
    special_grid_atoms: str = ""
    ri: str = ""                # NoRIJCOSX | RIJCOSX | RI-J ...
    solvent: str = ""
    program: str = ""           # e.g. "ORCA 6.1.0"

    HARD = ("functional", "basis", "fe_basis")
    SOFT = ("grid", "special_grid_int_acc", "special_grid_atoms", "ri", "solvent", "program")

    @staticmethod
    def _n(v) -> str:
        return re.sub(r"\s+", "", str(v or "")).lower()

    @classmethod
    def from_method_spec(cls, m) -> "Fingerprint":
        px = m.libxc_pair()
        func = m.functional.upper() if px else m.functional
        return cls(functional=func, basis=m.basis, fe_basis=m.fe_basis or "", grid=m.grid,
                   special_grid_int_acc="" if m.special_grid_int_acc is None else str(m.special_grid_int_acc),
                   special_grid_atoms="" if m.special_grid_atoms is None else str(m.special_grid_atoms),
                   ri="RIJCOSX" if m.rijcosx else "NoRIJCOSX", solvent=m.solvent or "")

    def compare(self, other: "Fingerprint") -> tuple[list[str], list[str]]:
        """Return (hard_mismatches, soft_mismatches) as readable strings.

        An empty Fe basis equals the main basis.  Program compares only major.minor.
        """
        def val(fp, k):
            v = getattr(fp, k)
            if k == "fe_basis" and not v:
                v = fp.basis
            if k == "program":
                mm = re.search(r"(\d+)\.(\d+)", v or "")
                v = f"{mm.group(1)}.{mm.group(2)}" if mm else ""
            return self._n(v)
        hard, soft = [], []
        for k in self.HARD:
            if val(self, k) != val(other, k):
                hard.append(f"{k}: calibration={getattr(self, k) or '-'!r} vs calculation={getattr(other, k) or '-'!r}")
        for k in self.SOFT:
            a, b = val(self, k), val(other, k)
            if a != b and not (k in ("special_grid_atoms", "program") and (not a or not b)):
                soft.append(f"{k}: calibration={getattr(self, k) or '-'!r} vs calculation={getattr(other, k) or '-'!r}")
        return hard, soft

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: dict) -> "Fingerprint":
        return cls(**{k: str(d.get(k, "") or "") for k in cls.__dataclass_fields__})


def fingerprint_from_input_text(inp: str, program: str = "") -> Fingerprint:
    """Build a fingerprint from ORCA input text (the echoed input in an .out, or an .inp)."""
    lines = []
    for ln in inp.split("\n"):
        m = re.match(r"^\|\s*\d+>\s?(.*)$", ln)
        lines.append(m.group(1) if m else ln)
    text = "\n".join(lines)
    simple = [l[1:].strip() for l in lines if l.strip().startswith("!")]
    toks = [t for l in simple for t in l.split()]
    # functional
    func = ""
    ex = re.search(r"^\s*exchange\s+(\S+)", text, re.I | re.M)
    co = re.search(r"^\s*correlation\s+(\S+)", text, re.I | re.M)
    if ex:
        rev = {v[0]: k for k, v in __import__("mossbauer_dft.orca_input", fromlist=["x"]).LIBXC_FUNCTIONALS.items()}
        func = rev.get(ex.group(1).lower(), ex.group(1) + ("+" + co.group(1) if co else ""))
    else:
        for t in toks:
            if t.lower() in _KNOWN_FUNCTIONALS:
                func = _KNOWN_FUNCTIONALS[t.lower()]
                break
    basis = ""
    for t in toks:
        if _BASIS_RE.match(t) and "/" not in t:
            basis = t
            break
    fe = ""
    m = re.search(r'NewGTO\s+Fe\s+"([^"]+)"', text, re.I)
    if m:
        fe = m.group(1)
    grid = next((t for t in toks if re.match(r"(?i)defgrid\d", t)), "")
    sia = re.search(r"SpecialGridIntAcc\s+(\d+)", text, re.I)
    sga = re.search(r"SpecialGridAtoms\s+(\d+)", text, re.I)
    ri = ""
    for i, t in enumerate(toks):
        if t.lower() == "norijcosx":
            ri = "NoRIJCOSX"
        elif t.lower() == "rijcosx":
            ri = "RIJCOSX"
    solv = ""
    m = re.search(r"(?i)\bC?PCM\(([^)]+)\)", " ".join(toks))
    if m:
        solv = m.group(1)
    return Fingerprint(functional=func, basis=basis, fe_basis=fe, grid=grid,
                       special_grid_int_acc=sia.group(1) if sia else "", special_grid_atoms=sga.group(1) if sga else "",
                       ri=ri, solvent=solv, program=program)


# ----------------------------------------------------------------------------
# result containers
# ----------------------------------------------------------------------------
@dataclass
class HyperfineTensor:
    nucleus_index: int
    element: str
    components: dict = field(default_factory=dict)   # name -> (x,y,z) MHz, e.g. "A(FC)", "A(SD)", "A(Tot)"
    a_iso_mhz: float | None = None
    raw_matrix_mhz: list | None = None


@dataclass
class FeSite:
    index: int                      # 0-based ORCA atom index
    element: str = "Fe"
    rho0: float | None = None       # a.u.^-3
    v_eigs_orca: tuple | None = None   # as printed (V(Tot) row)
    v_matrix: list | None = None
    eta_orca: float | None = None
    e2qQ_mhz: float | None = None
    deq_orca_mm_s: float | None = None  # signed, as printed by ORCA (Q = q_orca barn)
    q_orca_barn: float | None = None
    mulliken_spin: float | None = None
    mulliken_charge: float | None = None
    loewdin_spin: float | None = None
    loewdin_charge: float | None = None

    # ---- derived EFG quantities (recomputed from the eigenvalues) -------------
    def principal_axes(self):
        """(Vxx, Vyy, Vzz) ordered |Vxx| <= |Vyy| <= |Vzz|."""
        if not self.v_eigs_orca:
            return None
        return tuple(sorted(self.v_eigs_orca, key=abs))

    @property
    def vzz(self):
        p = self.principal_axes()
        return None if p is None else p[2]

    @property
    def vzz_sign(self):
        v = self.vzz
        return None if v is None or v == 0 else (1 if v > 0 else -1)

    @property
    def eta(self):
        p = self.principal_axes()
        if p is None or p[2] == 0:
            return None
        return (p[0] - p[1]) / p[2]

    @property
    def trace_residual(self):
        return None if not self.v_eigs_orca else sum(self.v_eigs_orca)

    def deq(self, q_barn: float = Q_FE57_BARN_DEFAULT, convention: str = "orca") -> float | None:
        """Signed Delta-EQ [mm/s] = (e Q Vzz / 2) sqrt(1+eta^2/3), recomputed with a configurable Q."""
        if self.vzz is None:
            return None
        return quadrupole_splitting(self.vzz, self.eta, q_barn, convention)

    def abs_deq(self, q_barn: float = Q_FE57_BARN_DEFAULT, convention: str = "orca") -> float | None:
        d = self.deq(q_barn, convention)
        return None if d is None else abs(d)


@dataclass
class OrcaResult:
    path: str = ""
    program: str = ""
    normal_termination: bool = False
    error_termination: bool = False
    scf_converged: bool | None = None       # outcome of the LAST SCF
    scf_cycles: int | None = None
    n_scf_runs: int = 0
    geom_converged: bool | None = None      # opt jobs
    is_optimization: bool = False
    final_energy: float | None = None
    s2: float | None = None
    s2_ideal: float | None = None
    charge: int | None = None
    multiplicity: int | None = None
    fingerprint: Fingerprint = field(default_factory=Fingerprint)
    fe_sites: list = field(default_factory=list)
    hyperfine: list = field(default_factory=list)
    g_tensor: dict | None = None
    zfs: list = field(default_factory=list)       # [{"label","D_cm1","E_over_D"}]
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.normal_termination and bool(self.scf_converged) and not self.error_termination

    def summary(self, q_barn: float = Q_FE57_BARN_DEFAULT) -> list[dict]:
        rows = []
        for s in self.fe_sites:
            rows.append(dict(path=self.path, atom=s.index, rho0=s.rho0, vzz=s.vzz, vzz_sign=s.vzz_sign, eta=s.eta,
                             deq_signed=s.deq(q_barn), abs_deq=s.abs_deq(q_barn), deq_orca=s.deq_orca_mm_s,
                             s2=self.s2, fe_spin_mulliken=s.mulliken_spin, fe_spin_loewdin=s.loewdin_spin,
                             energy=self.final_energy, converged=self.scf_converged, normal=self.normal_termination))
        return rows


# ----------------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------------
_NUC_HDR = re.compile(r"^\s*Nucleus\s+(\d+)\s*([A-Za-z]{1,2})\s*:")
_POP_ROW = re.compile(r"^\s*(\d+)\s+([A-Za-z]{1,2})\s*:\s+(" + _F + r")\s+(" + _F + r")\s*$")


def _triple(line):
    m = _FLOATS3.search(line)
    return tuple(float(x) for x in m.groups()) if m else None


def _parse_nucleus_block(idx, el, blk, res: OrcaResult):
    site = FeSite(index=idx, element=el)
    hfc = HyperfineTensor(idx, el)
    has_hfc = False
    n = len(blk)
    for i, ln in enumerate(blk):
        s = ln.strip()
        mq = re.search(r"Q\s*=\s*(" + _F + r")\s*barn", ln)
        if mq and site.q_orca_barn is None:
            site.q_orca_barn = float(mq.group(1))
        if s.startswith("Raw EFG matrix"):
            rows = []
            for k in range(i + 1, min(i + 8, n)):
                t = _triple(blk[k])
                if t and not blk[k].strip().startswith("V("):
                    rows.append(list(t))
                if len(rows) == 3:
                    break
            if len(rows) == 3:
                site.v_matrix = rows
        elif s.startswith("V(Tot)"):
            t = _triple(ln)
            if t:
                site.v_eigs_orca = t
        elif s.startswith("e**2qQ"):
            m = re.search(r"=\s*(" + _F + r")\s*MHz", ln)
            if m:
                site.e2qQ_mhz = float(m.group(1))
        elif s.startswith("eta"):
            m = re.match(r"eta\s*=\s*(" + _F + ")", s)
            if m:
                site.eta_orca = float(m.group(1))
        elif s.startswith("Delta-EQ"):
            m = re.search(r"=\s*(" + _F + r")\s*MHz\s*=\s*(" + _F + r")\s*mm/s", ln)
            if m:
                site.deq_orca_mm_s = float(m.group(2))
        elif s.startswith("RHO(0)"):
            m = re.search(r"=\s*(" + _F + ")", s)
            if m:
                site.rho0 = float(m.group(1))
        elif s.startswith("Raw HFC matrix"):
            rows = []
            for k in range(i + 1, min(i + 8, n)):
                t = _triple(blk[k])
                if t:
                    rows.append(list(t))
                if len(rows) == 3:
                    break
            if len(rows) == 3:
                hfc.raw_matrix_mhz = rows
                has_hfc = True
        else:
            m = re.match(r"^(A\([A-Za-z]+\))\s+(" + _F + r")\s+(" + _F + r")\s+(" + _F + r")(?:\s+A\(iso\)=\s*(" + _F + "))?", s)
            if m:
                hfc.components[m.group(1)] = tuple(float(m.group(k)) for k in (2, 3, 4))
                if m.group(5) is not None:
                    hfc.a_iso_mhz = float(m.group(5))
                has_hfc = True
    return site, (hfc if has_hfc else None)


def _parse_pop(lines, start):
    rows = {}
    j = start
    while j < len(lines):
        m = _POP_ROW.match(lines[j])
        if m:
            rows[int(m.group(1))] = (m.group(2), float(m.group(3)), float(m.group(4)))
        elif rows:
            break
        j += 1
        if j - start > 100000:
            break
    return rows


def parse_orca_text(text: str, path: str = "") -> OrcaResult:
    res = OrcaResult(path=str(path))
    L = text.split("\n")
    n = len(L)
    # ---- header / input echo --------------------------------------------------
    s_inp = e_inp = None
    for i, l in enumerate(L):
        if res.program == "":
            m = re.search(r"Program Version\s+([\d.]+)", l)
            if m:
                res.program = "ORCA " + m.group(1)
        if s_inp is None and "INPUT FILE" in l:
            s_inp = i
        if e_inp is None and "END OF INPUT" in l:
            e_inp = i
            break
    if s_inp is not None and e_inp is not None:
        res.fingerprint = fingerprint_from_input_text("\n".join(L[s_inp:e_inp + 1]), res.program)
    else:
        res.warnings.append("no echoed input found: method fingerprint is empty")
        res.fingerprint = Fingerprint(program=res.program)
    # ---- scalar quantities -----------------------------------------------------
    scf_events = []          # (line, converged, cycles)
    last_pop = {"mull": None, "loew": None}
    last_s2 = None
    unrec = []
    for i, l in enumerate(L):
        if "SCF CONVERGED AFTER" in l:
            m = re.search(r"AFTER\s+(\d+)", l)
            scf_events.append((i, True, int(m.group(1)) if m else None))
        elif "SCF NOT CONVERGED AFTER" in l:
            m = re.search(r"AFTER\s+(\d+)", l)
            scf_events.append((i, False, int(m.group(1)) if m else None))
        elif l.startswith("FINAL SINGLE POINT ENERGY"):
            try:
                res.final_energy = float(l.split()[-1])
            except ValueError:
                pass
        elif l.startswith("Expectation value of <S**2>"):
            m = re.search(r":\s*(" + _F + ")", l)
            if m:
                last_s2 = float(m.group(1))
                if i + 1 < n:
                    m2 = re.search(r"S=\s*(" + _F + r"|\d+)", L[i + 1])
                    if m2:
                        S = float(m2.group(1))
                        res.s2_ideal = S * (S + 1)
        elif l.startswith("MULLIKEN ATOMIC CHARGES AND SPIN POPULATIONS"):
            last_pop["mull"] = i
        elif l.startswith("LOEWDIN ATOMIC CHARGES AND SPIN POPULATIONS"):
            last_pop["loew"] = i
        elif "THE OPTIMIZATION HAS CONVERGED" in l:
            res.geom_converged = True
        elif "OPTIMIZATION RUN DONE" in l or "GEOMETRY OPTIMIZATION CYCLE" in l:
            res.is_optimization = True
        elif "ORCA TERMINATED NORMALLY" in l:
            res.normal_termination = True
        elif "ORCA finished by error termination" in l or "aborting the run" in l:
            res.error_termination = True
        elif re.match(r"\s*Total Charge\s+Charge\s+\.\.\.\.", l) and res.charge is None:
            res.charge = int(l.split()[-1])
        elif re.match(r"\s*Multiplicity\s+Mult\s+\.\.\.\.", l) and res.multiplicity is None:
            res.multiplicity = int(l.split()[-1])
    res.n_scf_runs = len(scf_events)
    if scf_events:
        res.scf_converged = scf_events[-1][1]
        res.scf_cycles = scf_events[-1][2]
    res.s2 = last_s2
    if res.is_optimization and res.geom_converged is None:
        res.geom_converged = False
    # ---- Fe / nuclei blocks (EFG + rho) ----------------------------------------
    hdr = [i for i, l in enumerate(L) if "ELECTRIC AND MAGNETIC HYPERFINE STRUCTURE" in l]
    if hdr:
        start = hdr[-1]
        end = n
        for k in range(start + 1, n):
            if "Hyperfine and quadrupole coupling calculation done" in L[k] or L[k].startswith("SUGGESTED CITATIONS"):
                end = k
                break
        seg = L[start:end]
        starts = [(i, _NUC_HDR.match(l)) for i, l in enumerate(seg) if _NUC_HDR.match(l)]
        for a, (i, m) in enumerate(starts):
            j = starts[a + 1][0] if a + 1 < len(starts) else len(seg)
            site, hfc = _parse_nucleus_block(int(m.group(1)), m.group(2), seg[i:j], res)
            if hfc is not None:
                res.hyperfine.append(hfc)
            if site.element.capitalize() == "Fe" or site.rho0 is not None or site.v_eigs_orca is not None:
                res.fe_sites.append(site)
    # ---- populations on Fe -----------------------------------------------------
    for key, hd in (("mull", last_pop["mull"]), ("loew", last_pop["loew"])):
        if hd is None:
            continue
        rows = _parse_pop(L, hd + 1)
        for s in res.fe_sites:
            if s.index in rows:
                el, q, sp = rows[s.index]
                if key == "mull":
                    s.mulliken_charge, s.mulliken_spin = q, sp
                else:
                    s.loewdin_charge, s.loewdin_spin = q, sp
    # ---- g tensor ----------------------------------------------------------------
    for i, l in enumerate(L):
        if "ELECTRONIC G-MATRIX" in l:
            g = {}
            for k in range(i, min(i + 40, n)):
                mm = re.match(r"^\s*g\(tot\)\s+(" + _F + r")\s+(" + _F + r")\s+(" + _F + r")\s+iso=\s*(" + _F + ")", L[k])
                if mm:
                    g = {"g_principal": tuple(float(mm.group(x)) for x in (1, 2, 3)), "g_iso": float(mm.group(4))}
                md = re.match(r"^\s*Delta-g\s+(" + _F + r")\s+(" + _F + r")\s+(" + _F + ")", L[k])
                if md:
                    g["delta_g"] = tuple(float(md.group(x)) for x in (1, 2, 3))
            if g:
                res.g_tensor = g
    # ---- ZFS (tolerant; unverified for DFT output) ---------------------------------
    for i, l in enumerate(L):
        m = re.match(r"^\s*D\s*=\s*(" + _F + r")\s*cm-1", l) or re.match(r"^\s*D\s*=\s*(" + _F + r")\s*cm\*\*-1", l)
        if m:
            d = float(m.group(1))
            ed = None
            for k in range(i + 1, min(i + 4, n)):
                m2 = re.match(r"^\s*E/D\s*=\s*(" + _F + ")", L[k])
                if m2:
                    ed = float(m2.group(1))
                    break
            label = next((L[k].strip() for k in range(i - 1, max(i - 6, -1), -1) if L[k].strip() and not L[k].startswith("Direction")), "")
            res.zfs.append({"label": label, "D_cm1": d, "E_over_D": ed})
    # ---- sanity warnings -----------------------------------------------------------
    if not res.normal_termination:
        res.warnings.append("ORCA did not terminate normally")
    if res.scf_converged is False:
        res.warnings.append("last SCF did not converge")
    if res.scf_converged is None:
        res.warnings.append("no SCF convergence message found")
    for s in res.fe_sites:
        tr = s.trace_residual
        if tr is not None and abs(tr) > 1e-3 * max(abs(x) for x in s.v_eigs_orca):
            res.warnings.append(f"Fe {s.index}: EFG eigenvalues are not traceless (sum={tr:.2e})")
    return res


def parse_orca_output(path) -> OrcaResult:
    p = Path(path)
    return parse_orca_text(p.read_text(errors="replace"), str(p))


def result_to_dict(res: OrcaResult) -> dict:
    d = asdict(res)
    d["fingerprint"] = res.fingerprint.to_dict()
    return d
