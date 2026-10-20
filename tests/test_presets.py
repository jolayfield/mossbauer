"""Method presets stored in the project (presets U1: R1-R4, R6, R8; AE2-AE5)."""
import json

import pytest

from mossbauer_dft import presets as PR
from mossbauer_dft import project as J
from test_plan import XYZ, _home  # noqa: F401
from test_project_status import fe1, proj  # noqa: F401


def test_save_list_get_delete(proj):
    PR.save(proj, "tpssh-hf", {"deq_functional": "TPSSh", "highfield": True}, description="group protocol")
    assert [p["name"] for p in PR.list_presets(proj)] == ["tpssh-hf"]
    assert PR.get(proj, "tpssh-hf")["options"] == {"deq_functional": "TPSSh", "highfield": True}
    doc = json.loads((proj / "presets.json").read_text())
    assert doc["schema_version"] == "1" and doc["presets"]["tpssh-hf"]["description"] == "group protocol"
    PR.delete(proj, "tpssh-hf")
    assert PR.list_presets(proj) == []


def test_project_without_presets_lists_none(proj):
    assert PR.list_presets(proj) == [] and not (proj / "presets.json").exists()


@pytest.mark.parametrize("key", ["broken_symmetry", "user_refs", "charge", "oxidation_state"])
def test_forbidden_keys_are_refused(proj, key):  # AE2
    with pytest.raises(J.ProjectError, match=key):
        PR.save(proj, "bad", {key: 1})
    assert not (proj / "presets.json").exists()


def test_existing_name_needs_replace(proj):  # AE4
    PR.save(proj, "std", {"highfield": True})
    with pytest.raises(J.ProjectError, match="already exists"):
        PR.save(proj, "std", {"nprocs": 8})
    assert PR.get(proj, "std")["options"] == {"highfield": True}
    PR.save(proj, "std", {"nprocs": 8}, replace=True)
    assert PR.get(proj, "std")["options"] == {"nprocs": 8}


@pytest.mark.parametrize("name", ["", "   ", "a" * 65, "a/b", "x\\y"])
def test_bad_names_are_refused(proj, name):
    with pytest.raises(J.ProjectError, match="name"):
        PR.save(proj, name, {"highfield": True})


def test_unknown_preset_lists_available(proj):
    PR.save(proj, "std", {"highfield": True})
    with pytest.raises(J.ProjectError, match="std"):
        PR.get(proj, "other")


def test_apply_edits_replaces_method_options_and_keeps_bs_and_refs():  # AE3 (library part)
    entry = {"delta_functional": "B3LYP", "highfield": True, "nprocs": 8, "broken_symmetry": {"kind": "brokensym", "na": 3, "nb": 1},
             "user_refs": [{"name": "r"}]}
    edit = PR.apply_edits(entry, {"deq_functional": "TPSSh", "nprocs": 8})
    assert edit == {"deq_functional": "TPSSh", "nprocs": 8, "delta_functional": "", "highfield": None}


def test_applying_a_preset_through_revision_keeps_bs_and_makes_a_draft(proj, fe1):  # AE3
    from mossbauer_dft import revision
    bs = {"kind": "brokensym", "na": 3, "nb": 1}
    ref = J.add_entry(proj, fe1, -1, 4, name="cmpd", oxidation_state=1,
                      overrides={"delta_functional": "B3LYP", "highfield": True, "broken_symmetry": bs})
    PR.save(proj, "tpssh", {"deq_functional": "TPSSh"})
    pv = PR.preview_apply(proj, ref.entry_id, "tpssh")
    fields = {c["field"] for c in pv["changes"]}
    assert "inputs.overrides.deq_functional" in fields and "inputs.overrides.highfield" in fields
    new = revision.save(proj, pv)
    ov = J.load_version(proj, new.entry_id, new.version)["inputs"]["overrides"]
    assert (new.entry_id, new.version) == (ref.entry_id, 2) and ov["deq_functional"] == "TPSSh" and ov["broken_symmetry"] == bs
    assert not ov.get("delta_functional") and not ov.get("highfield")
    assert J.load_version(proj, ref.entry_id, 2).get("approval") is None


def test_preset_plans_equal_hand_typed_plans(proj, fe1):  # AE5, R8
    opts = {"deq_functional": "TPSSh", "highfield": True}
    PR.save(proj, "p", opts)
    a = J.add_entry(proj, fe1, -1, 4, name="a", oxidation_state=1, overrides=PR.merge(PR.get(proj, "p"), {}))
    b = J.add_entry(proj, fe1, -1, 4, name="b", oxidation_state=1, overrides=dict(opts))
    ia, ib = (J.load_version(proj, r.entry_id)["inputs"] for r in (a, b))
    assert ia["overrides"] == ib["overrides"] and "preset" not in json.dumps(ia)


def test_merge_lets_explicit_options_win():  # R5
    assert PR.merge({"options": {"deq_functional": "TPSSh", "nprocs": 16}}, {"nprocs": 8}) == {"deq_functional": "TPSSh", "nprocs": 8}


def test_presets_belong_to_one_project(tmp_path, proj):  # R3
    other = tmp_path / "other"
    J.create_project(other, name="other")
    PR.save(proj, "std", {"highfield": True})
    assert PR.list_presets(other) == []


def test_user_refs_calibration_strategy_is_refused(proj):  # R2
    with pytest.raises(J.ProjectError, match="user_refs"):
        PR.save(proj, "refs", {"calibration_strategy": "user_refs"})


def test_hand_edited_preset_is_revalidated_on_read(proj):
    PR.save(proj, "ok", {"highfield": True})
    doc = json.loads((proj / "presets.json").read_text())
    doc["presets"]["ok"]["options"]["broken_symmetry"] = {"kind": "flipspin", "flip_atoms": [0], "final_ms": 0.5}
    (proj / "presets.json").write_text(json.dumps(doc))
    with pytest.raises(J.ProjectError, match="broken_symmetry"):
        PR.get(proj, "ok")


def test_damaged_presets_file_is_a_project_error(proj):
    (proj / "presets.json").write_text("{not json")
    with pytest.raises(J.ProjectError, match="damaged"):
        PR.list_presets(proj)
