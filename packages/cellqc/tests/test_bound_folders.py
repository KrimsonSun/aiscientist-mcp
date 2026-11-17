"""CellQC still consumes every bound sample folder after application-tool retirement.

Uses the existing fake executable fixture to test core routing/merge, not R biology.
"""

from pathlib import Path

import pytest

_LIBRARY = ["raw_feature_bc_matrix.h5", "filtered_feature_bc_matrix.h5"]


def _web_summary(chem, ref):
    return f'<script>var data = [["Chemistry","{chem}"],["Transcriptome","{ref}"]];</script>'


def _library(folder: Path) -> Path:
    """A Cell Ranger library AS the folder, the way a 10x Cloud download is uploaded."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "raw_feature_bc_matrix.h5").write_bytes(b"")
    (folder / "filtered_feature_bc_matrix.h5").write_bytes(b"")
    (folder / "metrics_summary.csv").write_text("Estimated Number of Cells\n4\n")
    (folder / "web_summary.html").write_text(_web_summary("Single Cell 3' v3 (polyA)", "GRCm39-2024-A"))
    return folder


def test_cellqc_reads_every_bound_folder_in_one_run(tmp_path, fake_cellqc):
    pytest.importorskip("scanpy")
    from types import SimpleNamespace as HarnessContext

    from aiscientist_mcp.cellqc.core import run_cellqc

    a = _library(tmp_path / "data" / "aaa" / "original")
    b = _library(tmp_path / "data" / "bbb" / "original")
    notes = tmp_path / "data" / "ccc" / "original"
    notes.mkdir(parents=True)
    (notes / "notes.txt").write_text("x")
    ws = tmp_path / "run"
    ctx = HarnessContext(
        workspace=ws,
        decisions={
            "dataset_root": str(a),
            "input_folders": [
                {"name": "Sample3_GSM5676874", "path": str(a)},
                {"name": "Sample1_WT", "path": str(b)},
                {"name": "notes", "path": str(notes)},
                {"name": "gone", "path": str(tmp_path / "missing")},
            ],
        },
    )
    out = run_cellqc({}, ctx)
    assert out["status"] == "ok", out
    lines = (ws / "work" / "cellqc" / "samples.txt").read_text().splitlines()[1:]
    assert [ln.split("\t")[0] for ln in lines] == ["Sample3_GSM5676874", "Sample1_WT"]
    assert out["n_libraries"] == 2
    assert {lib["sample"] for lib in out["libraries"]} == {"Sample3_GSM5676874", "Sample1_WT"}
    text = "\n".join(out["warnings"])
    assert "notes/ holds no Cell Ranger library" in text and "gone/ could not be read" in text
    assert out["read_from"] == "the bound folders (Sample3_GSM5676874/, Sample1_WT/)"
    import anndata as ad

    adata = ad.read_h5ad(ws / "work" / "adata_qc.h5ad")
    assert set(adata.obs["sampleid"]) == {"Sample3_GSM5676874", "Sample1_WT"}
