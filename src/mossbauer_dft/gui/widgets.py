"""Rendering helpers shared by several pages: badges, CLI hints, refresh controls, plot styling."""
from __future__ import annotations
import html

import streamlit as st

from mossbauer_dft import presets as PR
from mossbauer_dft import project as J
from mossbauer_dft.gui.state import Current, _cb_open_entry, _current, _goto, _invalidate, _root, _waiting, ss
from mossbauer_dft.gui.theme import GRID_COLOR, PLOT_COLORS, badge_class

UPLOAD_TYPES = ["xyz", "cif", "mol", "sdf", "pdb"]


def _need_entry() -> Current | None:
    cur = _current()
    if cur is not None:
        return cur
    if _root() is None:
        st.info("Open or create a project on the **Project** page first.")
        st.button("Go to Project", key="goto_project", on_click=_goto, args=("Project",))
        return None
    waiting = _waiting(ss.get("page", ""))
    if waiting:
        st.info(f"{len(waiting)} entr{'y is' if len(waiting) == 1 else 'ies are'} waiting for this step. Open one:")
        for i, (title, eid) in enumerate(waiting[:30]):
            st.button(f"{eid}  ·  {title.lower()}", key=f"open_waiting_{i}", on_click=_cb_open_entry, args=(eid, ss.get("page")))
    else:
        st.info("Select an entry in the overview on the **Project** page, or create one on **1  Structure**.")
    st.button("Go to Project", key="goto_project", on_click=_goto, args=("Project",))
    return None


def _cli(text: str):
    st.markdown(f"<p class='mb-cli'>CLI equivalent: <code>{html.escape(text)}</code></p>", unsafe_allow_html=True)


def _pill(text: str, cls: str) -> str:
    return f"<span class='mb-badge {badge_class(cls)}'>{html.escape(text)}</span>"


def _badges(vs: J.VersionStatus | None, older_results: bool = False, older_version: bool = False) -> str:
    """Status badge (with found-of-expected for outputs found) plus one badge per flag."""
    if vs is None:
        return "<span class='mb-badge mb-none'>no plan</span>"
    out = [_pill(vs.label, vs.status or "unreadable")]
    out += [_pill(f, f) for f in vs.flags if not (f == "unreadable" and vs.status is None)]
    if older_results:
        out.append(_pill("older results", "older results"))
    if older_version:
        out.append(_pill("older version", "older version"))
    return " ".join(out)


def _header(cur: Current):
    inp = cur.plan["inputs"] if cur.plan else {}
    st.markdown(f"{_badges(cur.vs, cur.entry.older_results, not cur.newest)} &nbsp; **{html.escape(cur.entry.display_name or cur.entry.entry_id)}** "
                f"&nbsp; `{html.escape(cur.entry.entry_id)}` v{cur.vs.version}"
                + (f" &nbsp; charge {inp['charge']:+d}, multiplicity {inp['multiplicity']}" if inp else ""), unsafe_allow_html=True)


def _refresh(key: str):
    st.button("Refresh status", key=key, on_click=_invalidate, help="Rescan the project folder (outputs added outside the app appear).")


def _scanned_caption():
    c = ss.get("_scan")
    if c:
        st.caption(f"scanned at {c['at'].strftime('%H:%M')}")


def _preset_names(root) -> list[str]:
    """Preset names for a selectbox; a damaged presets.json is reported instead of breaking the page."""
    try:
        return [p["name"] for p in PR.list_presets(root)]
    except Exception as e:  # noqa: BLE001
        st.warning(f"presets.json could not be read ({type(e).__name__}: {e}); presets are unavailable until it is fixed.")
        return []


def _style_fig(fig, x, y):
    fig.update_layout(template="simple_white", colorway=PLOT_COLORS, xaxis_title=x, yaxis_title=y, height=420,
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08, x=0))
    fig.update_xaxes(showgrid=True, gridcolor=GRID_COLOR)
    fig.update_yaxes(showgrid=True, gridcolor=GRID_COLOR)
    return fig

