# ruff: noqa: RUF013
# Omitted legacy parameters have no default, and explicit null is invalid. Pydantic validates
# supplied values strictly; the unvalidated None default is removed before execution and listing.
"""Native typed MCP tools for the independently packaged scRNA algorithms."""

from typing import Annotated, Any, Literal

from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import SERVER_NAME
from .runtime import SPECS, status, submit

server = make_server(
    SERVER_NAME,
    "Single-cell analysis in a confined, named workspace. Run QC first, then downstream tools in the same analysis_id. With Slurm/local, poll scrna_job_status until COMPLETED before the next step; inline returns the completed result. Use only admitted datasets.",
    strict=True,
)


def analysis_tool(fn):
    spec = SPECS[fn.__name__]
    fn.__doc__ = spec["description"] + "\n\n" + spec["documentation"]
    return tool(server)(fn)


def _omit_default(schema):
    schema.pop("default", None)


def _call(name, values):
    values = dict(values)
    analysis_id = values.pop("analysis_id")
    dataset = values.pop("dataset")
    rerun = values.pop("rerun")
    args = {key: value for key, value in values.items() if value is not None}
    try:
        with quiet():
            return submit(name, args, analysis_id, dataset, rerun)
    except (ValueError, FileNotFoundError, PermissionError) as exc:
        raise ToolError(str(exc)) from None


