"""MCP server visionkb-kg (stdio): read-only queries of the VisionKB knowledge graph through visionkb-api ``/v1``.

Flow: get_release -> search_entities (names to CURIEs) -> get_entity / get_relationships -> get_evidence;
get_gene_set (genes of a cell type or disease); query_atlas_expression (all genes x groups, no threshold);
find_datasets (atlases to bind for analysis).

Each tool is one (get_release: three) GET request. The result is the API envelope ``{status, kg_release, data,
page, warnings, error}`` plus ``http_status`` and ``request``; failures are ``status: "error"`` with the API's
error code, never a fallback.
"""

from typing import Annotated, Any, Literal, TypedDict

from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool
from pydantic import Field

from . import SERVER_NAME
from .api import add_paging, check_curie, error_envelope, get, segment, split_list
from .settings import ClientError, Settings

KnowledgeLevel = Literal[
    "knowledge_assertion", "logical_entailment", "not_provided", "observation", "prediction",
    "statistical_association", "text_co_occurrence",
]  # fmt: skip
Limit = Annotated[int, Field(ge=1, le=200, description="rows to return (the API caps at 200)")]
Offset = Annotated[
    int,
    Field(
        ge=0,
        le=10_000,
        description="rows to skip. For the next page pass paging.next_offset (offset + rows returned): the API may "
        "return fewer rows than limit, so offset + limit skips rows",
    ),
]

server = make_server(
    SERVER_NAME,
    "Read-only queries of the VisionKB knowledge graph (genes, retinal cell types, diseases, phenotypes, HECA "
    "atlas expression; Biolink 4.4.6) through visionkb-api. Start with get_release; resolve names to CURIEs "
    "with search_entities; then get_relationships (edges with knowledge_level and source) and get_evidence "
    "(StudyResults, publications) for an edge_id; get_gene_set; query_atlas_expression; find_datasets. Every "
    "result carries kg_release and warnings: cite kg_release and edge_id with any claim, and read warnings. "
    "List results can hold fewer rows than limit (the API trims to ~24,000 characters): read paging.summary, and "
    "page on with offset = paging.next_offset, never offset + limit. "
    "status 'error' means the API did not answer the question; do not substitute other knowledge for it.",
)


class Page(TypedDict):
    limit: int
    offset: int
    total: int | None


class ErrorInfo(TypedDict):
    code: str
    message: str


class Request(TypedDict):
    path: str
    params: dict[str, Any]


class Paging(TypedDict):
    returned: int  # rows in this result (may be fewer than limit: see trimmed)
    offset: int
    limit: int | None
    total: int | None
    trimmed: bool  # the API cut this page to fit its character cap
    next_offset: int | None  # offset for the next page (offset + returned); null on the last page
    trimmed_lists: dict[str, Any]  # every list the API cut: {"data": {"returned": n, "of": m}, ...}
    summary: str  # e.g. "trimmed: 7 of 20 rows returned ...; next page: offset=7"


class Result(TypedDict, total=False):
    status: Literal["ok", "error"]
    kg_release: str | None  # release that answered; null only when the API was not reached
    data: Any
    page: Page | None
    warnings: list[str]
    error: ErrorInfo | None
    http_status: int
    request: Request
    paging: Paging | None  # list tools only


def _call(path: str, params: dict[str, Any] | None = None, *, auth: bool = True) -> dict:
    try:
        cfg = Settings.from_env()
    except ClientError as exc:
        return error_envelope(exc.code, exc.message)
    return get(cfg, path, params, auth=auth)


def _invalid(exc: ClientError, path: str) -> dict:
    return error_envelope(exc.code, exc.message, request={"path": f"/v1{path}", "params": {}})


@tool(server)
def search_entities(
    q: Annotated[str, Field(min_length=1, max_length=200, description="CURIE, gene symbol, synonym or free text")],
    category: Annotated[str | None, Field(description="Biolink category, e.g. biolink:Gene, biolink:Cell")] = None,
    limit: Limit = 20,
) -> Result:
    """Resolve a name to graph entities: exact CURIE, then exact symbol, synonym, then full-text matches.

    Returns ``data: [{id, name, category, symbol, synonyms, match, score}]`` (ranked top-k; ``page.total`` is
    null). Use the ``id`` (e.g. HGNC:10012 for RHO, CL:0000604 retinal rod cell) in the other tools.
    """
    with quiet():
        return add_paging(_call("/entities/search", {"q": q, "category": category, "limit": limit}), has_offset=False)


