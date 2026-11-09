"""MCP server retigene-rag (stdio): search tools and a corpus-wide curation over the RetiGene papers.

Flow for "every gene with a property": search_sentences -> submit_curation (curation_status until
COMPLETED) -> get_genes, get_evidence -> write_report; evaluate scores it against the curator reference.
For a question about one topic: search_passages, read_passage, search_sentences.
"""

import contextlib
import functools
import json
import sys
from typing import Any

from aiscientist_mcp.common import jobs
from aiscientist_mcp.common.paths import child_dir
from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool

from . import SERVER_NAME
from .settings import Settings

server = make_server(
    SERVER_NAME,
    "Search tools and a corpus-wide curation over the RetiGene corpus: about 1,700 full-text papers on "
    "inherited retinal disease (39,563 passages, 426,706 sentences). Use only what the tools return, and cite "
    "the PMID and page they give.\n"
    "- For a question that asks for every gene with a property (for example haploinsufficient genes): "
    "search_sentences with the property's own words and their variants (e.g. 'haploinsufficien', 'one "
    "functional copy') to see what the corpus says, then submit_curation once with those terms. It reads "
    "EVERY sentence that holds them or states the property in words, grades each gene from its own sentences "
    "(A stated, B hedged or contested, N not supported) and returns when done (else poll curation_status). "
    "Answer from get_genes: the A genes, then the B genes apart, each with its quote, PMID and page. "
    "get_evidence gives a gene's sentences; write_report writes both tables as Markdown. "
    "submit_curation(estimate_only=true) gives the calls, tokens and cost before anything runs.\n"
    "- For a question about one gene, disease or method: search_passages (by meaning; with terms, only "
    "passages that hold them are ranked), then read_passage for context; search_sentences for exact names.\n"
    "The answers to a list question are often side remarks in papers about other genes, which only exact "
    "words find: the sentences that say 'haploinsufficien' name all 15 genes of the curator reference, while "
    "search_passages finds 1-4 of them in its top 1,000. The curation's rules are those for "
    "haploinsufficiency; for another property, read the sentences with search_sentences and read_passage.",
)

COMPACT_GENE = ("gene", "grade", "phenotype_kind", "phenotype", "confidence", "pmid", "page", "quote", "sentence_id",
                "n_papers", "n_sentences", "code_adjusted")  # fmt: skip
COMPACT_SENTENCE = ("gene", "sentence_id", "pmid", "page", "dosage_claim", "hedge", "mechanism_stated",
                    "phenotype_kind", "evidence_type", "meets_a", "contests", "quote", "title")  # fmt: skip


