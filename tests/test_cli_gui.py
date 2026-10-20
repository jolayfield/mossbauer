import json

import pytest
from mossbauer_dft.cli import main
from test_plan import XYZ, _home  # noqa: F401
from test_project_status import _approve, _place, _ready, fe1, proj  # noqa: F401

CAL = "mcwilliams2017_b3lyp_def2tzvp"


def test_cli_plan_approve_setup(tmp_path, capsys):
    x = tmp_path / "m.xyz"
    x.write_text(XYZ)
    pl = tmp_path / "p" / "plan.yaml"
    main(["plan", str(x), "--charge", "-1", "--multiplicity", "4", "--ox-state", "1", "--name", "cmpd", "--out", str(pl),
          "--exp-delta", "0.7", "--exp-deq", "1.9"])
    with pytest.raises(SystemExit):
        main(["setup", str(pl), "--out", str(tmp_path / "in")])
    main(["approve", str(pl), "--by", "me"])
    main(["setup", str(pl), "--out", str(tmp_path / "in")])
    assert (tmp_path / "in" / "cmpd_sp_b3lyp.inp").exists()
    out = capsys.readouterr().out
    assert "R01_FeI_lowcoordinate" in out and "submission commands" in out
    main(["refset", "list"])
    assert "fe1_calib18: 18 compounds, 18 ready" in capsys.readouterr().out  # default when McWilliams structures are missing
    main(["refset", "list", "--refset", "mcwilliams2017"])
    assert "35 compounds, 0 ready" in capsys.readouterr().out


# ----------------------------------------------------------------------------- GUI (streamlit AppTest)
PAGES = ("Plan review", "Inputs", "Results", "References", "New prediction", "Batch intake", "Project")


