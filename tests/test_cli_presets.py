"""`mossbauer project preset` and --preset on add/edit/batch (presets U2: R5-R7, R9; AE1, AE3, AE5)."""
import contextlib
import io
import json

import pytest

from mossbauer_dft import batch as B
from mossbauer_dft import project as J
from mossbauer_dft.cli import main
from test_plan import XYZ, _home  # noqa: F401
from test_project_status import proj  # noqa: F401


def _run(argv) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(argv)
    return buf.getvalue()


@pytest.fixture
def xyz(tmp_path):
    f = tmp_path / "fe1.xyz"
    f.write_text(XYZ)
    return f


def _add(proj, xyz, *extra, name="cmpd"):
    _run(["project", "add", "--project", str(proj), "--structure", str(xyz), "--charge", "-1", "--multiplicity", "4",
          "--ox-state", "1", "--name", name, *extra])
    return next(e.entry_id for e in J.list_entries(proj) if e.display_name == name)


def test_save_list_show_delete(proj):
    out = json.loads(_run(["project", "preset", "save", "--project", str(proj), "--name", "tpssh-hf", "--deq-functional", "TPSSh",
                           "--highfield", "--description", "group protocol"]))
    assert out["options"] == {"deq_functional": "TPSSh", "highfield": True}
    assert "tpssh-hf" in _run(["project", "preset", "list", "--project", str(proj)])
    doc = json.loads(_run(["project", "preset", "list", "--project", str(proj), "--json"]))
    assert [p["name"] for p in doc["presets"]] == ["tpssh-hf"]
    assert json.loads(_run(["project", "preset", "show", "--project", str(proj), "--name", "tpssh-hf"]))["description"] == "group protocol"
    _run(["project", "preset", "delete", "--project", str(proj), "--name", "tpssh-hf"])
    assert "no presets" in _run(["project", "preset", "list", "--project", str(proj)])


def test_add_with_preset_and_explicit_flag(proj, xyz):  # AE1, AE5
    _run(["project", "preset", "save", "--project", str(proj), "--name", "tpssh-hf", "--deq-functional", "TPSSh", "--highfield"])
    eid = _add(proj, xyz, "--preset", "tpssh-hf", "--nprocs", "8")
    typed = _add(proj, xyz, "--deq-functional", "TPSSh", "--highfield", "--nprocs", "8", name="typed")
    ov = J.load_version(proj, eid)["inputs"]["overrides"]
    assert ov == {"deq_functional": "TPSSh", "highfield": True, "nprocs": 8} == J.load_version(proj, typed)["inputs"]["overrides"]


def test_edit_with_preset_previews_then_saves_with_hash(proj, xyz, capsys):  # AE3, R7
    _run(["project", "preset", "save", "--project", str(proj), "--name", "tpssh", "--deq-functional", "TPSSh"])
    eid = _add(proj, xyz, "--delta-functional", "B3LYP", "--highfield", "--bs-na", "3", "--bs-nb", "1")
    pv = json.loads(_run(["project", "edit", "--project", str(proj), "--entry", eid, "--preset", "tpssh"]))
    assert len(J.list_versions(proj, eid)) == 1 and pv["changes"]
    _run(["project", "edit", "--project", str(proj), "--entry", eid, "--preset", "tpssh", "--expect-hash", pv["preset_hash"][:12]])
    ov = J.load_version(proj, eid, 2)["inputs"]["overrides"]
    assert ov["deq_functional"] == "TPSSh" and ov["broken_symmetry"]["kind"] == "brokensym" and not ov.get("highfield")
    with pytest.raises(SystemExit, match="preview again"):
        main(["project", "edit", "--project", str(proj), "--entry", eid, "--preset", "tpssh", "--expect-hash", pv["preset_hash"][:12]])


def test_edit_preset_refuses_other_edit_flags(proj, xyz):
    _run(["project", "preset", "save", "--project", str(proj), "--name", "p", "--highfield"])
    eid = _add(proj, xyz)
    with pytest.raises(SystemExit, match="on its own"):
        main(["project", "edit", "--project", str(proj), "--entry", eid, "--preset", "p", "--nprocs", "4"])


