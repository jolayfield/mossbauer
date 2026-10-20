"""Batch intake page: many compound-states at once (preview every row, then draft entries for the ready ones)."""
from __future__ import annotations
import copy
import hashlib
import html
import json
from collections import Counter

import pandas as pd
import streamlit as st

from mossbauer_dft import batch as B
from mossbauer_dft import presets as PR
from mossbauer_dft import project as J
from mossbauer_dft.gui.state import _goto, _invalidate, _root, ss
from mossbauer_dft.gui.widgets import UPLOAD_TYPES, _cli, _pill, _preset_names

BATCH_TRI = {"yes": True, "no": False}
BATCH_BADGE = {"ready": "approved", "ready-open-items": "partial", "skipped": "older results", "blocked": "modified",
               "created": "analyzed"}
_BATCH_INT, _BATCH_FLOAT = B.INT_COLUMNS, B.FLOAT_COLUMNS


def _batch_state():
    ss.setdefault("batch_rows", [])
    if ss.get("_batch_src") is None:
        ss._batch_src = B.Sources()
    ss.setdefault("_batch_seen", set())   # content hashes of the uploads and pastes already applied
    ss.setdefault("_batch_ver", 0)        # editor key generation (KTD11)
    ss.setdefault("_batch_npaste", 0)


def _batch_key() -> str:
    return f"batch_editor_{ss._batch_ver}"


def _batch_cell(col: str, v):
    """A grid cell value back in the row form batch.py reads (yes/no -> bool, whole numbers -> int)."""
    if v is None or (isinstance(v, float) and v != v) or (isinstance(v, str) and not v.strip()):
        return None
    if col in B.TRI_COLUMNS:
        return BATCH_TRI.get(str(v).strip().lower(), v)
    if col in _BATCH_INT:
        return int(v) if isinstance(v, (int, float)) and float(v).is_integer() else v
    if col in _BATCH_FLOAT:
        return float(v) if isinstance(v, (int, float)) else v
    return str(v)


def _batch_base() -> list[dict]:
    """The rows the current editor generation was created from.  The editor must keep receiving exactly this data:
    feeding it rows that already contain its own edits makes Streamlit drop the pending edits.  Rows replaced from
    outside (e.g. set directly in session state) start a new generation."""
    if ss.get("_batch_base_ver") != ss._batch_ver or ss.get("_batch_rows_seen") is not ss.batch_rows:
        if ss.get("_batch_rows_seen") is not None and ss.get("_batch_rows_seen") is not ss.batch_rows:
            ss._batch_ver += 1
        ss._batch_base = copy.deepcopy(ss.batch_rows)
        ss._batch_base_ver = ss._batch_ver
        ss._batch_rows_seen = ss.batch_rows
    return ss._batch_base


def _batch_fold():
    """Canonical rows = this generation's base rows + the grid's cell edits (re-sent by the editor on every rerun, so
    this is idempotent); structural changes rotate the editor key so an old edit is never applied to a different row."""
    rows = copy.deepcopy(_batch_base())
    ss.batch_rows = rows
    ss._batch_rows_seen = rows
    for i, changes in ((ss.get(_batch_key()) or {}).get("edited_rows") or {}).items():
        i = int(i)
        if 0 <= i < len(rows):
            for col, v in changes.items():
                if col in B.COLUMNS and col != "structure":
                    rows[i][col] = _batch_cell(col, v)


def _batch_set_rows(rows: list[dict]):
    ss.batch_rows = rows
    ss._batch_ver += 1
    ss._batch_base = copy.deepcopy(rows)
    ss._batch_base_ver = ss._batch_ver
    ss._batch_rows_seen = rows
    if (ss.get("batch_row_pick") or 0) >= len(rows):
        ss.batch_row_pick = max(len(rows) - 1, 0)


def _batch_add(items: list[tuple[str, str]]):
    """Load new sources (unique names) and append a row for each newly found structure no row refers to yet; a row
    imported earlier whose structure was missing now resolves by its reference instead."""
    src = ss._batch_src
    before = set(src.structures)
    B.load_sources(items, into=src)
    used = {r.get("structure") for r in ss.batch_rows}
    new = [r for r in B.rows_from_sources(src) if r["structure"] not in before and r["structure"] not in used]
    _batch_set_rows(ss.batch_rows + new)


def _cb_batch_paste():
    _batch_fold()
    txt = ss.get("batch_paste") or ""
    h = hashlib.sha256(txt.encode()).hexdigest()
    if h in ss._batch_seen:
        ss._batch_msg = "That text was already added."
    else:
        ss._batch_seen.add(h)
        ss._batch_npaste += 1
        _batch_add([(f"pasted-{ss._batch_npaste}.xyz", txt)])
    ss.batch_paste = ""


