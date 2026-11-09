"""The tools through an in-process MCP client (offline model and embedder), in the order an agent calls them,
and the stdio transport through the generic aiscientist-mcp client."""

import asyncio
import csv
import json
import os
import subprocess
import sys

TOOLS = {"search_sentences", "search_passages", "read_passage", "submit_curation", "curation_status", "get_genes",
         "get_evidence", "evaluate", "write_report", "list_curations"}  # fmt: skip
QUESTION = "What are haploinsufficient genes in RetiGene papers?"


def _reference(path):
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gene", "review_grade", "phenotype_kind"])
        w.writerows([("PAX6", "A", "other_ocular"), ("PRPF31", "A", "retinal"), ("SOX2", "B", "other_ocular"),
                     ("CRX", "B", "retinal"), ("DYRK1A", "N", "syndromic_with_eye")])  # fmt: skip
    return path


def test_agent_flow(env, tmp_path):
    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    ref = _reference(tmp_path / "ref.csv")

    async def flow():
        async with Client(server, raise_exceptions=True) as c:
            assert {t.name for t in (await c.list_tools()).tools} == TOOLS
            assert "submit_curation" in c.instructions
            hits = (await c.call_tool("search_sentences", {"terms": ["haploinsufficien"]})).structured_content
            assert hits["total_sentences"] == 9  # the ligature and the hyphen are folded
            assert "PMID 109 p2 c0:" in hits["sentences"]
            terms = ["haploinsufficien"]
            est = (await c.call_tool("submit_curation", {"question": QUESTION, "terms": terms,
                                                          "estimate_only": True})).structured_content  # fmt: skip
            assert est["state"] == "ESTIMATE" and est["estimate"]["genes"] == 7
            assert est["found"]["skipped_names"] == []
            cur = (await c.call_tool("submit_curation", {"question": QUESTION, "terms": terms})).structured_content
            assert cur["state"] == "COMPLETED", cur
            cid = cur["curation_id"]
            assert cur["total"]["calls"] > 0 and cur["total"]["seconds"] >= 0
            assert {"PAX6", "PRPF31"} <= set(cur["genes_A"]) and {"SOX2", "CRX"} <= set(cur["genes_B"])
            again = (await c.call_tool("submit_curation", {"question": "Reworded?", "terms": terms})).structured_content
            assert again["reused"] and again["curation_id"] == cid
            st = (await c.call_tool("curation_status", {"curation_id": cid})).structured_content
            assert st["state"] == "COMPLETED"
            got = await c.call_tool("get_genes", {"curation_id": cid, "grades": "A,B,N"})
            grade = {r["gene"]: r["grade"] for r in got.structured_content["rows"]}
            assert grade["PAX6"] == "A" and grade["PRPF31"] == "A"
            assert grade["SOX2"] == "B"  # hedged
            assert grade["CRX"] == "B"  # another sentence rules haploinsufficiency out
            assert grade["DYRK1A"] == "N"  # not an eye disease
            assert "UNC119" in grade and "OTX2" in grade  # HRG4 is UNC119; OTX2 is named next to its sentence
            pax6 = next(r for r in got.structured_content["rows"] if r["gene"] == "PAX6")
            assert (pax6["pmid"], pax6["page"], pax6["sentence_id"]) == ("101", "1", "101:0:0")
            assert pax6["quote"] == "Haploinsufficiency of PAX6 causes classic aniridia."
            ev = (await c.call_tool("get_evidence", {"curation_id": cid, "gene": "CRX"})).structured_content
            assert ev["total"] == 2 and {r["contests"] for r in ev["rows"]} == {"True", "False"}
            sc = (await c.call_tool("evaluate", {"curation_id": cid, "reference": str(ref)})).structured_content
            assert sc["passed"] and sc["A_recall"] == "2/2" and sc["B_listed_apart"] == "2/2"
            rep = (await c.call_tool("write_report", {"curation_id": cid})).structured_content
            assert (
                "## Genes graded B, listed apart (2)" in rep["markdown"]
                and "B genes listed apart: 2/2" in rep["markdown"]
            )
            near = await c.call_tool("search_passages", {"query": "filler passage imaging", "top_k": 2})
            assert len(near.structured_content["passages"]) == 2
            gated = await c.call_tool("search_passages", {"query": "eye disease", "terms": ["haploinsufficien"]})
            assert gated.structured_content["passages_with_terms"] == 8
            assert all(int(p["pmid"]) < 200 for p in gated.structured_content["passages"])
            p = (await c.call_tool("read_passage", {"pmid": "104", "chunk": 0})).structured_content
            assert p["title"] == "Paper 104" and p["doi"] == "10.1/104"
            assert (await c.call_tool("list_curations", {})).structured_content["curations"][0]["curation_id"] == cid
            assert (await c.call_tool("get_genes", {"curation_id": "../etc"})).is_error

    asyncio.run(flow())


