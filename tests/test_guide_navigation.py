"""The guide drives navigation (second-pass idea 3; decision D54)."""
from mossbauer_dft import tutorial as T
from mossbauer_dft.gui import app as A
from test_plan import _home  # noqa: F401
from test_cli_gui import _app, _btn, _texts


def test_guide_pages_are_real_gui_pages(tmp_path, _home):
    root = tmp_path / "ex"
    T.create_example(root)
    for s in T.guide(root)["steps"]:
        assert s["page_keys"] and all(p in A.PAGES for p in s["page_keys"])
        assert s["page"] == " / ".join(A.STEPS[p] for p in s["page_keys"])


def test_do_this_next_opens_the_entry_on_its_page(tmp_path, _home, monkeypatch):
    root = tmp_path / "ex"
    refs = T.create_example(root)
    at = _app(monkeypatch, root)
    _btn(at, f"Do this next: open {refs[0].entry_id} on 2  Review & approve").click().run()
    assert not at.exception
    assert at.session_state["page"] == "Plan review" and at.session_state["entry"] == refs[0].entry_id
    assert any(h.value == "Plan review" for h in at.header)


def test_step_page_without_entry_lists_waiting_entries(tmp_path, _home, monkeypatch):
    root = tmp_path / "ex"
    refs = T.create_example(root)
    at = _app(monkeypatch, root, qp={"page": "Plan review"})
    assert "3 entries are waiting for this step" in _texts(at)
    _btn(at, f"{refs[1].entry_id}  ·  review and approve plans").click().run()
    assert not at.exception and at.session_state["entry"] == refs[1].entry_id


def test_sidebar_shows_waiting_counts(tmp_path, _home, monkeypatch):
    root = tmp_path / "ex"
    T.create_example(root)
    at = _app(monkeypatch, root)
    radio = next(r for r in at.sidebar.radio if r.key == "page")
    assert "2  Review & approve  · 3 waiting" in radio.options
