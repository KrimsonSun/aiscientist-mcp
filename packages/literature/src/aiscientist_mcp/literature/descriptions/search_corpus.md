# search_corpus

Retrieve verbatim passages from the configured read-only corpus without calling a language model. Accepts 1-6 `queries`, `top_k` 1-30 (default 12), `max_per_paper` (default 3), optional exact `terms` (up to 12), and optional `pmids` to restrict papers.

The original hybrid ranking combines local PubMedBERT cosine similarity and BM25 over question words, preserves a share of semantic matches, boosts exact gene-symbol matches and alternates across questions. `terms` are checked against the corpus, with diagnostics for missing terms. Returned contexts carry text, PMID, page, citation, source metadata, ranking score, cosine and the query that retrieved them. No passage is synthesized.

Requires prebuilt `mechanism_cache/chunks.jsonl` and local embedding weights. `LITERATURE_HF_CACHE` accepts an HF_HOME containing `hub/` or the hub itself. No runtime model download is attempted. Missing vectors may be computed in memory; the shared cache is never rewritten. No matches is a failed lookup, not evidence that a biomedical association is false. Refreshing corpus indexes is a separate administrator operation.

Results and durable jobs stay in this analysis workspace. The caller may send returned passages to its chosen answer model; this tool itself does no generation.
