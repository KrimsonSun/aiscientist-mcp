"""The judgement: one call per gene, from the evidence sentences that name it.

An agent's keyword search leaves dozens of candidate genes. Each is graded A, B or N by the model
against written criteria, from its own sentences only. The model must quote one sentence; a quote that
is not in the sentences it was given is flagged, and PMIDs it was not given are dropped.
"""

from __future__ import annotations

import csv
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .core import CHARS_PER_TOKEN, CORPUS, Call, add_usage, cost, json_object

MAX_ROWS = 40  # evidence rows per gene: one per paper first, then the rest
MAX_CHARS = 30_000  # characters of evidence per gene
OUT_PER_GENE = (150, 700)  # output tokens per judgement, for the estimate
GRADES = ("A", "B", "N")
ATTEMPTS = 3  # calls per gene: the first, then up to two corrections of an unreadable reply
FIX_REPLY = ("Your reply could not be read. Reply with ONLY the JSON object the instructions describe, with every "
             "field filled in, and nothing before or after it.")  # fmt: skip

# The lab's rules for the RetiGene haploinsufficiency reference (curator review of the 24 model-graded
# genes, 2026-11-04). The model answers them as separate checks and the grade follows from the answers:
# A = an unhedged statement for an eye disease that no sentence contradicts; B = a hedged or contradicted
# statement for an eye disease; N = anything else.
HI = "haploinsufficiency"
HI_CHECKS = (
    "Answer each check from the sentences:\n"
    '- "explicit": does a sentence state, WITHOUT a hedge ("may", "might", "likely", "probably", "possibly", '
    '"proposed", "suggest", "consistent with", "associated with", "it is possible"), that loss of one copy of '
    "this gene causes a human disease: haploinsufficiency or reduced dosage of the gene, or heterozygous "
    "loss-of-function variants such as deletions, truncating or null alleles? A change in the ratio of the "
    "gene's isoforms (a splice variant that reduces some isoforms) is not loss of one copy.\n"
    '- "hedged": is there such a statement only with a hedge, or only as one of several possible mechanisms? '
    "A change in the ratio of isoforms is not such a statement.\n"
    '- "subject": is THIS gene the one the statement is about, named by its symbol or gene name? false when '
    "the symbol here stands for something else (an abbreviation of another gene's protein, a disease or a "
    "method), or the gene is only listed or mentioned near a statement about another gene.\n"
    '- "eye_disease": is the disease of that statement a retinal or ocular disease, or a syndrome with an eye '
    "disease among its features (for example optic atrophy, coloboma, microphthalmia, aniridia, a "
    "vitreoretinal or retinal degeneration, posterior embryotoxon)? For a named syndrome, its well-known "
    "defining features count even when these sentences do not list them. Dysmorphic eye features (deep-set "
    "eyes) or strabismus alone: false.\n"
    '- "contradicted": does another sentence say, for the same gene and disease, that haploinsufficiency does '
    "not cause it, or that its dominant form is usually caused by another mechanism (dominant-negative, gain "
    "of function)?\n"
    'Reply with ONLY one JSON object: {{"explicit": true, "hedged": false, "subject": true, '
    '"eye_disease": true, "contradicted": false, "pmids": ["12345678"], "quote": "the one sentence, copied exactly, behind '
    '\'explicit\' or \'hedged\'", "reason": "at most 40 words"}}. "pmids" lists the papers of the sentences '
    'behind your answers; "quote" is "" when no sentence makes the claim.'
)

JUDGE_SYSTEM = (
    "You judge one gene for one question, from sentences of research papers in {corpus}. The sentences are "
    "every sentence of the corpus that names the gene and uses a word of the claim or of a mechanism, grouped "
    "by passage. Judge "
    "only from these sentences, never from what you know about the gene. Read every passage before you "
    "answer: one hedged or contradicting sentence can decide.\n"
)
GRADE_REPLY = (
    "Grades:\n{criteria}\n"
    'Reply with ONLY one JSON object: {{"grade": "A", "pmids": ["12345678"], "quote": "the one sentence, '
    'copied exactly, that best supports the grade", "reason": "at most 40 words"}}. "pmids" lists the papers '
    'whose sentences support the grade. For N, quote the sentence that shows why, or give "" when no sentence '
    "speaks to the claim."
)
CHECKS = ("explicit", "hedged", "subject", "eye_disease", "contradicted")


