"""Project page: open / create / import, recent projects, guide, status overview, batch approval, bulk actions."""
from __future__ import annotations
import html
import os
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

from mossbauer_dft import plan as P
from mossbauer_dft import project as J
from mossbauer_dft.gui.state import STEPS, _cb_open_entry, _cb_seal, _evaluate, _fail, _goto, _invalidate, _root, _scan, _set_project, ss
from mossbauer_dft.gui.widgets import _cli, _preset_names, _refresh, _scanned_caption


def _cb_open():
    path = (ss.get("proj_path") or "").strip()
    if path:
        _evaluate(path)
    else:
        _fail("", "Type the path of a project folder.")


def _cb_create(path: str):
    try:
        J.create_project(path, name=(ss.get("new_name") or "").strip() or None)
        _set_project(Path(path))
    except Exception as e:  # noqa: BLE001
        _fail(path, f"Could not create a project in {path}: {type(e).__name__}: {e}")


def _cb_example(path: str):
    from mossbauer_dft import tutorial as T
    try:
        refs = T.create_example(path)
        _set_project(Path(path), refs[0].entry_id, refs[0].version)
    except Exception as e:  # noqa: BLE001
        _fail(path, f"Could not create the guided example in {path}: {type(e).__name__}: {e}")


def _cb_import(path: str):
    try:
        ref = J.import_legacy(path)
        _set_project(Path(path), ref.entry_id, ref.version)
    except Exception as e:  # noqa: BLE001
        _fail(path, f"Import failed (originals untouched): {type(e).__name__}: {e}")


def _overview_row(e: J.EntryStatus) -> dict:
    vs = e.newest
    row = dict(entry=e.entry_id, compound=e.display_name or e.entry_id, state=e.state_label or "", version="", status="unreadable",
               outputs="", problems="", flags="unreadable", approval="")
    if vs is None:
        row["problems"] = e.error or "no versions"
        return row
    cnt = Counter(j.state for j in vs.jobs)
    probs = [f"{cnt[k]} {k}" for k in ("incomplete", "wrong-version", "unverified") if cnt[k]]
    if vs.unmatched:
        probs.append(f"{len(vs.unmatched)} unmatched")
    if vs.hints:
        probs.append(f"{len(vs.hints)} in inputs/ (move to outputs/)")
    if vs.error:
        probs.append(vs.error)
    flags = list(vs.flags) + (["unreadable"] if e.error and "unreadable" not in vs.flags else []) + (["older results"] if e.older_results else [])
    line = vs.approval_line
    row.update(version=f"v{vs.version}", status=vs.label, outputs=f"{vs.found} of {len(vs.expected)}" if vs.status else "",
               problems=", ".join(probs), flags=", ".join(flags), approval=line[len("approved "):] if line else "")
    return row


def _approval_preview(root: Path, ents: dict, ids: list[str]) -> list[dict]:
    """Per selected entry (newest version on disk now): eligible, blocked (with reason) or already approved -- before
    anything is sealed.  An eligible row carries the version and plan hash shown, so the seal applies to exactly that."""
    rows = []
    for eid in ids:
        e = ents[eid]
        row = dict(entry=eid, compound=e.display_name or eid, version="", result="blocked", reason="")
        try:
            vis = J.list_versions(root, eid)
            b = J.approval_blocker(vis[-1]) if vis else J.ApprovalOutcome(eid, None, "skipped", f"unreadable: {e.error or 'no versions'}")
        except Exception as ex:  # noqa: BLE001
            vis, b = [], J.ApprovalOutcome(eid, None, "skipped", f"unreadable: {type(ex).__name__}: {ex}")
        if vis:
            row["version"] = f"v{vis[-1].version}"
        if b is None:
            row.update(result="eligible", _ref=J.VersionRef(eid, vis[-1].version, vis[-1].path), _hash=P.compute_hash(vis[-1].plan))
        elif b.outcome == "already-approved":
            row.update(result="already approved", reason=J.approval_line(b.approval) or "")
        else:
            row["reason"] = b.reason or ""
        rows.append(row)
    return rows


