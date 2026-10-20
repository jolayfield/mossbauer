"""Batch intake library: rows, CSV, preview and create (U3: R5-R16, R19-R25, R27-R28; AE3-AE6, AE9-AE13)."""
import json

import pytest

from mossbauer_dft import batch as B
from mossbauer_dft import families as F
from mossbauer_dft import project as J
from test_plan import XYZ, _home  # noqa: F401
from test_project_status import proj  # noqa: F401


def _blocks(names):
    body = XYZ.strip("\n").split("\n")
    return "\n".join("\n".join([body[0], n] + body[2:]) for n in names) + "\n"


def _src(names, filename="si.xyz"):
    return B.load_sources([(filename, _blocks(names))])


def _fill(rows, charge=-1, mult=4, **kw):
    for r in rows:
        r.update(charge=charge, multiplicity=mult, oxidation_state=1, **kw)
    return rows


def _outcomes(pv):
    return [r["outcome"] for r in pv["rows"]]


def test_two_states_of_one_structure_become_linked_entries(proj):  # AE3
    src = _src(["FeA"], "fe.xyz")
    rows = _fill(B.rows_from_sources(src))
    rows.append(dict(rows[0], multiplicity=2))
    res = B.create(proj, rows, src, {})
    ids = [r["entries"][0] for r in res["rows"]]
    assert [r["outcome"] for r in res["rows"]] == ["created", "created"] and len(set(ids)) == 2
    fam = F.read(proj)["families"]
    assert list(fam) == ["FeA"] and set(fam["FeA"]["entries"]) == set(ids)


def test_preview_blocks_bad_rows_and_create_makes_drafts_only(proj):  # AE4
    src = _src([f"Fe{i}" for i in range(10)])
    rows = _fill(B.rows_from_sources(src))
    rows[3]["multiplicity"] = 3  # parity mismatch for this 7-atom anion
    rows[7]["structure"] = "missing.xyz"
    pv = B.preview(proj, rows, src, {})
    assert _outcomes(pv).count("blocked") == 2 and "parity" in pv["rows"][3]["reason"] and "not found" in pv["rows"][7]["reason"]
    assert pv["rows"][0]["stats"]["n_atoms"] == 7 and pv["rows"][0]["stats"]["n_fe"] == 1
    res = B.create(proj, rows, src, {}, expected_hash=pv["preview_hash"])
    made = [r for r in res["rows"] if r["outcome"] == "created"]
    assert len(made) == 8 and len(J.list_entries(proj)) == 8
    assert all(J.load_version(proj, r["entries"][0]).get("approval") is None for r in made)


def test_rerun_skips_rows_already_in_project_without_suffix_ids(proj):  # AE5, R15
    src = _src(["FeA", "FeB"])
    rows = _fill(B.rows_from_sources(src))
    B.create(proj, rows, src, {})
    pv = B.preview(proj, rows, src, {})
    assert _outcomes(pv) == ["skipped", "skipped"] and pv["rows"][0]["entries"]
    res = B.create(proj, rows, src, {})
    assert [r["outcome"] for r in res["rows"]] == ["skipped", "skipped"]
    assert not [e for e in J.list_entries(proj) if e.entry_id.endswith("_2")] and len(J.list_entries(proj)) == 2


def test_name_match_ignores_case_and_spacing(proj):
    src = _src(["Fe(TPP)Cl"])
    B.create(proj, _fill(B.rows_from_sources(src)), src, {})
    rows = _fill(B.rows_from_sources(src))
    rows[0]["compound"] = "  fe(tpp)cl  "
    assert _outcomes(B.preview(proj, rows, src, {})) == ["skipped"]


