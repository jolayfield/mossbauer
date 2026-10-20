"""Study comparison page: saved analyses side by side across the project."""
from __future__ import annotations
import io
import json

import pandas as pd
import streamlit as st

from mossbauer_dft.gui.state import _root
from mossbauer_dft.gui.widgets import _cli


def page_study():
    from mossbauer_dft import study, trust
    st.header('Study comparison')
    root = _root()
    if root is None:
        st.info('Open a project on the Project page to compare saved analyses.')
        return
    _cli(f'mossbauer project compare --project {root} --json')
    choices = study.available(root)
    if not choices:
        st.info('No saved analyses yet. Analyze an entry on Results first.')
        return
    labels = {f"{c['entry']}:{c['version']}": c['label'] for c in choices}
    defaults = {}
    for c in choices:
        defaults[c['entry']] = f"{c['entry']}:{c['version']}"
    selected = st.multiselect('Saved versions to compare', list(labels), default=list(defaults.values()),
                              format_func=labels.__getitem__, key='study_selection')
    pairs = [(key.rsplit(':', 1)[0], int(key.rsplit(':', 1)[1])) for key in selected]
    doc = study.compare(root, pairs)
    for issue in doc['issues']:
        st.warning(f"{issue['entry']} v{issue['version']}: {issue['reason']}")
    if not doc['rows']:
        st.info('Select at least one readable saved analysis.')
        return
    st.caption('Values are mm/s; energies are Eh. Older versions are explicit selections, not replacements for newer plans.')
    table = []
    for row in doc['rows']:
        table.append({k: row.get(k) for k in ('compound', 'entry', 'version', 'older_version', 'job', 'atom', 'functional',
                    'delta', 'delta_exp', 'abs_deq', 'abs_deq_exp', 'energy_Eh', 'training_membership', 'flags',
                    'calibration', 'result_path')})
        table[-1]['trust'] = trust.label(row.get('trust_flags') or [])
    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
    with st.expander('Source evidence and protocol details'):
        st.json(doc)
    st.subheader('Agreement with experiment')
    st.caption('Separate cohorts use the full calculation protocol and calibration snapshot. Unknown membership is descriptive agreement, not held-out validation.')
    if doc['summaries']:
        st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k != 'protocol'} for r in doc['summaries']]),
                     hide_index=True, use_container_width=True)
    else:
        st.info('No comparable, current, reliable rows with unambiguous experimental observations are eligible for summaries.')
    for note in doc['notes']:
        st.caption(note)
    st.download_button('Download study CSV', study.comparison_csv(doc), 'study.csv', mime='text/csv')
    from mossbauer_dft import report
    st.download_button('Download study JSON (shareable snapshot)', json.dumps(report.public_snapshot(doc), indent=2),
                       'study.json', mime='application/json')
    st.subheader('Portable study report')
    title = st.text_input('Report title', value='Mössbauer study report', key='report_title')
    archive, html = report.bundle(doc, title)
    st.caption('A snapshot of this selection with methods, units, caveats and source hashes. Review before publication.')
    st.download_button('Download offline report ZIP', archive, 'study-report.zip', mime='application/zip')
    st.download_button('Download standalone report HTML', html, 'study-report.html', mime='text/html')
    st.caption('The ZIP also holds an SI table (CSV and LaTeX), a methods paragraph, BibTeX and a hash manifest.')
    with st.expander('Check a report ZIP'):
        st.caption('Re-checks member hashes, re-derives each delta from its recorded rho(0) and calibration constants, '
                   'and checks the status rules. Raw outputs are not in a report, so their contents are not re-checked.')
        upload = st.file_uploader('Report ZIP', type=['zip'], key='verify_zip')
        if upload is not None:
            r = report.verify(io.BytesIO(upload.getvalue()))
            (st.success if r['ok'] else st.error)('All checks passed.' if r['ok'] else 'Some checks failed.')
            st.dataframe(pd.DataFrame(r['checks']), hide_index=True, use_container_width=True, key='verify_table')
            _cli('mossbauer report verify study-report.zip')

