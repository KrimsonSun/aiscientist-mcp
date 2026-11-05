"""Grade the genes an agent found: each gene from its own sentences, one model call per gene.

A gene's evidence is every corpus sentence that names it (its approved symbol, or an alias with a digit
that belongs to it alone) and holds one of the agent's search terms or a mechanism word, grouped by
passage. The judge model answers the grading rules as checks for every gene; the review model answers
them again for every gene the first pass graded A or B, and its grade is final. On the haploinsufficiency
question the first pass already graded all 15 reference genes A, and reviewing only its A and B genes
(55 of 105) gave the same result as reviewing every gene with a claim (85), for $0.46 instead of $0.64.

Outputs, in ``<data_dir>/<grading_id>/``: ``genes.csv`` (one row per gene), ``sentences.csv`` (one row
per sentence that names an A or B gene), ``grading.json`` (time, calls, tokens and cost of each pass),
and each pass's record in ``first/`` and ``review/``. A grading resumes from those records.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from . import __version__, core, judge
from .model import caller
from .search import Corpus, fold

PRICES = {  # USD per million input / output tokens
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "offline-test": (0.0, 0.0),
}
# Words that carry a gene's mechanism, for or against a loss-of-one-copy claim (matched folded).
MECHANISM_WORDS = ("haploinsufficien", "loss of function", "dosage", "dominant negative", "gain of function",
                   "does not cause", "one copy", "null allele", "truncat", "nonsense mediated", "isoform")  # fmt: skip
MAX_GENES = 300
GENE_COLUMNS = ("gene", "grade", "claim", "papers", "passages", "pmids", "quote", "quote_pmid", "quote_page",
                "quote_title", "quote_verified", "checks", "reason", "first_pass", "reviewed_by")  # fmt: skip
SENTENCE_COLUMNS = ("gene", "grade", "sentence", "pmid", "page", "chunk", "citation", "title", "year")
CLAIM = {"A": "stated", "B": "hedged or contested", "N": "not supported"}


def price(model: str) -> tuple[float, float]:
    return PRICES.get(model, (0.0, 0.0))


def genes_named(c: Corpus, terms: list[str], symbols: dict[str, str]) -> list[str]:
    """Every gene (approved symbol) named, as a symbol or a digit alias, in the sentences that hold a term."""
    ts = [t for t in (fold(x) for x in terms) if len(t) >= 4]
    out: dict[str, None] = {}
    for _, s, f in c.folded:
        if any(t in f for t in ts):
            for tok in core._GENE_RX.findall(s):
                if tok in symbols and tok not in core.NOT_GENES:
                    out[symbols[tok]] = None
    return list(out)


def resolve_genes(genes: list[str], symbols: dict[str, str]) -> tuple[dict[str, set[str]], list[str]]:
    """Approved symbol -> the forms it is written as in the corpus (itself and its digit aliases), and the
    names HGNC does not know."""
    forms_of: dict[str, set[str]] = defaultdict(set)
    for written, approved in symbols.items():
        forms_of[approved].add(written)
    out: dict[str, set[str]] = {}
    unknown = []
    for g in dict.fromkeys(x.strip() for x in genes if x.strip()):
        approved = symbols.get(g) or symbols.get(g.upper())
        if approved:
            out[approved] = forms_of[approved] | {approved}
        else:
            unknown.append(g)
    return out, unknown


def evidence(c: Corpus, forms: dict[str, set[str]], terms: list[str], manifest: dict) -> dict[str, list[dict]]:
    """Gene -> its evidence rows: per passage, the sentences that name the gene and hold a term or a mechanism word."""
    words = [w for w in dict.fromkeys(fold(t) for t in [*terms, *MECHANISM_WORDS]) if len(w) >= 4]
    by_form = {f: g for g, fs in forms.items() for f in fs}
    hits: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for i, s, f in c.folded:
        if not any(w in f for w in words):
            continue
        for g in {by_form[t] for t in core._GENE_RX.findall(s) if t in by_form}:
            hits[g][i].append(s)
    rows: dict[str, list[dict]] = {}
    for g in forms:
        rows[g] = []
        for i, ss in hits.get(g, {}).items():
            p, m = c.passages[i], manifest.get(c.passages[i]["pmid"], {})
            rows[g].append({"pmid": p["pmid"], "chunk": p["chunk"], "page": p["page"], "title": m.get("title", ""),
                            "citation": m.get("citation") or f"PMID {p['pmid']}", "year": m.get("year", ""),
                            "sentences": " ".join(ss)})  # fmt: skip
    return rows


def grading_id(
    question: str, genes: list[str], terms: list[str], criteria: str, settings: dict, fingerprint: str
) -> str:
    key = json.dumps([question, sorted(genes), sorted(terms), criteria, settings, fingerprint,
                      __version__.rsplit(".", 1)[0]], sort_keys=True)  # fmt: skip
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:40].strip("-") or "grading"
    return f"{slug}-{hashlib.sha256(key.encode()).hexdigest()[:10]}"


def _stage(name, run, model, seconds, **extra):
    u = run["usage"]
    return {"stage": name, "model": model, "calls": u.get("calls", 0), "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0), "cost_usd": round(core.cost(u, *price(model)), 4),
            "seconds": round(seconds, 1), **extra}  # fmt: skip


def _grades(judged: dict) -> dict:
    return {g: sum(1 for j in judged.values() if j["grade"] == g) for g in judge.GRADES}


def _guard(spent: float, next_hi: float, limit: float, what: str) -> None:
    if limit > 0 and spent + next_hi > limit:
        raise ValueError(f"{what} could cost up to ${next_hi:.2f}, which with ${spent:.2f} spent exceeds "
                         f"max_cost_usd={limit}; call again with a higher max_cost_usd or fewer genes")  # fmt: skip


def run(d: Path, c: Corpus, rows: dict[str, list[dict]], forms: dict[str, set[str]], st: dict) -> dict:
    """Both passes for the genes in ``rows``; writes the tables and ``grading.json`` into ``d``."""
    t0 = time.time()
    limit = float(st.get("max_cost_usd") or 0)
    crit = st.get("criteria") or judge.HI
    table = [{"gene": g, "written_as": " ".join(sorted(forms[g])), "papers": len({r["pmid"] for r in rows[g]}),
              "passages": len(rows[g])} for g in rows if rows[g]]  # fmt: skip
    empty = [g for g in rows if not rows[g]]  # no sentence names the gene with a term: N, without a model call
    stages: list[dict] = []
    fm, rm = st["judge_model"], st.get("review_model") or ""
    fe = judge.estimate(st["question"], table, rows, criteria=crit, corpus=st["corpus"], price_in=price(fm)[0],
                        price_out=price(fm)[1])  # fmt: skip
    _guard(0.0, fe["cost_usd"][1], limit, "the first pass")
    t1 = time.time()
    first = judge.judge_genes(caller(fm, st["judge_effort"]), st["question"], table, rows, d / "first", criteria=crit,
                              corpus=st["corpus"], workers=int(st["workers"]))  # fmt: skip
    judge.write_judgement(d / "first", table, first, {"model": fm, "price_in": price(fm)[0], "price_out": price(fm)[1]})
    stages.append(_stage("first_pass", first, fm, time.time() - t1, genes=len(table), grades=_grades(first["judged"])))
    final = {g: {**j, "first_pass": j["grade"], "reviewed_by": ""} for g, j in first["judged"].items()}
    for g in first["failed"]:  # graded N for now, and tried again when grade_genes is called again
        final[g] = {"grade": "N", "pmids": [], "quote": "", "quote_verified": False, "checks": "", "first_pass": "",
                    "reason": "the judge model gave no usable answer; call grade_genes again to retry this gene",
                    "reviewed_by": ""}  # fmt: skip
    failed_review: list[str] = []
    for g in empty:
        final[g] = {"grade": "N", "pmids": [], "quote": "", "quote_verified": False, "checks": "", "first_pass": "N",
                    "reason": "no sentence names the gene together with a search term or a mechanism word",
                    "reviewed_by": ""}  # fmt: skip
    if rm:
        todo = [r for r in table if first["judged"].get(r["gene"], {}).get("grade") in ("A", "B")]
        re_ = judge.estimate(st["question"], todo, rows, criteria=crit, corpus=st["corpus"], price_in=price(rm)[0],
                             price_out=price(rm)[1])  # fmt: skip
        _guard(stages[0]["cost_usd"], re_["cost_usd"][1], limit, "the review")
        t2 = time.time()
        rev = judge.judge_genes(caller(rm, st["review_effort"]), st["question"], todo, rows, d / "review",
                                criteria=crit, corpus=st["corpus"], workers=int(st["workers"]))  # fmt: skip
        judge.write_judgement(
            d / "review", todo, rev, {"model": rm, "price_in": price(rm)[0], "price_out": price(rm)[1]}
        )
        failed_review = rev["failed"]
        for g in failed_review:  # keeps its first-pass grade, and is reviewed again when grade_genes is called again
            final[g]["reason"] = f"review failed, first-pass grade kept: {final[g]['reason']}"
        for g, j in rev["judged"].items():
            final[g] = {**j, "first_pass": final[g]["first_pass"], "reviewed_by": rm}
        stages.append(_stage("review", rev, rm, time.time() - t2, genes=len(todo), grades=_grades(rev["judged"])))
    table += [{"gene": g, "written_as": " ".join(sorted(forms[g])), "papers": 0, "passages": 0} for g in empty]
    write_tables(d, table, final, rows, forms)
    total = {k: sum(s.get(k, 0) for s in stages) for k in ("calls", "input_tokens", "output_tokens")}
    out = {"grading_id": st["grading_id"], "question": st["question"], "genes": len(rows),
           "not_in_hgnc": st.get("not_in_hgnc", []), "no_evidence": empty,
           "grades": _grades(final), "stages": stages, "failed_first_pass": first["failed"],
           "failed_review": failed_review, "complete": not first["failed"] and not failed_review,
           "total": {**total, "cost_usd": round(sum(s["cost_usd"] for s in stages), 4),
                     "seconds": round(time.time() - t0, 1)}}  # fmt: skip
    (d / "grading.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return out


def write_tables(d: Path, table: list[dict], final: dict, rows: dict[str, list[dict]], forms: dict[str, set[str]]):
    """``genes.csv`` (A, then B, then N; by papers within a grade) and ``sentences.csv`` (every evidence
    sentence that names an A or B gene)."""
    info = {r["gene"]: r for r in table}
    rank = {g: i for i, g in enumerate(judge.GRADES)}
    genes = []
    for gene, j in final.items():
        src = next(
            (r for r in rows.get(gene, []) if j["quote"] and j["quote"] in " ".join(r["sentences"].split())), None
        )
        genes.append({"gene": gene, "grade": j["grade"], "claim": CLAIM[j["grade"]], "papers": info[gene]["papers"],
                      "passages": info[gene]["passages"], "pmids": " ".join(j["pmids"]), "quote": j["quote"],
                      "quote_pmid": src["pmid"] if src else "", "quote_page": src["page"] if src else "",
                      "quote_title": src["title"] if src else "", "quote_verified": j["quote_verified"],
                      "checks": j.get("checks", ""), "reason": j["reason"], "first_pass": j.get("first_pass", ""),
                      "reviewed_by": j.get("reviewed_by", "")})  # fmt: skip
    genes.sort(key=lambda r: (rank[r["grade"]], -r["papers"], r["gene"]))
    with (d / "genes.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=GENE_COLUMNS)
        w.writeheader()
        w.writerows(genes)
    with (d / "sentences.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SENTENCE_COLUMNS)
        w.writeheader()
        for g in genes:
            if g["grade"] == "N":
                continue
            for r in rows.get(g["gene"], []):
                for s in core.split_sentences(r["sentences"]):
                    if any(t in forms[g["gene"]] for t in core._GENE_RX.findall(s)):
                        w.writerow({"gene": g["gene"], "grade": g["grade"], "sentence": s, "pmid": r["pmid"],
                                    "page": r["page"], "chunk": r["chunk"], "citation": r["citation"],
                                    "title": r["title"], "year": r["year"]})  # fmt: skip


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))
