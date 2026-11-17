"""Regression fixtures and expectations ported from AiScientist test_run_cellqc.py.

The executable below stands in for CellQC; it does not validate the real R pipeline.
"""

import json
import shutil
from types import SimpleNamespace

import pytest


def _ctx(ws, root):
    return SimpleNamespace(workspace=ws, decisions={"dataset_root": str(root)} if root else {})


def _web_summary(chem, ref):
    return f'<script>var data = [["Chemistry","{chem}"],["Transcriptome","{ref}"]];</script>'


_RESULT_FILES = {
    "manifest.tsv",
    "metrics.csv",
    "qc_status.csv",
    "report.html",
    "report_slides.pdf",
    "report_slides.tex",
    "S1_doublet_concordance.txt",
    "S1_doublet_summary.txt",
    "S1.h5ad",
    "S1_obs.txt.gz",
    "S1_var.txt.gz",
}


def test_cellqc_end_to_end_with_a_fake_pipeline(tmp_path, delivery, fake_cellqc):
    pytest.importorskip("scanpy")
    from aiscientist_mcp.cellqc.core import run_cellqc

    ws = tmp_path / "run"
    out = run_cellqc({"max_pct_mt": 5}, _ctx(ws, delivery))
    assert out["status"] == "ok", out

    # Staged without writing into the delivery: archives unpacked into directories of their name.
    staged = ws / "work" / "cellqc" / "lnfiles"
    assert (staged / "S1" / "filtered_feature_bc_matrix" / "barcodes.tsv.gz").is_file()
    assert (staged / "S1" / "analysis" / "clustering" / "gene_expression_graphclust" / "clusters.csv").is_file()
    assert (staged / "S2" / "raw_feature_bc_matrix").is_symlink()
    assert not (delivery / "S1" / "filtered_feature_bc_matrix").exists()

    # Configured from the data: mouse gene sets, and the caller's mito threshold.
    config = json.loads((ws / "work" / "cellqc" / "config.yaml").read_text())
    assert config["geneset"]["mt"]["patterns"] == ["^mt-"]
    assert config["filterbycount"]["mito"] == 5.0
    assert out["assumptions"]["species"][0] == "mouse" and "GRCm39" in out["assumptions"]["species"][1]
    assert any("disagree on the 10x chemistry" in w for w in out["warnings"])  # v4 vs v3
    lines = (ws / "work" / "cellqc" / "samples.txt").read_text().splitlines()
    assert lines[0] == "sample\tcellranger\tnreaction" and len(lines) == 3

    # A dry run first, then the run.
    calls = [json.loads(ln) for ln in fake_cellqc.read_text().splitlines()]
    assert "-n" in calls[0] and "-n" not in calls[1]

    # The merged checkpoint has the shape the rest of the line reads.
    import anndata as ad

    adata = ad.read_h5ad(ws / "work" / "adata_qc.h5ad")
    assert set(adata.obs["sampleid"]) == {"S1", "S2"} and adata.n_obs == out["cells_after"] == 90
    assert "counts" in adata.layers and adata.raw is not None and "highly_variable" in adata.var
    assert out["obs_sample_key"] == "sampleid" and out["doublets"]["removed_upstream"]

    # The validation checklist became warnings; CellQC's report is in the artifacts.
    text = "\n".join(out["warnings"])
    assert "S1 kept only 40% of its Cell Ranger cells" in text and "failed only the max_pct_mt" in text
    assert "S1: mean ambient contamination 31%" in text
    assert out["libraries"][0]["ambient_decontx_contamination_mean"] == 0.07  # the compared method
    assert "agree poorly (kappa 0.10)" in text
    assert "Report it" not in text  # the warnings reach the user's report as they are
    assert "nuclear_fraction" not in text  # skipped without a BAM is expected
    assert out["libraries"][0]["removed_only_by"]["max_pct_mt"] == 7
    # The kept cells' median % mito beside CellQC's own, which is over the cells before filtering.
    for lib in out["libraries"]:
        kept = adata.obs.loc[adata.obs["sampleid"] == lib["sample"], "pct_counts_mt"]
        assert lib["median_pct_mt_kept"] == round(float(kept.median()), 4)
        assert lib["median_pct_mt"] == 1.2


