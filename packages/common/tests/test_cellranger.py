"""Existing Cell Ranger layout/metric regressions, ported from AiScientist 48f64a6."""

import pytest
from aiscientist_mcp.common.cellranger import cellranger_layout_hint, describe_cellranger_layout


def test_the_three_forms_of_a_library_are_recognised():
    paths = [
        "/up/S1/raw_feature_bc_matrix.h5",
        "/up/S1/filtered_feature_bc_matrix.h5",
        "/up/S1/raw_feature_bc_matrix.tar.gz",
        "/up/S1/filtered_feature_bc_matrix.tar.gz",
        "/up/S1/analysis.tar.gz",
        "/up/S1/possorted_genome_bam.bam",
        "/up/S1/possorted_genome_bam.bam.bai",
        "/up/S1/metrics_summary.csv",
        "/up/S2/outs/raw_feature_bc_matrix",
        "/up/S2/outs/filtered_feature_bc_matrix",
        "/up/S2/outs/analysis",
        "/up/S3/filtered_feature_bc_matrix.h5",  # no raw matrix: not a library
    ]
    layout = describe_cellranger_layout(paths, "/up")
    assert layout["n_libraries"] == 2
    s1, s2 = layout["libraries"]
    assert s1["sample"] == "S1" and s1["bam"] and s1["packed"] and s1["clusters"] and s1["metrics"]
    assert s2["sample"] == "S2" and s2["dir"] == "/up/S2/outs"  # outs/ takes its parent's name
    assert not s2["bam"] and not s2["packed"] and s2["mtx_dirs"]
    assert not layout["all_have_bam"] and layout["any_packed"]


def test_the_bound_folder_can_itself_be_the_library():
    layout = describe_cellranger_layout(
        ["raw_feature_bc_matrix.h5", "filtered_feature_bc_matrix.h5"], "/up/Sample3_GSM5676874"
    )
    assert layout["libraries"][0]["sample"] == "Sample3_GSM5676874"


def test_anything_else_is_not_a_delivery():
    assert describe_cellranger_layout(["/up/a.h5ad", "/up/notes.txt"], "/up") is None
    assert cellranger_layout_hint(None) == ""


def test_the_hint_names_the_qc_route_and_what_not_to_plan():
    layout = describe_cellranger_layout(["raw_feature_bc_matrix.h5", "filtered_feature_bc_matrix.h5"], "/up/S1")
    hint = cellranger_layout_hint(layout)
    assert "`run_cellqc`" in hint and "Do NOT plan `run_scanpy_qc`" in hint
    assert "Do NOT plan `run_doublet_detection`" in hint and "1 library (S1)" in hint
    # Cell Ranger writes integer counts, so even with no file read the audit is ruled out.
    assert "PROVENANCE IS SETTLED" in hint and "Do NOT plan a `run_code` step" in hint


# --- what the matrices hold -----------------------------------------------------------------------------

# 5 features x 4 barcodes, the way Cell Ranger lays it out: CSC, one column per BARCODE. Barcode 3 is
# empty; feature 4 is an antibody, which Cell Ranger's UMI/gene metrics leave out.
_COUNTS = [[3, 0, 1, 0], [0, 2, 0, 0], [1, 1, 4, 0], [0, 5, 2, 0], [7, 0, 9, 0]]
_TYPES = ["Gene Expression"] * 4 + ["Antibody Capture"]
# metrics_summary.csv as Cell Ranger 9 writes it (header row, one value row; thousands separators).
_METRICS = (
    "Estimated Number of Cells,Median Genes per Cell,Number of Reads,Valid UMIs,"
    'Median UMI Counts per Cell\n"4","2","342,370,742",97.8%,"4"\n'
)


def _write_10x_h5(path, counts=_COUNTS, *, types=_TYPES, genome="GRCm39", dtype="int32", v2=False, gene_names=None):
    h5py = pytest.importorskip("h5py")
    np = pytest.importorskip("numpy")
    dense = np.asarray(counts, dtype=dtype)
    n_features, n_barcodes = dense.shape
    data, indices, indptr = [], [], [0]
    for col in range(n_barcodes):
        rows = np.nonzero(dense[:, col])[0]
        data += list(dense[rows, col])
        indices += list(rows)
        indptr.append(len(data))
    with h5py.File(path, "w") as f:
        g = f.create_group(genome if v2 else "matrix")
        g["data"] = np.asarray(data, dtype=dtype)
        g["indices"] = np.asarray(indices, dtype="int64")
        g["indptr"] = np.asarray(indptr, dtype="int64")
        g["shape"] = np.asarray([n_features, n_barcodes], dtype="int32")
        g["barcodes"] = np.asarray([f"AAAC{i}-1".encode() for i in range(n_barcodes)])
        ids = np.asarray([f"ENSMUSG{i:011d}".encode() for i in range(n_features)])
        names = np.asarray([(gene_names[i] if gene_names else f"Gene{i}").encode() for i in range(n_features)])
        if v2:
            g["genes"], g["gene_names"] = ids, names
        else:
            feats = g.create_group("features")
            feats["id"], feats["name"] = ids, names
            feats["feature_type"] = np.asarray([t.encode() for t in types])
            feats["genome"] = np.asarray([genome.encode()] * n_features)
            f.attrs["chemistry_description"] = "Single Cell 3' v4 (polyA)"
            f.attrs["library_ids"] = np.asarray([b"3v4_WT_1M"])
            f.attrs["software_version"] = "cellranger-9.0.1"
    return path


