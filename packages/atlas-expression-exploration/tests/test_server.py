"""The server's tools through an in-process MCP client (local executor), and the stdio transport
through the generic aiscientist-mcp client."""

import asyncio
import json
import os
import subprocess
import sys


def _env(atlas, tmp_path, monkeypatch):
    tmp, _ = atlas
    env = {"ATLAS_H5AD": str(tmp / "atlas.h5ad"), "AISCI_DATA_DIR": str(tmp_path / "data"),
           "ATLAS_HGNC": str(tmp / "hgnc.tsv"), "AISCI_EXECUTOR": "local", "ATLAS_CLASS_ORDER": "Rod,MG,RPE",
           "ATLAS_MIN_CELLS_DONOR": "5"}  # fmt: skip
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def test_tools_end_to_end(atlas, tmp_path, monkeypatch):
    from mcp import Client

    _env(atlas, tmp_path, monkeypatch)
    from aiscientist_mcp.atlas_expression_exploration.server import server

    async def flow():
        async with Client(server, raise_exceptions=True) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert {"resolve_genes", "submit_query", "query_status", "get_summary", "plot_dotplot",
                    "plot_violin", "plot_featureplot", "write_report", "list_queries"} == names  # fmt: skip
            r = (await c.call_tool("resolve_genes", {"genes": "LRAT, cralbp"})).structured_content
            assert [x["atlas_gene"] for x in r["resolution"]] == ["LRAT", "RLBP1"]
            qid = (await c.call_tool("submit_query", {"genes": "LRAT,cralbp"})).structured_content["query_id"]
            for _ in range(600):  # a cold scanpy import on a network filesystem can take minutes
                st = (await c.call_tool("query_status", {"query_id": qid})).structured_content
                if st["state"] in ("COMPLETED", "FAILED"):
                    break
                await asyncio.sleep(1)
            assert st["state"] == "COMPLETED", st
            again = (await c.call_tool("submit_query", {"genes": "LRAT,cralbp"})).structured_content
            assert again["state"] == "COMPLETED"  # reused, not resubmitted
            rows = (await c.call_tool("get_summary", {"query_id": qid, "top": 1})).structured_content["rows"]
            assert {(x["gene"], x["majorclass"]) for x in rows} == {("LRAT", "RPE"), ("RLBP1", "MG")}
            for t in ("plot_dotplot", "plot_violin", "plot_featureplot"):
                assert (await c.call_tool(t, {"query_id": qid})).structured_content["files"]
            rep = (await c.call_tool("write_report", {"query_id": qid})).structured_content
            assert "Highest in RPE" in rep["markdown"]
            assert (await c.call_tool("get_summary", {"query_id": "../etc"})).is_error

    asyncio.run(flow())


def test_stdio_transport(atlas, tmp_path, monkeypatch):
    env = os.environ | _env(atlas, tmp_path, monkeypatch)
    base = [sys.executable, "-m", "aiscientist_mcp.common.client", "--server",
            f"{sys.executable} -m aiscientist_mcp.atlas_expression_exploration.server"]  # fmt: skip
    out = subprocess.run([*base, "tools"], env=env, capture_output=True, text=True, check=True, timeout=600)
    assert len(json.loads(out.stdout)) == 9
    out = subprocess.run([*base, "call", "resolve_genes", '{"genes": "ABCR"}'], env=env, capture_output=True,
                         text=True, check=True, timeout=600)  # fmt: skip
    assert json.loads(out.stdout)["resolution"][0]["method"] == "hgnc_previous"