def _app(monkeypatch, wd=None, qp=None, timeout=60):
    """Run the app fresh (a new browser session): ``wd`` as MOSSBAUER_GUI_WORKDIR, ``qp`` as the URL's query params."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    import mossbauer_dft.gui.app as app
    if wd is None:
        monkeypatch.delenv("MOSSBAUER_GUI_WORKDIR", raising=False)
    else:
        monkeypatch.setenv("MOSSBAUER_GUI_WORKDIR", str(wd))
    at = AppTest.from_file(app.__file__, default_timeout=timeout)
    run = at._run

    def run_with_dataframe_state(widget_state=None, timeout=None):
        # AppTest renders dataframes and data editors as non-widget elements, so 1.65
        # omits their selection and cell edits from reruns. Send the state a browser
        # would retain alongside the other widgets (without duplicating it if AppTest
        # gains support).
        from streamlit.proto.WidgetStates_pb2 import WidgetStates
        if widget_state is None:
            widget_state = WidgetStates()
        sent = {w.id for w in widget_state.widgets}
        for df in at.dataframe:
            if not df.key or not df.proto.id or df.proto.id in sent:
                continue
            try:
                state = at.session_state[df.key]
            except KeyError:
                continue
            if isinstance(state, dict) and ("selection" in state or "edited_rows" in state):
                widget_state.widgets.add(id=df.proto.id, string_value=json.dumps(state))
        return run(widget_state, timeout)

    monkeypatch.setattr(at, "_run", run_with_dataframe_state)
    for k, v in (qp or {}).items():
        at.query_params[k] = v
    at.run()
    assert not at.exception
    return at


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def _overview(at):
    return next(d for d in at.dataframe if d.key == "overview").value


def _table(at, *cols):
    """The first dataframe with all of ``cols`` (plain st.dataframe elements carry no key in AppTest)."""
    return next(d.value for d in at.dataframe if set(cols) <= set(d.value.columns))


def _row(at, entry_id):
    df = _overview(at)
    return df[df["entry"] == entry_id].iloc[0]


def _select(at, rows):
    at.session_state["overview"] = {"selection": {"rows": list(rows), "columns": [], "cells": []}}
    at.run()
    assert not at.exception


def _texts(at):
    return " ".join(str(e.value) for kind in ("markdown", "caption", "info", "error", "warning", "success")
                    for e in getattr(at, kind))


def _add(root, s, name="cmpd", **kw):
    from mossbauer_dft import project as J
    return J.add_entry(root, s, -1, 4, name=name, oxidation_state=1, **kw)


def test_gui_empty_folder_offers_create_project(tmp_path, monkeypatch):
    from mossbauer_dft import project as J
    wd = tmp_path / "wd"
    wd.mkdir()
    at = _app(monkeypatch, wd)
    assert at.sidebar.radio[0].value == "Project"
    assert not J.is_project(wd)
    _btn(at, "Create project here").click().run()
    assert not at.exception and J.is_project(wd)
    assert J.list_entries(wd) == [] and "No entries yet" in _texts(at)


def test_gui_legacy_workdir_imports_on_confirmation(tmp_path, monkeypatch, fe1):
    """AE7: a pre-project work dir is imported only after confirmation, as one entry with v1; originals are kept."""
    from mossbauer_dft import plan as P
    from mossbauer_dft import project as J
    wd = tmp_path / "wd"
    P.save_plan(P.make_plan(fe1, -1, 4, name="cmpd", oxidation_state=1), wd / "plan" / "plan.yaml")
    at = _app(monkeypatch, wd)
    assert not J.is_project(wd)
    _btn(at, "Import as project — originals are kept").click().run()
    assert not at.exception and J.is_project(wd)
    df = _overview(at)
    assert list(df["entry"]) == ["cmpd"] and list(df["version"]) == ["v1"] and list(df["status"]) == ["draft"]
    assert (wd / "plan" / "plan.yaml").is_file()


def test_gui_nonexistent_path_shows_error_and_creates_nothing(tmp_path, monkeypatch):
    at = _app(monkeypatch)
    target = tmp_path / "nope"
    at.text_input(key="proj_path").input(str(target)).run()
    at.button(key="open_proj").click().run()
    assert not at.exception
    assert any("does not exist" in e.value for e in at.error)
    assert not target.exists()


def test_gui_refresh_status_picks_up_new_outputs(proj, fe1, monkeypatch):
    """R8: an output added while the app is open shows after 'Refresh status'."""
    ref = _ready(proj, fe1)
    at = _app(monkeypatch, proj)
    assert _row(at, ref.entry_id)["outputs"] == "0 of 3"
    _place(proj, ref, f"{ref.entry_id}_sp_b3lyp")
    _btn(at, "Refresh status").click().run()
    assert not at.exception
    assert _row(at, ref.entry_id)["outputs"] == "1 of 3"
    assert "scanned at" in _texts(at)


def test_gui_selection_mirrors_query_params_and_restores(proj, fe1, monkeypatch):
    a, b = _add(proj, fe1, "alpha"), _add(proj, fe1, "beta")
    at = _app(monkeypatch, proj)
    _select(at, [1])
    assert at.session_state["entry"] == b.entry_id and at.session_state["version"] == 1
    qp = dict(at.query_params)
    assert qp["entry"] == b.entry_id and qp["version"] == "1" and qp["project"] == str(proj.resolve())
    fresh = _app(monkeypatch, None, qp={"project": qp["project"], "entry": qp["entry"], "version": qp["version"]})
    assert fresh.session_state["entry"] == b.entry_id and fresh.session_state["version"] == 1
    assert "beta" in " ".join(m.value for m in fresh.sidebar.markdown) and a.entry_id != b.entry_id


def test_gui_create_plan_adds_draft_entry(proj, monkeypatch):
    from mossbauer_dft import project as J
    at = _app(monkeypatch, proj)
    at.sidebar.radio[0].set_value("New prediction").run()
    at.session_state["source"] = "Paste XYZ"
    at.run()
    at.text_area(key="xyz_text").input(XYZ).run()
    at.button(key="use_xyz").click().run()
    at.number_input(key="charge").set_value(-1).run()
    at.number_input(key="mult").set_value(4).run()
    at.text_input(key="name").input("cmpd").run()
    at.selectbox(key="ox").set_value(1).run()
    _btn(at, "Create plan").click().run()
    assert not at.exception
    (e,) = J.list_entries(proj)
    assert e.display_name == "cmpd" and [v.version for v in e.versions] == [1]
    at.sidebar.radio[0].set_value("Project").run()
    assert _row(at, e.entry_id)["status"] == "draft"


def test_gui_batch_approval_previews_blocked_and_seals_the_rest(proj, fe1, monkeypatch):
    """AE4 in the GUI: the open-item row is previewed as blocked; the other two are sealed with via: gui."""
    from mossbauer_dft import project as J
    refs = [_add(proj, fe1, "cmpd0"), _add(proj, fe1, "cmpd1"), _add(proj, fe1, "radical", radical_ligand=True)]
    at = _app(monkeypatch, proj)
    assert list(_overview(at)["entry"]) == [r.entry_id for r in refs]
    _select(at, [0, 1, 2])
    preview = _table(at, "entry", "result", "reason")
    blocked = preview[preview["entry"] == refs[2].entry_id].iloc[0]
    assert blocked["result"] == "blocked" and "broken-symmetry spec required" in blocked["reason"]
    at.text_input(key="approver").input("A. Researcher").run()
    _btn(at, "Seal 2 plans as A. Researcher").click().run()
    assert not at.exception
    for r in refs[:2]:
        a = J.load_version(proj, r.entry_id)["approval"]
        assert a["by"] == "A. Researcher" and a["via"] == "gui"
        assert J.scan_version(proj, r.entry_id).approved
    assert J.scan_version(proj, refs[2].entry_id).status == "draft"
    assert "sealed" in _texts(at) and _row(at, refs[0].entry_id)["approval"] == "by A. Researcher via gui"


def test_gui_overview_shows_outputs_found_n_of_m(proj, fe1, monkeypatch):
    """AE1 in the GUI: outputs placed in a version's outputs/ show as 'outputs found, 2 of 3' on the next open."""
    ref = _ready(proj, fe1)
    for stem in (f"{ref.entry_id}_sp_b3lyp", f"{ref.entry_id}_sp_tpssh"):
        _place(proj, ref, stem)
    at = _app(monkeypatch, proj)
    assert _row(at, ref.entry_id)["status"] == "outputs found, 2 of 3"


def _analysis_ready(proj, fe1):
    ref = _ready(proj, fe1)
    for stem in (f"{ref.entry_id}_sp_b3lyp", f"{ref.entry_id}_sp_tpssh"):
        _place(proj, ref, stem)
    return ref


