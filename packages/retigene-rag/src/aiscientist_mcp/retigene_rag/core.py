"""Sentences, passages, the manifest, HGNC symbols and model cost: what the search and grading tools share."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable
from pathlib import Path

MAX_TOKENS = 8000
PRICE_IN, PRICE_OUT = 0.10, 0.50  # USD per million tokens, Claude Haiku 5.5
CORPUS = "the lab's RetiGene corpus (inherited retinal disease)"
# Characters sent per input token, measured on whole reads of the RetiGene corpus (Claude Haiku 5.5).
CHARS_PER_TOKEN = 2.3


Call = Callable[[list[dict]], "tuple[str, dict]"]


_ABBR = re.compile(r"\b(?:et al|Fig|Figs|Dr|vs|e\.g|i\.e|cf|approx|No|Ref|Refs|Suppl|Tab|ca)\.$", re.I)


_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\(\[“\"])")


def split_sentences(text: str) -> list[str]:
    """The passage as sentences: split after a full stop, a question or an exclamation mark before a
    capital, a digit or an opening bracket, never after a common abbreviation."""
    parts = _SPLIT.split(" ".join(str(text or "").split()))
    out: list[str] = []
    for part in parts:
        if out and _ABBR.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return [s.strip() for s in out if s.strip()]


def load_passages(path: Path) -> list[dict]:
    """Every passage with text, in file order, keyed ``pmid:chunk``; a repeated key is kept once."""
    out: list[dict] = []
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            text = str(row.get("text") or "")
            pmid = str(row.get("pmid") or "").strip()
            key = f"{pmid}:{row.get('chunk')}"
            if not pmid or not text.strip() or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "key": key,
                    "pmid": pmid,
                    "chunk": row.get("chunk"),
                    "page": row.get("page"),
                    "sentences": split_sentences(text),
                }
            )
    return out


def load_manifest(path: Path | None) -> dict[str, dict]:
    """PMID -> citation, title and year, from a manifest CSV with a ``doc_id`` or ``pmid`` column."""
    if not path:
        return {}
    out: dict[str, dict] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            pmid = str(row.get("pmid") or row.get("doc_id") or "").strip()
            if pmid:
                out[pmid] = {k: row.get(k) or "" for k in ("citation", "title", "year")}
    return out


def json_object(text: str) -> dict | None:
    """The JSON object in a reply: the whole reply (``json`` fences allowed), else the outermost
    braces in it."""
    s = str(text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[4:].strip() if s.lower().startswith("json") else s
    for candidate in (s, (re.search(r"\{.*\}", s, re.DOTALL) or [""])[0]):
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def cost(use: dict, price_in: float = PRICE_IN, price_out: float = PRICE_OUT) -> float:
    """USD for a usage record: cache writes at 1.25 and cache reads at 0.1 times the input price."""
    return (
        use.get("input_tokens", 0) * price_in
        + use.get("output_tokens", 0) * price_out
        + use.get("cache_write_tokens", 0) * price_in * 1.25
        + use.get("cache_read_tokens", 0) * price_in * 0.1
    ) / 1e6


def add_usage(total: dict, use: dict) -> dict:
    for k in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"):
        total[k] = total.get(k, 0) + int(use.get(k) or 0)
    total["calls"] = total.get("calls", 0) + 1
    return total


_GENE_RX = re.compile(r"\b[A-Z][A-Z0-9]{1,7}(?:-[A-Z0-9]{1,4})?\b")


# Words written like gene symbols that are not genes in this field, or are far more often not.
NOT_GENES = frozenset(
    """
    DNA RNA THE AND FOR WITH WHAT WHICH ARE WAS NOT PCR MRNA SNP CNV OR IN OF TO IS AS AT BY
    ERG EOG RPE OCT FAF AMD LCA RP CSNB CRD ADRP ARRP XLRP BCVA VA IOP ONL OPL INL IPL GCL RNFL ELM
    EZ IZ ISCEV ETDRS ROP FEVR SNV WES WGS NGS AAV CRISPR MRI CT ICG FA ERM CME MD BEST STGD PDE ISS
    OMIM HGMD ACMG LOF GOF NMD DN AD AR XL WT KO
    """.split()  # noqa: SIM905
)


def hgnc_symbols(path: Path) -> dict[str, str]:
    """Symbol as written -> approved symbol: every approved symbol, and each previous or alias
    symbol that belongs to one approved gene only, is not itself an approved symbol, and has three
    characters or more with a digit among them (HRG4, PRP31, CORD2). Shorter or digit-free aliases
    (MIM, II, S1, BBS) match ordinary text far more often than the gene."""
    approved: set[str] = set()
    other: dict[str, set[str]] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if str(row.get("status") or "Approved") != "Approved":
                continue
            sym = str(row.get("symbol") or "").strip()
            if not sym:
                continue
            approved.add(sym)
            for col in ("alias_symbol", "prev_symbol"):
                for a in str(row.get(col) or "").split("|"):
                    a = a.strip().strip('"')
                    if len(a) >= 3 and any(c.isdigit() for c in a):
                        other.setdefault(a, set()).add(sym)
    out = {s: s for s in approved}
    out.update({a: next(iter(s)) for a, s in other.items() if len(s) == 1 and a not in approved})
    return out
