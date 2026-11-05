"""The search tools: keyword search over every sentence, embedding search over passages, a passage by id.

Keyword search is the main tool. A question that asks for every gene with a property is answered by the
sentences that use the property's words, and those sentences sit in passages about many other topics.
On the RetiGene corpus, the sentences that say "haploinsufficien" (392 of 598,885) name all 15 genes of
the curator reference, while the top 1,000 passages by chunk embedding hold 1-4 of them. Embedding search
ranks passages by what they are about, which suits a question about one topic.

Keywords match the folded text: lowercase, PDF ligatures as letters, spaces and hyphens removed, so
"loss of function" finds "loss-of-function" and "haploinsufficien" finds "haploinsuf- ficiency".
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import core

EMBEDDING_MODEL = "NeuML/pubmedbert-base-embeddings"
OFFLINE = "offline-test"
MIN_TERM = 4  # folded characters; shorter terms match inside too many unrelated words
# Hyphen-minus, soft hyphen, hyphen and non-breaking hyphen; the PDF ligatures ff, fi, fl, ffi, ffl.
_HYPHENS = "-" + chr(0x00AD) + chr(0x2010) + chr(0x2011)
_SQUASH = re.compile(r"[\s" + re.escape(_HYPHENS) + "]+")
_LIGATURES = {chr(0xFB00): "ff", chr(0xFB01): "fi", chr(0xFB02): "fl", chr(0xFB03): "ffi", chr(0xFB04): "ffl"}


def fold(text: str) -> str:
    """Lowercase, PDF ligatures as letters, and no spaces or hyphens: how keywords are matched."""
    t = str(text or "")
    for k, v in _LIGATURES.items():
        t = t.replace(k, v)
    return _SQUASH.sub("", t.lower())


@dataclass
class Corpus:
    passages: list[dict]  # key, pmid, chunk, page, sentences (core.load_passages)
    texts: list[str]  # each passage's text as written
    folded: list[tuple[int, str, str]]  # every sentence: passage index, sentence, folded sentence
    by_key: dict[str, int]

    @classmethod
    def load(cls, chunks: Path) -> Corpus:
        passages = core.load_passages(chunks)
        texts, seen = [], set()
        with Path(chunks).open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                text, pmid = str(row.get("text") or ""), str(row.get("pmid") or "").strip()
                key = f"{pmid}:{row.get('chunk')}"
                if not pmid or not text.strip() or key in seen:
                    continue
                seen.add(key)
                texts.append(text)
        folded = [(i, s, fold(s)) for i, p in enumerate(passages) for s in p["sentences"]]
        return cls(passages, texts, folded, {p["key"]: i for i, p in enumerate(passages)})


def sentence_line(c: Corpus, i: int, sentence: str) -> str:
    p = c.passages[i]
    return f"PMID {p['pmid']} p{p['page']} c{p['chunk']}: {sentence}"


def keyword_search(c: Corpus, terms: list[str], gene: str = "", limit: int = 100, offset: int = 0) -> dict:
    """Every sentence that holds any of ``terms`` (and names ``gene`` as a word, when given), a page at a time."""
    ts = [t for t in dict.fromkeys(fold(t) for t in terms) if t]
    if not ts or any(len(t) < MIN_TERM for t in ts):
        raise ValueError(f"give terms of at least {MIN_TERM} letters, e.g. 'haploinsufficien' or 'loss of function'")
    grx = re.compile(rf"\b{re.escape(gene.strip())}\b") if gene.strip() else None
    hits = [(i, s) for i, s, f in c.folded if any(t in f for t in ts) and (grx is None or grx.search(s))]
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    page = hits[offset : offset + limit]
    return {"terms": ts, "gene": gene.strip(), "total_sentences": len(hits),
            "total_passages": len({i for i, _ in hits}), "offset": offset,
            "next_offset": offset + limit if offset + limit < len(hits) else None,
            "sentences": "\n".join(sentence_line(c, i, s) for i, s in page)}  # fmt: skip


def _offline_embed(dim: int):
    """A bag-of-words hashing embedder for the tests; it shares no vector space with a real model."""
    import hashlib

    import numpy as np

    def embed(texts):
        out = np.zeros((len(texts), dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", str(t).lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0  # noqa: S324 (not security)
            n = np.linalg.norm(out[i])
            out[i] /= n or 1.0
        return out

    return embed


def embedder(model: str = EMBEDDING_MODEL, dim: int = 64):
    """``embed(texts) -> normalised vectors`` with sentence-transformers (the ``embed`` extra); the model loads
    from the Hugging Face cache (``HF_HOME``), offline when it is there. ``offline-test``: the tests' embedder."""
    if model == OFFLINE:
        return _offline_embed(dim)
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    if (hub / ("models--" + model.replace("/", "--"))).is_dir():  # cached: never ask the Hub
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from sentence_transformers import SentenceTransformer

    m = SentenceTransformer(model)
    return lambda texts: m.encode(list(texts), batch_size=32, show_progress_bar=False, normalize_embeddings=True)


def load_vectors(path: Path, n: int):
    import numpy as np

    vecs = np.load(path, mmap_mode="r")
    if len(vecs) != n:
        raise ValueError(f"{path} has {len(vecs)} vectors for {n} passages; rebuild the index")
    return vecs


def embedding_search(c: Corpus, vectors, embed, query: str, top_k: int = 5, manifest: dict | None = None) -> dict:
    """The ``top_k`` passages nearest ``query`` by cosine, as written, with PMID, page and title."""
    import numpy as np

    q = np.asarray(embed([query]))[0]
    scores = np.asarray(vectors @ q)
    order = np.argsort(-scores, kind="stable")[: max(1, min(int(top_k), 10))]
    out = []
    for i in (int(x) for x in order):
        p, m = c.passages[i], (manifest or {}).get(c.passages[i]["pmid"], {})
        out.append({"pmid": p["pmid"], "chunk": p["chunk"], "page": p["page"], "title": m.get("title", ""),
                    "cosine": round(float(scores[i]), 4), "text": c.texts[i]})  # fmt: skip
    return {"query": query, "passages": out}


def read_passage(c: Corpus, pmid: str, chunk: int, manifest: dict | None = None) -> dict:
    i = c.by_key.get(f"{str(pmid).strip()}:{int(chunk)}")
    if i is None:
        raise ValueError(f"no passage {pmid}:{chunk}")
    p, m = c.passages[i], (manifest or {}).get(c.passages[i]["pmid"], {})
    return {"pmid": p["pmid"], "chunk": p["chunk"], "page": p["page"], "citation": m.get("citation", ""),
            "title": m.get("title", ""), "text": c.texts[i]}  # fmt: skip