def test_gui_analyze_saves_result_and_reload_shows_it(proj, fe1, monkeypatch):
    """AE5: the saved result reopens in a fresh session without re-running the analysis."""
    from mossbauer_dft import project as J
    ref = _analysis_ready(proj, fe1)
    qp = {"project": str(proj.resolve()), "entry": ref.entry_id, "version": "1", "page": "Results"}
    at = _app(monkeypatch, None, qp=qp)
    assert at.sidebar.radio[0].value == "Results"
    assert all(u.label == 'Compact cluster result bundle (JSON)' for u in at.file_uploader)  # no raw-output upload
    jobs = _table(at, "stem", "kind", "state")
    assert dict(zip(jobs["stem"], jobs["state"]))[f"{ref.entry_id}_sp_b3lyp"] == "complete"
    at.text_input(key="calib").input(CAL).run()
    _btn(at, "Analyze").click().run()
    assert not at.exception
    saved = J.load_results(proj, ref.entry_id, 1)
    assert saved and saved["calibration"] == CAL
    fresh = _app(monkeypatch, None, qp=qp)
    assert J.load_results(proj, ref.entry_id, 1)["analyzed_at"] == saved["analyzed_at"]
    assert any(b.label == "Re-analyze" for b in fresh.button)
    rows = _table(fresh, "job", "rho0")
    assert len(rows) == len(saved["rows"]) and f"Calibration: **{CAL}**" in _texts(fresh)


def test_gui_edit_of_analyzed_plan_creates_new_version(proj, fe1, monkeypatch):
    """F4 / R11: a non-state edit writes a draft v2; the overview flags older results; v1 stays viewable read-only."""
    from mossbauer_dft import project as J
    ref = _analysis_ready(proj, fe1)
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    qp = {"project": str(proj.resolve()), "entry": ref.entry_id, "version": "1", "page": "Plan review"}
    at = _app(monkeypatch, None, qp=qp)
    next(t for t in at.text_input if t.label == "DeltaEQ functional").input("PBE0")  # form fields are sent with the submit
    _btn(at, 'Preview changes').click().run()
    _btn(at, 'Save reviewed changes').click().run()
    assert not at.exception
    vs = J.list_versions(proj, ref.entry_id)
    assert [v.version for v in vs] == [1, 2] and vs[1].plan["status"] == "draft"
    assert vs[1].plan["methods"]["deq"]["functional"] == "PBE0" and vs[0].plan["status"] == "approved"
    assert J.load_results(proj, ref.entry_id, 1) is not None and J.load_results(proj, ref.entry_id, 2) is None
    assert at.session_state["version"] == 2
    at.selectbox(key=f"vsel_{ref.entry_id}").set_value(1).run()
    assert not at.exception and "older version" in _texts(at)
    assert not any(b.label == 'Preview changes' for b in at.button)
    at.sidebar.radio[0].set_value("Project").run()
    assert "older results" in _row(at, ref.entry_id)["flags"]


@pytest.mark.parametrize("change", ["new-version", "changed-plan"])
def test_gui_review_approval_seals_only_the_plan_shown(proj, fe1, monkeypatch, change):
    from mossbauer_dft import plan as P, project as J
    ref = _add(proj, fe1)
    qp = {"project": str(proj.resolve()), "entry": ref.entry_id, "version": "1", "page": "Plan review"}
    at = _app(monkeypatch, None, qp=qp)
    if change == "new-version":
        J.new_version(proj, ref.entry_id, overrides={"nprocs": 8})
    else:
        plan = P.update_plan(J.load_version(proj, ref.entry_id), overrides={"nprocs": 8})
        P.save_plan(plan, ref.path / "plan.yaml")
    _btn(at, "Approve plan").click().run()
    assert not at.exception
    assert all(not v.plan.get("approval") for v in J.list_versions(proj, ref.entry_id))
    assert "Not approved:" in _texts(at)


def test_gui_edit_keeps_a_brokensym_spec_it_cannot_show(proj, fe1, monkeypatch):
    """Review #7: an nprocs-only edit of an entry with a kind=brokensym (na/nb) spec writes v2 of the same entry."""
    from mossbauer_dft import project as J
    ref = J.add_entry(proj, fe1, -1, 4, name="cmpd", oxidation_state=1,
                      overrides={"broken_symmetry": dict(kind="brokensym", na=3, nb=1)})
    qp = {"project": str(proj.resolve()), "entry": ref.entry_id, "version": "1", "page": "Plan review"}
    at = _app(monkeypatch, None, qp=qp)
    next(n for n in at.number_input if n.label == "nprocs").set_value(4)
    _btn(at, 'Preview changes').click().run()
    _btn(at, 'Save reviewed changes').click().run()
    assert not at.exception
    assert [e.entry_id for e in J.list_entries(proj)] == [ref.entry_id]
    plan = J.load_version(proj, ref.entry_id, 2)
    assert plan["inputs"]["overrides"]["broken_symmetry"]["kind"] == "brokensym" and plan["resources"]["nprocs"] == 4


def test_streamlit_smoke(proj, fe1, monkeypatch):
    """Every page renders with no project open and with a project and entry selected; approving from the review page
    seals the selected version with via: gui."""
    from mossbauer_dft import project as J
    at = _app(monkeypatch)
    for page in PAGES:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page
    ref = _add(proj, fe1)
    at = _app(monkeypatch, proj)
    _select(at, [0])
    for page in PAGES:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page
    at.sidebar.radio[0].set_value("Plan review").run()
    _btn(at, "Approve plan").click().run()
    assert not at.exception
    assert J.load_version(proj, ref.entry_id)["approval"]["via"] == "gui"
    at.sidebar.radio[0].set_value("Inputs").run()
    _btn(at, "Generate inputs").click().run()
    assert not at.exception and J.scan_version(proj, ref.entry_id).status == "inputs written"
    for page in PAGES:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page


