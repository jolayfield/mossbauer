import json
import shutil
from pathlib import Path

import pytest
import yaml

from mossbauer_dft import plan as P
from mossbauer_dft import project as J
from mossbauer_dft import workflow as W
from mossbauer_dft.structure import Structure
from test_plan import XYZ, _home  # noqa: F401


@pytest.fixture
def fe1():
    return Structure.from_xyz_text(XYZ)


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "study"
    J.create_project(root, name="Fe study")
    return root


def _add(root, s, mult=4, name="cmpd", **kw):
    return J.add_entry(root, s, -1, mult, name=name, oxidation_state=1, **kw)


def _tree_bytes(d):
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_approved_export_roundtrip_with_windows_text_encoding(tmp_path, fe1, windows_text_encoding):
    from mossbauer_dft import handoff as H
    import io
    import zipfile

    root = tmp_path / "study"
    fe1.comment += " Ł"
    J.create_project(root, name="Ł study")
    assert J.open_project(root)["name"] == "Ł study"
    ref = _add(root, fe1)
    J.approve_versions(root, [ref.entry_id], "Ł researcher", "cli")
    J.write_version_inputs(root, ref.entry_id, 1, site="example")
    plan = J.load_version(root, ref.entry_id, 1)
    assert P.plan_integrity(plan) and plan["approval"]["by"] == "Ł researcher"
    with zipfile.ZipFile(io.BytesIO(H.export_inputs(root, ref.entry_id, 1, agent=True))) as archive:
        assert "Ł researcher" in archive.read("README_inputs.txt").decode("utf-8")
        assert "Ł" in archive.read("structure.xyz").decode("utf-8")
        assert b"\r" not in archive.read("submit.sh")


def test_create_project_writes_marker_and_readme_and_reopens_empty(tmp_path):
    root = tmp_path / "new"
    J.create_project(root)
    assert (root / "project.yaml").is_file() and (root / "README.md").is_file()
    meta = J.open_project(root)
    assert meta["schema_version"] == J.SCHEMA and meta["name"] == "new"
    assert "outputs/" in (root / "README.md").read_text()
    assert J.list_entries(root) == []


def test_generated_readme_tells_agents_where_outputs_go_and_how_to_approve(tmp_path):
    root = tmp_path / "new"
    J.create_project(root)
    text = (root / "README.md").read_text()
    assert "mossbauer project status --json" in text
    assert "--via agent --on-behalf-of" in text
    assert "move" in text and "inputs/" in text and "outputs/" in text      # cluster copy-back lands in inputs/
    assert "slurm-*.out" in text
    assert "docs/project-layout.md" in text


def test_create_refuses_existing_project_and_open_refuses_plain_folder(tmp_path, proj):
    with pytest.raises(J.ProjectError):
        J.create_project(proj)
    (tmp_path / "plain").mkdir()
    with pytest.raises(J.NotAProjectError):
        J.open_project(tmp_path / "plain")


def test_add_entry_creates_v1_and_name_is_entry_id(proj, fe1):
    ref = _add(proj, fe1)
    assert ref.entry_id == "cmpd_qm1_m4" and ref.version == 1
    v1 = proj / "entries" / "cmpd_qm1_m4" / "v1"
    assert ref.path == v1 and (v1 / "plan.yaml").is_file() and (v1 / "structure.xyz").is_file() and (v1 / "outputs").is_dir()
    plan = P.load_plan(v1 / "plan.yaml")
    assert plan["inputs"]["name"] == ref.entry_id and plan["status"] == "draft" and P.plan_integrity(plan)
    assert all(j["stem"].startswith("cmpd_qm1_m4_") for j in plan["jobs"])
    e = yaml.safe_load((proj / "entries" / ref.entry_id / "entry.yaml").read_text())
    assert e == {"schema_version": J.SCHEMA, "name": "cmpd", "state": "q-1 m4", "last_version": 1}
    assert b"\r" not in (v1 / "structure.xyz").read_bytes()


def test_same_compound_other_multiplicity_is_separate_entry_without_stem_collision(proj, fe1):
    a, b = _add(proj, fe1, 4), _add(proj, fe1, 2)
    assert a.entry_id != b.entry_id and b.entry_id == "cmpd_qm1_m2"
    stems = [{j["stem"] for j in J.load_version(proj, r.entry_id)["jobs"]} for r in (a, b)]
    assert not stems[0] & stems[1]
    assert [e.entry_id for e in J.list_entries(proj)] == ["cmpd_qm1_m2", "cmpd_qm1_m4"]