def _cb_browse_folder(path):
    ss._browse_folder = str(path)


def _cb_start_folder_browser():
    current = Path(ss.get("proj_path") or ss.get("project") or Path.home()).expanduser()
    while not current.is_dir() and current != current.parent:
        current = current.parent
    ss._browse_folder = str(current.resolve())
    ss._folder_browser_open = True


def _folder_browser():
    st.subheader("Choose a project folder")
    current = Path(ss._browse_folder)
    st.caption("Browse folders on the computer running this app. Existing projects open immediately; empty folders need confirmation before creating a project.")
    st.code(str(current), language=None, wrap_lines=True)
    st.button("Parent folder", disabled=current == current.parent, on_click=_cb_browse_folder,
              args=(current.parent,), key="browse_parent")
    hidden = st.checkbox("Show hidden folders", key="browse_hidden")
    try:
        children = sorted((p.name for p in current.iterdir() if (hidden or not p.name.startswith('.')) and p.is_dir()),
                          key=str.casefold)
    except OSError as ex:
        st.error(f"Cannot list this folder: {ex}")
        children = []
    if children:
        child = st.selectbox("Subfolder", children, index=None, placeholder="Choose a subfolder",
                             key=f"browse_child_{current}")
        st.button("Open subfolder", disabled=child is None, on_click=_cb_browse_folder,
                  args=(current / child if child else current,), key="browse_descend")
    else:
        st.caption("No visible subfolders.")
    choose, cancel = st.columns(2)
    if choose.button("Use this folder", type="primary", use_container_width=True):
        ss._picked_project_folder = str(current)
        ss._folder_browser_open = False
        st.rerun()
    if cancel.button("Cancel", use_container_width=True):
        ss._folder_browser_open = False
        st.rerun()


def _open_controls():
    selected = ss.pop("_picked_project_folder", None)
    if selected is not None:
        ss.proj_path = selected
        _evaluate(selected)
    st.text_input("Project folder", key="proj_path", placeholder="/path/to/my-study",
                  help="Any folder you can write to. An empty or new folder can become a project; an existing project opens.")
    c1, c2 = st.columns(2)
    c1.button("Browse folders…", key="browse_proj", on_click=_cb_start_folder_browser, use_container_width=True)
    c2.button("Open", key="open_proj", on_click=_cb_open, use_container_width=True)
    if ss.get("_folder_browser_open"):
        with st.container(border=True):
            _folder_browser()
    t = ss.get("_target")
    if t:
        kind, path = t["kind"], t["path"]
        if kind in ("error", "refused"):
            st.error(t["msg"])
        elif kind in ("new", "empty"):
            (st.error if kind == "new" else st.info)(t["msg"])
            st.text_input("Project name (optional)", key="new_name", placeholder=Path(path).name)
            st.button("Create folder and project here" if kind == "new" else "Create project here", key="create_proj", type="primary",
                      on_click=_cb_create, args=(path,))
            st.button("Create the guided example here", key="create_example", on_click=_cb_example, args=(path,),
                      help="A teaching project of three draft entries (ferrocene, [FeCl4]2-, [FeCl4]-) with literature structures "
                           "and experimental values from the bundled reference set. You review, approve and run them yourself.")
        elif kind == "legacy":
            st.warning(t["msg"])
            st.button("Import as project — originals are kept", key="import_proj", type="primary", on_click=_cb_import, args=(path,))
    recents = J.recent_projects()
    if recents:
        with st.expander(f"Recent projects ({len(recents)})", expanded=_root() is None):
            for i, r in enumerate(recents[:12]):
                a, b, c = st.columns([6, 1, 1.4], vertical_alignment="center")
                a.markdown(f"<p class='mb-kv'><b>{html.escape(Path(r['path']).name)}</b> &nbsp; <code>{html.escape(r['path'])}</code>"
                           + ("" if r["exists"] else " &nbsp; folder not found") + "</p>", unsafe_allow_html=True)
                b.button("Open", key=f"recent_open_{i}", disabled=not r["exists"], on_click=_evaluate, args=(r["path"],))
                if not r["exists"]:
                    c.button("Remove from list", key=f"recent_rm_{i}", on_click=J.remove_recent, args=(r["path"],))