def test_streamlit_structure_sources(proj, monkeypatch):
    pytest.importorskip("rdkit")
    from mossbauer_dft import project as J
    at = _app(monkeypatch, proj, timeout=90)
    at.sidebar.radio[0].set_value("New prediction").run()
    # SMILES -> 3D, charge inferred from formal charges
    at.session_state["source"] = "SMILES"
    at.run()
    at.text_input(key="smiles").input("Cl[Fe-](Cl)(Cl)Cl").run()
    at.button(key="build_smi").click().run()
    assert not at.exception
    assert at.session_state["charge"] == -1
    assert at.session_state["structure"]["built"]
    # paste XYZ
    at.session_state["source"] = "Paste XYZ"
    at.run()
    at.text_area(key="xyz_text").input("2\nx\nFe 0 0 0\nCl 0 0 2.2\n").run()
    at.button(key="use_xyz").click().run()
    assert not at.exception and at.session_state["structure"]["filename"] == "pasted.xyz"
    # parity hint: FeCl has 43 electrons (odd) -> quartet fine, triplet flagged; [FeCl]+ (42, even) -> triplet fine
    at.number_input(key="charge").set_value(0).run()
    at.number_input(key="mult").set_value(4).run()
    assert not any("electrons" in e.value for e in at.error)
    at.number_input(key="mult").set_value(3).run()
    assert any("electrons" in e.value for e in at.error)
    at.number_input(key="charge").set_value(1).run()
    assert not any("electrons" in e.value for e in at.error)
    # bundled example pre-fills the form; then create a plan (a new entry in the project)
    at.session_state["source"] = "Examples"
    at.run()
    at.button(key="load_ex").click().run()
    assert not at.exception and at.session_state["mult"] >= 1 and at.session_state["expd"] is not None
    _btn(at, "Create plan").click().run()
    assert not at.exception
    (e,) = J.list_entries(proj)
    assert e.versions[0].plan["inputs"]["experimental"]
    # same compound and state again: a new version of the selected entry, not a second entry
    _btn(at, "Create plan").click().run()
    assert not at.exception
    (e,) = J.list_entries(proj)
    assert [v.version for v in e.versions] == [1, 2]
    # drawing source renders without a drawing
    at.session_state["source"] = "Draw"
    at.run()
    assert not at.exception


def test_study_page_uses_saved_analysis_and_labels_older_version(proj, fe1, monkeypatch):
    from mossbauer_dft import project as J
    ref = _analysis_ready(proj, fe1)
    J.analyze_version(proj, ref.entry_id, calibration=CAL)
    J.new_version(proj, ref.entry_id, overrides={'nprocs': 8})
    qp = {'project': str(proj.resolve()), 'page': 'Study'}
    at = _app(monkeypatch, None, qp=qp)
    assert not at.exception
    rows = _table(at, 'compound', 'version', 'training_membership')
    assert set(rows['version']) == {1} and rows['older_version'].all()
    at.multiselect(key='study_selection').set_value([]).run()
    assert not at.exception and 'Select at least one' in _texts(at)


def test_calibration_workbench_reference_preview_is_a_separate_save(proj, fe1, monkeypatch):
    from mossbauer_dft import project as J, calibration_workspace as C
    ref = _add(proj, fe1)
    C.create(proj, 'series', ref.entry_id)
    C.add_reference(proj, 'series', 'r1', ref.path / 'structure.xyz', -1, 4, 0.5)
    qp = {'project': str(proj.resolve()), 'entry': ref.entry_id, 'version': '1', 'page': 'Calibration'}
    at = _app(monkeypatch, None, qp=qp)
    assert 'reference inputs' in _texts(at).lower() or any(b.label == 'Prepare reference inputs' for b in at.button)
    _btn(at, 'Use workspace references in a plan preview').click().run()
    assert not at.exception and at.sidebar.radio[0].value == 'Plan review'
    assert len(J.list_versions(proj, ref.entry_id)) == 1
    _btn(at, 'Save reviewed changes').click().run()
    assert not at.exception
    plan = J.load_version(proj, ref.entry_id)
    assert plan['calibration']['strategy'] == 'user_refs' and len(plan['calibration']['user_refs']) == 1


def test_state_family_preview_does_not_create_until_save(proj, fe1, monkeypatch):
    from mossbauer_dft import project as J, families as F
    ref = _add(proj, fe1)
    qp = {'project': str(proj.resolve()), 'entry': ref.entry_id, 'version': '1', 'page': 'States'}
    at = _app(monkeypatch, None, qp=qp)
    _btn(at, 'Preview candidate states').click().run()
    assert not at.exception and len(J.list_entries(proj)) == 1
    _btn(at, 'Create reviewed state entries').click().run()
    assert not at.exception and len(J.list_entries(proj)) == 2
    assert len(F.read(proj)['families']['cmpd']['entries']) == 2


# ----------------------------------------------------------------------------- batch intake (GUI)
def _named(name):
    return XYZ.replace(XYZ.splitlines()[1], name, 1)


TWO = _named("alpha") + _named("beta")


def test_gui_new_prediction_refuses_multi_structure_upload(proj, monkeypatch):
    """R18 in the GUI: a three-block xyz on the single-structure upload is refused with the count; no structure is set."""
    at = _app(monkeypatch, proj)
    at.sidebar.radio[0].set_value("New prediction").run()
    at.file_uploader(key="struct").set_value(("three.xyz", (XYZ * 3).encode(), "text/plain")).run()
    assert not at.exception
    assert any("holds 3 structures" in e.value for e in at.error)
    assert "structure" not in at.session_state


