"""Regressions moved from optional app science tests before retiring the old tools.

The Atlas consumer test intentionally needs a reviewed installed skill script; it never
fetches a script or executes a network-supplied path. The numerical handoff test is always
part of the scientific package suite.
"""

import os
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse


def test_native_de_reads_a_handed_on_checkpoint_and_qc_refuses_renormalization(tmp_path):
    """Platform export/adopt is tested in the app; this exercises its native science consumer."""
    import hashlib
    from types import SimpleNamespace

    from aiscientist_mcp.scrna_analysis.run_de.tool import run_de
    from aiscientist_mcp.scrna_analysis.run_scanpy_qc.tool import run_scanpy_qc

    rng = np.random.default_rng(0)
    groups = np.arange(300) % 3
    means = np.ones((300, 60))
    for group in range(3):
        means[groups == group, group * 5 : (group + 1) * 5] = 12
    counts = rng.poisson(means).astype(np.float32)
    normalized = np.log1p(counts / counts.sum(1, keepdims=True) * 1e4)
    data = ad.AnnData(
        sparse.csr_matrix(normalized),
        obs=pd.DataFrame({"leiden": pd.Categorical(groups.astype(str))}, index=[f"cell{i}" for i in range(300)]),
        var=pd.DataFrame(index=[f"Gene{i}" for i in range(60)]),
    )
    data.layers["counts"] = sparse.csr_matrix(counts)
    data.uns["aiscientist"] = {
        "processed": True,
        "run_id": "run1",
        "checkpoints": ["adata_qc.h5ad", "adata_clustered.h5ad"],
    }
    # The delivered file omits redundant .raw; the platform recreates it on adoption.
    delivered = tmp_path / "Sample1_WT.processed.20261012-1000.run1.h5ad"
    data.write_h5ad(delivered)
    data.raw = data.copy()
    work = tmp_path / "run2" / "work"
    work.mkdir(parents=True)
    for name in ("adata_qc.h5ad", "adata_clustered.h5ad"):
        data.write_h5ad(work / name)
    original = hashlib.sha256(delivered.read_bytes()).hexdigest()
    checkpoint = hashlib.sha256((work / "adata_qc.h5ad").read_bytes()).hexdigest()
    ctx = SimpleNamespace(workspace=work.parent, decisions={"dataset_path": str(delivered)})
    result = run_de({"groupby": "leiden"}, ctx)
    assert result["status"] == "ok" and (work / "adata_de.h5ad").is_file()
    assert result["read_from"] == "work/adata_clustered.h5ad"
    refusal = run_scanpy_qc({}, ctx)
    assert refusal["status"] == "error" and "already quality-controlled" in refusal["error"]
    assert "normalise twice" in refusal["error"]
    assert hashlib.sha256(delivered.read_bytes()).hexdigest() == original
    assert hashlib.sha256((work / "adata_qc.h5ad").read_bytes()).hexdigest() == checkpoint


