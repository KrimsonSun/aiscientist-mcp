"""Retained scientific ordering/scope contracts from the app retrieval benchmark."""

import pytest

np = pytest.importorskip("numpy")

from aiscientist_mcp.literature import corpus as C  # noqa: E402
from aiscientist_mcp.literature import search as sc  # noqa: E402


@pytest.fixture
def tiny():
    chunks = [
        {"pmid": pmid, "chunk": chunk, "text": text}
        for pmid, chunk, text in [
            ("a", 0, "RPGR marker"),
            ("a", 1, "RPGR marker marker"),
            ("b", 0, "rescue pathway"),
            ("c", 0, "rescue rescue pathway"),
            ("d", 0, "unrelated"),
        ]
    ]
    vecs = C.l2([[0, 1], [0.1, 1], [1, 0], [0.8, 0.6], [0.6, 0.8]])
    return chunks, vecs


def embed(queries):
    return np.asarray([[1, 0]] * len(queries), dtype="float32")


def test_default_hybrid_is_unchanged(tiny):
    chunks, vecs = tiny
    queries = ["RPGR rescue", "pathway"]
    kwargs = {"top_k": 5, "max_per_paper": 2}
    assert sc.rank_passages(queries, chunks, vecs, embed(queries), **kwargs) == sc.rank_passages(
        queries, chunks, vecs, embed(queries), method="hybrid", **kwargs
    )
    index = C.TextIndex.of_chunks(chunks)
    terms = index.check(C.query_terms(queries[0]))
    assert C.hybrid_rank(embed(queries)[0], vecs, index, terms) == C.hybrid_rank(
        embed(queries)[0], vecs, index, terms, method="hybrid"
    )


def test_baseline_orderings(tiny):
    chunks, vecs = tiny
    for method, expected in [("dense", [2, 3, 4, 1, 0]), ("bm25", [3, 2])]:
        picked, _ = sc.rank_passages(["rescue"], chunks, vecs, embed([""]), top_k=5, max_per_paper=2, method=method)
        assert [r["idx"] for r in picked] == expected
    with pytest.raises(ValueError, match="Unknown retrieval"):
        sc.rank_passages(["rescue"], chunks, vecs, embed([""]), top_k=1, max_per_paper=1, method="bad")


@pytest.mark.parametrize("method", ("hybrid", "dense", "bm25"))
def test_shared_dedupe_caps_and_scope(tiny, method):
    chunks, vecs = tiny
    queries = ["RPGR rescue", "RPGR rescue"]
    picked, _ = sc.rank_passages(
        queries, chunks, vecs, embed(queries), top_k=10, max_per_paper=1, method=method, allowed={"a", "b", "c"}
    )
    assert len(picked) == 3
    assert len({r["idx"] for r in picked}) == 3
    assert {chunks[r["idx"]]["pmid"] for r in picked} == {"a", "b", "c"}
