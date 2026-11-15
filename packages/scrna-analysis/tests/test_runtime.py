"""Real MCP protocol, isolated jobs, admission and job reuse boundaries."""

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from aiscientist_mcp.scrna_analysis import runtime


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    monkeypatch.setenv("SCRNA_READ_ROOTS", str(tmp_path / "inputs"))
    monkeypatch.delenv("SCRNA_DATASET", raising=False)
    monkeypatch.delenv("AISCIENTIST_DATASET", raising=False)
    (tmp_path / "inputs").mkdir()
    return tmp_path


def test_real_mcp_schema_preserves_legacy_properties_and_defaults(settings):
    from aiscientist_mcp.scrna_analysis.server import server
    from mcp import Client

    async def check():
        async with Client(server) as client:
            listing = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(listing) == set(runtime.SPECS) | {"scrna_job_status"}
            for name, spec in runtime.SPECS.items():
                properties = listing[name].input_schema["properties"]
                for key, expected in spec["inputSchema"]["properties"].items():
                    for field, value in expected.items():
                        assert properties[key][field] == value, (name, key, field)
                    assert ("default" in properties[key]) == ("default" in expected)
                assert not listing[name].input_schema.get("required")
            for name, args in [
                ("run_scanpy_qc", {"min_genes": "2"}),
                ("run_scanpy_qc", {"min_genes": True}),
                ("run_integration", {"method": "invalid"}),
                ("run_integration", {"method": None}),
                ("run_clustering", {"resolution_candidates": None}),
            ]:
                assert (await client.call_tool(name, args)).is_error, (name, args)
            assert not (settings / "data").exists()

    asyncio.run(check())


def test_read_and_write_paths_are_confined(settings):
    source = settings / "inputs" / "source.h5ad"
    source.write_text("placeholder")
    ws = runtime.workspace("allowed")
    assert runtime.bind_dataset(ws, str(source)) == str(source)
    with pytest.raises(ValueError, match="outside"):
        runtime.bind_dataset(ws, str(settings / "secret.h5ad"))
    with pytest.raises(ValueError, match="invalid"):
        runtime.workspace("../escape")
    with pytest.raises(ValueError, match="inside"):
        runtime.submit("run_de", {"input": str(source)}, "allowed")
    (ws / "work" / "redirect.h5ad").symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        runtime.workspace("allowed")
    with pytest.raises(ValueError, match="names"):
        runtime.submit("run_enrichment", {"gene_sets": ["../private"]})


def test_pending_job_reused_and_other_writes_refused(settings, monkeypatch):
    source = settings / "inputs" / "source.h5ad"
    source.write_text("placeholder")
    monkeypatch.setenv("AISCI_EXECUTOR", "slurm")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "sbatch":
            return SimpleNamespace(stdout="12345;test-cluster\n")
        if command[0] == "squeue":
            return SimpleNamespace(stdout="PENDING\n")
        raise AssertionError(command)

    monkeypatch.setattr(runtime.jobs.subprocess, "run", fake_run)
    monkeypatch.setenv("AISCI_SLURM", "--partition=standard --cpus-per-task=2 --mem=4G")
    monkeypatch.setenv("AISCI_JOB_PREFIX", "apptainer exec /approved/science.sif")
    first = runtime.submit("run_scanpy_qc", {}, "queue", str(source))
    assert first["state"] == "PENDING" and first["job_id"] == "12345"
    again = runtime.submit("run_scanpy_qc", {}, "queue", str(source))
    assert again["task_id"] == first["task_id"]
    assert sum(command[0] == "sbatch" for command in commands) == 1
    with pytest.raises(ValueError, match="active task"):
        runtime.submit("run_scanpy_qc", {"min_genes": 10}, "queue", str(source))
    script = runtime.workspace("queue") / "jobs" / first["task_id"] / "job.sbatch"
    assert "--partition=standard --cpus-per-task=2 --mem=4G" in script.read_text()
    assert "apptainer exec /approved/science.sif python -m aiscientist_mcp.scrna_analysis.worker" in script.read_text()


def test_changed_input_or_parameters_get_new_identity(settings, monkeypatch):
    source = settings / "inputs" / "source.h5ad"
    source.write_text("first")

    def finish(cfg, command, task, name):
        runtime._json(task / "state.json", {"state": "COMPLETED", "executor": "inline"})
        runtime._json(task / "done.json", {"state": "COMPLETED"})
        runtime._json(task / "result.json", {"status": "ok", "cells_after": 12})
        runtime._json(task / "outputs.json", [])

    monkeypatch.setattr(runtime.jobs, "submit", finish)
    first = runtime.submit("run_scanpy_qc", {}, "identity", str(source))
    again = runtime.submit("run_scanpy_qc", {}, "identity", str(source))
    assert again["task_id"] == first["task_id"]
    changed_args = runtime.submit("run_scanpy_qc", {"min_genes": 1}, "identity", str(source))
    assert changed_args["task_id"] != first["task_id"]
    source.write_text("second input of a different size")
    changed_input = runtime.submit("run_scanpy_qc", {}, "identity", str(source))
    assert changed_input["task_id"] != first["task_id"]


def test_analysis_lock_excludes_a_second_writer(settings):
    path = runtime.workspace("locked") / ".analysis.lock"
    with runtime.lock(path), pytest.raises(ValueError, match="busy"), runtime.lock(path):
        pytest.fail("another writer acquired the lock")