def test_duplicates_and_shared_names_in_batch_are_blocked(proj):  # AE6
    src = _src(["FeA", "FeB"])
    rows = _fill(B.rows_from_sources(src))
    dup = rows + [dict(rows[0])]
    assert _outcomes(B.preview(proj, dup, src, {})) == ["blocked", "ready", "blocked"]
    rows[1]["compound"] = "FeA"
    rows[1]["multiplicity"] = 2
    pv = B.preview(proj, rows, src, {})
    assert _outcomes(pv) == ["blocked", "blocked"] and "different structures" in pv["rows"][0]["reason"]


def test_placeholder_name_is_blocked_after_csv_round_trip(proj):  # AE12
    src = _src([""])
    rows = _fill(B.rows_from_sources(src))
    assert rows[0]["compound"].startswith("unnamed-")
    back, opts = B.read_csv(B.write_csv(rows, {}))
    pv = B.preview(proj, back, src, opts)
    assert _outcomes(pv) == ["blocked"] and "not filled in" in pv["rows"][0]["reason"]


def test_renamed_row_round_trips_through_csv(proj):  # AE9
    src = _src(["I"], "paper.xyz")
    rows = _fill(B.rows_from_sources(src))
    rows[0]["compound"] = "Fe(TPP)Cl"
    back, opts = B.read_csv(B.write_csv(rows, {"highfield": True}))
    assert back[0]["structure"] == "paper.xyz" and back[0]["compound"] == "Fe(TPP)Cl" and opts == {"highfield": True}
    assert _outcomes(B.preview(proj, back, src, opts)) == ["ready"]


def test_spreadsheet_resaved_csv_still_imports():  # R28
    text = B.write_csv(_fill([dict(B.EMPTY_ROW, structure="si.xyz#1", compound="FeA")]), {"deq_functional": "TPSSh", "highfield": True})
    lines = text.splitlines()
    resaved = "﻿" + "\n".join(ln + ",," for ln in lines) + "\n"
    rows, opts = B.read_csv(resaved)
    assert opts == {"deq_functional": "TPSSh", "highfield": True} and rows[0]["compound"] == "FeA" and rows[0]["charge"] == -1


def test_unknown_csv_column_refuses_the_file():
    with pytest.raises(J.ProjectError, match="unknown column 'colour'"):
        B.read_csv("structure,compound,colour\nsi.xyz,FeA,red\n")


def test_options_mismatch_and_broken_symmetry_option(proj):  # R21, R6
    src = _src(["FeA"])
    rows = _fill(B.rows_from_sources(src))
    pv = B.preview(proj, rows, src, {"highfield": True}, recorded_options={})
    assert any("differ" in w for w in pv["warnings"])
    with pytest.raises(J.ProjectError, match="broken symmetry"):
        B.preview(proj, rows, src, {"broken_symmetry": {"kind": "flipspin", "flip_atoms": [0], "final_ms": 0.5}})


def test_bs_rule_row_is_ready_with_open_items_naming_states_flow(proj):  # AE11
    src = _src(["FeR"])
    rows = _fill(B.rows_from_sources(src), radical_ligand=True)
    row = B.preview(proj, rows, src, {})["rows"][0]
    assert row["outcome"] == "ready-open-items" and any("States" in n for n in row["notes"])


def test_two_pastes_get_distinct_references_and_separate_families(proj):  # AE13, R27
    src = B.load_sources([("pasted.xyz", _blocks(["FeA"])), ("pasted.xyz", _blocks(["FeB"]))])
    assert sorted(src.structures) == ["pasted-2.xyz", "pasted.xyz"]
    rows = _fill(B.rows_from_sources(src))
    rows += [dict(r, multiplicity=2) for r in rows]
    B.create(proj, rows, src, {})
    fams = F.read(proj)["families"]
    assert sorted(fams) == ["FeA", "FeB"] and all(len(f["entries"]) == 2 for f in fams.values())


