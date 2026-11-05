# retigene-rag

Search tools over the RetiGene corpus (about 1,700 full-text papers on inherited retinal disease, 39,563
passages, 598,885 sentences) for an agent such as Claude. The agent writes its own searches. Keyword search
is the main tool and embedding search the secondary one. For a question that asks for every gene with a
property, a grading tool grades each gene from its own sentences. The answer is a gene table and a sentence
table, each row with a verbatim quote, PMID and page.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `search_sentences` | `terms`, `gene`, `limit`, `offset` | every sentence that holds a term, as `PMID p c: sentence` lines, paged; case, spaces, hyphens and PDF ligatures ignored |
| `search_passages` | `query`, `top_k` | the passages nearest the query by chunk embedding (PubMedBERT), for a question about one topic |
| `read_passage` | `pmid`, `chunk` | the whole passage with its citation and title |
| `grade_genes` | `question`, `terms`, `genes`, `criteria`, `max_cost_usd` | grades every gene the sentences holding `terms` name (plus `genes`): A stated, B hedged or contested, N not supported |
| `get_grades` | `grading_id`, `grades`, `top` | one row per gene: grade, papers, PMIDs, quote with PMID, page and title, rule checks, reason |
| `get_gene_evidence` | `grading_id`, `gene`, `pmid`, `offset`, `limit` | one row per evidence sentence of an A or B gene |
| `evaluate` | `grading_id`, `reference` | A recall (all, ocular, syndromic), B listed apart, N listed, against the curator reference |
| `list_gradings` | none | `gradings` |

**Grading.** A gene's evidence is every sentence that names it (the approved symbol, or an alias with a digit
that belongs to it alone) and holds a search term or a mechanism word ("dominant negative", "does not
cause", ...). Claude Haiku answers the grading rules as checks for every gene: an unhedged statement? hedged?
is this gene its subject? an eye disease? contradicted for the same disease? The grade follows from the answers.
Claude Sonnet answers the checks again for the genes Haiku graded A or B, and its grade is final. An unreadable
reply is answered with a correction. A gene that still fails is listed in `failed_first_pass` or
`failed_review`, and calling `grade_genes` again retries only those genes. Before each pass the tool checks
that the spend stays within `max_cost_usd`.

| Variable | Meaning | Default |
| --- | --- | --- |
| `RETIGENE_INDEX` | the index folder, read-only: `mechanism_cache/chunks.jsonl`, `mechanism_cache/chunk_vecs.npy`, `manifest.csv`, optionally `hgnc_complete_set.txt` | required |
| `ANTHROPIC_API_KEY` | the key for the grading (`ANTHROPIC_BASE_URL` for a proxy) | required for `grade_genes` |
| `RETIGENE_JUDGE_MODEL`, `RETIGENE_REVIEW_MODEL` | first-pass and review models; review empty for none | `claude-haiku-5-5`, `claude-sonnet-5-5` |
| `RETIGENE_JUDGE_EFFORT`, `RETIGENE_REVIEW_EFFORT` | reasoning effort | `medium` |
| `RETIGENE_EMBEDDING_MODEL` | the model that made `chunk_vecs.npy` | `NeuML/pubmedbert-base-embeddings` |
| `HF_HOME` | Hugging Face cache holding that model; offline when it is there | `~/.cache/huggingface` |
| `RETIGENE_HGNC` | HGNC table when the index has none (downloaded on first use) | `$AISCI_DATA_DIR/retigene-rag/hgnc_complete_set.txt` |
| `RETIGENE_WORKERS` | parallel model calls of the grading | `48` |

Install the `embed` extra (`sentence-transformers`, with torch) for `search_passages`.

## The 15-gene test

`eval/agent_eval.py` starts the server over stdio, as an MCP client does, and gives Claude only the server's
tools and instructions. Claude answers the question, and the runner scores the grading Claude made with
`evaluate` (RCHENLAB/AiScientist issue 99):

```bash
python eval/agent_eval.py --out run.json --model claude-haiku-5-5
python eval/agent_eval.py --out run2.json --question "Which genes cause eye disease when one copy is lost, according to the RetiGene papers?"
```

Tested on the lab's copy of the RetiGene index with anthropic 1.13.0, mcp 2.3.0, sentence-transformers 6.2.0
and Python 3.13.3. Claude Haiku 5.5 was the agent. The reference is the curator review of 2026-11-04,
confirmed by Dr. Chen (15 A, 7 B, 2 N):

| Run | Agent | Grading | Total | Time | A genes | B listed apart | N listed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| "What are haploinsufficient genes in RetiGene papers?" | 4 turns, $0.011 | 139 genes, $0.50 | $0.51 | 52 s | 15/15 (ocular 10/10, syndromic 5/5) | 2/7 | none |
| the same, again | 5 turns, $0.011 | 139 genes, $0.46 | $0.47 | 52 s | 15/15 | 4/7 | none |
| "Which genes cause eye disease when one copy is lost …?" | 5 turns, $0.012 | 139 genes, $0.45 | $0.46 | 56 s | 15/15 | 2/7 | none |

- **What the agent did.** In every run it searched "haploinsufficien" on its own (397 sentences), read both
  pages, and called `grade_genes` with that term.
- **Where the cost goes.** The Haiku first pass checked 139 genes for $0.07 in 13 s, and the Sonnet review
  checked 55-61 for $0.39-0.43 in 7 s.
- **Unchecked genes.** The genes graded A outside the reference (DYRK1A, LRP5, SCRIB in all three runs;
  HNRNPK, MED12, TWIST1, ZEB2 in some) have not been checked by a curator.
- **The weak part.** It is keeping hedged genes apart: COL11A1, RCBTB1 and SOX2 were graded A in all three runs.

In an earlier version the agent typed the gene list itself, and once it left OTX2 out. `grade_genes` now
takes the agent's search terms and grades every gene their sentences name.

Why keyword search leads: in the same corpus, the top 1,000 passages by chunk embedding hold 1-4 of the 15
genes, and all 15 need the top 17,000-33,000. 7 of the 15 claims are side remarks in papers about another
gene, and the embedding ranks a passage by what it is about.

The offline tests use a stand-in model and embedder (`offline-test`): 15 tests, about 15 s.
