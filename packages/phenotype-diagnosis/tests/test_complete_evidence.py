import asyncio
import base64
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from aiscientist_mcp.phenotype_diagnosis.literature_client import complete_result


class Peer:
    def __init__(self, value, corrupt=None):
        self.data = json.dumps(value).encode()
        self.corrupt = corrupt
        self.calls = []

    async def read_resource(self, uri):
        self.calls.append(uri)
        offset = int(uri.rsplit("/", 1)[1])
        chunk = self.data[offset : offset + 8192]
        end = offset + len(chunk)
        page = {
            "analysis_id": "case",
            "task_id": "task",
            "offset": offset,
            "total_bytes": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "encoding": "base64",
            "data": base64.b64encode(chunk).decode(),
            "next_offset": end if end < len(self.data) else None,
        }
        if self.corrupt:
            self.corrupt(page)
        return SimpleNamespace(contents=[SimpleNamespace(text=json.dumps(page))])


def preview():
    return {
        "response_omitted": True,
        "state": "COMPLETED",
        "task_id": "task",
        "full_result_uri": "literature://results/case/task/0",
    }


def read(peer, value=None):
    return asyncio.run(complete_result(peer, value or preview(), "case", time.monotonic() + 10))


def test_all_evidence_is_restored_before_grading():
    value = {
        "status": "ok",
        "answer": "STRONG",
        "contexts": [{"citation": f"Synthetic PMID: {n}", "summary": "families and knockout " * 800} for n in range(9)],
    }
    peer = Peer(value)
    out = read(peer)
    assert out["contexts"] == value["contexts"] and len(peer.calls) > 2
    assert all(uri.startswith("literature://results/case/task/") for uri in peer.calls)


@pytest.mark.parametrize(
    "change",
    [
        {"total_bytes": 100 * 1024 * 1024},
        {"analysis_id": "other"},
        {"task_id": "other"},
        {"offset": 1},
        {"sha256": "0" * 64},
        {"next_offset": 0},
        {"data": ""},
        {"encoding": "utf8"},
        {"offset": False},
    ],
)
def test_bad_page_is_never_graded(change):
    with pytest.raises(ValueError):
        read(Peer({"status": "ok"}, lambda page: page.update(change)))


def test_changing_snapshot_and_arbitrary_resource_are_rejected():
    def changed(page):
        if page["offset"]:
            page["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="changed"):
        read(Peer({"answer": "x" * 20000}, changed))
    with pytest.raises(ValueError, match="resource"):
        read(Peer({}), preview() | {"full_result_uri": "file:///etc/passwd"})
    with pytest.raises(ValueError, match="full result"):
        read(Peer({"response_omitted": True}))