GUIDE_MARK = {"done": "✓", "next": "→", "later": "·"}


def _guide(root, meta: dict, ents):
    """The next-step guide: derived from the scan the overview already has; writes nothing."""
    from mossbauer_dft import tutorial as T
    g = T.guide(root, ents)
    nxt = next((s for s in g["steps"] if s["status"] == "next"), None)
    learning = bool(meta.get("example")) or not any(s["id"] == "compare" and s["n"] for s in g["steps"])
    with st.expander(f"Guide: next, {nxt['title'].lower()}" if nxt else "Guide: resolve unreadable entries", expanded=learning):
        if meta.get("example") and (root / "EXAMPLE.md").is_file():
            st.caption("Guided example: the compounds, literature values and caveats are in `EXAMPLE.md` in the project folder.")
        for s in g["steps"]:
            who = f" ({s['n']} entr{'y' if s['n'] == 1 else 'ies'})" if s["n"] else ""
            line = f"{GUIDE_MARK[s['status']]} **{html.escape(s['title'])}**{who} &nbsp; *{html.escape(s['page'])}*"
            st.markdown(line if s["status"] != "next" else f"{line} &nbsp; **← next**")
            if s["status"] == "next":
                st.caption(s["why"])
                if s["entries"]:
                    st.button(f"Do this next: open {s['entries'][0]} on {STEPS[s['page_keys'][0]].strip()}", key="guide_next",
                              type="primary", on_click=_cb_open_entry, args=(s["entries"][0], s["page_keys"][0]))
                elif s["id"] == "add":
                    st.button("Do this next: add an entry", key="guide_next", type="primary", on_click=_goto, args=("New prediction",))
                _cli(s["cli"])
        for n in g["notes"]:
            st.warning(n)
        st.caption(f"Same guide for agents and scripts: `mossbauer project guide --project {root}`")


def page_project():
    st.header("Project")
    _open_controls()
    root = _root()
    if root is None:
        return
    meta = J.open_project(root)
    st.subheader(str(meta.get("name") or root.name))
    _cli(f"mossbauer project status --project {root}")
    r1, r2 = st.columns([1, 5], vertical_alignment="center")
    with r1:
        _refresh("refresh")
    ents = _scan()
    with r2:
        _scanned_caption()
    _guide(root, meta, ents)
    if not ents:
        st.info("No entries yet: add one from a structure on **1  Structure**, or many at once on **Batch intake**.")
        return
    rows = [_overview_row(e) for e in ents]
    ss._overview_ids = [r["entry"] for r in rows]
    ev = st.dataframe(pd.DataFrame(rows), key="overview", on_select="rerun", selection_mode="multi-row", hide_index=True,
                      use_container_width=True)
    st.caption("Click rows to select entries: the last clicked becomes the current entry (newest version) for the other pages. "
               "Per-file details are on **4  Results**.")
    ids = [ss._overview_ids[i] for i in ev.selection.rows if i < len(ss._overview_ids)]
    if not ids:
        return
    with st.container(border=True):
        st.markdown(f"#### Approve {len(ids)} selected")
        preview = _approval_preview(root, {e.entry_id: e for e in ents}, ids)
        st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in preview]),
                     key="approve_preview", hide_index=True, use_container_width=True)
        eligible = [r for r in preview if r["result"] == "eligible"]
        n = len(eligible)
        a1, a2 = st.columns([2, 2], vertical_alignment="bottom")
        a1.text_input("Approver name", value=os.environ.get("USER", ""), key="approver")
        who = (ss.get("approver") or "").strip()
        a2.button(f"Seal {n} plan{'s' if n != 1 else ''} as {who or '...'}", key="seal", type="primary", disabled=not (n and who),
                  on_click=_cb_seal, args=(eligible,), use_container_width=True)
        st.caption("Each plan is sealed individually with its own hash, your name, the time and `via: gui`. Blocked plans are not changed.")
        _cli(f"mossbauer project approve --project {root} --entry ID [--entry ID ...] --by NAME")
        for r in ss.get("_approve_results") or []:
            st.markdown(f"- `{html.escape(r['entry'])}` v{r['version']}: **{r['outcome']}**"
                        + (f" ({html.escape(r['detail'])})" if r["detail"] else ""))
    _bulk_panel(root, {e.entry_id: e for e in ents}, ids)


