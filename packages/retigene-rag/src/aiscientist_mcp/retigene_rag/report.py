"""A Markdown report of one curation: the gene table (A, then B apart), its evidence, and what the run cost."""

from __future__ import annotations

import json
from pathlib import Path

from .curate import read_csv

KINDS = {"retinal": "retinal", "other_ocular": "other ocular", "syndromic_with_eye": "syndrome with an eye disease"}


def _cell(text: str, limit: int = 400) -> str:
    t = " ".join(str(text or "").split()).replace("|", "\\|")
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _table(rows: list[dict]) -> list[str]:
    out = ["| Gene | Phenotype | Quote | PMID | Page | Paper |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        kind = KINDS.get(r.get("phenotype_kind", ""), r.get("phenotype_kind", ""))
        phen = _cell(r.get("phenotype", ""), 120) + (f" ({kind})" if kind else "")
        out.append(f"| {r['gene']} | {phen} | {_cell(r.get('quote', ''))} | {r.get('pmid', '')} | {r.get('page', '')} "
                   f"| {_cell(r.get('citation') or r.get('title', ''), 160)} |")  # fmt: skip
    return out


def write(d: Path) -> tuple[Path, str]:
    cur = json.loads((d / "curation.json").read_text(encoding="utf-8"))
    genes = read_csv(d / "genes.csv")
    a = [r for r in genes if r["grade"] == "A"]
    b = [r for r in genes if r["grade"] == "B"]
    t = cur["total"]
    idx = cur.get("index") or {}
    where = f"{idx['dataset']} {idx['version']}" if idx.get("version") else idx.get("fingerprint", "")
    lines = [
        f"# {cur['question']}",
        "",
        f"From the RetiGene papers ({cur['corpus']['papers']:,} papers, {cur['corpus']['sentences']:,} sentences; "
        f"index {where}). Every sentence that states {cur['property']} in words or holds the search terms was read "
        f"({cur['found']['sentences']:,} sentences naming {cur['found']['genes']} genes), and each gene was graded "
        "from its own sentences: A, stated without a hedge for a retinal or ocular disease or a syndrome with one; "
        "B, hedged, contested, or one of several mechanisms; N, not supported.",
        "",
        f"## Genes graded A ({len(a)})",
        "",
        *(_table(a) if a else ["None."]),
        "",
        f"## Genes graded B, listed apart ({len(b)})",
        "",
        *(_table(b) if b else ["None."]),
        "",
        f"{cur['grades'].get('N', 0)} genes were graded N. `genes.csv` holds every gene with its reason; "
        "`sentences.csv` holds every sentence read, with the checks behind the grade.",
    ]
    if cur.get("code_adjusted"):
        lines += ["", "Grades the rules changed after the review model's answer:", ""]
        lines += [f"- {g}: {why}" for g, why in sorted(cur["code_adjusted"].items())]
    ev = d / "evaluation.json"
    if ev.is_file():
        s = json.loads(ev.read_text(encoding="utf-8"))
        lines += ["", "## Against the curator reference of 2026-11-04", "",
                  f"A genes listed as A: {s['A_recall']} (ocular {s['A_recall_ocular']}, syndromic "
                  f"{s['A_recall_syndromic']}); B genes listed apart: {s['B_listed_apart']}; N genes listed: "
                  f"{', '.join(s['N_listed']) or 'none'}."]  # fmt: skip
    m = cur["models"]
    lines += ["", "## Run", "",
              f"Check model {m['check_model']} (read each gene {m['check_passes']} times), review model "
              f"{m['review_model']}, effort {m['effort'] or 'default'}, rules {cur['rules']} (revision "
              f"{cur['rules_revision']}). {t['seconds']:.0f} s, {t['calls']:,} model calls ({t['cached']:,} answered "
              f"from the cache), {t['input_tokens']:,} input and {t['output_tokens']:,} output tokens, "
              f"${t['cost_usd']:.2f}."]  # fmt: skip
    text = "\n".join(lines) + "\n"
    path = d / "report.md"
    path.write_text(text, encoding="utf-8")
    return path, text
