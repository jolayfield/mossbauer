"""Recorded real-run packs for the teaching example (second-pass idea 7; decisions D61-D62).

An instructor who has run the example's plans on a real cluster exports a pack: the exact plans that were run (frozen, as
drafts) and the harvested compact evidence for each. A learner creates the example from the pack, so their draft plans are
byte-for-byte those plans; after they review and approve a plan *unchanged*, they may import the recorded evidence through
the normal compact validation. Nothing is synthetic and nothing is approved for the learner; a changed plan no longer
matches its recorded run and needs its own calculation.
"""
from __future__ import annotations

import copy
import json
import re
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

from . import compact, harvest, plan as P, project as J

FORMAT = "mossbauer-recorded-pack/1"
DIR = "recorded"
MAX_MEMBERS, MAX_MEMBER_BYTES = 200, harvest.MAX_BYTES
_MEMBER = re.compile(r"(pack\.json|plans/[A-Za-z0-9_-]+/(plan\.yaml|structure\.xyz)|bundles/[A-Za-z0-9_-]+\.json)\Z")
_LABEL_KEYS = ("recorded_by", "recorded_at", "site", "program", "note")


class PackError(J.ProjectError):
    pass


def _bundle_for(root, entry_id: str, plan: dict) -> dict:
    """One compact bundle for version 1 of an entry: harvested from raw outputs, or merged from imported compact records."""
    vd = J.version_dir(root, entry_id, 1)
    with tempfile.TemporaryDirectory() as tmp:
        m = Path(tmp) / "manifest.json"
        m.write_text(json.dumps(compact.manifest(plan, entry_id, 1)))
        doc = harvest.harvest(m, vd / "outputs")
    for stored in sorted((vd / "outputs").glob("*.compact.json")):
        stem = stored.name.removesuffix(".compact.json")
        if stem in doc["records"]:
            continue
        rec = harvest.read_json(stored)["records"].get(stem)
        if rec:
            doc["records"][stem] = rec
            doc["missing"] = [s for s in doc["missing"] if s != stem]
    doc["rejected"] = []
    return doc


def export_pack(root, out, recorded_by: str, *, site: str = "", program: str = "", note: str = "") -> dict:
    """Write a pack from an example project whose v1 plans were approved, run and returned (raw or compact outputs).
    Every property job of every entry must be present: a partial pack would teach a partial lesson."""
    meta = J.open_project(root)
    if not meta.get("example"):
        raise PackError("only a project made with `project example` can be exported as a recorded pack")
    if not str(recorded_by).strip():
        raise PackError("say who ran the calculations (--recorded-by)")
    entries, members = [], {}
    for e in J.list_entries(root):
        vs = J.scan_version(root, e.entry_id, 1)
        if not vs.approved:
            raise PackError(f"{e.entry_id} v1 is not an approved, unmodified plan; the pack must hold the plan that was run")
        props = [j for j in vs.jobs if j.kind in J.PROPERTY_KINDS]
        missing = [j.stem for j in props if j.state != "complete"]
        if missing:
            raise PackError(f"{e.entry_id} v1 has no complete output for {', '.join(missing)}")
        plan = P.load_plan(vs.path / "plan.yaml")
        doc = _bundle_for(root, e.entry_id, plan)
        if any(j.stem not in doc["records"] for j in props):
            raise PackError(f"{e.entry_id}: outputs did not harvest cleanly; check them on Results")
        draft = copy.deepcopy(plan)
        draft.update(status="draft", approval=None)
        with tempfile.TemporaryDirectory() as tmp:
            P.save_plan(draft, Path(tmp) / "plan.yaml")
            members[f"plans/{e.entry_id}/plan.yaml"] = (Path(tmp) / "plan.yaml").read_text()
            members[f"plans/{e.entry_id}/structure.xyz"] = (Path(tmp) / "structure.xyz").read_text()
        members[f"bundles/{e.entry_id}.json"] = json.dumps(doc, indent=2, allow_nan=False)
        entries.append(dict(entry=e.entry_id, display_name=e.display_name or e.entry_id, plan_hash=P.compute_hash(plan)))
    if not entries:
        raise PackError("the project has no entries")
    label = dict(recorded_by=str(recorded_by).strip(), recorded_at=P._now(), site=site, program=program, note=note)
    members["pack.json"] = json.dumps(dict(format=FORMAT, example=meta["example"], entries=entries, **label), indent=2)
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for name, text in members.items():
            z.writestr(name, text)
    return dict(path=str(out), example=meta["example"], entries=[x["entry"] for x in entries], **label)


