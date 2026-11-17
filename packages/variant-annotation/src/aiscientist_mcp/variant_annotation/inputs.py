"""Restrict caller-selected VCF and BED files to deployment-approved roots."""

import os
from pathlib import Path

from aiscientist_mcp.common.tasks import read_path


def read_roots() -> list[Path]:
    roots = [Path(value) for value in os.environ.get("VARIANT_READ_ROOTS", "").split(os.pathsep) if value]
    bound = os.environ.get("VARIANT_DATASET") or os.environ.get("AISCIENTIST_DATASET", "")
    if bound:
        roots.append(Path(bound))
    return roots


def input_vcf(value: str = "") -> Path:
    value = value or os.environ.get("VARIANT_DATASET") or os.environ.get("AISCIENTIST_DATASET", "")
    if not value:
        raise ValueError("set VARIANT_DATASET or supply vcf_path under VARIANT_READ_ROOTS")
    path = read_path(value, read_roots(), directory=False)
    if not path.name.lower().endswith((".vcf", ".vcf.gz")):
        raise ValueError("vcf_path must be a .vcf or .vcf.gz file")
    return path


def input_bed(value: str) -> Path:
    return read_path(value, read_roots(), directory=False)


def confine_read(value: str, ctx=None):
    """Adapter for the ported REST implementation; always uses the same server bounds."""
    try:
        return read_path(value, read_roots(), directory=False), ""
    except (OSError, ValueError) as exc:
        return None, str(exc)
