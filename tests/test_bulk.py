"""Series verbs over one selection (second-pass idea 2; decisions D52-D53)."""
import pytest

from mossbauer_dft import bulk as B, presets as PR, project as J
from mossbauer_dft.cli import main
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _add, _approve, _ready, _place, _jobs  # noqa: F401


def _outputs(root, ref):
    for stem, job in _jobs(root, ref).items():
        if job["kind"] == "mossbauer_sp":
            _place(root, ref, stem)


def test_setup_writes_approved_and_skips_drafts_with_reason(proj, fe1):
    a, b = _add(proj, fe1, name="a"), _add(proj, fe1, name="b")
    _approve(proj, a)
    outs = {o["entry"]: o for o in B.run(proj, "setup", [a.entry_id, b.entry_id])}
    assert outs[a.entry_id]["outcome"] == "done" and J.scan_version(proj, a.entry_id).status == "inputs written"
    assert outs[b.entry_id]["outcome"] == "skipped" and "not approved" in outs[b.entry_id]["detail"]
    assert J.scan_version(proj, b.entry_id).status == "draft"


def test_analyze_and_reanalyze_eligibility(proj, fe1):
    a, b = _ready(proj, fe1, name="a"), _ready(proj, fe1, name="b")
    _outputs(proj, a)
    assert B.eligible(proj, "analyze") == [a.entry_id]
    assert B.eligible(proj, "reanalyze") == []
    outs = B.run(proj, "analyze", [a.entry_id, b.entry_id])
    assert [o["outcome"] for o in outs] == ["done", "skipped"]
    assert J.load_results(proj, a.entry_id) is not None
    assert B.eligible(proj, "reanalyze") == [a.entry_id]


def test_one_failure_does_not_stop_the_others(proj, fe1, monkeypatch):
    a, b = _ready(proj, fe1, name="a"), _ready(proj, fe1, name="b")
    _outputs(proj, a); _outputs(proj, b)
    real = J.analyze_version
    def flaky(root, entry, *args, **kw):
        if entry == a.entry_id:
            raise RuntimeError("disk full")
        return real(root, entry, *args, **kw)
    monkeypatch.setattr(J, "analyze_version", flaky)
    outs = B.run(proj, "analyze", [a.entry_id, b.entry_id])
    assert [o["outcome"] for o in outs] == ["failed", "done"] and "disk full" in outs[0]["detail"]


def test_preset_preview_pin_then_apply_makes_drafts(proj, fe1):
    a, b = _add(proj, fe1, name="a"), _add(proj, fe1, name="b")
    PR.save(proj, "hf", {"highfield": True})
    pv = B.preview_preset(proj, [a.entry_id, b.entry_id], "hf")
    assert len(pv["proposals"]) == 2 and pv["pin"]
    outs = B.apply_preset(proj, pv, expect_pin=pv["pin"])
    assert all(o["outcome"] == "done" for o in outs)
    assert all(J.scan_version(proj, e).version == 2 for e in (a.entry_id, b.entry_id))
    again = B.preview_preset(proj, [a.entry_id], "hf")
    assert again["proposals"] == [] and "already matches" in again["skipped"][0]["detail"]


def test_preset_apply_refuses_a_stale_pin(proj, fe1):
    a = _add(proj, fe1, name="a")
    PR.save(proj, "hf", {"highfield": True})
    pv = B.preview_preset(proj, [a.entry_id], "hf")
    J.edit_entry(proj, a.entry_id, overrides={"nprocs": 8})  # plan changes after the preview
    with pytest.raises(J.ProjectError, match="preview again"):
        B.apply_preset(proj, pv, expect_pin=pv["pin"])


def test_cli_bulk_all_and_exit_code(proj, fe1, capsys):
    a, b = _add(proj, fe1, name="a"), _add(proj, fe1, name="b")
    _approve(proj, a)
    main(["project", "bulk", "setup", "--project", str(proj), "--all"])
    out = capsys.readouterr().out
    assert f"{a.entry_id} v1: done" in out and b.entry_id not in out and "1 done" in out
    with pytest.raises(SystemExit):
        main(["project", "bulk", "analyze", "--project", str(proj)])


def test_gui_bulk_panel_writes_inputs_for_eligible_selection(proj, fe1, monkeypatch):
    from test_cli_gui import _app, _btn, _select, _texts
    a, b = _add(proj, fe1, name="a"), _add(proj, fe1, name="b")
    _approve(proj, a)
    at = _app(monkeypatch, proj)
    _select(at, [0, 1])
    _btn(at, "Write inputs (1)").click().run()
    assert not at.exception
    assert J.scan_version(proj, a.entry_id).status == "inputs written"
    assert J.scan_version(proj, b.entry_id).status == "draft"
    assert "1 done" in _texts(at)
