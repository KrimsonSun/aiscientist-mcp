"""Call an explicitly configured literature MCP server; never read its workspace."""

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import time

from aiscientist_mcp.common.paths import safe_id
from aiscientist_mcp.common.tasks import ACTIVE
from mcp import Client
from mcp.client.stdio import StdioServerParameters

MAX_RESULT_BYTES = 32 * 1024 * 1024
CHUNK_BYTES = 8192


async def complete_result(client, preview: dict, analysis_id: str, deadline: float) -> dict:
    """Reassemble a peer's completed evidence only through bounded MCP resources.

    No local path is accepted, even when both servers happen to share a container.
    A missing/inconsistent page remains an error, never partial graded evidence.
    """
    task_id = safe_id(preview.get("task_id"), "literature task_id")
    safe_id(analysis_id, "literature analysis_id")
    base = f"literature://results/{analysis_id}/{task_id}/"
    if preview.get("full_result_uri") != base + "0" or preview.get("state") != "COMPLETED":
        raise ValueError("literature MCP has no complete evidence resource")
    data = bytearray()
    identity = None
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("literature MCP evidence read timed out")
        response = await client.read_resource(base + str(len(data)))
        if len(response.contents) != 1 or not isinstance(getattr(response.contents[0], "text", None), str):
            raise ValueError("literature MCP result page is not one text resource")
        text = response.contents[0].text
        if len(text.encode()) > 16000:
            raise ValueError("literature MCP result page exceeds its bound")
        page = json.loads(text)
        if not isinstance(page, dict):
            raise ValueError("literature MCP result page is not an object")
        total, digest = page.get("total_bytes"), page.get("sha256")
        if (
            type(total) is not int
            or not 0 < total <= MAX_RESULT_BYTES
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or page.get("analysis_id") != analysis_id
            or page.get("task_id") != task_id
            or type(page.get("offset")) is not int
            or page["offset"] != len(data)
            or page.get("encoding") != "base64"
        ):
            raise ValueError("literature MCP result page has invalid identity or bounds")
        if identity is not None and identity != (total, digest):
            raise ValueError("literature MCP evidence changed between pages")
        identity = (total, digest)
        chunk = base64.b64decode(page.get("data", ""), validate=True)
        if not 0 < len(chunk) <= CHUNK_BYTES or len(data) + len(chunk) > total:
            raise ValueError("literature MCP result chunk has invalid size")
        data.extend(chunk)
        expected_next = len(data) if len(data) < total else None
        if page.get("next_offset") != expected_next or (
            expected_next is not None and type(page.get("next_offset")) is not int
        ):
            raise ValueError("literature MCP result pagination is inconsistent")
        if expected_next is None:
            break
    if hashlib.sha256(data).hexdigest() != identity[1]:
        raise ValueError("literature MCP complete evidence checksum differs")
    result = json.loads(data)
    if not isinstance(result, dict) or result.get("response_omitted"):
        raise ValueError("literature MCP complete evidence is not a full result object")
    if result.get("analysis_id", analysis_id) != analysis_id or result.get("task_id", task_id) != task_id:
        raise ValueError("literature MCP complete evidence belongs to another task")
    return result | {"analysis_id": analysis_id, "task_id": task_id, "state": "COMPLETED"}


async def query(command: str, question: str, analysis_id: str, timeout: float):
    argv = shlex.split(command)
    if not argv:
        raise ValueError("PHENOTYPE_LITERATURE_COMMAND is empty")
    # Independent literature workspaces per case/question, accessible only through MCP.
    token = hashlib.sha256(f"{analysis_id}\0{question}".encode()).hexdigest()[:32]
    literature_id = "phenotype-" + token
    parameters = StdioServerParameters(command=shutil.which(argv[0]) or argv[0], args=argv[1:], env=dict(os.environ))
    deadline = time.monotonic() + timeout
    async with Client(parameters, read_timeout_seconds=timeout) as client:

        async def call(name, arguments):
            result = await client.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError("literature MCP tool failed; inspect its job status/logs")
            if not isinstance(result.structured_content, dict):
                raise ValueError("literature MCP returned no structured result")
            return result.structured_content

        result = await call("deep_literature", {"question": question, "analysis_id": literature_id})
        while result.get("state") in ACTIVE:
            if time.monotonic() >= deadline:
                raise TimeoutError("literature MCP task timed out")
            if not result.get("task_id"):
                raise ValueError("literature MCP task returned no task_id")
            await asyncio.sleep(min(2, max(0, deadline - time.monotonic())))
            result = await call("literature_job_status", {"analysis_id": literature_id, "task_id": result["task_id"]})
        if result.get("state") == "FAILED":
            return {"status": "error", "note": "literature MCP job failed", "task_id": result.get("task_id")}
        if result.get("response_omitted"):
            result = await complete_result(client, result, literature_id, deadline)
        return result


def executor(command: str, analysis_id: str, timeout: float):
    def call(arguments, _context=None):
        return asyncio.run(query(command, arguments["question"], analysis_id, timeout))

    return call
