"""The server's tools through an in-process MCP client against the fake API, and the stdio transport through the
generic aiscientist-mcp client."""

import asyncio
import json
import os
import subprocess
import sys

from vkg_fake import EDGE, EDGE_TRIM, RELEASE, TOKEN, TRIM_GENE, TRIM_TOTAL

TOOLS = {"search_entities", "get_entity", "get_relationships", "get_evidence", "get_gene_set",
         "query_atlas_expression", "find_datasets", "get_release"}  # fmt: skip


def run(flow):
    from aiscientist_mcp.visionkb_kg.server import server
    from mcp import Client

    async def wrapped():
        async with Client(server, raise_exceptions=True) as c:
            return await flow(c)

    return asyncio.run(wrapped())


def sc(r):
    assert not r.is_error, r.content
    return r.structured_content


def test_tool_list_and_schemas(api_env):
    async def flow(c):
        return {t.name: t for t in (await c.list_tools()).tools}

    tools = run(flow)
    assert set(tools) == TOOLS
    for t in tools.values():
        assert t.description and t.output_schema
        assert {"status", "kg_release", "warnings", "error"} <= set(t.output_schema["properties"])
    assert tools["search_entities"].input_schema["required"] == ["q"]
    assert tools["get_evidence"].input_schema["required"] == ["edge_id"]
    lv = tools["query_atlas_expression"].input_schema["properties"]["level"]
    assert lv["enum"] == ["majorclass", "celltype", "region_majorclass"]
    lim = tools["get_relationships"].input_schema["properties"]["limit"]
    assert (lim["minimum"], lim["maximum"], lim["default"]) == (1, 200, 20)
    assert "required" not in tools["get_release"].input_schema


def test_each_tool_calls_its_endpoint(api_env):
    fake, _ = api_env
    calls = [
        ("search_entities", {"q": "RHO", "category": "biolink:Gene"}, "/v1/entities/search",
         {"q": ["RHO"], "category": ["biolink:Gene"], "limit": ["20"]}),
        ("get_entity", {"curie": "HGNC:10012"}, "/v1/entities/HGNC:10012", {}),
        ("get_relationships", {"subject": "HGNC:10012", "predicate": "biolink:expressed_in", "limit": 5},
         "/v1/relationships",
         {"subject": ["HGNC:10012"], "predicate": ["biolink:expressed_in"], "limit": ["5"], "offset": ["0"]}),
        ("get_evidence", {"edge_id": EDGE}, f"/v1/evidence/{EDGE}", {"limit": ["20"], "offset": ["0"]}),
        ("get_gene_set", {"entity": "CL:0000604", "descendants": False}, "/v1/gene-sets",
         {"entity": ["CL:0000604"], "descendants": ["false"], "limit": ["20"], "offset": ["0"]}),
        ("query_atlas_expression", {"genes": "RHO, GNAT1", "level": "celltype"}, "/v1/atlas/expression",
         {"genes": ["RHO", "GNAT1"], "level": ["celltype"], "sort": ["pseudobulk_cp10k"], "limit": ["20"],
          "offset": ["0"]}),
        ("find_datasets", {"entities": ["CL:0000604"], "tissue": "UBERON:0000966"}, "/v1/datasets",
         {"entities": ["CL:0000604"], "tissue": ["UBERON:0000966"], "limit": ["20"], "offset": ["0"]}),
    ]  # fmt: skip

    async def flow(c):
        out = []
        for name, args, path, query in calls:
            fake.log.clear()
            r = sc(await c.call_tool(name, args))
            out.append((r, fake.log[-1], path, query))
        return out

    for r, hit, path, query in run(flow):
        assert r["status"] == "ok" and r["kg_release"] == RELEASE and isinstance(r["warnings"], list)
        assert (hit["path"], hit["query"], hit["auth"]) == (path, query, f"Bearer {TOKEN}")
        assert r["request"]["path"] == path
        assert TOKEN not in json.dumps(r)


def test_get_release(api_env):
    async def flow(c):
        return sc(await c.call_tool("get_release", {}))

    r = run(flow)
    assert r["status"] == "ok" and r["kg_release"] == RELEASE and r["error"] is None
    assert r["data"]["ready"] is True and r["data"]["release"]["biolink_version"] == "4.4.6"
    assert r["data"]["health"]["version"] == "0.1.1"


