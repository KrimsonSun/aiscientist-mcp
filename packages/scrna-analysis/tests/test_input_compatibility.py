"""New AnnData inputs remain usable without changing expression or caller data."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import anndata as ad
import h5py
import numpy as np
import pandas as pd
import pytest
from aiscientist_mcp.scrna_analysis import runtime
from aiscientist_mcp.scrna_analysis.input_compatibility import normalize_h5ad
from scipy import sparse


def modern_input(path, dense=False):
    matrix = np.arange(12, dtype=np.float32).reshape(4, 3)
    if not dense:
        matrix = sparse.csr_matrix(matrix)
    a = ad.AnnData(matrix)
    a.obs_names = pd.Index(["cell 视网膜", "cell b", "cell c", "cell d"], dtype="string")
    a.var_names = pd.Index(["gene A", "gene B", "gene C"], dtype="string")
    a.obs["label"] = pd.array(["Rod", None, "BC", "Rod"], dtype="string")
    a.obs["empty"] = pd.array([None] * 4, dtype="string")
    a.obs["category"] = pd.Categorical(["b", "a", "b", "a"], categories=pd.Index(["a", "b"], dtype="string"))
    a.var["symbol"] = pd.array(["A", "B", "C"], dtype="string")
    a.layers["counts"] = matrix.copy()
    a.raw = a.copy()
    a.obsm["X_pca"] = np.arange(8, dtype=np.float64).reshape(4, 2)
    with ad.settings.override(allow_write_nullable_strings=True):
        a.write_h5ad(path, convert_strings_to_categoricals=False)
    return a


def numeric_storage(path):
    result = {}
    with h5py.File(path) as handle:

        def visit(name, value):
            if isinstance(value, h5py.Dataset) and (
                name in {"X", "raw/X"} or name.startswith(("X/", "layers/", "raw/X/", "obsm/"))
            ):
                result[name] = (str(value.dtype), value.shape, value[:].tobytes())

        handle.visititems(visit)
    return result


@pytest.mark.parametrize("dense", [False, True])
def test_private_storage_normalization_preserves_all_values_and_original(tmp_path, dense):
    source = tmp_path / "modern.h5ad"
    original = modern_input(source, dense)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "private.h5ad"
    changes = normalize_h5ad(source, target)
    assert {"obs/_index", "var/_index", "raw/var/_index"} <= {item["field"] for item in changes}
    actual = ad.read_h5ad(target)
    assert numeric_storage(source) == numeric_storage(target)
    for left, right in [(original.obs, actual.obs), (original.var, actual.var), (original.raw.var, actual.raw.var)]:
        pd.testing.assert_frame_equal(left.astype("string"), right.astype("string"), check_index_type=False)
    # A normal writer needs no opt-in nullable-string setting after conversion.
    with ad.settings.override(allow_write_nullable_strings=False):
        actual.write_h5ad(tmp_path / "rewritten.h5ad")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert normalize_h5ad(target, tmp_path / "unused.h5ad") == []
    assert not (tmp_path / "unused.h5ad").exists()


def test_private_checkpoint_conversion_is_atomic_and_rejects_symlinks(tmp_path):
    private = tmp_path / "adata_qc.h5ad"
    modern_input(private)
    assert normalize_h5ad(private, private)
    assert normalize_h5ad(private, private) == []
    link = tmp_path / "link.h5ad"
    link.symlink_to(private)
    with pytest.raises(ValueError, match="symlink"):
        normalize_h5ad(link, tmp_path / "out.h5ad")
    with pytest.raises(ValueError, match="regular file"):
        normalize_h5ad(private, link)


@pytest.mark.parametrize("field", ["obs", "raw", "var/symbol"])
def test_external_hdf5_links_never_mutate_caller_owned_files(tmp_path, field):
    original = tmp_path / "caller.h5ad"
    modern_input(original)
    source = tmp_path / "linked.h5ad"
    modern_input(source)
    with h5py.File(source, "r+") as handle:
        del handle[field]
        handle[field] = h5py.ExternalLink(str(original), "/" + field)
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="external or soft HDF5 links"):
        normalize_h5ad(source, tmp_path / "out.h5ad")
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    assert not (tmp_path / "out.h5ad").exists()


def test_soft_hdf5_metadata_link_is_rejected(tmp_path):
    source = tmp_path / "soft.h5ad"
    modern_input(source)
    with h5py.File(source, "r+") as handle:
        handle.move("obs", "original_obs")
        handle["obs"] = h5py.SoftLink("/original_obs")
    with pytest.raises(ValueError, match="external or soft HDF5 links"):
        normalize_h5ad(source, tmp_path / "out.h5ad")


def test_execution_stages_dataset_and_records_source_without_rebinding(tmp_path, monkeypatch):
    source = tmp_path / "modern.h5ad"
    modern_input(source)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCRNA_READ_ROOTS", str(tmp_path))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    monkeypatch.delenv("SCRNA_CHECKPOINT_ROOT", raising=False)
    observed = []

    def science(args, ctx):
        path = Path(ctx.decisions["dataset_path"])
        observed.append(path)
        assert path != source and path.name == source.name
        assert str(ctx.workspace) in str(path)
        a = ad.read_h5ad(path)
        with ad.settings.override(allow_write_nullable_strings=False):
            a.write_h5ad(ctx.workspace / "work/adata_qc.h5ad")
        return {"status": "ok", "checkpoint": "work/adata_qc.h5ad"}

    original_import = runtime.importlib.import_module
    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(run_scanpy_qc=science) if name.endswith("run_scanpy_qc.tool") else original_import(name)
        ),
    )

    def inline(cfg, command, task, name):
        runtime.execute("compatible", task.name)
        runtime._json(task / "state.json", {"executor": "inline", "state": "COMPLETED"})
        runtime._json(task / "done.json", {"state": "COMPLETED"})

    monkeypatch.setattr(runtime.jobs, "submit", inline)
    result = runtime.submit("run_scanpy_qc", {}, "compatible", str(source))
    assert result["status"] == "ok"
    assert Path(result["input_compatibility_path"]).is_file()
    assert runtime._read(runtime.workspace("compatible") / "dataset.json")["path"] == str(source)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    cached = runtime.submit("run_scanpy_qc", {}, "compatible", str(source))
    assert cached["task_id"] == result["task_id"] and len(observed) == 1
