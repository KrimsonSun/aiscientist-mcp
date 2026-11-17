"""Real numerical regression against the pre-migration application checkout.

Set AISCI_LEGACY_SCRNA_ROOT to its src/ directory for the cross-repository test.
The normal suite also executes QC through the actual MCP client and inline worker.
"""

import asyncio
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def counts(tmp_path, monkeypatch):
    import anndata as ad
    from scipy import sparse

    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "LOKY_MAX_CPU_COUNT",
    ):
        monkeypatch.setenv(key, "2")
    rng = np.random.default_rng(94)
    n = 240
    celltype = np.tile(np.repeat(["TypeA", "TypeB"], 20), 6)
    condition = np.repeat(["ctrl", "case"], n // 2)
    donors = np.repeat([f"D{i}" for i in range(6)], n // 6)
    genes = ["MT-A", "A1", "A2", "A3", "B1", "B2", "B3"] + [f"G{i}" for i in range(153)]
    x = rng.poisson(2, (n, len(genes))).astype(np.float32)
    x[:, 0] = rng.binomial(1, 0.1, n)
    x[celltype == "TypeA", 1:4] += rng.poisson(15, ((celltype == "TypeA").sum(), 3))
    x[celltype == "TypeB", 4:7] += rng.poisson(15, ((celltype == "TypeB").sum(), 3))
    x[condition == "case", 10:20] += rng.poisson(3, ((condition == "case").sum(), 10))
    obs = pd.DataFrame(
        {"celltype": celltype, "condition": condition, "donor": donors}, index=[f"cell{i}" for i in range(n)]
    )
    a = ad.AnnData(sparse.csr_matrix(x), obs=obs, var=pd.DataFrame(index=genes))
    path = tmp_path / "counts.h5ad"
    a.write_h5ad(path)
    genesets = tmp_path / "genesets"
    genesets.mkdir()
    (genesets / "SyntheticPathways.gmt").write_text(
        "type_a\tsynthetic\tA1\tA2\tA3\tG1\tG2\n"
        "type_b\tsynthetic\tB1\tB2\tB3\tG3\tG4\n"
        "condition\tsynthetic\tG3\tG4\tG5\tG6\tG7\tG8\tG9\tG10\n"
    )
    monkeypatch.setenv("AISCIENTIST_GENESETS_DIR", str(genesets))
    monkeypatch.setenv("SCRNA_GENESETS_DIR", str(genesets))
    monkeypatch.setenv("SCRNA_DATASET", str(path))
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    return path


QC = {"min_genes": 1, "min_cells": 1, "max_pct_mt": 100.0, "n_top_genes": 80}
STEPS = [
    ("run_scanpy_qc", QC),
    ("run_doublet_detection", {"filter": False, "threshold": 0.5}),
    ("run_integration", {"method": "harmony", "batch_key": "donor", "n_pcs": 8}),
    ("run_clustering", {"n_pcs": 8, "n_neighbors": 8, "resolution": 0.4}),
    ("run_de", {"groupby": "celltype", "n_genes": 20, "min_cells": 10}),
    ("run_enrichment", {"gene_sets": ["SyntheticPathways"], "groupby": "celltype"}),
    (
        "run_depth_matched_de",
        {"groupby": "condition", "reference": "ctrl", "stratify_by": "celltype", "n_genes": 10, "min_cells": 10},
    ),
    (
        "run_gsea_prerank",
        {"gene_sets": ["SyntheticPathways"], "groupby": "celltype", "permutations": 10, "min_size": 3, "max_size": 50},
    ),
    (
        "run_pseudobulk_de",
        {
            "sample_key": "donor",
            "condition_key": "condition",
            "group_key": "celltype",
            "min_cells_per_sample": 5,
            "min_count": 1,
        },
    ),
    ("run_composition", {"group_key": "celltype", "sample_key": "donor", "condition_key": "condition"}),
    (
        "run_marker_annotation",
        {
            "reference": "none",
            "cluster_key": "celltype",
            "panel": {"TypeA": ["A1", "A2", "A3"], "TypeB": ["B1", "B2", "B3"]},
        },
    ),
]


def _portable(value, workspace):
    if isinstance(value, dict):
        return {key: _portable(item, workspace) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable(item, workspace) for item in value]
    return value.replace(str(workspace), "<workspace>") if isinstance(value, str) else value


def test_qc_through_real_mcp_and_inline_worker(counts):
    import anndata as ad
    from aiscientist_mcp.scrna_analysis import runtime
    from aiscientist_mcp.scrna_analysis.server import server
    from mcp import Client

    async def check():
        async with Client(server, read_timeout_seconds=120) as client:
            reply = await client.call_tool("run_scanpy_qc", QC | {"analysis_id": "real-qc"})
            assert not reply.is_error, reply
            result = reply.structured_content
            assert result["state"] == "COMPLETED" and result["status"] == "ok"
            assert result["cells_before"] == result["cells_after"] == 240
            assert Path(result["artifact_root"]).is_dir()
            assert Path(result["checkpoint_path"]).is_file()
            stored = await client.call_tool(
                "scrna_job_status", {"analysis_id": "real-qc", "task_id": result["task_id"]}
            )
            assert stored.structured_content == result
            repeated = await client.call_tool("run_scanpy_qc", QC | {"analysis_id": "real-qc"})
            assert repeated.structured_content == result
            path = runtime.workspace("real-qc") / "work" / "adata_qc.h5ad"
            checkpoint = ad.read_h5ad(path)
            assert "counts" in checkpoint.layers and checkpoint.raw is not None
            assert checkpoint.n_obs == 240
            assert all((runtime.workspace("real-qc") / "artifacts" / p).is_file() for p in result["figures"])

    asyncio.run(check())


def test_combat_accepts_a_readonly_corrected_matrix(counts, tmp_path, monkeypatch):
    import scanpy as sc
    from aiscientist_mcp.scrna_analysis.run_integration.tool import run_integration
    from aiscientist_mcp.scrna_analysis.run_scanpy_qc.tool import run_scanpy_qc

    ctx = SimpleNamespace(workspace=tmp_path / "combat", decisions={"dataset_path": str(counts)})
    assert run_scanpy_qc(QC, ctx)["status"] == "ok"
    original = sc.pp.combat

    def readonly(adata, **kwargs):
        original(adata, **kwargs)
        adata.X.flags.writeable = False

    monkeypatch.setattr(sc.pp, "combat", readonly)
    result = run_integration({"method": "combat", "batch_key": "donor", "n_pcs": 8}, ctx)
    assert result["status"] == "ok" and result["method_used"] == "combat"
    assert (ctx.workspace / "work" / "adata_integrated.h5ad").is_file()


def test_all_algorithms_match_legacy_source(counts, tmp_path, monkeypatch):
    legacy_src = os.environ.get("AISCI_LEGACY_SCRNA_ROOT")
    if not legacy_src:
        pytest.skip("set AISCI_LEGACY_SCRNA_ROOT to the original application's src/ to run cross-repository parity")
    monkeypatch.syspath_prepend(legacy_src)
    contexts = {
        name: SimpleNamespace(workspace=tmp_path / name, decisions={"dataset_path": str(counts)})
        for name in ("legacy", "mcp")
    }
    results = {}
    for name, args in STEPS:
        for label, package in (("legacy", "aiscientist.tools"), ("mcp", "aiscientist_mcp.scrna_analysis")):
            module = importlib.import_module(f"{package}.{name}.tool")
            result = getattr(module, name)(args, contexts[label])
            assert result["status"] == "ok", (label, name, result)
            results[label] = _portable(result, contexts[label].workspace)
        assert results["legacy"] == results["mcp"], (name, results)
    # Numeric data and table contents, not container-dependent PNG/PDF metadata.
    for path in sorted((tmp_path / "legacy/artifacts/tables").glob("*.csv")):
        other = tmp_path / "mcp/artifacts/tables" / path.name
        pd.testing.assert_frame_equal(pd.read_csv(path), pd.read_csv(other))
    (tmp_path / "parity-results.json").write_text(
        json.dumps({"tools": [name for name, _ in STEPS], "status": "matched"}, indent=2)
    )
