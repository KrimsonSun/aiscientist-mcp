"""Bounded file inspection and reproducible conceptual schematics."""

from functools import wraps
from typing import Annotated, Any

from aiscientist_mcp.common import tasks
from aiscientist_mcp.common.response import bounded_result
from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import SERVER_NAME
from .guards import check_dot
from .settings import CONFIG, admitted, model_configuration

server = make_server(
    SERVER_NAME,
    "Inspect an admitted dataset before making format assumptions. "
    "Render conceptual diagrams from Graphviz DOT. These short tools run inline by default; "
    "when configured for Slurm/local, poll data_utilities_job_status until COMPLETED.",
    strict=True,
)


def expected_errors(fn):
    @wraps(fn)
    def checked(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            raise ToolError(str(exc)) from None

    return checked


def _preview(result):
    full_result = result.get("full_result")
    return bounded_result(result, full_result=full_result) if full_result else result


def _submit(analysis_id, operation, args, *, inputs=(), settings=None, rerun=False):
    with quiet():
        out = tasks.submit(CONFIG, analysis_id, operation, args, inputs=inputs, settings=settings, rerun=rerun)
    if out.get("state") == "FAILED":
        raise ValueError(
            f"{operation} task {out['task_id']} failed: "
            f"{out.get('error', out.get('note', 'utility failed'))}; poll data_utilities_job_status for evidence"
        )
    return _preview(out)


@tool(server)
@expected_errors
def inspect_dataset(
    path: Annotated[
        str, Field(strict=True, description="Admitted server-side file; omit to use the bound dataset.")
    ] = "",
    analysis_id: str = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Skim an uploaded file of any type and return deterministic peek evidence plus a description:
    VCF assembly/sample IDs, HDF5 tree, table columns or an unknown/binary descriptor. Only a bounded
    head is read, never the whole matrix. An explicitly configured model may summarize that evidence;
    otherwise the original deterministic fallback is returned. Do not invent facts beyond it."""
    source = admitted(path)
    return _submit(
        analysis_id,
        "inspect_dataset",
        {"path": str(source)},
        inputs=[str(source)],
        settings=model_configuration(),
        rerun=rerun,
    )


@tool(server)
@expected_errors
def make_schematic(
    source: Annotated[str, Field(strict=True, description="The diagram in Graphviz DOT.")],
    basename: Annotated[str, Field(strict=True, description="Short name for the figure file.")] = "schematic",
    analysis_id: str = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Render a conceptual workflow, pathway or mechanism as a PNG from auditable Graphviz DOT text.
    A deterministic renderer draws the figure. Use analysis tools for data plots. Example:
    digraph { QC -> Clustering -> "DE per cluster" -> Enrichment }. External file/image references
    are refused; output always stays in this analysis's artifacts/figures directory."""
    with quiet():
        check_dot(source)
    return _submit(analysis_id, "make_schematic", {"source": source, "basename": basename}, rerun=rerun)


@tool(server)
@expected_errors
def data_utilities_job_status(analysis_id: str, task_id: str) -> dict[str, Any]:
    """Poll an inspection or schematic task. COMPLETED includes its original structured result;
    FAILED includes the error. A missing Graphviz dependency is never a successful figure."""
    with quiet():
        return _preview(tasks.status(CONFIG, analysis_id, task_id))


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
