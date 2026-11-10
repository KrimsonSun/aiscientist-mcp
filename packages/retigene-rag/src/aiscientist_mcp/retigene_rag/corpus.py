"""The corpus text as the curation reads it: folding, terms, gene symbols, HGNC and the sentence scan.

The unit of a scan for "every gene with property P" is the sentence. The corpus' sentence table
(``mechanism_cache/sentences.jsonl``: sid ``pmid:chunk:sent``, page, text, and the sentence before and
after it) is searched for the property's words, every hit is taken, and the hits are grouped by the gene
named in the sentence or next to it, so that one gene is graded from everything the corpus says about it.
On the RetiGene haploinsufficiency question this is 470 sentences naming about 300 genes, ~48k tokens,
instead of ~1,550 passage reads.

The helpers follow AiScientist's ``tools/_lib/corpus.py`` and ``extract_haploinsufficient_genes``
(RCHENLAB/AiScientist #99, sentence curation by Ziyao Ma), so a scan here gives the same groups.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---- text ------------------------------------------------------------------------------------------

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
_NZ_DROP = re.compile("[\\s\u00ad\\-\u2010-\u2015\u2212\x00-\x1f\x7f]+")


def fold(text: str | None) -> str:
    """NFKC (PDF ligatures as letters), soft hyphens out, Unicode dashes as ``-``."""
    return unicodedata.normalize("NFKC", text or "").replace("\u00ad", "").translate(_DASHES)


def nz(text: str | None) -> str:
    """Text for comparing quotes: folded, lower case, no whitespace, dashes or control characters."""
    return _NZ_DROP.sub("", fold(text).lower())


def word_in(word: str, text: str) -> bool:
    """``word`` occurs in ``text`` as a whole word, any case."""
    return bool(word) and re.search(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", text, re.I) is not None


_LENIENT = json.JSONDecoder(strict=False)  # a quote copied with the PDF's raw control characters still reads


def json_object(text: str) -> dict | None:
    """The JSON object in a model reply: the reply itself (``json`` fences allowed), else the outermost
    braces in it. A bare list of objects is read as ``{"items": [...]}``, the shape the check asks for."""
    text = re.sub(r"^```(json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    text = re.sub(r"^<think>.*?</think>\s*", "", text, flags=re.S)  # a reasoning model's thinking, if returned
    for cand in (text, text[text.find("{") : text.rfind("}") + 1] if "{" in text else ""):
        if not cand:
            continue
        try:
            data = _LENIENT.decode(cand)
        except ValueError:
            continue
        if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
            return {"items": data}
        if isinstance(data, dict):
            return data
    return None


# ---- gene symbols ----------------------------------------------------------------------------------

_GENE_RX = re.compile(r"\b[A-Z][A-Z0-9]{1,7}(?:-[A-Z0-9]{1,4})?\b")
_NOT_GENES = frozenset(
    [
        "DNA",
        "RNA",
        "THE",
        "AND",
        "FOR",
        "WITH",
        "WHAT",
        "WHICH",
        "ARE",
        "WAS",
        "NOT",
        "PCR",
        "MRNA",
        "SNP",
        "CNV",
        "OR",
        "IN",
        "OF",
        "TO",
        "IS",
        "AS",
        "AT",
        "BY",
    ]
)
# Upper-case abbreviations of this field that are not genes in its papers, though some are HGNC symbols
# (ERG: electroretinogram; RPE: retinal pigment epithelium; OCT: optical coherence tomography).
NOT_GENES_HERE = frozenset(
    """
    ERG EOG RPE OCT FAF AMD LCA RP CSNB CRD ADRP ARRP XLRP BCVA VA IOP ONL OPL INL IPL GCL RNFL ELM EZ IZ
    ISCEV ETDRS ROP FEVR CNV SNV WES WGS NGS PCR AAV CRISPR MRI CT ICG FA ERM CME MD BEST STGD PDE ISS
    OMIM HGMD ACMG LOF GOF NMD DN AD AR XL WT KO
    """.split()  # noqa: SIM905
)


def gene_symbols(text: str) -> list[str]:
    """Tokens of ``text`` written like gene symbols (RPGR, PRPF31), in order, without repeats."""
    seen: dict[str, None] = {}
    for tok in _GENE_RX.findall(text):
        if tok not in _NOT_GENES and any(c.isalpha() for c in tok) and tok not in seen:
            seen[tok] = None
    return list(seen)


def is_gene(token: str, known: frozenset[str] | None = None) -> bool:
    """Written like a gene symbol, not one of this field's abbreviations, and (with an HGNC table) an
    approved, previous or alias symbol."""
    return gene_symbols(token) == [token] and token not in NOT_GENES_HERE and (known is None or token in known)


def genes_named(text: str, known: frozenset[str] | None = None) -> list[str]:
    return [g for g in gene_symbols(fold(text)) if is_gene(g, known)]


def known_gene_symbols(path: Path | None) -> frozenset[str] | None:
    """Every approved, previous and alias symbol in the HGNC table, as written; None without one."""
    if not path or not Path(path).is_file():
        return None
    out: set[str] = set()
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            for col in ("symbol", "alias_symbol", "prev_symbol"):
                out.update(a.strip().strip('"') for a in str(r.get(col) or "").split("|") if a.strip())
    return frozenset(out)


class Hgnc:
    """Symbol normalisation only, never evidence: approved symbols first; previous symbols and aliases
    second; an alias of several approved symbols is reported as ambiguous."""

    def __init__(self, path: Path | None):
        self.hg: dict[str, str] = {}
        self.alias: dict[str, set[str]] = {}
        self.loaded = False
        if not path or not Path(path).is_file():
            return
        with Path(path).open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                if str(r.get("status") or "Approved") != "Approved":
                    continue
                sym = str(r.get("symbol") or "").strip()
                if not sym:
                    continue
                self.hg[sym.upper()] = sym
                for col in ("alias_symbol", "prev_symbol"):
                    for a in str(r.get(col) or "").split("|"):
                        a = a.strip().strip('"')
                        if a:
                            self.alias.setdefault(a.upper(), set()).add(sym)
        self.loaded = True

    def norm(self, g: Any) -> tuple[str | None, str]:
        """``(symbol, status)``; status ok | alias | ambiguous:A|B | unknown | missing | unchecked."""
        g = re.sub(r"[^A-Za-z0-9-]", "", str(g or "")).upper()
        if not g:
            return None, "missing"
        if not self.loaded:
            return g, "unchecked"
        if g in self.hg:
            return self.hg[g], "ok"
        if g in self.alias and len(self.alias[g]) == 1:
            return next(iter(self.alias[g])), "alias"
        if g in self.alias:
            return g, "ambiguous:" + "|".join(sorted(self.alias[g]))
        return g, "unknown"


# ---- terms -----------------------------------------------------------------------------------------


@dataclass
class Term:
    """One exact word or phrase. ``kind``: ``gene`` (case-sensitive whole word), ``word`` (any case, word
    endings and hyphen/space variants allowed) or ``pattern`` (a regular expression, any case)."""

    text: str
    kind: str = "word"
    pattern: str = ""
    source: str = ""  # "floor" (the rules' own wording) or "agent" (the caller's terms)
    n_sentences: int = 0


def _word_rx(w: str) -> str:
    """One word of a term: short words exactly (plus a plural), long ones by their stem, so
    "haploinsufficiency" also matches "haploinsufficient"."""
    end = "(?![a-z0-9])"
    if w.isdigit() or len(w) <= 3:
        return re.escape(w) + ("" if w.isdigit() else "s?") + end
    if len(w) <= 5:
        return (re.escape(w[:-1]) + "(?:y|ies)" if w.endswith("y") else re.escape(w) + "(?:s|es)?") + end
    return re.escape(w[: max(5, len(w) - 3)]) + "[a-z0-9]*"


def term_regex(term: Term) -> tuple[re.Pattern[str], str, bool]:
    """The pattern of ``term``, the text view it runs on (``case``, ``low`` or ``join``) and whether a
    match must start a word."""
    if term.kind == "pattern":
        return re.compile(term.pattern, re.I), "low", False
    t = fold(term.text).strip()
    if term.kind == "gene":
        return re.compile(re.escape(t) + r"(?![A-Za-z0-9])"), "case", True
    words = re.findall(r"[a-z0-9]+", t.lower())
    if not words:
        return re.compile(r"(?!x)x"), "low", False
    if len(words) == 1:  # also its hyphenated spelling: the join view drops hyphens between letters
        return re.compile(_word_rx(words[0])), "join", True
    return re.compile(r"[\s\-/]*".join(_word_rx(w) for w in words)), "low", True


def parse_terms(raw: Any, source: str = "agent", known: frozenset[str] | None = None) -> list[Term]:
    """Terms from a list of strings: a gene symbol becomes a ``gene`` term, anything else a ``word``."""
    out: list[Term] = []
    seen: set[str] = set()
    for x in raw or []:
        t = fold(str(x)).strip().strip("\"'")
        if not t or len(t) > 80 or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(Term(t, "gene" if is_gene(t, known) else "word", source=source))
    return out


class TextIndex:
    """The sentence texts, folded, in three searchable views (as written, lower case, hyphens between
    letters removed)."""

    def __init__(self, texts):
        folded = [fold(t) for t in texts]
        low = [t.lower() for t in folded]
        joined = [re.sub(r"(?<=[a-z])-(?=[a-z])", "", t) for t in low]
        self.n = len(folded)
        self._views: dict[str, tuple[str, list[int]]] = {}
        for name, docs in (("case", folded), ("low", low), ("join", joined)):
            starts, pos = [], 0
            for d in docs:
                starts.append(pos)
                pos += len(d) + 1
            self._views[name] = ("\n".join(docs), starts)

    def holding(self, term: Term) -> list[int]:
        """Indexes of the texts that hold ``term``."""
        rx, view, word_start = term_regex(term)
        text, starts = self._views[view]
        out: Counter[int] = Counter()
        for m in rx.finditer(text):
            at = m.start()
            if word_start and at and text[at - 1].isalnum():
                continue
            out[bisect_right(starts, at) - 1] += 1
        return sorted(out)


# The rules' own wording for the haploinsufficiency property: every sentence that states a dosage
# mechanism in words is read whatever terms a caller gives (a reworded question adds words, never
# removes these).
DOSAGE_WORDING = re.compile(
    r"haplo-?\s?insuffici\w*|gene[- ]dosage|dosage[- ]sensitiv\w*|dosage effect|reduced (?:gene )?dosage|"
    r"half[- ]dosage|(?:one|single|a single|the remaining) (?:functional|normal|wild[- ]type|intact|working) "
    r"(?:copy|allele)|loss of (?:one|a single) (?:copy|allele)|half (?:the |of the )?(?:normal )?(?:amount|level|dose)",
    re.I,
)
DOSAGE_TERM = Term("dosage wording", "pattern", DOSAGE_WORDING.pattern, source="floor")


# ---- the sentence scan -----------------------------------------------------------------------------


@dataclass
class SentenceHit:
    """One matching sentence with its place in the corpus, the terms that matched it and the genes it
    names. ``near_genes`` are named only by its neighbours in the same chunk, which is how "This results
    in haploinsufficiency." is attributed to the gene the paragraph is about."""

    sid: str
    pmid: str
    chunk: int
    sent: int
    page: int
    text: str
    prev: str = ""
    next: str = ""
    terms: list[str] = field(default_factory=list)
    genes: list[str] = field(default_factory=list)
    near_genes: list[str] = field(default_factory=list)

    @property
    def all_genes(self) -> list[str]:
        return self.genes + [g for g in self.near_genes if g not in self.genes]


