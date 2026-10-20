"""Every GUI page renders without an exception, with and without a selected entry (module split; decision D64)."""
import pytest
from test_cli_gui import _app
from test_plan import _home  # noqa: F401
from test_project_status import _ready, fe1, proj  # noqa: F401

st = pytest.importorskip("streamlit")
from mossbauer_dft.gui.state import PAGES  # noqa: E402


def _visit_every_page(at):
    for page in PAGES:
        next(r for r in at.sidebar.radio if r.key == "page").set_value(page).run()
        assert not at.exception, (page, [e.value for e in at.exception])
        assert at.session_state["page"] == page


def test_every_page_without_project(tmp_path, monkeypatch, _home):
    _visit_every_page(_app(monkeypatch, tmp_path / "empty"))


def test_every_page_without_selected_entry(proj, fe1, monkeypatch, _home):
    _ready(proj, fe1)
    at = _app(monkeypatch, proj)
    assert "entry" not in at.session_state or at.session_state["entry"] is None
    _visit_every_page(at)


def test_every_page_with_selected_entry(proj, fe1, monkeypatch, _home):
    ref = _ready(proj, fe1)
    at = _app(monkeypatch, proj, qp={"entry": ref.entry_id})
    assert at.session_state["entry"] == ref.entry_id
    _visit_every_page(at)
