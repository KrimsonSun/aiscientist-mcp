"""Gene-expression query functions (scanpy). Each is also exposed as an MCP tool by server.py.

extract() streams the full atlas and writes <outdir>/query.h5ad (all cells x query genes) plus
summary tables; the plot and report functions read only that file.
"""

import difflib
import logging
import re
from pathlib import Path

import anndata as ad
import h5py
import matplotlib as mpl
import numpy as np
import pandas as pd
import scanpy as sc
import scipy.sparse as sp

LEVELS = ("majorclass", "celltype", "region_class")
CHUNK_ROWS = 2048
CHUNK_NNZ = 1_000_000
log = logging.getLogger(__name__)


def read_var_names(h5ad):
    """Gene names from `var` only, without loading the matrix."""
    with h5py.File(h5ad, "r") as f:
        return ad.io.read_elem(f["var"]).index.astype(str)


def load_hgnc(path):
    return pd.read_csv(path, sep="\t", dtype=str, low_memory=False).fillna("")


def resolve(names, atlas, hgnc):
    """Map each query name to one atlas gene: exact, case-insensitive, then HGNC symbol/previous/alias/ID."""
    upper = {}
    for g in atlas:
        upper.setdefault(g.upper(), []).append(g)
    rows = []
    for q in names:
        hit, how, cand = "", "unresolved", ""
        if q in upper.get(q.upper(), []):
            hit, how = q, "exact"
        elif len(upper.get(q.upper(), [])) == 1:
            hit, how = upper[q.upper()][0], "case_insensitive"
        else:
            for method, col in [("hgnc_symbol", "symbol"), ("hgnc_previous", "prev_symbol"),
                                ("hgnc_alias", "alias_symbol"), ("hgnc_id", "hgnc_id"),
                                ("ensembl_id", "ensembl_gene_id")]:  # fmt: skip
                m = hgnc[col].str.upper().str.split("|").apply(lambda v, q=q: q.upper() in v)
                if not m.any():
                    continue
                found = set()
                for _, r in hgnc[m].iterrows():  # approved symbol, else a previous symbol the atlas uses
                    syms = [r.symbol] if r.symbol.upper() in upper else r.prev_symbol.split("|")
                    found |= {upper[s.upper()][0] for s in syms if s and s.upper() in upper}
                if len(found) == 1:
                    hit, how = found.pop(), method
                elif found:
                    how, cand = "ambiguous", ",".join(sorted(found))
                else:
                    how, cand = "not_in_atlas", ",".join(sorted(hgnc.loc[m, "symbol"]))
                break
            else:
                close = difflib.get_close_matches(q.upper(), list(upper), n=5, cutoff=0.75)
                cand = ",".join(upper[c][0] for c in close)
        rows.append({"query": q, "atlas_gene": hit, "method": how, "candidates": cand})
    return pd.DataFrame(rows)


def natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def summarize(q, keys, donor, min_cells):
    """Per group and gene: cell-level stats (scanpy aggregate) and donor-level pseudobulk CP10k."""
    obs = q.obs
    agg = sc.get.aggregate(q, by=keys, func=["mean", "count_nonzero"])
    pb = sc.get.aggregate(q, by=keys, func="sum", layer="counts")
    idx = pd.MultiIndex.from_frame(agg.obs[keys].astype(str))
    n = obs.groupby(keys, observed=True).size()
    n.index = pd.MultiIndex.from_frame(n.index.to_frame().astype(str))
    tot = obs.groupby(keys, observed=True)["total_counts"].sum()
    tot.index = n.index
    don = sc.get.aggregate(q, by=[*keys, donor], func="sum", layer="counts")
    dobs = don.obs[[*keys, donor]].astype(str)
    dn = obs.groupby([*keys, donor], observed=True).agg(n=("total_counts", "size"), t=("total_counts", "sum"))
    dn.index = pd.MultiIndex.from_frame(dn.index.to_frame().astype(str))
    dmi = pd.MultiIndex.from_frame(dobs)
    out = []
    for j, g in enumerate(q.var_names):
        t = pd.DataFrame(index=idx)
        t["gene"] = g
        t["n_cells"] = n.reindex(idx).to_numpy()
        t["n_expressing"] = agg.layers["count_nonzero"][:, j]
        t["pct_expressing"] = 100 * t["n_expressing"] / t["n_cells"]
        t["mean_lognorm"] = agg.layers["mean"][:, j]
        t["pseudobulk_cp10k"] = np.asarray(pb.layers["sum"][:, j]).ravel() / tot.reindex(idx).to_numpy() * 1e4
        d = pd.DataFrame({"c": np.asarray(don.layers["sum"][:, j]).ravel()}, index=dmi).join(dn)
        d = d[d["n"] >= min_cells]
        d["cp10k"] = d["c"] / d["t"] * 1e4
        dg = d.groupby(level=list(range(len(keys))))
        t["n_donors"] = dg.size().reindex(idx).fillna(0).astype(int).to_numpy()
        t["donor_median_cp10k"] = dg["cp10k"].median().reindex(idx).to_numpy()
        t["donor_q25_cp10k"] = dg["cp10k"].quantile(0.25).reindex(idx).to_numpy()
        t["donor_q75_cp10k"] = dg["cp10k"].quantile(0.75).reindex(idx).to_numpy()
        t["frac_donors_detected"] = dg["c"].apply(lambda c: (c > 0).mean()).reindex(idx).to_numpy()
        t["rank_pseudobulk"] = t["pseudobulk_cp10k"].rank(ascending=False, method="min").astype(int)
        out.append(t.reset_index())
    return pd.concat(out, ignore_index=True)


