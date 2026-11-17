"""Versioned CellQC defaults bundled independently of the application and skill registry."""

import hashlib
import json
from importlib.resources import files


def defaults_source() -> dict:
    return json.loads(files(__package__).joinpath("assets/source.json").read_text())


def load_defaults() -> dict:
    raw = files(__package__).joinpath("assets/cellqc-defaults.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != defaults_source()["sha256"]:
        raise ValueError("bundled CellQC defaults checksum mismatch")
    value = json.loads(raw)
    if not (
        isinstance(value["genesets"], dict)
        and isinstance(value["doublet"]["rate"], dict)
        and int(value["doublet"]["capacity"]) > 0
        and all(k in value["thresholds"] for k in ("min_counts", "min_features", "max_pct_mt"))
    ):
        raise ValueError("invalid bundled CellQC defaults")
    return value
