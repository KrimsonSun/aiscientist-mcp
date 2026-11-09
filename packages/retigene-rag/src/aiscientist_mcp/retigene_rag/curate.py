"""The corpus-wide curation of one gene property: the job behind ``submit_curation``.

1. Every sentence of the corpus that states the property in words (the rules' own wording, always) or
   holds one of the agent's terms, every hit taken, grouped by the gene named in it or next to it.
2. ``grading.grade_genes``: the check model reads each gene's sentences twice against the rules, the
   review model grades the genes with a claim A / B / N by the curator's definitions, and the code
   bounds the grade on verified quotes.
3. Two tables in the curation folder: ``genes.csv`` (one row per gene) and ``sentences.csv`` (one row per
   checked sentence), each with a verbatim quote, PMID and page; ``requests.csv`` (what each term found)
   and ``curation.json`` (time, calls, tokens and cost of each model).

A curation is kept by its id: the terms, the rules, the models and the index fingerprint. Asking again
with the same ones reuses it, and the model replies are cached across curations, so a reworded question
or a corpus update reads only the genes whose sentences changed.

    python -m aiscientist_mcp.retigene_rag.curate --dir <curation folder>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__, grading
from . import corpus as C
from . import model as M

PROPERTY = "haploinsufficiency"
RULES_REVISION = 17  # the sentence-curation rules of RCHENLAB/AiScientist #99
MAX_TERMS = 12
REQUEST_COLUMNS = ("term", "kind", "source", "n_sentences")
GENE_COLUMNS = (*grading.GRADE_COLUMNS, "citation", "title", "year", "doi")
SENTENCE_COLUMNS = (*grading.SENTENCE_COLUMNS, "citation", "title")
CHARS_PER_TOKEN = 2.3  # measured on whole reads of the RetiGene corpus (Claude Haiku 5.5)


def rules_digest() -> str:
    """The prompts, vocabularies, floor wording and columns: what decides the tables besides the corpus
    and the models."""
    parts = [
        PROPERTY,
        str(RULES_REVISION),
        C.DOSAGE_WORDING.pattern,
        *grading.fingerprint_parts(),
        ",".join(GENE_COLUMNS),
        ",".join(SENTENCE_COLUMNS),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def curation_id(terms: list[str], models: dict, fingerprint: str) -> str:
    key = json.dumps([rules_digest(), sorted(t.lower() for t in terms), models, fingerprint,
                      __version__.rsplit(".", 1)[0]], sort_keys=True)  # fmt: skip
    return f"{PROPERTY}-{hashlib.sha256(key.encode()).hexdigest()[:12]}"


class Loaded:
    """The sentence table, its text index, the HGNC table and the paper titles, loaded once per process."""

    def __init__(self, index):
        self.sentences = C.read_sentences(index.sentences)
        self.text_index = C.TextIndex(s.get("text", "") for s in self.sentences)
        self.hgnc_path = index.hgnc()
        self.known = C.known_gene_symbols(self.hgnc_path)
        self.hgnc = C.Hgnc(self.hgnc_path)
        self.manifest = C.read_manifest(index.manifest)
        self.titles = {p: m["title"] for p, m in self.manifest.items() if m.get("title")}
        self.n_papers = len({str(s.get("pmid")) for s in self.sentences})


def find(loaded: Loaded, agent_terms: list[str]) -> tuple[list[C.Term], dict[str, list[C.SentenceHit]], dict]:
    """The terms (the floor first), the sentences grouped by approved symbol, and what the search found."""
    terms = [C.Term(C.DOSAGE_TERM.text, C.DOSAGE_TERM.kind, C.DOSAGE_TERM.pattern, source="floor")]
    terms += [t for t in C.parse_terms(agent_terms, "agent", loaded.known) if t.kind != "gene"][:MAX_TERMS]
    hits = C.scan(loaded.sentences, terms, loaded.text_index, loaded.known)
    groups = C.group_by_gene(hits)
    merged, status = C.merge_spellings(groups, loaded.hgnc)
    found = {"sentences": len(hits), "papers": len({h.pmid for h in hits}),
             "names": len([g for g in groups if g]), "genes": len(merged),
             "sentences_without_gene": len(groups.get("", [])),
             "skipped_names": sorted(r for r, s in status.items() if s in ("unknown", "missing") or
                                     s.startswith("ambiguous"))}  # fmt: skip
    return terms, groups, found | {"merged": merged}


def estimate(merged: dict[str, list[C.SentenceHit]], titles: dict, models: dict, workers: int) -> dict:
    """Calls, tokens, cost and minutes of grading ``merged``, before any call, as a low-high range. The check
    pass is counted from its requests; the review is assumed for a quarter to a half of the genes (36% on
    the haploinsufficiency question). The system prompts are read from the provider's cache after the first
    call (a tenth of the input price), and the output per sentence and reading is the range measured on
    RetiGene with Claude Haiku 5.5 at low effort (about 320 tokens)."""
    passes = int(models["check_passes"])
    calls = user = sentences = 0
    for gene, hits in merged.items():
        for b in range(0, len(hits), grading.MAX_SENTENCES_PER_CALL):
            msgs = grading.gene_check_messages(gene, hits[b : b + grading.MAX_SENTENCES_PER_CALL], titles=titles)
            user += len(msgs[1]["content"])
            calls += 1
        sentences += len(hits)
    genes = len(merged)
    sys_check, sys_review = (len(x) / CHARS_PER_TOKEN for x in (grading.GENE_CHECK_SYSTEM, grading.GENE_REVIEW_SYSTEM))
    pc, pr = M.price(models["check_model"]), M.price(models["review_model"])
    out = {"genes": genes, "sentences": sentences}
    rows = []
    for share, o_sent, o_rev, secs in ((0.25, 250, 300, 2.0), (0.5, 400, 700, 4.0)):
        reviewed = math.ceil(genes * share)
        check = {"in": user * passes / CHARS_PER_TOKEN, "cached": calls * passes * sys_check,
                 "out": sentences * passes * o_sent}  # fmt: skip
        review = {"in": share * user * 1.6 / CHARS_PER_TOKEN, "cached": reviewed * sys_review, "out": reviewed * o_rev}
        usd = sum(
            (u["in"] * p[0] + u["cached"] * p[0] * 0.1 + u["out"] * p[1]) / 1e6 for u, p in ((check, pc), (review, pr))
        )
        n = calls * passes + reviewed
        rows.append({"calls": n, "input_tokens": round(check["in"] + check["cached"] + review["in"] + review["cached"]),
                     "output_tokens": round(check["out"] + review["out"]), "cost_usd": round(usd, 3),
                     "minutes": round((n * secs / max(1, workers) + 40) / 60, 1)})  # fmt: skip
    return out | {k: [rows[0][k], rows[1][k]] for k in rows[0]}


def _write(path: Path, rows: list[dict[str, Any]], columns) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def run(d: Path, index, cache_dir: Path | None, loaded: Loaded | None = None) -> dict:
    """The curation described by ``d/request.json``; writes the tables and ``curation.json`` into ``d``."""
    req = json.loads((d / "request.json").read_text(encoding="utf-8"))
    models = req["models"]
    t0 = time.time()
    loaded = loaded or Loaded(index)
    t1 = time.time()
    terms, groups, found = find(loaded, req["terms"])
    found.pop("merged")
    t2 = time.time()
    meter = M.Meter()
    cache = M.ReplyCache(cache_dir)
    progress = d / "progress.json"
    lock = threading.Lock()
    done = {"check": 0, "review": 0}

    def counted(role: str, call: M.Complete) -> M.Complete:
        def wrapped(messages):
            text = call(messages)
            with lock:
                done[role] += 1
                if (done["check"] + done["review"]) % 10 == 0:  # often enough for curation_status
                    tmp = progress.with_suffix(".tmp")
                    tmp.write_text(json.dumps({"model_calls": done, "estimate_calls": req["estimate"]["calls"]}))
                    tmp.replace(progress)
            return text

        return wrapped

    def C_(model):
        return M.complete(models["provider"], model, effort=models["effort"], meter=meter, cache=cache)

    check, review = counted("check", C_(models["check_model"])), counted("review", C_(models["review_model"]))
    log_lines: list[str] = []
    gene_rows, sent_rows = grading.grade_genes(groups, check, review, hgnc=loaded.hgnc, titles=loaded.titles,
                                               review_model=models["review_model"], log=log_lines.append,
                                               workers=int(req.get("workers") or 16),
                                               check_passes=int(models["check_passes"]))  # fmt: skip
    t3 = time.time()
    (d / "grading.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    for r in gene_rows:
        r.update({k: loaded.manifest.get(r["pmid"], {}).get(k, "") for k in ("citation", "title", "year", "doi")})
    for r in sent_rows:
        r.update({k: loaded.manifest.get(r["pmid"], {}).get(k, "") for k in ("citation", "title")})
    _write(d / "genes.csv", gene_rows, GENE_COLUMNS)
    _write(d / "sentences.csv", sent_rows, SENTENCE_COLUMNS)
    _write(d / "requests.csv", [{"term": t.text, "kind": t.kind, "source": t.source, "n_sentences": t.n_sentences}
                                for t in terms], REQUEST_COLUMNS)  # fmt: skip
    usage = meter.report()
    grades = {g: sum(1 for r in gene_rows if r["grade"] == g) for g in ("A", "B", "N", "unresolved")}
    out = {
        "curation_id": d.name,
        "question": req["question"],
        "property": PROPERTY,
        "rules": rules_digest(),
        "rules_revision": RULES_REVISION,
        "index": req["index"],
        "models": models,
        "terms": [{"term": t.text, "source": t.source, "n_sentences": t.n_sentences} for t in terms],
        "corpus": {"papers": loaded.n_papers, "sentences": len(loaded.sentences)},
        "found": found,
        "grades": grades,
        "genes_A": [r["gene"] for r in gene_rows if r["grade"] == "A"],
        "genes_B": [r["gene"] for r in gene_rows if r["grade"] == "B"],
        "unresolved": [r["gene"] for r in gene_rows if r["grade"] == "unresolved"],
        "code_adjusted": {r["gene"]: r["code_adjusted"] for r in gene_rows if r["code_adjusted"]},
        "usage": usage["by_model"],
        "total": {
            **usage["total"],
            "seconds": round(time.time() - t0, 1),
            "seconds_by_stage": {"load": round(t1 - t0, 1), "search": round(t2 - t1, 1), "grade": round(t3 - t2, 1)},
        },
        "server_version": __version__,
    }
    (d / "curation.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def main(argv=None):
    from aiscientist_mcp.common import jobs

    from .settings import Settings

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, type=Path)
    a = ap.parse_args(argv)
    cfg = Settings.from_env()
    jobs.record_done(a.dir, lambda: run(a.dir, cfg.index.check(), cfg.cache_dir))


if __name__ == "__main__":
    main()
