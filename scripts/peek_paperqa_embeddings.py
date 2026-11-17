#!/usr/bin/env python3
"""Peek at a finished PaperQA embedding index — what the vectors actually look like.

Run on a compute node inside the published literature image, after the embed job has written the index:

    python scripts/peek_paperqa_embeddings.py \
        /path/to/retigene/index_pubmedbert/retigene_full_pubmedbert

If you pass no path it falls back to the local 50-paper MiniLM pilot.

Each doc file is a zlib-compressed pickle of a paperqa `Docs` object:
  Docs.docs   -> {doc_id: Doc}      (paper-level metadata: citation, docname, dockey)
  Docs.texts  -> [Text, ...]        (the chunks; Text.text is the snippet,
                                     Text.embedding is the vector)
"""

import contextlib
import glob
import os
import pickle
import sys
import zlib

import numpy as np

# paperqa's Docs.__setstate__ assumes a live pydantic private state; when we load a
# pickle purely to inspect it (no LLM/client), that assumption trips. Neutralize it.
try:
    import paperqa.docs as _pd

    def _safe_setstate(self, state):
        self.__dict__.update(state.get("__dict__", {}))
        for k, v in state.items():
            if k != "__dict__":
                with contextlib.suppress(Exception):
                    object.__setattr__(self, k, v)

    _pd.Docs.__setstate__ = _safe_setstate
except Exception as e:  # pragma: no cover
    print("warn: could not patch paperqa Docs.__setstate__:", e)

DEFAULT = "output/retigene_papers/paperqa_pilot_50/index_minilm/retigene_pilot_50_minilm"


def load(path):
    with open(path, "rb") as stream:
        return pickle.loads(zlib.decompress(stream.read()))  # noqa: S301 - operator-owned trusted PaperQA cache


def main():
    index_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    docs_dir = os.path.join(index_dir, "docs")
    files = sorted(glob.glob(os.path.join(docs_dir, "*.zip")))
    if not files:
        sys.exit(f"no doc files under {docs_dir}")

    print(f"index dir : {index_dir}")
    print(f"papers    : {len(files)} doc files\n")

    # --- one chunk, in full detail ---
    d = load(files[0])
    texts = d.texts
    t = texts[0]
    emb = np.asarray(t.embedding, dtype=float)
    doc = next(iter(d.docs.values()))
    print("=== one paper ===")
    print("citation :", str(getattr(doc, "citation", ""))[:100])
    print("chunks   :", len(texts))
    print("\n=== one chunk ===")
    print("name     :", t.name)
    print("text     :", t.text[:200].replace("\n", " "), "...")
    print(f"embedding: dim={emb.shape[0]} dtype={emb.dtype}")
    print("first 10 :", np.round(emb[:10], 4))
    print("L2 norm  :", round(float(np.linalg.norm(emb)), 4))

    # --- aggregate over a sample of papers ---
    dims, norms, nchunks = set(), [], []
    for f in files[:25]:
        dd = load(f)
        nchunks.append(len(dd.texts))
        for x in dd.texts:
            v = np.asarray(x.embedding, dtype=float)
            dims.add(v.shape[0])
            norms.append(float(np.linalg.norm(v)))
    print(f"\n=== sampled {min(25, len(files))} papers ===")
    print("embedding dim(s) :", sorted(dims), "  (PubMedBERT=768, MiniLM=384)")
    print(f"chunks/paper     : min={min(nchunks)} mean={sum(nchunks) / len(nchunks):.1f} max={max(nchunks)}")
    print("vector L2 norm   : mean=%.3f (≈1.0 => normalized, cosine = dot product)" % (sum(norms) / len(norms)))


if __name__ == "__main__":
    main()
