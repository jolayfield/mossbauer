"""Calibration workbench page: project reference sets, fits and their residuals."""
from __future__ import annotations
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from mossbauer_dft import project as J
from mossbauer_dft import references as R
from mossbauer_dft.gui.state import ss
from mossbauer_dft.gui.theme import REF_LINE_COLOR
from mossbauer_dft.gui.widgets import _cli, _need_entry, _style_fig


def page_calibration():
    from mossbauer_dft import calibration_workspace as C
    st.header('Calibration workbench')
    cur = _need_entry()
    if cur is None:
        return
    root = cur.root
    st.caption('References and fits stay in this project. Nothing submits cluster jobs or promotes a fit to verified.')
    with st.form('new_calibration_workspace'):
        name = st.text_input('New calibration workspace', value='references')
        create = st.form_submit_button('Create calibration workspace')
    if create:
        try:
            C.create(root, name, cur.entry.entry_id, cur.vs.version)
            st.success('Workspace created. Add references or import the bundled set.')
        except (OSError, ValueError) as ex:
            st.error(str(ex))
    names = C.workspaces(root)
    if not names:
        return
    name = st.selectbox('Calibration workspace', names)
    folder = C.path(root, name)
    doc = C.status(root, name)
    _cli(f'mossbauer project calibration status --project {root} --workspace {name}')
    if doc['references']:
        st.dataframe(pd.DataFrame(doc['references']), hide_index=True, use_container_width=True)
    if not doc['prepared']:
        if st.button('Import bundled reference compounds'):
            try:
                result = C.import_bundled(root, name)
                st.success(f"Imported {len(result['imported'])} references; {len(result['skipped'])} were not ready.")
                st.rerun()
            except (OSError, ValueError, R.ReferenceError_) as ex:
                st.error(str(ex))
        with st.form('add_calibration_reference'):
            rid = st.text_input('Reference id')
            upload = st.file_uploader('Reference structure (XYZ or CIF)', type=['xyz', 'cif'])
            a, b, c = st.columns(3)
            charge = a.number_input('Reference charge', value=-1, step=1)
            mult = b.number_input('Reference multiplicity', value=4, min_value=1, step=1)
            delta = c.number_input('Reference experimental delta (mm/s)', value=0.0)
            deq = st.number_input('Reference experimental |DeltaEQ| (mm/s; optional)', value=None)
            add = st.form_submit_button('Add reference compound')
        if add:
            if upload is None:
                st.error('Upload a reference structure first.')
            else:
                import tempfile
                try:
                    with tempfile.TemporaryDirectory() as temp:
                        source = Path(temp) / ('reference.' + upload.name.rsplit('.', 1)[-1])
                        source.write_bytes(upload.getvalue())
                        C.add_reference(root, name, rid, source, int(charge), int(mult), delta, deq)
                    st.rerun()
                except (OSError, ValueError) as ex:
                    st.error(str(ex))
        if doc['references']:
            st.button('Use workspace references in a plan preview', on_click=_cb_reference_preview,
                      args=(str(root), name, cur.entry.entry_id))
        if st.button('Prepare reference inputs', disabled=not cur.vs.approved or not doc['references']):
            try:
                C.prepare(root, name, cur.entry.entry_id, cur.vs.version)
                st.rerun()
            except (OSError, ValueError, R.ReferenceError_) as ex:
                st.error(str(ex))
        if not cur.vs.approved:
            st.info('Approve the target plan before preparing reference inputs; reference data can be entered first.')
    else:
        st.info(f"Run inputs/submit_refset.sh externally, then place <reference>_sp.out in {folder / 'outputs'}.")
        with st.form('fit_project_calibration'):
            reasons = st.text_area('Exclusions and reasons (JSON)', value='{}')
            fit = st.form_submit_button('Fit and inspect calibration')
        if fit:
            try:
                result = C.fit(root, name, json.loads(reasons))
                st.success('Fit saved as an unverified project calibration. Select it explicitly on Results.')
                st.json(result)
            except (OSError, ValueError, R.ReferenceError_) as ex:
                st.error(str(ex))
    for fit_path in C.status(root, name)['fits']:
        with st.expander(f'Inspect fit {Path(fit_path).stem}'):
            entry = J._read_yaml(root / fit_path)
            st.caption(f"Select {fit_path} on Results to use this exact snapshot.")
            st.json(entry)
            points = (entry.get('calibration') or {}).get('per_point') or []
            if points:
                _residual_plot(points, key=f"resid_{Path(fit_path).stem}")
                st.dataframe(pd.DataFrame(points), hide_index=True, use_container_width=True)
                _drop_one(entry, key=f"drop_{Path(fit_path).stem}")


def _residual_plot(points, key):
    """Residual and leave-one-out error per reference against ρ(0); flagged points are labelled."""
    import plotly.graph_objects as go
    df = pd.DataFrame(points)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["rho0"], y=df["residual"], mode="markers", name="residual", marker=dict(size=9),
                             text=df["name"], hovertemplate="%{text}<br>rho0 %{x:.3f}<br>residual %{y:.3f}<extra></extra>"))
    if "loo_error" in df and df["loo_error"].notna().any():
        fig.add_trace(go.Scatter(x=df["rho0"], y=df["loo_error"], mode="markers", name="leave-one-out error",
                                 marker=dict(size=8, symbol="x"), text=df["name"]))
    flagged = df[df["flags"].map(bool)] if "flags" in df else df.iloc[0:0]
    for _, r in flagged.iterrows():
        fig.add_annotation(x=r["rho0"], y=r["residual"], text=f"{r['name']}: {', '.join(r['flags'])}", showarrow=True, arrowhead=1)
    fig.add_hline(y=0, line=dict(dash="dash", color=REF_LINE_COLOR))
    st.plotly_chart(_style_fig(fig, "rho(0) (a.u.)", "delta exp − fit (mm/s)"), use_container_width=True, key=key)


def _drop_one(entry: dict, key: str):
    """Preview refitting without one reference; saving it means a new fit with a written exclusion reason."""
    from mossbauer_dft import calcontext
    from mossbauer_dft.calibration import Calibration
    cal = Calibration.from_dict(entry["calibration"])
    if len(cal.names) < 4:
        return
    name = st.selectbox("Preview a refit without", cal.names, key=f"{key}_pick")
    try:
        d = calcontext.drop_one(cal, name)
    except ValueError as ex:
        st.caption(str(ex))
        return
    st.dataframe(pd.DataFrame([dict(fit="current", **d["before"]), dict(fit=f"without {name}", **d["after"])]),
                 hide_index=True, use_container_width=True, key=f"{key}_table")
    st.caption("Preview only. To keep it, fit again above with this reference in the exclusions and a written reason; "
               "the result is a new, unverified project fit and the current one is unchanged.")


def _cb_reference_preview(root, name, entry):
    from mossbauer_dft import calibration_workspace as C, revision
    proposal = revision.preview(root, entry, overrides=dict(user_refs=C.plan_references(root, name), calibration_strategy='user_refs'))
    ss._edit_preview = dict(root=str(root), proposal=proposal)
    ss.page = 'Plan review'

