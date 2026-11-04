from pathlib import Path

import numpy as np
import pandas as pd
from aiscientist_mcp.atlas_expression_exploration import core


def test_resolve(atlas):
    tmp, a = atlas
    res = core.resolve(["LRAT", "cralbp", "rho", "ABCR", "NOTAGENE"], a.var_names, core.load_hgnc(tmp / "hgnc.tsv"))
    r = res.set_index("query")
    assert r.loc["cralbp", ["atlas_gene", "method"]].tolist() == ["RLBP1", "hgnc_alias"]
    assert r.loc["rho", ["atlas_gene", "method"]].tolist() == ["RHO", "case_insensitive"]
    assert r.loc["ABCR", ["atlas_gene", "method"]].tolist() == ["ABCA4", "hgnc_previous"]
    assert r.loc["NOTAGENE", "atlas_gene"] == ""


def test_extract_tables_figures(atlas):
    tmp, a = atlas
    q = tmp / "q"
    core.extract(tmp / "atlas.h5ad", ["LRAT", "RLBP1", "RHO"], q, "majorclass,celltype,tissue,donor,sampleid",
                 "Rod,MG,RPE", 1e4, 5)  # fmt: skip
    maj = pd.read_csv(q / "summary_majorclass.tsv", sep="\t")
    top = maj.sort_values("rank_pseudobulk").groupby("gene").head(1).set_index("gene")["majorclass"]
    assert top.to_dict() == {"LRAT": "RPE", "RLBP1": "MG", "RHO": "Rod"}
    x, tot = a.X[:, 0].toarray().ravel(), np.asarray(a.X.sum(1)).ravel()
    rpe = (a.obs.majorclass == "RPE").to_numpy()
    r = maj[(maj.gene == "LRAT") & (maj.majorclass == "RPE")].iloc[0]
    assert r.n_cells == rpe.sum() and np.isclose(r.pct_expressing, 100 * (x[rpe] > 0).mean())
    assert np.isclose(r.pseudobulk_cp10k, x[rpe].sum() / tot[rpe].sum() * 1e4, rtol=1e-5)
    assert np.isclose(r.mean_lognorm, np.log1p(x[rpe] / tot[rpe] * 1e4).mean(), rtol=1e-5)
    pd.DataFrame({"query": ["LRAT"], "atlas_gene": ["LRAT"], "method": ["exact"], "candidates": [""]}).to_csv(
        q / "gene_resolution.tsv", sep="\t", index=False
    )
    files = core.dotplot(q) + core.violin(q) + core.featureplot(q)
    assert len(files) == 8 and all(b"/Helvetica" in Path(f).read_bytes() for f in files if f.endswith(".pdf"))
    _, text = core.report(q)
    assert "Highest in RPE" in text and "dotplot_majorclass.png" in text