def test_the_whole_cellqc_result_directory_is_handed_on(tmp_path, delivery, fake_cellqc):
    """The bundle used to get seven hand-picked files under ``cellqc/`` (filed under ``extra/`` at
    publish): no per-library matrix, no obs/var tables, no slide source."""
    pytest.importorskip("scanpy")
    from aiscientist_mcp.cellqc.core import RESULT_POINTER as CELLQC_RESULT_POINTER
    from aiscientist_mcp.cellqc.core import run_cellqc

    ws = tmp_path / "run"
    out = run_cellqc({"samples": ["S1"]}, _ctx(ws, delivery))
    assert out["status"] == "ok", out

    # Complete, beside the run's other deliverable data and outside artifacts/.
    full = ws / "result" / "cellqc"
    assert {p.name for p in full.iterdir()} == _RESULT_FILES
    produced = ws / "work" / "cellqc" / "out" / "result"
    assert (full / "S1.h5ad").read_bytes() == (produced / "S1.h5ad").read_bytes()
    assert (produced / "S1.h5ad").is_file()  # CellQC's own copy is left where it was

    # The bundle has CellQC's directory under its own name, without the matrix.
    bundled = ws / "artifacts" / "result"
    assert {p.name for p in bundled.iterdir()} == _RESULT_FILES - {"S1.h5ad"}
    assert not (ws / "artifacts" / "cellqc").exists()
    assert sorted(out["cellqc_outputs"]) == sorted(f"result/{n}" for n in _RESULT_FILES - {"S1.h5ad"})

    # The pointer the console reads: where the complete directory is, and what is in it.
    pointer = json.loads((ws / "artifacts" / "data" / CELLQC_RESULT_POINTER).read_text())
    assert pointer["path"] == str(full) and pointer["saved_to"] is None
    assert {f["name"] for f in pointer["files"]} == _RESULT_FILES
    assert pointer["size_bytes"] == sum(p.stat().st_size for p in full.iterdir())
    assert pointer["matrices"] == ["S1.h5ad"] and pointer["libraries"] == ["S1"]
    assert pointer["not_in_bundle"] == ["S1.h5ad"]
    assert out["cellqc_result"]["n_files"] == len(_RESULT_FILES)
    assert out["cellqc_result"]["matrices"] == ["S1.h5ad"]


def test_a_table_too_big_for_the_bundle_stays_with_the_complete_directory(tmp_path, delivery, fake_cellqc, monkeypatch):
    pytest.importorskip("scanpy")
    from aiscientist_mcp.cellqc import core as tool

    monkeypatch.setattr(tool, "_BUNDLE_FILE_MAX", 30)  # the bundle is re-copied after every step
    ws = tmp_path / "run"
    out = tool.run_cellqc({"samples": ["S1"]}, _ctx(ws, delivery))
    assert out["status"] == "ok", out
    pointer = json.loads((ws / "artifacts" / "data" / "cellqc_result.json").read_text())
    assert "S1_obs.txt.gz" in pointer["not_in_bundle"] and "S1_doublet_summary.txt" not in pointer["not_in_bundle"]
    assert (ws / "result" / "cellqc" / "S1_obs.txt.gz").is_file()
    assert not (ws / "artifacts" / "result" / "S1_obs.txt.gz").exists()
    assert (ws / "artifacts" / "result" / "S1_doublet_summary.txt").is_file()


def test_a_second_cellqc_run_replaces_the_first_runs_result_files(tmp_path, delivery, fake_cellqc):
    pytest.importorskip("scanpy")
    from aiscientist_mcp.cellqc.core import run_cellqc

    ws = tmp_path / "run"
    assert run_cellqc({}, _ctx(ws, delivery))["status"] == "ok"
    assert (ws / "result" / "cellqc" / "S2.h5ad").is_file()
    shutil.rmtree(ws / "work" / "cellqc" / "out")  # as a fresh output directory would be
    assert run_cellqc({"samples": ["S1"]}, _ctx(ws, delivery))["status"] == "ok"
    assert not (ws / "result" / "cellqc" / "S2.h5ad").exists()
    assert not (ws / "artifacts" / "result" / "S2_obs.txt.gz").exists()


