"""New prediction page (step 1): structure sources, 3D view, checks, and the plan form."""
from __future__ import annotations
import html
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from mossbauer_dft import plan as P
from mossbauer_dft import presets as PR
from mossbauer_dft import project as J
from mossbauer_dft import references as R
from mossbauer_dft import structure_sources as SS
from mossbauer_dft.gui.state import _current, _invalidate, _root, ss
from mossbauer_dft.gui.theme import FE_COLOR, TEXT_COLOR
from mossbauer_dft.gui.widgets import UPLOAD_TYPES, _cli, _preset_names
from mossbauer_dft.structure import MultipleStructuresError, Structure

SMILES_EXAMPLES = {
    "[FeCl4]-  (tetrahedral Fe(III))": "Cl[Fe-](Cl)(Cl)Cl",
    "[Fe(H2O)6]2+  (octahedral Fe(II))": "O->[Fe+2](<-O)(<-O)(<-O)(<-O)<-O",
    "FeCl2(phen)": "Cl[Fe]1(Cl)<-n2cccc3ccc4cccn->1c4c32",
    "[Fe(SPh)4]2-": "[S-](c1ccccc1)[Fe+2]([S-]c1ccccc1)([S-]c1ccccc1)[S-]c1ccccc1",
    "nacnac(Xyl)FeCl  (Fe(II), 3-coordinate)": "CC1=CC(C)=N(c2c(C)cccc2C)->[Fe](Cl)N1c1c(C)cccc1C",
    "[Fe(CN)6]4-": "N#C[Fe-4](C#N)(C#N)(C#N)(C#N)C#N",
    "Ferrocene  (eta5-Cp, written ionically)": "[cH-]1cccc1.[cH-]1cccc1.[Fe+2]",
    "nacnac(Xyl)Fe(eta6-C6H6)  (Fe(I))": "CC1=C(C)C(C)=N(c2c(C)cccc2C)->[Fe]N1c1c(C)cccc1C.c1ccccc1",
    "FeCl2(bpy)2  (cis / trans isomers)": "Cl[Fe]%10%11%12%13Cl.c1ccn%10c(c1)-c1ccccn1%11.c1ccn%12c(c1)-c1ccccn1%13",
    "FeCl3(H2O)3  (fac / mer isomers)": "Cl[Fe](Cl)(Cl)(<-O)(<-O)<-O",
}


def _set_structure(res: SS.BuildResult, filename: str, prefill: dict | None = None):
    """Store the loaded structure; pre-fill the form fields we can infer (set before those widgets render)."""
    st.session_state["structure"] = dict(xyz=res.structure.to_xyz_text(), filename=filename, warnings=res.warnings,
                                         source=res.source, built="built by RDKit" in res.structure.comment,
                                         shape=getattr(res, "polyhedron", ""), isomer=getattr(res, "isomer", ""))
    if res.charge or res.source.startswith(("SMILES", "drawing")):
        st.session_state["charge"] = int(res.charge)
    stem = Path(filename).stem
    if stem and stem not in ("structure", "drawing", "smiles"):
        st.session_state["name"] = stem
    for k, v in (prefill or {}).items():
        st.session_state[k] = v


def _structure() -> Structure | None:
    s = st.session_state.get("structure")
    return Structure.from_xyz_text(s["xyz"]) if s else None


SOURCES = ["Upload file", "Paste XYZ", "SMILES", "Draw", "Examples"]


def _source_picker() -> str:
    return st.segmented_control("Structure source", SOURCES, default="Upload file", key="source", label_visibility="collapsed") or "Upload file"


