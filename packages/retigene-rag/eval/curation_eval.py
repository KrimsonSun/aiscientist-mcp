"""The 15-gene test without an agent: one curation through the server over stdio, then its score.

    python eval/curation_eval.py --out run.json [--terms haploinsufficien] [--question "..."] [--rerun]

For the model paths: the server's settings (RETIGENE_PROVIDER, RETIGENE_CHECK_MODEL, RETIGENE_REVIEW_MODEL, the
endpoint and key) choose who reads, so the same command measures the Claude key path and the shared-model path.
--rerun runs it again even when a kept curation exists (model replies still come from the cache unless
RETIGENE_CACHE points at an empty folder).
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

QUESTION = "What are haploinsufficient genes in RetiGene papers?"


async def run(a) -> dict:
    params = StdioServerParameters(command=sys.executable, args=["-m", "aiscientist_mcp.retigene_rag.server"],
                                   env=dict(os.environ))  # fmt: skip
    t0 = time.time()
    async with Client(params, read_timeout_seconds=7200) as c:
        args = {"question": a.question, "terms": a.terms, "rerun": a.rerun, "max_cost_usd": a.max_cost_usd}
        r = await c.call_tool("submit_curation", args)
        if r.is_error:
            raise SystemExit(" ".join(getattr(x, "text", "") for x in r.content))
        cur = r.structured_content
        while cur["state"] in ("PENDING", "RUNNING"):
            await asyncio.sleep(15)
            cur = (await c.call_tool("curation_status", {"curation_id": cur["curation_id"]})).structured_content
        if cur["state"] != "COMPLETED":
            raise SystemExit(json.dumps(cur))
        score = (await c.call_tool("evaluate", {"curation_id": cur["curation_id"]})).structured_content
        report = (await c.call_tool("write_report", {"curation_id": cur["curation_id"]})).structured_content
    return {"question": a.question, "terms": a.terms, "seconds": round(time.time() - t0), "curation": cur,
            "score": score, "report": report["file"]}  # fmt: skip


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--question", default=QUESTION)
    ap.add_argument("--terms", nargs="*", default=["haploinsufficien"])
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--max-cost-usd", type=float, default=5.0)
    a = ap.parse_args()
    rep = asyncio.run(run(a))
    a.out.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    s, t = rep["score"], rep["curation"]["total"]
    print(json.dumps({"models": rep["curation"]["models"], "seconds": t["seconds"], "calls": t["calls"],
                      "cached": t["cached"], "input_tokens": t["input_tokens"], "output_tokens": t["output_tokens"],
                      "cost_usd": t["cost_usd"]}))  # fmt: skip
    print(json.dumps({k: s.get(k) for k in ("A_recall", "A_recall_ocular", "A_recall_syndromic", "A_missed",
                                            "B_listed_apart", "B_graded", "N_listed", "A_outside_reference", "passed")}))  # fmt: skip


if __name__ == "__main__":
    main()
