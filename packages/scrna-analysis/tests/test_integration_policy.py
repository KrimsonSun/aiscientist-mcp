"""Installed optional packages cannot silently change the deployed auto method."""

from types import SimpleNamespace

import pytest
from aiscientist_mcp.scrna_analysis import runtime


@pytest.mark.parametrize(
    "method,effective", [(None, "combat"), ("auto", "combat"), ("combat", "combat"), ("harmony", "harmony")]
)
def test_worker_policy_preserves_requested_arguments_and_reports_actual_method(
    tmp_path, monkeypatch, method, effective
):
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    for name in ("SCRNA_DATASET", "AISCIENTIST_DATASET", "SCRNA_CHECKPOINT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    ws = runtime.workspace("policy")
    (ws / "work/adata_qc.h5ad").write_text("fixture admitted by the test scientific function")
    requested = {"batch_key": "donor"} | ({"method": method} if method is not None else {})
    observed = []

    def integration(args, ctx):
        observed.append(dict(args))
        assert args["method"] == effective
        return {"status": "ok", "method_used": effective}

    original = runtime.importlib.import_module
    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(run_integration=integration) if name.endswith("run_integration.tool") else original(name)
        ),
    )

    def inline(cfg, command, task, name):
        runtime.execute("policy", task.name)
        runtime._json(task / "state.json", {"executor": "inline", "state": "COMPLETED"})
        runtime._json(task / "done.json", {"state": "COMPLETED"})

    monkeypatch.setattr(runtime.jobs, "submit", inline)
    result = runtime.submit("run_integration", requested, "policy")
    assert result["method_used"] == effective
    policy = result["integration_policy"]
    assert policy["requested_method"] == (method or "auto")
    assert policy["method_argument_supplied"] == (method is not None)
    assert policy["effective_method"] == effective
    request = runtime._read(ws / "jobs" / result["task_id"] / "request.json")
    assert request["args"] == requested  # The caller's request and cache key are never rewritten.
    assert request["integration_policy"] == runtime.INTEGRATION_POLICY
    assert observed == [requested | {"method": effective}]
