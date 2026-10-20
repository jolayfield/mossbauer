"""mossbauer-dft Streamlit app.  Run:  mossbauer gui   (or: streamlit run app.py -- --workdir PROJECT_DIR)

Project-first: the Project page opens or creates a project folder and shows the status overview; the other pages work on
the selected entry version.  All actions call the library (project, plan, workflow, references); equivalent CLI commands
are shown on each page.  Project path, entry and version are mirrored into the URL so a reload returns to the same place.
"""
from __future__ import annotations
import html

import streamlit as st

from mossbauer_dft.gui.state import PAGES, STEPS, _apply_selection, _current, _evaluate, _guide_now, _initial_path, _root, ss
from mossbauer_dft.gui.theme import CSS
from mossbauer_dft.gui.views.batch import page_batch
from mossbauer_dft.gui.views.calibration import page_calibration
from mossbauer_dft.gui.views.cluster import page_cluster
from mossbauer_dft.gui.views.inputs import page_inputs
from mossbauer_dft.gui.views.new import page_new
from mossbauer_dft.gui.views.project import page_project
from mossbauer_dft.gui.views.refs import page_refs
from mossbauer_dft.gui.views.results import page_results
from mossbauer_dft.gui.views.review import page_review
from mossbauer_dft.gui.views.states import page_states
from mossbauer_dft.gui.views.study import page_study
from mossbauer_dft.gui.widgets import _badges


def _sidebar():
    root, cur = _root(), _current()
    st.sidebar.title("mossbauer-dft")
    st.sidebar.markdown("<p class='mb-sub'><sup>57</sup>Fe Mossbauer parameters from DFT (ORCA)</p>", unsafe_allow_html=True)
    vs = cur.vs if cur else None
    done = {"Project": root is not None, "New prediction": cur is not None, "Plan review": bool(vs and vs.approved),
            "Inputs": bool(vs and vs.status in ("inputs written", "outputs found", "analyzed")), "Results": bool(vs and vs.has_results)}

    g = _guide_now()
    waiting = {}
    for step in (g["steps"] if g else []):
        if step["id"] != "add":
            waiting[step["page_keys"][0]] = waiting.get(step["page_keys"][0], 0) + step["n"]

    def fmt(p):
        # the check is the current entry's progress; the count is how many entries in the project wait at this step
        return STEPS[p] + ("  ✓" if done.get(p) else "") + (f"  · {waiting[p]} waiting" if waiting.get(p) else "")
    page = st.sidebar.radio("Workflow", PAGES, key="page", format_func=fmt)
    with st.sidebar.container(border=True):
        if root is None:
            st.markdown("<span class='mb-badge mb-none'>no project</span>", unsafe_allow_html=True)
            st.caption("Open or create a project on the Project page.")
        elif cur is None:
            st.markdown(f"<p class='mb-kv'>project <b>{html.escape(root.name)}</b></p>", unsafe_allow_html=True)
            st.caption("Select an entry in the overview, or create one on step 1.")
        else:
            st.markdown(f"{_badges(cur.vs, cur.entry.older_results, not cur.newest)}", unsafe_allow_html=True)
            st.markdown(f"**{html.escape(cur.entry.display_name or cur.entry.entry_id)}** &nbsp; v{cur.vs.version}"
                        + ("" if cur.newest else " (older version)"), unsafe_allow_html=True)
            kv = f"<p class='mb-kv'>project <b>{html.escape(root.name)}</b></p><p class='mb-kv'>entry <b>{html.escape(cur.entry.entry_id)}</b></p>"
            if cur.plan:
                inp, ev = cur.plan["inputs"], cur.plan["rule_evaluation"]
                kv += (f"<p class='mb-kv'>charge <b>{inp['charge']:+d}</b> &nbsp; mult <b>{inp['multiplicity']}</b></p>"
                       f"<p class='mb-kv'>rule <b>{html.escape(str(ev['selected_rule']))}</b></p>"
                       f"<p class='mb-kv'>cost <b>~{cur.plan['cost']['total_core_hours']} core-h</b></p>")
            if cur.vs.approval_line:
                kv += f"<p class='mb-kv'>{html.escape(cur.vs.approval_line)}</p>"
            st.markdown(kv, unsafe_allow_html=True)
    if root is not None:
        with st.sidebar.expander("Project folder"):
            st.code(str(root), language=None, wrap_lines=True)
            if cur:
                st.code(str(cur.vs.path.relative_to(root)), language=None, wrap_lines=True)
    return page


def _boot():
    """First run of a browser session: restore project / entry / version / page from the URL, else open --workdir /
    MOSSBAUER_GUI_WORKDIR.  Runs before any session-state default is set."""
    if ss.get("_booted"):
        return
    ss._booted = True
    qp = st.query_params
    path = qp.get("project") or _initial_path()
    if path:
        ss.proj_path = path
        v = qp.get("version")
        _evaluate(path, qp.get("entry"), int(v) if v and v.isdigit() else None)
    if qp.get("page") in PAGES:
        ss.page = qp.get("page")


def _sync_query_params():
    """Mirror the current place into the URL so a reload returns to it."""
    want = dict(project=ss.get("project"), entry=ss.get("entry") if ss.get("project") else None,
                version=str(ss.version) if ss.get("project") and ss.get("entry") and ss.get("version") else None, page=ss.get("page"))
    qp = st.query_params
    for k, v in want.items():
        if v and qp.get(k) != v:
            qp[k] = v
        elif not v and k in qp:
            del qp[k]


def main():
    st.set_page_config(page_title="mossbauer-dft", page_icon=":material/science:", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    _boot()
    _apply_selection()
    page = _sidebar()
    {"Project": page_project, "New prediction": page_new, "Batch intake": page_batch, "Plan review": page_review, "Inputs": page_inputs, "Results": page_results,
     "Study": page_study, "Calibration": page_calibration, "States": page_states, "References": page_refs, "Cluster setup": page_cluster}[page]()
    _sync_query_params()


main()