def test_a_nonzero_exit_with_results_is_a_warning_not_a_failure(tmp_path, delivery, fake_cellqc, monkeypatch):
    pytest.importorskip("scanpy")
    from aiscientist_mcp.cellqc.core import run_cellqc

    monkeypatch.setenv("FAKE_CELLQC_RC", "1")  # e.g. the slide deck failed
    out = run_cellqc({"samples": ["S2"]}, _ctx(tmp_path / "run", delivery))
    assert out["status"] == "ok" and out["n_libraries"] == 1
    assert any("exited with status 1" in w for w in out["warnings"])


def test_a_mixed_species_cohort_is_qcd_one_species_per_cellqc_run(tmp_path, delivery, fake_cellqc):
    # The 2026-10-02 test folder held two mouse libraries and a human one: QC'd together, the human
    # cells were filtered on mouse "^mt-" genes. That was then refused, which also refused the two
    # folders of RCHENLAB/AiScientist issue 22 (a human and a mouse library). Each species now gets a
    # CellQC run of its own, with its own gene sets, and both are kept.
    pytest.importorskip("scanpy")
    import csv

    import anndata as ad
    from aiscientist_mcp.cellqc.core import run_cellqc

    (delivery / "S2" / "outs" / "web_summary.html").write_text(
        _web_summary("Single Cell 3' v3 (polyA)", "GRCh38-2024-A")
    )
    ws = tmp_path / "run"
    out = run_cellqc({}, _ctx(ws, delivery))
    assert out["status"] == "ok", out
    qc = ws / "work" / "cellqc"
    assert json.loads((qc / "mouse" / "config.yaml").read_text())["geneset"]["mt"]["patterns"] == ["^mt-"]
    assert json.loads((qc / "human" / "config.yaml").read_text())["geneset"]["mt"]["patterns"] == ["^MT-"]
    assert [ln.split("\t")[0] for ln in (qc / "human" / "samples.txt").read_text().splitlines()[1:]] == ["S2"]
    # The primary's species is the checkpoint later steps read; the other has its own.
    assert out["checkpoints"]["mouse"]["checkpoint"] == "adata_qc.h5ad"
    assert out["checkpoints"]["human"]["checkpoint"] == "adata_qc.human.h5ad"
    assert set(ad.read_h5ad(ws / "work" / "adata_qc.h5ad").obs["sampleid"]) == {"S1"}
    assert set(ad.read_h5ad(ws / "work" / "adata_qc.human.h5ad").obs["sampleid"]) == {"S2"}
    assert out["n_libraries"] == 2 and {lib["species"] for lib in out["libraries"]} == {"mouse", "human"}
    assert out["cells_after"] == sum(lib["cells_after_doublets"] for lib in out["libraries"])
    assert out["assumptions"]["species"][0] == "mouse+human"
    assert any("different species (mouse: S1; human: S2)" in w for w in out["notes"])
    # ONE flat result directory: each library's files, the reports per species, the tables joined.
    names = {p.name for p in (ws / "artifacts" / "result").iterdir()}
    assert {
        "report_mouse.html",
        "report_human.html",
        "S1_obs.txt.gz",
        "S2_obs.txt.gz",
        "metrics.csv",
        "manifest.tsv",
        "qc_status.csv",
    } <= names
    rows = list(csv.DictReader((ws / "artifacts" / "result" / "metrics.csv").open()))
    assert [(r["sampleid"], r["species"]) for r in rows] == [("S1", "mouse"), ("S2", "human")]
    pointer = json.loads((ws / "artifacts" / "data" / "cellqc_result.json").read_text())
    assert pointer["libraries"] == ["S1", "S2"] and set(pointer["matrices"]) == {"S1.h5ad", "S2.h5ad"}
    assert len(out["figures"]) == 2  # one QC violin per species
    # Asking for one species' libraries is one run, as before.
    one = run_cellqc({"samples": ["S2"]}, _ctx(tmp_path / "run2", delivery))
    assert one["status"] == "ok" and one["assumptions"]["species"][0] == "human"
    assert "checkpoints" not in one


