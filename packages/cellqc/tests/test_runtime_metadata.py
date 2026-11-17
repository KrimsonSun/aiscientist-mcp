"""CellQC-specific metadata publication and confinement across polling and worker execution."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from aiscientist_mcp.cellqc import __version__, core, runtime, worker
from aiscientist_mcp.cellqc.settings import defaults_source
from aiscientist_mcp.common import jobs


@pytest.fixture
def task(tmp_path, delivery, monkeypatch):
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CELLQC_DATASET", str(delivery))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    ws = runtime.workspace("cohort")
    directory = runtime.task_dir(ws, "qc-example")
    directory.mkdir()
    folders = runtime.bound_folders()
    jobs.write_json(
        directory,
        "request.json",
        {
            "analysis_id": ws.name,
            "folders": folders,
            "inputs": runtime.input_identity(folders),
            "arguments": {},
            "settings": defaults_source(),
            "version": __version__,
        },
    )
    jobs.write_state(directory, {"state": "RUNNING", "executor": "test"})
    return ws, directory


@pytest.mark.parametrize("name", ["state.json", "done.json", "result.json"])
def test_poll_refuses_linked_metadata(task, tmp_path, name):
    ws, directory = task
    outside = tmp_path / "outside.json"
    outside.write_text('{"state":"COMPLETED","private":"not a result"}')
    target = directory / name
    target.unlink(missing_ok=True)
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="regular file"):
        runtime.result(ws, directory.name)
    assert json.loads(outside.read_text())["private"] == "not a result"


@pytest.mark.parametrize("name", ["active.json", ".submit.lock"])
def test_submission_refuses_linked_analysis_metadata(task, tmp_path, name):
    ws, _ = task
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (ws / name).symlink_to(outside)
    with pytest.raises(ValueError, match="regular file"):
        runtime.submit(ws.name, "", None, {}, False)
    assert outside.read_text() == "{}"


@pytest.mark.parametrize("where", ["request", "result", "work_lock"])
def test_worker_refuses_linked_metadata(task, tmp_path, monkeypatch, where):
    ws, directory = task
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    target = ws / ".work.lock" if where == "work_lock" else directory / (where + ".json")
    target.unlink(missing_ok=True)
    target.symlink_to(outside)
    monkeypatch.setattr(core, "run_cellqc", lambda *args: {"status": "error", "error": "fixture"})
    with pytest.raises(ValueError, match="regular file"):
        worker.execute(directory)
    assert outside.read_text() == "{}"


def test_workspace_and_jobs_parent_links_are_refused(task, tmp_path, monkeypatch):
    ws, directory = task
    moved = ws / "actual-jobs"
    directory.parent.rename(moved)
    (ws / "jobs").symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        runtime.result(ws, directory.name)
    data = tmp_path / "other-data"
    data.mkdir()
    (data / "cellqc").symlink_to(ws.parent, target_is_directory=True)
    monkeypatch.setenv("AISCI_DATA_DIR", str(data))
    with pytest.raises(ValueError, match="escapes"):
        runtime.workspace("cohort")


def test_worker_rejects_outside_directory_before_reading_request(task, tmp_path):
    outside = tmp_path / "elsewhere/jobs/qc-other"
    outside.mkdir(parents=True)
    (outside / "request.json").write_text("not JSON")
    with pytest.raises(ValueError, match="outside"):
        worker.execute(outside)
    assert not (outside / "done.json").exists()


def test_completed_without_result_is_failure(task):
    ws, directory = task
    jobs.write_json(directory, "done.json", {"state": "COMPLETED"})
    result = runtime.result(ws, directory.name)
    assert result["state"] == "FAILED" and result["status"] == "error"
    assert "missing" in result["error"]


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY"])
def test_failed_job_never_reuses_success_status(task, state):
    ws, directory = task
    jobs.write_json(directory, "result.json", {"status": "ok", "cells_after": 9})
    jobs.write_state(directory, {"state": state, "executor": "test"})
    result = runtime.result(ws, directory.name)
    assert result["state"] == state and result["status"] == "error"


def test_poll_during_real_worker_publication_never_reads_partial_json(task, monkeypatch):
    ws, directory = task
    output = {"status": "error", "error": "diagnostic failure", "details": "x" * 100000}
    monkeypatch.setattr(core, "run_cellqc", lambda *args: output)
    started, resume = threading.Event(), threading.Event()
    real_dump = json.dump

    def paused_dump(value, stream, *args, **kwargs):
        if Path(stream.name).name.startswith(".result.json."):
            stream.write('{"partially_written":')
            stream.flush()
            started.set()
            assert resume.wait(10)
            stream.seek(0)
            stream.truncate()
        return real_dump(value, stream, *args, **kwargs)

    monkeypatch.setattr(jobs.json, "dump", paused_dump)
    with ThreadPoolExecutor(max_workers=5) as pool:
        future = pool.submit(worker.execute, directory)
        assert started.wait(10)
        try:
            polls = [pool.submit(runtime.result, ws, directory.name) for _ in range(16)]
            for poll in polls:
                result = poll.result(timeout=10)
                assert result["state"] == "RUNNING"
                assert "partially_written" not in result and "details" not in result
        finally:
            resume.set()
        with pytest.raises(RuntimeError, match="diagnostic failure"):
            future.result(timeout=10)
    assert json.loads((directory / "result.json").read_text()) == output
    assert not list(directory.glob(".result.json.*"))


@pytest.mark.parametrize("override", [None, "/opt/science/bin/python"])
def test_prefix_uses_image_python_not_host_virtualenv(task, monkeypatch, override):
    ws, _ = task
    monkeypatch.setenv("AISCI_JOB_PREFIX", "apptainer exec --bind /data /image.sif")
    if override:
        monkeypatch.setenv("AISCI_JOB_PYTHON", override)
    else:
        monkeypatch.delenv("AISCI_JOB_PYTHON", raising=False)
    commands = []

    def pending(cfg, cmd, directory, name, extra):
        commands.append(cmd)
        jobs.write_state(directory, {"state": "PENDING", "executor": "test"})

    monkeypatch.setattr(jobs, "submit", pending)
    runtime.submit(ws.name, "", None, {}, False)
    assert commands[0][0] == (override or "python")


@pytest.mark.parametrize("state", ["PENDING", "RUNNING", "REQUEUED", "UNKNOWN"])
def test_retry_does_not_expose_prior_result(task, state):
    ws, directory = task
    jobs.write_json(directory, "result.json", {"status": "error", "cells_after": 123, "error": "old attempt"})
    jobs.write_state(directory, {"state": state, "executor": "test"})
    result = runtime.result(ws, directory.name)
    assert result["state"] == state
    assert not {"status", "cells_after", "error"}.intersection(result)


@pytest.mark.parametrize("key,value", [("version", "0.0.0"), ("settings", {"sha256": "changed"})])
def test_changed_package_or_defaults_refuse_before_calculation(task, monkeypatch, key, value):
    _, directory = task
    request = json.loads((directory / "request.json").read_text())
    request[key] = value
    jobs.write_json(directory, "request.json", request)
    monkeypatch.setattr(core, "run_cellqc", lambda *args: pytest.fail("stale request reached calculation"))
    with pytest.raises(ValueError, match="changed after submission"):
        worker.execute(directory)


@pytest.mark.parametrize("relative", [".", "work", "artifacts", "work/cellqc", "artifacts/figures"])
def test_output_link_refused_before_scientific_write(task, tmp_path, monkeypatch, relative):
    _, directory = task
    outside = tmp_path / "outside-output"
    outside.mkdir()
    target = directory / "output" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(core, "run_cellqc", lambda *args: pytest.fail("redirected output reached calculation"))
    with pytest.raises(ValueError, match="symlink"):
        worker.execute(directory)
    assert not list(outside.iterdir())


def test_retry_accepts_only_real_staged_input_and_internal_fontconfig_links(task, tmp_path):
    _, directory = task
    request = json.loads((directory / "request.json").read_text())
    output = directory / "output"
    staged = output / "work/cellqc/lnfiles/S1"
    staged.mkdir(parents=True)
    source = Path(request["folders"][0]["path"]) / "S1/raw_feature_bc_matrix.h5"
    (staged / source.name).symlink_to(source)
    cache = output / "work/cellqc/mouse/cache/fontconfig"
    cache.mkdir(parents=True)
    (cache / "font.cache-12").write_text("cache")
    (cache / "font.cache-9").symlink_to("font.cache-12")
    worker.checked_output(output, request["folders"])
    (staged / source.name).unlink()
    (staged / source.name).symlink_to(tmp_path / "outside")
    (tmp_path / "outside").write_text("not admitted")
    with pytest.raises(ValueError, match="permitted"):
        worker.checked_output(output, request["folders"])
