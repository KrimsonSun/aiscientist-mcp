import asyncio
import base64
import hashlib
import json
import os
import sys

import pytest
from aiscientist_mcp.literature import runtime, server
from aiscientist_mcp.literature.result_resource import result_chunk, result_uri
from mcp import Client
from mcp.client.stdio import StdioServerParameters


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    ws = runtime.workspace("synthetic")
    task = ws / "jobs" / "task-one"
    task.mkdir()
    for name, value in {
        "request.json": {"tool": "deep_literature"},
        "state.json": {"state": "COMPLETED", "job_id": "inline"},
        "done.json": {"state": "COMPLETED"},
        "result.json": {
            "status": "ok",
            "answer": "Complete evidence",
            "contexts": [{"citation": f"Synthetic PMID: {n}", "summary": "retinal research " * 800} for n in range(8)],
        },
    }.items():
        (task / name).write_text(json.dumps(value))
    return task


@pytest.mark.parametrize("stdio", [False, True])
def test_protocol_pages_preserve_every_byte_without_large_tool_responses(evidence, stdio):
    async def run():
        transport = (
            StdioServerParameters(
                command=sys.executable, args=["-m", "aiscientist_mcp.literature.server"], env=dict(os.environ)
            )
            if stdio
            else server.server
        )
        async with Client(transport) as client:
            preview = await client.call_tool(
                "literature_job_status", {"analysis_id": "synthetic", "task_id": "task-one"}
            )
            assert len(preview.model_dump_json().encode()) < 24000
            value = preview.structured_content
            assert value["response_omitted"] and value["full_result_uri"] == result_uri("synthetic", "task-one")
            templates = await client.list_resource_templates()
            assert any("literature://results/" in str(t.uri_template) for t in templates.resource_templates)
            data = bytearray()
            while True:
                page = await client.read_resource(result_uri("synthetic", "task-one", len(data)))
                assert len(page.model_dump_json().encode()) < 16000
                item = json.loads(page.contents[0].text)
                assert item["offset"] == len(data)
                data.extend(base64.b64decode(item["data"]))
                if item["next_offset"] is None:
                    break
            assert data == (evidence / "result.json").read_bytes()
            assert item["sha256"] == hashlib.sha256(data).hexdigest()

    asyncio.run(run())


@pytest.mark.parametrize("offset", ["-1", "../0", "nan", "99999999999", "999999999"])
def test_offset_rejected(evidence, offset):
    with pytest.raises(ValueError):
        result_chunk("synthetic", "task-one", offset)


def test_missing_failed_other_analysis_and_symlink_rejected(evidence, tmp_path):
    with pytest.raises(ValueError):
        result_chunk("other", "task-one", "0")
    with pytest.raises(ValueError):
        result_chunk("../synthetic", "task-one", "0")
    (evidence / "done.json").write_text('{"state":"FAILED"}')
    with pytest.raises(ValueError, match="completed"):
        result_chunk("synthetic", "task-one", "0")
    (evidence / "done.json").write_text('{"state":"COMPLETED"}')
    source = tmp_path / "outside.json"
    source.write_text('{"status":"ok"}')
    (evidence / "result.json").unlink()
    (evidence / "result.json").symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        result_chunk("synthetic", "task-one", "0")