def _batch_app(monkeypatch, proj, text=TWO, name="two.xyz"):
    at = _app(monkeypatch, None, qp={"project": str(proj.resolve()), "page": "Batch intake"})
    at.file_uploader(key="batch_files").set_value([(name, text.encode(), "text/plain")]).run()
    assert not at.exception
    return at


def _fill(at, **kw):
    rows = [dict(r, charge=-1, multiplicity=4, oxidation_state=1, **kw) for r in at.session_state["batch_rows"]]
    at.session_state["batch_rows"] = rows
    at.run()
    assert not at.exception
    return rows


def _outcomes(at):
    return list(_table(at, "compound", "outcome", "reason")["outcome"])


def _can_create(at):
    return any(b.label.startswith("Create ") for b in at.button)


def test_gui_batch_paste_sources_are_numbered_and_downloadable(proj, monkeypatch):
    at = _app(monkeypatch, None, qp={"project": str(proj.resolve()), "page": "Batch intake"})
    for text in (TWO, _named("delta"), _named("delta")):
        at.text_area(key="batch_paste").input(text).run()
        _btn(at, "Add pasted structures").click().run()
        assert not at.exception
    assert [r["structure"] for r in at.session_state["batch_rows"]] == ["pasted-1.xyz#1", "pasted-1.xyz#2", "pasted-2.xyz"]
    assert "already added" in _texts(at)
    assert {"Download pasted-1.xyz", "Download pasted-2.xyz"} <= {b.label for b in at.get("download_button")}


def test_gui_batch_upload_two_block_xyz_makes_two_named_rows(proj, monkeypatch):
    at = _batch_app(monkeypatch, proj)
    rows = at.session_state["batch_rows"]
    assert [r["compound"] for r in rows] == ["alpha", "beta"]
    assert [r["structure"] for r in rows] == ["two.xyz#1", "two.xyz#2"]
    at.run()  # the same upload is applied once
    assert len(at.session_state["batch_rows"]) == 2


def test_gui_batch_grid_edits_survive_adding_another_file(proj, monkeypatch):
    """KTD11: a cell edit is folded into the canonical rows and kept when a later upload appends rows (the editor key
    rotates).  AppTest has no data_editor element, so the edit is sent as the widget state a browser would send."""
    at = _batch_app(monkeypatch, proj)  # skips without Streamlit before the proto import below
    from streamlit.proto.WidgetStates_pb2 import WidgetStates
    editor = next(d for d in at.dataframe if (d.key or "").startswith("batch_editor_"))
    ws = WidgetStates()
    ws.widgets.extend(w for w in at._tree.get_widget_states().widgets if w.id != editor.proto.id)
    ws.widgets.add(id=editor.proto.id, string_value=json.dumps({"edited_rows": {"0": {"compound": "gamma", "charge": -1}}}))
    at._run(ws)
    assert not at.exception
    at.file_uploader(key="batch_files").set_value([("two.xyz", TWO.encode(), "text/plain"),
                                                   ("one.xyz", _named("delta").encode(), "text/plain")]).run()
    assert not at.exception
    rows = at.session_state["batch_rows"]
    assert [(r["compound"], r["charge"], r["structure"]) for r in rows] == [
        ("gamma", -1, "two.xyz#1"), ("beta", None, "two.xyz#2"), ("delta", None, "one.xyz")]


def test_gui_batch_preview_then_create_makes_unapproved_drafts(proj, monkeypatch):
    from mossbauer_dft import project as J
    at = _batch_app(monkeypatch, proj)
    _fill(at)
    assert not _can_create(at)  # nothing to create before a preview
    _btn(at, "Preview batch").click().run()
    assert not at.exception
    assert set(_outcomes(at)) <= {"ready", "ready-open-items"} and len(_outcomes(at)) == 2
    assert J.list_entries(proj) == []  # preview writes nothing
    next(b for b in at.button if b.label.startswith("Create ")).click().run()
    assert not at.exception
    assert sorted(e.display_name for e in J.list_entries(proj)) == ["alpha", "beta"]
    assert all(not v.approved for e in J.scan_project(proj) for v in e.versions)
    assert set(_outcomes(at)) == {"created"} and "not approved" in _texts(at)
    assert not _can_create(at)  # hidden until the next preview


def test_gui_batch_create_failure_lists_the_entries_kept(proj, monkeypatch):
    from mossbauer_dft import project as J
    at = _batch_app(monkeypatch, proj)
    _fill(at)
    _btn(at, "Preview batch").click().run()
    real = J.add_entry
    calls = []

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(*a, **kw)
    monkeypatch.setattr(J, "add_entry", flaky)
    _btn(at, "Create 2 draft entries").click().run()
    assert not at.exception
    (e,) = J.list_entries(proj)
    err = " ".join(x.value for x in at.error)
    assert "stopped after creating 1 entry" in err and e.entry_id in err and not _can_create(at)


def test_gui_batch_edit_after_preview_hides_create(proj, monkeypatch):
    at = _batch_app(monkeypatch, proj)
    rows = _fill(at)
    _btn(at, "Preview batch").click().run()
    assert _can_create(at)
    at.session_state["batch_rows"] = [dict(rows[0], compound="gamma"), rows[1]]
    at.run()
    assert not _can_create(at) and "out of date" in _texts(at)
    _btn(at, "Preview batch").click().run()
    assert _can_create(at)