def test_the_atlas_skill_reads_the_tables_the_real_run_de_writes(tmp_path, monkeypatch):
    """The labels the atlas skill trusts (a marker table is refused, a stratified contrast's
    exploratory p-values are not inferential) are the ones run_de actually writes. The skill's own
    tests in the registry write those files by hand; this runs the platform's tool."""
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")
    pytest.importorskip("matplotlib")
    pytest.importorskip("scanpy")
    import runpy
    from types import SimpleNamespace

    import anndata as ad
    from aiscientist_mcp.scrna_analysis.run_de import tool as run_de_tool
    from aiscientist_mcp.scrna_analysis.run_scanpy_qc import tool as run_scanpy_qc_tool

    source = os.environ.get("AISCI_ATLAS_SKILL_SCRIPT", "")
    if not source:
        pytest.skip("set AISCI_ATLAS_SKILL_SCRIPT to the reviewed installed Atlas skill reference.py")
    source = Path(source)
    assert source.is_absolute() and source.is_file()
    script = tmp_path / "reference.py"
    script.write_bytes(source.read_bytes())

    def run(**overrides) -> dict:
        module = runpy.run_path(str(script), run_name="skill_module")
        return module["main"](dict(module["CONFIG"], atlas_path="atlas/precalculated_atlas.csv", **overrides))

    rng = np.random.default_rng(0)
    n = 300
    x = rng.poisson(4.0, (n, 40)).astype(np.float32) + 1.0
    cond = np.array(["DDX41" if i % 2 else "WT" for i in range(n)])
    ctype = np.array([["Rod", "Cone", "MG"][i % 3] for i in range(n)])
    x[(cond == "DDX41") & (ctype == "Rod"), 0] *= 6  # Gene0 up in DDX41 rods
    adata = ad.AnnData(x)
    adata.var_names = [f"Gene{i}" for i in range(40)]
    adata.obs_names = [f"c{i}" for i in range(n)]
    adata.obs["sampleid"] = pd.Categorical(cond)
    adata.obs["majorclass"] = pd.Categorical(ctype)
    adata.obs["orig.ident"] = pd.Categorical(["s1"] * n)
    adata.write(tmp_path / "retina.h5ad")
    ctx = SimpleNamespace(workspace=tmp_path, decisions={"dataset_path": str(tmp_path / "retina.h5ad")})
    assert (
        run_scanpy_qc_tool.run_scanpy_qc({"min_genes": 1, "min_cells": 1, "max_pct_mt": 100.0}, ctx)["status"] == "ok"
    )
    work, art = tmp_path / "work", tmp_path / "artifacts"
    (work / "atlas").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"cell_type": ct, "gene": f"Gene{i}", "mean_expression": 40.0 if (ct, i) == ("Rod", 0) else 4.0}
            for ct in ("Rod", "Cone", "MG")
            for i in range(40)
        ]
    ).to_csv(work / "atlas" / "precalculated_atlas.csv", index=False)
    monkeypatch.setenv("AISCIENTIST_WORK", str(work))
    monkeypatch.setenv("AISCIENTIST_ARTIFACTS", str(art))

    assert run_de_tool.run_de({"groupby": "majorclass"}, ctx)["status"] == "ok"
    with pytest.raises(SystemExit, match="MARKER table"):
        run(de_key="majorclass")

    out = run_de_tool.run_de({"groupby": "sampleid", "reference": "WT", "stratify_by": "majorclass"}, ctx)
    assert out["status"] == "ok" and out["inference"] == "exploratory_ranking"
    summary = run(de_key="majorclass", query_path=None)
    assert summary["p_values_inferential"] is False
    assert set(summary["de_source"]["rows_from"].values()) == {"per_group_full"}
    assert set(summary["de_source"]["rows_from"]) == {"Rod", "Cone", "MG"}
    candidates = pd.read_csv(art / "tables" / "atlas" / "candidates.csv")
    top = candidates[candidates.selected].iloc[0]
    assert (top["gene"], top["group"]) == ("GENE0", "Rod")


def test_scientific_default_resolver_and_schema_remain_one_contract():
    from aiscientist_mcp.scrna_analysis._lib.scrna import PARAMS, _p
    from aiscientist_mcp.scrna_analysis.runtime import SPECS

    for name, spec in SPECS.items():
        for parameter, (default, meaning) in PARAMS.get(name, {}).items():
            actual = spec["inputSchema"]["properties"][parameter]
            assert actual["default"] == default, (name, parameter)
            assert actual["description"] == meaning, (name, parameter)
            assert _p(name, parameter, {}) == default
    assert _p("run_de", "n_genes", {}) == 50
    assert _p("run_de", "n_genes", {"n_genes": 200}) == 200
    assert _p("run_scanpy_qc", "max_pct_mt", {}) == 10.0
    with pytest.raises(KeyError):
        _p("run_de", "not_a_declared_parameter", {})
    assert _p("run_pseudobulk_de", "min_samples_per_condition", {}) == 2
    assert _p("run_enrichment", "top_n_genes", {}) == 0
    assert _p("run_enrichment", "padj", {}) == 0.05
    assert _p("run_enrichment", "lfc", {}) == _p("run_de", "lfc", {}) == 0.25
