# deep_literature

Answer one focused `question` with PaperQA's search, evidence gathering and synthesis over the prebuilt local corpus index. Ask a complete biomedical question rather than a bag of identifiers. Returns the answer, supporting contexts/citations and retrieval diagnostics. Zero contexts or no generated answer is a failed lookup.

All model roles use the explicit server `LITERATURE_MODEL_ENDPOINT` and `LITERATURE_MODEL`; there is no public model fallback. Configure `LITERATURE_MODEL_API_KEY_FILE` if needed. Embeddings remain local (`st-` sentence-transformers weights). PaperQA may make bibliographic metadata network requests. Model/protocol parameters are not tool arguments.

The shared corpus index is read-only and index synchronization is disabled. Mutable answer files live under the current analysis workspace. Evidence breadth, concurrency, answer length and model timeouts are administrator settings described in the package README. The default deterministic PaperQA agent preserves the original search/gather/answer workflow.

Use `analysis_id` to isolate questions/workflows and poll `literature_job_status` for asynchronous execution. To curate a gene list across corpus passages use `extract_haploinsufficient_genes`; one RAG answer is not a whole-corpus scan.
