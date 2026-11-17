# visionkb-kg

Read-only queries of the VisionKB knowledge graph (genes, retinal cell types and states, diseases, phenotypes and
HECA atlas expression, on Biolink 4.4.6) through the visionkb-api HTTP service (`/v1`). The tools are thin, typed
clients: one GET per call (three for `get_release`), no local data, no jobs, no caching.

1. `get_release`: which KG release is served, and is the service ready.
2. `search_entities`: resolve a name or symbol to CURIEs.
3. `get_entity`, `get_relationships`: a node and its edges (with `knowledge_level`, `agent_type`,
   `primary_knowledge_source`, publications).
4. `get_evidence`: the StudyResults and publications behind one `edge_id`.
5. `get_gene_set`, `query_atlas_expression`, `find_datasets`: genes of a cell type or disease, per-group
   expression summaries (all genes x groups, no threshold), and the atlases to bind for an analysis.

| Tool | Arguments | Returns (`data`) |
| --- | --- | --- |
| `search_entities` | `q`, `category`, `limit` | `[{id, name, category, symbol, synonyms, match, score}]` |
| `get_entity` | `curie` | `{node, degree_by_predicate}` |
| `get_relationships` | `subject` and/or `object`, `predicate`, `object_category`, `knowledge_level`, `species`, `limit`, `offset` | `[{edge_id, subject, predicate, object, qualifiers, knowledge_level, agent_type, primary_knowledge_source, publications, evidence_count, status}]` |
| `get_evidence` | `edge_id`, `limit`, `offset` | `{edge, study_results, publications}` |
| `get_gene_set` | `entity` (CL, CellState, MONDO, HP), `kind`, `dataset`, `min_pct`, `descendants`, `limit`, `offset` | `[{gene_id, symbol, score, evidence}]` |
| `query_atlas_expression` | `genes` (<= 20), `dataset`, `level`, `cell_type`, `sort`, `limit`, `offset` | expression summary rows + `dataset_version` |
| `find_datasets` | `entities`, `species`, `tissue`, `assay`, `disease`, `limit`, `offset` | registry records with `bind`, `score`, `matched` |
| `get_release` | none | `{ready, health, release}` (manifest subset, predicates in the graph) |

## Results

Every result is the API envelope plus two fields:

```json
{"status": "ok", "kg_release": "visionkb-YYYY.MM.N", "data": "...", "page": {"limit": 20, "offset": 0, "total": 3},
 "warnings": [], "error": null, "http_status": 200, "request": {"path": "/v1/relationships", "params": {"subject": "HGNC:10012"}}}
```

- `kg_release` and `warnings` are in every result; cite `kg_release` (and `edge_id`) with any claim.
- List tools default to `limit` 20. The API caps rows (200) and response size (about 24,000 characters); to stay
  within the size cap it may return **fewer rows than `limit`**, says so in `warnings`, and keeps `page.limit` at the
  requested value.
- List tools (`search_entities`, `get_relationships`, `get_evidence` for `study_results`, `get_gene_set`,
  `query_atlas_expression`, `find_datasets`) return one page and a `paging` block:
  `{returned, offset, limit, total, trimmed, next_offset, trimmed_lists, summary}`. For the next page pass
  `offset = paging.next_offset` (= offset + rows returned; null on the last page). **Never step by `offset + limit`**:
  after a trimmed page that skips rows. A trimmed page also gets `paging.summary` in `warnings`, e.g.
  `trimmed: 7 of 20 rows returned to stay within the API's 24000 characters (5482 in total); next page: offset=7 ...`.
  `search_entities` has no offset (ranked top-k): when it is trimmed, lower `limit` or narrow `q`.
- Each tool result (compact JSON, including `request` and `paging`) stays within 24,000 characters. The API trims its
  envelope to that size, and this client's extra fields can push a full page just over it; the client then drops rows
  from the end of the ranked list the same way, and `paging` reports the rows actually returned.
