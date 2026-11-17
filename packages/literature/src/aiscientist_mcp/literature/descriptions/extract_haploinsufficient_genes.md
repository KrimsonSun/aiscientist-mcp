# extract_haploinsufficient_genes

Curate genes reported as haploinsufficient in retinal disease from the configured corpus. The original pipeline is retained: model-written retrieval questions, local embedding/BM25 retrieval, keyword and exact-term rounds, per-passage JSON judgments, gene-directed follow-up, optional unread-passage checking, exact-quote verification, HGNC normalization, evidence flags and review tables. A statement is kept only when its quoted text can be found in the cited passage. A mechanism assertion and an experimental dosage measurement remain distinct.

The tool writes `genes.csv`, `hits.csv`, and `requests.csv` plus durable requests/judgments in its analysis workspace. Tables carry PMIDs, verbatim quotes, source pages, evidence classes, classification/verification flags and gene-normalization diagnostics. Inspect review flags and source quotes before using a candidate list. A bounded smoke run demonstrates execution, not exhaustive recall or scientific completeness.

The schema exposes retrieval settings (`n_requests`, `k_per_request`, `keyword_round`, `term_round`, `max_term_passages`, `k_per_gene`), output controls (`max_quotes_per_gene`), parallelism (`concurrency`), smoke-test cap (`max_passages`, zero means all), pilot settings, unread checks, optional `pmids` and optional `spec`. A pilot detects failed or unparsable model replies before the full pass. A positive `max_cost_usd` requires an operator-configured priced relay. Its measured usage feeds the pilot cost projection, and both worker and gateway stop subsequent calls at the cap. An in-flight provider request may cross the threshold. Unpriced endpoints cannot accept a positive cap.

To curate another property, provide the complete typed `spec` object:

```json
{
  "target": "the property to curate",
  "definition": "what qualifies, phrased precisely",
  "counts_when": ["an explicit qualifying statement in a retrieved passage"],
  "does_not_count": ["a gene mention without the qualifying assertion"],
  "classes": ["a supported evidence class"],
  "scope": "the relevant disease or biological context",
  "requests": ["a focused retrieval question"],
  "terms": ["an exact phrase to read in full"]
}
```

The spec definition/rules become the reading prompt; its questions and exact terms drive retrieval. Unknown spec fields are rejected. Without a spec, the original haploinsufficiency rules apply. Model endpoint/name/key are explicit server settings, never tool arguments. Shared corpus paths remain read-only; missing text extraction writes only to this analysis. Reuse an analysis after a recoverable interruption to resume stored judgments; use a new analysis ID when changing corpus/model identity.