def test_a_cell_ranger_h5_is_read_the_right_way_round(tmp_path, monkeypatch):
    from aiscientist_mcp.common import cellranger

    path = _write_10x_h5(tmp_path / "filtered_feature_bc_matrix.h5")
    monkeypatch.setattr(cellranger, "_CHUNK_ENTRIES", 3)  # several barcodes per read, and splits
    m = cellranger.describe_10x_h5(str(path))
    assert (m["layout"], m["n_barcodes"], m["n_features"]) == ("v3", 4, 5)
    assert m["genomes"] == {"GRCm39": 5} and m["feature_types"]["Antibody Capture"] == 1
    assert m["library_ids"] == ["3v4_WT_1M"] and m["software_version"] == "cellranger-9.0.1"
    c = m["counts"]
    assert c["integer"] and c["non_negative"] and c["empty_barcodes"] == 1
    # Gene Expression only: per-barcode UMIs 4, 8, 7, 0 and genes 2, 3, 3, 0 (the antibody's 7 and 9
    # are left out, as metrics_summary.csv leaves them out).
    assert c["total_umis"] == 19
    assert (c["median_umis_per_barcode"], c["median_genes_per_barcode"]) == (5.5, 2.5)


def test_the_reader_agrees_with_scanpy(tmp_path):
    sc = pytest.importorskip("scanpy")
    path = _write_10x_h5(tmp_path / "m.h5", types=["Gene Expression"] * 5)
    from aiscientist_mcp.common.cellranger import describe_10x_h5

    adata = sc.read_10x_h5(str(path))
    m = describe_10x_h5(str(path))
    assert adata.shape == (m["n_barcodes"], m["n_features"])
    assert int(adata.X.sum()) == m["counts"]["total_umis"] == 35


def test_the_cell_ranger_2_layout_is_read_too(tmp_path):
    from aiscientist_mcp.common.cellranger import describe_10x_h5

    m = describe_10x_h5(str(_write_10x_h5(tmp_path / "v2.h5", genome="mm10", v2=True)))
    assert m["layout"] == "v2" and m["genomes"] == {"mm10": 5} and m["n_barcodes"] == 4
    assert m["counts"]["total_umis"] == 35  # no feature types: every feature counts


def test_anything_but_a_10x_matrix_reads_as_none(tmp_path):
    h5py = pytest.importorskip("h5py")
    from aiscientist_mcp.common.cellranger import describe_10x_h5

    with h5py.File(tmp_path / "x.h5ad", "w") as f:
        f.create_group("obs")
        f["X"] = [[1.0]]
    (tmp_path / "empty.h5").write_bytes(b"")
    assert describe_10x_h5(str(tmp_path / "x.h5ad")) is None
    assert describe_10x_h5(str(tmp_path / "empty.h5")) is None
    assert describe_10x_h5(str(tmp_path / "missing.h5")) is None


def test_metrics_summary_numbers_and_the_check(tmp_path):
    from aiscientist_mcp.common.cellranger import check_against_metrics, describe_10x_h5, parse_metrics_summary

    metrics = parse_metrics_summary(_METRICS)
    assert metrics["Number of Reads"] == 342370742 and metrics["Valid UMIs"] == pytest.approx(0.978)
    assert parse_metrics_summary("not,a\n") == {} and parse_metrics_summary("") == {}
    m = describe_10x_h5(str(_write_10x_h5(tmp_path / "m.h5")))
    check = check_against_metrics(m, metrics)
    # 4 cells exactly; medians 5.5 vs 4 is off, 2.5 vs 2 is within Cell Ranger's rounding.
    assert [c["match"] for c in check["checks"]] == [True, False, True] and not check["agrees"]
    assert check_against_metrics(m, {"Number of Reads": 1.0}) is None