- Errors are results, not exceptions: `status: "error"` with `error.code` from the API (`not_found`,
  `missing_anchor`, `invalid_curie`, `unknown_predicate`, `too_many_genes`, `unauthorized`, `not_ready`, ...) or
  from the client (`not_configured`, `invalid_setting`, `token_unreadable`, `invalid_argument`, `unreachable`,
  `timeout`, `redirect_refused`, `bad_response`). `kg_release` is null only when the API was not reached. Nothing
  is retried or replaced with other data. Arguments that violate the tool schema (an unknown `level`, `limit`
  over 200) are rejected by the MCP layer as a tool error.

## Settings

| Variable | Meaning | Default |
| --- | --- | --- |
| `VISIONKB_API_URL` | base URL of visionkb-api (with or without `/v1`) | required |
| `VISIONKB_API_TOKEN_FILE` | file whose first non-comment line is a bearer token with scope `read` | none (`/v1/health` and `/v1/ready` only) |
| `VISIONKB_TIMEOUT_S` | seconds per HTTP request | `30` |

The token is read from the file at each call; it is never logged, returned or put on a command line, and it is not
sent on redirects (redirects are refused).

```bash
# <ref>: the release tag visionkb-kg-vX.Y.Z once released (install common the same way)
pip install --no-deps "git+https://github.com/lijinbio/aiscientist-mcp@<ref>#subdirectory=packages/visionkb-kg"
claude mcp add-json --scope project visionkb-kg '{"type":"stdio","command":"visionkb-kg-mcp","env":{"VISIONKB_API_URL":"http://127.0.0.1:8770","VISIONKB_API_TOKEN_FILE":"/path/to/token"}}'
aiscientist-mcp --server visionkb-kg-mcp call search_entities '{"q": "RHO"}'
```

## Tests

- Unit and MCP tests (`tests/test_visionkb_kg_api.py`, `tests/test_visionkb_kg_server.py`) run against a fake
  API on 127.0.0.1 with canned envelopes (real identifiers, synthetic values; no external network): settings,
  token handling, URL and query building, every tool's endpoint and parameters, output schemas, error results
  (HTTP errors, non-JSON, redirects, unreachable, timeout), the stdio transport through `aiscientist-mcp`, and
  paging over trimmed responses. In that test the fake API cuts each page to 3 rows the way the real one cuts to
  its character cap: following `next_offset` returns all 7 edges once, while stepping by `limit` loses 2.
- `tests/test_visionkb_kg_live.py` runs only when `VISIONKB_API_URL` is set. It follows the graph from a gene
  (`VISIONKB_LIVE_GENE`, default `RHO`): release -> gene -> `expressed_in` edge -> evidence -> the edge's cell type
  -> gene set and datasets, plus atlas expression and error cases, and checks that every result fits in 26,000
  characters and carries the served `kg_release`.

Tested on (2026-11-09, visionkb-api 0.1.1 served by visionkb-deploy 0.1.1 with Neo4j 2026.10.1 CE and
PostgreSQL 17.11 on an HPC node):

- Fake API (CI, Python 3.12 and 3.13; locally 3.13.18): 18 tests pass.
- Fixture release `visionkb-0000.01.0` (synthetic values on real identifiers; 44 nodes, 41 relationships): the live
  module passes (8/8), and a recorded chain of 15 stdio calls through `aiscientist-mcp` (release -> RHO ->
  `expressed_in` edges -> evidence -> retinal rod cell gene set and datasets -> atlas expression at both levels ->
  retinitis pigmentosa genes -> three error cases) returns `status` ok for the 12 queries and the expected
  `not_found`, `missing_anchor` and `invalid_argument` errors; the largest result is 5,323 characters.
- First real release `visionkb-2026.11.0` (2,783,649 nodes, 5,119,626 relationships; 13 HECA atlases): the live module
  passes (10/10, including two paging tests). A 17-call stdio chain returns `status` ok for the 14 queries, all
  carrying `kg_release`, plus the three expected errors. A `get_gene_set` request with `limit=200` was trimmed by
  the API to 51 rows; the client kept 50 (23,851 characters), so `next_offset=50`, and the next page returned
  rows 51-100 with no overlap. The largest result is 23,882 compact characters. Each stdio call takes about 3 s
  including server start-up.
