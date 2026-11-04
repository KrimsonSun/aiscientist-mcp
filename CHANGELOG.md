# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Each package is
versioned on its own ([Semantic Versioning](https://semver.org/)) and released with the tag
`<package>-vX.Y.Z`.

## [Unreleased]

### Added

- Community and maintenance files following the AiScientist skills registry: code of conduct,
  security policy, citation, editor and pre-commit configuration, CODEOWNERS, Dependabot, issue
  and pull request templates, `docs/governance.md`, `docs/architecture.md`, `CLAUDE.md`.
- A uv workspace with `uv.lock`. CI runs index, lint (with dependency audit), test (Python 3.12
  and 3.13), pip install and container build gates. The release workflow checks the version,
  pushes the image and publishes a GitHub release.
- A README catalog grouped by category, generated with `servers.json` by `index_servers.py`
  (`--check` in CI).

## common 0.0.1 - 2026-11-04

### Added

- Server factory, path checks, a Slurm/local job runner with preemption fallback and a container
  prefix, and the `aiscientist-mcp` CLI client.

## atlas-expression-exploration 0.0.1 - 2026-11-04

### Added

- The first server, with 9 tools. They resolve gene names, run the full-atlas scanpy step, and
  return summaries, scanpy dot, violin and UMAP plots, and a report. It was tested on the HRCA
  snRNA-seq atlas (3.18M nuclei). The category is `single-cell`.