def _source_body(src: str):
    if src == "Upload file":
        up = st.file_uploader("Structure file", type=UPLOAD_TYPES, key="struct",
                              help="xyz and CIF need no extra packages; MOL / SDF / PDB are read with RDKit.")
        if up is not None and st.session_state.get("_loaded_upload") != (up.name, up.size):
            try:
                _set_structure(SS.read_any(up.getvalue().decode(), up.name), up.name)
                st.session_state["_loaded_upload"] = (up.name, up.size)
            except MultipleStructuresError as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not read {up.name}: {type(e).__name__}: {e}")
    elif src == "Paste XYZ":
        txt = st.text_area("XYZ text (atom count, comment line, then one atom per line)", height=180, key="xyz_text",
                           placeholder="5\n[FeCl4]-\nFe 0.000 0.000 0.000\nCl 1.316 1.316 1.316\n...")
        if st.button("Use this XYZ", key="use_xyz", disabled=not txt.strip()):
            try:
                _set_structure(SS.read_any(txt, "pasted.xyz"), "pasted.xyz")
            except MultipleStructuresError as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001
                st.error(f"{type(e).__name__}: {e}")
    elif src == "SMILES":
        ex = st.selectbox("Start from an example (optional)", ["-"] + list(SMILES_EXAMPLES), key="smi_ex")
        if ex != "-" and st.session_state.get("_smi_ex_applied") != ex:
            st.session_state["smiles"] = SMILES_EXAMPLES[ex]
            st.session_state["_smi_ex_applied"] = ex
        smi = st.text_input("SMILES", key="smiles", placeholder="Cl[Fe-](Cl)(Cl)Cl")
        poly, iso = _coordination_picker("smi", smiles=smi)
        st.caption("Write metal-ligand bonds to neutral donors as dative (`N->[Fe]`) or as plain bonds (converted automatically). "
                   "Ionic forms such as `[Fe+3].[Cl-].[Cl-].[Cl-].[Cl-]` are bonded for you. Cp-type anions and neutral arenes "
                   "written as separate fragments (`[cH-]1cccc1.[cH-]1cccc1.[Fe+2]`, `...[Fe]....c1ccccc1`) bind eta5 / eta6.")
        if st.button("Build 3D structure", key="build_smi", type="primary", disabled=not smi.strip()):
            try:
                with st.spinner("Embedding ligands and relaxing ..."):
                    _set_structure(SS.from_smiles(smi, polyhedron=poly, isomer=iso), "smiles.xyz")
            except ImportError as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001
                st.error(f"{type(e).__name__}: {e}")
    elif src == "Draw":
        _draw_tab()
    else:
        _examples_tab()


@st.cache_data(show_spinner=False, max_entries=64)
def _coord_options(smiles: str | None, molblock: str | None, polyhedron: str | None) -> dict:
    try:
        return SS.coordination_options(smiles=smiles, molblock=molblock, polyhedron=polyhedron)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _coordination_picker(key: str, smiles: str | None = None, molblock: str | None = None) -> tuple[str | None, int | None]:
    """Coordination polyhedron + geometric isomer choices for the structure about to be built (sites: an eta-n ring counts once)."""
    if not (smiles or "").strip() and not molblock:
        return None, None
    try:
        import rdkit  # noqa: F401
    except ImportError:
        return None, None
    o = _coord_options(smiles, molblock, None)
    if not o or "error" in o:
        if o.get("error"):
            st.caption(f"Cannot build yet: {o['error']}")
        return None, None
    hapt = f", incl. eta{'/eta'.join(map(str, o['haptic']))}" if o.get("haptic") else ""
    poly = None
    if len(o["shapes"]) > 1:
        pick = st.radio(f"Fe coordination geometry ({o['cn']} sites{hapt})", ["auto"] + o["shapes"], horizontal=True,
                        key=f"{key}_poly_{o['cn']}", help="auto keeps the usual shape unless a chelate cannot fit it.")
        poly = None if pick == "auto" else pick
        if poly:
            o = _coord_options(smiles, molblock, poly)
    else:
        st.caption(f"Fe coordination: {o['shape']} ({o['cn']} sites{hapt})")
    isos = o.get("isomers", [])
    if len(isos) < 2:
        return poly, None
    labels = [f"{lab}  (default: least steric clash)" if n == 0 else lab for n, lab in enumerate(isos)]
    i = st.radio("Geometric isomer", range(len(isos)), format_func=labels.__getitem__, key=f"{key}_iso_{o['shape']}_{len(isos)}",
                 help="Distinct ligand arrangements on the chosen polyhedron (mirror images are not distinguished).")
    return poly, int(i)