def test_same_state_twice_gets_unique_entry_id(proj, fe1):
    a, b = _add(proj, fe1), _add(proj, fe1)
    assert a.entry_id == "cmpd_qm1_m4" and b.entry_id == "cmpd_qm1_m4_2"
    assert J.load_version(proj, b.entry_id)["inputs"]["name"] == "cmpd_qm1_m4_2"


def test_edit_creates_v2_draft_and_leaves_v1_byte_identical(proj, fe1):
    ref = _add(proj, fe1)
    approved = P.approve_plan(J.load_version(proj, ref.entry_id), "tester")
    P.save_plan(approved, ref.path / "plan.yaml")
    before = _tree_bytes(ref.path)
    r2 = J.edit_entry(proj, ref.entry_id, overrides={"deq_functional": "TPSSh"})
    assert (r2.entry_id, r2.version) == (ref.entry_id, 2)
    assert _tree_bytes(ref.path) == before
    v2 = J.load_version(proj, ref.entry_id)
    assert v2["status"] == "draft" and v2["approval"] is None and v2["methods"]["deq"]["functional"] == "TPSSh"
    assert v2["inputs"]["name"] == ref.entry_id
    assert list((r2.path / "outputs").iterdir()) == [] and not (r2.path / "inputs").exists()
    assert [v.version for v in J.list_versions(proj, ref.entry_id)] == [1, 2]
    assert J.load_version(proj, ref.entry_id, 1)["status"] == "approved"


def test_multiplicity_edit_creates_new_entry_and_leaves_original_unchanged(proj, fe1):
    ref = _add(proj, fe1)
    entry_dir = proj / "entries" / ref.entry_id
    before = _tree_bytes(entry_dir)
    with pytest.raises(J.StateChangeError):
        J.new_version(proj, ref.entry_id, multiplicity=2)
    r = J.edit_entry(proj, ref.entry_id, multiplicity=2)
    assert r.entry_id == "cmpd_qm1_m2" and r.version == 1
    assert _tree_bytes(entry_dir) == before
    new = J.load_version(proj, r.entry_id)
    assert new["inputs"]["multiplicity"] == 2 and new["inputs"]["name"] == r.entry_id
    assert new["inputs"]["hints"]["oxidation_state"] == 1
    assert yaml.safe_load((proj / "entries" / r.entry_id / "entry.yaml").read_text())["name"] == "cmpd"


def test_version_numbers_never_reused_after_manual_delete(proj, fe1):
    ref = _add(proj, fe1)
    r2 = J.new_version(proj, ref.entry_id, overrides={"deq_functional": "TPSSh"})
    shutil.rmtree(r2.path)
    r3 = J.new_version(proj, ref.entry_id, overrides={"deq_functional": "TPSSh"})
    assert r3.version == 3 and [v.version for v in J.list_versions(proj, ref.entry_id)] == [1, 3]


def test_copied_project_opens_with_same_entries_versions_and_hashes(tmp_path, proj, fe1):
    a = _add(proj, fe1)
    _add(proj, fe1, 2)
    J.new_version(proj, a.entry_id, overrides={"deq_functional": "TPSSh"})

    def snap(root):
        return [(e.entry_id, e.display_name, e.state_label,
                 [(v.version, v.plan["plan_hash"], P.compute_hash(v.plan)) for v in e.versions]) for e in J.list_entries(root)]
    before = snap(proj)
    moved = tmp_path / "elsewhere" / "copy"
    shutil.copytree(proj, moved)
    shutil.rmtree(proj)
    J.open_project(moved)
    s = snap(moved)
    assert s == before and len(s) == 2 and all(h == c for _, _, _, vs in s for _, h, c in vs)
    # no absolute location of the original folder is recorded anywhere inside the project
    for p in moved.rglob("*"):
        if p.is_file():
            assert str(tmp_path) not in p.read_text()


