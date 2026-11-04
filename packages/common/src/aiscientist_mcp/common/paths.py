"""Identifier and path checks: every tool argument that names a folder goes through here."""

import re
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def safe_id(value, what="id"):
    if not ID_RE.match(value or "") or ".." in value:
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def child_dir(root, name, what="id", must_exist=None):
    """``root/name`` for a validated ``name``; with ``must_exist``, that file must exist inside it."""
    d = Path(root) / safe_id(name, what)
    if must_exist and not (d / must_exist).exists():
        raise ValueError(f"unknown {what}: {name!r}")
    return d
