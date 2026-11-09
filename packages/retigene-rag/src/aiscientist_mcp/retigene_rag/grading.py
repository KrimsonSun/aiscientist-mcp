"""Gene-level grading of a dosage property from the corpus' own sentences (RCHENLAB/AiScientist #99).

From AiScientist's ``extract_haploinsufficient_genes/grading.py`` (sentence curation by Ziyao Ma, rules
revision 17), unchanged but for the imports.

The unit is the gene, not the passage. ``corpus.scan`` brings every
sentence of the corpus that says the property, grouped by the gene it names; this module reads one
gene at a time: a cheap model checks each of its sentences against fixed rules (``GENE_CHECK_SYSTEM``,
Haiku), and a stronger model grades the gene A / B / N from those checks (``GENE_REVIEW_SYSTEM``,
Sonnet), only for genes with at least one dosage claim. The grades follow the curator's definitions
of the 2026-11-04 review (``reference_genes_reviewed.xlsx``, README sheet), copied into the prompt.

Why two passes over one gene rather than one pass over each passage: the seven B genes of that review
each have a sentence that passes a per-passage check (explicit, not negated, ocular); what makes them
B is in the gene's OTHER sentences (another paper says the mechanism is dominant-negative, the HI
phenotype is not the disease, the eye finding is not tied to an HI allele). The reader must see them
together. The code keeps the final word: a grade of A stands only when a verified sentence meets the
A conditions, every quote is checked verbatim against the corpus, and the tables are assembled here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from .corpus import Hgnc, SentenceHit, fold, nz
from .corpus import json_object as _json_object
from .corpus import word_in as _word

Complete = Callable[[list[dict[str, str]]], str]

# ---------------------------------------------------------------------------------------------
# Vocabularies (closed: a value outside them is read as the nearest safe one)
# ---------------------------------------------------------------------------------------------

DOSAGE_CLAIMS = ("yes", "hypothesis_only", "loose_synonym_for_lof", "no")
HEDGE_TARGETS = ("none", "mechanism", "attribution", "other")
MECHANISMS = (
    "haploinsufficiency",
    "dominant_negative",
    "gain_of_function",
    "recessive",
    "isoform_imbalance",
    "unspecified",
    "hi_ruled_out",
)
# The fixture's names (reference_genes_reviewed.csv, phenotype_kind): the three A kinds first.
PHENOTYPE_KINDS = (
    "retinal",
    "other_ocular",
    "syndromic_with_eye",
    "syndromic_eye_minor",
    "non_ocular",
    "animal_or_cell_only",
    "unclear",
)
EYE_KINDS = frozenset(PHENOTYPE_KINDS[:3])
EVIDENCE_TYPES = ("primary_finding", "background_statement", "cited_title", "reference_entry", "mentioned_nearby")
NOT_EVIDENCE = frozenset({"cited_title", "reference_entry", "mentioned_nearby"})
GRADES = ("A", "B", "N")

# What makes a sentence a claim at all (anything that puts the gene's dosage and a disease together).
CLAIM_VALUES = frozenset({"yes", "hypothesis_only", "loose_synonym_for_lof"})

# ---------------------------------------------------------------------------------------------
# Pass one: one call per gene, every sentence checked against the rules (Haiku)
# ---------------------------------------------------------------------------------------------

GENE_CHECK_SYSTEM = """You are a careful curator of inherited eye disease genetics. You judge ONLY from the text given; never use prior knowledge about the gene.

You are given ONE GENE and every sentence from a corpus of papers that names it (or names it next door) together with dosage wording. Each sentence is numbered, with its paper (PMID, page, title) and the sentence before and after it as CONTEXT. Check each numbered sentence against these rules and return one item per sentence.

DEFINITION. Haploinsufficiency (HI): a heterozygous loss-of-function allele (nonsense, frameshift, splice, deletion, NMD of the transcript) causes a disease because ONE functional copy is not enough: a dominant reduced-dosage mechanism. Not dominant-negative, not gain-of-function, not recessive (biallelic) loss.

