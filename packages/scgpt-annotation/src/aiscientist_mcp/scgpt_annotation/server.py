"""Typed scGPT annotation service; inference stays in the validated GPU environment."""

from functools import wraps
from typing import Annotated, Any

from aiscientist_mcp.common import tasks
from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import SERVER_NAME
from .settings import CONFIG, admitted, inference_settings

server = make_server(
    SERVER_NAME,
    "Reference-based per-cell scGPT annotation. Submit scgpt_annotate, then poll "
    "scgpt_job_status until COMPLETED. Use the same approved model and inspect species_harmonization. "
    "Cross-species transfer assumes orthology by gene name; confirm glial and vascular labels with markers.",
    strict=True,
)


def expected_errors(fn):
    @wraps(fn)
    def checked(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            # Expected admission/task errors contain no credentials. MCP otherwise
            # masks ValueError and loses the task ID needed to poll failure evidence.
            raise ToolError(str(exc)) from None

    return checked


@tool(server)
@expected_errors
def scgpt_annotate(
    model_dir: Annotated[
        str, Field(strict=True, description="Approved reference model directory; empty uses SCGPT_MODEL_DIR.")
    ] = "",
    dataset: Annotated[
        str, Field(strict=True, description="Query .h5ad inside SCGPT_READ_ROOTS; empty uses SCGPT_DATASET.")
    ] = "",
    analysis_id: str = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Transfer per-cell labels and confidence from an approved pretrained reference atlas using the
    validated scGPT harness on a GPU. This is a gene-expression transformer, not an LLM or marker
    naming. A mouse query may use case-folded human gene names: that assumption is reported.
    In the prior mouse-retina validation, neuronal labels agreed better than glia and vascular
    labels; check those classes against independent markers. No model is downloaded or invented.
    Slurm/local returns a task to poll; inline returns when the caller's GPU allocation finishes."""
    with quiet():
        source, model, inputs = admitted(dataset, model_dir)
        settings, harness_files = inference_settings()
        result = tasks.submit(
            CONFIG,
            analysis_id,
            "scgpt_annotate",
            {"dataset": str(source), "model_dir": str(model)},
            inputs=inputs + harness_files,
            settings=settings,
            rerun=rerun,
        )
    if result.get("state") == "FAILED":
        raise ValueError(
            f"scGPT task {result['task_id']} failed: {result.get('error', 'inference failed')}; poll scgpt_job_status for its evidence"
        )
    return result


@tool(server)
@expected_errors
def scgpt_job_status(analysis_id: str, task_id: str) -> dict[str, Any]:
    """Poll GPU annotation. COMPLETED includes predictions_csv, label counts and species provenance;
    FAILED includes the error. Do not describe a queued, failed or missing task as an annotation."""
    with quiet():
        return tasks.status(CONFIG, analysis_id, task_id)


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
