"""Reference-data loaders.

Another track compiles ``calibrations.yaml``, ``training_set.csv`` and
``method_rules.yaml`` into this directory.  The loaders return ``None`` when a
file is not present (yet), so the core library works without them.
"""
from __future__ import annotations
import csv
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
SITES_DIR = DATA_DIR / "sites"


def _yaml(name):
    p = DATA_DIR / name
    if not p.exists():
        return None
    import yaml
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_calibrations():
    """Contents of calibrations.yaml (or None)."""
    return _yaml("calibrations.yaml")


def load_method_rules():
    """Contents of method_rules.yaml (or None)."""
    return _yaml("method_rules.yaml")


def load_training_set():
    """training_set.csv as a list of dict rows (or None)."""
    p = DATA_DIR / "training_set.csv"
    if not p.exists():
        return None
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_method_rules_md() -> str | None:
    p = DATA_DIR / "method_rules.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


REFSETS_DIR = DATA_DIR / "refsets"


def load_refset(name: str = "mcwilliams2017") -> dict | None:
    """Bundled reference-set definition (YAML)."""
    p = REFSETS_DIR / f"{name}.yaml"
    if not p.exists():
        return None
    import yaml
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def available_refsets() -> list[str]:
    return sorted(p.stem for p in REFSETS_DIR.glob("*.yaml"))


def user_home():
    """Per-user store for fitted calibrations (``$MOSSBAUER_DFT_HOME`` or ``~/.mossbauer_dft``)."""
    import os
    return Path(os.environ.get("MOSSBAUER_DFT_HOME", Path.home() / ".mossbauer_dft"))


def available() -> dict:
    return {n: (DATA_DIR / n).exists() for n in ("calibrations.yaml", "training_set.csv", "method_rules.yaml")}
