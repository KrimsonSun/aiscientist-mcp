"""Real worker processes exercise task reuse, failures, confinement and async status."""

import os
import signal
import subprocess
import sys
import time

import pytest
from aiscientist_mcp.common import tasks


@pytest.fixture
def config(tmp_path, monkeypatch):
    worker = tmp_path / "fixture_worker.py"
    worker.write_text("""
import os
import time
from aiscientist_mcp.common.tasks import TaskConfig, worker_main
cfg = TaskConfig("fixture", "fixture_worker", "0.0.1")
def execute(request, workspace):
    if request["operation"] == "abrupt":
        os._exit(7)
    if request["operation"] == "wait":
        while not (workspace / "release").exists():
            time.sleep(0.02)
    if request["operation"] == "fail":
        return {"status": "error", "error": "scientific validation failed"}
    counter = workspace / "counter"
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    return {"status": "ok", "count": count, "arguments": request["arguments"]}
if __name__ == "__main__":
    worker_main(cfg, execute)
""")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    return tasks.TaskConfig("fixture", "fixture_worker", "0.0.1")


def test_inline_reuses_only_current_identical_completed_result(config):
    first = tasks.submit(config, "analysis", "count", {"threshold": 3})
    assert first["status"] == "ok" and first["state"] == "COMPLETED" and first["count"] == 1
    assert tasks.submit(config, "analysis", "count", {"threshold": 3}) == first
    changed = tasks.submit(config, "analysis", "count", {"threshold": 4})
    assert changed["count"] == 2 and changed["task_id"] != first["task_id"]
    assert tasks.status(config, "analysis", first["task_id"])["count"] == 1
    assert tasks.submit(config, "analysis", "count", {"threshold": 3})["count"] == 3


def test_input_change_and_rerun_invalidate_cache(config, tmp_path):
    source = tmp_path / "input.txt"
    source.write_text("before")
    first = tasks.submit(config, "analysis", "count", {}, inputs=[str(source)])
    source.write_text("after; different content")
    second = tasks.submit(config, "analysis", "count", {}, inputs=[str(source)])
    third = tasks.submit(config, "analysis", "count", {}, inputs=[str(source)], rerun=True)
    assert [first["count"], second["count"], third["count"]] == [1, 2, 3]


def test_failed_result_is_failed_job(config):
    result = tasks.submit(config, "analysis", "fail", {})
    assert result["state"] == "FAILED" and result["status"] == "error"
    assert "scientific validation failed" in result["error"]


def test_missing_result_is_not_reused_as_success(config):
    first = tasks.submit(config, "analysis", "count", {})
    directory = tasks.task_dir(tasks.workspace(config, "analysis"), first["task_id"])
    (directory / "result.json").unlink()
    assert tasks.status(config, "analysis", first["task_id"])["state"] == "FAILED"
    rerun = tasks.submit(config, "analysis", "count", {})
    assert rerun["status"] == "ok" and rerun["count"] == 2


def test_local_job_reuses_active_request_and_serializes_workspace(config, monkeypatch):
    monkeypatch.setenv("AISCI_EXECUTOR", "local")
    first = tasks.submit(config, "analysis", "wait", {})
    try:
        assert tasks.submit(config, "analysis", "wait", {})["task_id"] == first["task_id"]
        with pytest.raises(ValueError, match="another task"):
            tasks.submit(config, "analysis", "count", {})
    finally:
        (tasks.workspace(config, "analysis") / "release").touch()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = tasks.status(config, "analysis", first["task_id"])
        if result["state"] not in tasks.ACTIVE:
            break
        time.sleep(0.05)
    assert result["state"] == "COMPLETED" and result["status"] == "ok"


