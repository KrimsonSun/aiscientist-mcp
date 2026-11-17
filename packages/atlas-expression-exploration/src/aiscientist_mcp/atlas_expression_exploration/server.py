"""MCP server atlas-expression-exploration (stdio).

Flow: resolve_genes -> submit_query -> query_status (poll until COMPLETED) -> get_summary,
plot_dotplot, plot_violin, plot_featureplot -> write_report.
"""

import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Literal

from aiscientist_mcp.common import jobs
from aiscientist_mcp.common.paths import child_dir
from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool

from . import SERVER_NAME
from .settings import Settings

HGNC_URL = "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
LEVELS = ("majorclass", "celltype", "region_class")
GENE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,40}$")

server = make_server(
    SERVER_NAME,
    "Gene expression across the annotated cell types of a single-cell atlas. Flow: resolve_genes -> "
    "submit_query -> query_status (poll until COMPLETED; the full-atlas load takes ~10-15 min) -> "
    "get_summary, plot_dotplot, plot_violin, plot_featureplot -> write_report. Percentages and means are "
    "descriptive; report donor support (n_donors, frac_donors_detected) with any claim.",
)


def _core():
    from . import core  # scanpy and pandas load here, not at server start

    return core


def _genes(genes):
    names = [g.strip() for g in re.split(r"[,\s]+", genes) if g.strip()]
    bad = [g for g in names if not GENE_RE.match(g)]
    if bad or not names:
        raise ValueError(f"invalid gene names: {bad or genes!r}")
    return names


def _atlas(cfg, atlas):
    a = atlas or cfg.atlas
    if not a or not a.endswith(".h5ad") or not Path(a).is_file():
        raise ValueError(f"atlas not found or not .h5ad: {a!r} (set ATLAS_H5AD or pass atlas)")
    return str(Path(a).resolve())


def _hgnc(cfg):
    p = cfg.hgnc
    if not p.exists() or p.stat().st_size == 0:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        urllib.request.urlretrieve(HGNC_URL, tmp)  # fixed https URL
        tmp.replace(p)
    return p


def _resolve(cfg, atlas, genes):
    core = _core()
    return core.resolve(_genes(genes), core.read_var_names(atlas), core.load_hgnc(_hgnc(cfg)))


def _qdir(cfg, query_id):
    return child_dir(cfg.data_dir, query_id, "query_id", must_exist="state.json")


def _completed(cfg, query_id):
    d = _qdir(cfg, query_id)
    if jobs.status(_job_cfg(), d).get("state") != "COMPLETED":
        raise ValueError(f"query {query_id} is not COMPLETED yet; poll query_status")
    return d


def _job_cfg():
    return jobs.JobSettings.from_env("--cpus-per-task=4 --mem=300G --time=04:00:00")


@tool(server)
def resolve_genes(genes: str, atlas: str = "") -> dict[str, Any]:
    """Resolve gene names (comma- or space-separated; any case; HGNC aliases, previous symbols, HGNC
    or Ensembl IDs) against the atlas genes. Reads only the gene list, so it takes seconds."""
    cfg = Settings.from_env()
    with quiet():
        res = _resolve(cfg, _atlas(cfg, atlas), genes)
    return {"resolution": res.to_dict(orient="records")}


@tool(server)
def submit_query(genes: str, atlas: str = "", rerun: bool = False) -> dict[str, Any]:
    """Start a query: resolve names, then stream the atlas counts in row chunks and write query.h5ad
    plus summary tables. With the Slurm or local executor it returns at once with a query_id (poll
    query_status); with the inline executor it returns when the step ends. Reuses a finished or
    running query unless rerun=True."""
    cfg, jc = Settings.from_env(), _job_cfg()
    with quiet():
        atlas = _atlas(cfg, atlas)
        res = _resolve(cfg, atlas, genes)
        hits = list(dict.fromkeys(g for g in res["atlas_gene"] if g))
        if not hits:
            return {"state": "UNRESOLVED", "resolution": res.to_dict(orient="records")}
        qid = f"{Path(atlas).stem}__{'_'.join(hits)}"[:120]
        qdir = child_dir(cfg.data_dir, qid, "query_id")
        qdir.mkdir(parents=True, exist_ok=True)
        res.to_csv(qdir / "gene_resolution.tsv", sep="\t", index=False)
        state = jobs.status(jc, qdir) if (qdir / "state.json").exists() else {}
        if rerun or state.get("state") not in ("COMPLETED", "PENDING", "RUNNING"):
            jobs.write_state(qdir, {"query_id": qid, "atlas": atlas, "genes": hits})
            cmd = [sys.executable, "-m", "aiscientist_mcp.atlas_expression_exploration.extract", "--h5ad", atlas,
                   "--genes", ",".join(hits), "--outdir", str(qdir), "--obs-cols", cfg.obs_cols,
                   "--class-order", cfg.class_order, "--target-sum", str(cfg.target_sum),
                   "--min-cells-donor", str(cfg.min_cells_donor)]  # fmt: skip
            jobs.submit(jc, cmd, qdir, f"{SERVER_NAME}_{qid}")
        state = jobs.status(jc, qdir)
    return {"query_id": qid, "state": state.get("state"), "job_id": state.get("job_id", ""),
            "resolution": res.to_dict(orient="records")}  # fmt: skip