def system_prompt(criteria: str = HI, corpus: str = CORPUS) -> str:
    """The checks of the haploinsufficiency rules (``criteria`` = ``HI`` or empty), or a grade by the
    given criteria."""
    tail = HI_CHECKS if criteria in ("", HI) else GRADE_REPLY.format(criteria=criteria)
    return JUDGE_SYSTEM.format(corpus=corpus) + tail.replace("{{", "{").replace("}}", "}")


def grade_of(checks: dict) -> str:
    if not checks["subject"]:
        return "N"
    if checks["eye_disease"] and checks["explicit"] and not checks["contradicted"]:
        return "A"
    if checks["eye_disease"] and (checks["explicit"] or checks["hedged"]):
        return "B"
    return "N"


JUDGED_COLUMNS = ("gene", "grade", "papers", "passages", "pmids", "quote", "quote_verified", "reason", "checks",
                  "evidence_rows")  # fmt: skip


def pick_rows(rows: list[dict], max_rows: int = MAX_ROWS, max_chars: int = MAX_CHARS) -> list[dict]:
    """One row per paper first (in evidence order), then the others, within the row and size limits."""
    seen: set[str] = set()
    first, rest = [], []
    for r in rows:
        (rest if r["pmid"] in seen else first).append(r)
        seen.add(r["pmid"])
    out, size = [], 0
    for r in first + rest:
        n = len(r.get("sentences") or "")
        if len(out) >= max_rows or (out and size + n > max_chars):
            break
        out.append(r)
        size += n
    return out


def judge_messages(question: str, gene: str, written: str, rows: list[dict], *, criteria: str = HI,
                   corpus: str = CORPUS) -> list[dict]:  # fmt: skip
    blocks = []
    for i, r in enumerate(rows, 1):
        title = f" — {r['title']}" if r.get("title") else ""
        blocks.append(f"[{i}] PMID {r['pmid']}, p. {r.get('page') or '?'}{title}\n{r['sentences']}")
    user = (f"Question: {question}\nGene: {gene} (written in these sentences as {written})\n\n"
            + "\n\n".join(blocks))  # fmt: skip
    return [{"role": "system", "content": system_prompt(criteria, corpus)},
            {"role": "user", "content": user}]  # fmt: skip


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).strip(" \"'“”")


def _flag(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "yes", "1")


def parse_judgement(raw: str, rows: list[dict], criteria: str = HI) -> dict | None:
    """``grade``, ``pmids`` (only the given ones), ``quote``, ``quote_verified`` (the quote is in the given
    sentences), ``reason`` and, for the haploinsufficiency rules, ``checks`` from which the grade follows;
    None when the reply holds neither a grade nor the checks."""
    obj = json_object(raw)
    if not obj:
        return None
    checks = ""
    if criteria in ("", HI):
        if not all(k in obj for k in CHECKS):
            return None
        flags = {k: _flag(obj[k]) for k in CHECKS}
        grade = grade_of(flags)
        checks = " ".join(f"{k}={'yes' if v else 'no'}" for k, v in flags.items())
    else:
        grade = str(obj.get("grade") or "").strip().upper()[:1]
        if grade not in GRADES:
            return None
    given = {r["pmid"] for r in rows}
    pmids = [str(p).strip() for p in obj.get("pmids") or [] if str(p).strip() in given]
    quote = _norm(obj.get("quote"))
    text = _norm(" ".join(r.get("sentences") or "" for r in rows))
    return {"grade": grade, "pmids": list(dict.fromkeys(pmids)), "quote": quote,
            "quote_verified": bool(quote) and quote in text, "reason": _norm(obj.get("reason"))[:400],
            "checks": checks}  # fmt: skip


def estimate(question: str, table: list[dict], by_gene: dict[str, list[dict]], *, criteria: str = HI,
             corpus: str = CORPUS, price_in: float = 0.10, price_out: float = 0.50, workers: int = 48) -> dict:  # fmt: skip
    """Calls, tokens and cost of judging ``table``'s genes; no call is made."""
    chars = sum(len(m["content"]) for r in table
                for m in judge_messages(question, r["gene"], r["written_as"], pick_rows(by_gene[r["gene"]]),
                                        criteria=criteria, corpus=corpus))  # fmt: skip
    tokens_in = round(chars / CHARS_PER_TOKEN)
    lo, hi = (len(table) * r for r in OUT_PER_GENE)
    return {"genes": len(table), "calls": len(table), "input_tokens": tokens_in, "output_tokens": [lo, hi],
            "cost_usd": [round((tokens_in * price_in + o * price_out) / 1e6, 4) for o in (lo, hi)],
            "minutes": round(math.ceil(len(table) / max(1, workers)) * 8 / 60, 1)}  # fmt: skip


