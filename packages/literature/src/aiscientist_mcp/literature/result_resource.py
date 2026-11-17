"""Bounded, read-only MCP access to complete terminal task evidence."""

import base64
import hashlib
import json
import os
import stat

from aiscientist_mcp.common import jobs
from aiscientist_mcp.common.paths import safe_id

from .runtime import task_dir, workspace

CHUNK_BYTES = 8192
MAX_RESULT_BYTES = 32 * 1024 * 1024
URI_TEMPLATE = "literature://results/{analysis_id}/{task_id}/{offset}"


def result_uri(analysis_id: str, task_id: str, offset: int = 0) -> str:
    return URI_TEMPLATE.format(
        analysis_id=safe_id(analysis_id, "analysis_id"), task_id=safe_id(task_id, "task_id"), offset=offset
    )


def result_chunk(analysis_id: str, task_id: str, offset: str) -> str:
    """Read only a completed task's own result.json, never a caller-supplied path."""
    if not offset.isascii() or not offset.isdecimal() or len(offset) > 10:
        raise ValueError("invalid result byte offset")
    start = int(offset)
    task = task_dir(workspace(analysis_id), safe_id(task_id, "task_id"))
    done = jobs.managed_file(task, "done.json")
    if not done.is_file() or json.loads(done.read_text()).get("state") != "COMPLETED":
        raise ValueError("complete result is available only for a completed task")
    path = jobs.managed_file(task, "result.json")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_RESULT_BYTES:
            raise ValueError("result is not a supported bounded regular file")
        if start >= info.st_size:
            raise ValueError("result byte offset is outside the file")
        # One file descriptor is a stable snapshot across an atomic replacement.
        data = handle.read(MAX_RESULT_BYTES + 1)
        if len(data) != info.st_size:
            raise ValueError("result changed while being read")
    chunk = data[start : start + CHUNK_BYTES]
    end = start + len(chunk)
    return json.dumps(
        {
            "analysis_id": analysis_id,
            "task_id": task_id,
            "offset": start,
            "total_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "encoding": "base64",
            "data": base64.b64encode(chunk).decode("ascii"),
            "next_offset": end if end < len(data) else None,
        },
        ensure_ascii=True,
    )
