"""The tools through an in-process MCP client (offline model and embedder), in the order an agent calls them,
and the stdio transport through the generic aiscientist-mcp client."""

import asyncio
import csv
import json
import os
import subprocess
import sys

TOOLS = {"search_sentences", "search_passages", "read_passage", "grade_genes", "get_grades", "get_gene_evidence",
         "evaluate", "list_gradings"}  # fmt: skip
QUESTION = "What are haploinsufficient genes in RetiGene papers?"


def test_agent_flow(env, tmp_path):
    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    ref = tmp_path / "ref.csv"
    with ref.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["gene", "review_grade", "phenotype_kind"])
        w.writerows([("PAX6", "A", "other_ocular"), ("PRPF31", "A", "retinal"), ("SOX2", "B", "other_ocular"),
                     ("CRX", "B", "retinal"), ("DYRK1A", "N", "syndromic_with_eye")])  # fmt: skip

    async def flow():
        async with Client(server, raise_exceptions=True) as c:
            assert {t.name for t in (await c.list_tools()).tools} == TOOLS
            assert "search_sentences" in c.instructions
            terms = ["haploinsufficien", "loss of function"]
            hits = (await c.call_tool("search_sentences", {"terms": terms})).structured_content
            assert hits["total_sentences"] == 8
            # the agent gives its terms; the server grades every gene their sentences name (HRG4 -> UNC119)
            g = (await c.call_tool("grade_genes", {"question": QUESTION, "terms": terms})).structured_content
            assert g["grades"]["A"] >= 3 and [s["stage"] for s in g["stages"]] == ["first_pass", "review"]
            assert g["genes"] == 6 and g["no_evidence"] == []  # PAX6 PRPF31 SOX2 UNC119 DYRK1A CRX
            gid = g["grading_id"]
            again = (await c.call_tool("grade_genes", {"question": QUESTION, "terms": terms})).structured_content
            assert again["reused"] and again["grading_id"] == gid
            rows = (await c.call_tool("get_grades", {"grading_id": gid, "grades": "A,B,N"})).structured_content["rows"]
            grade = {r["gene"]: r["grade"] for r in rows}
            assert grade["PAX6"] == "A" and grade["PRPF31"] == "A" and grade["UNC119"] == "A"  # HRG4 -> UNC119
            assert grade["SOX2"] == "B"  # hedged
            assert grade["CRX"] == "B"  # contradicted
            assert grade["DYRK1A"] == "N"  # not an eye disease
            pax6 = next(r for r in rows if r["gene"] == "PAX6")
            assert (pax6["quote_pmid"], pax6["quote_page"]) == ("101", "1")
            ev = (await c.call_tool("get_gene_evidence", {"grading_id": gid, "gene": "PAX6"})).structured_content
            assert ev["rows"][0]["sentence"] == "Haploinsufficiency of PAX6 causes classic aniridia."
            sc = (await c.call_tool("evaluate", {"grading_id": gid, "reference": str(ref)})).structured_content
            assert sc["passes"] == {"all_A_listed": True, "all_B_listed_apart": True, "N_not_listed": True}
            near = (
                await c.call_tool("search_passages", {"query": "filler passage imaging", "top_k": 2})
            ).structured_content
            assert len(near["passages"]) == 2
            assert (await c.call_tool("read_passage", {"pmid": "104", "chunk": 0})).structured_content[
                "title"
            ] == "Paper 104"
            assert (await c.call_tool("list_gradings", {})).structured_content["gradings"][0]["grading_id"] == gid
            assert (await c.call_tool("get_grades", {"grading_id": "../etc"})).is_error

    asyncio.run(flow())


def test_unreadable_replies_are_corrected_or_retried(env, monkeypatch):
    from aiscientist_mcp.retigene_rag import model
    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    real = model._offline
    broken = {"SOX2": "always", "PAX6": "once"}

    def flaky(messages):
        user = messages[1]["content"]
        gene = next((g for g in broken if f"Gene: {g} " in user), None)
        corrected = any("could not be read" in m["content"] for m in messages[2:])
        if gene and (broken[gene] == "always" or not corrected):
            return "I think it is fine.", {"input_tokens": 10, "output_tokens": 5}
        return real(messages)

    monkeypatch.setattr(model, "_offline", flaky)
    args = {
        "question": QUESTION,
        "genes": ["PAX6", "SOX2", "PRPF31"],
        "terms": ["haploinsufficien", "loss of function"],
    }

    async def flow():
        async with Client(server, raise_exceptions=True) as c:
            g = (await c.call_tool("grade_genes", args)).structured_content
            assert g["failed_first_pass"] == ["SOX2"] and not g["complete"]
            grade1 = await c.call_tool("get_grades", {"grading_id": g["grading_id"], "grades": "A,B,N"})
            first = {r["gene"]: r["grade"] for r in grade1.structured_content["rows"]}
            assert first["PAX6"] == "A" and first["SOX2"] == "N"  # PAX6 fixed by the correction prompt
            broken.clear()
            again = (await c.call_tool("grade_genes", args)).structured_content
            assert again["complete"] and not again.get("reused") and again["failed_first_pass"] == []
            rows = (
                await c.call_tool("get_grades", {"grading_id": g["grading_id"], "grades": "A,B,N"})
            ).structured_content
            assert {r["gene"]: r["grade"] for r in rows["rows"]}["SOX2"] == "B"

    asyncio.run(flow())


def test_over_budget_stops_before_any_call(env, monkeypatch):
    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    monkeypatch.setenv("RETIGENE_JUDGE_MODEL", "claude-haiku-5-5")  # priced, but never called

    async def flow():
        async with Client(server) as c:
            r = await c.call_tool("grade_genes", {"question": QUESTION, "genes": ["PAX6"], "terms": ["haploinsufficien"],
                                                  "max_cost_usd": 1e-9})  # fmt: skip
            assert r.is_error and "max_cost_usd" in " ".join(getattr(x, "text", "") for x in r.content)

    asyncio.run(flow())


def test_stdio_transport(env):
    base = [sys.executable, "-m", "aiscientist_mcp.common.client", "--server",
            f"{sys.executable} -m aiscientist_mcp.retigene_rag.server"]  # fmt: skip
    e = os.environ | env
    out = subprocess.run([*base, "tools"], env=e, capture_output=True, text=True, check=True, timeout=300)
    assert {t["name"] for t in json.loads(out.stdout)} == TOOLS
    out = subprocess.run([*base, "call", "search_sentences", json.dumps({"terms": ["haploinsufficien"]})], env=e,
                         capture_output=True, text=True, check=True, timeout=300)  # fmt: skip
    assert json.loads(out.stdout)["total_sentences"] == 6