def test_tectonic_keeps_its_tex_files_in_the_users_tool_cache(tmp_path, delivery, fake_cellqc, monkeypatch):
    # CellQC's slide deck: 1 min 43 s with an empty tectonic cache, 6 s with a filled one.
    from aiscientist_mcp.cellqc.core import run_cellqc

    cache = tmp_path / "tool-cache" / "tester"
    cache.mkdir(parents=True)
    monkeypatch.setenv("AISCIENTIST_TOOL_CACHE", str(cache))
    run_cellqc({}, _ctx(tmp_path / "run", delivery))
    assert fake_cellqc.with_suffix(".tectonic").read_text() == str(cache / "tectonic")
    assert (cache / "tectonic").is_dir()


def test_without_a_tool_cache_tectonic_uses_the_runs_own(tmp_path, delivery, fake_cellqc, monkeypatch):
    from aiscientist_mcp.cellqc.core import run_cellqc

    monkeypatch.delenv("AISCIENTIST_TOOL_CACHE", raising=False)
    run_cellqc({}, _ctx(tmp_path / "run", delivery))
    assert fake_cellqc.with_suffix(".tectonic").read_text() == ""  # XDG_CACHE_HOME/tectonic


def test_a_tool_cache_that_cannot_be_used_is_ignored(tmp_path, monkeypatch):
    from aiscientist_mcp.cellqc.core import _tex_cache

    not_a_folder = tmp_path / "tool-cache"
    not_a_folder.write_text("")
    monkeypatch.setenv("AISCIENTIST_TOOL_CACHE", str(not_a_folder))
    assert _tex_cache() == ""


def test_without_a_folder_it_points_at_run_scanpy_qc(tmp_path, fake_cellqc):
    from aiscientist_mcp.cellqc.core import run_cellqc

    out = run_cellqc({}, _ctx(tmp_path / "run", None))
    assert out["status"] == "error" and "run_scanpy_qc" in out["error"]


def test_without_cellqc_installed_it_says_so(tmp_path, delivery, monkeypatch):
    from aiscientist_mcp.cellqc.core import run_cellqc

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = run_cellqc({}, _ctx(tmp_path / "run", delivery))
    assert out["status"] == "not_enabled" and "CellQC executable is missing" in out["note"]


def test_a_folder_with_no_library_is_explained(tmp_path, fake_cellqc):
    from aiscientist_mcp.cellqc.core import run_cellqc

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.h5ad").write_bytes(b"")
    out = run_cellqc({}, _ctx(tmp_path / "run", tmp_path / "data"))
    assert out["status"] == "error" and "No Cell Ranger library" in out["error"]


def test_text_columns_are_written_as_plain_strings_for_the_older_anndata_downstream():
    # run_cellqc writes with anndata 0.13 / pandas 3 (nullable strings); analysis.sif's anndata 0.12
    # reads those but cannot write them back, so the next tool died saving its checkpoint.
    ad = pytest.importorskip("anndata")
    import numpy as np
    import pandas as pd
    from aiscientist_mcp.cellqc.core import _plain_strings

    if hasattr(pd.options, "future") and hasattr(pd.options.future, "infer_string"):
        pd.set_option("future.infer_string", True)  # pandas 3 behaviour: str re-inferred
    obs = pd.DataFrame(
        {
            "sampleid": pd.Categorical(pd.array(["a", "b"], dtype="string")),
            "note": pd.array(["x", "y"], dtype="string"),
        },
        index=pd.Index(pd.array(["c1", "c2"], dtype="string")),
    )
    var = pd.DataFrame(
        {"gene_ids": pd.array(["E1", "E2", "E3"], dtype="string")},
        index=pd.Index(pd.array(["G1", "G2", "G3"], dtype="string")),
    )
    a = ad.AnnData(X=np.ones((2, 3)), obs=obs, var=var)
    a.raw = a.copy()  # .raw keeps its own var
    _plain_strings(a)
    assert a.raw.var.index.dtype == object
    assert a.obs.index.dtype == object and a.var.index.dtype == object
    assert a.obs["note"].dtype == object and a.var["gene_ids"].dtype == object
    assert a.obs["sampleid"].cat.categories.dtype == object
    assert a.raw.var["gene_ids"].dtype == object
    if hasattr(pd.options, "future") and hasattr(pd.options.future, "infer_string"):
        pd.set_option("future.infer_string", False)