@contextlib.contextmanager
def _call():
    """A tool body: prints go to stderr, and a refusal (a bad argument, the cost limit, an unfinished
    curation) reaches the agent with its message instead of a generic error."""
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        with quiet():
            yield
    except (ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc


@functools.lru_cache(maxsize=1)
def _corpus(sentences: str, chunks: str, fingerprint: str):
    from .search import Corpus

    return Corpus.load(sentences, chunks)


@functools.lru_cache(maxsize=1)
def _loaded(root: str, fingerprint: str):
    from .curate import Loaded

    return Loaded(Settings.from_env().index)


@functools.lru_cache(maxsize=1)
def _embedder(model: str, dim: int):
    from .search import embedder

    return embedder(model, dim)


@functools.lru_cache(maxsize=1)
def _manifest(path: str, fingerprint: str):
    from .corpus import read_manifest

    return read_manifest(path)


def _index(cfg):
    return cfg.index.check()


def _search_corpus(cfg):
    index = _index(cfg)
    return index, _corpus(str(index.sentences), str(index.chunks), index.fingerprint())


def _job_cfg():
    return jobs.JobSettings.from_env("--cpus-per-task=2 --mem=16G --time=02:00:00")


def _cdir(cfg, curation_id, must_exist="request.json"):
    return child_dir(cfg.data_dir, curation_id, "curation_id", must_exist=must_exist)


def _strings(xs, limit, max_len=80):
    out = [" ".join(str(x).split()) for x in (xs or []) if str(x).strip()]
    if len(out) > limit or any(len(x) > max_len for x in out):
        raise ValueError(f"at most {limit} items of at most {max_len} characters each")
    return out


def _clip(row: dict, keys, quote=300) -> dict:
    out = {k: row.get(k, "") for k in keys}
    for k in ("quote", "title", "phenotype"):
        if k in out and len(str(out[k])) > quote:
            out[k] = str(out[k])[: quote - 1] + "…"
    return out


def _summary(d, state: dict) -> dict:
    out = {"curation_id": d.name, "state": state.get("state")}
    for k in ("job_id", "note", "error", "elapsed_s"):
        if state.get(k):
            out[k] = state[k]
    cur = d / "curation.json"
    if out["state"] == "COMPLETED" and cur.is_file():
        c = json.loads(cur.read_text(encoding="utf-8"))
        out |= {k: c[k] for k in ("question", "grades", "genes_A", "genes_B", "unresolved", "index", "models", "total")}
        out["found"] = {k: c["found"][k] for k in ("sentences", "papers", "genes")}
        out["terms"] = c["terms"]
        out["files"] = {"genes": str(d / "genes.csv"), "sentences": str(d / "sentences.csv")}
    elif out["state"] in ("PENDING", "RUNNING"):
        req = json.loads((d / "request.json").read_text(encoding="utf-8"))
        try:
            prog = json.loads((d / "progress.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prog = {"model_calls": {"check": 0, "review": 0}}
        calls = prog["model_calls"]
        lo, hi = req["estimate"]["calls"]
        out["progress"] = f"{calls['check'] + calls['review']} model calls of about {lo}-{hi}"
    return out


def _completed(cfg, curation_id):
    d = _cdir(cfg, curation_id)
    st = jobs.status(_job_cfg(), d)
    if st.get("state") != "COMPLETED" or not (d / "genes.csv").is_file():
        raise ValueError(f"curation {curation_id} is {st.get('state')}, not COMPLETED: poll curation_status")
    return d


# ---- search ----------------------------------------------------------------------------------------


@tool(server)
def search_sentences(terms: list[str], gene: str = "", limit: int = 40, offset: int = 0) -> dict[str, Any]:
    """Keyword search over every sentence of the corpus. Returns ALL sentences that hold any of the terms, one
    per line as 'PMID <pmid> p<page> c<chunk>: <sentence>', in pages of `limit` (at most 200; page with
    offset until next_offset is null), with the total count. Case, spaces, hyphens and PDF ligatures are
    ignored and a term matches inside words, so 'haploinsufficien' finds haploinsufficient and
    haplo-insufficiency, and 'loss of function' finds loss-of-function. Terms need at least 4 letters.
    gene: only sentences that name that symbol as a word."""
    from .search import keyword_search

    cfg = Settings.from_env()
    with _call():
        _, c = _search_corpus(cfg)
        return keyword_search(c, _strings(terms, 20), gene, limit, offset)


@tool(server)
def search_passages(query: str, terms: list[str] | None = None, top_k: int = 5) -> dict[str, Any]:
    """Search by meaning (chunk co-embedding, PubMedBERT): the passages (about 300 words each) nearest the query,
    as written, with PMID, page, title and cosine. With `terms`, only passages holding one of them are ranked
    (keywords and embedding together). It ranks passages by their main topic, so it suits a question about one
    gene, disease or method, and misses claims made in passing: for every gene with a property, use
    search_sentences and submit_curation. At most 10."""
    from .search import embedding_search, load_vectors

    cfg = Settings.from_env()
    with _call():
        index, c = _search_corpus(cfg)
        vectors = load_vectors(index.vectors, len(c.passages))
        embed = _embedder(cfg.embedding_model, int(vectors.shape[1]))
        manifest = _manifest(str(index.manifest), index.fingerprint())
        q = " ".join(str(query).split())[:2000]
        return embedding_search(c, vectors, embed, q, _strings(terms, 20), top_k, manifest)


@tool(server)
def read_passage(pmid: str, chunk: int) -> dict[str, Any]:
    """The whole passage around a sentence (pmid and chunk as search_sentences gives them), with its paper's
    citation, title and DOI."""
    from .search import read_passage as read

    cfg = Settings.from_env()
    with _call():
        index, c = _search_corpus(cfg)
        return read(c, pmid, chunk, _manifest(str(index.manifest), index.fingerprint()))


# ---- curation --------------------------------------------------------------------------------------


@tool(server)
def submit_curation(question: str, terms: list[str] | None = None, estimate_only: bool = False,
                    max_cost_usd: float = 5.0, rerun: bool = False) -> dict[str, Any]:  # fmt: skip
    """The corpus-wide curation of haploinsufficient genes. Reads EVERY sentence of the corpus that states a
    dosage mechanism in words (haploinsufficiency, one functional copy, reduced dosage, ...) or holds one of
    `terms` (the words you searched with search_sentences), groups them by the gene named in or next to each
    sentence, and grades each gene from its own sentences by the curator's rules: A, the authors state
    without a hedge that loss of one copy causes a retinal or ocular disease, or a syndrome with one; B,
    hedged, contested by another paper, or one of several mechanisms; N, not supported. A cheap model reads
    each gene's sentences twice, a review model grades the genes with a claim, and the code checks every
    quote verbatim. About 300 genes, 4-5 minutes and $0.30 with Claude Haiku. estimate_only=true: the
    genes, calls, tokens and cost, with no model call. Refuses when the estimate's upper end exceeds
    max_cost_usd (0 = no limit). A curation with the same terms, rules, models and corpus is reused (rerun=true
    runs it again; model replies are still reused from the cache). Returns the curation_id and its state;
    with the inline executor it returns when the curation is COMPLETED, else poll curation_status."""
    from . import curate

    cfg, jc = Settings.from_env(), _job_cfg()
    with _call():
        q = " ".join(str(question or "").split())
        if not q or len(q) > 2000:
            raise ValueError("question must be 1-2000 characters")
        ts = _strings(terms, curate.MAX_TERMS)
        index = _index(cfg)
        loaded = _loaded(str(index.root), index.fingerprint())
        found_terms, _groups, found = curate.find(loaded, ts)
        merged = found.pop("merged")
        if not merged:
            raise ValueError("no HGNC gene is named in or next to the sentences these terms find")
        est = curate.estimate(merged, loaded.titles, cfg.models(), cfg.workers)
        cid = curate.curation_id(ts, cfg.models(), index.fingerprint())
        d = child_dir(cfg.data_dir, cid, "curation_id")
        info = {"curation_id": cid, "estimate": est, "found": found,
                "terms": [{"term": t.text, "source": t.source, "n_sentences": t.n_sentences} for t in found_terms],
                "models": cfg.models()}  # fmt: skip
        state = jobs.status(jc, d) if (d / "state.json").exists() else {}
        if not rerun and state.get("state") in ("COMPLETED", "PENDING", "RUNNING"):
            return {**_summary(d, state), "reused": True, "estimate": est}
        if estimate_only:
            return {**info, "state": "ESTIMATE"}
        if max_cost_usd > 0 and est["cost_usd"][1] > max_cost_usd:
            raise ValueError(f"the curation could cost up to ${est['cost_usd'][1]:.2f} (estimate "
                             f"${est['cost_usd'][0]:.2f}-{est['cost_usd'][1]:.2f}, {est['genes']} genes), above "
                             f"max_cost_usd={max_cost_usd}: call again with a higher max_cost_usd or narrower terms")  # fmt: skip
        d.mkdir(parents=True, exist_ok=True)
        for f in ("curation.json", "genes.csv", "sentences.csv", "evaluation.json", "report.md"):
            (d / f).unlink(missing_ok=True)
        req = {"question": q, "terms": ts, "models": cfg.models(), "workers": cfg.workers, "estimate": est,
               "index": index.version()}  # fmt: skip
        (d / "request.json").write_text(json.dumps(req, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        jobs.write_state(d, {"curation_id": cid, "question": q})
        if jc.executor == "inline":  # this process has the corpus loaded already: run here, as the job would
            _run_inline(d, lambda: curate.run(d, index, cfg.cache_dir, loaded))
        else:
            cmd = [sys.executable, "-m", "aiscientist_mcp.retigene_rag.curate", "--dir", str(d)]
            jobs.submit(jc, cmd, d, f"{SERVER_NAME}_{cid}")
        return {**_summary(d, jobs.status(jc, d)), "estimate": est}


def _run_inline(d, fn) -> None:
    """``fn`` in this process, recorded as an inline job is (``state.json``, ``done.json``)."""
    import time
    import traceback

    jobs.write_state(d, jobs.read_state(d) | {"executor": "inline", "state": "RUNNING", "submitted": time.time()})
    (d / "done.json").unlink(missing_ok=True)
    t0 = time.time()
    try:
        fn()
        done = {"state": "COMPLETED"}
    except Exception as exc:  # recorded, like a failed job, and reported by curation_status
        done = {"state": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        traceback.print_exc(file=sys.stderr)
    done["elapsed_s"] = round(time.time() - t0, 1)
    (d / "done.json").write_text(json.dumps(done))
    jobs.write_state(d, jobs.read_state(d) | done)


@tool(server)
def curation_status(curation_id: str) -> dict[str, Any]:
    """State of a curation (PENDING, RUNNING, COMPLETED, FAILED, ...), with the genes graded so far; when
    COMPLETED, the grades, the A and B genes, and its time, model calls, tokens and cost."""
    cfg = Settings.from_env()
    with _call():
        d = _cdir(cfg, curation_id)
        return _summary(d, jobs.status(_job_cfg(), d))


@tool(server)
def get_genes(curation_id: str, grades: str = "A,B", top: int = 100, full: bool = False) -> dict[str, Any]:
    """The gene table of a curation, A first, then B, then N and unresolved: gene, grade, phenotype kind and
    phenotype, confidence, the quoted sentence with its PMID, page and sentence id, papers and sentences read,
    and what the rules changed (code_adjusted). full=true adds the reason, the counts of claims and the paper's
    citation. `grades`: comma-separated grades to return (default A,B)."""
    from .curate import GENE_COLUMNS, read_csv

    cfg = Settings.from_env()
    with _call():
        d = _completed(cfg, curation_id)
        want = {g.strip().upper() for g in grades.split(",") if g.strip()} or {"A", "B", "N", "UNRESOLVED"}
        rows = [r for r in read_csv(d / "genes.csv") if r["grade"].upper() in want]
        counts = {
            g: sum(1 for r in rows if r["grade"] == g) for g in ("A", "B", "N", "unresolved") if g.upper() in want
        }
        keys = GENE_COLUMNS if full else COMPACT_GENE
        return {"curation_id": curation_id, "file": str(d / "genes.csv"), "counts": counts,
                "rows": [_clip(r, keys, 2000 if full else 300) for r in rows[: max(1, min(int(top), 2000))]]}  # fmt: skip


@tool(server)
def get_evidence(curation_id: str, gene: str = "", pmid: str = "", grades: str = "A,B", offset: int = 0,
                 limit: int = 30) -> dict[str, Any]:  # fmt: skip
    """The sentence table of a curation: one row per sentence read for a gene, with the checks behind its grade
    (dosage claim, hedge, mechanism, phenotype kind, evidence type, meets_a, contests), the verbatim quote,
    PMID, page, sentence id and paper title. Filter by gene or pmid, or by the genes' grades (default A,B);
    page with offset/limit (limit at most 200)."""
    from .curate import read_csv

    cfg = Settings.from_env()
    with _call():
        d = _completed(cfg, curation_id)
        want = {g.strip().upper() for g in grades.split(",") if g.strip()}
        graded = {r["gene"]: r["grade"].upper() for r in read_csv(d / "genes.csv")}
        rows = [r for r in read_csv(d / "sentences.csv")
                if (not gene or r["gene"].upper() == gene.strip().upper()) and (not pmid or r["pmid"] == pmid.strip())
                and (gene or not want or graded.get(r["gene"]) in want)]  # fmt: skip
        lim, off = max(1, min(int(limit), 200)), max(0, int(offset))
        return {"file": str(d / "sentences.csv"), "total": len(rows), "offset": off,
                "next_offset": off + lim if off + lim < len(rows) else None,
                "rows": [_clip(r, COMPACT_SENTENCE) for r in rows[off : off + lim]]}  # fmt: skip


@tool(server)
def evaluate(curation_id: str, reference: str = "") -> dict[str, Any]:
    """Score a curation against a graded reference (default: the curator review of 2026-11-04, confirmed by Dr.
    Chen: 15 A, of them 10 ocular and 5 syndromic, 7 B, 2 N). Returns A recall (all, ocular, syndromic), the B
    genes listed apart (B or not listed, never A), the N genes listed, the A genes outside the reference, the
    pass flags, and the curation's time, calls, tokens and cost."""
    from . import evaluate as ev
    from .curate import read_csv

    cfg = Settings.from_env()
    with _call():
        d = _completed(cfg, curation_id)
        out = ev.score(read_csv(d / "genes.csv"), ev.load_reference(reference))
        (d / "evaluation.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        cur = json.loads((d / "curation.json").read_text(encoding="utf-8"))
        return {"curation_id": curation_id, **out, "curation_total": cur["total"], "models": cur["models"]}


@tool(server)
def write_report(curation_id: str) -> dict[str, Any]:
    """A Markdown report of a curation: the A genes, then the B genes apart, each with its quote, PMID, page and
    paper; the genes the rules regraded; the score when evaluate was called; the run's models, time, calls,
    tokens and cost. Written to report.md in the curation folder."""
    from . import report

    cfg = Settings.from_env()
    with _call():
        path, text = report.write(_completed(cfg, curation_id))
        clipped = len(text) > 12000
        return {"file": str(path), "markdown": text[:12000] + ("\n…(cut; the whole report is in the file)" if clipped else ""),
                "clipped": clipped}  # fmt: skip


@tool(server)
def list_curations() -> dict[str, Any]:
    """Curations in this server's data directory, with their question, state, grades and cost."""
    cfg = Settings.from_env()
    out = []
    for p in sorted(cfg.data_dir.glob("*/request.json")):
        d = p.parent
        st = jobs.read_state(d)
        row = {"curation_id": d.name, "question": json.loads(p.read_text(encoding="utf-8"))["question"],
               "state": st.get("state")}  # fmt: skip
        cur = d / "curation.json"
        if cur.is_file():
            c = json.loads(cur.read_text(encoding="utf-8"))
            row |= {"grades": c["grades"], "cost_usd": c["total"]["cost_usd"], "seconds": c["total"]["seconds"]}
        out.append(row)
    return {"data_dir": str(cfg.data_dir), "curations": out}


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