def test_replies_are_reused_from_the_cache(env):
    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    async def flow():
        async with Client(server, raise_exceptions=True) as c:
            args = {"question": QUESTION, "terms": ["haploinsufficien"]}
            first = (await c.call_tool("submit_curation", args)).structured_content
            again = (await c.call_tool("submit_curation", args | {"rerun": True})).structured_content
            assert first["total"]["calls"] > 0 and first["total"]["cached"] == 0
            assert again["total"]["calls"] == 0 and again["total"]["cached"] == first["total"]["calls"]
            assert again["genes_A"] == first["genes_A"] and again["genes_B"] == first["genes_B"]

    asyncio.run(flow())


def test_over_budget_stops_before_any_call(env, monkeypatch):
    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    monkeypatch.setenv("RETIGENE_CHECK_MODEL", "claude-haiku-5-5")  # priced, but never called

    async def flow():
        async with Client(server) as c:
            r = await c.call_tool("submit_curation", {"question": QUESTION, "terms": [], "max_cost_usd": 1e-9})
            assert r.is_error and "max_cost_usd" in " ".join(getattr(x, "text", "") for x in r.content)
            ok = await c.call_tool("submit_curation", {"question": QUESTION, "estimate_only": True})
            assert ok.structured_content["estimate"]["cost_usd"][1] > 0

    asyncio.run(flow())


def test_stdio_transport(env):
    base = [sys.executable, "-m", "aiscientist_mcp.common.client", "--server",
            f"{sys.executable} -m aiscientist_mcp.retigene_rag.server"]  # fmt: skip
    e = os.environ | env
    out = subprocess.run([*base, "tools"], env=e, capture_output=True, text=True, check=True, timeout=300)
    assert {t["name"] for t in json.loads(out.stdout)} == TOOLS
    out = subprocess.run([*base, "call", "search_sentences", json.dumps({"terms": ["dominant negative"]})], env=e,
                         capture_output=True, text=True, check=True, timeout=300)  # fmt: skip
    assert json.loads(out.stdout)["total_sentences"] == 1


def test_curation_as_a_background_job(env, monkeypatch):
    """With the local executor the curation runs as its own process (the job entry point a Slurm job runs),
    and submit_curation returns at once; curation_status follows it to COMPLETED."""
    import time

    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    monkeypatch.setenv("AISCI_EXECUTOR", "local")

    async def flow():
        async with Client(server, raise_exceptions=True) as c:
            first = (await c.call_tool("submit_curation", {"question": QUESTION})).structured_content
            assert first["state"] in ("RUNNING", "COMPLETED")
            st, t0 = first, time.time()
            while st["state"] == "RUNNING" and time.time() - t0 < 120:
                await asyncio.sleep(0.5)
                st = (await c.call_tool("curation_status", {"curation_id": first["curation_id"]})).structured_content
            assert st["state"] == "COMPLETED", st
            assert {"PAX6", "PRPF31"} <= set(st["genes_A"]) and st["total"]["calls"] > 0

    asyncio.run(flow())