def _csc_query_counts(encoded, indices, n_obs):
    """Scan CSC nonzeros once; row slicing a backed CSC first loads every column.

    Library totals need every gene, but only query-column overlaps are retained.
    Copies of overlaps keep small queries from retaining the complete read blocks.
    Memory is bounded by one NNZ block, row totals and the requested output matrix.
    """
    indptr = np.asarray(encoded["indptr"])
    intervals = [(int(indptr[index]), int(indptr[index + 1])) for index in indices]
    values, rows = [[] for _ in indices], [[] for _ in indices]
    totals = np.zeros(n_obs, dtype=np.float64)
    for start in range(0, len(encoded["data"]), CHUNK_NNZ):
        stop = min(start + CHUNK_NNZ, len(encoded["data"]))
        data = encoded["data"][start:stop]
        row = encoded["indices"][start:stop]
        # add.at handles repeated row indices across columns (and duplicate
        # entries within a sparse column) without dropping any contribution.
        np.add.at(totals, row, data)
        for pos, (left, right) in enumerate(intervals):
            lo, hi = max(left, start) - start, min(right, stop) - start
            if lo < hi:
                values[pos].append(data[lo:hi].copy())
                rows[pos].append(row[lo:hi].copy())
    pointers = np.cumsum([0, *(sum(len(part) for part in column) for column in values)], dtype=np.int64)
    values = [part for column in values for part in column]
    rows = [part for column in rows for part in column]
    selected = sp.csc_matrix(
        (
            np.concatenate(values) if values else np.empty(0, dtype=encoded["data"].dtype),
            np.concatenate(rows) if rows else np.empty(0, dtype=encoded["indices"].dtype),
            pointers,
        ),
        shape=(n_obs, len(indices)),
    )
    return selected.tocsr(), totals