def test_errors_are_results(api_env, monkeypatch):
    fake, _ = api_env

    async def flow(c):
        out = {}
        out["not_found"] = sc(await c.call_tool("get_entity", {"curie": "HGNC:99999999"}))
        out["anchor"] = sc(await c.call_tool("get_relationships", {}))
        fake.log.clear()
        out["bad_curie"] = sc(await c.call_tool("get_entity", {"curie": "../../etc/passwd"}))
        out["bad_edge"] = sc(await c.call_tool("get_evidence", {"edge_id": "x/y"}))
        out["sent_after_bad"] = len(fake.log)
        out["too_many"] = sc(await c.call_tool("query_atlas_expression", {"genes": [f"G{i}" for i in range(21)]}))
        bad_enum = await c.call_tool("query_atlas_expression", {"genes": "RHO", "level": "cell"})
        bad_limit = await c.call_tool("search_entities", {"q": "RHO", "limit": 500})
        out["schema_errors"] = (bad_enum.is_error, bad_limit.is_error)
        monkeypatch.setenv("VISIONKB_API_TOKEN_FILE", "/nonexistent/token")
        out["no_token"] = sc(await c.call_tool("search_entities", {"q": "RHO"}))
        monkeypatch.delenv("VISIONKB_API_URL")
        out["no_url"] = sc(await c.call_tool("get_release", {}))
        return out

    o = run(flow)
    assert (o["not_found"]["status"], o["not_found"]["error"]["code"], o["not_found"]["http_status"]) == (
        "error", "not_found", 404)  # fmt: skip
    assert o["not_found"]["kg_release"] == RELEASE and o["not_found"]["data"] is None
    assert o["anchor"]["error"]["code"] == "missing_anchor"
    assert (
        o["bad_curie"]["error"]["code"] == "invalid_argument" and o["bad_edge"]["error"]["code"] == "invalid_argument"
    )
    assert o["sent_after_bad"] == 0  # invalid path arguments never reach the API
    assert o["too_many"]["error"]["code"] == "too_many_genes"
    assert o["schema_errors"] == (True, True)
    assert o["no_token"]["error"]["code"] == "token_unreadable"
    assert o["no_url"]["error"]["code"] == "not_configured" and o["no_url"]["kg_release"] is None


def test_stdio_transport(api_env):
    _, env = api_env
    env = os.environ | env
    base = [sys.executable, "-m", "aiscientist_mcp.common.client", "--timeout", "60", "--server",
            f"{sys.executable} -m aiscientist_mcp.visionkb_kg.server"]  # fmt: skip
    out = subprocess.run([*base, "tools"], env=env, capture_output=True, text=True, check=True, timeout=120)
    assert {t["name"] for t in json.loads(out.stdout)} == TOOLS
    out = subprocess.run([*base, "call", "search_entities", '{"q": "RHO"}'], env=env, capture_output=True,
                         text=True, check=True, timeout=120)  # fmt: skip
    r = json.loads(out.stdout)
    assert r["status"] == "ok" and r["data"][0]["id"] == "HGNC:10012" and r["kg_release"] == RELEASE
    assert TOKEN not in out.stdout + out.stderr


def test_trimmed_pages_follow_next_offset(api_env):
    """The fake API cuts each page to 3 rows (as the real one cuts to its character cap): following
    paging.next_offset returns every edge once; stepping by limit would skip rows."""
    fake, _ = api_env

    async def flow(c):
        pages, offset = [], 0
        while offset is not None and len(pages) < 10:
            r = sc(await c.call_tool("get_relationships", {"subject": TRIM_GENE, "limit": 5, "offset": offset}))
            pages.append(r)
            offset = r["paging"]["next_offset"]
        by_limit = [sc(await c.call_tool("get_relationships", {"subject": TRIM_GENE, "limit": 5, "offset": o}))
                    for o in (0, 5)]  # fmt: skip
        ev = sc(await c.call_tool("get_evidence", {"edge_id": EDGE_TRIM, "limit": 5}))
        return pages, by_limit, ev

    fake.log.clear()
    pages, by_limit, ev = run(flow)
    assert [p["paging"]["offset"] for p in pages] == [0, 3, 6]
    assert [p["paging"]["trimmed"] for p in pages] == [True, True, False]
    assert pages[0]["paging"]["summary"].startswith("trimmed: 3 of 5 rows returned")
    assert any(w.startswith("data: returned 3 of 5 items") for w in pages[0]["warnings"])  # the API's note is kept
    ids = [e["edge_id"] for p in pages for e in p["data"]]
    assert len(ids) == len(set(ids)) == TRIM_TOTAL
    skipped = {e["edge_id"] for p in by_limit for e in p["data"]}
    assert len(skipped) == 5 < TRIM_TOTAL  # offset + limit silently loses 2 of the 7 edges
    p = ev["paging"]
    assert (p["returned"], p["total"], p["trimmed"], p["next_offset"]) == (2, 9, True, 2)
    assert ev["warnings"][-1] == p["summary"]
