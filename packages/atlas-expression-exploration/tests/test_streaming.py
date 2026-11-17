"""Full-transcriptome denominators agree across on-disk matrix layouts and chunks."""

import anndata as ad
import h5py
import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp
from aiscientist_mcp.atlas_expression_exploration import core


@pytest.mark.parametrize("layout", ["dense", "csr", "csc"])
def test_chunked_query_preserves_counts_normalization_and_summary(atlas, tmp_path, monkeypatch, layout):
    _, source = atlas
    source = source.copy()
    counts = source.X.toarray()
    source.X = counts if layout == "dense" else getattr(sp, layout + "_matrix")(counts)
    # Unrelated raw/layers must not be loaded or copied into a four-gene query.
    source.raw = source.copy()
    source.layers["unrelated"] = source.X.copy()
    src = tmp_path / "atlas.h5ad"
    source.write_h5ad(src)
    monkeypatch.setattr(core, "CHUNK_ROWS", 71)
    out = tmp_path / "result"
    core.extract(src, ["RHO", "LRAT"], out, "majorclass,celltype,tissue,donor,sampleid", "Rod,MG,RPE", 1e4, 5)
    query = ad.read_h5ad(out / "query.h5ad")
    totals = counts.sum(axis=1, dtype=np.float64)
    np.testing.assert_array_equal(query.obs["total_counts"], totals)
    np.testing.assert_array_equal(query.layers["counts"].toarray(), counts[:, [2, 0]])
    normalized = (counts[:, [2, 0]] * (1e4 / totals)[:, None]).astype(np.float32)
    np.testing.assert_allclose(query.X.toarray(), np.log1p(normalized), rtol=1e-6)
    assert query.raw is None and set(query.layers) - {None} == {"counts"}
    summary = pd.read_csv(out / "summary_majorclass.tsv", sep="\t")
    for gene, index in [("RHO", 2), ("LRAT", 0)]:
        for group in source.obs.majorclass.unique():
            keep = (source.obs.majorclass == group).to_numpy()
            row = summary[(summary.gene == gene) & (summary.majorclass == group)].iloc[0]
            assert row.pseudobulk_cp10k == pytest.approx(counts[keep, index].sum() / totals[keep].sum() * 1e4, rel=1e-5)


def test_missing_metadata_fails_before_reading_matrix(atlas, tmp_path):
    _, source = atlas
    source = source.copy()
    del source.obs["celltype"]
    src = tmp_path / "atlas.h5ad"
    source.write_h5ad(src)
    with pytest.raises(ValueError, match="ATLAS_OBS_COLS"):
        core.extract(src, ["LRAT"], tmp_path / "q", "majorclass,celltype,tissue,donor", "Rod")


def test_csc_reads_only_bounded_nnz_blocks(atlas, tmp_path, monkeypatch):
    _, source = atlas
    source = source.copy()
    source.X = source.X.tocsc()
    path = tmp_path / "csc.h5ad"
    source.write_h5ad(path)
    limit = 37
    assert source.X.nnz > limit * 3
    monkeypatch.setattr(core, "CHUNK_NNZ", limit)
    reads = []
    original = h5py.Dataset.__getitem__

    def bounded_read(dataset, index, *args, **kwargs):
        if dataset.file.filename == str(path) and dataset.name in {"/X/data", "/X/indices"}:
            assert isinstance(index, slice), "CSC read must select a bounded nonzero block"
            start, stop, step = index.indices(dataset.shape[0])
            assert step == 1 and stop - start <= limit, "CSC loaded more than one nonzero block"
            reads.append((dataset.name, start, stop))
        return original(dataset, index, *args, **kwargs)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", bounded_read)
    out = tmp_path / "result"
    core.extract(path, ["RHO", "LRAT"], out, "majorclass,celltype,tissue,donor,sampleid", "Rod,MG,RPE", 1e4, 5)
    query = ad.read_h5ad(out / "query.h5ad")
    np.testing.assert_array_equal(query.layers["counts"].toarray(), source.X[:, [2, 0]].toarray())
    np.testing.assert_array_equal(query.obs["total_counts"], np.ravel(source.X.sum(axis=1, dtype=np.float64)))
    # All full-transcriptome entries are visited exactly once for each backing
    # vector; selected query columns never trigger a second full-column read.
    for name in ("/X/data", "/X/indices"):
        segments = [(start, stop) for key, start, stop in reads if key == name]
        assert segments == [(start, min(start + limit, source.X.nnz)) for start in range(0, source.X.nnz, limit)]


def test_csc_empty_query_column_and_duplicate_nonzeros(tmp_path, monkeypatch):
    # Rows may recur both across columns and within a non-canonical CSC column.
    counts = sp.csc_matrix((np.array([2, 3, 7, 11]), np.array([0, 0, 1, 2]), np.array([0, 2, 2, 4])), shape=(4, 3))
    path = tmp_path / "duplicate.h5ad"
    ad.AnnData(counts).write_h5ad(path)
    monkeypatch.setattr(core, "CHUNK_NNZ", 1)
    with h5py.File(path, "r") as handle:
        selected, totals = core._csc_query_counts(handle["X"], np.array([2, 1, 0]), 4)
    np.testing.assert_array_equal(selected.toarray(), counts[:, [2, 1, 0]].toarray())
    np.testing.assert_array_equal(totals, [5, 7, 11, 0])