def test_worker_caches_are_private_attempt_directories(settings, monkeypatch):
    from aiscientist_mcp.scrna_analysis.worker import _configure_caches

    monkeypatch.setenv("NUMBA_CACHE_DIR", "/unwritable/shared-numba")
    monkeypatch.setenv("MPLCONFIGDIR", "/unwritable/shared-matplotlib")
    task = runtime.workspace("cache") / "jobs" / "attempt-one"
    task.mkdir()
    _configure_caches(task)
    assert os.environ["NUMBA_CACHE_DIR"] == str(task / "numba-cache")
    assert os.environ["MPLCONFIGDIR"] == str(task / "matplotlib-cache")
    (task / "numba-cache").rmdir()
    (task / "numba-cache").symlink_to(settings)
    with pytest.raises(ValueError, match="private task"):
        _configure_caches(task)


def test_checkpoint_handoff_is_operator_configured_and_never_overwrites(settings, monkeypatch):
    handoff = settings / "handoff"
    handoff.mkdir()
    (handoff / "adata_qc.h5ad").write_text("previous server output")
    (handoff / "unrelated.h5ad").write_text("not a checkpoint")
    monkeypatch.setenv("SCRNA_CHECKPOINT_ROOT", str(handoff))
    ws = runtime.workspace("handoff")
    assert runtime._handoff_checkpoints(ws) == [handoff / "adata_qc.h5ad"]
    (ws / "work" / "adata_qc.h5ad").write_text("newer local checkpoint")
    assert runtime._handoff_checkpoints(ws) == []
    (handoff / "adata_bad.h5ad").symlink_to(settings / "secret.h5ad")
    with pytest.raises(ValueError, match="regular files"):
        runtime._handoff_checkpoints(ws)


def test_changed_outputs_invalidate_completed_cache(settings):
    ws = runtime.workspace("cached")
    output = ws / "work" / "adata_qc.h5ad"
    output.write_text("first")
    task = ws / "jobs" / "task"
    task.mkdir()
    runtime._json(task / "outputs.json", runtime._outputs(ws))
    assert runtime._cache_valid(task)
    output.write_text("overwritten by a later step")
    assert not runtime._cache_valid(task)


def test_real_local_job_failure_is_visible(settings, monkeypatch):
    monkeypatch.setenv("AISCI_EXECUTOR", "local")
    submitted = runtime.submit("run_composition", {}, "missing-checkpoint")
    for _ in range(300):
        current = runtime.status("missing-checkpoint", submitted["task_id"])
        if current["state"] in runtime.TERMINAL:
            break
        time.sleep(0.1)
    assert current["state"] == "FAILED", current
    assert current["status"] == "error"
    assert "checkpoint" in current["error"]


def test_stdio_lists_tools_and_returns_errors(settings):
    server_cmd = shlex.join([sys.executable, "-m", "aiscientist_mcp.scrna_analysis.server"])
    command = [sys.executable, "-m", "aiscientist_mcp.common.client", "--server", server_cmd]
    listed = subprocess.run([*command, "tools"], env=os.environ, capture_output=True, text=True, check=True, timeout=30)
    assert len(json.loads(listed.stdout)) == 12
    bad = subprocess.run(
        [*command, "call", "run_scanpy_qc", '{"analysis_id":"../escape"}'],
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert bad.returncode != 0 and "invalid analysis_id" in bad.stderr


@pytest.mark.parametrize("spelling", ["adata_annotated.h5ad", "work/adata_annotated.h5ad"])
def test_a_handed_off_checkpoint_is_found_by_its_bare_name(settings, monkeypatch, spelling):
    """`input` documents a bare name as work/<name>. A checkpoint another analysis of the run wrote
    is not in this analysis's work/ yet: it must be found in the handoff under either spelling."""
    handoff = settings / "handoff"
    handoff.mkdir()
    source = handoff / "adata_annotated.h5ad"
    source.write_text("annotation output of another analysis")
    monkeypatch.setenv("SCRNA_CHECKPOINT_ROOT", str(handoff))
    ws = runtime.workspace("handoff-bare-name")
    assert runtime._input_identity(ws, "run_de", {"input": spelling}, "") == [runtime._identity(source)]
    with pytest.raises(ValueError, match=r"matches no \.h5ad file"):
        runtime._input_identity(ws, "run_de", {"input": "adata_missing.h5ad"}, "")


def test_upstream_qc_change_cannot_reuse_old_clustering(settings, monkeypatch):
    handoff = settings / "handoff"
    handoff.mkdir()
    source = handoff / "adata_qc.h5ad"
    source.write_text("CellQC first version")
    monkeypatch.setenv("SCRNA_CHECKPOINT_ROOT", str(handoff))
    ws = runtime.workspace("handoff-rerun")
    assert runtime._input_identity(ws, "run_clustering", {"input": "work/adata_qc.h5ad"}, "") == [
        runtime._identity(source)
    ]
    (ws / "work/adata_qc.h5ad").write_text("imported QC")
    (ws / "work/adata_clustered.h5ad").write_text("old clustering")
    runtime._json(ws / "checkpoint_imports.json", {source.name: runtime._identity(source)})
    assert runtime._handoff_checkpoints(ws) == []
    source.write_text("CellQC rerun, different cells")
    with pytest.raises(ValueError, match="upstream checkpoint changed"):
        runtime.submit("run_clustering", {}, "handoff-rerun")
    assert (ws / "work/adata_clustered.h5ad").read_text() == "old clustering"