@analysis_tool
def run_scanpy_qc(
    min_genes: Annotated[
        int,
        Field(
            description="drop a cell detecting fewer genes than this — an empty droplet or a dying cell", strict=True
        ),
    ] = 200,
    min_cells: Annotated[
        int,
        Field(
            description="drop a gene detected in fewer cells than this — too sparse to support any test", strict=True
        ),
    ] = 3,
    max_pct_mt: Annotated[
        float,
        Field(
            description="drop a cell whose reads are more than this percent mitochondrial (a stressed or lysed cell). 10 is the common working threshold for tissue; the Seurat and scanpy tutorials use 5 for PBMC, and single NUCLEI need far less (1-5) because a nucleus should carry almost no mitochondrial signal. Raise it only for a tissue known to be mitochondria-rich, and say so",
            strict=True,
        ),
    ] = 10.0,
    n_top_genes: Annotated[
        int,
        Field(
            description="how many highly-variable genes to keep for the embedding — more genes carry more structure and more noise. 2000 is Seurat's default and the usual starting point",
            strict=True,
        ),
    ] = 2000,
    warn_removed_pct: Annotated[
        float,
        Field(
            description="warn when QC discards more than this percent of cells. An ENGINEERING guard, not a literature threshold: losing half a dataset usually means a threshold is wrong for this tissue, and it should be checked before the result is used",
            strict=True,
        ),
    ] = 50.0,
    mito_prefix: Annotated[
        str,
        Field(
            description="the name prefix that marks a mitochondrial gene, matched without regard to case, so the default also covers mouse 'mt-'. It is the only way `max_pct_mt` knows which genes to count: a dataset whose genes match nothing gets no mitochondrial filter at all",
            strict=True,
        ),
    ] = "MT-",
    gene_symbols_key: Annotated[
        str,
        Field(
            description="a `var` column holding gene SYMBOLS, for data whose gene names are Ensembl IDs (cellxgene files keep the symbols in `feature_name`). The mitochondrial prefix is then matched against that column. Empty = match the gene names themselves",
            strict=True,
        ),
    ] = "",
    input: Annotated[
        str,
        Field(
            description="an .h5ad to QC INSTEAD of the bound dataset — e.g. a subset or merge a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the bound dataset.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_scanpy_qc", locals())


@analysis_tool
def run_clustering(
    resolution: Annotated[
        float, Field(description="Leiden granularity: higher splits the cells into more, smaller clusters", strict=True)
    ] = 1.0,
    n_pcs: Annotated[int, Field(description="principal components fed into the neighbourhood graph", strict=True)] = 30,
    n_neighbors: Annotated[
        int,
        Field(description="neighbours per cell in that graph — larger gives smoother, coarser structure", strict=True),
    ] = 15,
    select_resolution: Annotated[
        bool,
        Field(description="choose the resolution by bootstrap stability instead of accepting the default", strict=True),
    ] = False,
    n_bootstrap: Annotated[
        int, Field(description="resampling rounds per candidate resolution when selecting one", strict=True)
    ] = 10,
    subsample_frac: Annotated[float, Field(description="fraction of cells per bootstrap round", strict=True)] = 0.8,
    stability_min: Annotated[
        float, Field(description="minimum adjusted Rand index a resolution must clear to be called stable", strict=True)
    ] = 0.9,
    max_sweep_cells: Annotated[
        int, Field(description="cap on the cells used for the stability sweep", strict=True)
    ] = 20000,
    resolution_candidates: Annotated[
        list[float],
        Field(
            description="resolutions to try when select_resolution is on", strict=True, json_schema_extra=_omit_default
        ),
    ] = None,
    input: Annotated[
        str,
        Field(
            description="an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_clustering", locals())


@analysis_tool
def run_de(
    groupby: Annotated[str, Field(description="the obs column whose levels are compared", strict=True)] = "leiden",
    method: Annotated[
        Literal["wilcoxon", "t-test", "t-test_overestim_var", "logreg"],
        Field(description="the rank test rank_genes_groups uses"),
    ] = "wilcoxon",
    n_genes: Annotated[
        int, Field(description="rows kept per group — per DIRECTION when it is a contrast", strict=True)
    ] = 50,
    reference: Annotated[
        str,
        Field(
            description="the baseline level. 'rest' gives one-vs-rest MARKERS; a named level (e.g. 'WT') gives a real condition-vs-control contrast",
            strict=True,
        ),
    ] = "rest",
    stratify_by: Annotated[
        str,
        Field(
            description="an EXISTING cell-type column — runs the contrast separately within each cell type instead of pooling them",
            strict=True,
        ),
    ] = "",
    min_cells: Annotated[
        int,
        Field(
            description="a group with fewer cells than this in either arm is skipped. 30 is the usual rule-of-thumb floor for the rank test's normal approximation — an ENGINEERING guard, not a literature threshold",
            strict=True,
        ),
    ] = 30,
    min_pct: Annotated[
        float,
        Field(
            description="a gene must be detected in at least this fraction of the cells of one of the two populations compared, or it is not tested at all (Seurat FindMarkers' min.pct). Without it most genes enter the test undetected — on the DDX41 retina object 2/3 did — which triples the BH denominator and floods the ranking with divide-by-zero fold-changes above 2^20. 0 disables",
            strict=True,
        ),
    ] = 0.1,
    tie_correct: Annotated[
        bool,
        Field(
            description="tie-correct the Wilcoxon normal approximation. A single-cell matrix is ~90% zeros, so ties dominate every comparison; scanpy's default (False) is anti-conservative on sparse data",
            strict=True,
        ),
    ] = True,
    padj: Annotated[
        float, Field(description="adjusted-p (Benjamini-Hochberg) cutoff for calling a gene significant", strict=True)
    ] = 0.05,
    lfc: Annotated[
        float,
        Field(
            description="minimum |log2 fold-change| for calling a gene significant — applied together with `padj`, and drawn as the volcano's vertical line. 0.25 is the single-cell convention (Seurat's FindMarkers threshold); 1.0 is a bulk-RNA habit and hides most real single-cell effects",
            strict=True,
        ),
    ] = 0.25,
    force: Annotated[
        bool,
        Field(
            description="run a pooled per-cell test across a CONDITION column anyway. The result is pseudoreplicated and must be reported as non-inferential",
            strict=True,
        ),
    ] = False,
    groups: Annotated[
        list[str],
        Field(
            description="restrict the comparison to these levels of groupby",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    input: Annotated[
        str,
        Field(
            description="an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_de", locals())


@analysis_tool
def run_enrichment(
    top_n_genes: Annotated[
        int,
        Field(
            description="optional cap on how many genes enter the over-representation test. 0 means no cap — use every gene passing the thresholds below, which is the standard way ORA is run. Set it only to deliberately shorten a very long list, and say that you did",
            strict=True,
        ),
    ] = 0,
    top_n_terms: Annotated[int, Field(description="how many enriched terms to report per group", strict=True)] = 10,
    padj: Annotated[
        float,
        Field(
            description="adjusted-p cutoff a DE gene must clear to enter the test — the same Benjamini-Hochberg cutoff run_de applies",
            strict=True,
        ),
    ] = 0.05,
    lfc: Annotated[
        float,
        Field(
            description="minimum |log2 fold-change| a DE gene must clear to enter the test. Pairing an effect-size floor with the p-value cutoff is what keeps a list of thousands of barely-changed genes from returning only large generic terms; 0.25 is the single-cell convention",
            strict=True,
        ),
    ] = 0.25,
    gene_sets: Annotated[
        list[str],
        Field(description="local gene-set libraries to test against", strict=True, json_schema_extra=_omit_default),
    ] = None,
    background: Annotated[
        int,
        Field(
            description="ORA background size; omit to use the tested universe",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    split_direction: Annotated[
        bool,
        Field(description="test up- and down-regulated genes separately", strict=True, json_schema_extra=_omit_default),
    ] = None,
    groupby: Annotated[
        str,
        Field(
            description="which DE table to read, by the key in its file name tables/de_<groupby>_all.csv: the column run_de grouped by (e.g. leiden, majorclass), or the `de_key` run_pseudobulk_de returned (pseudobulk_<group_key>), not the `group` column inside the table; omit to find the table automatically, a replicated pseudobulk test first",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    genes: Annotated[
        list[str],
        Field(
            description="explicit gene list; omit so the DE tables are found instead",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_enrichment", locals())


@analysis_tool
def run_depth_matched_de(
    groupby: Annotated[
        str, Field(description="the obs column holding the experimental CONDITION", strict=True)
    ] = "sampleid",
    reference: Annotated[
        str,
        Field(
            description="the CONTROL level of `groupby` (e.g. 'WT'). Required — the check is a two-level contrast",
            strict=True,
        ),
    ] = "",
    stratify_by: Annotated[
        str,
        Field(
            description="the CELL-TYPE column; the check runs separately within each type, because depth imbalance differs per type and a pooled answer hides it",
            strict=True,
        ),
    ] = "",
    n_genes: Annotated[
        int,
        Field(
            description="how many top-ranked genes per direction enter the comparison. Deep enough that the correlation is not driven by three genes, shallow enough to stay the list a reader would actually look at",
            strict=True,
        ),
    ] = 100,
    min_cells: Annotated[
        int,
        Field(
            description="skip a cell type with fewer cells than this in either arm — the usual floor for the rank test's normal approximation",
            strict=True,
        ),
    ] = 30,
    seed: Annotated[
        int, Field(description="random seed for the down-sampling, so the check reproduces", strict=True)
    ] = 0,
    min_ratio: Annotated[
        float,
        Field(
            description="skip a cell type whose arms already differ by less than this ratio in median library size — there is no imbalance to correct",
            strict=True,
        ),
    ] = 1.05,
    input: Annotated[
        str,
        Field(
            description="an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_depth_matched_de", locals())


@analysis_tool
def run_gsea_prerank(
    gene_sets: Annotated[list[str], Field(strict=True, json_schema_extra=_omit_default)] = None,
    groupby: Annotated[
        str,
        Field(
            description="which ranked lists to read, by the key in tables/rank_<groupby>_<group>.rnk: the column run_de grouped by, or the `de_key` run_pseudobulk_de returned; omit to choose automatically, a replicated pseudobulk test first",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    top_n_terms: Annotated[int, Field(strict=True, json_schema_extra=_omit_default)] = None,
    min_size: Annotated[int, Field(strict=True, json_schema_extra=_omit_default)] = None,
    max_size: Annotated[int, Field(strict=True, json_schema_extra=_omit_default)] = None,
    permutations: Annotated[int, Field(strict=True, json_schema_extra=_omit_default)] = None,
    fdr_max: Annotated[float, Field(strict=True, json_schema_extra=_omit_default)] = None,
    seed: Annotated[int, Field(strict=True, json_schema_extra=_omit_default)] = None,
    input: Annotated[
        str,
        Field(
            description="ranked gene list(s) to test INSTEAD of the rank files run_de wrote — a .rnk file (gene<TAB>score per line) or a file-name pattern such as `my_rank_*.rnk`, inside this run's work/ or artifacts/ directory. Each file is one group, named by its file stem, ranked by the score as given. Empty = run_de's rank files.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_gsea_prerank", locals())


@analysis_tool
def run_doublet_detection(
    expected_doublet_rate: Annotated[
        float,
        Field(
            description="prior doublet rate handed to the detector — roughly the ~0.8% per 1,000 cells that 10x Genomics quotes, so 0.06 suits a ~8,000-cell lane. Set it from YOUR loading, not from this default",
            strict=True,
        ),
    ] = 0.06,
    flag_rate_above: Annotated[
        float,
        Field(
            description="warn when the detected doublet fraction exceeds this. An ENGINEERING guard, not a literature threshold: it exists to catch a mis-set expected rate or an overloaded lane, and a run above it is a prompt to check the loading, not a finding",
            strict=True,
        ),
    ] = 0.2,
    filter: Annotated[
        bool,
        Field(
            description="remove the predicted doublets (default) or only annotate them",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    batch_key: Annotated[
        str,
        Field(
            description="obs column to simulate doublets within, per batch",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    threshold: Annotated[
        float,
        Field(
            description="explicit score cutoff; omit to let scrublet choose one",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    force: Annotated[
        bool,
        Field(
            description="score again even though obs already holds doublet calls (from run_cellqc or an earlier run); off by default",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    input: Annotated[
        str,
        Field(
            description="an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. Doublets are scored and filtered IN PLACE, in that file.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_doublet_detection", locals())


@analysis_tool
def run_integration(
    batch_key: Annotated[str, Field(strict=True, json_schema_extra=_omit_default)] = None,
    method: Annotated[
        Literal["auto", "harmony", "combat"],
        Field(
            description="Omitted or auto preserves the deployed ComBat default. Set harmony explicitly to use bundled Harmony. The result records requested/effective methods and method_used.",
            json_schema_extra=_omit_default,
        ),
    ] = None,
    n_pcs: Annotated[int, Field(strict=True, json_schema_extra=_omit_default)] = None,
    input: Annotated[
        str,
        Field(
            description="an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_integration", locals())


@analysis_tool
def run_pseudobulk_de(
    min_cells_per_sample: Annotated[
        int,
        Field(
            description="a sample contributing fewer cells than this to a cell type is dropped from that cell type's test",
            strict=True,
        ),
    ] = 10,
    min_samples_per_condition: Annotated[
        int,
        Field(
            description="refuse to test an arm backed by fewer samples than this — below it there is no replication and no valid p-value. 2 is the COMPUTABILITY floor, not a recommendation: the single-cell DE literature (Squair et al. 2021) asks for >=3 replicates per arm, and a 2-vs-2 result should be reported as underpowered",
            strict=True,
        ),
    ] = 2,
    min_count: Annotated[
        int,
        Field(
            description="a gene must reach this many summed counts in at least as many samples as the smaller arm, or it is not tested (edgeR filterByExpr's rule of thumb). Genes nobody detected cannot be tested — they only inflate the BH denominator",
            strict=True,
        ),
    ] = 10,
    padj: Annotated[
        float, Field(description="adjusted-p (Benjamini-Hochberg) cutoff used to count significant genes", strict=True)
    ] = 0.05,
    sample_key: Annotated[
        str,
        Field(
            description="obs column holding the sample / donor / library id",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    condition_key: Annotated[
        str,
        Field(
            description="obs column holding the experimental condition", strict=True, json_schema_extra=_omit_default
        ),
    ] = None,
    group_key: Annotated[
        str,
        Field(
            description="cell-type column to test within, one test per cell type",
            strict=True,
            json_schema_extra=_omit_default,
        ),
    ] = None,
    input: Annotated[
        str,
        Field(
            description="an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. It must carry the raw counts in layers['counts'], as run_scanpy_qc's checkpoint does.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_pseudobulk_de", locals())


@analysis_tool
def run_composition(
    group_key: Annotated[str, Field(strict=True, json_schema_extra=_omit_default)] = None,
    sample_key: Annotated[str, Field(strict=True, json_schema_extra=_omit_default)] = None,
    condition_key: Annotated[str, Field(strict=True, json_schema_extra=_omit_default)] = None,
    input: Annotated[
        str,
        Field(
            description="an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_composition", locals())


@analysis_tool
def run_marker_annotation(
    reference: Annotated[
        Literal["auto", "none", "retina"],
        Field(
            description="curated marker reference for the tissue; 'auto' (default) applies one when your panel's cell types match it. 'none' is for a tissue without one: a panel naming three or more of a reference's cell types uses the reference whatever is passed",
            json_schema_extra=_omit_default,
        ),
    ] = None,
    panel: Annotated[dict[str, Any], Field(strict=True, json_schema_extra=_omit_default)] = None,
    discriminators: Annotated[dict[str, Any], Field(strict=True, json_schema_extra=_omit_default)] = None,
    cluster_key: Annotated[str, Field(strict=True, json_schema_extra=_omit_default)] = None,
    min_discriminator_mean: Annotated[float, Field(strict=True, json_schema_extra=_omit_default)] = None,
    dominance_ratio: Annotated[float, Field(strict=True, json_schema_extra=_omit_default)] = None,
    input: Annotated[
        str,
        Field(
            description="an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint.",
            strict=True,
        ),
    ] = "",
    analysis_id: str = "default",
    dataset: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    return _call("run_marker_annotation", locals())


@tool(server)
def scrna_job_status(analysis_id: str, task_id: str) -> dict[str, Any]:
    """Poll an analysis task and return its scientific result once completed; FAILED includes its error. Never start a dependent step before COMPLETED."""
    with quiet():
        return status(analysis_id, task_id)


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