def read_pack(source) -> dict:
    """Open and fully validate a pack (shape, sizes, plan integrity, every bundle against its frozen plan) before any use."""
    try:
        z = ZipFile(source)
    except (BadZipFile, OSError) as ex:
        raise PackError(f"not a readable pack: {ex}") from ex
    with z:
        infos = z.infolist()
        if len(infos) > MAX_MEMBERS or any(i.file_size > MAX_MEMBER_BYTES for i in infos):
            raise PackError("pack is too large")
        bad = [i.filename for i in infos if not _MEMBER.fullmatch(i.filename)]
        if bad:
            raise PackError(f"unexpected files in pack: {', '.join(bad[:5])}")
        try:
            meta = json.loads(z.read("pack.json"))
        except (KeyError, ValueError) as ex:
            raise PackError("pack.json missing or unreadable") from ex
        if not isinstance(meta, dict) or meta.get("format") != FORMAT or not isinstance(meta.get("entries"), list) or \
                any(not isinstance(meta.get(k, ""), str) or len(meta.get(k, "")) > 500 for k in _LABEL_KEYS + ("example",)):
            raise PackError("unsupported pack format")
        plans, bundles = {}, {}
        for item in meta["entries"]:
            eid = item.get("entry") if isinstance(item, dict) else None
            if not isinstance(eid, str) or not harvest.SAFE.fullmatch(eid):
                raise PackError("invalid pack entry")
            with tempfile.TemporaryDirectory() as tmp:
                for name in ("plan.yaml", "structure.xyz"):
                    try:
                        (Path(tmp) / name).write_bytes(z.read(f"plans/{eid}/{name}"))
                    except KeyError as ex:
                        raise PackError(f"pack has no {name} for {eid}") from ex
                plan = P.load_plan(Path(tmp) / "plan.yaml")
            if not P.plan_integrity(plan) or P.compute_hash(plan) != item.get("plan_hash") or plan["inputs"].get("name") != eid:
                raise PackError(f"frozen plan for {eid} does not match its recorded hash")
            if plan.get("status") != "draft" or plan.get("approval") is not None:
                raise PackError(f"frozen plan for {eid} is not a draft; a pack must never carry an approval")
            try:
                doc = json.loads(z.read(f"bundles/{eid}.json"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
                harvest.validate(doc, compact.manifest(plan, eid, 1))
            except (KeyError, ValueError, TypeError) as ex:
                raise PackError(f"recorded evidence for {eid} is invalid: {ex}") from ex
            plans[eid], bundles[eid] = plan, doc
    return dict(meta=meta, plans=plans, bundles=bundles)


def install(root, pack: dict) -> list[J.VersionRef]:
    """Create the pack's entries (frozen draft plans) in an example project and keep its evidence under recorded/."""
    refs = []
    for item in pack["meta"]["entries"]:
        eid = item["entry"]
        refs.append(J._new_entry(root, item.get("display_name") or eid, eid, pack["plans"][eid]))
    d = Path(root) / DIR
    d.mkdir(exist_ok=True)
    for eid, doc in pack["bundles"].items():
        J._write_atomic(d / f"{eid}.json", json.dumps(doc, indent=2, allow_nan=False))
    J._write_atomic(d / "pack.json", json.dumps(pack["meta"], indent=2))
    return refs


def label(root) -> dict | None:
    """Who recorded the pack this project was made from (None when there is none)."""
    p = Path(root) / DIR / "pack.json"
    try:
        meta = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return {k: meta.get(k, "") for k in _LABEL_KEYS} | dict(entries=[e.get("entry") for e in meta.get("entries", [])])


def available(root, entry_id: str) -> str | None:
    """None when the recorded run can be imported for this entry now; otherwise why not."""
    lab = label(root)
    if not lab or entry_id not in lab["entries"]:
        return "this project has no recorded run for this entry"
    vs = J.scan_version(root, entry_id)
    if vs.version != 1:
        return "the plan was edited (newer version): the recorded run belongs to v1 only"
    if not vs.approved:
        return "approve the plan first: reviewing it is your step"
    meta = json.loads((Path(root) / DIR / "pack.json").read_text())
    want = next(e["plan_hash"] for e in meta["entries"] if e["entry"] == entry_id)
    if vs.plan_hash != want:
        return "the plan differs from the one that was run"
    return None


def imported(root, entry_id: str) -> bool:
    """True when v1's outputs/ holds the pack's own evidence for every recorded job (compact records with the recorded
    source hash). The learner's own raw outputs or bundles are not the recorded run and do not count."""
    try:
        doc = json.loads((Path(root) / DIR / f"{entry_id}.json").read_text())
        out = J.version_dir(root, entry_id, 1) / "outputs"
        if not doc.get("records"):
            return False
        for stem, rec in doc["records"].items():
            stored = out / f"{stem}.compact.json"
            if not stored.is_file():
                return False
            have = (json.loads(stored.read_text()).get("records") or {}).get(stem) or {}
            if (have.get("source") or {}).get("sha256") != (rec.get("source") or {}).get("sha256"):
                return False
    except (OSError, ValueError, AttributeError, J.ProjectError):
        return False
    return True


def import_recorded(root, entry_id: str) -> dict:
    """Import an entry's recorded evidence through the normal compact import (all checks apply)."""
    why = available(root, entry_id)
    if why:
        raise PackError(f"{entry_id}: {why}")
    out = compact.import_bundle(root, entry_id, 1, Path(root) / DIR / f"{entry_id}.json")
    lab = label(root)
    out["recorded"] = (f"Recorded real calculation by {lab['recorded_by']} on {lab['recorded_at'][:10]}"
                       + (f" ({lab['site']})" if lab["site"] else "") + (f", {lab['program']}" if lab["program"] else "")
                       + ": imported as compact evidence, not computed in this project.")
    return out
