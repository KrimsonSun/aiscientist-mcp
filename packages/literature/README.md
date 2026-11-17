# literature

Independent MCP tools for biomedical citation search, hybrid corpus retrieval, PaperQA answers,
and evidence-checked whole-corpus gene curation. The scientific implementation is ported from
AiScientist `48f64a6be1b9f44eb2948eea1ac448df6a4219e2`; the package imports no AiScientist application
or HarnessTool code.

| Tool | Main arguments | Result |
| --- | --- | --- |
| `literature_search` | `query`, `limit`, `pubmed_only`, `abstracts` | Europe PMC citations, optional abstracts |
| `search_corpus` | `queries`, `top_k`, `max_per_paper`, `terms`, `pmids` | Verbatim passages, PMID/page/citation, hybrid retrieval diagnostics |
| `deep_literature` | `question` | PaperQA answer and supporting contexts; empty evidence is a failure |
| `extract_haploinsufficient_genes` | retrieval/pilot settings, `pmids`, optional typed `spec` | Verified `genes.csv`, `hits.csv`, `requests.csv`, warnings and review flags |
| `literature_job_status` | `analysis_id`, `task_id` | Job state, original result or diagnostic failure |

All four analysis tools accept `analysis_id` (default `default`) and `rerun` (default false).
Their outputs live under `$AISCI_DATA_DIR/literature/<analysis_id>/`: `work/`, `artifacts/`,
`jobs/<task_id>/request.json`, `result.json`, `state.json`, `done.json` and job logs. IDs are validated;
symlink escapes are refused. A workspace binds its corpus version and model identity so old model
judgments are not silently reused against another corpus or endpoint. Use a new analysis ID to change
those inputs. Repeating the current completed request reuses its result; `rerun=true` creates a new attempt.
Failed attempts remain individually addressable by task ID.

## Install and run

From the repository:

```bash
uv sync --all-packages
uv run literature-mcp
# Full local retrieval, PDF extraction and PaperQA dependencies:
uv sync --all-packages --extra full
# Or from a clean interpreter:
pip install ./packages/common './packages/literature[full]'
```

Configure a corpus root containing `papers/`, `mechanism_cache/chunks.jsonl`,
`mechanism_cache/sentences.jsonl`, optional `chunk_vecs.npy`, `paperqa_manifest.csv`,
`hgnc_complete_set.txt`, `hf_cache/`, and the prebuilt PaperQA `index_pubmedbert/` directory:

```bash
export LITERATURE_CORPUS_ROOT=/path/to/corpus
export AISCI_DATA_DIR=/path/to/results
export AISCI_EXECUTOR=slurm
export AISCI_SLURM='--partition=<partition> --cpus-per-task=4 --mem=16G --time=04:00:00'
export AISCI_JOB_PREFIX='apptainer exec --bind /path/to/corpus:/path/to/corpus:ro,/path/to/results /path/to/literature.sif'
aiscientist-mcp --server literature-mcp call search_corpus \
  '{"queries":["Does loss of one PRPF31 copy cause retinitis pigmentosa?"],"analysis_id":"example"}'
# Poll with the returned task_id:
aiscientist-mcp --server literature-mcp call literature_job_status \
  '{"analysis_id":"example","task_id":"<returned-task-id>"}'
```

The common `slurm`, `local`, and `inline` executors are supported. Slurm/local returns the job
identity promptly; inline blocks inside its compute allocation and returns the original result
fields plus `state`, `analysis_id`, `task_id`, and `job_id`. A failed algorithm writes `FAILED`,
never `COMPLETED`; its partial result remains available through `literature_job_status`.
There is one active task per analysis to protect shared checkpoints. Follow common-runtime
`AISCI_SLURM_FALLBACK` and `AISCI_JOB_PREFIX` configuration for scheduling and image selection.

