# retigene-rag

Questions about the RetiGene corpus: about 1,700 full-text papers on inherited retinal disease (39,563
passages, 426,706 sentences). An agent such as Claude writes its own searches, and for "every gene with a
property" a curation reads every sentence that states it and grades each gene from its own sentences. The
answer is two tables, one row per gene and one row per sentence, each with a verbatim quote, PMID and page.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `search_sentences` | `terms`, `gene`, `limit`, `offset` | every sentence that holds a term, as `PMID p c: sentence` lines, paged; case, spaces, hyphens and PDF ligatures ignored |
| `search_passages` | `query`, `terms`, `top_k` | the passages nearest the query by chunk co-embedding (PubMedBERT); with `terms`, only passages that hold one are ranked |
| `read_passage` | `pmid`, `chunk` | the whole passage with its citation, title and DOI |
| `submit_curation` | `question`, `terms`, `estimate_only`, `max_cost_usd`, `rerun` | `curation_id`, `state`; when COMPLETED the A and B genes and the run's time, calls, tokens and cost. `estimate_only`: genes, calls, tokens and cost, no model call |
| `curation_status` | `curation_id` | `state`, model calls so far; when COMPLETED as above |
| `get_genes` | `curation_id`, `grades`, `top`, `full` | the gene table: grade, phenotype, quote with PMID, page and sentence id, what the rules changed |
| `get_evidence` | `curation_id`, `gene`, `pmid`, `grades`, `offset`, `limit` | the sentence table: every sentence read for a gene, with the checks behind its grade |
| `evaluate` | `curation_id`, `reference` | A recall (all, ocular, syndromic), B listed apart, N listed, pass flags, against the curator reference |
| `write_report` | `curation_id` | `report.md`: the A genes, the B genes apart, the run |
| `list_curations` | none | `curations` |

**The curation** (`submit_curation`) is the corpus-wide scan of RCHENLAB/AiScientist #95, with the
sentence grading of #99 (Ziyao Ma, rules revision 17):

1. Every sentence that states a dosage mechanism in words (haploinsufficiency, gene dosage, one functional
   copy, loss of one allele, ...) or holds one of the agent's terms, every hit taken, grouped by the gene
   named in the sentence or next to it (HGNC symbols and aliases). On RetiGene: 474 sentences, 301 genes.
2. The check model reads each gene's sentences twice against closed questions (is it a dosage claim, is the
   claim hedged, is it about this gene's own dosage, which mechanism, which phenotype, is it the paper's own
   finding or a reference entry), and the readings are merged field by field.
3. The review model grades the genes with a claim by the curator's definitions: **A**, the authors state
   without a hedge that heterozygous loss of function causes a retinal or ocular disease, or a syndrome with
   one, through haploinsufficiency; **B**, hedged, contradicted by another corpus paper, or one of several
   mechanisms; **N**, not supported.
4. The code bounds the grade: every quote is checked verbatim against the corpus, an A needs a sentence that
   meets the A conditions, and a contradiction, a hedged primary conclusion, a milder-phenotype statement or
   a non-ocular phenotype lowers it.

A curation is kept under an id made of its terms, the rules, the models and the index fingerprint, and
asking again reuses it. Every model reply is cached (`RETIGENE_CACHE`), so a rerun, a reworded question with
the same sentences, or a corpus update reads only what changed.

| Variable | Meaning | Default |
| --- | --- | --- |
| `RETIGENE_INDEX` | the index, read-only: a version of the AiScientist shared dataset `retigene` (`mechanism_cache/sentences.jsonl`, `chunks.jsonl`, `chunk_vecs.npy`, `manifest.csv`, `hgnc_complete_set.txt`) | required |
| `RETIGENE_PROVIDER` | `anthropic` (Claude) or `openai` (any OpenAI-compatible endpoint, such as the lab's shared model on vLLM) | `anthropic` |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | the Claude key (and a proxy) | for `anthropic` |
| `RETIGENE_BASE_URL`, `RETIGENE_API_KEY` | the endpoint (`http://host:port/v1`) and its key; `RETIGENE_OPENAI_BODY` is JSON merged into each request | for `openai` |
| `RETIGENE_CHECK_MODEL`, `RETIGENE_REVIEW_MODEL` | the two readers | `claude-haiku-5-5`, `claude-haiku-5-5` |
| `RETIGENE_EFFORT`, `RETIGENE_CHECK_PASSES`, `RETIGENE_WORKERS` | Claude reasoning effort; readings per gene; genes graded at a time | `low`, `2`, `16` |
| `RETIGENE_PRICE_IN`, `RETIGENE_PRICE_OUT` | USD per million tokens for a model outside the Claude price table | `0` |
| `RETIGENE_CACHE` | model replies, kept across curations | `$AISCI_DATA_DIR/retigene-rag/cache` |
| `AISCI_DATA_DIR` | curations: `<root>/retigene-rag/<curation_id>/` (`genes.csv`, `sentences.csv`, `curation.json`, `report.md`) | `./aiscientist-mcp-data` |
| `AISCI_EXECUTOR`, `AISCI_SLURM` | how `submit_curation` runs: `slurm`, `local` or `inline` (see `aiscientist_mcp.common.jobs`) | `slurm` |

Install the `embed` extra (`sentence-transformers`, with torch) for `search_passages`. The image holds the
query model at the revision that made the chunk vectors.

## The 15-gene test

`eval/agent_eval.py` starts the server over stdio, as an MCP client does, gives Claude only the server's
tools and instructions, and scores the curation Claude made with `evaluate`. `eval/curation_eval.py` runs
one curation with no agent, for the model paths. The reference is the curator review of 2026-11-04
confirmed by Dr. Chen, bundled as `reference/reference_genes_reviewed.csv` (24 genes: 15 A, of them 10
ocular and 5 syndromic, 7 B, 2 N; grades without the quotes and review notes).

```bash
python eval/agent_eval.py --out run.json --model claude-haiku-5-5
python eval/agent_eval.py --out rw.json --question "Which genes cause eye disease when one copy is lost, according to the RetiGene papers?"
python eval/curation_eval.py --out cur.json
```

RESULTS

The offline tests use a stand-in model and embedder (`offline-test`): 8 tests, about 10 s.
