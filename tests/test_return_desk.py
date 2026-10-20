"""Return desk: job-state records, adopting stray outputs, resubmitting failed jobs (second-pass idea 4; D55-D56)."""
import json

import pytest

from mossbauer_dft import compact as C, harvest as H, project as J
from mossbauer_dft.cli import main
from test_plan import _home  # noqa: F401
from test_project_status import fe1, proj, _ready, _place, _jobs  # noqa: F401

FAILED = "started job=77 node=n01 at=2026-10-18T10:00:00Z\nfinished rc=1 at=2026-10-18T11:00:00Z reason=ORCA did not terminate normally\n"
STARTED = "started job=78 node=n02 at=2026-10-18T10:00:00Z\n"


def _stems(root, ref, kind=None):
    return [s for s, j in _jobs(root, ref).items() if kind is None or j["kind"] == kind]


def test_parse_state_reads_last_events_and_bounds_fields():
    st = H.parse_state(FAILED + "garbage line\n")
    assert st["started"] == dict(job="77", node="n01", at="2026-10-18T10:00:00Z")
    assert st["finished"]["rc"] == "1" and st["finished"]["reason"] == "ORCA did not terminate normally"
    assert H.parse_state("nothing useful") == {}
    with pytest.raises(ValueError):
        H.check_state({"finished": {"rc": 1}})


def test_status_scan_explains_failed_and_unfinished_jobs(proj, fe1):
    ref = _ready(proj, fe1)
    a, b = _stems(proj, ref, "mossbauer_sp")[:2]
    (ref.path / "inputs" / f"{a}.state").write_text(FAILED)
    (ref.path / "outputs").mkdir(exist_ok=True)
    (ref.path / "outputs" / f"{b}.state").write_text(STARTED)
    jobs = {j.stem: j for j in J.scan_version(proj, ref.entry_id).jobs}
    assert jobs[a].run_label == "failed" and "rc=1" in jobs[a].reason
    assert jobs[b].run_label == "killed or running" and "no finish record" in jobs[b].reason
    vs = J.scan_version(proj, ref.entry_id)
    assert J.failed_jobs(vs) == [a]
    assert set(J.failed_jobs(vs, include_unreturned=True)) == set(_stems(proj, ref))


def test_harvest_carries_job_states_and_import_installs_them(proj, fe1, tmp_path):
    ref = _ready(proj, fe1)
    a, b = _stems(proj, ref, "mossbauer_sp")[:2]
    _place(proj, ref, a)
    (ref.path / "outputs" / f"{b}.state").write_text(FAILED)
    doc = H.harvest(ref.path / "inputs/harvest-manifest.json", ref.path / "outputs")
    assert doc["census"][b]["finished"]["rc"] == "1" and b in doc["missing"]
    (ref.path / "outputs" / f"{b}.state").unlink()
    (ref.path / "outputs" / f"{a}.out").unlink()
    src = tmp_path / "bundle.json"; src.write_text(json.dumps(doc))
    out = C.import_bundle(proj, ref.entry_id, 1, src)
    assert out["imported"] == [a] and b in out["job_states"]
    assert json.loads((ref.path / "outputs" / f"{b}.state.json").read_text())["finished"]["rc"] == "1"
    assert {j.stem: j for j in J.scan_version(proj, ref.entry_id).jobs}[b].run_label == "failed"
    stored = json.loads((ref.path / "outputs" / f"{a}.compact.json").read_text())
    assert stored["census"] == {}


def test_malformed_census_rejects_the_whole_bundle(proj, fe1, tmp_path):
    ref = _ready(proj, fe1)
    a = _stems(proj, ref, "mossbauer_sp")[0]
    _place(proj, ref, a)
    doc = H.harvest(ref.path / "inputs/harvest-manifest.json", ref.path / "outputs")
    doc["census"] = {"../escape": {"started": {"job": "1"}}}
    (ref.path / "outputs" / f"{a}.out").unlink()
    src = tmp_path / "bundle.json"; src.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="census"):
        C.import_bundle(proj, ref.entry_id, 1, src)
    assert not list((ref.path / "outputs").glob("*.json"))


def test_adopt_copies_without_overwriting(proj, fe1):
    ref = _ready(proj, fe1)
    a, b = _stems(proj, ref, "mossbauer_sp")[:2]
    _place(proj, ref, a, where="inputs")
    _place(proj, ref, b, where="inputs")
    (ref.path / "inputs" / f"{a}.state").write_text(FAILED)
    (ref.path / "outputs").mkdir(exist_ok=True)
    (ref.path / "outputs" / f"{b}.out").write_text("different")
    r = J.adopt_outputs(proj, ref.entry_id)
    assert set(r["adopted"]) == {f"{a}.out", f"{a}.state"}
    assert r["skipped"][0]["file"] == f"{b}.out" and (ref.path / "outputs" / f"{b}.out").read_text() == "different"
    assert (ref.path / "inputs" / f"{a}.out").is_file()  # originals stay