def test_gui_batch_csv_of_created_rows_previews_all_skipped(proj, monkeypatch):
    from mossbauer_dft import batch as B, project as J
    rows = [dict(B.EMPTY_ROW, structure=f"two.xyz#{i}", compound=n, charge=-1, multiplicity=4, oxidation_state=1)
            for i, n in ((1, "alpha"), (2, "beta"))]
    B.create(proj, rows, B.load_sources([("two.xyz", TWO)]), {})
    at = _batch_app(monkeypatch, proj)
    at.file_uploader(key="batch_csv").set_value(("sheet.csv", B.write_csv(rows, {}).encode(), "text/csv")).run()
    assert not at.exception and "replaces the 2 current rows" in _texts(at)
    _btn(at, "Replace 2 rows").click().run()
    assert not at.exception and at.session_state["batch_rows"] == rows
    _btn(at, "Preview batch").click().run()
    assert _outcomes(at) == ["skipped", "skipped"] and len(J.list_entries(proj)) == 2
    assert not _can_create(at)


def test_gui_batch_csv_keeps_rows_whose_structure_is_not_loaded(proj, monkeypatch):
    from mossbauer_dft import batch as B
    rows = [dict(B.EMPTY_ROW, structure="si.cif#2", compound="far", charge=0, multiplicity=5)]
    at = _app(monkeypatch, None, qp={"project": str(proj.resolve()), "page": "Batch intake"})
    at.file_uploader(key="batch_csv").set_value(("sheet.csv", B.write_csv(rows, {"nprocs": 8}).encode(), "text/csv")).run()
    assert not at.exception and at.session_state["batch_rows"] == rows  # no current rows: nothing to confirm
    assert "si.cif#2" in " ".join(w.value for w in at.warning)
    _btn(at, "Preview batch").click().run()
    assert _outcomes(at) == ["blocked"] and "differ from the ones recorded" in _texts(at)


def test_gui_batch_remove_row_drops_it_from_preview_and_csv(proj, monkeypatch):
    from mossbauer_dft import batch as B
    at = _batch_app(monkeypatch, proj)
    _fill(at)
    at.selectbox(key="batch_row_pick").set_value(0).run()
    _btn(at, "Remove row").click().run()
    assert not at.exception and [r["compound"] for r in at.session_state["batch_rows"]] == ["beta"]
    _btn(at, "Preview batch").click().run()
    assert list(_table(at, "compound", "outcome", "reason")["compound"]) == ["beta"]
    csv_text = B.write_csv(at.session_state["batch_rows"], {})  # what the CSV download serves (AppTest cannot read it)
    assert "beta" in csv_text and "alpha" not in csv_text
    _btn(at, "Duplicate row").click().run()
    assert [r["compound"] for r in at.session_state["batch_rows"]] == ["beta", "beta"]


def test_gui_batch_preset_fills_options_and_save_preset_appears(proj, monkeypatch):  # presets U3
    from mossbauer_dft import presets as PR
    PR.save(proj, "tpssh-hf", {"deq_functional": "TPSSh", "highfield": True})
    at = _batch_app(monkeypatch, proj)
    _fill(at)
    at.selectbox(key="bo_preset_pick").set_value("tpssh-hf").run()
    assert not at.exception and at.session_state["bo_deq_functional"] == "TPSSh" and at.session_state["bo_highfield"] is True
    _btn(at, "Preview batch").click().run()
    next(b for b in at.button if b.label.startswith("Create ")).click().run()
    from mossbauer_dft import project as J
    ov = J.load_version(proj, J.list_entries(proj)[0].entry_id)["inputs"]["overrides"]
    assert ov == {"deq_functional": "TPSSh", "highfield": True}
    at.text_input(key="bo_preset_name").input("copy").run()
    _btn(at, "Save preset").click().run()
    assert not at.exception and "copy" in [p["name"] for p in PR.list_presets(proj)]


def test_gui_batch_refuses_preset_it_cannot_show(proj, monkeypatch):
    from mossbauer_dft import presets as PR
    PR.save(proj, "asgiven", {"geometry": {"mode": "as_given", "as_given_label": "BP86/def2-TZVP"}})
    at = _batch_app(monkeypatch, proj)
    at.selectbox(key="bo_preset_pick").set_value("asgiven").run()
    assert not at.exception and "cannot show" in _texts(at) and not at.session_state["bo_geometry"]


def test_gui_review_apply_preset_goes_through_preview(proj, fe1, monkeypatch):
    from mossbauer_dft import presets as PR, project as J
    ref = J.add_entry(proj, fe1, -1, 4, name="cmpd", oxidation_state=1, overrides={"delta_functional": "B3LYP"})
    PR.save(proj, "tpssh", {"deq_functional": "TPSSh"})
    at = _app(monkeypatch, None, qp={"project": str(proj.resolve()), "entry": ref.entry_id, "version": "1", "page": "Plan review"})
    _btn(at, "Preview preset").click().run()
    assert not at.exception and len(J.list_versions(proj, ref.entry_id)) == 1
    _btn(at, "Save reviewed changes").click().run()
    assert not at.exception
    ov = J.load_version(proj, ref.entry_id, 2)["inputs"]["overrides"]
    assert ov.get("deq_functional") == "TPSSh" and not ov.get("delta_functional")
    assert J.load_version(proj, ref.entry_id, 2).get("approval") is None