def _cb_batch_dup(i: int):
    _batch_fold()
    rows = ss.batch_rows
    if 0 <= i < len(rows):
        _batch_set_rows(rows[:i + 1] + [dict(rows[i])] + rows[i + 1:])


def _cb_batch_remove(i: int):
    _batch_fold()
    rows = ss.batch_rows
    if 0 <= i < len(rows):
        _batch_set_rows(rows[:i] + rows[i + 1:])


def _batch_import(pending: dict):
    _batch_set_rows([dict(B.EMPTY_ROW, **r) for r in pending["rows"]])
    ss._batch_recorded = pending["options"]
    ss._batch_csv_seen, ss._batch_csv_pending = pending["hash"], None


def _cb_batch_import(pending: dict):
    _batch_fold()
    _batch_import(pending)


def _cb_batch_csv_keep(h: str):
    ss._batch_csv_seen, ss._batch_csv_pending = h, None


def _batch_status(row: dict, src: B.Sources) -> str:
    """The first problem preview would block this row for before building its plan (same rule as the library)."""
    return B.row_problem(row, src) or ""


def _batch_frame(rows: list[dict], src: B.Sources) -> pd.DataFrame:
    recs = []
    for r in rows:
        d = {c: r.get(c) for c in B.COLUMNS}
        for c in B.TRI_COLUMNS:
            d[c] = "yes" if d[c] is True else "no" if d[c] is False else "" if d[c] is None else str(d[c])
        for c in ("structure", "compound", "solvent"):
            d[c] = "" if d[c] is None else str(d[c])
        recs.append(d)
    df = pd.DataFrame(recs, columns=B.COLUMNS)
    for c in _BATCH_INT:  # a value that is not a whole number shows blank here; the status column names it
        num = pd.to_numeric(df[c], errors="coerce")
        df[c] = num.where(num.isna() | (num % 1 == 0)).astype("Int64")
    for c in _BATCH_FLOAT:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df


def _batch_options() -> dict:
    """The batch-wide method options as `cli._overrides` builds them: only the widgets changed from the rule (KTD6)."""
    o = {}
    g = (ss.get("bo_geometry") or "").strip()
    if g:
        f, _, b = g.partition("/")
        o["geometry"] = dict(mode="optimize", functional=f.strip(), basis=b.strip() or "def2-SVP")
    if ss.get("bo_freq"):
        o.setdefault("geometry", dict(mode="optimize", functional="MN15", basis="def2-SVP"))["freq"] = True
    for k in ("delta_functional", "deq_functional", "crosscheck_functional"):
        v = (ss.get(f"bo_{k}") or "").strip()
        if v:
            o[k] = v
    if (ss.get("bo_calibration_strategy") or "auto") != "auto":
        o["calibration_strategy"] = ss.bo_calibration_strategy
    if ss.get("bo_nprocs"):
        o["nprocs"] = int(ss.bo_nprocs)
    for k in ("highfield", "allow_unverified_calibration", "allow_protocol_mismatch"):
        if ss.get(f"bo_{k}"):
            o[k] = True
    return o


_NO_PRESET = "(choose a preset)"
_BO_KEYS = ("bo_delta_functional", "bo_deq_functional", "bo_crosscheck_functional", "bo_geometry", "bo_freq",
            "bo_calibration_strategy", "bo_nprocs", "bo_highfield", "bo_allow_unverified_calibration", "bo_allow_protocol_mismatch")


def _cb_batch_use_preset():
    """Fill the option widgets from the chosen preset; refuse a preset the widgets cannot show (it would silently change)."""
    name = ss.get("bo_preset_pick")
    if name == _NO_PRESET:
        return
    before = {k: ss.get(k) for k in _BO_KEYS}
    opts = PR.get(_root(), name)["options"]
    g = opts.get("geometry") or {}
    vals = dict(bo_delta_functional=opts.get("delta_functional", ""), bo_deq_functional=opts.get("deq_functional", ""),
                bo_crosscheck_functional=opts.get("crosscheck_functional", ""),
                bo_geometry=f"{g['functional']}/{g['basis']}" if g.get("functional") else "", bo_freq=bool(g.get("freq")),
                bo_calibration_strategy=opts.get("calibration_strategy", "auto"), bo_nprocs=opts.get("nprocs"),
                bo_highfield=bool(opts.get("highfield")), bo_allow_unverified_calibration=bool(opts.get("allow_unverified_calibration")),
                bo_allow_protocol_mismatch=bool(opts.get("allow_protocol_mismatch")))
    if vals["bo_calibration_strategy"] not in ("auto", "lookup", "compute_refset"):
        vals["bo_calibration_strategy"] = "auto"
    for k, v in vals.items():
        ss[k] = v
    shown = _batch_options()
    if shown != opts:
        for k, v in before.items():
            ss[k] = v
        diff = sorted(set(opts) ^ set(shown) | {k for k in opts if shown.get(k) != opts[k]})
        ss._batch_msg = (f"Preset {name!r} uses options this page cannot show ({', '.join(diff)}); "
                         "use it from the CLI (`--preset`) instead.")
    else:
        ss._batch_msg = f"Batch options filled from preset {name!r}."


