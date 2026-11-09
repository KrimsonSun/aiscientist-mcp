"""The 15-gene test (RCHENLAB/AiScientist issue 99): Claude answers a RetiGene question with only this server's
tools and instructions, and the curation it made is scored against the curator reference.

    python eval/agent_eval.py --out run.json [--model claude-haiku-5-5] [--question "..."]

The server is started over stdio, as an MCP client starts it, so this tests what Claude Code would see. It needs
ANTHROPIC_API_KEY (ANTHROPIC_BASE_URL for a proxy) and the server's settings (RETIGENE_INDEX, AISCI_DATA_DIR,
AISCI_EXECUTOR=inline, ...) in the environment. The report holds every tool call, the agent's tokens and cost, the
curation's time, calls, tokens and cost, the score and the agent's final answer.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import anthropic
from mcp import Client
from mcp.client.stdio import StdioServerParameters

PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
QUESTION = "What are haploinsufficient genes in RetiGene papers?"
PREAMBLE = "Answer the user's question with the tools of the MCP server retigene-rag. The server's instructions:\n\n"


def _cost(use: dict, model: str) -> float:
    pin, pout = PRICES[model]
    return (use["input_tokens"] * pin + use["output_tokens"] * pout + use["cache_write_tokens"] * pin * 1.25
            + use["cache_read_tokens"] * pin * 0.1) / 1e6  # fmt: skip


async def run(a) -> dict:
    params = StdioServerParameters(command=sys.executable, args=["-m", "aiscientist_mcp.retigene_rag.server"],
                                   env=dict(os.environ))  # fmt: skip
    claude = anthropic.Anthropic(timeout=600.0, max_retries=3)
    use = {"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0, "calls": 0}
    log, curations, curation_total, answer, t0 = [], [], {}, "", time.time()
    async with Client(params, read_timeout_seconds=1800) as c:
        tools = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                 for t in (await c.list_tools()).tools]  # fmt: skip
        system = PREAMBLE + (c.instructions or "")
        messages = [{"role": "user", "content": a.question}]
        for turn in range(a.max_turns):
            kw = {"model": a.model, "max_tokens": 16000, "system": system, "tools": tools, "messages": messages,
                  "cache_control": {"type": "ephemeral"}, "output_config": {"effort": a.effort}}  # fmt: skip
            with claude.messages.stream(**kw) as st:
                final = st.get_final_message()
            u = final.usage
            use["calls"] += 1
            use["input_tokens"] += u.input_tokens
            use["output_tokens"] += u.output_tokens
            use["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
            use["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
            messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in final.content]})
            calls = [b for b in final.content if b.type == "tool_use"]
            if not calls:
                answer = "\n".join(getattr(b, "text", "") for b in final.content if b.type == "text")
                break
            results = []
            for b in calls:
                r = await c.call_tool(b.name, b.input)
                payload = r.structured_content if r.structured_content is not None else " ".join(
                    getattr(x, "text", "") for x in r.content)  # fmt: skip
                done = isinstance(payload, dict) and payload.get("state") == "COMPLETED"
                if b.name in ("submit_curation", "curation_status") and not r.is_error and done:
                    curations.append(payload["curation_id"])
                    if not payload.get("reused"):
                        curation_total[payload["curation_id"]] = payload["total"]
                entry = {"turn": turn, "tool": b.name, "args": b.input, "error": r.is_error,
                         "total_sentences": payload.get("total_sentences") if isinstance(payload, dict) else None}  # fmt: skip
                log.append(entry)
                print(json.dumps(entry)[:300], flush=True)
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": bool(r.is_error),
                                "content": json.dumps(payload, ensure_ascii=False)})  # fmt: skip
            messages.append({"role": "user", "content": results})
        cid = curations[-1] if curations else None
        score = (await c.call_tool("evaluate", {"curation_id": cid})).structured_content if cid else None
        grades = (
            (await c.call_tool("get_genes", {"curation_id": cid, "grades": "A"})).structured_content
            if cid
            else {"rows": []}
        )
    agent_cost = _cost(use, a.model)
    cur = {
        k: sum(t.get(k, 0) for t in curation_total.values())
        for k in ("calls", "cached", "input_tokens", "output_tokens", "cost_usd", "seconds")
    }
    a_genes = [r["gene"] for r in grades["rows"]]
    named = sum(bool(re.search(r"\b" + re.escape(g) + r"\b", answer)) for g in a_genes)
    return {"question": a.question, "model": a.model, "effort": a.effort, "agent": {**use, "cost_usd": round(agent_cost, 4)},
            "curation": cur, "total_cost_usd": round(agent_cost + cur["cost_usd"], 4),
            "total_calls": use["calls"] + cur["calls"], "total_input_tokens": use["input_tokens"] + use["cache_write_tokens"]
            + use["cache_read_tokens"] + cur["input_tokens"], "total_output_tokens": use["output_tokens"] + cur["output_tokens"],
            "seconds": round(time.time() - t0), "tool_calls": log, "curation_id": cid,
            "score": score, "answer": answer,
            "A_genes_named_in_answer": f"{named}/{len(a_genes)}"}  # fmt: skip


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="claude-haiku-5-5", choices=sorted(PRICES))
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--question", default=QUESTION)
    ap.add_argument("--max-turns", type=int, default=30)
    a = ap.parse_args()
    rep = asyncio.run(run(a))
    a.out.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    s = rep["score"] or {}
    print(json.dumps({k: rep[k] for k in ("model", "total_cost_usd", "total_calls", "seconds", "A_genes_named_in_answer")}
                     | {"agent_cost_usd": rep["agent"]["cost_usd"], "agent_turns": rep["agent"]["calls"], "curation": rep["curation"]}))  # fmt: skip
    print(json.dumps({k: s.get(k) for k in ("A_recall", "A_recall_ocular", "A_recall_syndromic", "A_missed",
                                            "B_listed_apart", "B_graded", "N_listed", "A_outside_reference", "passed")}))  # fmt: skip


if __name__ == "__main__":
    main()
