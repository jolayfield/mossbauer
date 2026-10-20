"""`mossbauer project batch` and the CLI parts of the multi-structure guard (U4, U2: R17, R18, R26; AE8; F2)."""
import contextlib
import io
import json

import pytest

from mossbauer_dft import batch as B
from mossbauer_dft import project as J
from mossbauer_dft.cli import main
from test_plan import XYZ, _home  # noqa: F401
from test_project_status import proj  # noqa: F401


def _blocks(names):
    body = XYZ.strip("\n").split("\n")
    return "\n".join("\n".join([body[0], n] + body[2:]) for n in names) + "\n"


def _run(argv) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(argv)
    return buf.getvalue()


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def sheet(tmp_path, proj):
    si = tmp_path / "si.xyz"
    si.write_text(_blocks(["FeA", "FeB"]))
    csv = tmp_path / "sheet.csv"
    out = json.loads(_run(["project", "batch", "template", "--project", str(proj), "--structures", str(si), "--out", str(csv)]))
    assert [s["compound"] for s in out["structures"]] == ["FeA", "FeB"]
    rows, opts = B.read_csv(csv.read_text())
    for r in rows:
        r.update(charge=-1, multiplicity=4, oxidation_state=1)
    csv.write_text(B.write_csv(rows, opts))
    return si, csv


def test_template_writes_options_line_header_and_one_row_per_block(sheet):
    _, csv = sheet
    lines = csv.read_text().splitlines()
    assert lines[0].startswith('"# mossbauer-batch options:') or lines[0].startswith("# mossbauer-batch options:")
    assert lines[1].split(",")[:2] == ["structure", "compound"] and len(lines) == 4


def test_preview_writes_nothing_and_reports_rows(proj, sheet):  # AE8
    si, csv = sheet
    before = _tree(proj)
    doc = json.loads(_run(["project", "batch", "preview", "--project", str(proj), "--structures", str(si), "--csv", str(csv)]))
    assert _tree(proj) == before
    assert [r["outcome"] for r in doc["rows"]] == ["ready", "ready"] and doc["schema_version"] == "1" and doc["preview_hash"]


def test_create_then_rerun_skips(proj, sheet):
    si, csv = sheet
    args = ["project", "batch", "create", "--project", str(proj), "--structures", str(si), "--csv", str(csv)]
    doc = json.loads(_run(args))
    ids = [r["entries"][0] for r in doc["rows"]]
    assert [r["outcome"] for r in doc["rows"]] == ["created", "created"] and doc["created"] == ids
    assert doc["rows"][0]["paths"][0].startswith("entries/")
    again = json.loads(_run(args))
    assert [r["outcome"] for r in again["rows"]] == ["skipped", "skipped"] and len(J.list_entries(proj)) == 2


def test_create_with_stale_hash_is_refused(proj, sheet):
    si, csv = sheet
    with pytest.raises(SystemExit, match="preview again"):
        main(["project", "batch", "create", "--project", str(proj), "--structures", str(si), "--csv", str(csv), "--expect-hash", "0" * 64])
    assert J.list_entries(proj) == []


def test_batch_does_not_take_broken_symmetry_flags(proj, sheet):
    si, csv = sheet
    with pytest.raises(SystemExit):
        main(["project", "batch", "preview", "--project", str(proj), "--structures", str(si), "--csv", str(csv), "--bs-flip", "0"])


def test_add_refuses_multi_structure_file_and_trajectory(proj, tmp_path):
    three = tmp_path / "three.xyz"
    three.write_text(_blocks(["A", "B", "C"]))
    with pytest.raises(SystemExit, match="holds 3 structures"):
        main(["project", "add", "--project", str(proj), "--structure", str(three), "--charge", "-1", "--multiplicity", "4"])
    assert J.list_entries(proj) == []
    one = tmp_path / "fe1.xyz"
    one.write_text(XYZ)
    _run(["project", "add", "--project", str(proj), "--structure", str(one), "--charge", "-1", "--multiplicity", "4", "--name", "fe1"])
    eid = J.list_entries(proj)[0].entry_id
    trj = tmp_path / "opt_trj.xyz"
    trj.write_text(_blocks(["s1", "s2"]))
    with pytest.raises(SystemExit, match="final geometry"):
        main(["project", "edit", "--project", str(proj), "--entry", eid, "--structure", str(trj)])


def test_nitrosyl_flag_on_add_and_edit(proj, tmp_path):  # R26
    one = tmp_path / "fe1.xyz"
    one.write_text(XYZ)
    _run(["project", "add", "--project", str(proj), "--structure", str(one), "--charge", "-1", "--multiplicity", "4", "--nitrosyl", "yes"])
    eid = J.list_entries(proj)[0].entry_id
    assert J.load_version(proj, eid)["inputs"]["hints"]["nitrosyl"] is True
    _run(["project", "edit", "--project", str(proj), "--entry", eid, "--nitrosyl", "no"])
    assert J.load_version(proj, eid, 2)["inputs"]["hints"]["nitrosyl"] is False