@tool(server)
def get_entity(curie: Annotated[str, Field(description="CURIE, e.g. HGNC:10012, CL:0000604, MONDO:0019200")]) -> Result:
    """One node (id, name, category, symbol, synonyms, xrefs, description) with its degree by predicate.

    ``status: error`` with code ``not_found`` when the CURIE is not in the release.
    """
    with quiet():
        try:
            c = check_curie(curie, "curie")
        except ClientError as exc:
            return _invalid(exc, "/entities/{curie}")
        return _call(f"/entities/{segment(c)}")


@tool(server)
def get_relationships(
    subject: Annotated[str | None, Field(description="subject CURIE (e.g. a gene HGNC:10012)")] = None,
    object: Annotated[str | None, Field(description="object CURIE (e.g. a cell type CL:0000604)")] = None,
    predicate: Annotated[
        str | None, Field(description="Biolink predicate in the release, e.g. biolink:expressed_in")
    ] = None,
    object_category: Annotated[str | None, Field(description="Biolink category of the object")] = None,
    knowledge_level: Annotated[KnowledgeLevel | None, Field(description="Biolink knowledge level")] = None,
    species: Annotated[str | None, Field(description="NCBITaxon CURIE, e.g. NCBITaxon:9606")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Result:
    """Edges from ``subject`` and/or to ``object`` (at least one), ranked by evidence.

    Each row: ``edge_id, subject, predicate, object, qualifiers, knowledge_level, agent_type,
    primary_knowledge_source, publications (<= 5), evidence_count, status``. Pass ``edge_id`` to get_evidence.
    """
    with quiet():
        return add_paging(_call(
            "/relationships",
            {"subject": subject, "object": object, "predicate": predicate, "object_category": object_category,
             "knowledge_level": knowledge_level, "species": species, "limit": limit, "offset": offset},
        ))  # fmt: skip


@tool(server)
def get_evidence(
    edge_id: Annotated[str, Field(description="edge id from get_relationships, visionkb:e-<40 hex>")],
    limit: Limit = 20,
    offset: Offset = 0,
) -> Result:
    """The evidence behind one edge: ``{edge, study_results[], publications[]}``.

    Expression StudyResults give dataset_id, atlas_version, level, group_label, n_cells, n_donors,
    pct_expressing, mean_log1p_cp10k, pseudobulk_cp10k, n_donors_expressing; literature ones give pmid,
    supporting_text, grade. ``page`` pages the study results.
    """
    with quiet():
        try:
            e = check_curie(edge_id, "edge_id")
        except ClientError as exc:
            return _invalid(exc, "/evidence/{edge_id}")
        return add_paging(_call(f"/evidence/{segment(e)}", {"limit": limit, "offset": offset}), "study_results")


@tool(server)
def get_gene_set(
    entity: Annotated[str, Field(description="CL, CellState (visionkb:cs-...), MONDO or HP CURIE")],
    kind: Annotated[
        Literal["expressed", "disease_genes"] | None,
        Field(description="default: expressed for CL/CellState, disease_genes for MONDO/HP"),
    ] = None,
    dataset: Annotated[str | None, Field(description="restrict expression evidence to one dataset id")] = None,
    min_pct: Annotated[float | None, Field(ge=0.0, le=1.0, description="minimum pct_expressing (0-1)")] = None,
    descendants: Annotated[bool, Field(description="include subclass_of descendants (up to 3 hops)")] = True,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Result:
    """Genes expressed in a cell type/state, or genes associated with a disease/phenotype.

    Rows ``{gene_id, symbol, score, evidence}``; ``warnings`` say what ``score`` means. The graph holds only
    expression edges above the release's threshold: use query_atlas_expression for the full table.
    """
    with quiet():
        return add_paging(_call(
            "/gene-sets",
            {"entity": entity, "kind": kind, "dataset": dataset, "min_pct": min_pct, "descendants": descendants,
             "limit": limit, "offset": offset},
        ))  # fmt: skip


@tool(server)
def query_atlas_expression(
    genes: Annotated[
        list[str] | str, Field(description="gene CURIEs or symbols (at most 20), a list or comma-separated")
    ],
    dataset: Annotated[str | None, Field(description="dataset id, e.g. visionkb:ds-hrca-snrna")] = None,
    level: Literal["majorclass", "celltype", "region_majorclass"] = "majorclass",
    cell_type: Annotated[str | None, Field(description="cell_type_id CURIE or group label")] = None,
    sort: Literal["pseudobulk_cp10k", "mean_log1p_cp10k", "pct_expressing", "n_cells"] = "pseudobulk_cp10k",
    limit: Limit = 20,
    offset: Offset = 0,
) -> Result:
    """Per-group expression summaries from the HECA atlases (every gene x group, no threshold).

    Rows: dataset_id, atlas_version, dataset_version, level, group_label, cell_type_id, region, gene_id,
    gene_symbol, n_cells, n_donors, n_expressing, pct_expressing, mean_log1p_cp10k, pseudobulk_cp10k,
    n_donors_expressing. Values are descriptive; report donor support with any claim.
    """
    with quiet():
        return add_paging(_call(
            "/atlas/expression",
            {"genes": split_list(genes), "dataset": dataset, "level": level, "cell_type": cell_type, "sort": sort,
             "limit": limit, "offset": offset},
        ))  # fmt: skip


@tool(server)
def find_datasets(
    entities: Annotated[
        list[str] | str | None,
        Field(description="CURIEs (cell types/states, genes, tissue, disease, assay), a list or comma-separated"),
    ] = None,
    species: Annotated[str | None, Field(description="NCBITaxon CURIE")] = None,
    tissue: Annotated[str | None, Field(description="UBERON CURIE, e.g. UBERON:0000966 (retina)")] = None,
    assay: Annotated[str | None, Field(description="EFO CURIE")] = None,
    disease: Annotated[str | None, Field(description="MONDO CURIE")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Result:
    """Dataset registry records matching entities and filters, ranked, with ``matched`` reasons.

    Each record has id, version, title, species, tissue, assay, n_cells, n_donors, cell_types, access_level and
    ``bind`` (path, name, role) for binding the atlas in an analysis. Paths are HPC-side metadata.
    """
    with quiet():
        return add_paging(_call(
            "/datasets",
            {"entities": split_list(entities), "species": species, "tissue": tissue, "assay": assay,
             "disease": disease, "limit": limit, "offset": offset},
        ))  # fmt: skip


@tool(server)
def get_release() -> Result:
    """The served KG release and service state: ``/v1/health``, ``/v1/ready`` and ``/v1/release``.

    ``data: {ready, health, release}``; ``release`` is the manifest subset (release id, Biolink version, code
    versions, sources, atlases, counts, validation, predicates in the graph). ``status`` is ok only when the
    service is ready and the release manifest was returned.
    """
    with quiet():
        health = _call("/health", auth=False)
        if health.get("http_status") is None:  # API not reached (or not configured): nothing else to ask
            return health
        ready = _call("/ready", auth=False)
        release = _call("/release")
        warnings = list(dict.fromkeys(health["warnings"] + ready["warnings"] + release["warnings"]))
        err = None
        for name, part in (("ready", ready), ("release", release)):
            if part["status"] != "ok":
                e = part.get("error") or {"code": "error", "message": ""}
                err = err or {"code": e["code"], "message": f"{name}: {e['message']}"}
        return {
            "status": "error" if err else "ok",
            "kg_release": release.get("kg_release") or health.get("kg_release"),
            "data": {
                "ready": ready["status"] == "ok",
                "health": health.get("data"),
                "release": release.get("data"),
            },
            "page": None,
            "warnings": warnings,
            "error": err,
            "http_status": release.get("http_status", ready.get("http_status")),
            "request": {"path": "/v1/health,/v1/ready,/v1/release", "params": {}},
        }


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