def test_workspace_and_input_symlink_escape(config, tmp_path):
    for name in (".", "..", "../escape", "/tmp/out"):
        with pytest.raises(ValueError):
            tasks.workspace(config, name)
    ws = tasks.workspace(config, "ok")
    (ws.parent / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        tasks.workspace(config, "escape")
    outside = tmp_path / "private.txt"
    outside.write_text("unrelated file")
    (ws / "input").symlink_to(outside)
    with pytest.raises(ValueError, match="read roots"):
        tasks.read_path(str(ws / "input"), [ws])
    with pytest.raises(ValueError, match="unknown task"):
        tasks.status(config, "ok", "missing")


@pytest.mark.parametrize("filename", ["result.json", "state.json", "done.json"])
def test_status_rejects_metadata_symlinks(config, tmp_path, filename):
    first = tasks.submit(config, "analysis", "count", {})
    directory = tasks.task_dir(tasks.workspace(config, "analysis"), first["task_id"])
    outside = tmp_path / "private.json"
    outside.write_text('{"private": "unrelated data"}')
    (directory / filename).unlink()
    (directory / filename).symlink_to(outside)
    with pytest.raises(ValueError, match="regular file"):
        tasks.status(config, "analysis", first["task_id"])
    assert outside.read_text() == '{"private": "unrelated data"}'


def test_submission_rejects_active_metadata_symlink(config, tmp_path):
    ws = tasks.workspace(config, "analysis")
    outside = tmp_path / "private.json"
    outside.write_text("{}")
    (ws / "active.json").symlink_to(outside)
    with pytest.raises(ValueError, match="regular file"):
        tasks.submit(config, "analysis", "count", {})


def test_local_abrupt_exit_releases_workspace(config, monkeypatch):
    monkeypatch.setenv("AISCI_EXECUTOR", "local")
    first = tasks.submit(config, "analysis", "abrupt", {})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = tasks.status(config, "analysis", first["task_id"])
        if result["state"] not in tasks.ACTIVE:
            break
        time.sleep(0.05)
    assert result["state"] == "FAILED" and result["status"] == "error"
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    assert tasks.submit(config, "analysis", "count", {})["state"] == "COMPLETED"


def test_killed_inline_caller_does_not_stick_running(config):
    code = (
        "from aiscientist_mcp.common.tasks import TaskConfig, submit; "
        'submit(TaskConfig("fixture", "fixture_worker", "0.0.1"), "analysis", "wait", {})'
    )
    process = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    ws = tasks.workspace(config, "analysis")
    try:
        deadline = time.monotonic() + 10
        states = []
        while time.monotonic() < deadline:
            states = list((ws / "jobs").glob("*/state.json"))
            if states:
                break
            time.sleep(0.02)
        assert states, "inline job did not publish its owner"
    finally:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    result = tasks.status(config, "analysis", states[0].parent.name)
    assert result["state"] == "FAILED"
    assert tasks.submit(config, "analysis", "count", {})["state"] == "COMPLETED"


@pytest.mark.parametrize("state", sorted(tasks.ACTIVE))
def test_active_state_never_reads_stale_or_partial_result(config, state):
    directory = tasks.task_dir(tasks.workspace(config, "analysis"), "retry")
    directory.mkdir(parents=True)
    tasks.jobs.write_state(directory, {"state": state, "executor": "fixture"})
    (directory / "result.json").write_text('{"status":"ok","old_science":')
    result = tasks.status(config, "analysis", "retry")
    assert result["state"] == state
    assert not {"status", "old_science"}.intersection(result)


@pytest.mark.parametrize(
    "state",
    [
        "FAILED",
        "CANCELLED",
        "TIMEOUT",
        "OUT_OF_MEMORY",
        "BOOT_FAIL",
        "DEADLINE",
        "REVOKED",
        "SPECIAL_EXIT",
    ],
)
@pytest.mark.parametrize("scientific_status", [None, "ok", "empty"])
def test_every_noncompleted_terminal_state_is_not_success(config, state, scientific_status):
    directory = tasks.task_dir(tasks.workspace(config, "analysis"), "terminated")
    directory.mkdir(parents=True)
    tasks.jobs.write_state(directory, {"state": state, "executor": "fixture"})
    payload = {"n_rows": 8, "status": scientific_status} if scientific_status else {"n_rows": 8}
    tasks.jobs.write_json(directory, "result.json", payload)
    result = tasks.status(config, "analysis", "terminated")
    assert result["state"] == state and result["status"] == "error"
    assert result["n_rows"] == 8  # Complete partial evidence remains available for inspection.


def test_missing_executor_state_cannot_inherit_scientific_success(config):
    directory = tasks.task_dir(tasks.workspace(config, "analysis"), "unknown")
    directory.mkdir(parents=True)
    tasks.jobs.write_state(directory, {})
    tasks.jobs.write_json(directory, "result.json", {"status": "ok", "state": "COMPLETED"})
    assert tasks.status(config, "analysis", "unknown") == {
        "state": "UNKNOWN",
        "analysis_id": "analysis",
        "task_id": "unknown",
    }


def test_terminal_preserves_explicit_domain_failure_reason(config):
    directory = tasks.task_dir(tasks.workspace(config, "analysis"), "disabled")
    directory.mkdir(parents=True)
    tasks.jobs.write_state(directory, {"state": "CANCELLED", "executor": "fixture"})
    tasks.jobs.write_json(directory, "result.json", {"status": "not_enabled", "note": "reference unavailable"})
    result = tasks.status(config, "analysis", "disabled")
    assert result["status"] == "not_enabled" and result["note"] == "reference unavailable"


def test_slurm_retry_preserves_history_but_hides_previous_payload(config, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("AISCI_EXECUTOR", "slurm")
    monkeypatch.setenv("AISCI_SLURM_FALLBACK", "--partition=standard")
    directory = tasks.task_dir(tasks.workspace(config, "analysis"), "preempted")
    directory.mkdir(parents=True)
    tasks.jobs.write_state(
        directory,
        {
            "state": "RUNNING",
            "executor": "slurm",
            "job_id": "101",
            "cmd": [sys.executable, "-c", "pass"],
            "jobs": [{"job_id": "101", "slurm_opts": "--partition=free"}],
        },
    )
    tasks.jobs.write_json(directory, "result.json", {"status": "ok", "n_rows": 99})
    monkeypatch.setattr(tasks.jobs, "_slurm_state", lambda jid: "PREEMPTED" if jid == "101" else "PENDING")
    submitted = []

    def sbatch(command, **kwargs):
        assert command[0] == "sbatch"
        submitted.append(command)
        return SimpleNamespace(stdout="102\n")

    monkeypatch.setattr(tasks.jobs.subprocess, "run", sbatch)
    for _ in range(2):
        result = tasks.status(config, "analysis", "preempted")
        assert result["state"] == "PENDING" and result["job_id"] == "102"
        assert not {"status", "n_rows"}.intersection(result)
    assert len(submitted) == 1
    saved = tasks.jobs.read_state(directory)
    assert [row["job_id"] for row in saved["jobs"]] == ["101", "102"]
    assert saved["slurm_opts"] == "--partition=standard"