def extract(h5ad, genes, outdir, obs_cols, class_order, target_sum=1e4, min_cells_donor=10):
    """Keep query genes while summing full-library counts in bounded matrix chunks."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    cols = obs_cols.split(",")
    cls, typ, reg, donor = cols[:4]
    # Reading only named HDF5 elements also excludes unrelated raw/layers, which
    # an AnnData backed reader may still materialize before the matrix is sliced.
    with h5py.File(h5ad, "r") as handle:
        obs = ad.io.read_elem(handle["obs"])
        var = ad.io.read_elem(handle["var"])
        missing = sorted(set(cols) - set(obs.columns))
        if missing:
            raise ValueError(f"atlas is missing observation columns {missing}; configure ATLAS_OBS_COLS")
        if "obsm/X_umap" not in handle:
            raise ValueError("atlas must provide obsm['X_umap']")
        indices = var.index.get_indexer(genes)
        if (indices < 0).any():
            raise ValueError("query genes must be resolved against the atlas first")
        matrix = ad.io.sparse_dataset(handle["X"]) if isinstance(handle["X"], h5py.Group) else handle["X"]
        if getattr(matrix, "format", None) == "csc":
            selected, totals = _csc_query_counts(handle["X"], indices, len(obs))
        else:
            totals = np.empty(len(obs), dtype=np.float64)
            chunks = []
            for start in range(0, len(obs), CHUNK_ROWS):
                stop = min(start + CHUNK_ROWS, len(obs))
                chunk = matrix[start:stop, :]
                totals[start:stop] = np.ravel(chunk.sum(axis=1, dtype=np.float64))
                chunks.append(sp.csr_matrix(chunk[:, indices]))
            selected = sp.vstack(chunks, format="csr")
        obs = obs[cols].copy()
        obs["total_counts"] = totals
        q = ad.AnnData(
            selected,
            obs=obs,
            var=var.iloc[indices].copy(),
            obsm={"X_umap": ad.io.read_elem(handle["obsm/X_umap"])},
        )
    log.info("extracted %s without loading the full atlas matrix", q)
    q.layers["counts"] = q.X.copy()
    # Same as sc.pp.normalize_total(target_sum) on the full matrix: divide by full-library totals.
    scale = np.divide(target_sum, totals, out=np.zeros_like(totals), where=totals != 0)
    q.X = sp.csr_matrix(sp.diags(scale) @ q.X, dtype=np.float32)
    sc.pp.log1p(q)

    pref = [c for c in class_order.split(",") if c in set(q.obs[cls])]
    classes = pref + sorted(set(q.obs[cls].astype(str)) - set(pref), key=natural)
    major = q.obs.groupby(typ, observed=True)[cls].agg(lambda s: s.value_counts().index[0])
    types = sorted(major.index, key=lambda t: (classes.index(major[t]), natural(t)))
    q.obs[cls] = pd.Categorical(q.obs[cls].astype(str), classes, ordered=True)
    q.obs[typ] = pd.Categorical(q.obs[typ].astype(str), types, ordered=True)
    q.uns["query"] = {"genes": list(genes), "class_col": cls, "type_col": typ, "region_col": reg,
                      "donor_col": donor, "target_sum": target_sum,
                      "min_cells_donor": min_cells_donor, "source": str(h5ad)}  # fmt: skip
    q.write_h5ad(out / "query.h5ad")

    for name, keys in zip(LEVELS, ([cls], [typ], [reg, cls]), strict=True):
        tab = summarize(q, keys, donor, min_cells_donor)
        if name == "celltype":
            tab.insert(1, cls, tab[typ].map(major))
        tab.to_csv(out / f"summary_{name}.tsv", sep="\t", index=False, float_format="%.6g")


def style():
    """scanpy defaults; PDF text as editable Helvetica, scatter layers rasterized at 500 DPI."""
    mpl.use("Agg")
    sc.set_figure_params(dpi_save=500, vector_friendly=True, frameon=False, fontsize=8)
    mpl.rcParams.update({"pdf.use14corefonts": True, "font.family": "sans-serif",
                         "font.sans-serif": ["Helvetica", "Nimbus Sans", "DejaVu Sans"]})  # fmt: skip
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)


def _save(fig, out, stem):
    fig.savefig(out / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(out / f"{stem}.png", dpi=200, bbox_inches="tight")
    return [str(out / f"{stem}.pdf"), str(out / f"{stem}.png")]


def dotplot(qdir):
    """scanpy dot plots by class and by cell type. Returns the written files."""
    import matplotlib.pyplot as plt

    style()
    q, out = sc.read_h5ad(Path(qdir) / "query.h5ad"), Path(qdir)
    m, files = q.uns["query"], []
    for level in (m["class_col"], m["type_col"]):
        dp = sc.pl.dotplot(q, list(q.var_names), groupby=level, swap_axes=True, return_fig=True,
                           colorbar_title="Mean log1p(CP10k)\nin group", size_title="% cells\nexpressing")  # fmt: skip
        dp.savefig(out / f"dotplot_{level}.pdf")
        dp.savefig(out / f"dotplot_{level}.png", dpi=200)
        files += [str(out / f"dotplot_{level}.pdf"), str(out / f"dotplot_{level}.png")]
        plt.close("all")
    return files


def violin(qdir, groupby=None):
    """scanpy violin of per-cell log1p(CP10k) (all cells, zeros included). Returns the written files."""
    import matplotlib.pyplot as plt

    style()
    q, out = sc.read_h5ad(Path(qdir) / "query.h5ad"), Path(qdir)
    grp = groupby or q.uns["query"]["class_col"]
    sc.pl.violin(q, list(q.var_names), groupby=grp, stripplot=False, rotation=90,
                 ylabel=[f"{g}\nlog1p(CP10k)" for g in q.var_names], show=False)  # fmt: skip
    files = _save(plt.gcf(), out, f"violin_{grp}")
    plt.close("all")
    return files


def featureplot(qdir, vmax_quantile=0.99):
    """scanpy UMAP; zero cells light gray, expressing cells on top, color capped per gene at the
    `vmax_quantile` of expressing cells. Returns the written files."""
    import matplotlib.pyplot as plt

    style()
    q, out = sc.read_h5ad(Path(qdir) / "query.h5ad"), Path(qdir)
    genes = list(q.var_names)
    x = q[:, genes].X.toarray()
    vmax = [float(np.quantile(v[v > 0], vmax_quantile)) if (v > 0).any() else 1.0 for v in x.T]
    cmap = mpl.colors.LinearSegmentedColormap.from_list("gray_red", ["#d9d9d9", "#fcbba1", "#cb181d", "#67000d"])
    sc.pl.umap(q, color=genes, cmap=cmap, vmin=0, vmax=vmax, sort_order=True, ncols=3,
               colorbar_loc="right", show=False)  # fmt: skip
    files = _save(plt.gcf(), out, "featureplot")
    plt.close("all")
    return files


def md_table(df):
    rows = [list(map(str, r)) for r in df.itertuples(index=False)]
    return "\n".join(["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
                     + ["| " + " | ".join(r) + " |" for r in rows])  # fmt: skip


def report(qdir, min_donors=5):
    """Short Markdown report from the tables and any figures present. Returns (path, text)."""
    d = Path(qdir)
    res = pd.read_csv(d / "gene_resolution.tsv", sep="\t", keep_default_na=False)
    maj = pd.read_csv(d / "summary_majorclass.tsv", sep="\t")
    ct = pd.read_csv(d / "summary_celltype.tsv", sep="\t")
    cls, typ = maj.columns[0], ct.columns[0]
    lines = [f"# Expression of {', '.join(maj.gene.unique())}", "", "## Gene name resolution", "",
             md_table(res), ""]  # fmt: skip
    for g, t in maj.groupby("gene", sort=False):
        t = t.sort_values("rank_pseudobulk")
        top, nxt = t.iloc[0], t.iloc[1]
        c = ct[(ct.gene == g) & (ct.n_donors >= min_donors)].sort_values("rank_pseudobulk").head(5)
        lines += [
            f"## {g}", "",
            (f"Highest in {top[cls]}: {top.pseudobulk_cp10k:.3g} pseudobulk CP10k, {top.pct_expressing:.1f}% of "
             f"{top.n_cells:,} cells, detected in {top.frac_donors_detected:.0%} of {top.n_donors} donors; "
             f"{top.pseudobulk_cp10k / max(nxt.pseudobulk_cp10k, 1e-12):.1f}-fold above {nxt[cls]} "
             f"({nxt.pseudobulk_cp10k:.3g} CP10k)."),
            f"Top cell types with >= {min_donors} donors: "
            + ", ".join(f"{r[typ]} ({r.pseudobulk_cp10k:.3g}, {r.pct_expressing:.1f}%)" for _, r in c.iterrows()) + ".",
            "",
            md_table(t[[cls, "n_cells", "pct_expressing", "pseudobulk_cp10k", "n_donors", "donor_median_cp10k",
                        "frac_donors_detected"]].round(3)),
            "",
        ]  # fmt: skip
    figs = [p for s in ("dotplot_*", "violin_*", "featureplot") for p in sorted(d.glob(f"{s}.png"))]
    lines += ["## Figures", ""] + [f"![{p.stem}]({p.name})\n" for p in figs]
    lines += [
        "## Caveats", "",
        "- snRNA-seq measures nuclear transcripts; low-level detection across many classes can be ambient RNA.",
        ("- Percentages and means over cells are descriptive, not tests; compare groups with donor-aware "
         "pseudobulk DE."),
        "- Pooled multi-donor labels count as one donor; cell-type labels are the atlas annotation.",
    ]  # fmt: skip
    text = "\n".join(lines) + "\n"
    (d / "report.md").write_text(text)
    return str(d / "report.md"), text