def _cb_batch_save_preset():
    try:
        PR.save(_root(), ss.get("bo_preset_name") or "", _batch_options(), description=ss.get("bo_preset_desc") or "",
                replace=bool(ss.get("bo_preset_replace")))
        ss._batch_msg = f"Saved the batch options as preset {ss.get('bo_preset_name')!r}."
    except J.ProjectError as e:
        ss._batch_msg = f"Preset not saved: {e}"


def _batch_options_form():
    with st.expander("Batch method options (one set for every row; blank = the rule's choice)"):
        names = _preset_names(_root())
        if names:
            st.selectbox("Start from a project preset", [_NO_PRESET] + names, key="bo_preset_pick", on_change=_cb_batch_use_preset)
        c1, c2, c3 = st.columns(3)
        c1.text_input("delta functional", key="bo_delta_functional", placeholder="from rule")
        c2.text_input("DeltaEQ functional", key="bo_deq_functional", placeholder="from rule")
        c3.text_input("Cross-check functional", key="bo_crosscheck_functional", placeholder="from rule ('none' = off)")
        g1, g2, g3 = st.columns(3)
        g1.text_input("Geometry functional/basis", key="bo_geometry", placeholder="from rule (e.g. MN15/def2-SVP)")
        g2.checkbox("Frequency job", key="bo_freq")
        g3.selectbox("Calibration strategy", ["auto", "lookup", "compute_refset"], key="bo_calibration_strategy")
        r1, r2, r3, r4 = st.columns(4)
        r1.number_input("nprocs", min_value=1, step=1, value=None, key="bo_nprocs", placeholder="16")
        r2.checkbox("Add highfield_sp (A, g, ZFS)", key="bo_highfield")
        r3.checkbox("Allow UNVERIFIED calibration", key="bo_allow_unverified_calibration")
        r4.checkbox("Allow protocol (geometry) mismatch", key="bo_allow_protocol_mismatch")
        st.caption("Broken symmetry is per structure: add it afterwards on **States** for the entries that need it.")
        n1, n2, n3, n4 = st.columns([2, 3, 1, 1], vertical_alignment="bottom")
        n1.text_input("Save these options as preset", key="bo_preset_name", placeholder="preset name")
        n2.text_input("Description", key="bo_preset_desc", placeholder="optional")
        n3.checkbox("Replace", key="bo_preset_replace")
        n4.button("Save preset", key="bo_preset_save", on_click=_cb_batch_save_preset)
    opts = _batch_options()
    st.caption("Batch options: " + (f"`{json.dumps(opts, sort_keys=True)}`" if opts else "all from the rules"))
    return opts


def _batch_fingerprint(rows: list[dict], options: dict) -> str:
    pin = dict(rows=rows, options=options, recorded=ss.get("_batch_recorded"), refs=list(ss._batch_src.structures))
    return hashlib.sha256(json.dumps(pin, sort_keys=True, default=str).encode()).hexdigest()


def _batch_table(rows: list[dict]):
    cnt = Counter(r["outcome"] for r in rows)
    st.markdown(" ".join(_pill(f"{n} {k}", BATCH_BADGE.get(k, k)) for k, n in cnt.items()), unsafe_allow_html=True)
    st.dataframe(pd.DataFrame([dict(row=r["index"], compound=r["compound"], structure=r["structure"], state=r["state"] or "",
                                    outcome=r["outcome"], reason=r["reason"] or "", entries=", ".join(r["entries"]),
                                    atoms=r["stats"].get("n_atoms"), Fe=r["stats"].get("n_fe"), formula=r["stats"].get("formula"),
                                    core_hours=r["stats"].get("core_hours"), open_items="; ".join(map(str, r["open_items"])),
                                    warnings="; ".join(r["warnings"] + r["notes"])) for r in rows]),
                 hide_index=True, width="stretch")