def read_sentences(path: Path) -> list[dict[str, Any]]:
    """The records of ``sentences.jsonl`` (``sid, pmid, chunk, sent, page, text, prev, next``), in file
    order; a broken line is skipped."""
    out: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if str(d.get("text") or "").strip():
                out.append(d)
    return out


def scan(sentences: list[dict[str, Any]], terms: list[Term], index: TextIndex,
         known: frozenset[str] | None = None) -> list[SentenceHit]:  # fmt: skip
    """Every sentence that holds a term, in corpus order, with the genes it or its neighbours name. No cap
    and no top-k: this is the scan's recall floor. Each term's ``n_sentences`` is filled in."""
    where: dict[int, list[str]] = {}
    for t in terms:
        hold = index.holding(t)
        t.n_sentences = len(hold)
        for i in hold:
            where.setdefault(i, []).append(t.text)
    out: list[SentenceHit] = []
    for i in sorted(where):
        s = sentences[i]
        text, prev, nxt = str(s.get("text") or ""), str(s.get("prev") or ""), str(s.get("next") or "")
        genes = genes_named(text, known)
        near = [g for g in genes_named(prev + " " + nxt, known) if g not in genes]
        out.append(SentenceHit(sid=str(s.get("sid") or ""), pmid=str(s.get("pmid") or ""),
                               chunk=int(s.get("chunk") or 0), sent=int(s.get("sent") or 0),
                               page=int(s.get("page") or 0), text=text, prev=prev, next=nxt, terms=where[i],
                               genes=genes, near_genes=near))  # fmt: skip
    return out


