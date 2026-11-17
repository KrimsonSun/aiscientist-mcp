"""Attempt history, concurrent polling and filesystem boundaries of the runtime."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from aiscientist_mcp.scrna_analysis import runtime


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    monkeypatch.setenv("SCRNA_READ_ROOTS", str(tmp_path / "inputs"))
    for key in ("SCRNA_DATASET", "AISCIENTIST_DATASET", "SCRNA_CHECKPOINT_ROOT", "AISCI_JOB_PREFIX"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "inputs").mkdir()
    source = tmp_path / "inputs" / "source.h5ad"
    source.write_text("input")
    return source


def complete(task, status="ok"):
    state = "COMPLETED" if status == "ok" else "FAILED"
    runtime._json(task / "state.json", {"state": state, "executor": "inline"})
    runtime._json(task / "done.json", {"state": state})
    runtime._json(task / "result.json", {"status": status, "note": "original evidence"})
    runtime._json(task / "outputs.json", runtime._outputs(task.parent.parent))


@pytest.mark.parametrize("initial", ["ok", "invalid-input"])
def test_rerun_retains_success_and_failure_history(settings, monkeypatch, initial):
    tasks = []

    def run(cfg, command, task, name):
        tasks.append(task)
        complete(task, initial if len(tasks) == 1 else "ok")

    monkeypatch.setattr(runtime.jobs, "submit", run)
    if initial == "ok":
        first = runtime.submit("run_scanpy_qc", {}, "history", str(settings))
    else:
        with pytest.raises(ValueError, match="failed"):
            runtime.submit("run_scanpy_qc", {}, "history", str(settings))
        first = runtime.status("history", tasks[0].name)
    preserved = {p.name: p.read_bytes() for p in tasks[0].iterdir() if p.is_file() and p.name != "state.json"}
    again = runtime.submit("run_scanpy_qc", {}, "history", str(settings), rerun=initial == "ok")
    assert first["task_id"] != again["task_id"]
    old = runtime.status("history", first["task_id"])
    assert old["status"] == initial and old["note"] == "original evidence"
    assert old["state"] == ("COMPLETED" if initial == "ok" else "FAILED")
    assert preserved == {name: (tasks[0] / name).read_bytes() for name in preserved}


def test_poll_works_while_inline_submission_holds_lock(settings, monkeypatch):
    started, release = Event(), Event()
    tasks = []

    def run(cfg, command, task, name):
        tasks.append(task)
        runtime._json(task / "state.json", {"state": "RUNNING"})
        started.set()
        assert release.wait(10)
        complete(task)

    monkeypatch.setattr(runtime.jobs, "submit", run)
    with ThreadPoolExecutor(max_workers=9) as pool:
        submitted = pool.submit(runtime.submit, "run_scanpy_qc", {}, "concurrent", str(settings))
        assert started.wait(5)
        try:
            polls = list(pool.map(lambda _: runtime.status("concurrent", tasks[0].name), range(24)))
            assert all(value["state"] == "RUNNING" for value in polls)
            assert all("status" not in value for value in polls)
        finally:
            release.set()
        assert submitted.result(timeout=5)["state"] == "COMPLETED"


def test_concurrent_preemption_poll_submits_one_retry(settings, monkeypatch):
    monkeypatch.setenv("AISCI_EXECUTOR", "slurm")
    ws = runtime.workspace("preempt")
    task = ws / "jobs" / "attempt"
    task.mkdir()
    runtime._json(task / "request.json", {})
    runtime._json(task / "state.json", {"job_id": "old", "executor": "slurm", "cmd": ["python", "worker"]})
    runtime._json(task / "result.json", {"status": "ok", "stale": "previous allocation"})
    submissions = []

    def run(command, **kwargs):
        if command[0] == "sbatch":
            submissions.append(command)
            return SimpleNamespace(stdout="new\n")
        if command[0] == "squeue":
            return SimpleNamespace(stdout="PREEMPTED\n" if command[2] == "old" else "PENDING\n")
        raise AssertionError(command)

    monkeypatch.setattr(runtime.jobs.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: runtime.status("preempt", "attempt"), range(24)))
    assert len(submissions) == 1
    assert all(result["state"] == "PENDING" and result["job_id"] == "new" for result in results)
    assert all("stale" not in result and "status" not in result for result in results)


def test_new_output_and_same_mtime_input_replacement_invalidate_cache(settings, monkeypatch):
    monkeypatch.setattr(runtime.jobs, "submit", lambda cfg, command, task, name: complete(task))
    first = runtime.submit("run_scanpy_qc", {}, "inventory", str(settings))
    ws = runtime.workspace("inventory")
    (ws / "work" / "adata_qc.h5ad").write_text("new checkpoint")
    second = runtime.submit("run_scanpy_qc", {}, "inventory", str(settings))
    assert first["task_id"] != second["task_id"]
    identity = runtime._identity(settings)
    replacement = settings.with_name("replacement.h5ad")
    replacement.write_bytes(settings.read_bytes())
    os.utime(replacement, ns=(settings.stat().st_atime_ns, settings.stat().st_mtime_ns))
    replacement.replace(settings)
    assert runtime._identity(settings) != identity
    third = runtime.submit("run_scanpy_qc", {}, "inventory", str(settings))
    assert third["task_id"] != second["task_id"]


@pytest.mark.parametrize("handoff", [False, True])
def test_successful_in_place_checkpoint_and_handoff_are_reusable(settings, monkeypatch, handoff):
    ws = runtime.workspace("in-place")
    if handoff:
        upstream = settings.parent / "upstream"
        upstream.mkdir()
        (upstream / "adata_qc.h5ad").write_text("external checkpoint")
        monkeypatch.setenv("SCRNA_CHECKPOINT_ROOT", str(upstream))
    else:
        (ws / "work" / "adata_qc.h5ad").write_text("local checkpoint")
    count = []

    def science(args, ctx):
        count.append(1)
        (ctx.workspace / "work" / "adata_qc.h5ad").write_text("updated checkpoint")
        return {"status": "ok", "checkpoint": "adata_qc.h5ad"}

    monkeypatch.setattr(runtime.importlib, "import_module", lambda name: SimpleNamespace(run_doublet_detection=science))

    def run(cfg, command, task, name):
        runtime.execute("in-place", task.name)
        runtime._json(task / "state.json", {"state": "COMPLETED"})
        runtime._json(task / "done.json", {"state": "COMPLETED"})

    monkeypatch.setattr(runtime.jobs, "submit", run)
    first = runtime.submit("run_doublet_detection", {}, "in-place")
    second = runtime.submit("run_doublet_detection", {}, "in-place")
    assert first["task_id"] == second["task_id"] and len(count) == 1


@pytest.mark.parametrize(
    "name", ["dataset.json", "active.json", "cache.json", "checkpoint_imports.json", ".submit.lock"]
)
def test_workspace_metadata_symlinks_refused(settings, name):
    ws = runtime.workspace("links")
    target = settings.parent / "outside.json"
    target.write_text('{"secret": true}')
    (ws / name).symlink_to(target)
    with pytest.raises(ValueError, match=r"symlink|regular file"):
        runtime.submit("run_scanpy_qc", {}, "links", str(settings))
    assert target.read_text() == '{"secret": true}'


@pytest.mark.parametrize(
    "name", ["request.json", "result.json", "outputs.json", "state.json", "done.json", ".status.lock"]
)
def test_task_metadata_symlinks_refused_after_workspace_check(settings, name):
    ws = runtime.workspace("task-links")
    task = ws / "jobs" / "attempt"
    task.mkdir()
    runtime._json(task / "request.json", {})
    complete(task)
    target = settings.parent / "outside.json"
    target.write_text("{}")
    (task / name).unlink(missing_ok=True)
    (task / name).symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        if name == "outputs.json":
            runtime._cache_valid(task)
        else:
            runtime._response(ws, "task-links", "attempt")
    assert target.read_text() == "{}"


def test_configured_data_root_symlink_refused(settings, monkeypatch):
    alias = settings.parent / "alias"
    alias.symlink_to(settings.parent, target_is_directory=True)
    monkeypatch.setenv("AISCI_DATA_DIR", str(alias))
    with pytest.raises(ValueError, match="symlink"):
        runtime.workspace("bad-root")


@pytest.mark.parametrize("field,value", [("version", "obsolete"), ("analysis_id", "other"), ("genesets", "/changed")])
def test_worker_rejects_changed_configuration(settings, monkeypatch, field, value):
    monkeypatch.setattr(
        runtime.jobs,
        "submit",
        lambda cfg, command, task, name: runtime._json(task / "state.json", {"state": "PENDING"}),
    )
    submitted = runtime.submit("run_scanpy_qc", {}, "config", str(settings))
    task = runtime.workspace("config") / "jobs" / submitted["task_id"]
    request = runtime._read(task / "request.json")
    runtime._json(task / "request.json", request | {field: value})
    with pytest.raises(ValueError, match=r"configuration|another analysis"):
        runtime.execute("config", task.name)
    assert not (task / "result.json").exists()


def test_missing_result_and_submission_failure_keep_task_context(settings, monkeypatch):
    def fail(cfg, command, task, name):
        raise OSError("sbatch unavailable")

    monkeypatch.setattr(runtime.jobs, "submit", fail)
    with pytest.raises(OSError, match="sbatch"):
        runtime.submit("run_scanpy_qc", {}, "failure", str(settings))
    ws = runtime.workspace("failure")
    task_id = runtime._read(ws / "active.json")["task_id"]
    failed = runtime.status("failure", task_id)
    assert failed["state"] == "FAILED" and failed["status"] == "error"
    assert failed["task_id"] == task_id and "submit" in failed["error"]
    monkeypatch.setattr(runtime.jobs, "submit", lambda cfg, command, task, name: complete(task))
    second = runtime.submit("run_scanpy_qc", {}, "failure", str(settings))
    assert second["task_id"] != task_id
    (ws / "jobs" / second["task_id"] / "result.json").unlink()
    missing = runtime.status("failure", second["task_id"])
    assert missing["state"] == "FAILED" and missing["status"] == "error"
    assert "without result" in missing["error"]


def test_atomic_writes_do_not_share_temporary_files(settings):
    path = runtime.workspace("atomic") / "metadata.json"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda value: runtime._json(path, {"sequence": value, "payload": "x" * 10000}), range(100)))
    result = json.loads(path.read_text())
    assert result["sequence"] in range(100) and len(result["payload"]) == 10000
    assert list(path.parent.glob(".metadata.json.*")) == []


@pytest.mark.parametrize("terminal", sorted(runtime.TERMINAL - {"COMPLETED"}))
def test_terminal_executor_failure_cannot_report_success(settings, terminal):
    ws = runtime.workspace("terminal")
    task = ws / "jobs" / "attempt"
    task.mkdir()
    runtime._json(task / "request.json", {})
    runtime._json(task / "state.json", {"state": terminal})
    runtime._json(task / "result.json", {"status": "ok", "partial": "saved before termination"})
    result = runtime.status("terminal", "attempt")
    assert result["state"] == terminal and result["status"] == "error"
    assert result["partial"] == "saved before termination"


def test_output_root_link_is_refused_after_workspace_check(settings):
    ws = runtime.workspace("output-root")
    (ws / "work").rmdir()
    (ws / "work").symlink_to(settings.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        runtime._outputs(ws)
