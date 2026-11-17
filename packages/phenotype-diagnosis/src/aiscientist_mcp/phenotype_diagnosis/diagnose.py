# ruff: noqa: RUF001
"""The ``diagnose_disease`` tool. Documentation: ``descriptions/diagnose_disease.md``.

Split out of ``tools/phenotype_dx.py`` by ``scripts/refactor/split_tools.py``: the code is the old
module's text, verbatim, with only the imports rewritten.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .lirical import DiseaseCandidate, run_lirical

# ClinGen gene-disease validity tiers — the currency of the LITERATURE track, strongest first. Disputed/
# Refuted are contradictory evidence: they rank below the positive tiers so they never pass a positive
# threshold (they can only argue AGAINST a call, never rescue one).
CLINGEN_TIERS = ("DEFINITIVE", "STRONG", "MODERATE", "LIMITED", "DISPUTED", "REFUTED", "NONE")


_TIER_RANK = {t: i for i, t in enumerate(CLINGEN_TIERS)}


def tier_at_least(tier: str, threshold: str) -> bool:
    """True if ``tier`` is at least as strong as ``threshold`` (DEFINITIVE strongest). Unknown → NONE."""
    return _TIER_RANK.get((tier or "NONE").upper(), _TIER_RANK["NONE"]) <= _TIER_RANK.get(
        (threshold or "NONE").upper(), _TIER_RANK["NONE"]
    )


def paperqa2_evidence(
    gene: str, disease: str, hpo_terms: Iterable[str] = (), *, runner: Callable[..., dict] | None = None
) -> dict[str, Any]:
    """The literature EVIDENCE track (PaperQA2). Placeholder until the corpus/QA is wired: returns
    ``status='not_enabled'`` unless a ``runner`` implementing the contract is injected. Contract: given
    (gene, candidate disease, HPO terms) it returns
    ``{association: bool, clingen_tier: <CLINGEN_TIERS>, evidence: [{pmid, quote, study_type}]}``,
    GROUNDED in retrieved passages — never an ungrounded model number, never a probability."""
    if runner is None:
        return {
            "status": "not_enabled",
            "gene": gene,
            "disease": disease,
            "association": False,
            "clingen_tier": "NONE",
            "evidence": [],
        }
    out = runner(gene=gene, disease=disease, hpo_terms=list(hpo_terms)) or {}
    tier = str(out.get("clingen_tier") or "NONE").upper()
    if tier not in _TIER_RANK:
        tier = "NONE"
    # ``evidence_status``/``notes`` are grading PROVENANCE, not part of the original contract: they
    # say WHY a record is negative — nothing retrieved vs. retrieved-and-unsupportive vs. refuted —
    # which is the distinction ``adjudicate`` needs and a bare ``association=False`` cannot carry.
    # Absent (a runner written to the plain contract) they default to "", which adjudicate reads as
    # "not asked" and scores as LIRICAL-only, so an older runner behaves exactly as before.
    return {
        "status": "ok",
        "gene": gene,
        "disease": out.get("disease") or disease,
        "association": bool(out.get("association")),
        "clingen_tier": tier,
        "evidence": list(out.get("evidence") or []),
        "evidence_status": str(out.get("evidence_status") or ""),
        "notes": list(out.get("notes") or []),
    }


@dataclass
class DifferentialResult:
    ranked: list[DiseaseCandidate]  # LIRICAL-ranked primary differential (posttest_prob desc)
    literature_rescued: list[DiseaseCandidate]  # LIRICAL missed, literature-supported → for human review

    def as_dict(self) -> dict[str, Any]:
        return {
            "ranked": [c.as_dict() for c in self.ranked],
            "literature_rescued": [c.as_dict() for c in self.literature_rescued],
        }


def reconcile(
    lirical: list[DiseaseCandidate], literature: list[dict[str, Any]], *, rescue_threshold: str = "STRONG"
) -> DifferentialResult:
    """Two-track merge that does NOT blend the currencies (a calibrated probability and an evidence grade
    are different things):

    * ATTACH — literature evidence (ClinGen tier + PMIDs) is attached to the matching LIRICAL candidate
      as SUPPORT; ``posttest_prob`` is never modified.
    * RESCUE — a literature-supported association LIRICAL did NOT surface, whose ClinGen tier is at least
      ``rescue_threshold``, is added to a SEPARATE list flagged for human review, with ``posttest_prob``
      left None (we do not fabricate a probability the model cannot calibrate).

    ``literature`` is a list of :func:`paperqa2_evidence` dicts (each carries gene/disease/tier/evidence)."""

    # Index the literature by gene. CONTRADICTORY records (DISPUTED/REFUTED) carry ``association=False``
    # but MUST be indexed too — they are the only channel by which the literature can argue against a
    # curated LIRICAL call, and dropping them (as this did originally) made a conflict invisible. They
    # still cannot RESCUE anything: ``tier_at_least`` puts them below every positive threshold.
    def _informative(ev: dict[str, Any]) -> bool:
        if ev.get("status") not in (None, "ok") or not ev.get("gene"):
            return False
        if ev.get("association") or ev.get("clingen_tier", "").upper() in ("DISPUTED", "REFUTED"):
            return True
        # A searched-but-unsupportive record is negative AND tier NONE, yet it is not the same as
        # never asking: passages came back and none supported the link. It must reach the candidate
        # so ``adjudicate`` can apply its (small) haircut. It can still never RESCUE anything —
        # ``tier_at_least("NONE", …)`` fails every positive threshold.
        return str(ev.get("evidence_status") or "") == "unsupported"

    by_gene: dict[str, dict[str, Any]] = {}
    for ev in literature:
        if _informative(ev):
            by_gene.setdefault(ev["gene"], ev)  # first informative record per gene wins

    ranked = sorted(lirical, key=lambda c: (c.posttest_prob is None, -(c.posttest_prob or 0.0)))
    seen_genes: set[str] = set()
    for c in ranked:
        seen_genes.add(c.gene)
        ev = by_gene.get(c.gene)
        if ev:
            c.evidence_tier = ev.get("clingen_tier", "")
            c.evidence_pmids = [e.get("pmid") for e in ev.get("evidence", []) if e.get("pmid")]
            c.evidence_status = str(ev.get("evidence_status") or "")
            c.sources.add("literature")

    rescued: list[DiseaseCandidate] = []
    for gene, ev in by_gene.items():
        if gene in seen_genes:
            continue
        if tier_at_least(ev.get("clingen_tier", ""), rescue_threshold):
            rescued.append(
                DiseaseCandidate(
                    disease_name=ev.get("disease") or "",
                    gene=gene,
                    evidence_tier=ev.get("clingen_tier", ""),
                    evidence_pmids=[e.get("pmid") for e in ev.get("evidence", []) if e.get("pmid")],
                    evidence_status=str(ev.get("evidence_status") or ""),
                    sources={"literature"},
                    flags=["lirical_missed_literature_supported"],
                )
            )
    return DifferentialResult(ranked=ranked, literature_rescued=rescued)


# Positive tiers map onto 0..1; contradictory tiers go NEGATIVE, which is what lets them sink a
# candidate rather than merely fail to lift it.
TIER_STRENGTH: dict[str, float] = {
    "DEFINITIVE": 1.00,
    "STRONG": 0.80,
    "MODERATE": 0.50,
    "LIMITED": 0.25,
    "DISPUTED": -0.60,
    "REFUTED": -1.00,
    "NONE": 0.0,
}


LITERATURE_WEIGHT = 0.65  # > LIRICAL_WEIGHT: the literature wins a disagreement


LIRICAL_WEIGHT = 0.35


# A candidate the corpus actively failed to support keeps LIRICAL's ranking, minus a haircut. Small
# on purpose: the corpus is BOUNDED (~the IRD papers), so "no support here" is weak evidence, not the
# same thing as the refutation branch above.
UNSUPPORTED_PENALTY = 0.85


def tier_strength(tier: str) -> float:
    """A ClinGen tier on the ranking axis: positive = support, negative = contradiction, 0 = no signal."""
    return TIER_STRENGTH.get((tier or "NONE").upper(), 0.0)


def adjudicate(
    result: DifferentialResult, *, literature_weight: float = LITERATURE_WEIGHT, lirical_weight: float = LIRICAL_WEIGHT
) -> list[dict[str, Any]]:
    """Merge the two reconciled tracks into ONE ranked differential, literature-weighted.

    Each candidate lands in exactly one ``agreement`` branch, and the branch — not a single blended
    formula — decides how it is scored:

    ``concordant``
        both tracks positive → ``literature_weight·tier + lirical_weight·posttest_prob``. The
        literature's larger share is what makes it dominate on disagreement.
    ``conflict``
        the literature DISPUTES/REFUTES a LIRICAL candidate → the same weighted sum, but the tier
        is negative, so a high LIRICAL probability is dragged below every uncontradicted candidate.
        This is the "literature wins" case, and it is flagged for the reviewer.
    ``literature_only``
        LIRICAL never surfaced it (not curated, or LIRICAL could not run) → scored on the tier
        alone. This is the gap-fill: without it these candidates are simply absent from the answer.
    ``lirical_only``
        the literature was not asked, or the corpus returned nothing (``ungraded``) → scored on
        ``posttest_prob`` alone. NO penalty: silence from a bounded corpus is absence of data.
    ``unsupported``
        passages came back and none supported the link → ``posttest_prob`` minus a small haircut.

    Returns plain dicts sorted by ``final_score`` (desc), each carrying both raw tracks so a reader
    can always see what each one contributed.
    """
    out: list[dict[str, Any]] = []

    for c in result.ranked:
        prob = c.posttest_prob or 0.0
        tier = (c.evidence_tier or "").upper()
        status = (c.evidence_status or "").lower()
        strength = tier_strength(tier)

        if status == "contradicted" or tier in ("DISPUTED", "REFUTED"):
            agreement = "conflict"
            score = literature_weight * strength + lirical_weight * prob
            note = (
                f"the literature {tier.lower()}s this association — it outranks LIRICAL's "
                f"{prob:.0%} here; review before reporting"
            )
        elif status == "unsupported":
            agreement = "unsupported"
            score = prob * UNSUPPORTED_PENALTY
            note = (
                "LIRICAL's call; the RetiGene corpus returned related passages but none supporting this "
                "gene–disease link (the RetiGene corpus is bounded — treat as weak, not as refutation)"
            )
        elif "literature" in c.sources and strength > 0:
            agreement = "concordant"
            score = literature_weight * strength + lirical_weight * prob
            note = f"both tracks agree: LIRICAL {prob:.0%}, literature {tier}"
        else:
            agreement = "lirical_only"
            score = prob
            note = (
                "LIRICAL only — the literature track was not asked or the RetiGene corpus returned "
                "nothing (absence of data, not evidence of absence)"
            )

        d = c.as_dict()
        d.update({"final_score": round(score, 4), "agreement": agreement, "decision_note": note})
        out.append(d)

    # The gap-fill: candidates LIRICAL never produced enter the SAME ranked list rather than a side
    # list, because a differential a clinician has to cross-reference against a second list is one
    # they will read as "LIRICAL's answer, plus footnotes".
    for c in result.literature_rescued:
        strength = tier_strength(c.evidence_tier)
        d = c.as_dict()
        d.update(
            {
                "final_score": round(strength, 4),
                "agreement": "literature_only",
                "decision_note": (
                    f"LIRICAL did not surface this (not curated, or LIRICAL could not "
                    f"run); the literature grades it {(c.evidence_tier or 'NONE').upper()}. "
                    f"No post-test probability exists for it — ranked on evidence alone."
                ),
            }
        )
        out.append(d)

    out.sort(key=lambda d: -d["final_score"])
    for i, d in enumerate(out, 1):
        d["rank"] = i
    return out


def _candidate_from_dict(d: dict[str, Any]) -> DiseaseCandidate:
    """Rebuild a :class:`DiseaseCandidate` from its ``as_dict()`` form (``sources`` comes back as a
    sorted list). Unknown keys are ignored so an older/newer serialised candidate still loads."""
    known = {f for f in DiseaseCandidate.__dataclass_fields__}
    kw = {k: v for k, v in d.items() if k in known}
    kw["sources"] = set(kw.get("sources") or ())
    kw["matched_hpo"] = list(kw.get("matched_hpo") or [])
    kw["evidence_pmids"] = list(kw.get("evidence_pmids") or [])
    kw["flags"] = list(kw.get("flags") or [])
    return DiseaseCandidate(**kw)


def plan_literature_queries(
    lirical_candidates: list[DiseaseCandidate], candidate_genes: Iterable[str] = (), *, max_queries: int = 6
) -> list[tuple[str, str]]:
    """Which ``(gene, disease)`` pairs to ask the literature about, highest value first.

    Two populations, and the SECOND is the point of this whole line:
      1. LIRICAL's top candidates — the literature confirms, grades, or contradicts them.
      2. Genes from the variant shortlist that LIRICAL never scored — the gap. A gene can be absent
         from LIRICAL's output because it is genuinely irrelevant, or because nobody has curated it
         into OMIM/HPOA yet, and LIRICAL cannot tell those apart. Only the literature can.

    Capped at ``max_queries``: each query is a full RAG loop, and an uncapped differential over a
    whole variant shortlist would take longer than the analysis it belongs to.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for c in lirical_candidates:
        if c.gene and c.gene not in seen:
            seen.add(c.gene)
            pairs.append((c.gene, c.disease_name))
    for g in candidate_genes:
        g = str(g).strip()
        if g and g not in seen:
            seen.add(g)
            pairs.append((g, ""))  # no curated disease name — ask the gene-centric question
    return pairs[:max_queries]


