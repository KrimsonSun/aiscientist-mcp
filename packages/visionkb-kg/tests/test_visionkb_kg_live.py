"""Live tests against a running visionkb-api. Skipped unless VISIONKB_API_URL is set.

  VISIONKB_API_URL=http://127.0.0.1:8770 VISIONKB_API_TOKEN_FILE=/path/to/token python -m pytest \
      packages/visionkb-kg/tests/test_visionkb_kg_live.py

Optional: VISIONKB_LIVE_GENE (symbol, default RHO), VISIONKB_LIVE_RELEASE (expected kg_release). The checks are
release-agnostic: they follow the graph from the gene (gene -> expressed_in edge -> evidence -> cell type ->
gene set, datasets) instead of expecting fixed values.
"""

import asyncio
import json
import os
import re

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("VISIONKB_API_URL"), reason="VISIONKB_API_URL not set")
GENE = os.environ.get("VISIONKB_LIVE_GENE", "RHO")
RELEASE_RE = re.compile(r"^visionkb-\d{4}\.\d{2}\.\d+$")
MAX_CHARS = 24_000  # D13: every tool result, compact JSON, within the AiScientist tool-result cap


def call(name, args=None):
    from aiscientist_mcp.visionkb_kg.server import server
    from mcp import Client

    async def flow():
        async with Client(server, raise_exceptions=True) as c:
            r = await c.call_tool(name, args or {})
            assert not r.is_error, r.content
            return r.structured_content

    r = asyncio.run(flow())
    size = len(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
    assert size <= MAX_CHARS, f"{name}: {size} characters"
    assert isinstance(r["warnings"], list)
    return r


def ok(name, args=None):
    r = call(name, args)
    assert r["status"] == "ok", r["error"]
    assert r["kg_release"] == release()
    return r


_release = {}


def release():
    if "id" not in _release:
        r = call("get_release")
        assert r["status"] == "ok", r["error"]
        _release["id"] = r["kg_release"]
        _release["data"] = r["data"]
    return _release["id"]


def test_release():
    rid = release()
    assert RELEASE_RE.match(rid)
    if os.environ.get("VISIONKB_LIVE_RELEASE"):
        assert rid == os.environ["VISIONKB_LIVE_RELEASE"]
    d = _release["data"]
    assert d["ready"] is True
    assert d["release"]["biolink_version"] == "4.4.6"
    assert d["health"]["release"] == rid


@pytest.fixture(scope="module")
def walk():
    """Gene -> its top expressed_in edge -> the edge's object (a cell type or state)."""
    hits = ok("search_entities", {"q": GENE, "category": "biolink:Gene", "limit": 5})["data"]
    gene = next(h for h in hits if h["match"] in ("exact_symbol", "exact_id"))
    rels = ok("get_relationships", {"subject": gene["id"], "predicate": "biolink:expressed_in", "limit": 5})
    assert rels["data"], f"{GENE}: no expressed_in edges"
    return {"gene": gene, "edge": rels["data"][0], "rels": rels}


def test_entity(walk):
    g = walk["gene"]
    assert g["id"].startswith("HGNC:") and g["symbol"].upper() == GENE.upper()
    node = ok("get_entity", {"curie": g["id"]})["data"]
    assert node["node"]["id"] == g["id"] and "biolink:expressed_in" in node["degree_by_predicate"]


def test_relationships(walk):
    rels = walk["rels"]
    assert rels["page"]["limit"] == 5 and len(rels["data"]) <= 5
    for e in rels["data"]:
        assert e["subject"]["id"] == walk["gene"]["id"] and e["predicate"] == "biolink:expressed_in"
        assert e["edge_id"].startswith("visionkb:e-") and e["knowledge_level"] and e["primary_knowledge_source"]


def test_evidence(walk):
    ev = ok("get_evidence", {"edge_id": walk["edge"]["edge_id"], "limit": 3})["data"]
    assert ev["edge"]["edge_id"] == walk["edge"]["edge_id"]
    assert ev["study_results"] and all(sr.get("dataset_id") for sr in ev["study_results"])


def test_gene_set(walk):
    obj = walk["edge"]["object"]["id"]
    r = ok("get_gene_set", {"entity": obj, "limit": 10})
    assert r["data"] and all(x["gene_id"] for x in r["data"])
    assert any("score" in w for w in r["warnings"])


def test_atlas_expression(walk):
    r = ok("query_atlas_expression", {"genes": [GENE], "limit": 10})
    assert r["data"] and {x["gene_id"] for x in r["data"]} == {walk["gene"]["id"]}
    assert all(x["level"] == "majorclass" and x["dataset_version"] for x in r["data"])
    v = [x["pseudobulk_cp10k"] for x in r["data"]]
    assert v == sorted(v, reverse=True)


def test_find_datasets(walk):
    r = ok("find_datasets", {"entities": [walk["edge"]["object"]["id"]], "limit": 5})
    assert r["data"] and all(x["bind"] and x["id"].startswith("visionkb:ds-") for x in r["data"])


def test_errors(tmp_path, monkeypatch):
    rid = release()
    r = call("get_entity", {"curie": "HGNC:0"})
    assert (r["status"], r["error"]["code"], r["http_status"], r["kg_release"]) == ("error", "not_found", 404, rid)
    r = call("get_relationships", {})
    assert r["error"]["code"] == "missing_anchor"
    bad = tmp_path / "token"
    bad.write_text("not-a-valid-token\n")
    monkeypatch.setenv("VISIONKB_API_TOKEN_FILE", str(bad))
    r = call("search_entities", {"q": GENE})
    assert (r["status"], r["http_status"], r["error"]["code"]) == ("error", 401, "unauthorized")


def test_paging_follows_next_offset(walk):
    """Walk the gene's edges with a small limit via paging.next_offset: every edge exactly once."""
    gid, total, ids, offset = walk["gene"]["id"], None, [], 0
    for _ in range(50):
        r = ok("get_relationships", {"subject": gid, "limit": 7, "offset": offset})
        p = r["paging"]
        assert p["returned"] == len(r["data"]) <= 7 and p["offset"] == offset
        total = p["total"]
        ids += [e["edge_id"] for e in r["data"]]
        offset = p["next_offset"]
        if offset is None:
            break
    assert len(ids) == len(set(ids)) == total


def test_paging_large_page():
    """A large request: if the API trims it, paging says so and next_offset = offset + rows returned."""
    r = ok("get_gene_set", {"entity": "CL:0000604", "limit": 200})
    p = r["paging"]
    assert p["returned"] == len(r["data"]) <= 200
    if p["trimmed"]:
        assert p["returned"] < 200 and p["next_offset"] == p["returned"]
        assert any(w.startswith("data: returned") for w in r["warnings"]) and r["warnings"][-1] == p["summary"]
    elif p["total"] is not None and p["total"] > p["returned"]:
        assert p["next_offset"] == p["returned"]