def _draw_tab():
    try:
        from streamlit_ketcher import st_ketcher
    except ImportError:
        st.info("The drawing editor is missing or broken. Run `mossbauer doctor` and reinstall your downloaded wheel.")
        return
    st.caption("Draw the complex (use the periodic-table button for Fe; draw bonds from donors to Fe, and from every ring atom "
               "for eta-n rings; draw Cp as the anion C5H5-). Press **Apply** in the editor, then build. Hydrogens are added automatically.")
    mol = st_ketcher(st.session_state.get("_drawn", ""), height=520, molecule_format="MOLFILE", key="ketcher")
    if mol and mol != st.session_state.get("_drawn"):
        st.session_state["_drawn"] = mol
    drawn = st.session_state.get("_drawn") or ""
    has = ("V2000" in drawn or "V3000" in drawn) and len(drawn.splitlines()) > 4 and not drawn.splitlines()[3].strip().startswith("0  0")
    poly = iso = None
    if has:
        try:
            from rdkit import Chem
            m = Chem.MolFromMolBlock(st.session_state["_drawn"], sanitize=False)
            smi = Chem.MolToSmiles(m) if m is not None else ""
            st.caption(f"Drawn: `{smi}`")
            poly, iso = _coordination_picker("draw", molblock=st.session_state["_drawn"])
        except Exception:  # noqa: BLE001
            pass
    if st.button("Build 3D structure from drawing", key="build_draw", type="primary", disabled=not has):
        try:
            with st.spinner("Embedding ligands and relaxing ..."):
                _set_structure(SS.from_molblock(st.session_state["_drawn"], polyhedron=poly, isomer=iso), "drawing.xyz")
        except Exception as e:  # noqa: BLE001
            st.error(f"{type(e).__name__}: {e}")


def _examples_tab():
    rn = R.default_refset_name()
    ready, _ = R.resolve_refset(rn)
    if not ready:
        st.info("No bundled structures available.")
        return
    labels = {c["id"]: f"{c['id']}  -  Fe({c['formal_ox_state']}), S={c['spin_S']}, q={c['charge']:+d}" for c in ready}
    pick = st.selectbox(f"Bundled `{rn}` reference compounds (with experimental values)", list(labels), format_func=labels.get, key="ex_pick")
    c = next(x for x in ready if x["id"] == pick)
    st.caption(f"{c['name']}  |  geometry: {c.get('geometry_source', 'n/a')}  |  delta_exp {c.get('delta_exp_mm_s')} mm/s, "
               f"|DeltaEQ|_exp {c.get('deq_abs_exp_mm_s')} mm/s")
    if st.button("Load example", key="load_ex", type="primary"):
        res = SS.read_any(Path(c["xyz_path"]).read_text(), Path(c["xyz_path"]).name)
        res.charge = int(c["charge"])
        ox = int(str(c["formal_ox_state"]).replace("+", "")) if str(c.get("formal_ox_state", "")).lstrip("+-").isdigit() else None
        _set_structure(res, f"{c['id']}.xyz", prefill=dict(
            charge=int(c["charge"]), mult=int(c["multiplicity"]), name=c["id"], ox=ox,
            expd=c.get("delta_exp_mm_s"), expq=c.get("deq_abs_exp_mm_s"), temp=float(c.get("delta_T_K") or 0.0)))


def _viewer_html(s: Structure, labels: bool, style: str, height: int = 430) -> str:
    import py3Dmol
    v = py3Dmol.view(width="100%", height=height)
    v.addModel(s.to_xyz_text(), "xyz")
    if style == "ball and stick":
        v.setStyle({}, {"stick": {"radius": 0.14, "colorscheme": "Jmol"}, "sphere": {"scale": 0.24, "colorscheme": "Jmol"}})
    elif style == "sticks":
        v.setStyle({}, {"stick": {"radius": 0.16, "colorscheme": "Jmol"}})
    else:
        v.setStyle({}, {"sphere": {"scale": 0.9, "colorscheme": "Jmol"}})
    v.setStyle({"elem": "Fe"}, {"sphere": {"scale": 0.42 if style != "spheres" else 0.9, "color": FE_COLOR},
                                "stick": {"radius": 0.14, "color": FE_COLOR}})
    if labels:
        for i, e in enumerate(s.symbols):
            if e.capitalize() != "H":
                v.addLabel(str(i), {"position": {"serial": i}, "fontSize": 11, "fontColor": TEXT_COLOR, "backgroundColor": "#FFFFFF",
                                    "backgroundOpacity": 0.75, "borderThickness": 0, "inFront": True, "showBackground": True})
    v.setBackgroundColor("#FFFFFF")
    v.zoomTo()
    return v._make_html()


