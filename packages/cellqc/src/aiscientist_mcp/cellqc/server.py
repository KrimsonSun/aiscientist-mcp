"""Typed MCP interface for the standalone CellQC pipeline."""

from typing import Annotated, Any, Literal

from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool
from pydantic import ConfigDict, Field, validate_call

from . import runtime

server = make_server(
    "cellqc",
    "QC Cell Ranger folders with raw AND filtered matrices. Use run_cellqc instead of "
    "run_scanpy_qc; do not remove doublets a second time. Poll cellqc_job_status for "
    "asynchronous jobs. Treat warnings, cell retention, ambient contamination and "
    "doublet agreement separately from execution success.",
    strict=True,
)

Count = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


@tool(server)
@validate_call(config=ConfigDict(strict=True, extra="forbid"))
def run_cellqc(
    analysis_id: str = "default",
    dataset: str = "",
    dataset_paths: list[str] | None = None,
    species: Literal["auto", "human", "mouse", "macaque"] = "auto",
    max_pct_mt: Percent = 10.0,
    min_counts: Count = 500,
    min_features: Count = 300,
    ambient_method: Literal["soupx", "decontx", "none"] = "soupx",
    ambient_compare: Literal["", "soupx", "decontx", "none"] = "decontx",
    chemistry: Literal["auto", "next_gem", "gem_x"] = "auto",
    nreaction: Positive = 1,
    doublet_decider: Literal["doubletfinder", "scdblfinder"] = "doubletfinder",
    n_top_genes: Positive = 2000,
    seed: Count = 42,
    warn_ambient_frac: Fraction = 0.25,
    warn_retained_below: Fraction = 0.5,
    samples: list[str] | None = None,
    rerun: bool = False,
) -> dict[str, Any]:
    """Run CellQC on raw and filtered Cell Ranger libraries, preserving the input delivery.

    Correct ambient RNA, filter counts/features/mitochondrial percentage, remove doublets,
    and compute nuclear fraction when a BAM exists. Merge libraries by species into normalized
    checkpoints with ambient-corrected integer counts and per-library QC tables and reports.
    Do not run a second doublet-removal step after this tool.

    dataset defaults to CELLQC_DATASET or AISCIENTIST_DATASET. Explicit dataset and extra
    dataset_paths must be inside the server's CELLQC_READ_ROOTS. analysis_id selects this
    server's isolated analysis folder; samples selects library IDs. Species and chemistry
    'auto' read Cell Ranger summaries. nreaction is a caller-supplied assumption, not measured.
    max_pct_mt=10 suits whole cells; the reference nuclei run used 5. warn_* values are
    engineering review guards, not scientific significance thresholds.

    Slurm/local return analysis_id, task_id and state; poll cellqc_job_status. Inline waits
    and includes the original QC result, checkpoint_path, artifact_root and settings_source.
    An unchanged completed request reuses its result; rerun forces a fresh attempt. Different
    requests in one analysis cannot run concurrently. Inspect QC warnings before using results.
    """
    arguments = {
        "species": species,
        "max_pct_mt": max_pct_mt,
        "min_counts": min_counts,
        "min_features": min_features,
        "ambient_method": ambient_method,
        "ambient_compare": "" if ambient_compare == "none" else ambient_compare,
        "chemistry": chemistry,
        "nreaction": nreaction,
        "doublet_decider": doublet_decider,
        "n_top_genes": n_top_genes,
        "seed": seed,
        "warn_ambient_frac": warn_ambient_frac,
        "warn_retained_below": warn_retained_below,
        "samples": samples or [],
    }
    with quiet():
        return runtime.submit(analysis_id, dataset, dataset_paths, arguments, rerun)


@tool(server)
def cellqc_job_status(analysis_id: str, task_id: str) -> dict[str, Any]:
    """Poll a CellQC task, returning execution state and its QC result when available.

    COMPLETED means execution succeeded; retention, ambient and doublet warnings still need
    review. FAILED carries the actual error. Slurm preemption uses AISCI_SLURM_FALLBACK.
    """
    with quiet():
        return runtime.result(runtime.workspace(analysis_id), task_id)


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
