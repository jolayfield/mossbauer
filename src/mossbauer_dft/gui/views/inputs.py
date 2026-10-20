"""ORCA inputs page (step 3): write inputs for the approved plan."""
from __future__ import annotations
from pathlib import Path
import shlex
import yaml

import streamlit as st

from mossbauer_dft import project as J
from mossbauer_dft import workflow as W
from mossbauer_dft.gui.state import _invalidate, ss
from mossbauer_dft.gui.widgets import _cli, _header, _need_entry


def _inputs_on_disk(inputs: Path) -> dict | None:
    """What write_inputs reported, rebuilt from a version's inputs/ folder (after a reload or for inputs written elsewhere)."""
    if not (inputs / J.MANIFEST).is_file():
        return None
    sub = inputs / "submit.sh"
    return dict(outdir=str(inputs), files=sorted(p.name for p in inputs.iterdir() if p.is_file()),
                commands=W.submit_commands(inputs) if sub.is_file() else [])


def page_inputs():
    st.header("ORCA inputs")
    cur = _need_entry()
    if cur is None:
        return
    _header(cur)
    eid, n, vd = cur.entry.entry_id, cur.vs.version, cur.vs.path
    if not cur.vs.approved:
        st.warning(f"v{n} is not approved" + (" (modified after approval)" if "modified" in cur.vs.flags else "")
                   + ": approve it on **2  Review & approve** (or in the Project overview) before generating inputs.")
        return
    from mossbauer_dft.site import available_sites, resolve_site
    with st.container(border=True):
        c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
        options = available_sites()
        try:
            default = resolve_site()
        except (ValueError, OSError, yaml.YAMLError) as exc:
            default = None
            st.info(str(exc))
        if ss.get('input_site_profile') not in options:
            ss.pop('input_site_profile', None)
        site = c1.selectbox('Site profile', options, index=options.index(default) if default in options else None,
                            placeholder='Choose a cluster profile', key='input_site_profile')
        if site is None:
            st.caption('Select a profile explicitly or configure a default on Cluster setup. SSH settings are optional.')
        if c2.button("Generate inputs", type="primary", use_container_width=True, disabled=site is None):
            try:
                J.write_version_inputs(cur.root, eid, n, site)
                _invalidate()
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"{type(e).__name__}: {e}")
    _cli(f"mossbauer project setup --project {shlex.quote(str(cur.root))} --entry {shlex.quote(eid)} --version {n} --site {shlex.quote(site) if site else 'SITE'}")
    r = _inputs_on_disk(vd / "inputs")
    if not r:
        return
    st.success(f"Inputs for v{n} are in {r['outdir']}")
    st.info(f"Put each finished ORCA output in `{vd / 'outputs'}` as `<stem>.out` (the job stems below); the app finds them there.")
    from mossbauer_dft import handoff as H
    st.caption('Download the files, transfer them yourself and run submit.sh on your cluster. SSH details are optional.')
    agent = st.checkbox('Include instructions for an external agent', key='inputs_agent')
    upload = st.checkbox('Include a Bash upload helper', key='inputs_upload')
    try:
        bundle = H.export_inputs(cur.root, eid, n, agent=agent)
    except (ValueError, OSError) as exc:
        st.error(str(exc))
        return
    if upload:
        st.caption('Requires Bash, tar, OpenSSH and shasum/sha256sum. Uses your SSH configuration. Uploads only; never submits.')
        try:
            preview = H.upload_preview(cur.root, eid, n)
            bundle = H.export_inputs(cur.root, eid, n, agent=agent, upload=True)
            st.caption('Upload destination (a new directory):')
            st.code(f"{preview['host']}:{preview['destination']}", language=None)
            with st.expander('Preview upload script'):
                st.code(preview['script'], language='bash')
        except (ValueError, OSError) as exc:
            st.warning(f'{exc}. This download contains calculation files without the upload helper.')
    st.download_button("Download calculation files", bundle, f"{eid}_v{n}_inputs.zip",
                       mime="application/zip", type="primary")
    if agent:
        with st.expander('Preview agent instructions'):
            st.code(H.agent_instructions(cur.root, eid, n), language='markdown')
    st.subheader("Submission commands (displayed, not executed)")
    st.code("\n".join(r["commands"]), language="bash")
    st.subheader("Input decks")
    st.caption('For a compact return, run this with standard-library Python in the remote inputs folder after ORCA finishes:')
    st.code('python3 harvest.pyz harvest-manifest.json . results.json', language='bash')
    st.caption('Return results.json by file import or SSH fetch on Results. Full outputs remain supported; job submission remains external.')
    for f in r["files"]:
        if f.endswith(".inp"):
            with st.expander(f):
                st.code((Path(r["outdir"]) / f).read_text())

