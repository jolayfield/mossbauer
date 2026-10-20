"""Electronic-state families page: link related states of one compound as separate drafts."""
from __future__ import annotations
import json

import pandas as pd
import streamlit as st

from mossbauer_dft.gui.state import _invalidate, _scan, ss
from mossbauer_dft.gui.widgets import _need_entry


def page_states():
    from mossbauer_dft import families as F
    st.header('Electronic-state families')
    cur = _need_entry()
    if cur is None:
        return
    root, entry = cur.root, cur.entry.entry_id
    st.caption('Link related states explicitly. Every new state is a separate draft with its own approval and calculation cost.')
    if ss.get('_family_message'):
        st.info(ss.pop('_family_message'))
    with st.form('candidate_states'):
        name = st.text_input('Family name', value=cur.entry.display_name or entry)
        candidates = st.text_area('Candidate states (JSON)', value='[{"multiplicity": 2}, {"multiplicity": 4}]',
                                  help='Each state has multiplicity and optional charge/broken_symmetry. The selected entry supplies the structure and protocol.')
        preview = st.form_submit_button('Preview candidate states')
    if preview:
        try:
            ss._state_preview = dict(root=str(root), family=name, proposal=F.preview(root, entry, json.loads(candidates)))
        except (ValueError, OSError) as ex:
            st.error(str(ex))
    pending = ss.get('_state_preview')
    if pending and pending['root'] == str(root) and pending['proposal']['entry'] == entry:
        proposal = pending['proposal']
        st.dataframe(pd.DataFrame(proposal['states']), hide_index=True, use_container_width=True)
        st.warning(f"Additional jobs: {proposal['additional_jobs']}; estimated core-hours: {proposal['additional_core_hours']} (unvalidated estimate). No jobs are submitted.")
        st.button('Create reviewed state entries', on_click=_cb_family_save, args=(str(root), pending), type='primary')
    with st.expander('Link existing entries into a family'):
        with st.form('link_existing_states'):
            existing_name = st.text_input('Existing-state family name')
            members = st.multiselect('Family members', [e.entry_id for e in _scan()])
            link = st.form_submit_button('Create family from selected entries')
        if link:
            try:
                F.create(root, existing_name, members)
                st.rerun()
            except (ValueError, OSError) as ex:
                st.error(str(ex))
    names = list(F.read(root)['families'])
    if names:
        name = st.selectbox('Family to compare', names)
        tolerance = st.number_input('Near-degeneracy tolerance (kJ/mol)', value=1.0, min_value=0.0)
        result = F.energies(root, name, tolerance)
        st.write('Members: ' + ', '.join(result['family']['entries']))
        if result['rows']:
            st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k != 'protocol'} for r in result['rows']]),
                         hide_index=True, use_container_width=True)
        else:
            st.info('Analyze family members first. Compatible complete property outputs are required for energy comparison.')
        for note in result['notes']:
            st.caption(note)
        with st.expander('Comparison evidence and excluded results'):
            st.json(result)


def _cb_family_save(root, pending):
    from mossbauer_dft import families as F
    try:
        result = F.generate(root, pending['family'], pending['proposal'])
        ss._state_preview = None
        ss._family_message = f"Created {len(result['created'])} draft state entries; independent approval is still required."
        _invalidate()
    except (ValueError, OSError) as ex:
        ss._family_message = f'No state family saved: {ex}'