def _cb_batch_create(root: str, pending: dict):
    """Create exactly what the shown preview pinned (its rows, options and hash); the scan is refreshed afterwards."""
    try:
        doc = B.create(root, pending["rows"], ss._batch_src, pending["options"], expected_hash=pending["doc"]["preview_hash"],
                       recorded_options=pending["recorded"])
        ss._batch_report = dict(root=root, doc=doc, error=None)
    except B.BatchError as e:
        ss._batch_report = dict(root=root, doc=dict(rows=e.rows, created=e.created, families=[]), error=str(e))
    except J.ProjectError as e:
        ss._batch_msg = f"Nothing created: {e}"
    ss._batch_preview = None
    _invalidate()


def _batch_report_view(rep: dict):
    doc = rep["doc"]
    created = doc["created"]
    if rep["error"]:
        st.error(rep["error"] + (f". Created and kept: {', '.join(created)}." if created else ""))
    st.success(f"Created {len(created)} draft entr{'y' if len(created) == 1 else 'ies'}, not approved: each plan still needs "
               "its own approval on the **Project** page (select the rows in the overview, then seal).")
    _batch_table(doc["rows"])
    for f in doc["families"]:
        st.markdown(f"- State family **{html.escape(str(f['family'] or ', '.join(f['families'])))}** ({f['status']}): "
                    + ", ".join(f"`{html.escape(e)}`" for e in f["entries"]))
    st.caption("Rows that were not created stay in the grid: fix them and preview again, or download the CSV to continue later.")
    st.button("Go to Project overview", key="batch_goto_project", on_click=_goto, args=("Project",))