@pytest.mark.parametrize(
    "threshold,column,median_column,value,median,median_text",
    [
        ("max_pct_mt", "mito", "filter_median_pct_counts_mt", 10, 6.0886, "6.09%"),
        ("min_counts", "mincount", "cellranger_median_umi_counts_per_cell", 1000, 2400, "2,400."),
        ("min_features", "minfeature", "cellranger_median_genes_per_cell", 200, 500, "500."),
    ],
)
@pytest.mark.parametrize("removed,expected", [(2277, True), (1355, False), (400, False)])
def test_threshold_alone_warns_above_ten_percent(
    threshold, column, median_column, value, median, median_text, removed, expected
):
    from aiscientist_mcp.cellqc.core import _library_summary, _validate

    row = {
        "sampleid": "Sample3",
        "filter_ncell_before": "13559",
        f"filter_fail_{column}_only": str(removed),
        median_column: str(median),
    }
    lib = _library_summary(row, "doubletfinder", "")
    warnings = _validate([lib], [], [], {threshold: value})
    warning = next((w for w in warnings if "alone removed" in w), "")
    assert bool(warning) is expected
    if expected:
        assert f"{threshold}={value}" in warning
        assert "2,277 of 13,559 cells (16.8%)" in warning
        assert "median before filtering" in warning and f": {median_text}" in warning


@pytest.mark.parametrize(
    "knee,inflection,expected", [(117, 683, True), (1615, 1046, False), (683, 683, False), ("NA", 683, False)]
)
def test_barcode_rank_warns_only_for_knee_below_inflection(knee, inflection, expected):
    from aiscientist_mcp.cellqc.core import _library_summary, _validate

    lib = _library_summary(
        {"sampleid": "S2", "barcoderank_knee_total": str(knee), "barcoderank_inflection_total": str(inflection)},
        "doubletfinder",
        "",
    )
    warnings = _validate([lib], [], [], {})
    warning = next((w for w in warnings if "barcode-rank knee" in w), "")
    assert bool(warning) is expected
    if expected:
        assert "117 UMIs" in warning and "683 UMIs" in warning
        assert "Cell Ranger's called cells were used" in warning


def test_one_metric_named_two_ways_by_cell_ranger_is_one_column_when_species_are_joined(tmp_path):
    # The mouse libraries' Cell Ranger 9 writes cellranger_valid_umis, the human library's older
    # Cell Ranger cellranger_valid_umi_sequences: the joined table carries the metric in one column.
    import csv

    from aiscientist_mcp.cellqc.core import _combine_results

    runs = []
    for species, sample, column in (
        ("mouse", "S1", "cellranger_valid_umis"),
        ("human", "S2", "cellranger_valid_umi_sequences"),
    ):
        res = tmp_path / species / "result"
        res.mkdir(parents=True)
        (res / "metrics.csv").write_text(f"sampleid,{column},frac_retained\n{sample},99.9%,0.9\n")
        runs.append({"species": species, "result_dir": res, "included": [sample], "libs": [{"sample": sample}]})
    out = _combine_results(runs, tmp_path / "joined")
    with (out / "metrics.csv").open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert reader.fieldnames == ["sampleid", "cellranger_valid_umis", "frac_retained", "species"]
    assert [(r["sampleid"], r["cellranger_valid_umis"]) for r in rows] == [("S1", "99.9%"), ("S2", "99.9%")]