def _structure_panel():
    s = _structure()
    meta = st.session_state.get("structure") or {}
    if s is None:
        with st.container(border=True):
            st.markdown("#### No structure loaded")
            st.caption("Upload a file, paste XYZ, type a SMILES, draw the complex, or load one of the bundled reference compounds. "
                       "A 3D view, the Fe coordination sphere and basic checks appear here.")
        return
    with st.container(border=True):
        formula = re.sub(r"(\d+)", r"<sub>\1</sub>", s.formula)
        st.markdown(f"<h4 style='margin-bottom:0'>{formula}</h4>", unsafe_allow_html=True)
        src = meta.get("source") or meta.get("filename") or ""
        kind, _, detail = src.partition(" ")
        st.markdown(f"<p class='mb-kv'><b>{len(s)}</b> atoms &nbsp;|&nbsp; <b>{len(s.fe_indices)}</b> Fe &nbsp;|&nbsp; from "
                    f"<b>{html.escape(kind if detail else src)}</b>"
                    + (f" <code style='font-size:0.75rem'>{html.escape(detail if len(detail) < 70 else detail[:67] + '...')}</code>" if detail else "")
                    + "</p>", unsafe_allow_html=True)
        if meta.get("shape"):
            st.markdown(f"<p class='mb-kv'>built as <b>{html.escape(meta['shape'])}</b>"
                        + (f", <b>{html.escape(meta['isomer'])}</b>" if meta.get("isomer") else "") + "</p>", unsafe_allow_html=True)
        v1, v2 = st.columns([3, 2], vertical_alignment="center")
        style = v1.segmented_control("Style", ["ball and stick", "sticks", "spheres"], default="ball and stick", key="view_style",
                                     label_visibility="collapsed") or "ball and stick"
        labels = v2.toggle("Atom indices", value=False, key="view_labels",
                           help="0-based, as ORCA and the broken-symmetry flip atoms use them (H not labelled).")
        try:
            import streamlit.components.v1 as components
            components.html(_viewer_html(s, labels, style), height=440)
        except Exception as exc:
            st.info(f"The 3D viewer is unavailable: {exc}. Run mossbauer doctor and reinstall your downloaded wheel.")
        env = SS.fe_environment(s)
        for e in env:
            nb = pd.DataFrame(e["neighbours"])
            st.markdown(f"**Fe {e['fe']}**: coordination number {e['cn']}")
            if len(nb):
                st.dataframe(nb.rename(columns={"atom": "atom #", "distance": "Fe-X (A)"}), hide_index=True, use_container_width=True,
                             height=min(38 + 35 * len(nb), 260))
        bad = SS.close_contacts(s)
        if bad:
            st.error(f"{len(bad)} atom pair(s) closer than 0.7 A, e.g. atoms {bad[0][0]}-{bad[0][1]} at {bad[0][2]} A: "
                     "duplicated or overlapping atoms.")
        if not s.fe_indices:
            st.error("No Fe atom in this structure: a plan cannot be made.")
        notes = meta.get("warnings", [])
        if notes:
            with st.expander(f"Build notes ({len(notes)})", expanded=False):
                for w in notes:
                    st.markdown("- " + html.escape(w))
        st.download_button("Download xyz", s.to_xyz_text(), f"{Path(meta.get('filename') or 'structure').stem}.xyz", key="dl_xyz")


FORM_DEFAULTS = dict(charge=0, mult=4, name="molecule", ox=1, rad="not suspected", nitrosyl="no", solv="", temp=0.0, expd=None, expq=None)