def judge_genes(call: Call, question: str, table: list[dict], by_gene: dict[str, list[dict]], out: Path, *,
                criteria: str = HI, corpus: str = CORPUS, workers: int = 48) -> dict:  # fmt: skip
    """Judge every gene of ``table``, ``workers`` calls at a time, appending each to ``out/judge.jsonl``;
    genes an earlier run judged are skipped. An unreadable reply is answered with ``FIX_REPLY``, up to
    ``ATTEMPTS`` calls in all; a gene that still fails is reported, and a later run tries it again."""
    out.mkdir(parents=True, exist_ok=True)
    log = out / "judge.jsonl"
    done: dict[str, dict] = {}
    usage: dict = {}
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            for use in row.get("usage") or []:
                add_usage(usage, use)
            if row.get("judgement"):
                done[row["gene"]] = row["judgement"]
    todo = [r for r in table if r["gene"] not in done]
    lock = threading.Lock()
    failed: list[str] = []
    run_usage: dict = {}
    t0 = time.time()

    def one(r: dict) -> None:
        rows = pick_rows(by_gene[r["gene"]])
        msgs = judge_messages(question, r["gene"], r["written_as"], rows, criteria=criteria, corpus=corpus)
        uses, got = [], None
        for _ in range(ATTEMPTS):
            try:
                text, use = call(msgs)
            except Exception as exc:  # reported as failed, never fatal
                print(f"[judge] {r['gene']}: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
                break
            uses.append(use)
            got = parse_judgement(text, rows, criteria)
            if got:
                break
            # an unreadable reply: say so and ask again, keeping the reply in the conversation
            msgs = [*msgs, {"role": "assistant", "content": text.strip() or "(no reply)"},
                    {"role": "user", "content": FIX_REPLY}]  # fmt: skip
        if got:
            got["evidence_rows"] = len(rows)
        with lock:
            for use in uses:
                add_usage(usage, use)
                add_usage(run_usage, use)
            if got:
                done[r["gene"]] = got
            else:
                failed.append(r["gene"])
            entry = {"gene": r["gene"], "judgement": got, "usage": uses}
            if not got and uses:
                entry["last_reply"] = str(msgs[-2]["content"])[:2000] if len(msgs) > 2 else ""  # for diagnosis
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as ex:
            list(ex.map(one, todo))
    return {"judged": done, "failed": sorted(set(failed) - set(done)), "usage": usage, "run_usage": run_usage,
            "wall_s": round(time.time() - t0)}  # fmt: skip


def write_judgement(out: Path, table: list[dict], run: dict, settings: dict) -> dict:
    """``judged.csv`` (A, then B, then N; by papers within a grade) and ``judgement.json``."""
    rank = {g: i for i, g in enumerate(GRADES)}
    info = {r["gene"]: r for r in table}
    rows = []
    for gene, j in run["judged"].items():
        t = info.get(gene, {})
        rows.append({"gene": gene, "grade": j["grade"], "papers": int(t.get("papers") or 0),
                     "passages": int(t.get("passages") or 0), "pmids": " ".join(j["pmids"]), "quote": j["quote"],
                     "quote_verified": j["quote_verified"], "reason": j["reason"], "checks": j.get("checks", ""),
                     "evidence_rows": j.get("evidence_rows", 0)})  # fmt: skip
    rows.sort(key=lambda r: (rank[r["grade"]], -r["papers"], r["gene"]))
    with (out / "judged.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=JUDGED_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    counts = {g: sum(1 for r in rows if r["grade"] == g) for g in GRADES}
    summary = {**settings, "genes": len(table), "judged": len(rows), "failed": run["failed"], "grades": counts,
               "unverified_quotes": sum(1 for r in rows if not r["quote_verified"] and r["grade"] != "N"),
               "usage": {**run["usage"], "cost_usd": round(cost(run["usage"], settings["price_in"],
                                                                settings["price_out"]), 4)},
               "this_run": {**run["run_usage"], "wall_s": run["wall_s"]}}  # fmt: skip
    (out / "judgement.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    return summary


def read_judged(out: Path) -> list[dict]:
    with (out / "judged.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def gene_list(text: str) -> list[str]:
    return [g for g in re.split(r"[,\s]+", text or "") if g]
