"""Typed literature tools over the common Slurm/local/inline job executors."""

from pathlib import Path
from typing import Annotated, Any, Literal

from aiscientist_mcp.common.server import quiet, run_stdio
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from . import SERVER_NAME
from .runtime import status, submit
from .strict_server import StrictServer

server = StrictServer(
    SERVER_NAME,
    instructions="Biomedical literature tools. literature_search uses Europe PMC; "
    "search_corpus retrieves verbatim passages without a language model. deep_literature and "
    "extract_haploinsufficient_genes use only the server's explicitly configured model. "
    "Reuse analysis_id for a workflow. Poll literature_job_status for asynchronous jobs; "
    "inline execution returns the original result fields plus job identity.",
)


class CurationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: str
    definition: str
    counts_when: list[str]
    does_not_count: list[str]
    classes: list[str]
    scope: str
    requests: list[str]
    terms: list[str]


def register(fn):
    doc = Path(__file__).with_name("descriptions") / f"{fn.__name__}.md"
    description = fn.__doc__ + ("\n\n" + doc.read_text() if doc.exists() else "")
    return server.tool(description=description, structured_output=True)(fn)


def _call(name, values):
    args = dict(values)
    analysis_id, rerun = args.pop("analysis_id"), args.pop("rerun")
    if args.get("spec") is not None:
        args["spec"] = args["spec"].model_dump()
    with quiet():
        try:
            return submit(name, {key: value for key, value in args.items() if value is not None}, analysis_id, rerun)
        except ValueError as exc:
            raise ToolError(str(exc)) from None


@register
def literature_search(
    query: Annotated[str, Field(min_length=1)],
    limit: Annotated[int, Field(ge=1, le=25)] = 8,
    pubmed_only: bool = False,
    abstracts: bool = False,
    analysis_id: str = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Search Europe PMC for real biomedical citations. Sends the query to this public service;
    no language model is called. Returns ranked papers with title, authors, year, DOI and PMID."""
    return _call("literature_search", locals())


@register
def search_corpus(
    queries: Annotated[list[str], Field(min_length=1, max_length=6)],
    top_k: Annotated[int, Field(ge=1, le=30)] = 12,
    max_per_paper: Annotated[int, Field(ge=1, le=30)] = 3,
    terms: Annotated[list[str], Field(max_length=12)] | None = None,
    pmids: list[str] | None = None,
    analysis_id: str = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Retrieve verbatim passages from the configured corpus, with PMID, page and citation.
    Hybrid BM25 and local PubMedBERT retrieval; no language model or external embedding call."""
    return _call("search_corpus", locals())


@register
def deep_literature(
    question: Annotated[str, Field(min_length=1)],
    analysis_id: str = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Answer a focused question with PaperQA over the configured corpus and prebuilt index.
    Uses only LITERATURE_MODEL_ENDPOINT / LITERATURE_MODEL; reports empty evidence as failure."""
    return _call("deep_literature", locals())


@register
def extract_haploinsufficient_genes(
    n_requests: Annotated[int, Field(ge=1)] = 16,
    k_per_request: Annotated[int, Field(ge=1)] = 50,
    keyword_round: Literal[0, 1] = 1,
    term_round: Literal[0, 1] = 1,
    max_term_passages: Annotated[int, Field(ge=0)] = 300,
    k_per_gene: Annotated[int, Field(ge=0)] = 15,
    max_quotes_per_gene: Annotated[int, Field(ge=1)] = 3,
    concurrency: Annotated[int, Field(ge=1)] = 16,
    max_passages: Annotated[int, Field(ge=0)] = 0,
    pilot_fraction: Annotated[float, Field(ge=0, le=1)] = 0.01,
    max_cost_usd: Annotated[float, Field(ge=0)] = 0.0,
    check_unread: Literal[0, 1] | None = None,
    max_unread_passages: Annotated[int, Field(ge=0)] = 600,
    pmids: list[str] | None = None,
    spec: CurationSpec | None = None,
    analysis_id: str = "default",
    rerun: bool = False,
) -> dict[str, Any]:
    """Curate genes explicitly reported as haploinsufficient in retinal disease. Preserve the
    two retrieval rounds, exact-quote verification, HGNC normalization, review flags, pilot
    and resumable judgments. Writes genes.csv, hits.csv and requests.csv under this analysis.
    A spec can define another curation target. Model credentials are server settings only.
    A positive max_cost_usd requires an operator-configured priced model relay.
    The pilot estimates total cost; subsequent calls stop at the spending cap."""
    return _call("extract_haploinsufficient_genes", locals())


@server.tool(structured_output=True)
def literature_job_status(analysis_id: str, task_id: str) -> dict[str, Any]:
    """Poll a submitted literature task. COMPLETED includes its original result; FAILED
    keeps the error and any partial result. No new analysis is started by polling."""
    with quiet():
        try:
            return status(analysis_id, task_id)
        except ValueError as exc:
            raise ToolError(str(exc)) from None


@server.resource(
    "literature://results/{analysis_id}/{task_id}/{offset}",
    mime_type="application/json",
    description="Read a completed task's full JSON evidence in 8192-byte base64 pages. "
    "Start at offset 0; validate total_bytes and sha256, then follow next_offset. "
    "No analysis or model call is started. Each page is bounded; tool responses remain previews.",
)
def complete_result(analysis_id: str, task_id: str, offset: str) -> str:
    from .result_resource import result_chunk

    with quiet():
        return result_chunk(analysis_id, task_id, offset)


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