| Setting | Meaning |
| --- | --- |
| `LITERATURE_CORPUS_ROOT` | Admitted read-only corpus tree and defaults below |
| `LITERATURE_ALLOWED_ROOTS` | Additional permitted input roots, separated by the OS path separator |
| `LITERATURE_PAPERS` | PDF directory; default `<corpus>/papers` |
| `LITERATURE_CACHE_DIR` | Text/vector cache; default `<corpus>/mechanism_cache` |
| `LITERATURE_MANIFEST` | Manifest CSV; default `<corpus>/paperqa_manifest.csv` |
| `LITERATURE_HGNC` | HGNC table; default `<corpus>/hgnc_complete_set.txt` |
| `LITERATURE_HF_CACHE` | Pre-staged embedding cache; default `<corpus>/hf_cache` |
| `LITERATURE_EMBEDDING` | Local embedding identifier; default `st-NeuML/pubmedbert-base-embeddings` |
| `LITERATURE_INDEX_DIR`, `LITERATURE_INDEX_NAME` | Prebuilt PaperQA index root and name; defaults `index_pubmedbert`, `retigene_full_pubmedbert` |
| `LITERATURE_MODEL_ENDPOINT`, `LITERATURE_MODEL` | Explicit OpenAI-compatible `/v1` endpoint and served model name; no default |
| `LITERATURE_MODEL_API_KEY_FILE` | Preferred model credential file; read only by the worker |
| `LITERATURE_MODEL_API_KEY` | Optional credential environment value if no key file is set |
| `LITERATURE_MODEL_TIMEOUT` | Whole-corpus model request timeout, seconds; default 180 |
| `LITERATURE_MODEL_MAX_TOKENS` | Optional positive response token limit for PaperQA model roles; unset preserves PaperQA defaults |
| `LITERATURE_SEARCH_URL` | Optional Europe PMC-compatible URL; defaults to the public Europe PMC search endpoint |

Explicit paths must resolve inside an admitted root, including manifest PDF locations. Shared
corpus caches stay read-only. Missing text caches may be built from PDFs only inside the analysis
workspace. PaperQA gets an atomic private copy of the prebuilt corpus index inside that
workspace, because Tantivy takes a writer lock even during searches. Saved
embeddings are reused; the shared index remains read-only. Index synchronization is disabled during queries.

## Models, network, and budgets

`literature_search` sends its public keyword query to Europe PMC. `search_corpus` never calls a
language model: it uses local PubMedBERT embeddings and BM25. Embeddings use pre-staged weights
only and do not download them during analysis. PaperQA may perform bibliographic metadata requests.
Both answer and curation models use the configured endpoint exclusively; without endpoint/model
settings they report `not_enabled`. Credentials are never tool arguments or persisted job requests.
Model HTTP redirects are refused for the extraction client; keys are redacted from worker output.

A gateway relay can declare `LITERATURE_MODEL_USAGE_PROTOCOL=aisci-relay-v1` through trusted server configuration. The relay returns measured token usage and a known numeric per-call USD estimate in `aiscientist_usage`; the extraction worker accumulates it in `api_usage` and the original pilot projects the full scan cost. Positive `max_cost_usd` is accepted only for this declared priced protocol. The gateway also applies the smaller of that requested cap and its configured ceiling. Worker and gateway serialize priced calls and stop further calls once the cap is reached; an already in-flight request can cross it. Missing/unknown prices stop the scan and are reported as null, never free. Arbitrary unpriced endpoints still reject a positive cap. The
pilot still detects failed or unparsable model replies before the full pass.

## Validation and rollout

The package includes the original synthetic extraction, quote verification, HGNC, curation-spec,
BM25/fusion and public-search regression fixtures, plus real MCP schema/argument checks, an actual
stdio-to-inline-worker run against a local synthetic HTTP service, confinement tests, model key-file
routing, and PaperQA configuration tests. These prove software behavior on synthetic fixtures.
They do not establish parity on the school's real corpus or validate a released image.

Deployment acceptance separately checks a pinned released image, the approved real corpus/model,
citations and passages, extraction review flags and the website-to-HPC-to-artifact chain.
The recorded runs below distinguish early source-staged tests, released-image tests and
actual website results. A successful tool call alone does not certify a generated report.

## Tool reference

### literature_search

Search Europe PMC using focused biomedical keyword queries, with no API key or language model. Returns real citations with title, authors, year, journal, DOI, PMID and URL. `abstracts=true` includes bounded plain-text abstracts; `pubmed_only=true` restricts results to PubMed records. `limit` is 1-25, default 8.

The original query focusing, generic-poster filtering and IDF-weighted title/abstract relevance ranking are preserved. Only cite returned identifiers. This tool sends its query to the configured Europe PMC-compatible endpoint, defaulting to the public service. Empty queries and HTTP failures return failed tasks with diagnostics.

Use `analysis_id` for the output workspace and poll `literature_job_status` when asynchronous. For local corpus passages use `search_corpus`; for a synthesized answer with evidence use `deep_literature`.

### search_corpus

