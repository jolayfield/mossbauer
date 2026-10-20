"""Results page (step 4): returned outputs, analysis, calibrated predictions."""
from __future__ import annotations
import html
from pathlib import Path

import pandas as pd
import streamlit as st

from mossbauer_dft import project as J
from mossbauer_dft import workflow as W
from mossbauer_dft.gui.state import Current, _invalidate, _root, ss
from mossbauer_dft.gui.theme import REF_LINE_COLOR
from mossbauer_dft.gui.widgets import _cli, _header, _need_entry, _pill, _refresh, _scanned_caption, _style_fig

FLAG_NOTES = {"partial": "covers fewer than all expected property outputs (some were missing, incomplete or excluded)",
              "stale": "the property outputs in outputs/ changed since this analysis (by name or SHA-256): re-analyze"}


def _cb_adopt(eid: str, n: int):
    try:
        r = J.adopt_outputs(_root(), eid, n)
        ss._desk_message = (f"Adopted {len(r['adopted'])} file(s) into outputs/"
                            + "".join(f"; skipped {x['file']}: {x['reason']}" for x in r["skipped"]))
    except J.ProjectError as ex:
        ss._desk_message = f"Nothing adopted: {ex}"
    _invalidate()


def _cb_resubmit(eid: str, n: int, stems: list[str]):
    res = {}
    t, m = (ss.get("resub_time") or "").strip(), ss.get("resub_mem") or 0
    for stem in stems:
        if t:
            res.setdefault(stem, {})["time"] = t
        if m:
            res.setdefault(stem, {})["mem_gb"] = float(m)
    try:
        r = J.write_resubmit(_root(), eid, n, stems=stems, resources=res)
        ss._desk_message = f"Wrote inputs/resubmit_failed.sh for {', '.join(r['stems'])}. Run it from inputs/ on the cluster."
        ss._resubmit_cmds = r["commands"]
    except (J.ProjectError, OSError, ValueError) as ex:
        ss._desk_message, ss._resubmit_cmds = f"Not written: {ex}", None


def _return_desk(cur: Current):
    """Everything that comes back from the cluster in one place: stray outputs to adopt, failed jobs to resubmit."""
    eid, n, vs = cur.entry.entry_id, cur.vs.version, cur.vs
    stray = [j.stem for j in vs.jobs if j.hint]
    failed = J.failed_jobs(vs)
    unreturned = [j.stem for j in vs.jobs if j.state == "missing" and j.stem not in failed and not j.hint]
    if not (stray or failed or unreturned or ss.get("_desk_message")):
        return
    with st.container(border=True):
        st.markdown("#### Return desk")
        if stray:
            st.info(f"{len(stray)} output(s) are in inputs/, where the job script copies them back: "
                    + ", ".join(f"`{s}`" for s in stray))
            st.button(f"Adopt {len(stray)} output(s) into outputs/", key="adopt", type="primary", on_click=_cb_adopt, args=(eid, n))
            _cli(f"mossbauer project adopt --project {cur.root} --entry {eid} --version {n}")
        if (failed or unreturned) and not J.inputs_written(vs):
            st.caption("Some jobs have no output, but this version has no written inputs to resubmit from "
                       "(its evidence was imported). Write inputs on 3 ORCA inputs to run them yourself.")
        elif failed or unreturned:
            st.markdown(f"**{len(failed)} failed**, {len(unreturned)} not returned (never run, still running, or killed without notice).")
            pick = st.multiselect("Jobs to resubmit (their dependents are added)", failed + unreturned, default=failed, key="resub_pick")
            c1, c2 = st.columns(2)
            c1.text_input("Walltime for these jobs (HH:MM:SS, blank = site default)", key="resub_time")
            c2.number_input("Memory (GB, 0 = site default)", min_value=0, step=8, key="resub_mem")
            st.button("Write resubmit_failed.sh", key="resubmit", disabled=not pick, on_click=_cb_resubmit, args=(eid, n, pick))
            st.caption("The script is written to inputs/ and displayed; the app never submits jobs.")
            _cli(f"mossbauer project resubmit --project {cur.root} --entry {eid} --version {n} [--time STEM=HH:MM:SS] [--mem STEM=GB]")
        if ss.get("_desk_message"):
            st.success(ss.pop("_desk_message"))
        if ss.get("_resubmit_cmds"):
            st.code("\n".join(ss.pop("_resubmit_cmds")), language="bash")


