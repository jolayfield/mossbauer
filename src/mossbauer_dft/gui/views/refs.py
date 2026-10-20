"""Reference data page: the bundled reference sets and calibrations."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from mossbauer_dft import data as D
from mossbauer_dft import references as R
from mossbauer_dft.gui.widgets import _cli


def page_refs():
    st.header("Reference data")
    _cli("mossbauer refset list | import | setup | fit")
    tab1, tab2, tab3, tab4 = st.tabs(["Calibrations", "Training set", "Reference set", "Method rules"])
    with tab1:
        rows = []
        for e in R.list_calibrations():
            rows.append(dict(id=e["id"], source=e.get("source", "literature"), verified=R.entry_verified(e), numeric=R.is_numeric_entry(e),
                             alpha=e["alpha"] if R.is_numeric_entry(e) else None, C=e["C"] if R.is_numeric_entry(e) else None,
                             beta=e["beta"] if R.is_numeric_entry(e) else None, doi=e.get("doi"), n=e.get("n_training")))
        df = pd.DataFrame(rows)
        only = st.checkbox("verified only", value=False)
        st.dataframe(df[df.verified] if only else df, hide_index=True, use_container_width=True)
    with tab2:
        ts = pd.DataFrame(R.training_set_rows())
        sets = st.multiselect("Set", sorted(ts["set"].unique()), default=sorted(ts["set"].unique()))
        st.dataframe(ts[ts["set"].isin(sets)], hide_index=True, use_container_width=True)
    with tab3:
        rn = R.default_refset_name()
        d = R.refset_definition(rn)
        ready, skipped = R.resolve_refset(rn)
        st.write(f"{d['title']} (doi {d['doi']}): {len(d['compounds'])} compounds, **{len(ready)} with structures**")
        st.warning(d["structure_status_note"])
        st.dataframe(pd.DataFrame(d["compounds"]), hide_index=True, use_container_width=True)
    with tab4:
        st.markdown(D.load_method_rules_md() or "n/a")