def _cb_bulk(verb: str, ids: list[str]):
    from mossbauer_dft import bulk as B
    ss._bulk_results = (verb, B.run(_root(), verb, ids))
    _invalidate()


def _cb_bulk_preset_preview(ids: list[str]):
    from mossbauer_dft import bulk as B
    name = ss.get("bulk_preset")
    ss._bulk_preview = B.preview_preset(_root(), ids, name) if name else None
    ss._bulk_results = None


def _cb_bulk_preset_apply():
    from mossbauer_dft import bulk as B
    pv = ss.get("_bulk_preview")
    if not pv:
        return
    try:
        outs = B.apply_preset(_root(), pv, expect_pin=pv["pin"]) + pv["skipped"]
    except J.ProjectError as ex:
        outs = [B.outcome("(all)", None, "failed", str(ex))]
    ss._bulk_results, ss._bulk_preview = ("preset", outs), None
    _invalidate()


def _bulk_panel(root, by_id: dict, ids: list[str]):
    """The other per-entry steps for the same selection: write inputs, analyze, re-analyze, apply a preset."""
    from mossbauer_dft import bulk as B
    with st.container(border=True):
        st.markdown(f"#### Act on {len(ids)} selected")
        counts = {v: [e for e in ids if B.eligibility(by_id[e].newest if e in by_id else None, v) is None] for v in B.VERBS}
        labels = dict(setup="Write inputs", analyze="Analyze", reanalyze="Re-analyze saved")
        cols = st.columns(3)
        for col, verb in zip(cols, B.VERBS):
            col.button(f"{labels[verb]} ({len(counts[verb])})", key=f"bulk_{verb}", disabled=not counts[verb],
                       on_click=_cb_bulk, args=(verb, counts[verb]), use_container_width=True)
        st.caption("Each entry's newest version is handled on its own; entries that are not ready are skipped with the reason. "
                   "Re-analyze refreshes calibration status and trust flags of saved analyses.")
        names = _preset_names(root)
        if names:
            p1, p2 = st.columns([3, 2], vertical_alignment="bottom")
            p1.selectbox("Apply a method preset to the selection", names, key="bulk_preset")
            p2.button("Preview preset", key="bulk_preset_preview", on_click=_cb_bulk_preset_preview, args=(ids,),
                      use_container_width=True)
            pv = ss.get("_bulk_preview")
            if pv:
                st.dataframe(pd.DataFrame([dict(entry=p["entry"], version=p["base_version"], creates=p["creates"],
                                                changes=len(p["changes"]), cost=f"{p['cost_before']} → {p['cost_after']} core-h",
                                                blockers=len(p["blockers"])) for p in pv["proposals"]]
                                          + [dict(entry=o["entry"], version=o["version"], creates="—", changes=0, cost="",
                                                  blockers=0, note=o["detail"]) for o in pv["skipped"]]),
                             key="bulk_preset_table", hide_index=True, use_container_width=True)
                st.button(f"Save {len(pv['proposals'])} new draft version(s) with preset {pv['preset']}", key="bulk_preset_apply",
                          type="primary", disabled=not pv["proposals"], on_click=_cb_bulk_preset_apply)
                st.caption("Each changed plan becomes a new draft and needs its own approval.")
        _cli(f"mossbauer project bulk setup|analyze|reanalyze --project {root} --entry ID [--entry ID ...]")
        res = ss.get("_bulk_results")
        if res:
            verb, outs = res
            st.markdown(f"**{labels.get(verb, 'Preset')}:** {B.summary(outs)}")
            st.dataframe(pd.DataFrame(outs), key="bulk_results_table", hide_index=True, use_container_width=True)

