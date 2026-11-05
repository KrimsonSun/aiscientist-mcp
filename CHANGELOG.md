# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each package is
versioned on its own ([Semantic Versioning](https://semver.org/)) and released with the tag
`<package>-vX.Y.Z`.

## [Unreleased]

### Added

- `retigene-rag` 0.0.1: search tools over the RetiGene corpus for an agent, which writes its own searches.
  Keyword search over every sentence (`search_sentences`) leads, with embedding search over passages
  (`search_passages`) and `read_passage` beside it. `grade_genes` grades every gene the sentences holding the
  agent's terms name: Claude Haiku answers the lab's rule checks, and Claude Sonnet reviews the A and B genes.
  It returns a gene table and a sentence table, each row with a verbatim quote, PMID and page. `evaluate`
  scores a grading against the curator reference of 2026-11-04, which is bundled. `eval/agent_eval.py` runs
  the 15-gene test over stdio. Tested with Claude Haiku as the agent on "What are haploinsufficient genes in
  RetiGene papers?" and a reworded question: 15 of 15 A genes in all three runs, at $0.46-0.51 and about
  1 minute per question.

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
