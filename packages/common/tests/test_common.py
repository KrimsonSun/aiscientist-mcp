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