def test_resubmit_adds_dependents_and_records_resources(proj, fe1):
    ref = _ready(proj, fe1, freq=True)
    jobs = J.load_version(proj, ref.entry_id)["jobs"]
    opt = next(j for j in jobs if j["kind"] == "opt")
    dependents = [j["stem"] for j in jobs if opt["id"] in j["depends_on"]]
    r = J.write_resubmit(proj, ref.entry_id, stems=[opt["stem"]], resources={opt["stem"]: {"time": "96:00:00", "mem_gb": 128}})
    assert r["stems"][0] == opt["stem"] and set(dependents) <= set(r["stems"])
    script = (ref.path / "inputs" / "resubmit_failed.sh").read_text()
    assert "-t 96:00:00" in script and "--mem=128G" in script and "--dependency=afterok:${J_" in script
    rec = json.loads((ref.path / "inputs" / "resubmit.json").read_text())
    assert rec["requested"] == [opt["stem"]] and rec["resources"][opt["stem"]]["time"] == "96:00:00"
    assert J.scan_version(proj, ref.entry_id).status == "inputs written"  # still reads as written


def test_resubmit_refuses_when_nothing_failed_or_not_set_up(proj, fe1):
    from test_project_status import _add
    ref = _ready(proj, fe1)
    with pytest.raises(J.ProjectError, match="no failed jobs"):
        J.write_resubmit(proj, ref.entry_id)
    draft = _add(proj, fe1, name="d")
    with pytest.raises(J.ProjectError, match="not written"):
        J.write_resubmit(proj, draft.entry_id, stems=["x"])


def test_cli_adopt_and_resubmit(proj, fe1, capsys):
    ref = _ready(proj, fe1)
    a = _stems(proj, ref, "mossbauer_sp")[0]
    _place(proj, ref, a, where="inputs", terminated=False)
    main(["project", "adopt", "--project", str(proj), "--entry", ref.entry_id])
    assert f"adopted inputs/{a}.out" in capsys.readouterr().out
    main(["project", "resubmit", "--project", str(proj), "--entry", ref.entry_id, "--time", f"{a}=12:00:00"])
    out = capsys.readouterr().out
    assert "resubmit_failed.sh" in out and a in out and "-t 12:00:00" in out


def test_gui_return_desk_adopts(proj, fe1, monkeypatch):
    from test_cli_gui import _app, _btn
    ref = _ready(proj, fe1)
    a = _stems(proj, ref, "mossbauer_sp")[0]
    _place(proj, ref, a, where="inputs")
    at = _app(monkeypatch, proj, qp={"page": "Results", "entry": ref.entry_id, "version": "1"})
    _btn(at, "Adopt 1 output(s) into outputs/").click().run()
    assert not at.exception and (ref.path / "outputs" / f"{a}.out").is_file()


def test_resubmit_refuses_without_written_inputs(proj, fe1):
    from test_project_status import _add, _approve
    ref = _add(proj, fe1, name="imported")
    _approve(proj, ref)  # approved, but evidence came back without decks being written here
    stem = _stems(proj, ref, "mossbauer_sp")[0]
    with pytest.raises(J.ProjectError, match="inputs are not written"):
        J.write_resubmit(proj, ref.entry_id, stems=[stem])


@pytest.mark.parametrize("res,match", [({"typo_stem": {"time": "96:00:00"}}, "not a job"),
                                       ({"STEM": {"mem_gb": 0.5}}, "at least 1"),
                                       ({"STEM": {"mem_gb": float("nan")}}, "at least 1"),
                                       ({"STEM": {"time": "96 hours"}}, "HH:MM:SS")])
def test_resubmit_validates_resources(proj, fe1, res, match):
    ref = _ready(proj, fe1)
    stem = _stems(proj, ref, "mossbauer_sp")[0]
    res = {stem if k == "STEM" else k: v for k, v in res.items()}
    with pytest.raises(J.ProjectError, match=match):
        J.write_resubmit(proj, ref.entry_id, stems=[stem], resources=res)
    assert not (ref.path / "inputs" / "resubmit_failed.sh").exists()


def test_newest_run_record_wins(proj, fe1):
    ref = _ready(proj, fe1)
    stem = _stems(proj, ref, "mossbauer_sp")[0]
    (ref.path / "outputs").mkdir(exist_ok=True)
    (ref.path / "outputs" / f"{stem}.state").write_text(FAILED)  # adopted old run
    (ref.path / "inputs" / f"{stem}.state").write_text(
        "started job=99 node=n03 at=2026-10-19T08:00:00Z\nfinished rc=0 at=2026-10-19T09:00:00Z reason=normal\n")
    job = {j.stem: j for j in J.scan_version(proj, ref.entry_id).jobs}[stem]
    assert job.run_label == "finished" and stem not in J.failed_jobs(J.scan_version(proj, ref.entry_id))


def test_newest_run_is_the_one_that_started_last():
    old = dict(started=dict(at="2026-10-18T09:00:00Z"), finished=dict(rc="1"))
    new = dict(started=dict(at="2026-10-18T10:30:00Z"))
    assert J._newest_run([new, old]) is new and J._newest_run([old, new]) is new
    assert J._newest_run([]) is None
