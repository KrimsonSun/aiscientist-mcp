"""MCP server retigene-rag (stdio): search tools for an agent, keyword search first.

Flow for "every gene with a property": search_sentences (every page) -> grade_genes -> get_grades,
get_gene_evidence -> evaluate. For a question about one topic: search_passages, read_passage.
"""

import contextlib
import functools
import json
from typing import Any

from aiscientist_mcp.common.paths import child_dir
from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool

from . import SERVER_NAME, __version__
from .settings import Settings

server = make_server(
    SERVER_NAME,
    "Search tools over the RetiGene corpus: about 1,700 full-text papers on inherited retinal disease, 39,563 "
    "passages, 598,885 sentences. Use only what the tools return, and cite the PMID and page they give.\n"
    "- For a question that asks for every gene with a property (for example haploinsufficient genes), use "
    "search_sentences with the property's own words and their variants, and read every page of hits: the "
    "answers are often side remarks in papers about other genes, which only exact words find. Then call "
    "grade_genes once with the terms you searched (it grades every gene those sentences name; add genes you "
    "found otherwise), and answer from "
    "get_grades: the A genes, then the B genes apart, each with its quote, PMID and page. If grade_genes "
    "reports complete=false, call it again with the same arguments: it retries only the genes that failed.\n"
    "- For a question about one gene, disease or method, use search_passages (search by meaning), then "
    "read_passage for more context; add search_sentences for exact names.\n"
    "On the haploinsufficiency question, one search_sentences term ('haploinsufficien', 392 sentences) names "
    "all 15 genes of the curator reference; search_passages finds 1-4 of them in its top 1,000.",
)


