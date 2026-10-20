"""Recorded real-run packs for the teaching example (second-pass idea 7; decisions D61-D62)."""
import json
import zipfile

import pytest

from mossbauer_dft import plan as P, project as J, recorded as RC, tutorial as T
from mossbauer_dft.cli import main
from test_plan import _home  # noqa: F401
from test_project_status import _place, _jobs


def _run_example(root):
    """An instructor's example project: plans approved, inputs written, real-looking outputs returned."""
    refs = T.create_example(root)
    for ref in refs:
        P.save_plan(P.approve_plan(J.load_version(root, ref.entry_id, 1), "instructor"), ref.path / "plan.yaml")
        J.write_version_inputs(root, ref.entry_id, 1)
        for stem, job in _jobs(root, ref).items():
            if job["kind"] in J.PROPERTY_KINDS:
                _place(root, ref, stem)
    return refs


@pytest.fixture
def pack(tmp_path, _home):
    src = tmp_path / "instructor"
    _run_example(src)
    out = tmp_path / "pack.zip"
    RC.export_pack(src, out, "Dr. Instructor", site="campus-cluster", program="ORCA 6.1.0")
    return out


def test_pack_holds_frozen_drafts_and_evidence(pack):
    with zipfile.ZipFile(pack) as z:
        meta = json.loads(z.read("pack.json"))
        plan_text = z.read(f"plans/{meta['entries'][0]['entry']}/plan.yaml").decode()
    assert meta["recorded_by"] == "Dr. Instructor" and len(meta["entries"]) == 3
    assert "status: draft" in plan_text and "instructor" not in plan_text  # the instructor's approval is not shipped


def test_learner_flow_approve_unchanged_then_import(pack, tmp_path, _home):
    root = tmp_path / "learner"
    refs = T.create_example(root, recorded_pack=pack)
    assert all(J.scan_version(root, r.entry_id).status == "draft" for r in refs)  # nothing approved for the learner
    assert "recorded real run by Dr. Instructor" in (root / "EXAMPLE.md").read_text()
    eid = refs[0].entry_id
    assert "approve the plan first" in RC.available(root, eid)
    with pytest.raises(RC.PackError):
        RC.import_recorded(root, eid)
    P.save_plan(P.approve_plan(J.load_version(root, eid, 1), "learner"), refs[0].path / "plan.yaml")
    out = RC.import_recorded(root, eid)
    assert out["imported"] and "Recorded real calculation by Dr. Instructor" in out["recorded"]
    res = J.analyze_version(root, eid)
    assert res["rows"] and all(s["kind"] == "compact" for s in res["evidence_sources"])


def test_edited_plan_no_longer_matches_its_recording(pack, tmp_path, _home):
    root = tmp_path / "learner"
    refs = T.create_example(root, recorded_pack=pack)
    eid = refs[1].entry_id
    J.edit_entry(root, eid, overrides={"nprocs": 8})
    assert "newer version" in RC.available(root, eid)


def test_tampered_pack_is_refused_before_writing(pack, tmp_path, _home):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(pack) as src, zipfile.ZipFile(bad, "w") as dst:
        for n in src.namelist():
            body = src.read(n)
            if n.endswith("plan.yaml") and "Cp2Fe" in n:
                body = body.replace(b"nprocs: ", b"nprocs: 1")
            dst.writestr(n, body)
    root = tmp_path / "learner"
    with pytest.raises(J.ProjectError, match="does not match its recorded hash"):
        T.create_example(root, recorded_pack=bad)
    assert not root.exists() or not any(root.iterdir())


def test_unexpected_member_is_refused(pack, tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(pack) as src, zipfile.ZipFile(bad, "w") as dst:
        for n in src.namelist():
            dst.writestr(n, src.read(n))
        dst.writestr("../evil.sh", "rm -rf /")
    with pytest.raises(RC.PackError, match="unexpected files"):
        RC.read_pack(bad)


def test_export_refuses_unrun_or_unapproved_projects(tmp_path, _home):
    root = tmp_path / "ex"
    T.create_example(root)
    with pytest.raises(RC.PackError, match="not an approved"):
        RC.export_pack(root, tmp_path / "p.zip", "x")


def test_cli_pack_round_trip_and_guide_mentions_it(pack, tmp_path, capsys, _home):
    root = tmp_path / "learner"
    main(["project", "example", str(root), "--recorded-pack", str(pack)])
    capsys.readouterr()
    run = next(s for s in T.guide(root)["steps"] if s["id"] == "run")
    assert "recorded real run" in run["why"]
    main(["project", "approve", "--project", str(root), "--all-drafts", "--by", "learner"])
    main(["project", "import-recorded", "--project", str(root), "--all"])
    out = capsys.readouterr().out
    assert out.count("imported") >= 3 and "not imported" not in out


def test_imported_is_reported_once_done(pack, tmp_path, _home):
    root = tmp_path / "learner"
    refs = T.create_example(root, recorded_pack=pack)
    eid = refs[0].entry_id
    assert not RC.imported(root, eid)
    P.save_plan(P.approve_plan(J.load_version(root, eid, 1), "learner"), refs[0].path / "plan.yaml")
    RC.import_recorded(root, eid)
    assert RC.imported(root, eid)


def _rewrite_pack(pack, dest, mutate):
    with zipfile.ZipFile(pack) as src, zipfile.ZipFile(dest, "w") as dst:
        for n in src.namelist():
            dst.writestr(n, mutate(n, src.read(n)))
    return dest


def test_pack_with_a_preapproved_plan_is_refused(pack, tmp_path, _home):
    import yaml
    def approve(n, body):
        if not n.endswith("plan.yaml"):
            return body
        d = yaml.safe_load(body)
        d.update(status="approved", approval=dict(by="learner", at="2026-10-18T00:00:00Z", plan_hash=d["plan_hash"]))
        return yaml.safe_dump(d, sort_keys=False).encode()
    bad = _rewrite_pack(pack, tmp_path / "approved.zip", approve)
    with pytest.raises(RC.PackError, match="not a draft"):
        T.create_example(tmp_path / "learner", recorded_pack=bad)


def test_learners_own_outputs_are_not_called_recorded(pack, tmp_path, _home):
    root = tmp_path / "learner"
    refs = T.create_example(root, recorded_pack=pack)
    ref = refs[0]
    P.save_plan(P.approve_plan(J.load_version(root, ref.entry_id, 1), "learner"), ref.path / "plan.yaml")
    J.write_version_inputs(root, ref.entry_id, 1)
    for stem, job in _jobs(root, ref).items():
        if job["kind"] in J.PROPERTY_KINDS:
            _place(root, ref, stem)  # the learner ran it themselves
    assert not RC.imported(root, ref.entry_id)
