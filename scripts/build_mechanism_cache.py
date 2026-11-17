"""Build the mechanism cache inside the independently published literature MCP image.

Run on a compute node with papers/model cache bound to explicit reference roots.
The app may stage this operator script from an explicitly configured MCP checkout;
scientific dependencies come from the image, never the gateway's Python environment.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiscientist_mcp.literature import corpus as _CORPUS
from aiscientist_mcp.literature import extract as _EXTRACT


def l2(*args, **kwargs):
    return _CORPUS.l2(*args, **kwargs)


def build_text_cache(*args, **kwargs):
    return _EXTRACT.build_text_cache(*args, **kwargs)


def chunk_vectors(*args, **kwargs):
    return _CORPUS.chunk_vectors(*args, **kwargs)


def load_chunks(*args, **kwargs):
    return _CORPUS.load_chunks(*args, **kwargs)


def sentence_transformer_embed(*args, **kwargs):
    return _CORPUS.sentence_transformer_embed(*args, **kwargs)


MARKER = "AISCIENTIST_INDEX_BUILD "  # gateway/corpus_index.REBUILD_MARKER reads it
SAMPLE = 4  # cached vectors embedded again before only new chunks are
BATCH = 256


def extend_vectors(
    chunks: list[dict[str, Any]], cache: Path, embed: Callable[[list[str]], Any], log: Callable[[str], None]
) -> bool:
    """Embed only the chunks after those ``chunk_vecs.npy`` has vectors for, and append them. True
    when the vectors now cover every chunk; False when every chunk must be embedded again (no
    vectors, more vectors than chunks, or cached vectors that are not those of the same chunks)."""
    import numpy as np

    path = cache / "chunk_vecs.npy"
    if not path.is_file():
        return False
    old = np.load(path)
    n = old.shape[0]
    if n == len(chunks):
        return True
    if n == 0 or n > len(chunks):
        return False
    idx = sorted({int(i) for i in np.linspace(0, n - 1, min(SAMPLE, n))})
    again = l2(embed([chunks[i]["text"] for i in idx]))
    if float(np.min(np.sum(again * old[idx], axis=1))) < 0.999:
        log("the cached vectors are not those of the cached chunks; embedding every chunk again")
        return False
    parts = [old]
    for i in range(n, len(chunks), BATCH):
        parts.append(l2(embed([c["text"] for c in chunks[i : i + BATCH]])).astype(old.dtype))
    np.save(path, np.concatenate(parts))
    log(f"embedded {len(chunks) - n} new chunks ({n} cached)")
    return True


def _stats(d: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in d.iterdir() if p.is_file()}


def _counts(cache: Path) -> tuple[int, int, int]:
    """(chunks, papers, vector rows) of a cache."""
    import numpy as np

    chunks, pmids = 0, set()
    with (cache / "chunks.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                chunks += 1
                pmids.add(str(json.loads(line).get("pmid")))
    vecs = cache / "chunk_vecs.npy"
    rows = int(np.load(vecs, mmap_mode="r").shape[0]) if vecs.is_file() else -1
    return chunks, len(pmids), rows


def build_beside(out: Path, build: Callable[[Path], None], *, vectors: bool = True) -> dict[str, Any]:
    """Run ``build`` on ``<out>.next`` (a copy of ``out``), check it and put it in place."""
    nxt, prev = out.with_name(out.name + ".next"), out.with_name(out.name + ".prev")
    if nxt.exists():
        shutil.rmtree(nxt)
    existed = out.is_dir()
    if existed:
        shutil.copytree(out, nxt)  # copy2: the modification times are kept
    else:
        nxt.mkdir(parents=True)
    before = _stats(nxt)
    try:
        build(nxt)
        chunks, papers, rows = _counts(nxt)
        if vectors and rows != chunks:
            raise RuntimeError(f"the new cache has {rows} vectors for {chunks} chunks")
    except BaseException:
        shutil.rmtree(nxt, ignore_errors=True)
        raise
    old_chunks = _counts(out)[0] if existed else 0
    summary = {
        "changed": not existed or _stats(nxt) != before,
        "chunks": chunks,
        "papers": papers,
        "new_chunks": chunks - old_chunks,
        "cache": str(out),
    }
    if not summary["changed"]:
        shutil.rmtree(nxt)
        return summary
    _share(nxt)
    if prev.exists():
        try:
            shutil.rmtree(prev)
        except OSError:
            # Another lab member's files, in a folder only they may write: set aside, not deleted.
            kept = prev.with_name(f"{prev.name}-{time.strftime('%Y%m%d-%H%M%S')}")
            os.rename(prev, kept)
            summary["not_removed"] = str(kept)
    if existed:
        os.rename(out, prev)
    os.rename(nxt, out)
    return summary


def _share(d: Path) -> None:
    """Group-writable, so whichever lab member rebuilds next can replace it (the corpus root is a
    shared folder; the setgid bit keeps the lab's group on new files)."""
    for p in [d, *d.rglob("*")]:
        with contextlib.suppress(OSError):
            os.chmod(p, 0o2775 if p.is_dir() else 0o664)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--papers", required=True, help="the PDF corpus directory")
    ap.add_argument("--manifest", default="", help="paperqa_manifest.csv (file_location, title, doc_id)")
    ap.add_argument("--out", default="", help="cache directory (default: <papers>/../mechanism_cache)")
    ap.add_argument("--no-vectors", action="store_true", help="text only; skip the embedding pass")
    ap.add_argument(
        "--replace", action="store_true", help="build beside the cache and put the result in place (see above)"
    )
    ns = ap.parse_args(argv)
    papers = Path(ns.papers)
    out = Path(ns.out) if ns.out else papers.parent / "mechanism_cache"
    log = lambda s: print(s, flush=True)  # noqa: E731
    hf = papers.parent / "hf_cache"
    model: dict[str, Any] = {}

    def embed(texts: list[str]) -> Any:  # the model is loaded only when something needs it
        if "fn" not in model:
            model["fn"] = sentence_transformer_embed(hf if hf.is_dir() else None)
            if model["fn"] is None:
                raise RuntimeError("sentence-transformers or the PubMedBERT model is not available")
        return model["fn"](texts)

    def build(cache: Path) -> None:
        _, chunk_path = build_text_cache(papers, Path(ns.manifest) if ns.manifest else None, cache, log=log)
        if ns.no_vectors:
            return
        chunks = load_chunks(chunk_path)
        if not extend_vectors(chunks, cache, embed, log):
            chunk_vectors(chunks, cache, embed, writable=True, log=log)

    try:
        if not ns.replace:
            build(out)
            return 0
        summary = build_beside(out, build, vectors=not ns.no_vectors)
    except RuntimeError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1
    log(
        ("index updated: " if summary["changed"] else "no new papers; index unchanged: ")
        + f"{summary['papers']} papers, {summary['chunks']} chunks ({summary['new_chunks']} new)"
    )
    print(MARKER + json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