def page_new():
    for k, v in FORM_DEFAULTS.items():
        st.session_state.setdefault(k, v)
    st.header("New prediction")
    root = _root()
    _cli(f"mossbauer project add --project {root or 'PROJECT'} --structure structure.xyz --charge Q --multiplicity M --ox-state N [...]")
    st.subheader("Structure")
    src = _source_picker()
    if src == "Draw":
        _source_body(src)
        _structure_panel()
    else:
        left, right = st.columns([1.1, 1], gap="large")
        with left:
            _source_body(src)
        with right:
            _structure_panel()
    s = _structure()
    meta = st.session_state.get("structure") or {}
    st.subheader("Electronic state and conditions")
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        charge = c1.number_input("Charge", step=1, key="charge")
        mult = c2.number_input("Multiplicity (2S+1)", min_value=1, step=1, key="mult")
        name = c3.text_input("Name", key="name")
        hint = SS.electron_parity_hint(s, int(charge)) if s is not None else None
        if hint:
            ok = SS.multiplicity_allowed(s, int(charge), int(mult))
            (c2.caption if ok else c2.error)(hint + f"  (S = {(int(mult) - 1) / 2:g})")
        h1, h2, h3, h4, h5 = st.columns(5)
        ox = h1.selectbox("Fe oxidation state", [None, 0, 1, 2, 3, 4, 5, 6], key="ox", format_func=lambda v: "not given" if v is None else f"Fe({v})")
        rad = h2.selectbox("Suspected radical ligand", ["not suspected", "yes"], key="rad")
        no = h3.selectbox("Nitrosyl (Fe-NO)", ["no", "yes"], key="nitrosyl", help="Selects the {FeNO} method rules.")
        solv = h4.text_input("CPCM solvent", key="solv", placeholder="gas phase", help="Blank = gas phase.")
        temp = h5.number_input("Experimental T (K)", key="temp", help="0 = not given")
        e1, e2 = st.columns(2)
        exp_d = e1.number_input("Experimental delta (mm/s, optional)", format="%.3f", key="expd")
        exp_q = e2.number_input("Experimental |DeltaEQ| (mm/s, optional)", format="%.3f", key="expq")
    if meta.get("built"):
        st.info("This geometry was built by RDKit. Keep the plan's geometry step on **optimize**.")
    cur = _current()
    same = None
    if cur is not None and (cur.entry.display_name or cur.entry.entry_id) == name:
        try:
            newest = cur.plan if cur.newest and cur.plan is not None else J.load_version(cur.root, cur.entry.entry_id)
            if J.state_of(newest) == (int(charge), int(mult), None):
                same = (cur.entry.entry_id, newest)
        except Exception:  # noqa: BLE001
            same = None
    if root is None:
        st.info("Open or create a project on the **Project** page first: each plan is saved as an entry of the project.")
    elif same:
        st.caption(f"Same compound and electronic state as the selected entry `{same[0]}`: **Create plan** adds its next version.")
    else:
        st.caption("**Create plan** adds a new entry (this compound in this electronic state) to the project.")
    names = _preset_names(root) if root is not None else []
    preset = None
    if names:
        preset = st.selectbox("Method preset", ["(none: rule defaults)"] + names, key="new_preset", disabled=bool(same),
                              help="A project preset fills the method options of the new entry.")
        if same:
            st.caption("To use a preset for an existing entry, apply it on **2  Review & approve** (it shows the changes first).")
        preset = None if same or preset not in names else preset
    if st.button("Create plan", type="primary", disabled=root is None or s is None or not (s and s.fe_indices)):
        try:
            exp = {k: v for k, v in (("delta", exp_d), ("deq", exp_q)) if v is not None}
            fn = meta.get("filename") or "structure.xyz"
            kw = dict(oxidation_state=ox, radical_ligand=(rad == "yes"), nitrosyl=(no == "yes") or None, solvent=solv or None,
                      temperature_K=temp or None, experimental=exp,
                      structure_filename=fn if fn.endswith((".xyz", ".cif")) else f"{Path(fn).stem}.xyz")
            if same:
                ref = J.add_version(root, same[0], P.make_plan(s, int(charge), int(mult), name=same[1]["inputs"]["name"],
                                                               overrides=same[1]["inputs"].get("overrides"), **kw))
            else:
                if preset:
                    kw["overrides"] = PR.resolve(root, preset)
                ref = J.add_entry(root, s, int(charge), int(mult), name=name, **kw)
            ss.entry, ss.version = ref.entry_id, ref.version
            _invalidate()
            st.session_state["_flash"] = (f"`{ref.entry_id}` v{ref.version}", P.summarize(J.load_version(root, ref.entry_id, ref.version)))
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"{type(e).__name__}: {e}")
    if st.session_state.get("_flash"):
        what, summary = st.session_state.pop("_flash")
        st.success(f"Draft plan created as {what}: continue with **2  Review & approve** in the sidebar.")
        st.code(summary)