def test_corrupted_version_listed_as_unreadable_others_still_load(proj, fe1):
    a, b = _add(proj, fe1), _add(proj, fe1, 2)
    (a.path / "plan.yaml").write_text("schema: [unclosed\n  - :")
    entries = {e.entry_id: e for e in J.list_entries(proj)}
    bad = entries[a.entry_id].versions[0]
    assert bad.unreadable and bad.plan is None and bad.error
    good = entries[b.entry_id].versions[0]
    assert not good.unreadable and good.plan["inputs"]["multiplicity"] == 2


def _legacy(tmp_path, fe1, approve=True):
    wd = tmp_path / "legacy"
    plan = P.make_plan(fe1, -1, 4, name="cmpd", oxidation_state=1)
    if approve:
        plan = P.approve_plan(plan, "tester")
    pp = P.save_plan(plan, wd / "plan" / "plan.yaml")
    if approve:
        W.write_inputs(pp, wd / "inputs")
    (wd / "uploaded_outputs").mkdir()
    (wd / "uploaded_outputs" / "cmpd_opt.out").write_text("ORCA output\n")
    (wd / "uploaded_outputs" / "cmpd_sp_b3lyp.out").write_text("ORCA output\n")
    return wd


def test_import_legacy_copies_into_v1_and_keeps_originals(tmp_path, fe1):
    wd = _legacy(tmp_path, fe1)
    before = _tree_bytes(wd)
    with pytest.raises(J.LegacyWorkdirError):
        J.open_project(wd)
    ref = J.import_legacy(wd)
    assert all(_tree_bytes(wd)[k] == v for k, v in before.items())
    J.open_project(wd)
    entries = J.list_entries(wd)
    assert [(e.entry_id, [v.version for v in e.versions]) for e in entries] == [("cmpd", [1])]
    assert ref.path == wd / "entries" / "cmpd" / "v1"
    assert (ref.path / "plan.yaml").read_bytes() == before["plan/plan.yaml"]
    assert (ref.path / "structure.xyz").read_bytes() == before["plan/structure.xyz"]
    plan = J.load_version(wd, "cmpd")
    assert plan["inputs"]["name"] == "cmpd"
    P.require_approved(plan)
    stems = {j["stem"] for j in plan["jobs"]}
    outs = {p.name[:-4] for p in (ref.path / "outputs").glob("*.out")}
    assert outs == {"cmpd_opt", "cmpd_sp_b3lyp"} and outs <= stems
    man = json.loads((ref.path / "inputs" / "manifest.json").read_text())
    assert man["plan_hash"] == plan["approval"]["plan_hash"] and man["schema_version"] == J.SCHEMA
    assert sorted(man["expected_stems"]) == sorted(stems)
    assert "README_inputs.txt" in man["files"] and "manifest.json" not in man["files"]


def test_import_legacy_without_matching_readme_hash_writes_no_manifest(tmp_path, fe1):
    wd = _legacy(tmp_path, fe1)
    r = wd / "inputs" / "README_inputs.txt"
    r.write_text("Inputs for plan 000000000000 approved by x at y\n")
    ref = J.import_legacy(wd)
    assert (ref.path / "inputs" / "README_inputs.txt").is_file()
    assert not (ref.path / "inputs" / "manifest.json").exists()


def test_import_legacy_draft_writes_no_manifest(tmp_path, fe1):
    ref = J.import_legacy(_legacy(tmp_path, fe1, approve=False))
    assert not (ref.path / "inputs" / "manifest.json").exists()


def test_recent_projects_records_and_keeps_missing(tmp_path, proj):
    other = tmp_path / "gone"
    J.create_project(other)
    J.add_recent(proj)
    J.add_recent(other)
    J.add_recent(proj)
    shutil.rmtree(other)
    rec = J.recent_projects()
    assert [r["path"] for r in rec] == [str(proj.resolve()), str(other.resolve())]
    assert [r["exists"] for r in rec] == [True, False]
    assert (tmp_path / "home" / "recent_projects.json").is_file()


