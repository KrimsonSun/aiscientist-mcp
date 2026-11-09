"""Score a curation's genes.csv against a graded reference.

The bundled reference (``reference/reference_genes_reviewed.csv``) is the curator review of 2026-11-04 of
the 24-gene model reference built from the RetiGene corpus, confirmed by Dr. Chen, as in
``reference_genes_reviewed.xlsx`` (RCHENLAB/AiScientist #95), without the quotes and review notes: 15
genes graded A (explicit haploinsufficiency in an eye disease: 10 retinal or other ocular, 5 syndromes with
an eye feature), 7 graded B (hedged or contested) and 2 graded N (not supported).

The acceptance rules: every A gene listed as A; the B genes listed apart from the A genes (as B, or not
listed: never as A); the N genes not listed at all (neither A nor B).
"""

from __future__ import annotations

import csv
from importlib import resources
from pathlib import Path

DEFAULT_REFERENCE = "reference_genes_reviewed.csv"
OCULAR = ("retinal", "other_ocular")


def load_reference(path: str | Path = "") -> list[dict]:
    if path:
        text = Path(path).read_text(encoding="utf-8-sig")
    else:
        text = resources.files(__package__).joinpath("reference", DEFAULT_REFERENCE).read_text(encoding="utf-8")
    rows = list(csv.DictReader(text.splitlines()))
    if not rows or not {"gene", "review_grade", "phenotype_kind"} <= set(rows[0]):
        raise ValueError("a reference needs the columns gene, review_grade and phenotype_kind")
    return rows


def score(genes: list[dict], reference: list[dict]) -> dict:
    got = {r["gene"]: r["grade"] for r in genes}
    ref = {r["gene"]: r for r in reference}
    want_a = [g for g, r in ref.items() if r["review_grade"] == "A"]
    want_b = [g for g, r in ref.items() if r["review_grade"] == "B"]
    want_n = [g for g, r in ref.items() if r["review_grade"] == "N"]
    ocular = [g for g in want_a if ref[g]["phenotype_kind"] in OCULAR]
    syndromic = [g for g in want_a if g not in ocular]

    def as_a(gs):
        return [g for g in gs if got.get(g) == "A"]

    b_as_a = as_a(want_b)
    n_listed = [g for g in want_n if got.get(g) in ("A", "B")]
    out = {
        "reference": {
            "A": len(want_a),
            "A_ocular": len(ocular),
            "A_syndromic": len(syndromic),
            "B": len(want_b),
            "N": len(want_n),
        },
        "A_recall": f"{len(as_a(want_a))}/{len(want_a)}",
        "A_recall_ocular": f"{len(as_a(ocular))}/{len(ocular)}",
        "A_recall_syndromic": f"{len(as_a(syndromic))}/{len(syndromic)}",
        "A_missed": {g: got.get(g, "not listed") for g in want_a if got.get(g) != "A"},
        "B_listed_apart": f"{len(want_b) - len(b_as_a)}/{len(want_b)}",
        "B_graded": {g: got.get(g, "not listed") for g in want_b},
        "B_listed_as_A": b_as_a,
        "N_listed": n_listed,
        "A_outside_reference": sorted(g for g, v in got.items() if v == "A" and g not in ref),
        "B_outside_reference": sum(1 for g, v in got.items() if v == "B" and g not in ref),
    }
    out["passes"] = {"all_A_listed": len(as_a(want_a)) == len(want_a), "B_listed_apart": not b_as_a,
                     "N_not_listed": not n_listed}  # fmt: skip
    out["passed"] = all(out["passes"].values())
    return out
