# literature_search

Search Europe PMC using focused biomedical keyword queries, with no API key or language model. Returns real citations with title, authors, year, journal, DOI, PMID and URL. `abstracts=true` includes bounded plain-text abstracts; `pubmed_only=true` restricts results to PubMed records. `limit` is 1-25, default 8.

The original query focusing, generic-poster filtering and IDF-weighted title/abstract relevance ranking are preserved. Only cite returned identifiers. This tool sends its query to the configured Europe PMC-compatible endpoint, defaulting to the public service. Empty queries and HTTP failures return failed tasks with diagnostics.

Use `analysis_id` for the output workspace and poll `literature_job_status` when asynchronous. For local corpus passages use `search_corpus`; for a synthesized answer with evidence use `deep_literature`.
