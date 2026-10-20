"""Batch approval with channel recording (U3: R12, R13, R21; KTD6; AE4, AE8)."""
import io

import pytest
import yaml

from mossbauer_dft import plan as P
from mossbauer_dft import project as J
from mossbauer_dft.cli import main
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


def _add(root, s, name, **kw):
    return J.add_entry(root, s, -1, 4, name=name, oxidation_state=1, **kw)


def _raw(ref):
    return yaml.safe_load((ref.path / "plan.yaml").read_text())


def test_batch_approve_four_drafts_one_with_open_item(proj, fe1):
    """AE4: three are sealed individually; the fourth stays a draft and is returned with its open-item text."""
    refs = [_add(proj, fe1, f"cmpd{i}") for i in range(3)]
    blocked = _add(proj, fe1, "radical", radical_ligand=True)
    open_text = J.load_version(proj, blocked.entry_id)["open_items"][0]
    out = J.approve_versions(proj, refs + [blocked], "A. Researcher", via="gui")
    assert [o.outcome for o in out] == ["sealed", "sealed", "sealed", "skipped"]
    assert out[3].entry_id == blocked.entry_id and open_text in out[3].reason
    hashes = set()
    for ref in refs:
        plan = J.load_version(proj, ref.entry_id)
        P.require_approved(plan)
        a = plan["approval"]
        assert a["by"] == "A. Researcher" and a["via"] == "gui" and a["at"] and "on_behalf_of" not in a
        assert a["plan_hash"] == plan["plan_hash"] == P.compute_hash(plan)
        hashes.add(a["plan_hash"])
    assert len(hashes) == 3
    b = J.load_version(proj, blocked.entry_id)
    assert b["status"] == "draft" and b["approval"] is None
    assert J.scan_version(proj, blocked.entry_id).status == "draft"


def test_batch_accepts_entry_ids_and_targets_newest_version(proj, fe1):
    ref = _add(proj, fe1, "cmpd")
    v2 = J.new_version(proj, ref.entry_id, solvent="toluene")
    (o,) = J.approve_versions(proj, [ref.entry_id], "me", via="cli")
    assert (o.outcome, o.version) == ("sealed", 2)
    assert J.load_version(proj, ref.entry_id, 2)["status"] == "approved" and v2.version == 2
    assert J.load_version(proj, ref.entry_id, 1)["status"] == "draft"


def test_approving_one_entry_leaves_other_entry_seal_untouched(proj, fe1):
    """R13."""
    a, b = _add(proj, fe1, "first"), _add(proj, fe1, "second")
    J.approve_versions(proj, [a], "me", via="cli")
    before = (a.path / "plan.yaml").read_bytes()
    J.approve_versions(proj, [b], "someone else", via="gui")
    assert (a.path / "plan.yaml").read_bytes() == before
    assert J.scan_version(proj, a.entry_id).approved


def test_already_approved_unmodified_is_reported_and_not_rewritten(proj, fe1):
    a, b = _add(proj, fe1, "first"), _add(proj, fe1, "second")
    J.approve_versions(proj, [a], "me", via="cli")
    before = (a.path / "plan.yaml").read_bytes()
    out = J.approve_versions(proj, [a, b], "other", via="gui")
    assert [o.outcome for o in out] == ["already-approved", "sealed"]
    assert (a.path / "plan.yaml").read_bytes() == before
    assert _raw(a)["approval"]["by"] == "me"


