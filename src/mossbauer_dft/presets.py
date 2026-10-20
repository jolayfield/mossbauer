"""Named method presets kept in the project (``presets.json``): a group's protocol, applied by name.

A preset holds only batch-wide method options (functionals, geometry level, calibration route, highfield, resources).
Plans made from a preset are ordinary plans: the preset's name is not recorded in them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import batch as B
from . import project as J

SCHEMA = "1"
FILE = "presets.json"
METHOD_KEYS = sorted(B.OPTION_KEYS - {"user_refs"})   # what applying a preset sets or resets on an entry
_NAME = re.compile(r"^[A-Za-z0-9._\- ]{1,64}$")


def _path(root) -> Path:
    return Path(root) / FILE


def _read(root) -> dict:
    J.open_project(root)
    p = _path(root)
    if not p.is_file():
        return dict(schema_version=SCHEMA, presets={})
    try:
        doc = json.loads(p.read_text())
        if not isinstance(doc.get("presets"), dict):
            raise ValueError("no 'presets' mapping")
    except (ValueError, AttributeError) as e:
        raise J.ProjectError(f"{FILE} is damaged ({e}); fix or remove it") from e
    return doc


def _clean_name(name) -> str:
    n = str(name or "").strip()
    if not _NAME.match(n):
        raise J.ProjectError("preset name must be 1-64 characters: letters, digits, spaces, '.', '-' or '_'")
    return n


def _validate(options: dict) -> dict:
    opts = dict(options or {})
    for key in ("broken_symmetry", "user_refs"):
        if key in opts:
            raise J.ProjectError(f"a preset cannot hold {key}: it belongs to one structure or entry")
    if opts.get("calibration_strategy") == "user_refs":
        raise J.ProjectError("a preset cannot use calibration_strategy user_refs: the references belong to one entry")
    if opts.get("calibration_strategy") == "auto":
        del opts["calibration_strategy"]  # "auto" is the rule default, stored as absent like every other default
    return B.normalize_options(opts)


def save(root, name, options: dict, description: str = "", replace: bool = False) -> dict:
    """Store ``options`` under ``name``; an existing name is refused unless ``replace``."""
    name = _clean_name(name)
    opts = _validate(options)
    doc = _read(root)
    if name in doc["presets"] and not replace:
        raise J.ProjectError(f"preset {name!r} already exists; replace it explicitly")
    doc["presets"][name] = dict(name=name, description=str(description or "").strip(), options=opts)
    J._write_atomic(_path(root), json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return doc["presets"][name]


def list_presets(root) -> list[dict]:
    presets = _read(root)["presets"]
    return [presets[n] for n in sorted(presets)]


def _lookup(presets: dict, name) -> dict:
    if name not in presets:
        have = ", ".join(sorted(presets)) or "none saved yet"
        raise J.ProjectError(f"no preset {name!r} in this project (available: {have})")
    return presets[name]


def get(root, name) -> dict:
    """The stored preset, re-validated: a hand-edited presets.json cannot smuggle per-entry keys into a plan."""
    preset = dict(_lookup(_read(root)["presets"], name))
    preset["options"] = _validate(preset.get("options") or {})
    return preset


def delete(root, name) -> None:
    doc = _read(root)
    _lookup(doc["presets"], name)
    del doc["presets"][name]
    J._write_atomic(_path(root), json.dumps(doc, indent=2, sort_keys=True) + "\n")


def merge(preset: dict, explicit: dict) -> dict:
    """Options for a new entry or batch: the preset's, with any explicitly given option winning."""
    return {**preset["options"], **(explicit or {})}


def resolve(root, name, explicit: dict | None = None) -> dict:
    """Options from preset ``name`` with any explicitly given option winning (new entries and batches)."""
    return merge(get(root, name), explicit or {})


def apply_edits(entry_overrides: dict, preset_options: dict) -> dict:
    """Override edit that makes an entry's method options equal the preset's: preset keys are set, other method keys
    the entry has are cleared to the rule default ("" for functionals, None otherwise).  Broken symmetry and reference
    structures are not method options and are left alone."""
    edit = dict(preset_options)
    for k in METHOD_KEYS:
        if k not in edit and entry_overrides.get(k) not in (None, "", False):
            edit[k] = "" if k.endswith("functional") else None
    return edit


def apply_pin(proposal: dict) -> str:
    """Hash of the plan version and the exact edit a preset preview showed (changes if either changes)."""
    import hashlib
    blob = json.dumps(dict(base=proposal["base_hash"], edits=proposal["edits"]), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def preview_apply(root, entry_id: str, name: str) -> dict:
    """Revision preview of applying preset ``name`` to the entry's newest version (save it with ``revision.save``)."""
    from . import revision
    preset = get(root, name)
    current = J.load_version(root, entry_id)["inputs"].get("overrides") or {}
    return revision.preview(root, entry_id, overrides=apply_edits(current, preset["options"]))