def page_results():
    st.header("Results")
    cur = _need_entry()
    if cur is None:
        return
    _header(cur)
    eid, n, vd, vs = cur.entry.entry_id, cur.vs.version, cur.vs.path, cur.vs
    _cli(f"mossbauer project analyze --project {cur.root} --entry {eid} --version {n} [--calibration ID]")
    from mossbauer_dft import recorded
    rec = recorded.label(cur.root)
    if rec and eid in rec["entries"]:
        with st.container(border=True):
            st.markdown("#### Recorded run")
            st.caption(f"This example comes with a real calculation recorded by {rec['recorded_by']} on {rec['recorded_at'][:10]}"
                       + (f" ({rec['site']})" if rec["site"] else "") + ". It is imported as compact evidence and labelled as such.")
            why = recorded.available(cur.root, eid)
            if recorded.imported(cur.root, eid):
                st.success("The recorded run is imported for this entry; its values below are recorded compact evidence.")
            elif why:
                st.info(f"Not available yet: {why}.")
            elif st.button("Import the recorded run", key="import_recorded", type="primary"):
                try:
                    out = recorded.import_recorded(cur.root, eid)
                    ss._compact_message = f"Imported {len(out['imported'])} job(s). {out['recorded']}"
                    _invalidate(); st.rerun()
                except (J.ProjectError, ValueError) as ex:
                    st.error(f"Nothing imported: {ex}")
            _cli(f"mossbauer project import-recorded --project {cur.root} --entry {eid}")
    with st.expander('Import compact cluster evidence'):
        bundle = st.file_uploader('Compact cluster result bundle (JSON)', type=['json'], key='compact_bundle')
        replace = st.checkbox('Archive and replace conflicting compact evidence', value=False)
        if st.button('Validate and import compact results', disabled=bundle is None):
            import tempfile
            from mossbauer_dft import compact
            try:
                if bundle.size > 5_000_000:
                    raise ValueError('compact bundle exceeds 5 MB')
                with tempfile.TemporaryDirectory() as temp:
                    source = Path(temp) / 'bundle.json'; source.write_bytes(bundle.getvalue())
                    outcome = compact.import_bundle(cur.root, eid, n, source, replace)
                ss._compact_message = f"Imported {len(outcome['imported'])} jobs; {len(outcome['missing'])} missing and {len(outcome['rejected'])} rejected. {outcome['warning']}"
                _invalidate(); st.rerun()
            except (ValueError, OSError, KeyError, TypeError) as ex:
                st.error(f'Nothing imported: {ex}')
    with st.expander('Fetch compact results over SSH'):
        st.caption('Uses SSH configuration on the computer running this app (macOS/Linux), with existing keys and trusted '
                   'host records. Create results.json with harvest.pyz on the cluster first. Each click fetches one file; '
                   'the app does not submit jobs. Transfers stop after 30 seconds or 5 MB.')
        host = st.text_input('SSH host alias or user@hostname', key='return_host')
        remote_path = st.text_input('Absolute remote bundle filename', key='return_path', placeholder='/work/my-run/results.json')
        remote_replace = st.checkbox('Archive and replace differing compact evidence from SSH', key='return_replace')
        _cli(f'mossbauer project fetch-results --project DIR --entry {eid} --version {n} '
             '--host HOST --remote-path /absolute/path/results.json [--replace]')
        if st.button('Fetch, validate and import', key='fetch_results', disabled=not (host.strip() and remote_path.strip())):
            from mossbauer_dft import remote
            try:
                with st.spinner('Fetching and validating compact evidence…'):
                    outcome = remote.fetch_results(cur.root, eid, n, host.strip(), remote_path, replace=remote_replace)
                ss._compact_message = (f"Imported {len(outcome['imported'])} jobs; {len(outcome['unchanged'])} unchanged; "
                                       f"{len(outcome['missing'])} missing and {len(outcome['rejected'])} rejected. {outcome['warning']}")
                _invalidate(); st.rerun()
            except (J.ProjectError, ValueError, OSError, KeyError, TypeError) as ex:
                st.error(f'Nothing imported: {ex}')
    if ss.get('_compact_message'):
        st.info(ss.pop('_compact_message'))
    r1, r2 = st.columns([1, 5], vertical_alignment="center")
    with r1:
        _refresh("refresh_results")
    with r2:
        _scanned_caption()
    if vs.status is None:
        st.error(f"v{n} cannot be read: {vs.error}")
        return
    st.caption(f"Outputs are read from `{vd / 'outputs'}` only: put each job's ORCA output there as `<stem>.out`.")
    st.dataframe(pd.DataFrame([dict(stem=j.stem, kind=j.kind, state=j.state, job=j.run_label, file=j.file or "",
                                    note=j.hint or j.reason) for j in vs.jobs]),
                 key="job_table", hide_index=True, use_container_width=True)
    if vs.unmatched:
        st.warning("In outputs/ but matching no expected job (not used): " + ", ".join(f"`{u}`" for u in vs.unmatched))
    _return_desk(cur)
    try:
        saved = J.load_results(cur.root, eid, n)
    except Exception as e:  # noqa: BLE001
        saved = None
        st.error(f"Saved result cannot be read: {type(e).__name__}: {e}")
    ready = [j for j in vs.jobs if j.kind in J.PROPERTY_KINDS and j.state == "complete"]
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        cal = c1.text_input("Calibration (entry id or YAML path)", key="calib", placeholder="from plan")
        from mossbauer_dft import calibration_workspace as C
        local_fit = st.selectbox('Project calibration fit', ['Use plan or typed calibration'] + C.fits(cur.root), key='result_local_fit')
        if local_fit != 'Use plan or typed calibration':
            cal = str(cur.root / local_fit)
        level = c2.select_slider("Prediction interval", [0.90, 0.95, 0.99], value=0.95, key="level")
        if st.button("Re-analyze" if saved else "Analyze", key="analyze", type="primary", disabled=not ready):
            try:
                with st.spinner("Analyzing ..."):
                    J.analyze_version(cur.root, eid, n, calibration=cal or None, level=level)
                _invalidate()
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"{type(e).__name__}: {e}")
        if not ready:
            st.caption("Analyze becomes available once at least one property output (mossbauer_sp / highfield_sp) is complete.")
    if not saved:
        if ready:
            _trial_panel(cur.root, eid, n)
        return
    flags = [f for f in vs.flags if f in FLAG_NOTES]
    st.markdown(f"Saved result for v{n}, analyzed {html.escape(str(saved.get('analyzed_at')))} &nbsp; "
                + " ".join(_pill(f, f) for f in flags), unsafe_allow_html=True)
    for f in flags:
        st.warning(f"{f}: {FLAG_NOTES[f]}")
    _delta_status(saved)
    _show_result(saved)
    _trial_panel(cur.root, eid, n)