def test_modified_and_unreadable_versions_are_skipped(proj, fe1):
    a, b = _add(proj, fe1, "first"), _add(proj, fe1, "second")
    J.approve_versions(proj, [a], "me", via="cli")
    d = _raw(a)
    d["inputs"]["charge"] = 0  # tamper after approval
    (a.path / "plan.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
    tampered = (a.path / "plan.yaml").read_bytes()
    (b.path / "plan.yaml").write_text(":\n  - [unbalanced")
    out = J.approve_versions(proj, [a, b], "me", via="cli")
    assert [o.outcome for o in out] == ["skipped", "skipped"]
    assert "modified" in out[0].reason and "unreadable" in out[1].reason
    assert (a.path / "plan.yaml").read_bytes() == tampered


def test_plan_sealed_with_old_signature_still_verifies(proj, fe1):
    """A seal from before this change has no via/on_behalf_of: it still loads, verifies and passes the gate."""
    ref = _add(proj, fe1, "old")
    P.save_plan(P.approve_plan(J.load_version(proj, ref.entry_id), "tester"), ref.path / "plan.yaml")
    plan = J.load_version(proj, ref.entry_id)
    assert set(plan["approval"]) == {"by", "at", "plan_hash"}
    assert P.plan_integrity(plan)
    P.require_approved(plan)
    vs = J.scan_version(proj, ref.entry_id)
    assert vs.approved and vs.approval_line == "approved by tester"
    assert J.approve_versions(proj, [ref], "me", via="cli")[0].outcome == "already-approved"


def test_on_behalf_of_is_stored_only_when_given(fe1, tmp_path):
    p = P.approve_plan(P.make_plan(fe1, -1, 4, name="x", oxidation_state=1), "bot", via="agent", on_behalf_of="J. Layfield")
    assert p["approval"]["via"] == "agent" and p["approval"]["on_behalf_of"] == "J. Layfield"
    P.require_approved(p)


def test_cli_agent_approval_shows_in_status(proj, fe1, monkeypatch):
    """AE8: Claude approves two plans through the CLI on the researcher's request; both show approved by them via agent."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    refs = [_add(proj, fe1, "first"), _add(proj, fe1, "second")]
    for r in refs:
        main(["approve", str(r.path / "plan.yaml"), "--via", "agent", "--on-behalf-of", "J. Layfield"])
    for e in J.scan_project(proj):
        vs = e.newest
        assert vs.approved and vs.approval["via"] == "agent" and vs.approval["on_behalf_of"] == "J. Layfield"
        assert vs.approval_line == "approved by J. Layfield via agent"


def test_cli_agent_requires_on_behalf_of(proj, fe1):
    ref = _add(proj, fe1, "first")
    with pytest.raises(SystemExit):
        main(["approve", str(ref.path / "plan.yaml"), "--via", "agent"])
    with pytest.raises(SystemExit):
        main(["approve", str(ref.path / "plan.yaml"), "--on-behalf-of", "J. Layfield"])
    assert _raw(ref)["status"] == "draft"


def test_cli_without_via_non_interactive_is_marked(proj, fe1, monkeypatch):
    ref = _add(proj, fe1, "first")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    main(["approve", str(ref.path / "plan.yaml"), "--by", "me"])
    a = _raw(ref)["approval"]
    assert a["by"] == "me" and a["via"] == "cli (non-interactive)" and "on_behalf_of" not in a


def test_cli_without_via_interactive_records_cli(proj, fe1, monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True
    ref = _add(proj, fe1, "first")
    monkeypatch.setattr("sys.stdin", Tty(""))
    main(["approve", str(ref.path / "plan.yaml"), "--by", "me"])
    assert _raw(ref)["approval"]["via"] == "cli"


def test_pinned_seal_skips_when_a_newer_version_or_changed_plan_appeared(proj, fe1):
    """Review #2: a seal pinned to the version and hash the approver reviewed never lands on a different plan."""
    ref = _add(proj, fe1, "cmpd")
    reviewed = P.compute_hash(J.load_version(proj, ref.entry_id))
    J.new_version(proj, ref.entry_id, overrides={"nprocs": 8})  # written after the review
    out = J.approve_versions(proj, [ref], "A. Researcher", via="gui", expected_hashes={ref.entry_id: reviewed})
    assert out[0].outcome == "skipped" and "newer version v2" in out[0].reason
    assert not _raw(ref).get("approval") and not yaml.safe_load((ref.path.parent / "v2" / "plan.yaml").read_text()).get("approval")
    v2 = J.VersionRef(ref.entry_id, 2, ref.path.parent / "v2")
    out = J.approve_versions(proj, [v2], "A. Researcher", via="gui", expected_hashes={ref.entry_id: reviewed})
    assert out[0].outcome == "skipped" and "changed since review" in out[0].reason
    good = P.compute_hash(J.load_version(proj, ref.entry_id, 2))
    out = J.approve_versions(proj, [v2], "A. Researcher", via="gui", expected_hashes={ref.entry_id: good[:12]})
    assert out[0].outcome == "sealed" and out[0].version == 2


def test_cli_approve_version_and_expect_hash(proj, fe1, capsys):
    ref = _add(proj, fe1, "cmpd")
    h = P.compute_hash(J.load_version(proj, ref.entry_id))
    J.new_version(proj, ref.entry_id, overrides={"nprocs": 8})
    main(["project", "approve", "--project", str(proj), "--entry", ref.entry_id, "--version", "1", "--expect-hash", h[:12],
          "--by", "A. Researcher"])
    assert "skipped - a newer version v2" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["project", "approve", "--project", str(proj), "--all-drafts", "--version", "1", "--by", "A. Researcher"])
