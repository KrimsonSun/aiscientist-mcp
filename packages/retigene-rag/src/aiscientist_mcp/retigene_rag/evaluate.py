"""Score a grading's genes.csv against a graded reference.

The bundled reference (``reference/hi_reference_2026-11-04.csv``) is the curator review of the 24-gene
model reference built from the RetiGene corpus: 15 genes graded A (explicit haploinsufficiency in an
eye disease: 10 retinal or other ocular, 5 syndromes with an eye feature), 7 graded B (hedged or
contested) and 2 graded N (not supported). The acceptance rules: every A gene listed as A, the B genes
listed apart from the A genes, and the N genes not listed at all.
"""

from __future__ import annotations

import csv
from importlib import resources
from pathlib import Path

DEFAULT_REFERENCE = "hi_reference_2026-11-04.csv"


def load_reference(path: str | Path = "") -> list[dict]:
    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = resources.files(__package__).joinpath("reference", DEFAULT_REFERENCE).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def score(genes: list[dict], reference: list[dict]) -> dict:
    grade = {r["gene"]: r["grade"] for r in genes}
    ref = {r["gene"]: r for r in reference}
    want_a = [g for g, r in ref.items() if r["review_grade"] == "A"]
    want_b = [g for g, r in ref.items() if r["review_grade"] == "B"]
    want_n = [g for g, r in ref.items() if r["review_grade"] == "N"]
    syndromic = [g for g in want_a if ref[g]["phenotype_kind"] == "syndromic_with_eye"]
    ocular = [g for g in want_a if g not in syndromic]

    def hits(gs, g_="A"):
        return [g for g in gs if grade.get(g) == g_]

    listed_n = [g for g in want_n if grade.get(g) in ("A", "B")]
    b_as_a = hits(want_b, "A")
    out = {
        "reference": {"A": len(want_a), "B": len(want_b), "N": len(want_n)},
        "A_recall": f"{len(hits(want_a))}/{len(want_a)}",
        "A_recall_ocular": f"{len(hits(ocular))}/{len(ocular)}",
        "A_recall_syndromic": f"{len(hits(syndromic))}/{len(syndromic)}",
        "A_missed": {g: grade.get(g, "not a candidate") for g in want_a if grade.get(g) != "A"},
        "B_listed_apart": f"{len(hits(want_b, 'B'))}/{len(want_b)}",
        "B_listed_as_A": b_as_a,
        "B_not_listed": [g for g in want_b if grade.get(g) not in ("A", "B")],
        "N_listed": listed_n,
        "A_outside_reference": sorted(g for g, v in grade.items() if v == "A" and g not in ref),
        "B_outside_reference": sum(1 for g, v in grade.items() if v == "B" and g not in ref),
    }
    out["passes"] = {
        "all_A_listed": len(hits(want_a)) == len(want_a),
        "all_B_listed_apart": len(hits(want_b, "B")) == len(want_b),
        "N_not_listed": not listed_n,
    }
    return out


def read_genes(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))