def page_batch():
    st.header("Batch intake")
    root = _root()
    _cli(f"mossbauer project batch preview --project {root or 'PROJECT'} --structures FILE [FILE ...] --csv sheet.csv")
    if root is None:
        st.info("Open or create a project on the **Project** page first: batch intake adds draft entries to a project.")
        st.button("Go to Project", key="batch_goto_open", on_click=_goto, args=("Project",))
        return
    _batch_state()
    _batch_fold()
    src = ss._batch_src
    if ss.get("_batch_msg"):
        st.warning(ss.pop("_batch_msg"))

    st.subheader("Structures")
    left, right = st.columns(2, gap="large")
    with left:
        ups = st.file_uploader("Structure files (several at once; multi-block xyz, CIF and SDF are split)", type=UPLOAD_TYPES,
                               accept_multiple_files=True, key="batch_files")
        fresh = []
        for up in ups or []:
            data = up.getvalue()
            h = hashlib.sha256(data).hexdigest()
            if h not in ss._batch_seen:
                ss._batch_seen.add(h)
                fresh.append((up.name, data.decode("utf-8", errors="replace")))
        if fresh:
            _batch_add(fresh)
    with right:
        st.text_area("Paste xyz blocks (one or more, concatenated)", key="batch_paste", height=120)
        st.button("Add pasted structures", key="batch_add_paste", on_click=_cb_batch_paste)
    if src.texts:
        per = Counter(ref.partition("#")[0] for ref in src.structures)
        st.caption("Loaded: " + ", ".join(f"{n} ({per.get(n, 0)})" for n in src.texts))
    for n in src.notes:
        st.caption(n)
    pastes = [n for n in src.texts if n.startswith("pasted-")]
    if pastes:
        cols = st.columns(min(len(pastes), 4))
        for k, n in enumerate(pastes):
            cols[k % len(cols)].download_button(f"Download {n}", src.texts[n], n, mime="text/plain", key=f"batch_dl_{n}",
                                                help="Keep it with the CSV to continue this batch later.")

    st.subheader("Rows")
    st.caption("One row per compound in one electronic state. Duplicate a row for another spin state of the same structure.")
    csv_up = st.file_uploader("Import a batch CSV", type=["csv"], key="batch_csv")
    if csv_up is not None:
        data = csv_up.getvalue()
        h = hashlib.sha256(data).hexdigest()
        if h != ss.get("_batch_csv_seen") and (ss.get("_batch_csv_pending") or {}).get("hash") != h:
            try:
                rows, recorded = B.read_csv(data.decode("utf-8", errors="replace"))
                pending = dict(hash=h, name=csv_up.name, rows=rows, options=recorded)
                if ss.batch_rows:
                    ss._batch_csv_pending = pending
                else:
                    _batch_import(pending)
            except J.ProjectError as e:
                st.error(f"Could not import {csv_up.name}: {e}")
    pending = ss.get("_batch_csv_pending")
    if pending:
        n = len(ss.batch_rows)
        st.warning(f"Importing {pending['name']} replaces the {n} current rows with its {len(pending['rows'])} rows.")
        b1, b2 = st.columns(2)
        b1.button(f"Replace {n} rows", key="batch_csv_replace", type="primary", on_click=_cb_batch_import, args=(pending,))
        b2.button("Keep current rows", key="batch_csv_keep", on_click=_cb_batch_csv_keep, args=(pending["hash"],))
    rows = ss.batch_rows
    if not rows:
        st.info("Add structure files or pasted xyz above, or import a batch CSV.")
        return
    missing = sorted({str(r.get("structure")) for r in rows if r.get("structure") not in src.structures})
    if missing:
        st.warning(f"Structure not loaded for: {', '.join(missing)}. Add those files (for pastes, the downloaded pasted-N.xyz) "
                   "and the rows find them by reference.")
    tri = st.column_config.SelectboxColumn(options=["", "yes", "no"], help="yes, no, or blank (not given)")
    st.data_editor(_batch_frame(_batch_base(), src), key=_batch_key(), num_rows="fixed", width="stretch", disabled=["structure"],
                   column_config={"structure": st.column_config.TextColumn("structure", help="source#n; fixed when the compound is renamed"),
                                  "compound": st.column_config.TextColumn("compound", help="unnamed-... is a placeholder to replace"),
                                  "charge": st.column_config.NumberColumn("charge", step=1, format="%d"),
                                  "multiplicity": st.column_config.NumberColumn("multiplicity", min_value=1, step=1, format="%d"),
                                  "oxidation_state": st.column_config.NumberColumn("Fe ox. state", min_value=0, max_value=6, step=1, format="%d"),
                                  "radical_ligand": tri, "nitrosyl": tri,
                                  "solvent": st.column_config.TextColumn("solvent", help="CPCM solvent; blank = gas phase"),
                                  "temperature_K": st.column_config.NumberColumn("T (K)", min_value=0.0, format="%.1f"),
                                  "exp_delta": st.column_config.NumberColumn("exp delta (mm/s)", format="%.3f"),
                                  "exp_deq": st.column_config.NumberColumn("exp |DeltaEQ| (mm/s)", format="%.3f")})
    flagged = [(k, _batch_status(r, src)) for k, r in enumerate(rows)]
    flagged = [(k, t) for k, t in flagged if t]
    if flagged:
        st.caption("Rows to fix before they can be created: " + "; ".join(f"row {k} ({rows[k].get('compound')}): {t}" for k, t in flagged))
    r1, r2, r3 = st.columns([3, 1, 1], vertical_alignment="bottom")
    i = r1.selectbox("Row", range(len(rows)), key="batch_row_pick",
                     format_func=lambda k: f"{k}: {rows[k].get('compound') or ''} ({rows[k].get('structure')})")
    r2.button("Duplicate row", key="batch_dup", on_click=_cb_batch_dup, args=(i or 0,), width="stretch")
    r3.button("Remove row", key="batch_rm", on_click=_cb_batch_remove, args=(i or 0,), width="stretch")

    options = _batch_options_form()
    st.download_button("Download CSV (rows and options)", B.write_csv(rows, options), "batch.csv", mime="text/csv", key="batch_dl_csv")

    st.subheader("Preview and create")
    fp = _batch_fingerprint(rows, options)
    if st.button("Preview batch", key="batch_preview"):
        try:
            recorded = ss.get("_batch_recorded")
            ss._batch_preview = dict(root=str(root), fp=fp, rows=copy.deepcopy(rows), options=dict(options), recorded=recorded,
                                     doc=B.preview(root, rows, src, options, recorded))
            ss._batch_report = None
        except J.ProjectError as e:
            st.error(str(e))
    rep, pv = ss.get("_batch_report"), ss.get("_batch_preview")
    if rep and rep["root"] == str(root):
        _batch_report_view(rep)
    elif pv and pv["root"] == str(root):
        doc = pv["doc"]
        for w in doc["warnings"]:
            st.warning(w)
        _batch_table(doc["rows"])
        n = doc["counts"]["ready"] + doc["counts"]["ready-open-items"]
        if pv["fp"] != fp:
            st.warning("This preview is out of date: rows, structures or options changed since. Preview again to create.")
        elif n:
            st.button(f"Create {n} draft entr{'y' if n == 1 else 'ies'}", key="batch_create", type="primary",
                      on_click=_cb_batch_create, args=(str(root), pv))
            st.caption("Creates a draft entry for each ready row; skipped and blocked rows are not written. Nothing is approved.")
        else:
            st.caption("No ready rows to create.")

