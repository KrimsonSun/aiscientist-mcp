"""Cross-domain regressions preserved before retiring the application scientific tools."""

from types import SimpleNamespace

import anndata as ad
import numpy as np
import pandas as pd
import pytest


def _ctx(workspace, dataset):
    return SimpleNamespace(workspace=workspace, decisions={"dataset_path": str(dataset)} if dataset else {})


def test_scrublet_does_not_remove_a_second_set_after_cellqc(tmp_path):
    ad = pytest.importorskip("anndata")
    import numpy as np
    import pandas as pd
    from aiscientist_mcp.scrna_analysis.run_doublet_detection.tool import run_doublet_detection

    work = tmp_path / "run" / "work"
    work.mkdir(parents=True)
    X = np.ones((5, 3))
    obs = pd.DataFrame({"doubletfinder_class": pd.Categorical(["Singlet"] * 5)}, index=[f"c{i}" for i in range(5)])
    a = ad.AnnData(X=X, obs=obs)
    a.layers["counts"] = X.copy()
    a.write_h5ad(work / "adata_qc.h5ad")
    out = run_doublet_detection({}, _ctx(tmp_path / "run", None))
    assert out["status"] == "ok" and out["skipped"] and out["cells_after"] == 5
    assert out["doublets_called_upstream"] == ["doubletfinder_class"]
    assert "Scrublet was NOT run" in out["note"]


def _two_arm_h5ad(tmp_path, *, depth_ratio=1.6):
    rng = np.random.default_rng(0)
    n = 600
    arm = np.array(["KO" if i % 2 else "WT" for i in range(n)])
    label = np.array([["Rod", "Cone", "MG"][i % 3] for i in range(n)])
    lam = np.where(arm == "KO", 8.0 * depth_ratio, 8.0)
    x = rng.poisson(lam[:, None], (n, 50)).astype(np.float32)
    a = ad.AnnData(x)
    a.var_names = [f"G{i}" for i in range(50)]
    a.obs_names = [f"c{i}" for i in range(n)]
    a.obs["sampleid"] = pd.Categorical(arm)
    a.obs["majorclass"] = pd.Categorical(label)
    a.obs["orig.ident"] = pd.Categorical(["0"] * n)
    a.obs["nCount_RNA"] = x.sum(1)
    a.obs["nFeature_RNA"] = (x > 0).sum(1)
    a.obs["percent.mt"] = rng.uniform(0.5, 3.0, n)
    p = tmp_path / "two_arm.h5ad"
    a.write(p)
    return p


def test_run_de_names_a_same_direction_skew_across_strata(tmp_path):
    """Every stratum up >> down is a technical signature; the tool says so where the numbers are."""
    from types import SimpleNamespace

    from aiscientist_mcp.scrna_analysis.run_de import tool as run_de_tool
    from aiscientist_mcp.scrna_analysis.run_scanpy_qc import tool as run_scanpy_qc_tool

    p = _two_arm_h5ad(tmp_path, depth_ratio=2.5)  # a big depth gap -> global 'up' in KO
    ctx = SimpleNamespace(workspace=tmp_path, decisions={"dataset_path": str(p)})
    assert (
        run_scanpy_qc_tool.run_scanpy_qc({"min_genes": 1, "min_cells": 1, "max_pct_mt": 100.0}, ctx)["status"] == "ok"
    )
    out = run_de_tool.run_de(
        {"groupby": "sampleid", "reference": "WT", "stratify_by": "majorclass", "min_cells": 10, "min_pct": 0}, ctx
    )
    assert out["status"] == "ok"
    # Whether the toy data trips the >=100-genes gate depends on the draw; the CONTRACT is that
    # when the skew is there it is named as technical, not biological.
    if out.get("direction_bias"):
        assert any("DIRECTION BIAS" in w and "technical" in w for w in out["warnings"])


def test_same_direction_bias_warning_is_emitted_for_measured_technical_shift(tmp_path):
    from aiscientist_mcp.scrna_analysis.run_de.tool import run_de

    rng = np.random.default_rng(17)
    labels = np.tile(["Rod", "Cone", "MG"], 80)
    conditions = np.repeat(["WT", "KO"], 120)
    means = np.where(conditions == "KO", 12.0, 2.0)
    counts = rng.poisson(means[:, None], (240, 160)).astype(np.float32)
    obs = pd.DataFrame(
        {"sampleid": pd.Categorical(conditions), "majorclass": pd.Categorical(labels)},
        index=[f"c{i}" for i in range(240)],
    )
    data = ad.AnnData(np.log1p(counts), obs=obs)
    data.var_names = [f"Gene{i}" for i in range(160)]
    data.layers["counts"] = counts
    data.uns["log1p"] = {"base": None}
    data.raw = data.copy()
    work = tmp_path / "work"
    work.mkdir()
    data.write_h5ad(work / "adata_qc.h5ad")
    result = run_de(
        {"groupby": "sampleid", "reference": "WT", "stratify_by": "majorclass", "min_cells": 10, "min_pct": 0},
        _ctx(tmp_path, None),
    )
    assert result["status"] == "ok"
    assert result["direction_bias"]["direction"] == "up"
    assert set(result["direction_bias"]["strata"]) == {"Rod", "Cone", "MG"}
    assert any("DIRECTION BIAS" in message and "technical" in message for message in result["warnings"])