@tool(server)
def query_status(query_id: str) -> dict[str, Any]:
    """State of a query's full-atlas step (PENDING, RUNNING, COMPLETED, FAILED, ...). A preempted
    Slurm job is resubmitted automatically with AISCI_SLURM_FALLBACK."""
    cfg = Settings.from_env()
    with quiet():
        st = jobs.status(_job_cfg(), _qdir(cfg, query_id))
    keep = ("query_id", "state", "job_id", "slurm_opts", "note", "error", "elapsed_s")
    return {k: st[k] for k in keep if k in st}


@tool(server)
def get_summary(
    query_id: str,
    level: Literal["majorclass", "celltype", "region_class"] = "majorclass",
    gene: str = "",
    top: int = 10,
) -> dict[str, Any]:
    """Summary rows ranked by pseudobulk CP10k. level: majorclass, celltype or region_class. Fields:
    n_cells, n_expressing, pct_expressing, mean_lognorm, pseudobulk_cp10k, n_donors, donor_median_cp10k,
    donor_q25_cp10k, donor_q75_cp10k, frac_donors_detected, rank_pseudobulk."""
    cfg = Settings.from_env()
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    d = _completed(cfg, query_id)
    import pandas as pd

    t = pd.read_csv(d / f"summary_{level}.tsv", sep="\t")
    if gene:
        t = t[t.gene.str.upper() == gene.upper()]
    t = t.sort_values(["gene", "rank_pseudobulk"]).groupby("gene", sort=False).head(max(1, int(top)))
    return {"table": str(d / f"summary_{level}.tsv"), "rows": t.round(6).to_dict(orient="records")}


@tool(server)
def plot_dotplot(query_id: str) -> dict[str, Any]:
    """scanpy dot plots by major class and by cell type (PDF with editable text, plus PNG)."""
    with quiet():
        return {"files": _core().dotplot(_completed(Settings.from_env(), query_id))}


@tool(server)
def plot_violin(query_id: str, groupby: str = "") -> dict[str, Any]:
    """scanpy violin plot of per-cell log1p(CP10k), all cells; groupby defaults to the class column."""
    with quiet():
        return {"files": _core().violin(_completed(Settings.from_env(), query_id), groupby or None)}


@tool(server)
def plot_featureplot(query_id: str) -> dict[str, Any]:
    """scanpy UMAP feature plot; zero cells gray, expressing cells on top, color capped at q99 of
    expressing cells per gene."""
    with quiet():
        return {"files": _core().featureplot(_completed(Settings.from_env(), query_id))}


@tool(server)
def write_report(query_id: str) -> dict[str, Any]:
    """Short Markdown report: resolution, top groups with donor support, figures present, caveats."""
    with quiet():
        path, text = _core().report(_completed(Settings.from_env(), query_id))
    return {"file": path, "markdown": text}


@tool(server)
def list_queries() -> dict[str, Any]:
    """Queries in this server's data directory, with their state."""
    cfg = Settings.from_env()
    out = []
    for p in sorted(cfg.data_dir.glob("*/state.json")):
        st = jobs.read_state(p.parent)
        out.append({"query_id": p.parent.name, "state": st.get("state"), "genes": st.get("genes")})
    return {"data_dir": str(cfg.data_dir), "queries": out}


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