def diagnose(
    *,
    hpo_terms: Iterable[str],
    literature_runner: Callable[..., dict] | None = None,
    lirical_result: dict[str, Any] | None = None,
    candidate_genes: Iterable[str] = (),
    max_literature_queries: int = 6,
    rescue_threshold: str = "STRONG",
    hpo_labels: dict[str, str] | None = None,
    **lirical_kwargs: Any,
) -> dict[str, Any]:
    """The full differential: run BOTH tracks, then adjudicate them into one ranked answer.

    This is the entry point that makes the phenotype line able to answer a case LIRICAL alone
    cannot. Three situations it handles that ``run_lirical`` on its own does not:

    * **LIRICAL has the answer** — the literature grades and cites it (``concordant``).
    * **LIRICAL has a WRONG answer** — the retrieved literature disputes/refutes the association and
      outranks it (``conflict``), instead of a stale curation being reported with false confidence.
    * **LIRICAL has NO answer** — it is not staged, it errored, or the gene is not curated. The
      differential is then built from the literature (``literature_only``) rather than coming back
      empty, which is what "cannot diagnose" used to mean here.

    ``literature_runner`` is the ``paperqa2_evidence`` contract runner — in production, the one
    :func:`~aiscientist_mcp.phenotype_diagnosis.evidence.make_deep_literature_runner` builds over
    ``deep_literature``. Without one the result degrades to LIRICAL-only rather than failing.
    """
    notes: list[str] = []
    if lirical_result is None:
        lirical_result = run_lirical(hpo_terms=hpo_terms, labels=hpo_labels, **lirical_kwargs)

    lirical_ok = lirical_result.get("status") == "ok"
    lirical_candidates = [_candidate_from_dict(d) for d in (lirical_result.get("candidates") or [])]
    if not lirical_ok:
        notes.append(
            f"LIRICAL unavailable ({lirical_result.get('status')}): "
            f"{lirical_result.get('error') or lirical_result.get('note') or ''}".strip()
            + " — no calibrated post-test probability is available for this case."
        )
    notes.extend(lirical_result.get("phenotype_notes") or [])

    # --- the literature track ---
    literature: list[dict[str, Any]] = []
    pairs = plan_literature_queries(lirical_candidates, candidate_genes, max_queries=max_literature_queries)
    if literature_runner is None:
        notes.append(
            "no literature runner wired — LIRICAL's ranking is reported unchanged "
            "(wire deep_literature to grade, cite, and gap-fill it)."
        )
    elif not pairs:
        notes.append(
            "nothing to ask the literature about: LIRICAL produced no gene-labelled "
            "candidate and no candidate_genes were supplied."
        )
    else:
        hpo_list = [str(h).strip() for h in hpo_terms if str(h).strip()]
        for gene, disease in pairs:
            literature.append(paperqa2_evidence(gene, disease, hpo_list, runner=literature_runner))

    reconciled = reconcile(lirical_candidates, literature, rescue_threshold=rescue_threshold)
    differential = adjudicate(reconciled)

    if lirical_ok and literature:
        mode = "lirical+literature"
    elif literature:
        mode = "literature_only"
    else:
        mode = "lirical_only"

    conflicts = [d for d in differential if d["agreement"] == "conflict"]
    if conflicts:
        notes.append(
            f"{len(conflicts)} candidate(s) where the literature contradicts LIRICAL — "
            "the literature was weighted higher; see decision_note on each."
        )

    # Status follows the line's graceful-degrade convention: "error" is reserved for something that
    # actually FAILED. A host where neither track is wired has not failed — it is ``not_installed``,
    # the same word ``run_lirical`` uses, so every existing caller already knows to continue without
    # a differential instead of treating a missing deployment as a broken run.
    if differential or lirical_ok or literature:
        status = "ok"
    elif lirical_result.get("status") == "error":
        status = "error"
    else:
        status = "not_installed"

    return {
        "status": status,
        "tool": "diagnose_disease",
        "mode": mode,
        "n_candidates": len(differential),
        "differential": differential,
        "top": differential[0] if differential else None,
        "lirical": {k: v for k, v in lirical_result.items() if k != "candidates"},
        "literature": literature,
        "reconciled": reconciled.as_dict(),  # provenance: the untouched two-track view
        "notes": notes,
        "raw_data_to_llm": False,
    }