DELTA_STATUS_BADGE = {"calibrated": "approved", "project-fit": "inputs written", "exploratory": "modified", "uncalibrated": "draft",
                      "unknown": "older version"}
DELTA_STATUS_TEXT = {"calibrated": "calibrated prediction", "project-fit": "project-fit prediction",
                     "exploratory": "exploratory: not a calibrated prediction", "uncalibrated": "uncalibrated: rho(0) only",
                     "unknown": "status not recorded (analyzed before calibration status existed; re-analyze to record it)"}


def _delta_status(res: dict):
    ds = res.get("delta_status") or dict(status="unknown", reasons=[])
    status = ds.get("status", "unknown")
    st.markdown(f"Isomer shift: {_pill(status, DELTA_STATUS_BADGE.get(status, 'none'))} "
                f"{html.escape(DELTA_STATUS_TEXT.get(status, status))}", unsafe_allow_html=True)
    if ds.get("reasons"):
        st.caption("; ".join(str(r) for r in ds["reasons"]))


def _trial_panel(root, eid: str, n: int):
    with st.expander("Trial analysis (not saved)"):
        st.caption("Try another calibration or prediction level on these outputs. Nothing is written: the saved analysis above, "
                   "comparisons and reports are unchanged.")
        t1, t2 = st.columns([3, 1])
        cal = t1.text_input("Calibration (entry id or YAML path)", key="trial_cal", placeholder="from plan")
        level = t2.select_slider("Prediction interval", [0.90, 0.95, 0.99], value=0.95, key="trial_level")
        if st.button("Run trial", key="trial_run"):
            try:
                ss._trial = dict(key=(str(root), eid, n), result=J.trial_analysis(root, eid, n, calibration=cal or None, level=level))
            except Exception as e:  # noqa: BLE001
                ss._trial = None
                st.error(f"{type(e).__name__}: {e}")
        trial = ss.get("_trial")
        if trial and trial["key"] == (str(root), eid, n):
            st.markdown("**Trial — not saved; not used in comparisons or reports**")
            _delta_status(trial["result"])
            _show_result(trial["result"], key="trial")