Retrieve verbatim passages from the configured read-only corpus without calling a language model. Accepts 1-6 `queries`, `top_k` 1-30 (default 12), `max_per_paper` (default 3), optional exact `terms` (up to 12), and optional `pmids` to restrict papers.

The original hybrid ranking combines local PubMedBERT cosine similarity and BM25 over question words, preserves a share of semantic matches, boosts exact gene-symbol matches and alternates across questions. `terms` are checked against the corpus, with diagnostics for missing terms. Returned contexts carry text, PMID, page, citation, source metadata, ranking score, cosine and the query that retrieved them. No passage is synthesized.

Requires prebuilt `mechanism_cache/chunks.jsonl` and local embedding weights. `LITERATURE_HF_CACHE` accepts an HF_HOME containing `hub/` or the hub itself. No runtime model download is attempted. Missing vectors may be computed in memory; the shared cache is never rewritten. No matches is a failed lookup, not evidence that a biomedical association is false. Refreshing corpus indexes is a separate administrator operation.

Results and durable jobs stay in this analysis workspace. The caller may send returned passages to its chosen answer model; this tool itself does no generation.

### deep_literature

Answer one focused `question` with PaperQA's search, evidence gathering and synthesis over the prebuilt local corpus index. Ask a complete biomedical question rather than a bag of identifiers. Returns the answer, supporting contexts/citations and retrieval diagnostics. Zero contexts or no generated answer is a failed lookup.

All model roles use the explicit server `LITERATURE_MODEL_ENDPOINT` and `LITERATURE_MODEL`; there is no public model fallback. Configure `LITERATURE_MODEL_API_KEY_FILE` if needed. Embeddings remain local (`st-` sentence-transformers weights). PaperQA may make bibliographic metadata network requests. Model/protocol parameters are not tool arguments.

The shared corpus index is read-only and index synchronization is disabled. Mutable answer files live under the current analysis workspace. Evidence breadth, concurrency, answer length and model timeouts are administrator settings described in the package README. The default deterministic PaperQA agent preserves the original search/gather/answer workflow.

Use `analysis_id` to isolate questions/workflows and poll `literature_job_status` for asynchronous execution. To curate a gene list across corpus passages use `extract_haploinsufficient_genes`; one RAG answer is not a whole-corpus scan.

### extract_haploinsufficient_genes

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

## Real HPC validation (2026-11-12)

The source-staged standalone server was exercised through actual MCP stdio in the
lab's existing PaperQA SIF (Python 3.12.13), using read-only RetiGene documents,
local PubMedBERT weights and an isolated, authenticated Qwen3.8-27B-INT4 vLLM
0.28.0 service on one A30. This was separate from the user's gateway/API-key
session and did not forward that session's credentials.

- Europe PMC citation search and local corpus retrieval passed against real data.
- `deep_literature` returned two supporting contexts and a 1,182-character answer
  in 265.74 seconds. A real read-only Tantivy lock failure first exposed the need
  for an atomic private copy of the prebuilt index; the successful rerun kept the
  source index read-only and reused its saved embeddings.
- `extract_haploinsufficient_genes` judged four passages from three papers in
  62.86 seconds. It returned one `RP11` candidate flagged `gene_not_in_quote` and
  `gene_alias_or_unknown`. This checks model integration and review flags; it is
  neither a validated HGNC gene list nor whole-corpus scientific curation.
- Task failure/status paths and bounded native responses were checked. The local
  Docker image also passed native stdio tool enumeration.

The earlier fork prerelease `literature-v0.0.2` was then tested on CPU Slurm with
its installed wheel, no source override, and the same public corpus/model setup.
Its OCI digest is
`sha256:05d85119e06222b6d2e583db1619f27ea6b55bdcb371b1f2707169d4b39c81b1`;
the converted SIF SHA-256 is
`ff01e6f070692d7ce3313c4dc61b9e19c6bbe024e712361d18bca95d29f87f60`.
All four actions, status polling, wrong-type rejection and unknown-argument
rejection passed. Extraction again judged four passages and retained the flagged
`RP11` candidate. The initial mechanism question returned eight contexts but
`I cannot answer.`; this is retained as an abstention, not substantive answer
acceptance. A focused follow-up asking about the published PRPF31 deletion and
family segregation returned one cited context and an answer in 189.79 seconds
(8,383-byte native response). The validator now rejects a bare abstention as a
substantive-answer pass. These bounded checks do not validate the answer's causal
interpretation or make the corpus review exhaustive.

