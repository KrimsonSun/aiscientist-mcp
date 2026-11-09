# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each package is
versioned on its own ([Semantic Versioning](https://semver.org/)) and released with the tag
`<package>-vX.Y.Z`.

## [Unreleased]

### Added

- `retigene-rag` 0.0.1: questions about the RetiGene papers (RCHENLAB/AiScientist #95). Search tools for an
  agent: keyword search over every sentence (`search_sentences`), chunk co-embedding search with an optional
  keyword filter (`search_passages`) and `read_passage`. The corpus-wide curation of haploinsufficient genes
  (`submit_curation` / `curation_status`, through `common.jobs`) reads every sentence that states a dosage
  mechanism or holds the agent's terms, and grades each gene from its own sentences with the sentence rules
  of #99 (Ziyao Ma): the check model reads each gene twice, the review model grades A / B / N by the
  curator's definitions, and the code checks every quote and bounds the grade. `get_genes` and
  `get_evidence` give the gene and sentence tables (verbatim quote, PMID, page, sentence id),
  `write_report` a Markdown report, `evaluate` the score against the bundled curator reference of
  2026-11-04 (`reference_genes_reviewed.csv`, grades only). Claude or any OpenAI-compatible endpoint (the
  lab's shared model) reads, set by environment variables; replies are cached, and a curation with the same
  terms, rules, models and corpus is reused. The index is mounted read-only (the AiScientist shared dataset
  `retigene`); the image holds the query embedding model.

- Community and maintenance files following the AiScientist skills registry: code of conduct,
  security policy, citation, editor and pre-commit configuration, CODEOWNERS, Dependabot, issue
  and pull request templates, `docs/governance.md`, `docs/architecture.md`, `CLAUDE.md`.
- A uv workspace with `uv.lock`. CI runs index, lint (with dependency audit), test (Python 3.12
  and 3.13), pip install and container build gates. The release workflow checks the version,
  pushes the image and publishes a GitHub release.
- A README catalog grouped by category, generated with `servers.json` by `index_servers.py`
  (`--check` in CI).

## common 0.0.2 - 2026-11-04

### Added

- `AISCI_EXECUTOR=inline` runs a job in the foreground, so `submit` returns when it ends. Use it
  when each tool call is a fresh process or container.

### Fixed

- A local job whose process is gone without `done.json` was reported RUNNING if its pid was reused,
  for example in a new container PID namespace. Jobs are now identified by host, pid and start
  time, and a lost job is reported FAILED with a note.
- An unknown `AISCI_EXECUTOR` value is rejected.

## atlas-expression-exploration 0.0.3 - 2026-11-04

### Fixed

- The image had no Helvetica-metric font, so PNG text and figure layout used DejaVu Sans while
  the PDF text was Helvetica. The image now installs Nimbus Sans (`fonts-urw-base35`), and the build
  fails if the figure font does not resolve to it. The Python code is unchanged from 0.0.2.

## atlas-expression-exploration 0.0.2 - 2026-11-04

### Changed

- Requires `aiscientist-mcp-common>=0.0.2`. `submit_query` refreshes a stale state before it reuses
  a query, so a lost local job is resubmitted.

## common 0.0.1 - 2026-11-04

### Added

- Server factory, path checks, a Slurm/local job runner with preemption fallback and a container
  prefix, and the `aiscientist-mcp` CLI client.

## atlas-expression-exploration 0.0.1 - 2026-11-04

### Added

- The first server, with 9 tools. They resolve gene names, run the full-atlas scanpy step, and
  return summaries, scanpy dot, violin and UMAP plots, and a report. It was tested on the HRCA
  snRNA-seq atlas (3.18M nuclei). The category is `single-cell`.
