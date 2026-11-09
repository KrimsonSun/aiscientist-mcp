"""The search tools: keyword search over every sentence, embedding search over passages, a passage by id.

Keyword search is the main tool. A question that asks for every gene with a property is answered by the
sentences that use the property's words, and those sentences sit in passages about many other topics.
On the RetiGene corpus, the sentences that say "haploinsufficien" name all 15 genes of the curator
reference, while the top 1,000 passages by chunk embedding hold 1-4 of them: a passage's vector is its
topic, and the claim is a side remark. Embedding search suits a question about one topic.

Keywords match the folded text: lowercase, PDF ligatures as letters, spaces and hyphens removed, so
"loss of function" finds "loss-of-function" and "haploinsufficien" finds "haploinsuf- ficiency".
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .corpus import fold as nfkc
from .settings import EMBEDDING_MODEL, EMBEDDING_REVISION

OFFLINE = "offline-test"
MIN_TERM = 4  # folded characters; shorter terms match inside too many unrelated words
_SQUASH = re.compile(r"[\s\-\u00ad\u2010\u2011]+")


def fold(text: str) -> str:
    """Lowercase, PDF ligatures as letters, and no spaces or hyphens: how keywords are matched."""
    return _SQUASH.sub("", nfkc(text).lower())


@dataclass
class Corpus:
    sentences: list[tuple[int, str, str, str]]  # passage index, sid, sentence, folded sentence
    passages: list[dict]  # pmid, chunk, page, text
    by_key: dict[str, int]  # "pmid:chunk" -> passage index

    @classmethod
    def load(cls, sentences: Path, chunks: Path) -> Corpus:
        passages, by_key = [], {}
        with Path(chunks).open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                pmid, text = str(row.get("pmid") or "").strip(), str(row.get("text") or "")
                key = f"{pmid}:{row.get('chunk')}"
                if pmid and text.strip() and key not in by_key:
                    by_key[key] = len(passages)
                    passages.append({"pmid": pmid, "chunk": row.get("chunk"), "page": row.get("page"), "text": text})
        rows = []
        with Path(sentences).open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    s = json.loads(line)
                except ValueError:
                    continue
                text = str(s.get("text") or "")
                i = by_key.get(f"{s.get('pmid')}:{s.get('chunk')}")
                if text.strip() and i is not None:
                    rows.append((i, str(s.get("sid") or ""), text, fold(text)))
        return cls(rows, passages, by_key)


def sentence_line(c: Corpus, i: int, sid: str, sentence: str) -> str:
    return f"PMID {c.passages[i]['pmid']} p{c.passages[i]['page']} c{c.passages[i]['chunk']}: {sentence}"


def keyword_search(c: Corpus, terms: list[str], gene: str = "", limit: int = 100, offset: int = 0) -> dict:
    """Every sentence that holds any of ``terms`` (and names ``gene`` as a word, when given), a page at a time."""
    ts = [t for t in dict.fromkeys(fold(t) for t in terms) if t]
    if not ts or any(len(t) < MIN_TERM for t in ts):
        raise ValueError(f"give terms of at least {MIN_TERM} letters, e.g. 'haploinsufficien' or 'loss of function'")
    grx = re.compile(rf"\b{re.escape(gene.strip())}\b") if gene.strip() else None
    hits = [(i, sid, s) for i, sid, s, f in c.sentences if any(t in f for t in ts) and (grx is None or grx.search(s))]
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    page = hits[offset : offset + limit]
    return {"terms": ts, "gene": gene.strip(), "total_sentences": len(hits),
            "total_passages": len({i for i, _, _ in hits}), "offset": offset,
            "next_offset": offset + limit if offset + limit < len(hits) else None,
            "sentences": "\n".join(sentence_line(c, i, sid, s) for i, sid, s in page)}  # fmt: skip


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
    """``embed(texts) -> normalised vectors`` with sentence-transformers (the ``embed`` extra), at the revision
    that made the chunk vectors (``RETIGENE_EMBEDDING_REVISION``); the model loads from the Hugging Face cache
    (``HF_HOME``; the image has it), offline when it is there. ``offline-test``: the tests' embedder."""
    if model == OFFLINE:
        return _offline_embed(dim)
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    if (hub / ("models--" + model.replace("/", "--"))).is_dir():  # cached: never ask the Hub
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "search_passages needs the embed extra: pip install 'aiscientist-mcp-retigene-rag[embed]' "
            "(or use the server image)"
        ) from exc

    revision = os.environ.get("RETIGENE_EMBEDDING_REVISION", EMBEDDING_REVISION if model == EMBEDDING_MODEL else "")
    m = SentenceTransformer(model, revision=revision or None)
    return lambda texts: m.encode(list(texts), batch_size=32, show_progress_bar=False, normalize_embeddings=True)


def load_vectors(path: Path, n: int):
    import numpy as np

    vecs = np.load(path, mmap_mode="r")
    if len(vecs) != n:
        raise ValueError(f"{path} has {len(vecs)} vectors for {n} passages; the index is inconsistent")
    return vecs


def embedding_search(c: Corpus, vectors, embed, query: str, terms: list[str] | None = None, top_k: int = 5,
                     manifest: dict | None = None) -> dict:  # fmt: skip
    """The ``top_k`` passages nearest ``query`` by cosine, as written, with PMID, page and title. With
    ``terms``, only passages that hold one of them (folded, as in keyword search) are ranked."""
    import numpy as np

    ts = [t for t in dict.fromkeys(fold(t) for t in terms or []) if t]
    if any(len(t) < MIN_TERM for t in ts):
        raise ValueError(f"terms need at least {MIN_TERM} letters")
    q = np.asarray(embed([query]))[0]
    scores = np.asarray(vectors @ q)
    pool = None
    if ts:
        pool = sorted({i for i, _, _, f in c.sentences if any(t in f for t in ts)})
        if not pool:
            return {"query": query, "terms": ts, "passages_with_terms": 0, "passages": []}
        order = sorted(pool, key=lambda i: -scores[i])
    else:
        order = [int(x) for x in np.argsort(-scores, kind="stable")]
    out = []
    for i in order[: max(1, min(int(top_k), 10))]:
        p, m = c.passages[i], (manifest or {}).get(c.passages[i]["pmid"], {})
        out.append({"pmid": p["pmid"], "chunk": p["chunk"], "page": p["page"], "title": m.get("title", ""),
                    "cosine": round(float(scores[i]), 4), "text": p["text"]})  # fmt: skip
    res = {"query": query, "passages": out}
    if ts:
        res |= {"terms": ts, "passages_with_terms": len(pool)}
    return res


def read_passage(c: Corpus, pmid: str, chunk: int, manifest: dict | None = None) -> dict:
    i = c.by_key.get(f"{str(pmid).strip()}:{int(chunk)}")
    if i is None:
        raise ValueError(f"no passage {pmid}:{chunk}")
    p, m = c.passages[i], (manifest or {}).get(c.passages[i]["pmid"], {})
    return {"pmid": p["pmid"], "chunk": p["chunk"], "page": p["page"], "citation": m.get("citation", ""),
            "title": m.get("title", ""), "doi": m.get("doi", ""), "text": p["text"]}  # fmt: skip