Reproduce the exact-SIF check with
`containers/literature/validate_hpc.py literature --image ... --output ...
--corpus ... --binding ...` inside an authorized CPU allocation. The validator
records its SHA-256 and calls the image's installed CLI without a source override.
The binding file contains endpoint/model/key-file path; it does not contain the
credential value. `--only deep_literature --question ...` records a focused
follow-up without replacing the original evidence.

Those early runs verified vLLM's OpenAI-compatible chat interface, including Qwen thinking
controls; they do not establish strict public OpenAI or native Anthropic compatibility.
Zero values in that unpriced model's cost/pilot fields are not billing measurements or
proof that no model calls occurred. Later website selected-model/cost evidence is below.


## Complete results over MCP resources (0.0.4)

Large tool/status replies retain bounded previews and `response_omitted=true`.
For a completed task they also expose `full_result_uri`, starting at
`literature://results/<analysis_id>/<task_id>/0`. Standard MCP `resources/read`
returns JSON with an 8192-byte base64 page, byte offset, `total_bytes`, SHA-256 and
`next_offset` (null at EOF). Verify every page's task identity, stable size/hash,
contiguous offsets and final checksum before interpreting the complete JSON.
The resource admits only the task's persisted terminal result, never arbitrary
paths; reading it starts no analysis or model request. Results over 32 MiB are
refused explicitly. Both tools and resource pages remain bounded on the wire.

The website phenotype acceptance exposed a real integration gap in 0.0.3:
its peer correctly refused a truncated literature preview but had no protocol
route to the complete evidence. Version 0.0.4 adds that route without changing
retrieval, model selection or scientific scoring. Local real MCP protocol tests
cover large synthetic evidence and corrupt/missing pages. Final released-image and
selected-session website results are recorded below; report grounding remains a separate gate.

## Final-image and website evidence (2026-11-13)

The actual 0.0.4 SIF passed native CLI/schema, public retrieval, complete result-resource
paging/checksums and failure-state controls in CPU Slurm `59540888`, without a source
overlay. These CPU protocol/resource controls made no generative-model calls.

Released image: [`literature-v0.0.4`](https://github.com/liujs123456/aiscientist-mcp/releases/tag/literature-v0.0.4), OCI `sha256:ea3d66b0b7bc6f217da5dd78e1fd16ae018cea780e45728a097fbaa306fd12e7`.

Normal website run `30e3f3c21630` then completed all four reviewed native actions with the
selected Haiku model observed in the UI. The downloaded results contain three real
identifier/citation records, three corpus passages, a deep answer supported by the retrieved
source and the complete evidence array. Bounded extraction retained the actual zero eligible
gene result; no unsupported gene was promoted. The sole retained review row is TLCD3B with
`recessive_lof`, a verified quote and `eligible=false`; the genes table is header-only. Extraction
read and judged four passages, not the complete corpus. It recorded four pilot calls costing
$0.001635 within its five-call total of $0.001761, below the approved $0.10 cap. This is not
the cost of all four tools or gateway report generation. These are bounded test outcomes,
not exhaustive corpus curation or proof of a positive gene-extraction case.

Scientific/result checks passed; the old application bridge had omitted substantive fields
from large replies and changed two slash-containing passages in exported prose. Those failed
exports and the first failed report revision remain preserved.
All four original tasks have now been recollected through reviewed status-only calls in the
same run. All four full browser JSON results match the original scientific fields exactly,
including types, list order and restored slash characters; only documented artifact locations
are mapped. All three CSVs in six published copies are byte-identical to the original files.
The original 19 native files remained unchanged, and no native task was added. Status recovery
did not repeat scientific or extraction-model work; gateway reasoning/report calls are a
separate activity. Report-only revision `0e86afd05ff2` passed 13 targeted checks across the
complete main and technical browser reports: the substantive primary-source answer, source
IDs, negative extraction, cost scope and unchanged scientific state/task snapshots. The
browser report copies match gateway bytes.

This is a qualified content gate. The technical report uses “screened” for a 1,736-paper /
426,706-sentence corpus whose model judged only four passages; it does not establish
exhaustive screening. The generic citation footer still fails to recognize status-collected
evidence, five numeric fields are automatically untraced, and two main-report links refer
to historical canonical paths. The current task paths and checksums in the grounding
manifest remain authoritative; automated audit/layout claims are not certified wholesale.
Provider identity was observed in the UI and is not independently encoded in
the downloaded bundle. Historical abstentions and flagged `RP11` results above remain
separate evidence, not successful verified gene lists.
