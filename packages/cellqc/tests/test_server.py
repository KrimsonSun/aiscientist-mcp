"""Real MCP/worker transport with the upstream synthetic CellQC executable fixture."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from aiscientist_mcp.cellqc import runtime
from aiscientist_mcp.cellqc.server import run_cellqc, server
from aiscientist_mcp.cellqc.settings import defaults_source, load_defaults


@pytest.fixture
def configured(tmp_path, delivery, monkeypatch):
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CELLQC_DATASET", str(delivery))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    monkeypatch.delenv("CELLQC_READ_ROOTS", raising=False)
    monkeypatch.delenv("AISCIENTIST_DATASET", raising=False)
    return delivery


def test_mcp_inline_result_reuse_input_change_and_forced_rerun(configured, fake_cellqc):
    from mcp import Client

    async def flow():
        async with Client(server, raise_exceptions=True) as client:
            schema = {t.name: t.input_schema for t in (await client.list_tools()).tools}
            assert set(schema) == {"run_cellqc", "cellqc_job_status"}
            props = schema["run_cellqc"]["properties"]
            assert props["species"]["enum"] == ["auto", "human", "mouse", "macaque"]
            assert props["nreaction"]["exclusiveMinimum"] == 0
            assert props["max_pct_mt"]["maximum"] == 100
            for name, value in load_defaults()["thresholds"].items():
                assert props[name]["default"] == value
            args = {"analysis_id": "cohort", "max_pct_mt": 5.0}
            first = (await client.call_tool("run_cellqc", args)).structured_content
            assert first["state"] == "COMPLETED", first
            assert first["status"] == "ok" and first["cells_after"] == 90
            assert first["settings_source"] == defaults_source()
            assert Path(first["checkpoint_path"]).is_file()
            assert (Path(first["artifact_root"]) / "result/report.html").is_file()
            status = (
                await client.call_tool("cellqc_job_status", {"analysis_id": "cohort", "task_id": first["task_id"]})
            ).structured_content
            assert status == first
            again = (await client.call_tool("run_cellqc", args)).structured_content
            assert again["task_id"] == first["task_id"]
            assert len(fake_cellqc.read_text().splitlines()) == 2
            # A changed delivery must invalidate reuse and keep the earlier evidence intact.
            summary = configured / "S1/web_summary.html"
            summary.write_text(summary.read_text() + "\n")
            changed = (await client.call_tool("run_cellqc", args)).structured_content
            assert changed["state"] == "COMPLETED"
            assert changed["task_id"] != first["task_id"]
            assert changed["checkpoint_path"] != first["checkpoint_path"]
            assert Path(first["checkpoint_path"]).is_file()
            forced = (await client.call_tool("run_cellqc", args | {"rerun": True})).structured_content
            assert forced["task_id"] != changed["task_id"]
            assert forced["state"] == "COMPLETED"
            repeated = (await client.call_tool("run_cellqc", args)).structured_content
            assert repeated["task_id"] == forced["task_id"]
            assert len(fake_cellqc.read_text().splitlines()) == 6

    asyncio.run(flow())


@pytest.mark.parametrize(
    "bad",
    [
        {"species": "rat"},
        {"chemistry": "unrecognized"},
        {"nreaction": 0},
        {"min_counts": -1},
        {"max_pct_mt": 101},
        {"max_pct_mt": float("nan")},
        {"warn_ambient_frac": 2},
        {"analysis_id": "../escape"},
        {"analysis_id": "."},
        {"ambient_method": "shell"},
        {"doublet_decider": "unknown"},
    ],
)
def test_invalid_inputs_never_submit(configured, monkeypatch, bad):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid input reached job submission")

    monkeypatch.setattr(runtime.jobs, "submit", forbidden)
    with pytest.raises((ValueError, TypeError)):
        run_cellqc(**bad)


def test_unbound_paths_and_nested_symlinks_are_refused(configured, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="outside"):
        run_cellqc(dataset=str(outside))
    (configured / "leak").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run_cellqc()
    (configured / "leak").unlink()
    monkeypatch.delenv("CELLQC_DATASET")
    monkeypatch.setenv("CELLQC_READ_ROOTS", str(tmp_path))
    assert runtime.bound_folders(str(configured))[0]["path"] == str(configured)


def test_workspace_symlink_and_concurrent_submission_are_refused(configured, tmp_path):
    ws = runtime.workspace("locked")
    with runtime.lock(ws / ".submit.lock"), pytest.raises(ValueError, match="active"):
        run_cellqc(analysis_id="locked")
    (ws.parent / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        run_cellqc(analysis_id="escape")


def test_pending_task_reuses_only_identical_request(configured, monkeypatch):
    def pending(cfg, cmd, directory, name, extra):
        state = {"state": "PENDING", "executor": "slurm", **extra}
        runtime.jobs.write_state(directory, state)
        return state

    monkeypatch.setattr(runtime.jobs, "submit", pending)
    first = run_cellqc()
    assert run_cellqc()["task_id"] == first["task_id"]
    with pytest.raises(ValueError, match="active"):
        run_cellqc(min_counts=501)
    with pytest.raises(ValueError, match="active"):
        run_cellqc(rerun=True)


def test_failed_inline_job_reports_actual_failure(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "no-cellqc"))
    result = run_cellqc()
    assert result["state"] == "FAILED"
    assert result["status"] == "error"
    assert "CellQC executable is missing" in result["error"]
    assert "checkpoint_path" not in result


def test_stdio_lists_native_tools():
    command = [
        sys.executable,
        "-m",
        "aiscientist_mcp.common.client",
        "--server",
        f"{sys.executable} -m aiscientist_mcp.cellqc.server",
        "tools",
    ]
    proc = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30, env=os.environ.copy())
    tools = json.loads(proc.stdout)
    assert {entry["name"] for entry in tools} == {"run_cellqc", "cellqc_job_status"}
