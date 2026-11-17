"""Native MCP tools for grounded phenotype diagnosis, independent of the app."""

from typing import Annotated, Any

from aiscientist_mcp.common.server import quiet, run_stdio
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .runtime import status, submit
from .strict_server import StrictServer

server = StrictServer(
    "phenotype-diagnosis",
    instructions=(
        "First map the patient's clinical text to grounded HPO IDs. Use run_lirical for raw disease ranking "
        "or diagnose_disease for literature adjudication. A final_score is a ranking score, never a probability. "
        "Poll phenotype_job_status for pending jobs. References, model and literature endpoints are server settings."
    ),
)
_ACTION = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
Identifier = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]


def _submit(name, arguments, analysis_id, rerun):
    with quiet():
        try:
            result = submit(name, arguments, analysis_id, rerun)
            if result.get("state") == "FAILED":
                raise ValueError(
                    f"{result.get('error') or result.get('note') or 'operation failed'}; task_id={result['task_id']}"
                )
            return result
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)) from None


@server.tool(structured_output=True, annotations=_ACTION)
def map_phenotype_to_hpo(
    text: Annotated[
        str, Field(min_length=1, description="Clinical description of this patient's observed/absent findings.")
    ],
    k: Annotated[int, Field(ge=1, le=30)] = 8,
    analysis_id: Identifier = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Map free clinical text to the bundled HPO closed set. No model: conservative lexical mapping; ambiguous negation/family history returns needs_llm mode. Pass observed/excluded HPO IDs to downstream tools."""
    return _submit("map_phenotype_to_hpo", {"text": text, "k": k}, analysis_id, rerun)


@server.tool(structured_output=True, annotations=_ACTION)
def run_lirical(
    hpo_terms: Annotated[list[str], Field(min_length=1, description="Observed HPO IDs from map_phenotype_to_hpo.")],
    excluded_hpo: list[str] = [],  # noqa: B006 - read-only, copied into the request
    sample_id: str = "sample-1",
    vcf_path: str = "",
    analysis_id: Identifier = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Rank diseases with LIRICAL using grounded observed/excluded HPO terms. If a VCF and matching server Exomiser reference exist, score genotype-aware; otherwise phenotype-only. Returns original calibrated probabilities, artifacts and job IDs."""
    return _submit(
        "run_lirical",
        {"hpo_terms": hpo_terms, "excluded_hpo": excluded_hpo, "sample_id": sample_id, "vcf_path": vcf_path},
        analysis_id,
        rerun,
    )


@server.tool(structured_output=True, annotations=_ACTION)
def diagnose_disease(
    hpo_terms: Annotated[list[str], Field(min_length=1, description="Observed grounded HPO IDs.")],
    excluded_hpo: list[str] = [],  # noqa: B006 - read-only, copied into the request
    candidate_genes: list[str] = [],  # noqa: B006 - read-only, copied into the request
    max_literature_queries: Annotated[int, Field(ge=0, le=100)] = 6,
    sample_id: str = "sample-1",
    vcf_path: str = "",
    analysis_id: Identifier = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Adjudicate LIRICAL and retrieved literature into one differential. The server's literature MCP connection can rescue uncurated candidate genes and flag contradictions. Evidence grades and ranking scores never overwrite LIRICAL probabilities; missing evidence remains ungraded."""
    return _submit(
        "diagnose_disease",
        {
            "hpo_terms": hpo_terms,
            "excluded_hpo": excluded_hpo,
            "candidate_genes": candidate_genes,
            "max_literature_queries": max_literature_queries,
            "sample_id": sample_id,
            "vcf_path": vcf_path,
        },
        analysis_id,
        rerun,
    )


@server.tool(structured_output=True, annotations=_READ)
def phenotype_job_status(analysis_id: Identifier, task_id: Identifier) -> dict[str, Any]:
    """Read the durable state and original result of one phenotype task, including failed jobs."""
    with quiet():
        try:
            return status(analysis_id, task_id)
        except (ValueError, OSError) as exc:
            raise ToolError(str(exc)) from None


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
