import json
import os
import sys
import time

import pytest
from aiscientist_mcp.common import jobs, paths


def test_safe_id():
    assert paths.safe_id("HRCA__LRAT") == "HRCA__LRAT"
    for bad in ("../etc", "a/b", "", "x" * 200):
        with pytest.raises(ValueError):
            paths.safe_id(bad)


def test_local_job_records_done(tmp_path, monkeypatch):
    monkeypatch.setenv("AISCI_EXECUTOR", "local")
    cfg = jobs.JobSettings.from_env()
    code = "from aiscientist_mcp.common import jobs; import sys; jobs.record_done(sys.argv[1], lambda: None)"
    st = jobs.submit(cfg, [sys.executable, "-c", code, str(tmp_path / "j")], tmp_path / "j", "t")
    assert st["state"] == "RUNNING"
    for _ in range(60):
        st = jobs.status(cfg, tmp_path / "j")
        if st["state"] != "RUNNING":
            break
        time.sleep(0.5)
    assert st["state"] == "COMPLETED"
    assert json.loads((tmp_path / "j" / "done.json").read_text())["state"] == "COMPLETED"


def test_job_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("AISCI_EXECUTOR", "local")
    monkeypatch.setenv("AISCI_JOB_PREFIX", "env FOO=1")
    cfg = jobs.JobSettings.from_env()
    st = jobs.submit(cfg, [sys.executable, "-c", "pass"], tmp_path / "k", "t")
    assert st["cmd"][:2] == ["env", "FOO=1"]
    assert os.path.exists(tmp_path / "k" / "state.json")


def test_inline_job_blocks_until_done(tmp_path, monkeypatch):
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    cfg = jobs.JobSettings.from_env()
    code = "from aiscientist_mcp.common import jobs; import sys; jobs.record_done(sys.argv[1], lambda: None)"
    st = jobs.submit(cfg, [sys.executable, "-c", code, str(tmp_path / "j")], tmp_path / "j", "t")
    assert st["state"] == "COMPLETED"
    assert jobs.status(cfg, tmp_path / "j")["state"] == "COMPLETED"
    st = jobs.submit(cfg, [sys.executable, "-c", "raise SystemExit(3)"], tmp_path / "f", "t")
    assert st["state"] == "FAILED" and st["error"] == "exit 3"


def test_lost_local_job_is_failed(tmp_path, monkeypatch):
    """A recorded pid that now belongs to another process (pid reuse, another PID namespace) is not the job."""
    monkeypatch.setenv("AISCI_EXECUTOR", "local")
    cfg = jobs.JobSettings.from_env()
    st = jobs.submit(cfg, [sys.executable, "-c", "pass"], tmp_path / "j", "t")
    st["proc"] = st["proc"] | {"pid": os.getpid(), "start": "0"}
    jobs.write_state(tmp_path / "j", st)
    st = jobs.status(cfg, tmp_path / "j")
    assert st["state"] == "FAILED" and "done.json" in st["note"]


def test_unknown_executor(monkeypatch):
    monkeypatch.setenv("AISCI_EXECUTOR", "sge")
    with pytest.raises(ValueError):
        jobs.JobSettings.from_env()
