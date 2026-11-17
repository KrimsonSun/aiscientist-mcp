"""Native MCP wire size includes both structured and pretty text representations."""

import asyncio
import copy
import json
from typing import Any

from aiscientist_mcp.common.response import bounded_result
from aiscientist_mcp.common.server import make_server
from mcp import Client


def test_small_result_is_unchanged():
    result = {"status": "ok", "state": "COMPLETED", "n_candidates": 1}
    assert bounded_result(result, full_result="/data/result.json") is result


def test_wire_bound_and_complete_result_are_preserved(tmp_path):
    result = {
        "status": "ok",
        "state": "COMPLETED",
        "analysis_id": "case",
        "task_id": "task",
        "job_id": "123",
        "n_candidates": 8621,
        "candidates": [{"gene": "X", "variants": "variant 演示" * 1000} for _ in range(100)],
    }
    original = copy.deepcopy(result)
    full = tmp_path / "result.json"
    full.write_text(json.dumps(result))
    server = make_server("bounded-fixture", "Synthetic response size test.")

    @server.tool(structured_output=True)
    def check() -> dict[str, Any]:
        return bounded_result(result, full_result=full)

    async def run():
        async with Client(server) as client:
            response = await client.call_tool("check", {})
            assert len(response.model_dump_json().encode()) < 24000
            preview = response.structured_content
            assert preview["n_candidates"] == 8621
            assert preview["task_id"] == "task" and preview["state"] == "COMPLETED"
            assert preview["response_omitted"]
            assert json.loads(full.read_text()) == original
            assert result == original

    asyncio.run(run())


def test_fallback_retains_failure_identity_and_counts():
    result = {str(i): "a" * 1000 for i in range(100)}
    result.update(
        status="error",
        state="FAILED",
        error="specific error " * 200,
        note="retry needed",
        analysis_id="a",
        task_id="b",
        job_id="c",
        n_contexts=0,
    )
    preview = bounded_result(result, full_result="/data/full.json")
    assert preview["status"] == "error" and preview["state"] == "FAILED"
    assert preview["analysis_id"] == "a" and preview["task_id"] == "b" and preview["job_id"] == "c"
    assert preview["error"].startswith("specific error") and preview["n_contexts"] == 0
    assert preview["response_omitted"]