def test_interrupted_run_is_linked_on_rerun(proj, monkeypatch):  # AE10, R24
    src = _src(["FeA"])
    rows = _fill(B.rows_from_sources(src))
    rows.append(dict(rows[0], multiplicity=2))
    monkeypatch.setattr(B.F, "link", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        B.create(proj, rows, src, {})
    monkeypatch.undo()
    assert F.read(proj)["families"] == {} and len(J.list_entries(proj)) == 2
    res = B.create(proj, rows, src, {})
    assert [r["outcome"] for r in res["rows"]] == ["skipped", "skipped"]
    assert len(F.read(proj)["families"]["FeA"]["entries"]) == 2


def test_failure_mid_create_reports_created_entries(proj, monkeypatch):  # R13
    src = _src(["FeA", "FeB"])
    rows = _fill(B.rows_from_sources(src))
    real, calls = J.add_entry, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(*a, **k)
    monkeypatch.setattr(B.J, "add_entry", flaky)
    with pytest.raises(B.BatchError) as e:
        B.create(proj, rows, src, {})
    assert len(e.value.created) == 1 and "disk full" in str(e.value)


def test_entry_added_after_preview_is_skipped_and_stale_hash_refused(proj):  # R23, KTD7
    src = _src(["FeA"])
    rows = _fill(B.rows_from_sources(src))
    pv = B.preview(proj, rows, src, {})
    J.add_entry(proj, src.structures["si.xyz"].structure, -1, 4, name="FeA", oxidation_state=1)
    with pytest.raises(J.ProjectError, match="preview again"):
        B.create(proj, rows, src, {}, expected_hash=pv["preview_hash"])
    assert [r["outcome"] for r in B.create(proj, rows, src, {})["rows"]] == ["skipped"]


def test_multi_fe_with_single_experimental_value_warns(proj):  # R25
    two_fe = XYZ.replace("7\n", "8\n", 1).rstrip("\n") + "\nFe 5.0 5.0 5.0\n"
    src = B.load_sources([("di.xyz", two_fe)])
    rows = _fill(B.rows_from_sources(src), exp_delta=0.45)
    row = B.preview(proj, rows, src, {})["rows"][0]
    assert any("Fe" in w and "single" in w for w in row["warnings"])


def test_batch_plans_match_one_at_a_time_plans(proj):  # R20
    src = _src(["FeA"])
    rows = _fill(B.rows_from_sources(src), solvent="water", temperature_K=80.0, exp_delta=0.5, radical_ligand=False)
    eid = B.create(proj, rows, src, {"highfield": True, "deq_functional": "TPSSh"})["rows"][0]["entries"][0]
    single = J.add_entry(proj, src.structures["si.xyz"].structure, -1, 4, name="single", oxidation_state=1, radical_ligand=False,
                         solvent="water", temperature_K=80.0, experimental={"delta": 0.5, "T_K": 80.0},
                         overrides={"highfield": True, "deq_functional": "TPSSh"}, structure_filename="si.xyz")
    a, b = J.load_version(proj, eid)["inputs"], J.load_version(proj, single.entry_id)["inputs"]
    assert {k: a[k] for k in ("hints", "experimental", "overrides", "charge", "multiplicity", "structure_file")} == \
           {k: b[k] for k in ("hints", "experimental", "overrides", "charge", "multiplicity", "structure_file")}


def test_family_link_extends_suffixes_and_reports_ambiguity(proj):
    src = _src(["A", "B", "C", "D"])
    ids = [r["entries"][0] for r in B.create(proj, _fill(B.rows_from_sources(src)), src, {})["rows"]]
    F.create(proj, "fam", [ids[0]])
    assert F.link(proj, "other", [ids[0], ids[1]])["family"] == "fam"
    F.create(proj, "taken", [ids[2]])
    assert F.link(proj, "taken", [ids[3]])["family"] == "taken-2"
    out = F.link(proj, "x", [ids[0], ids[2]])
    assert out["status"] == "ambiguous" and sorted(out["families"]) == ["fam", "taken"]


def test_batch_module_needs_no_gui_dependencies():
    import subprocess
    import sys
    code = ("import sys; sys.modules['streamlit']=None; sys.modules['pandas']=None; sys.modules['rdkit']=None\n"
            "import mossbauer_dft.batch")
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


def test_json_is_serializable(proj):
    src = _src(["FeA"])
    assert json.dumps(B.preview(proj, _fill(B.rows_from_sources(src)), src, {}))


def test_malformed_csv_number_is_kept_and_blocks_its_row(proj):
    src = _src(["FeA"])
    rows = _fill(B.rows_from_sources(src))
    back, _ = B.read_csv(B.write_csv(rows, {}).replace(",-1,4,", ",minus one,4,"))
    assert back[0]["charge"] == "minus one"
    row = B.preview(proj, back, src, {})["rows"][0]
    assert row["outcome"] == "blocked" and "charge must be an integer" in row["reason"]


def test_preview_hash_pins_the_geometry_not_just_the_rows(proj):  # review #1
    src = _src(["FeA"])
    rows = _fill(B.rows_from_sources(src))
    pv = B.preview(proj, rows, src, {})
    moved = B.load_sources([("si.xyz", _blocks(["FeA"]).replace("Fe 0.0 0.0 0.0", "Fe 0.0 0.0 0.2"))])
    with pytest.raises(J.ProjectError, match="preview again"):
        B.create(proj, rows, moved, {}, expected_hash=pv["preview_hash"])
    assert J.list_entries(proj) == []


def test_link_failure_after_create_still_reports_created_entries(proj):  # review #4
    src = _src(["FeA"])
    rows = _fill(B.rows_from_sources(src))
    rows.append(dict(rows[0], multiplicity=2))
    (proj / "families.json").write_text("{not json")
    with pytest.raises(B.BatchError, match="linking families failed") as e:
        B.create(proj, rows, src, {})
    assert len(e.value.created) == 2 and len(J.list_entries(proj)) == 2


def test_existing_entry_with_other_geometry_is_not_linked(proj):  # review #5
    other = B.load_sources([("old.xyz", _blocks(["FeA"]).replace("Fe 0.0 0.0 0.0", "Fe 0.0 0.0 0.3"))])
    B.create(proj, _fill(B.rows_from_sources(other)), other, {})
    src = _src(["FeA"])
    rows = _fill(B.rows_from_sources(src))
    rows.append(dict(rows[0], multiplicity=2))
    res = B.create(proj, rows, src, {})
    assert [r["outcome"] for r in res["rows"]] == ["skipped", "created"]
    assert any("different geometry" in n for n in res["rows"][0]["notes"]) and F.read(proj)["families"] == {}


def test_entry_written_by_another_writer_mid_run_is_skipped(proj, monkeypatch):  # R23 re-check inside create
    src = _src(["FeA", "FeB"])
    rows = _fill(B.rows_from_sources(src))
    real = J.add_entry

    def first_call_also_adds_feb(*a, **k):
        monkeypatch.setattr(B.J, "add_entry", real)
        real(proj, src.structures["si.xyz#2"].structure, -1, 4, name="FeB", oxidation_state=1)
        return real(*a, **k)
    monkeypatch.setattr(B.J, "add_entry", first_call_also_adds_feb)
    res = B.create(proj, rows, src, {})
    assert [r["outcome"] for r in res["rows"]] == ["created", "skipped"]
    assert not [e for e in J.list_entries(proj) if e.entry_id.endswith("_2")]


def test_unreadable_source_becomes_a_note_and_others_load(monkeypatch):
    src = B.load_sources([("bad.xyz", "x\nnot xyz\n"), ("good.xyz", XYZ)])
    assert list(src.structures) == ["good.xyz"] and any("bad.xyz" in n and "could not be read" in n for n in src.notes)
    monkeypatch.setattr(B.SS, "find_structures", lambda *a: (_ for _ in ()).throw(ImportError("rdkit")))
    assert any("needs RDKit" in n for n in B.load_sources([("set.sdf", "")]).notes)