def test_gui_new_prediction_preset_applies_only_to_a_new_entry(proj, monkeypatch):
    from mossbauer_dft import presets as PR, project as J
    PR.save(proj, "hf", {"highfield": True})
    at = _app(monkeypatch, proj)
    at.sidebar.radio[0].set_value("New prediction").run()
    at.session_state["source"] = "Paste XYZ"
    at.run()
    at.text_area(key="xyz_text").input(XYZ).run()
    at.button(key="use_xyz").click().run()
    at.number_input(key="charge").set_value(-1).run()
    at.number_input(key="mult").set_value(4).run()
    at.text_input(key="name").input("cmpd").run()
    at.selectbox(key="ox").set_value(1).run()
    at.selectbox(key="new_preset").set_value("hf").run()
    _btn(at, "Create plan").click().run()
    assert not at.exception
    (e,) = J.list_entries(proj)
    assert J.load_version(proj, e.entry_id)["inputs"]["overrides"] == {"highfield": True}
    assert at.selectbox(key="new_preset").disabled  # the next Create would add a version: apply it on Plan review instead


def test_gui_review_renders_after_preset_cleared_calibration_strategy(proj, fe1, monkeypatch):  # review P1
    from mossbauer_dft import presets as PR, project as J, revision
    ref = J.add_entry(proj, fe1, -1, 4, name="cmpd", oxidation_state=1, overrides={"calibration_strategy": "compute_refset"})
    PR.save(proj, "hf", {"highfield": True})
    revision.save(proj, PR.preview_apply(proj, ref.entry_id, "hf"))
    at = _app(monkeypatch, None, qp={"project": str(proj.resolve()), "entry": ref.entry_id, "version": "2", "page": "Plan review"})
    assert not at.exception


def test_gui_review_preset_that_cannot_apply_shows_error(proj, fe1, monkeypatch):  # review P2
    from mossbauer_dft import presets as PR, project as J
    ref = J.add_entry(proj, fe1, -1, 4, name="cmpd", oxidation_state=1)
    PR.save(proj, "strict", {"calibration_strategy": "lookup"})
    at = _app(monkeypatch, None, qp={"project": str(proj.resolve()), "entry": ref.entry_id, "version": "1", "page": "Plan review"})
    _btn(at, "Preview preset").click().run()
    assert not at.exception and any("cannot be applied" in e.value for e in at.error)


def test_gui_damaged_presets_file_warns_instead_of_breaking(proj, monkeypatch):
    (proj / "presets.json").write_text("{not json")
    at = _app(monkeypatch, None, qp={"project": str(proj.resolve()), "page": "Batch intake"})
    assert not at.exception
    at.file_uploader(key="batch_files").set_value([("two.xyz", TWO.encode(), "text/plain")]).run()
    assert not at.exception and any("presets.json" in w.value for w in at.warning)

def test_gui_results_show_status_and_trial_leaves_saved_analysis(proj, fe1, monkeypatch):
    from mossbauer_dft import project as J
    ref = _analysis_ready(proj, fe1)
    J.analyze_version(proj, ref.entry_id)
    saved = (ref.path / "results" / "analysis.json").read_bytes()
    qp = {"project": str(proj.resolve()), "entry": ref.entry_id, "version": "1", "page": "Results"}
    at = _app(monkeypatch, None, qp=qp)
    assert "uncalibrated" in _texts(at)
    at.text_input(key="trial_cal").input(CAL).run()
    _btn(at, "Run trial").click().run()
    assert not at.exception and "Trial — not saved" in _texts(at) and "exploratory" in _texts(at)
    assert (ref.path / "results" / "analysis.json").read_bytes() == saved


def test_project_folder_browser_selects_existing_project(proj, monkeypatch):
    at = _app(monkeypatch)
    at.text_input(key='proj_path').input(str(proj.parent)).run()
    at.button(key='browse_proj').click().run()
    assert not at.exception
    picker = next(s for s in at.selectbox if s.label == 'Subfolder')
    picker.select(proj.name).run()
    at.button(key='browse_descend').click().run()
    _btn(at, 'Use this folder').click().run()
    assert not at.exception
    assert at.text_input(key='proj_path').value == str(proj.resolve())
    assert at.session_state['project'] == str(proj.resolve())


def test_project_folder_browser_cancel_and_empty_folder(tmp_path, monkeypatch):
    from mossbauer_dft import project as J
    empty = tmp_path / 'empty'; empty.mkdir()
    at = _app(monkeypatch)
    at.text_input(key='proj_path').input(str(empty)).run()
    at.button(key='browse_proj').click().run()
    _btn(at, 'Cancel').click().run()
    assert not at.exception and not J.is_project(empty)
    assert at.text_input(key='proj_path').value == str(empty)
    at.button(key='browse_proj').click().run()
    _btn(at, 'Use this folder').click().run()
    assert not at.exception and not J.is_project(empty)
    assert any(b.label == 'Create project here' for b in at.button)


def test_gui_guided_example_from_empty_folder_shows_guide(tmp_path, monkeypatch):
    from mossbauer_dft import project as J
    wd = tmp_path / "class"
    wd.mkdir()
    at = _app(monkeypatch, wd)
    _btn(at, "Create the guided example here").click().run()
    assert not at.exception and J.open_project(wd)["example"] == "iron-basics"
    assert len(_overview(at)) == 3 and all(_overview(at)["status"] == "draft")
    texts = _texts(at)
    assert "Review and approve plans" in texts and "← next" in texts and "EXAMPLE.md" in texts
    assert any(e.label.startswith("Guide: next, review and approve") for e in at.expander)