def group_by_gene(hits: list[SentenceHit]) -> dict[str, list[SentenceHit]]:
    """``gene -> its sentences`` (corpus order; a sentence naming two genes is under both). Sentences that
    name no gene, nor their neighbours, are under ``""`` so that they are counted, not lost."""
    out: dict[str, list[SentenceHit]] = {}
    for h in hits:
        for g in h.all_genes or [""]:
            out.setdefault(g, []).append(h)
    return out


def merge_spellings(groups: dict[str, list[SentenceHit]], hgnc: Hgnc) -> tuple[dict[str, list[SentenceHit]], dict]:
    """One group per approved symbol ("Jag1", "JAG1" and an alias: one gene). Unknown and ambiguous names
    are left out; the second value says what happened to each name."""
    merged: dict[str, list[SentenceHit]] = {}
    status: dict[str, str] = {}
    for raw, hits in sorted(groups.items()):
        if not raw or not hits:
            continue
        sym, st = hgnc.norm(raw)
        status[raw] = st if sym == raw else f"{st}:{sym}"
        if st in ("unknown", "missing") or st.startswith("ambiguous"):
            continue
        seen = {h.sid for h in merged.get(sym, [])}
        merged.setdefault(sym, []).extend(h for h in hits if h.sid not in seen)
    return merged, status


def read_manifest(path: Path | None) -> dict[str, dict]:
    """PMID -> citation, title, year, doi, from a manifest CSV with a ``doc_id`` or ``pmid`` column."""
    if not path or not Path(path).is_file():
        return {}
    out: dict[str, dict] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            pmid = str(row.get("pmid") or row.get("doc_id") or "").strip()
            if pmid:
                out[pmid] = {k: row.get(k) or "" for k in ("citation", "title", "year", "doi", "journal")}
    return out