def test_project_files_hold_only_documented_fields(tmp_path, proj, fe1):
    ref = _add(proj, fe1)
    J.add_recent(proj)
    assert set(yaml.safe_load((proj / "project.yaml").read_text())) == {"schema_version", "name", "created"}
    assert set(yaml.safe_load((proj / "entries" / ref.entry_id / "entry.yaml").read_text())) == {"schema_version", "name", "state", "last_version"}
    rec = json.loads((tmp_path / "home" / "recent_projects.json").read_text())
    assert set(rec) == {"schema_version", "projects"} and all(set(p) == {"path", "opened"} for p in rec["projects"])
    wd = _legacy(tmp_path, fe1)
    r = J.import_legacy(wd)
    man = json.loads((r.path / "inputs" / "manifest.json").read_text())
    assert set(man) == {"schema_version", "plan_hash", "expected_stems", "files", "sha256"}
    assert sorted(p.name for p in proj.iterdir()) == ["README.md", "entries", "project.yaml"]


def test_remove_recent_drops_only_that_project(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    J.create_project(a)
    J.create_project(b)
    J.add_recent(a)
    J.add_recent(b)
    J.remove_recent(a)
    assert [Path(p["path"]) for p in J.recent_projects()] == [b.resolve()]


def test_add_version_stores_a_new_plan_for_same_state_and_refuses_state_change(proj, fe1):
    root = proj
    ref = _add(root, fe1)
    old = J.load_version(root, ref.entry_id)
    same = P.make_plan(Structure.from_xyz_text(XYZ), -1, 4, name=old["inputs"]["name"], oxidation_state=1)
    v2 = J.add_version(root, ref.entry_id, same)
    assert v2.version == 2 and J.load_version(root, ref.entry_id, 2)["status"] == "draft"
    other = P.make_plan(Structure.from_xyz_text(XYZ), -1, 2, name=old["inputs"]["name"], oxidation_state=1)
    with pytest.raises(J.StateChangeError):
        J.add_version(root, ref.entry_id, other)


def test_failed_legacy_import_leaves_no_entry_and_can_be_retried(tmp_path, fe1, monkeypatch):
    """Review #9: an import that fails mid-copy removes the entry it started, so a retry succeeds."""
    wd = _legacy(tmp_path, fe1)
    real = shutil.copytree

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(J.shutil, "copytree", boom)
    with pytest.raises(OSError):
        J.import_legacy(wd)
    assert not (wd / "entries" / "cmpd").exists()
    monkeypatch.setattr(J.shutil, "copytree", real)
    (wd / "entries" / "cmpd" / ".tmp-v1-dead").mkdir(parents=True)  # what a killed process leaves behind
    assert J.import_legacy(wd).version == 1


def test_replace_structure_adds_a_version_keeping_overrides(tmp_path, proj, fe1):
    ref = _add(proj, fe1, overrides={"nprocs": 8})
    moved = Structure(fe1.symbols, [[x + 0.01, y, z] for x, y, z in fe1.coords])
    x = tmp_path / "refined.xyz"
    x.write_text(moved.to_xyz_text())
    new = J.replace_structure(proj, ref.entry_id, x)
    plan = J.load_version(proj, ref.entry_id, new.version)
    assert (new.entry_id, new.version) == (ref.entry_id, 2) and plan["inputs"]["overrides"]["nprocs"] == 8
    assert plan["inputs"]["structure_file"] == "refined.xyz" and plan["_structure_xyz"] != J.load_version(proj, ref.entry_id, 1)["_structure_xyz"]


def test_edit_refuses_a_stale_base_version(proj, fe1):
    ref = _add(proj, fe1)
    J.new_version(proj, ref.entry_id, overrides={"nprocs": 8})
    with pytest.raises(J.ProjectError, match="v2 .* was written after v1"):
        J.edit_entry(proj, ref.entry_id, base_version=1, overrides={"nprocs": 4})
    assert J.edit_entry(proj, ref.entry_id, base_version=2, overrides={"nprocs": 4}).version == 3


def test_brokensym_spec_survives_edits_that_do_not_touch_it(proj, fe1):
    """Review #7 (project side): an edit without broken_symmetry keeps a kind=brokensym spec and the entry."""
    ref = _add(proj, fe1, overrides={"broken_symmetry": dict(kind="brokensym", na=3, nb=1)})
    new = J.edit_entry(proj, ref.entry_id, overrides={"nprocs": 4})
    assert (new.entry_id, new.version) == (ref.entry_id, 2)
    assert J.load_version(proj, ref.entry_id, 2)["inputs"]["overrides"]["broken_symmetry"]["kind"] == "brokensym"