def test_gui_fetches_remote_compact_results_with_the_shared_backend(proj, fe1, monkeypatch, tmp_path):
    from mossbauer_dft import harvest, remote
    from mossbauer_dft import project as J
    ref = _analysis_ready(proj, fe1)
    doc = harvest.harvest(ref.path / 'inputs/harvest-manifest.json', ref.path / 'outputs')
    for p in (ref.path / 'outputs').glob('*.out'):
        p.unlink()
    calls = []
    def download(command, destination):
        calls.append(command)
        destination.write_text(json.dumps(doc))
    monkeypatch.setattr(remote, '_download', download)
    at = _app(monkeypatch, qp={'project': str(proj), 'entry': ref.entry_id, 'version': '1', 'page': 'Results'})
    assert at.button(key='fetch_results').disabled
    at.text_input(key='return_host').input('test-cluster').run()
    at.text_input(key='return_path').input('/work/results.json').run()
    at.button(key='fetch_results').click().run()
    assert not at.exception and 'Imported 2 jobs' in _texts(at)
    assert calls[0][-2] == 'test-cluster'
    assert len(list((ref.path / 'outputs').glob('*.compact.json'))) == 2
    at.text_input(key='return_host').input('-oProxyCommand=bad').run()
    at.button(key='fetch_results').click().run()
    assert not at.exception and 'Nothing imported' in _texts(at) and len(calls) == 1


def test_cluster_setup_creates_profile_and_cli_default(tmp_path, monkeypatch):
    from mossbauer_dft.site import SiteProfile, resolve_site
    at = _app(monkeypatch, qp={'page': 'Cluster setup'})
    assert any(h.value == 'Cluster setup' for h in at.header)
    at.text_input(key='cluster_name').input('personal')
    at.text_input(key='cluster_partition').input('compute')
    at.text_input(key='cluster_sif').input('/images/orca.sif')
    at.text_input(key='cluster_account').input('chem')
    _btn(at, 'Save profile').click().run()
    assert not at.exception
    assert SiteProfile.load('personal').account == 'chem'
    at.selectbox(key='cluster_default').set_value('personal').run()
    _btn(at, 'Use as default').click().run()
    assert resolve_site() == 'personal'
    assert not at.exception


def test_cluster_setup_rejects_invalid_and_stale_profile(tmp_path, monkeypatch):
    from mossbauer_dft.site import SiteProfile, save_profile
    at = _app(monkeypatch, qp={'page': 'Cluster setup'})
    assert any(h.value == 'Cluster setup' for h in at.header)
    at.text_input(key='cluster_name').input('../escape')
    _btn(at, 'Save profile').click().run()
    assert at.error and not at.exception
    p = save_profile(SiteProfile(name='personal', partition='compute', sif='/images/orca.sif'))
    at.run()
    at.selectbox(key='cluster_pick').set_value('personal').run()
    at.text_input(key='cluster_account').input('chem')
    _btn(at, 'Save profile').click().run()
    assert at.error and SiteProfile.load('personal').account is None
    at.checkbox(key='cluster_replace').check().run()
    p.write_text(p.read_text() + '# outside edit\n')
    _btn(at, 'Save profile').click().run()
    assert any('changed outside' in e.value for e in at.error)
    assert '# outside edit' in p.read_text()


def test_inputs_require_site_selection_without_default(proj, fe1, monkeypatch):
    from mossbauer_dft.data import user_home
    ref = _ready(proj, fe1)
    (user_home() / 'config.yaml').unlink()
    at = _app(monkeypatch, proj, qp={'page': 'Inputs', 'entry': ref.entry_id})
    assert at.selectbox(key='input_site_profile').value is None
    assert _btn(at, 'Generate inputs').disabled
    at.selectbox(key='input_site_profile').set_value('example').run()
    _btn(at, 'Generate inputs').click().run()
    assert not at.exception and (ref.path / 'inputs/submit.sh').is_file()


def test_cluster_setup_edits_existing_yml_profile(tmp_path, monkeypatch):
    from mossbauer_dft.site import SiteProfile, save_profile
    p = save_profile(SiteProfile(name='personal', partition='compute', sif='/images/orca.sif'))
    p.rename(p.with_suffix('.yml'))
    at = _app(monkeypatch, qp={'page': 'Cluster setup'})
    at.selectbox(key='cluster_pick').set_value('personal').run()
    at.text_input(key='cluster_account').input('chem')
    at.checkbox(key='cluster_replace').check()
    _btn(at, 'Save profile').click().run()
    assert not at.exception
    assert SiteProfile.load('personal').account == 'chem'
    assert p.with_suffix('.yml').is_file() and not p.exists()


def test_inputs_manual_download_and_optional_agent_handoff(proj, fe1, monkeypatch):
    ref = _ready(proj, fe1)
    at = _app(monkeypatch, proj, qp={'page': 'Inputs', 'entry': ref.entry_id})
    assert not at.checkbox(key='inputs_agent').value
    assert any(b.label == 'Download calculation files' for b in at.get('download_button'))
    at.checkbox(key='inputs_agent').check().run()
    assert not at.exception
    assert any('Calculation handoff' in c.value for c in at.code)
    deck = next((ref.path / 'inputs').glob('*.inp'))
    deck.write_text('changed')
    at.run()
    assert any('regenerate inputs' in e.value for e in at.error)
    assert not any(b.label == 'Download calculation files' for b in at.get('download_button'))


def test_inputs_missing_transfer_settings_keeps_manual_download(proj, fe1, monkeypatch):
    ref = _ready(proj, fe1)
    at = _app(monkeypatch, proj, qp={'page': 'Inputs', 'entry': ref.entry_id})
    at.checkbox(key='inputs_upload').check().run()
    assert not at.exception
    assert any('without the upload helper' in w.value for w in at.warning)
    assert any(b.label == 'Download calculation files' for b in at.get('download_button'))
