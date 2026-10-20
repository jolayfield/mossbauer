"""One module per GUI page.  Pages import gui.state and gui.widgets, never each other; cross-page navigation goes through
ss.page (gui.state._goto / _cb_open_entry).  Not named ``pages`` because Streamlit treats a ``pages/`` folder next to the
entry script as multipage navigation."""
