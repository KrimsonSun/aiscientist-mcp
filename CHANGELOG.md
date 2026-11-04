# Changelog

Each server is versioned on its own and tagged `<server>-vX.Y.Z`; the shared runtime is tagged
`common-vX.Y.Z`.

## [Unreleased]

### Added

- `common` 0.1.0: server factory, path checks, Slurm/local job runner with preemption fallback and
  container prefix, and the `aiscientist-mcp` CLI client.
- `atlas-expression-exploration` 0.1.0: the first server. Its 9 tools resolve gene names, run the
  full-atlas scanpy step, and return summaries, scanpy dot, violin and UMAP plots, and a report.
  It was tested on the HRCA snRNA-seq atlas (3.18M nuclei).