def test_batch_preview_uses_preset_with_explicit_override(proj, tmp_path):
    _run(["project", "preset", "save", "--project", str(proj), "--name", "p", "--deq-functional", "TPSSh", "--nprocs", "16"])
    si = tmp_path / "si.xyz"
    si.write_text(XYZ)
    src = B.load_sources([("si.xyz", XYZ)])
    rows = B.rows_from_sources(src)
    rows[0].update(charge=-1, multiplicity=4, oxidation_state=1)
    csv = tmp_path / "s.csv"
    csv.write_text(B.write_csv(rows, {}))
    doc = json.loads(_run(["project", "batch", "preview", "--project", str(proj), "--structures", str(si), "--csv", str(csv),
                           "--preset", "p", "--nprocs", "8"]))
    assert doc["options"] == {"deq_functional": "TPSSh", "nprocs": 8}


def test_preset_save_refuses_user_refs_flag(proj, tmp_path):
    refs = tmp_path / "refs.yaml"
    refs.write_text("- name: r\n")
    with pytest.raises(SystemExit, match="user_refs"):
        main(["project", "preset", "save", "--project", str(proj), "--name", "p", "--user-refs", str(refs)])


def test_replacing_the_preset_after_preview_refuses_the_save(proj, xyz):  # review: pin the preset too
    _run(["project", "preset", "save", "--project", str(proj), "--name", "p", "--deq-functional", "TPSSh"])
    eid = _add(proj, xyz)
    pv = json.loads(_run(["project", "edit", "--project", str(proj), "--entry", eid, "--preset", "p"]))
    _run(["project", "preset", "save", "--project", str(proj), "--name", "p", "--deq-functional", "PBE0", "--replace"])
    with pytest.raises(SystemExit, match="preset changed"):
        main(["project", "edit", "--project", str(proj), "--entry", eid, "--preset", "p", "--expect-hash", pv["preset_hash"][:12]])
    assert len(J.list_versions(proj, eid)) == 1


def test_auto_calibration_strategy_is_stored_as_default(proj):
    out = json.loads(_run(["project", "preset", "save", "--project", str(proj), "--name", "a", "--calibration-strategy", "auto",
                           "--highfield"]))
    assert out["options"] == {"highfield": True}


def test_freq_flag_keeps_the_presets_geometry_level(proj, xyz):  # review P1
    _run(["project", "preset", "save", "--project", str(proj), "--name", "g", "--geometry", "B3LYP/def2-TZVP"])
    eid = _add(proj, xyz, "--preset", "g", "--freq")
    g = J.load_version(proj, eid)["inputs"]["overrides"]["geometry"]
    assert (g["functional"], g["basis"], g["freq"]) == ("B3LYP", "def2-TZVP", True)
    eid2 = _add(proj, xyz, "--preset", "g", "--geometry", "PBE/def2-SVP", name="explicit")
    assert J.load_version(proj, eid2)["inputs"]["overrides"]["geometry"]["functional"] == "PBE"


def test_geometry_flag_keeps_the_presets_frequency_job(proj, xyz):
    _run(["project", "preset", "save", "--project", str(proj), "--name", "gf", "--geometry", "B3LYP/def2-TZVP", "--freq"])
    eid = _add(proj, xyz, "--preset", "gf", "--geometry", "PBE/def2-SVP")
    g = J.load_version(proj, eid)["inputs"]["overrides"]["geometry"]
    assert (g["functional"], g["freq"]) == ("PBE", True)


def test_edit_preset_short_hash_is_refused(proj, xyz):
    _run(["project", "preset", "save", "--project", str(proj), "--name", "p", "--highfield"])
    eid = _add(proj, xyz)
    pv = json.loads(_run(["project", "edit", "--project", str(proj), "--entry", eid, "--preset", "p"]))
    with pytest.raises(SystemExit, match="8 characters"):
        main(["project", "edit", "--project", str(proj), "--entry", eid, "--preset", "p", "--expect-hash", pv["preset_hash"][:4]])