FIELDS, per sentence:
- "dosage_claim": "yes" if the sentence states that heterozygous loss / one copy / reduced dosage / HI of THIS GENE causes, underlies or is the mechanism of a human disease or phenotype (a background statement such as "X haploinsufficiency is known to cause Y" or "has been shown to cause" counts as yes); "hypothesis_only" if HI is only the hypothesis being tested, a prediction, or an aim ("to test whether haploinsufficiency of X might..."); "loose_synonym_for_lof" if the word is used as a synonym for loss of function or for nonsense/frameshift variants ("variants that cause haploinsufficiency (loss of function)"), with no statement that one copy is insufficient; "no" otherwise, including a sentence that only calls the gene "dosage-sensitive" without saying that loss of one copy causes the disease (the gene is merely mentioned, the sentence is about dosage of something else, or it states no claim).
- "hedge": "none" or "hedged" (may, might, could, suggests, likely, possibly, proposed, consistent with, remains to be proven, we speculate). NOT hedged: "has been shown to cause", "is known to cause", "is causative of", "we have shown", "established", "fit a model in which X haploinsufficiency alone causes Y" and "favoring a model for haploinsufficiency" (the authors' conclusion), and "variants are predicted to lead to haploinsufficiency" when the prediction is about what the variant class does to the transcript (NMD) and the sentence states HI as the disease mechanism.
- "hedge_target": what the hedge word modifies: "mechanism" (whether HI is the mechanism), "attribution" (whether this gene is the one responsible), "other" (something else in the sentence, e.g. whether a deletion reached the gene in one case, or the age of onset), or "none" when not hedged. A hedge on something else does NOT make the claim hedged.
- "subject_is_gene_dosage": true if the claim is about the dosage of the gene itself (its alleles, its transcript, its protein level); false if it is about one isoform, an imbalance of isoforms, a neighbouring gene, a chromosomal region, a protein complex, or the protein product of ANOTHER gene named in the sentence. Example: "mutations in CFH resulting in haploinsufficiency of FH and FHL-1": FH is the protein encoded by CFH, so when the GENE you are given is CFH this is TRUE (its own protein), and when the GENE you are given is FH it is false.
- "mechanism_stated": "haploinsufficiency" | "dominant_negative" | "gain_of_function" | "recessive" | "isoform_imbalance" | "unspecified" | "hi_ruled_out" (the sentence says HI is NOT the mechanism, or the mechanism is "other than haploinsufficiency").
- "phenotype": the disease or phenotype named for this gene in the sentence, as written, or null. When the sentence names it only as "the disease", "this disorder" or "GENE-related cases", take it from the paper's title or the CONTEXT when they name it.
- "phenotype_kind": "retinal" (retina, macula, RPE, photoreceptors, retinal vasculature; RP, LCA, macular dystrophy, FEVR, Coats, drusen) | "other_ocular" (a non-retinal eye disease: aniridia, microphthalmia/anophthalmia, coloboma, optic atrophy, glaucoma, cataract, high myopia) | "syndromic_with_eye" (a multi-system syndrome in which an eye DISEASE is a feature: optic atrophy, coloboma, vitreoretinopathy, retinal detachment, posterior embryotoxon; it counts when the sentence or its CONTEXT names the eye disease as a feature of the disorder, or when the syndrome is NAMED and the eye disease is one of its recognised diagnostic features: Alagille syndrome (posterior embryotoxon), papillorenal / renal-coloboma syndrome (optic nerve coloboma), Stickler syndrome (vitreous anomaly, myopia, retinal detachment), BBSOAS (optic atrophy), CHARGE (coloboma); the eye disease need not be the syndrome's main feature. A paper's title or topic alone does not make the syndrome ocular: "Optic atrophy in Lamb-Shaffer syndrome" beside "Lamb-Shaffer syndrome is a neurodevelopmental disorder associated with SOX5 haploinsufficiency" is syndromic_eye_minor unless the sentence ties the eye disease to the syndrome's haploinsufficient patients) | "syndromic_eye_minor" (a syndrome whose only eye findings are dysmorphic or non-specific: deep-set eyes, hypertelorism, strabismus, nystagmus, refractive error, without an eye disease) | "non_ocular" (no eye finding named and no such syndrome named) | "animal_or_cell_only" (the phenotype is in a model organism or cells) | "unclear".
- "evidence_type": "primary_finding" (this paper's own result or conclusion) | "background_statement" (the authors restate established knowledge, with or without a citation) | "cited_title" (the sentence is or contains the title of a cited paper) | "reference_entry" (a reference-list entry: author list, journal, volume) | "mentioned_nearby" (the gene is named but the dosage wording is about something else).
- "quote": the numbered sentence copied VERBATIM, character for character. Never paraphrase, never shorten, never merge sentences.

Return ONLY JSON: {"items":[{"sentence": <number>, "dosage_claim": ..., "hedge": ..., "hedge_target": ..., "subject_is_gene_dosage": ..., "mechanism_stated": ..., "phenotype": ..., "phenotype_kind": ..., "evidence_type": ..., "quote": ...}, ...]}
One item for EVERY numbered sentence, in order."""

CHECK_FIELDS = (
    "dosage_claim",
    "hedge",
    "hedge_target",
    "subject_is_gene_dosage",
    "mechanism_stated",
    "phenotype",
    "phenotype_kind",
    "evidence_type",
    "quote",
)
CLAIM_FIELDS = ("dosage_claim", "hedge", "hedge_target")  # the fields that define the claim (see claim_hedged)


def claim_hedged(c: dict[str, Any]) -> bool:
    """Whether the hedge the reader saw touches the claim (its mechanism or attribution). A "may"
    about something else ("may have been the cause of the phenotype (BMP4 haploinsufficiency is known
    to cause microphthalmia)") leaves the claim unhedged, and two readings that differ only on
    whether to call that sentence "hedged, about something else" or "not hedged" agree."""
    return c["hedge"] == "hedged" and c["hedge_target"] in ("mechanism", "attribution")


Titles = "Iterable[str] | dict[str, str]"


def _title_map(titles: Any) -> dict[str, str]:
    """PMID -> title when ``titles`` is a mapping; empty for a plain list of titles."""
    return {str(k): str(v) for k, v in titles.items()} if isinstance(titles, dict) else {}


def _title_list(titles: Any) -> list[str]:
    return [str(t) for t in (titles.values() if isinstance(titles, dict) else titles)]


def _paper(h: SentenceHit, by_pmid: dict[str, str]) -> str:
    t = by_pmid.get(h.pmid, "")
    return f"PMID {h.pmid}, page {h.page}" + (f', "{t[:120]}"' if t else "")


def gene_check_messages(
    gene: str, hits: list[SentenceHit], system: str = GENE_CHECK_SYSTEM, titles: Any = ()
) -> list[dict[str, str]]:
    """The pass-one request for ``gene``: its sentences numbered from 1, each with paper (PMID, page
    and, when known, title) and neighbours."""
    by_pmid = _title_map(titles)
    lines = [f"GENE: {gene}", ""]
    for k, h in enumerate(hits, 1):
        lines.append(f"[{k}] ({_paper(h, by_pmid)}) {h.text}")
        ctx = " ".join(x for x in (h.prev, h.next) if x)
        if ctx:
            lines.append(f"    CONTEXT: {ctx}")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def _pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
    v = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return v if v in allowed else default


def parse_gene_check(text: str, n_sentences: int) -> list[dict[str, Any]] | None:
    """The pass-one items, one per sentence number (1..n), with every field read into its closed
    vocabulary (an unknown value becomes the conservative one: no claim, hedged, not gene dosage,
    unspecified, unclear, mentioned nearby). None when the reply is not the JSON asked for."""
    data = _json_object(text)
    if not data or not isinstance(data.get("items"), list):
        return None
    out: dict[int, dict[str, Any]] = {}
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("sentence"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= n_sentences or n in out:
            continue
        out[n] = {
            "sentence": n,
            "dosage_claim": _pick(it.get("dosage_claim"), DOSAGE_CLAIMS, "no"),
            "hedge": _pick(it.get("hedge"), ("none", "hedged"), "hedged"),
            "hedge_target": _pick(it.get("hedge_target"), HEDGE_TARGETS, "mechanism"),
            "subject_is_gene_dosage": bool(it.get("subject_is_gene_dosage")),
            "mechanism_stated": _pick(it.get("mechanism_stated"), MECHANISMS, "unspecified"),
            "phenotype": (str(it.get("phenotype")).strip() or None) if it.get("phenotype") else None,
            "phenotype_kind": _pick(it.get("phenotype_kind"), PHENOTYPE_KINDS, "unclear"),
            "evidence_type": _pick(it.get("evidence_type"), EVIDENCE_TYPES, "mentioned_nearby"),
            "quote": str(it.get("quote") or ""),
        }
        if out[n]["hedge"] == "none":
            out[n]["hedge_target"] = "none"
    return [out[n] for n in sorted(out)]


def is_claim(c: dict[str, Any]) -> bool:
    """A checked sentence that puts this gene's dosage and a human disease together, however weakly."""
    return c["dosage_claim"] in CLAIM_VALUES and c["subject_is_gene_dosage"]


def meets_a(c: dict[str, Any]) -> bool:
    """The curator's A sentence: an unhedged statement (or hedged only about something else) that HI
    of the gene itself causes a retinal, ocular or syndromic-with-eye-disease phenotype, from the
    paper's own finding or a background statement, and verified verbatim in the corpus."""
    return (
        c["dosage_claim"] == "yes"
        and c["subject_is_gene_dosage"]
        and (c["hedge"] == "none" or c["hedge_target"] == "other")
        and c["mechanism_stated"] == "haploinsufficiency"
        and c["phenotype_kind"] in EYE_KINDS
        and c["evidence_type"] not in NOT_EVIDENCE
        and bool(c.get("quote_verified"))
        and explicit_wording(c.get("sentence_text") or c.get("quote") or "")
    )


OTHER_MECHANISMS = frozenset({"dominant_negative", "gain_of_function", "recessive", "isoform_imbalance"})

# "HI variants give a milder, less obvious disease" / "HI is the exception": the curator's B for a gene
# whose usual variants act otherwise (COL11A1, RP1). Matched on the folded quote (ligatures resolved).
HI_MINORITY = re.compile(
    r"haplo-?\s?insuffici\w+[^.;]{0,100}\b(milder|less obvious|less severe|attenuated|minority|the exception)\b"
    r"|\b(milder|less obvious|less severe|attenuated)\b[^.;]{0,100}haplo-?\s?insuffici",
    re.I,
)


# The wording an explicit statement must hold (on the folded quote): the mechanism named, or one copy
# said to be lost or insufficient. "Dosage-sensitive transcription factors" alone names no mechanism
# (the curator's SOX2 / OTX2 sentence), and "haploinsufficiency (loss of function)" after nonsense or
# frameshift variants is the loose synonym (RP1).
A_WORDING = re.compile(
    r"haplo-?\s?insuffici|\b(one|a single|single)\s+(functional|normal|wild[- ]type|intact)?\s*(copy|allele)\b"
    r"|loss of (one|a single) (allele|copy)|heterozygous (null|loss[- ]of[- ]function|truncating|nonsense|frameshift|loss)"
    r"|reduced (gene )?dosage|(50|fifty) ?% of (the )?(normal|wild[- ]type)|half (the|of the|of) (normal|wild[- ]type)"
    r"|nonsense[- ]mediated",
    re.I,
)
LOOSE_SYNONYM = re.compile(r"haplo-?\s?insuffici\w*\s*\((loss[- ]of[- ]function|LoF)\)", re.I)

# A hedge has to be visible in words. The check model reads "favoring a model for haploinsufficiency"
# and "it is understandable that most cases are dominant, as a single functional copy is insufficient"
# as hedged on one run and not the next; the curator counts both as explicit. When none of these cues
# is in the sentence, the model's "hedged" is not kept.
HEDGE_CUES = re.compile(
    r"\b(may|might|could|would|can(?:not)? (?:be )?(?:exclude|rule)|suggest\w*|likely|unlikely|possibl\w*|probab\w*|"
    r"presumabl\w*|proposed?|propose|hypothes\w*|speculat\w*|postulat\w*|putative\w*|potential\w*|predict\w*|"
    r"expected to|thought to|believed?|appear\w*|seem\w*|consistent with|compatible with|in line with|"
    r"remains? to be|unclear|unknown|uncertain|not (?:yet )?(?:been )?(?:proven|established|known|clear)|"
    r"whether|if|perhaps|tentative\w*|plausibl\w*|indicative of|indicat\w* that|supports? (?:the|a) (?:hypothesis|model|notion|idea)|"
    r"argu\w* (?:for|that)|favou?r\w* (?:the|a) hypothesis|at least in part|partly|in part)\b",
    re.I,
)


def hedge_visible(text: str) -> bool:
    return bool(HEDGE_CUES.search(fold(text or "")))


# A syndrome counts as "with eye" only when the sentence itself names an eye disease or a syndrome
# whose diagnostic features include one. "Ophthalmic findings", strabismus, deep-set eyes or a
# refractive error are the curator's non-specific findings (SOX5, GATAD2B), whatever the paper's
# next sentence or title goes on to say.
EYE_DISEASE = re.compile(
    r"optic (?:nerve )?(?:atrophy|hypoplasia|coloboma|disc)|optic atroph|coloboma|micro-?phthalm|anophthalm|aniridia|"
    r"cataract|glaucoma|keratocon|corneal|retin(?:a|al|itis|opathy|oschisis)\b|macul|fovea|photoreceptor|vitre|"
    r"\bFEVR\b|exudative vitreoretinopathy|\bCoats\b|embryotoxon|myopi|Stickler|\bSTL\d?\b|Alagille|\bAGS\b|"
    r"papillorenal|renal[- ]coloboma|BBSOAS|Bosch-Boonstra|\bCHARGE\b|Wagner|Stargardt|drusen|\bAMD\b|\bEOMD\b|"
    r"\bLCA\b|Leber|\bRP\b|adRP|\bCRD\b|\bCORD\b|cone[- ]rod|rod[- ]cone|achromatopsia|albinism|nyctalop|"
    r"night blindness|\bblind|\bA?DOA\b|\bDOAD\b|DOA ?plus|dominant optic",
    re.I,
)
NONSPECIFIC_EYE = re.compile(
    r"(?:ophthalm\w*|ocular|eye) (?:findings?|abnormalit\w*|features?|manifestations?|involvement|problems?|descriptions?|"
    r"anomal\w*|signs?)|\bstrabismus\b|deep-? ?set eyes|\bhypertelorism\b|refractive error|\bnystagmus\b|\bptosis\b|"
    r"\bepicanth",
    re.I,
)


def names_eye_disease(text: str) -> bool:
    return bool(EYE_DISEASE.search(fold(text or "")))


def syndrome_eye_named(text: str, context: str = "") -> bool:
    """Whether a sentence read as "a syndrome with an eye disease" may keep that kind: it names an
    eye disease (or an eye-defining syndrome) itself, or it carries no eye wording at all and its
    neighbours name one (OPA1's "about 50% of the variants cause ... haploinsufficiency" after a
    sentence on dominant optic atrophy). A sentence whose own eye wording is non-specific
    ("ophthalmic findings", strabismus, deep-set eyes) is non-specific whatever its neighbours say
    (SOX5, GATAD2B)."""
    t = fold(text or "")
    if EYE_DISEASE.search(t):
        return True
    if NONSPECIFIC_EYE.search(t):
        return False
    return bool(EYE_DISEASE.search(fold(context or "")))


# "X is associated with haploinsufficiency of Y", "the Frizzled-4 haploinsufficiency observed in FEVR":
# the curator's weak verb, enough for A only when the claim is consistent across papers (RCBTB1,
# SOX5, FZD4: one paper, B; NR2F1: many papers, A).
WEAK_VERB = re.compile(
    r"\b(associated with|association (?:of|between|with)|linked to|correlated? with|correlation|"
    r"observed in|seen in|found in|present in|reported in|detected in|identified in)\b",
    re.I,
)
STRONG_VERB = re.compile(
    r"\b(caus\w*|causative|underl\w*|results? in|resulting in|leads? to|leading to|due to|"
    r"responsible for|mechanism|pathomechanism|insufficient to|is not enough|cannot (?:maintain|sustain)|"
    r"produc\w* the|gives? rise to|explain\w*)\b",
    re.I,
)


# The sentence that states a paper's conclusion ("These results suggest that ...", "In conclusion",
# "Taken together ...") and the sentence that restates one study's finding ("haploinsufficiency of
# X was shown in two families in whom ..."). The curator's RCBTB1: the primary paper's own conclusion
# is hedged and the second paper only restates it, so B; its JAG1 and PAX2 are established knowledge
# ("has been shown to cause AGS", "is causative of"), A.
CONCLUSION_START = re.compile(
    r"^\W*(?:these|our|the present|the current|together,? these|taken together,? (?:these|our)?|collectively,? (?:these|our)?)"
    r"\s*(?:results|findings|data|observations)?\s*(?:therefore |thus |strongly )?(?:suggest|indicate|show|demonstrate|support|imply|"
    r"point)|^\W*(?:in conclusion|in summary|we conclude|taken together|together,|collectively,|overall,|altogether)",
    re.I,
)
STUDY_RESTATEMENT = re.compile(
    r"\b(?:was|were) (?:shown|demonstrated|reported|identified|found|described|observed) in (?:two|three|four|\d+|one|a|an) "
    r"(?:famil|patient|case|individual|proband|sibling|kindred|pedigree|cohort)",
    re.I,
)


def is_conclusion(text: str) -> bool:
    return bool(CONCLUSION_START.search(fold(text or "").strip()))


def restates_one_study(text: str) -> bool:
    return bool(STUDY_RESTATEMENT.search(fold(text or "")))


def weak_verb_only(text: str) -> bool:
    """The sentence ties the gene's HI to the disease only by a weak verb."""
    t = fold(text or "")
    return bool(WEAK_VERB.search(t)) and not STRONG_VERB.search(t)


def explicit_wording(quote: str) -> bool:
    """Whether the quote states the mechanism in words the curator accepts (see ``A_WORDING``) and
    not as the loose synonym for loss of function."""
    q = fold(quote or "")
    return bool(A_WORDING.search(q)) and not LOOSE_SYNONYM.search(q)


def hi_minority(c: dict[str, Any]) -> bool:
    """A verified claim sentence saying HI gives a milder or less obvious phenotype than the gene's
    usual variants, or is the minority mechanism."""
    return bool(
        is_claim(c)
        and c.get("quote_verified")
        and HI_MINORITY.search(fold(c.get("sentence_text") or c.get("quote") or ""))
    )


def contest_strength(c: dict[str, Any]) -> int:
    """How much a checked sentence argues against HI for this gene: 2 when it rules HI out or states
    another mechanism as THE mechanism, unhedged, for a human eye phenotype (or one left unclear);
    1 when it rules HI out but hedged ("suggests a mechanism other than HI"); 0 otherwise. Another
    mechanism stated for a different, non-ocular disease of the same gene (COL2A1's dominant-negative
    Kniest dysplasia beside its haploinsufficient Stickler syndrome) does not contest the eye claim."""
    if c["evidence_type"] in NOT_EVIDENCE or not c["subject_is_gene_dosage"]:
        return 0  # a cited title, a reference entry, another gene's dosage
    if c["mechanism_stated"] == "hi_ruled_out":
        return 2 if c["hedge"] == "none" else 1
    if (
        c["mechanism_stated"] in OTHER_MECHANISMS
        and c["hedge"] == "none"
        and c["phenotype_kind"] in EYE_KINDS | {"unclear"}
    ):
        return 2
    return 0


def contests(c: dict[str, Any]) -> bool:
    """A checked sentence that argues against HI for this gene (see :func:`contest_strength`)."""
    return contest_strength(c) > 0


def meets_a_with_gene(c: dict[str, Any], checks: list[dict[str, Any]]) -> bool:
    """:func:`meets_a`, plus the case the curator counts: an unhedged statement of the mechanism
    whose sentence names the disease only as "the disease" or "PRPH2-related cases" (kind unclear),
    in a paper whose other verified claim about this gene names an eye phenotype."""
    if meets_a(c):
        return True
    if not (
        c["dosage_claim"] == "yes"
        and c["subject_is_gene_dosage"]
        and (c["hedge"] == "none" or c["hedge_target"] == "other")
        and c["mechanism_stated"] == "haploinsufficiency"
        and c["phenotype_kind"] == "unclear"
        and c["evidence_type"] not in NOT_EVIDENCE
        and c.get("quote_verified")
        and explicit_wording(c.get("sentence_text") or c.get("quote") or "")
    ):
        return False
    return any(
        o is not c
        and o.get("pmid") == c.get("pmid")
        and is_claim(o)
        and o.get("quote_verified")
        and o["phenotype_kind"] in EYE_KINDS
        and not o.get("eye_disputed")
        for o in checks
    )


# ---------------------------------------------------------------------------------------------
# Pass two: one call per gene with a claim, the grade (Sonnet)
# ---------------------------------------------------------------------------------------------

# The curator's definitions, verbatim from reference_genes_reviewed.xlsx (README sheet, 2026-11-04).
CURATOR_RULES = """GOAL. Decide whether this is a positive haploinsufficient gene: the authors state that heterozygous loss of function causes a human retinal or ocular disease through haploinsufficiency or reduced dosage.

GRADE A (explicit): Authors state that heterozygous loss of function of the gene causes a human retinal or ocular disease through haploinsufficiency or reduced dosage. A = positive HI gene.
GRADE B (hedged): Same claim, but hedged or undecided ('may', 'likely', 'proposed', 'consistent with'), contradicted by another corpus paper, or one of several possible mechanisms.
GRADE N (not supported): Another mechanism (dominant-negative, gain of function, isoform imbalance, recessive); HI ruled out; non-ocular phenotype; animal/cell data only; gene only mentioned nearby; or a reference-list entry.
Grades are judged on the corpus text only. No outside knowledge (ClinGen, OMIM, what you know about the gene) may set a grade."""

GENE_REVIEW_SYSTEM = (
    CURATOR_RULES
    + """

HOW TO APPLY THEM. You see one gene, every corpus sentence about its dosage, and a first reader's check of each sentence. Read the sentences together: the grade comes from the whole set, not from the best sentence alone.
- A background statement ("X haploinsufficiency is known to cause Y", "has been shown to cause", "is causative of") counts as explicit, even when it is the only sentence. A cited title, a reference-list entry, or a sentence that only mentions the gene near dosage wording does not.
- A hedge word counts only when it modifies the mechanism or the attribution to this gene. "may" about whether a deletion reached the gene in one case does not hedge the claim. "Fit a model in which X haploinsufficiency alone causes Y" and "variants are predicted to lead to haploinsufficiency" (a prediction about the transcript, with HI stated as the mechanism) are explicit.
- Hedged sentences elsewhere do not lower an explicit one. When one sentence states HI as the mechanism without a hedge, other sentences of the same or another paper that say "suggested", "proposed", "predicted", "believed", or weigh HI against another mechanism ("both dominant-negative and haploinsufficiency mechanisms may underlie X disease"), do not make the gene B: only a contradiction does. "One of several possible mechanisms" means B only when NO sentence states HI as the mechanism outright. A paper concluding that mutations cause the disease "via haploinsufficiency and dominant-negative effects" states HI outright: A.
- One study, hedged by its own authors, stays B however often it is restated. When the claim rests on ONE primary paper in the corpus whose own CONCLUSION is hedged ("These results suggest that X haploinsufficiency may lead to...", "is associated with" in its title) and every other sentence only restates that study ("haploinsufficiency of X was shown in two families"), the gene is B, whatever that paper's result sentences say ("protein level is half, indicative of haploinsufficiency" is a result, not the conclusion). It does not apply when any primary paper concludes HI without a hedge ("We conclude that mutations of X cause RP via haploinsufficiency"), even if another primary paper is cautious. It does NOT apply when a later paper states the mechanism as established knowledge ("X haploinsufficiency has been shown to cause Y", "is causative of Y", a named syndrome with an OMIM number) or cites two or more independent studies for it: that is explicit, grade A, even if the original discovery paper in the corpus was cautious.
- "haploinsufficiency (loss of function)" used as a synonym for nonsense/frameshift variants, without saying one copy is insufficient, is not an explicit HI claim.
- "associated with" is a weak verb: enough for A only when the claim is consistent across papers; alone, with few patients, it is B.
- Contradiction: another sentence of this gene (same or another paper) says HI is not the mechanism, or that the disease is usually dominant-negative and HI the exception. Then B even if one sentence is explicit. An author who weighs two mechanisms and concludes HI ("rather than dominant-negative") does not contradict.
- The HI phenotype must be the disease itself. If HI only causes a subclinical or milder finding while the disease has another mechanism, B. A paper saying that this gene's HI variants give a "milder" or "less obvious" phenotype than its usual (dominant-negative) variants makes HI a minority mechanism: B, even when another sentence states the HI claim plainly.
- Another mechanism stated for a DIFFERENT, non-ocular disease of the same gene (dominant-negative COL2A1 variants in Kniest dysplasia beside haploinsufficient Stickler syndrome) does not contradict the eye claim.
- Syndromes: A when an eye DISEASE is a feature of the syndrome (optic atrophy, coloboma, vitreoretinopathy, posterior embryotoxon, vitreous anomaly...): named in this gene's sentences as a feature of the disorder, or a recognised diagnostic feature of the NAMED syndrome (Alagille: posterior embryotoxon; papillorenal / renal-coloboma: optic nerve coloboma; Stickler: vitreous anomaly and retinal detachment; BBSOAS: optic atrophy). The eye disease need not be the main feature: a neurologic disorder that "often includes optic atrophy" counts. Eye findings that are only dysmorphic or non-specific (deep-set eyes, strabismus, refractive error, "ophthalmic findings" without a disease) are not an eye disease: N when nothing else is ocular, B when the eye involvement is real but weak or not tied to the HI allele.
- Only a hypothesis or an aim, no concluding statement in the corpus: B. No dosage claim about this gene at all: N. Animal or cell evidence that supports a human claim does not lower the grade; animal or cell evidence alone is N.
- A sentence marked MEETS_A is an explicit statement by the check. With one or more of them the grade is A unless you can name one of these reasons for B: (1) a contradiction in this gene's sentences; (2) the claim rests on one study whose own conclusion is hedged, and the rest only restate it; (3) HI is a minority or milder mechanism for this gene (a sentence marked HI_MINORITY_WORDING); (4) the eye disease is not tied to the HI allele (reported in carriers of a missense variant, only as a share of "ophthalmic findings", or only in the paper's title or topic while the sentences name a neurodevelopmental disorder). "It is a background sentence", "other papers hedge", "the paper is about something else" are not reasons for B. Consistency across papers counts the papers' own statements, not cited titles or reference entries.

Return ONLY JSON: {"grade": "A" | "B" | "N", "phenotype_kind": one of retinal | other_ocular | syndromic_with_eye | syndromic_eye_minor | non_ocular | animal_or_cell_only | unclear (the kind of the phenotype the grade rests on), "reason": one or two sentences citing the PMIDs you relied on, "best_sentence": the number of the one sentence that best states the claim (it must name the gene and say the dosage mechanism), "confidence": "high" | "low" (low when the claim rests on one background sentence, one family, or a weak verb)}"""
)


def gene_review_messages(
    gene: str, hits: list[SentenceHit], checks: list[dict[str, Any]], system: str = GENE_REVIEW_SYSTEM, titles: Any = ()
) -> list[dict[str, str]]:
    """The pass-two request: each sentence with its check, as the first reader left it."""
    by_pmid = _title_map(titles)
    lines = [f"GENE: {gene}", f"SENTENCES: {len(checks)}", ""]
    for c in checks:
        h = hits[c["sentence"] - 1]
        flags = ", ".join(
            f"{k}={c[k]}"
            for k in (
                "dosage_claim",
                "hedge",
                "hedge_target",
                "subject_is_gene_dosage",
                "mechanism_stated",
                "phenotype_kind",
                "evidence_type",
            )
        )
        lines.append(f"[{c['sentence']}] ({_paper(h, by_pmid)}) {h.text}")
        if c.get("phenotype"):
            flags += f", phenotype={c['phenotype']!r}"
        if c.get("meets_a"):
            flags += ", MEETS_A (an unhedged statement of HI causing an eye phenotype, verified)"
        if c.get("readings_differ"):
            flags += f", READINGS_DIFFER ({c['readings_differ']}): read the sentence yourself"
        if c.get("hi_minority"):
            flags += ", HI_MINORITY_WORDING (HI gives a milder phenotype or is the minority mechanism: reason 3 for B)"
        if c.get("hedge_no_cue"):
            flags += ", hedge_no_cue (the first reader called it hedged, but the sentence holds no hedge word)"
        if c.get("weak_verb") and c.get("meets_a"):
            flags += ", WEAK_VERB (ties HI to the disease only by 'associated with')"
        if c.get("eye_disputed"):
            flags += ", EYE_DISPUTED (one reading called the phenotype ocular, the other not: not an eye disease unless the sentence names one)"
        if c.get("eye_unnamed"):
            flags += ", EYE_UNNAMED (read as a syndrome with an eye disease, but the sentence names none: counted as non-specific)"
        if c.get("primary_conclusion_hedged"):
            flags += (
                ", PRIMARY_CONCLUSION_HEDGED (this paper's own conclusion about the gene is hedged: reason 2 for B)"
            )
        if c.get("restates_study"):
            flags += ", RESTATES_ONE_STUDY (restates another study's families, not established knowledge)"
        lines.append(f"    CHECK: {flags}" + ("" if c.get("quote_verified") else ", quote_not_verified"))
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def parse_gene_review(text: str, n_sentences: int) -> dict[str, Any] | None:
    data = _json_object(text)
    if not data:
        return None
    grade = str(data.get("grade") or "").strip().upper()
    if grade not in GRADES:
        return None
    try:
        best = int(data.get("best_sentence"))
    except (TypeError, ValueError):
        best = 0
    return {
        "grade": grade,
        "phenotype_kind": _pick(data.get("phenotype_kind"), PHENOTYPE_KINDS, "unclear"),
        "reason": str(data.get("reason") or "").strip(),
        "best_sentence": best if 1 <= best <= n_sentences else 0,
        "confidence": _pick(data.get("confidence"), ("high", "low"), "low"),
    }


# ---------------------------------------------------------------------------------------------
# The run: check every gene, review those with a claim, assemble the tables in code
# ---------------------------------------------------------------------------------------------

GRADE_COLUMNS = (
    "gene",
    "grade",
    "phenotype_kind",
    "phenotype",
    "confidence",
    "reason",
    "n_sentences",
    "n_papers",
    "n_claims",
    "n_meeting_a",
    "n_contesting",
    "pmid",
    "page",
    "quote",
    "sentence_id",
    "gene_status",
    "reviewed_by",
    "code_adjusted",
    "error",
)
SENTENCE_COLUMNS = (
    "gene",
    "sentence_id",
    "pmid",
    "page",
    "dosage_claim",
    "hedge",
    "hedge_target",
    "subject_is_gene_dosage",
    "mechanism_stated",
    "phenotype",
    "phenotype_kind",
    "evidence_type",
    "gene_in_quote",
    "cite_like",
    "quote_verified",
    "meets_a",
    "contests",
    "hi_minority",
    "hedge_no_cue",
    "weak_verb",
    "eye_disputed",
    "eye_unnamed",
    "primary_conclusion_hedged",
    "restates_study",
    "readings_differ",
    "quote",
)

# Sentences per pass-one call: a gene with more is read in several calls (CRX has 66 in RetiGene).
MAX_SENTENCES_PER_CALL = 60


@dataclass
class GeneGrade:
    gene: str
    grade: str  # A | B | N | unresolved
    checks: list[dict[str, Any]] = field(default_factory=list)
    review: dict[str, Any] | None = None
    reviewed_by: str = ""  # "rules" (no claim: graded in code), the review model, or ""
    code_adjusted: str = ""  # what the code changed in the model's grade, and why
    error: str = ""


def _batches(n: int, size: int) -> list[range]:
    return [range(i, min(i + size, n)) for i in range(0, max(n, 0), size)] or [range(0)]


AUTHOR_LIST = re.compile(r"^(?:[A-Z][A-Za-z'-]+ [A-Z]{1,3}(?:,| and| &)|[A-Z][A-Za-z'-]+,? [A-Z]\.(?:[A-Z]\.)?,)")
JOURNAL_CITE = re.compile(r"\b(19|20)\d{2}\s*[;:]\s*\d+|\bdoi\s*:|\(\d{4}\)\s*[A-Z]")


def make_is_reference_entry(titles: Iterable[str]) -> Callable[[str, str, str], bool]:
    """A sentence is a reference-list entry (or a cited title) when it contains a corpus paper's
    title, starts like an author list, or sits between journal citations. An inline citation in
    running text ("... (Lenaers et al., 2002)", "has been shown to cause HPE [12]") is not one: that
    was v3's ``make_is_cite`` and it struck the one SHH sentence and OPA1's best sentence."""
    tl = [t for t in (nz(t) for t in titles) if len(t) > 30]

    def is_ref(q: str, prev: str = "", nxt: str = "") -> bool:
        n = nz(q)
        return (
            (len(n) > 30 and any(n in t or t in n for t in tl))
            or bool(AUTHOR_LIST.search(q.strip()))
            or bool(JOURNAL_CITE.search(prev) and JOURNAL_CITE.search(nxt))
        )

    return is_ref


def check_gene(
    gene: str,
    hits: list[SentenceHit],
    check_fn: Complete,
    *,
    titles: Any = (),
    max_per_call: int | None = None,
    retries: int = 1,
    passes: int = 1,
) -> tuple[list[dict[str, Any]], str]:
    """Pass one for one gene: its checked sentences (one per hit, numbered as in ``hits``) with the
    quote verified against the corpus text, and an error message when a batch could not be read.
    ``passes`` > 1 reads every sentence that many times (the cheap model is not deterministic: a
    hedge or a phenotype kind can change between readings). The readings are merged field by field:
    they must agree that the sentence is an unhedged claim (a hedge seen by one reading is a hedge)
    and whether its phenotype is ocular (one eye kind and one not leaves the kind unclear, which
    counts only through another sentence of the same paper); the reading that saw the claim's own
    evidence, the gene's own dosage, the mechanism or a verbatim quote is kept; a sentence argues
    against HI only when every reading says so. The reviewer sees the merged reading and which
    fields differed."""
    readings = [
        _check_gene_once(gene, hits, check_fn, titles=titles, max_per_call=max_per_call, retries=retries)
        for _ in range(max(1, passes))
    ]
    out, err = readings[0]
    for other, err2 in readings[1:]:
        for k, c in enumerate(out):
            o = other[k]
            differ = [f"{f} {c[f]}/{o[f]}" for f in CHECK_FIELDS if f != "quote" and c[f] != o[f]]
            c["claim_agreed"] = (
                c.get("claim_agreed", True)
                and c["dosage_claim"] == o["dosage_claim"]
                and claim_hedged(c) == claim_hedged(o)
            )
            # the phenotype: an eye kind on both readings stands; one eye and one not is disputed, and
            # the sentence then counts only through another sentence of the same paper (SOX5's
            # "neurodevelopmental disorder" read as ocular by one reading under an optic-atrophy title)
            if (c["phenotype_kind"] in EYE_KINDS) != (o["phenotype_kind"] in EYE_KINDS):
                if syndrome_eye_named(c.get("sentence_text", ""), c.get("context_text", "")):
                    # the sentence (or, with no eye wording of its own, its neighbours) names the eye
                    # disease: the reading that saw it stands (OPA1's "50% of the variants ..." after
                    # a sentence on dominant optic atrophy, read retinal by one reader, not by the other)
                    c["phenotype_kind"] = (
                        c["phenotype_kind"] if c["phenotype_kind"] in EYE_KINDS else o["phenotype_kind"]
                    )
                else:
                    c["phenotype_kind"], c["eye_disputed"] = "unclear", True
            # the other fields: the reading that saw a claim's own evidence, the gene's own dosage, the
            # mechanism, or a verbatim quote, is kept
            if c["evidence_type"] in NOT_EVIDENCE and o["evidence_type"] not in NOT_EVIDENCE:
                c["evidence_type"] = o["evidence_type"]
            if not c["subject_is_gene_dosage"] and o["subject_is_gene_dosage"]:
                c["subject_is_gene_dosage"] = True
            if c["mechanism_stated"] == "unspecified" and o["mechanism_stated"] == "haploinsufficiency":
                c["mechanism_stated"] = "haploinsufficiency"
            if not c["quote_verified"] and o["quote_verified"]:
                c["quote_verified"], c["quote"], c["gene_in_quote"] = True, o["quote"], o["gene_in_quote"]
            if not c.get("phenotype") and o.get("phenotype"):
                c["phenotype"] = o["phenotype"]
            c["readings_differ"] = "; ".join(x for x in (c.get("readings_differ", ""), "; ".join(differ)) if x)
            c["contests"] = contests(c) and contests(o)
            c["contest_strength"] = min(c.get("contest_strength", contest_strength(c)), contest_strength(o))
        if err2 and err2 not in err:
            err = "; ".join(x for x in (err, err2) if x)
    for c in out:
        c.setdefault("readings_differ", "")
        c.setdefault("claim_agreed", True)
        c.setdefault("eye_disputed", False)
        c.setdefault("contest_strength", contest_strength(c))
    for c in out:  # the A gate, on the merged readings
        c["meets_a"] = bool(c["claim_agreed"]) and meets_a_with_gene(c, out)
    # a paper whose own conclusion about this gene is hedged does not make the gene A on its result
    # sentences; and a sentence that restates one study's finding is not an explicit statement
    hedged_conclusions = {c["pmid"] for c in out if c.get("is_conclusion") and is_claim(c) and claim_hedged(c)}
    for c in out:
        c["primary_conclusion_hedged"] = c["pmid"] in hedged_conclusions and c["evidence_type"] == "primary_finding"
        if c["meets_a"] and (c["primary_conclusion_hedged"] or c.get("restates_study")):
            c["meets_a"] = False
    return out, err


def verify_quote(quote: str, h: SentenceHit) -> tuple[bool, str]:
    """``(verified, quote)``: the model's quote when it is in the sentence or its neighbours
    verbatim; the corpus sentence itself when the model shortened or ran on past it (it copied the
    CONTEXT line, or stopped at a parenthesis: BMP4's one explicit sentence went unverified twice
    that way) but clearly meant this sentence (the first 40+ folded characters agree); else the
    model's quote, unverified."""
    q = (quote or "").strip()
    if not q:
        return False, q
    ctx = nz(h.text + " " + h.prev + " " + h.next)
    q2 = q.split("CONTEXT:", 1)[0].strip()
    for cand in (q, q2):
        if cand and nz(cand) in ctx:
            # verbatim; the whole sentence when the model stopped before the claim's wording
            whole = nz(cand) in nz(h.text) and explicit_wording(h.text) and not explicit_wording(cand)
            return True, (h.text if whole else cand)
    a, b = nz(q2)[:60], nz(h.text)[:60]
    if len(a) >= 40 and len(b) >= 40 and (a in nz(h.text) or b in nz(q2)):
        return True, h.text
    return False, q


def _check_gene_once(
    gene: str,
    hits: list[SentenceHit],
    check_fn: Complete,
    *,
    titles: Any = (),
    max_per_call: int | None = None,
    retries: int = 1,
) -> tuple[list[dict[str, Any]], str]:
    is_ref = make_is_reference_entry(_title_list(titles))
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    for rng in _batches(len(hits), max_per_call or MAX_SENTENCES_PER_CALL):
        part = hits[rng.start : rng.stop]
        if not part:
            continue
        items = None
        for _ in range(1 + retries):
            raw = check_fn(gene_check_messages(gene, part, titles=titles))
            items = parse_gene_check(raw, len(part))
            if items is not None:
                break
        if items is None:
            errors.append(f"sentences {rng.start + 1}-{rng.stop}: reply was not the JSON asked for")
            continue
        got = {c["sentence"] for c in items}
        for k, h in enumerate(part, 1):
            c = next((c for c in items if c["sentence"] == k), None)
            if c is None:
                c = {
                    "sentence": k,
                    "dosage_claim": "no",
                    "hedge": "hedged",
                    "hedge_target": "mechanism",
                    "subject_is_gene_dosage": False,
                    "mechanism_stated": "unspecified",
                    "phenotype": None,
                    "phenotype_kind": "unclear",
                    "evidence_type": "mentioned_nearby",
                    "quote": "",
                }
            c = dict(c)
            c["sentence"] = rng.start + k  # number within the gene, not the batch
            c["quote_verified"], c["quote"] = verify_quote(c["quote"], h)
            c["sentence_text"] = h.text  # the wording rules read the sentence, not the quote
            c["context_text"] = (h.prev + " " + h.next).strip()
            c["hedge_no_cue"] = c["hedge"] == "hedged" and not hedge_visible(h.text)
            if c["hedge_no_cue"]:
                c["hedge"], c["hedge_target"] = "none", "none"
            c["eye_unnamed"] = c["phenotype_kind"] == "syndromic_with_eye" and not syndrome_eye_named(
                h.text, h.prev + " " + h.next
            )
            if c["eye_unnamed"]:
                c["phenotype_kind"] = "syndromic_eye_minor"
            c["gene_in_quote"] = _word(gene, c["quote"]) if c["quote"] else False
            c["cite_like"] = is_ref(h.text, h.prev, h.next)
            if c["cite_like"] and c["evidence_type"] in ("primary_finding", "background_statement"):
                c["evidence_type"] = "reference_entry"
            c["sentence_id"], c["pmid"], c["page"] = h.sid, h.pmid, h.page
            c["contests"] = contests(c)
            c["hi_minority"] = hi_minority(c)
            c["weak_verb"] = weak_verb_only(h.text)
            c["is_conclusion"] = is_conclusion(h.text)
            c["restates_study"] = restates_one_study(h.text)
            out.append(c)
        if len(got) < len(part):
            errors.append(f"{len(part) - len(got)} sentence(s) without an item, read as no claim")
    for c in out:
        c["meets_a"] = meets_a_with_gene(c, out)
    return out, "; ".join(errors)


def _best_check(checks: list[dict[str, Any]], prefer: int = 0) -> dict[str, Any] | None:
    """The quote for the gene row: the reviewer's choice when it is verified and names the gene,
    else the best by rule (meets A, verified, names the gene, is a claim, not a reference entry,
    then longest)."""
    if prefer:
        c = next((c for c in checks if c["sentence"] == prefer), None)
        if c and c["quote_verified"] and c["gene_in_quote"]:
            return c
    cands = [c for c in checks if c["quote_verified"]]
    if not cands:
        return None
    return sorted(
        cands,
        key=lambda c: (
            not c["meets_a"],
            not c["gene_in_quote"],
            not is_claim(c),
            c["cite_like"],
            c["evidence_type"] in NOT_EVIDENCE,
            -len(c["quote"]),
        ),
    )[0]


def grade_gene(
    gene: str,
    hits: list[SentenceHit],
    check_fn: Complete,
    review_fn: Complete,
    *,
    titles: Any = (),
    review_model: str = "review",
    retries: int = 1,
    check_passes: int = 1,
) -> GeneGrade:
    """Both passes for one gene. The code's guards on the model's grade:
    * no verified claim at all -> N without a review call (``reviewed_by="rules"``);
    * a review of A or B while no verified claim sentence carries an eye phenotype (both readings)
      -> N: the curator's non-ocular rule (GATAD2B), which the reviewer once softened to B;
    * a review of A with no sentence meeting the A conditions -> B;
    * a review of A while a verified sentence contests HI (unhedged, or two hedged ones) -> B;
    * a review of A while a verified claim sentence says HI gives a milder phenotype or is the
      minority mechanism for this gene -> B (the curator's COL11A1 and RP1);
    * a review of A whose explicit sentences all use the weak verb ("associated with") and all come
      from one paper -> B (the curator's SOX5; NR2F1 has many papers);
    * a review of N while a sentence meets the A conditions -> B (the reviewer must say why in its
      reason; the code does not promote to A, it refuses to drop a verified explicit claim silently);
    * an unreadable review -> ``unresolved`` (listed apart, never as A)."""
    checks, err = check_gene(gene, hits, check_fn, titles=titles, retries=retries, passes=check_passes)
    g = GeneGrade(gene, "unresolved", checks, error=err)
    claims = [c for c in checks if is_claim(c) and c["quote_verified"]]
    if not claims:
        g.grade, g.reviewed_by = "N", "rules"
        g.review = {
            "grade": "N",
            "phenotype_kind": "unclear",
            "best_sentence": 0,
            "confidence": "high",
            "reason": (
                "no verified sentence states a dosage claim about this gene" if checks else "no sentences were read"
            ),
        }
        if not checks and err:
            g.grade = "unresolved"
        return g
    review = None
    for _ in range(1 + retries):
        review = parse_gene_review(review_fn(gene_review_messages(gene, hits, checks, titles=titles)), len(hits))
        if review is not None:
            break
    if review is None:
        g.error = "; ".join(x for x in (err, "review reply was not the JSON asked for") if x)
        return g
    g.review, g.reviewed_by, g.grade = review, review_model, review["grade"]
    eye_claims = [c for c in claims if c["phenotype_kind"] in EYE_KINDS and not c.get("eye_disputed")]
    if g.grade in ("A", "B") and not eye_claims:
        g.grade, g.code_adjusted = (
            "N",
            (
                f"review said {g.grade}, but no verified claim sentence names an eye "
                "phenotype for this gene (non-ocular, dysmorphic or animal only)"
            ),
        )
        return g
    n_a = sum(c["meets_a"] for c in checks)
    strength = [c.get("contest_strength", contest_strength(c)) for c in checks if c["quote_verified"]]
    n_c = sum(1 for x in strength if x)
    contested = any(x == 2 for x in strength) or sum(1 for x in strength if x == 1) >= 2
    minority = [c["sentence"] for c in checks if c.get("hi_minority")]
    if g.grade == "A" and not n_a:
        g.grade, g.code_adjusted = "B", "review said A, but no verified sentence meets the A conditions"
    elif g.grade == "A" and contested:
        g.grade, g.code_adjusted = "B", f"review said A, but {n_c} verified sentence(s) argue against HI"
    elif g.grade == "A" and minority:
        g.grade, g.code_adjusted = (
            "B",
            (
                f"review said A, but sentence {minority[0]} says HI gives a milder phenotype "
                f"or is the minority mechanism for this gene"
            ),
        )
    elif (
        g.grade == "A"
        and all(c.get("weak_verb") for c in checks if c["meets_a"])
        and len({c["pmid"] for c in checks if c["meets_a"]}) == 1
    ):
        g.grade, g.code_adjusted = (
            "B",
            (
                "review said A, but every explicit sentence ties HI to the disease only by "
                "'associated with' and all come from one paper"
            ),
        )
    elif g.grade == "N" and n_a:
        g.grade, g.code_adjusted = "B", f"review said N, but {n_a} verified sentence(s) meet the A conditions"
    return g


def grade_genes(
    groups: dict[str, list[SentenceHit]],
    check_fn: Complete,
    review_fn: Complete,
    *,
    hgnc: Hgnc,
    titles: Any = (),
    review_model: str = "review",
    retries: int = 2,
    log: Callable[[str], None] = lambda s: None,
    max_genes: int = 0,
    workers: int = 1,
    check_passes: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(gene rows, sentence rows)`` for every gene in ``groups`` (the nameless group ``""`` and
    symbols HGNC does not know are skipped and logged). Gene rows in grade order A, B, N, unresolved,
    then by symbol; ``GRADE_COLUMNS`` / ``SENTENCE_COLUMNS`` name the fields. ``workers`` genes are
    graded at a time (their model calls run in threads, so ``check_fn`` / ``review_fn`` must be safe
    to call from several threads); an exception in one gene stops the rest."""
    gene_rows: list[dict[str, Any]] = []
    sent_rows: list[dict[str, Any]] = []
    merged: dict[str, tuple[str, list[SentenceHit]]] = {}  # symbol -> (status, hits of every spelling)
    for raw, hits in sorted(groups.items()):
        if not raw or not hits:
            continue
        sym, status = hgnc.norm(raw)
        if status in ("unknown", "missing") or status.startswith("ambiguous"):
            log(f"{raw}: skipped ({status})")
            continue
        if sym in merged:  # "Jag1", "JAG1", an alias: one gene, one grade
            seen = {h.sid for h in merged[sym][1]}
            merged[sym][1].extend(h for h in hits if h.sid not in seen)
        else:
            merged[sym] = (status, list(hits))
    todo = [(sym, st, hits) for sym, (st, hits) in sorted(merged.items())]
    if max_genes:
        todo = todo[:max_genes]

    def one(sym: str, hits: list[SentenceHit]) -> GeneGrade:
        return grade_gene(
            sym,
            hits,
            check_fn,
            review_fn,
            titles=titles,
            review_model=review_model,
            retries=retries,
            check_passes=check_passes,
        )

    if workers > 1 and len(todo) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as pool:
            futures = {pool.submit(one, sym, hits): (sym, status, hits) for sym, status, hits in todo}
            graded: list[tuple[str, str, list[SentenceHit], GeneGrade]] = []
            try:
                for fut in as_completed(futures):
                    sym, status, hits = futures[fut]
                    graded.append((sym, status, hits, fut.result()))
            except BaseException:
                for fut in futures:
                    fut.cancel()
                raise
    else:
        graded = [(sym, status, hits, one(sym, hits)) for sym, status, hits in todo]
    for sym, status, hits, g in graded:
        rv = g.review or {}
        best = _best_check(g.checks, int(rv.get("best_sentence") or 0))
        kinds = [c["phenotype_kind"] for c in g.checks if c["meets_a"]] or [
            c["phenotype_kind"] for c in g.checks if is_claim(c) and c["quote_verified"]
        ]
        kind = rv.get("phenotype_kind") or "unclear"
        if g.grade == "A" and kind not in EYE_KINDS and kinds:
            kind = kinds[0]  # an A rests on an eye phenotype by construction
        phen = next((c["phenotype"] for c in ([best] if best else []) + g.checks if c and c.get("phenotype")), "")
        gene_rows.append(
            {
                "gene": sym,
                "grade": g.grade,
                "phenotype_kind": kind,
                "phenotype": phen or "",
                "confidence": rv.get("confidence", ""),
                "reason": rv.get("reason", ""),
                "n_sentences": len(hits),
                "n_papers": len({h.pmid for h in hits}),
                "n_claims": sum(is_claim(c) and c["quote_verified"] for c in g.checks),
                "n_meeting_a": sum(c["meets_a"] for c in g.checks),
                "n_contesting": sum(c["contests"] and c["quote_verified"] for c in g.checks),
                "pmid": best["pmid"] if best else "",
                "page": best["page"] if best else "",
                "quote": best["quote"] if best else "",
                "sentence_id": best["sentence_id"] if best else "",
                "gene_status": status,
                "reviewed_by": g.reviewed_by,
                "code_adjusted": g.code_adjusted,
                "error": g.error,
            }
        )
        for c in g.checks:
            sent_rows.append({"gene": sym, **{k: c.get(k, "") for k in SENTENCE_COLUMNS if k != "gene"}})
        log(f"{sym}: {g.grade} ({len(hits)} sentences)" + (f" [{g.code_adjusted}]" if g.code_adjusted else ""))
    order = {"A": 0, "B": 1, "N": 2, "unresolved": 3}
    gene_rows.sort(key=lambda r: (order.get(r["grade"], 9), r["gene"]))
    return gene_rows, sent_rows


def recall_report(gene_rows: list[dict[str, Any]], reference: list[dict[str, str]]) -> dict[str, Any]:
    """Scores against the curator's table (``reference_genes_reviewed.csv``: gene, review_grade,
    phenotype_kind). The 15 A genes by subgroup (ocular = retinal + other_ocular, syndromic), the B
    genes listed apart (B, N or absent: anything but A), the N genes absent or N, and anything
    listed as A outside the reference."""
    got = {r["gene"]: r["grade"] for r in gene_rows}
    ref_a = [r for r in reference if r["review_grade"] == "A"]
    ocular = [r["gene"] for r in ref_a if r["phenotype_kind"] in ("retinal", "other_ocular")]
    syndromic = [r["gene"] for r in ref_a if r["phenotype_kind"] not in ("retinal", "other_ocular")]
    ref_b = [r["gene"] for r in reference if r["review_grade"] == "B"]
    ref_n = [r["gene"] for r in reference if r["review_grade"] == "N"]
    a_as_a = {g: got.get(g) == "A" for g in [r["gene"] for r in ref_a]}
    return {
        "a_listed_as_a": sum(a_as_a.values()),
        "a_total": len(ref_a),
        "a_ocular": [sum(a_as_a[g] for g in ocular), len(ocular)],
        "a_syndromic": [sum(a_as_a[g] for g in syndromic), len(syndromic)],
        "a_missed": sorted(g for g, ok in a_as_a.items() if not ok),
        "a_graded": {g: got.get(g, "absent") for g in a_as_a},
        "b_listed_apart": sum(got.get(g) != "A" for g in ref_b),
        "b_total": len(ref_b),  # B, N or absent: not A
        "b_graded": {g: got.get(g, "absent") for g in ref_b},
        "b_as_a": sorted(g for g in ref_b if got.get(g) == "A"),
        "n_listed": sorted(g for g in ref_n if got.get(g) in ("A", "B")),
        "a_outside_reference": sorted(
            g for g, gr in got.items() if gr == "A" and g not in {r["gene"] for r in reference}
        ),
    }


def fingerprint_parts() -> list[str]:
    """What a stored result of this grading depends on, for the scan's rules digest."""
    return [
        GENE_CHECK_SYSTEM,
        GENE_REVIEW_SYSTEM,
        json.dumps([DOSAGE_CLAIMS, HEDGE_TARGETS, MECHANISMS, PHENOTYPE_KINDS, EVIDENCE_TYPES]),
        str(MAX_SENTENCES_PER_CALL),
        re.sub(r"\s+", " ", fold(CURATOR_RULES)),
    ]