def _show_result(res: dict, key: str = "result"):
    st.write(f"Calibration: **{res['calibration']}**")
    for n in res["notes"]:
        st.warning(n)
    if not res["rows"]:
        st.info("No matching Mossbauer property outputs found.")
        return
    from mossbauer_dft import trust
    df = pd.DataFrame([dict(r, flags=trust.label(trust.for_row(r))) for r in res["rows"]])
    cols = [c for c in ["job", "atom", "rho0", "delta", "delta_lo", "delta_hi", "deq_signed", "abs_deq", "eta", "s2", "fe_spin_mulliken",
                        "fe_spin_loewdin", "delta_exp", "abs_deq_exp", "lowest_energy_state", "ok", "flags"] if c in df]
    st.dataframe(df[cols], key=f"{key}_rows", hide_index=True, use_container_width=True)
    near = [(r["job"], r["atom"], r["nearest_refs"]) for r in res["rows"] if r.get("nearest_refs")]
    if near:
        with st.expander("Nearest calibration references"):
            st.caption("The calibration points closest in ρ(0) to each prediction: compare with similar compounds before "
                       "trusting the number. Flags mark points with a large residual, influence or leverage.")
            for job, atom, refs in near:
                st.markdown(f"**{html.escape(str(job))} · Fe{atom}**")
                st.dataframe(pd.DataFrame([dict(reference=x["label"], ox=x["ox"], S=x["spin"], d_rho0=x["d_rho0"],
                                                delta_exp=x["delta_exp"], residual=x["residual"], flags=", ".join(x["flags"]))
                                           for x in refs]), hide_index=True, use_container_width=True, key=f"{key}_near_{job}_{atom}")
    details = [(r["job"], r["atom"], f) for r in res["rows"] for f in trust.for_row(r)]
    if details:
        with st.expander(f"Trust flags ({sum(f['severity'] == trust.BLOCKING for *_, f in details)} blocking, "
                         f"{sum(f['severity'] == trust.CAUTION for *_, f in details)} caution)"):
            st.caption("Blocking: do not read the value as a prediction. Caution: the value stands with the stated limitation.")
            for job, atom, f in details:
                (st.error if f["severity"] == trust.BLOCKING else st.warning)(f"{job} Fe{atom} · {f['property']}: {f['text']}")
    st.download_button("Download CSV", W.results_csv(res), "trial.csv" if res.get("trial") else "results.csv", key=f"{key}_csv")
    import plotly.graph_objects as go
    p1, p2 = st.columns(2)
    d = df[df["delta"].notna()] if "delta" in df else df.iloc[0:0]
    if "delta_exp" in d and len(d) and d["delta_exp"].notna().any():
        d = d[d["delta_exp"].notna()].astype({"delta": float, "delta_lo": float, "delta_hi": float, "delta_exp": float})
        fig = go.Figure()
        status = (res.get("delta_status") or {}).get("status") if isinstance(res.get("delta_status"), dict) else res.get("delta_status")
        fig.add_trace(go.Scatter(x=d["delta_exp"], y=d["delta"], mode="markers", name=f"delta ({status or 'calculated'})", marker=dict(size=9),
                                 error_y=dict(type="data", symmetric=False, array=d["delta_hi"] - d["delta"], arrayminus=d["delta"] - d["delta_lo"])))
        lo, hi = float(min(d["delta_exp"].min(), d["delta"].min())) - 0.2, float(max(d["delta_exp"].max(), d["delta"].max())) + 0.2
        fig.add_trace(go.Scatter(x=[lo, hi], y=[lo, hi], mode="lines", name="y = x", line=dict(dash="dash", color=REF_LINE_COLOR)))
        p1.plotly_chart(_style_fig(fig, "delta exp (mm/s)", f"delta calc (mm/s), {int(res['level']*100)}% PI"), use_container_width=True,
                        key=f"{key}_delta_plot")
    if "abs_deq_exp" in df and "functional" in df:
        f2 = go.Figure()
        for fn, g in df.groupby("functional"):
            f2.add_trace(go.Scatter(x=g["abs_deq_exp"], y=g["abs_deq"], mode="markers", name=fn, marker=dict(size=9)))
        p2.plotly_chart(_style_fig(f2, "|DeltaEQ| exp (mm/s)", "|DeltaEQ| calc (mm/s)"), use_container_width=True, key=f"{key}_deq_plot")
    if "delta_exp" not in df and "abs_deq_exp" not in df:
        st.caption("Enter experimental values on '1  Structure' (or in plan inputs.experimental) to get the comparison plots.")