@contextlib.contextmanager
def _call():
    """A tool body: prints go to stderr, and a refusal (a bad argument, the cost limit, a grading to resume)
    reaches the agent with its message instead of a generic error."""
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        with quiet():
            yield
    except (ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc


@functools.lru_cache(maxsize=2)
def _corpus(chunks: str, fingerprint: str):
    from .search import Corpus

    return Corpus.load(chunks)


@functools.lru_cache(maxsize=1)
def _symbols(path: str):
    from .core import hgnc_symbols

    return hgnc_symbols(path)


@functools.lru_cache(maxsize=1)
def _embedder(model: str, dim: int):
    from .search import embedder

    return embedder(model, dim)


def _load(cfg):
    index = cfg.index.check()
    return index, _corpus(str(index.chunks), index.fingerprint())


def _gdir(cfg, grading_id):
    return child_dir(cfg.data_dir, grading_id, "grading_id", must_exist="genes.csv")


def _strings(xs, limit, max_len=200):
    out = [" ".join(str(x).split()) for x in (xs or []) if str(x).strip()]
    if len(out) > limit or any(len(x) > max_len for x in out):
        raise ValueError(f"at most {limit} items of at most {max_len} characters each")
    return out


# ---- search ----------------------------------------------------------------------------------------


@tool(server)
def search_sentences(terms: list[str], gene: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Keyword search over every sentence of the corpus. Returns ALL sentences that hold any of the terms, one
    per line as 'PMID <pmid> p<page> c<chunk>: <sentence>', in pages of up to 200 (page with offset until
    next_offset is null). Case, spaces, hyphens and PDF ligatures are ignored and a term matches inside words,
    so 'haploinsufficien' finds haploinsufficient and haplo-insufficiency, and 'loss of function' finds
    loss-of-function. Terms need at least 4 letters. gene: only sentences that name that symbol as a word."""
    from .search import keyword_search

    cfg = Settings.from_env()
    with _call():
        _, c = _load(cfg)
        return keyword_search(c, _strings(terms, 20, 80), gene, limit, offset)


@tool(server)
def search_passages(query: str, top_k: int = 5) -> dict[str, Any]:
    """Search by meaning: the passages (about 300 words each) whose embedding is nearest the query, as written,
    with PMID, page, title and cosine. It ranks passages by their main topic, so it suits a question about one
    gene, disease or method, and misses claims made in passing: for every gene with a property, use
    search_sentences. At most 10."""
    from .search import embedding_search, load_vectors

    cfg = Settings.from_env()
    with _call():
        index, c = _load(cfg)
        vectors = load_vectors(index.vectors, len(c.passages))
        embed = _embedder(cfg.embedding_model, int(vectors.shape[1]))
        return embedding_search(c, vectors, embed, " ".join(str(query).split())[:2000], top_k, index.manifest_rows())


@tool(server)
def read_passage(pmid: str, chunk: int) -> dict[str, Any]:
    """The whole passage around a sentence (pmid and chunk as search_sentences gives them), with its paper's
    citation and title."""
    from .search import read_passage as read

    cfg = Settings.from_env()
    with _call():
        index, c = _load(cfg)
        return read(c, pmid, chunk, index.manifest_rows())


# ---- grading ---------------------------------------------------------------------------------------


@tool(server)
def grade_genes(question: str, terms: list[str], genes: list[str] | None = None, criteria: str = "",
                max_cost_usd: float = 2.0) -> dict[str, Any]:  # fmt: skip
    """Grade every gene that the sentences holding `terms` name (the words you searched with search_sentences,
    e.g. ['haploinsufficien']), plus any `genes` you add: A (stated), B (hedged or contested) or N (not
    supported), each from its own sentences, i.e. every corpus sentence that names the gene (symbol or HGNC
    alias) and holds one of the terms or a mechanism word. Claude Haiku answers the grading rules as checks
    for every gene, and Claude Sonnet answers them again for the genes it graded A or B. `criteria` empty =
    the lab's haploinsufficiency rules (A: the authors state, without a hedge, that loss of one copy causes a
    retinal or ocular disease, or a syndrome with an eye disease). About a minute and $0.40 for the 130 genes
    of 'haploinsufficien'; refuses above max_cost_usd (0 = no limit). Calling again with the same arguments
    reuses the grading, or retries the genes in failed_first_pass and failed_review when there are any."""
    from . import grade as G

    cfg = Settings.from_env()
    with _call():
        q = " ".join(str(question or "").split())
        if not q or len(q) > 2000:
            raise ValueError("question must be 1-2000 characters")
        ts = _strings(terms, 20, 80)
        gs = _strings(genes, G.MAX_GENES, 40)
        if not ts:
            raise ValueError("give the terms you searched, e.g. ['haploinsufficien']")
        index, c = _load(cfg)
        symbols = _symbols(str(index.hgnc()))
        found = G.genes_named(c, ts, symbols)
        forms, unknown = G.resolve_genes([*found, *gs], symbols)
        if not forms:
            raise ValueError(f"no HGNC gene is named in the sentences that hold {ts}")
        if len(forms) > G.MAX_GENES:
            raise ValueError(f"{len(forms)} genes to grade, more than {G.MAX_GENES}: use more specific terms")
        models = {"judge_model": cfg.judge_model, "judge_effort": cfg.judge_effort, "review_model": cfg.review_model,
                  "review_effort": cfg.review_effort, "corpus": cfg.corpus, "review_of": "first-pass A and B"}  # fmt: skip
        gid = G.grading_id(q, sorted(forms), ts, criteria.strip(), models, index.fingerprint())
        d = child_dir(cfg.data_dir, gid, "grading_id")
        if (d / "grading.json").exists():
            out = json.loads((d / "grading.json").read_text())
            if out.get("complete", True):
                return {**out, "reused": True}  # one with failed genes runs again, retrying only those
        d.mkdir(parents=True, exist_ok=True)
        st = {"grading_id": gid, "question": q, "terms": ts, "genes_from_terms": len(found), "genes_added": gs,
              "genes": sorted(forms), "criteria": criteria.strip(), "not_in_hgnc": unknown,
              "max_cost_usd": float(max_cost_usd), "workers": cfg.workers, "server_version": __version__,
              **models}  # fmt: skip
        (d / "state.json").write_text(json.dumps(st, indent=1) + "\n")
        rows = G.evidence(c, forms, ts, index.manifest_rows())
        return G.run(d, c, rows, forms, st)


@tool(server)
def get_grades(grading_id: str, grades: str = "A,B", top: int = 500) -> dict[str, Any]:
    """The gene table of a grading, A first, then B, then N (by papers within a grade): gene, grade, claim,
    papers, passages, supporting PMIDs, the quoted sentence with its PMID, page and title, whether the quote was
    found verbatim, the rule checks, the reason, the first-pass grade and the review model. `grades`:
    comma-separated grades to return (default A,B)."""
    from .grade import read_csv

    cfg = Settings.from_env()
    with _call():
        d = _gdir(cfg, grading_id)
        want = {g.strip().upper() for g in grades.split(",") if g.strip()} or {"A", "B", "N"}
        rows = [r for r in read_csv(d / "genes.csv") if r["grade"] in want]
        counts = {g: sum(1 for r in rows if r["grade"] == g) for g in ("A", "B", "N") if g in want}
        return {"grading_id": grading_id, "file": str(d / "genes.csv"), "counts": counts,
                "rows": rows[: max(1, min(int(top), 2000))]}  # fmt: skip


@tool(server)
def get_gene_evidence(
    grading_id: str, gene: str = "", pmid: str = "", offset: int = 0, limit: int = 50
) -> dict[str, Any]:
    """The sentence table of a grading: one row per evidence sentence that names an A or B gene, with gene,
    grade, the sentence as written, PMID, page, chunk, citation, title and year. Filter by gene or pmid; page
    with offset/limit (limit at most 200)."""
    from .grade import read_csv

    cfg = Settings.from_env()
    with _call():
        d = _gdir(cfg, grading_id)
        rows = [r for r in read_csv(d / "sentences.csv")
                if (not gene or r["gene"] == gene.strip().upper()) and (not pmid or r["pmid"] == pmid.strip())]  # fmt: skip
        lim = max(1, min(int(limit), 200))
        return {"file": str(d / "sentences.csv"), "total": len(rows), "offset": int(offset),
                "rows": rows[int(offset) : int(offset) + lim]}  # fmt: skip


@tool(server)
def evaluate(grading_id: str, reference: str = "") -> dict[str, Any]:
    """Score a grading against a graded reference (default: the curator review of 2026-11-04, confirmed by Dr.
    Chen: 15 A, 7 B, 2 N). Returns A recall (all, the 10 ocular, the 5 syndromic), the B genes listed apart or
    as A, the N genes listed, the A genes outside the reference, and pass flags."""
    from . import evaluate as ev
    from .grade import read_csv

    cfg = Settings.from_env()
    with _call():
        d = _gdir(cfg, grading_id)
        out = ev.score(read_csv(d / "genes.csv"), ev.load_reference(reference))
        (d / "evaluation.json").write_text(json.dumps(out, indent=1) + "\n")
        g = json.loads((d / "grading.json").read_text())
        return {"grading_id": grading_id, **out, "grading_total": g["total"]}


@tool(server)
def list_gradings() -> dict[str, Any]:
    """Gradings in this server's data directory, with their question, gene count and grades."""
    cfg = Settings.from_env()
    out = []
    for p in sorted(cfg.data_dir.glob("*/grading.json")):
        g = json.loads(p.read_text())
        out.append({"grading_id": g["grading_id"], "question": g["question"], "genes": g["genes"],
                    "grades": g["grades"], "cost_usd": g["total"]["cost_usd"]})  # fmt: skip
    return {"data_dir": str(cfg.data_dir), "gradings": out}


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
