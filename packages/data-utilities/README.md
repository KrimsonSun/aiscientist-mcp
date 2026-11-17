# data-utilities

Two native MCP tools migrated from `RCHENLAB/AiScientist` commit `48f64a6`, with the
original bounded byte inspection, evidence-grounded description, DOT renderer and layout
algorithms. Source hashes are in `provenance.json`; the original MIT license is retained.
The server has no dependency on the AiScientist application or `HarnessTool`.

- `inspect_dataset(path="", analysis_id="default", rerun=False)` reads at most 256 KiB of
  a file's head. It returns the original `peek`, `description`, and `raw_data_to_llm` fields.
  VCF header facts, HDF5 tree/shape metadata and table columns come from code. Unknown or
  malformed formats remain best-effort descriptors. Missing or disallowed paths are MCP errors.
- `make_schematic(source, basename="schematic", analysis_id="default", rerun=False)` renders
  conceptual Graphviz DOT as PNG, preserves the `.dot` source and reports dimensions/aspect
  warnings. It uses the original filename sanitization and `figure` relative path. File/image
  references in DOT and HDF5 external links are refused. Data plots belong to analysis tools.
- `data_utilities_job_status(analysis_id, task_id)` polls queued or completed tasks.

These short tools default to `inline`, returning their completed structured result. Set
`AISCI_EXECUTOR=slurm` or `local` for submission and polling. The common task runtime serializes
writes per analysis and includes arguments, input identity and non-secret model configuration
in task identity. A missing renderer or failed worker never counts as a successful figure.

Outputs live under `$AISCI_DATA_DIR/data-utilities/<analysis_id>/` (default base `./data`).
For schematics, `artifact_root` is the `artifacts/` directory and `figure` is relative to it.
Inspection persists its complete result in a distinct `artifacts/data/dataset_inspection-*.json`
for each attempt. A large response is marked `response_omitted=true` and points to `full_result`;
both MCP text and structured copies are bounded together, including status polling. A later
inspection never overwrites an earlier attempt's full evidence.
The `.dot` source remains available after a renderer failure; task logs/status retain the reason.

Configure `DATA_UTILITIES_DATASET` (or compatibility alias `AISCIENTIST_DATASET`) as the bound
input. An explicit `path` must be inside `DATA_UTILITIES_READ_ROOTS` (OS-path-separator list)
or be that bound file. Server-side paths must exist on this service host; the client adapter
is responsible for mapping paths from other hosts. Symlink escapes and output symlinks are refused.

Without a configured model, inspection uses the same deterministic description fallback as
before. To enable descriptions by an existing OpenAI-compatible service, set
`DATA_UTILITIES_MODEL_ENDPOINT` (base URL ending in `/v1`) and `DATA_UTILITIES_MODEL`.
`DATA_UTILITIES_MODEL_API_KEY_FILE` is preferred for credentials; `DATA_UTILITIES_MODEL_API_KEY`
is also supported. `DATA_UTILITIES_MODEL_TIMEOUT` defaults to 180 seconds. Only the bounded
file evidence goes to the configured endpoint; the original grounding and fallback behavior
remain in place. No model is provisioned and no endpoint is selected from tool arguments.

The image installs Graphviz and uses the workspace lock. Run `uv run pytest packages/data-utilities`
for unit/protocol tests. `AISCI_LEGACY_SOURCE=/path/to/AiScientist` enables actual-source parity:
original/new inspection objects and DOT are equal, and PNG bytes are compared using the same
real Graphviz binary. These checks do not replace deployment testing in the target environment.

The released `data-utilities-v0.0.2` image with common 0.0.4 passed native HPC3 acceptance
in Slurm job `59536142` (standard partition, 30s). Inspection of the real public HECA
265,767-cell by 36,601-gene HDF5 dataset matched the original code in the new image; rendering the test
workflow with the same new-image Graphviz 2.42.4 binary produced identical PNG bytes. A separate check ran the unchanged original renderer with the production website
interpreter and host Graphviz (also 2.42.4); its 587 × 539 PNG was byte-identical to this final
image output (SHA256 `ed60002b1764050f787d69c9e09f3e691c0992f091ca748d9b831964223edc36`).
The earlier source-staged HPC host used Graphviz 2.44.0, not the website runtime. Tool and status responses
stayed below 24 KB, including a long-line input whose complete content was preserved in its
artifact. This installed-image test used deterministic inspection; the subsequent website
check is recorded below.

## Normal website execution (2026-11-13)

Website run `db89ba14f023` completed `inspect_dataset` and `make_schematic` through the
final 0.0.2 image in the same reviewed 13-tool workflow as the eleven scRNA tools.
The deterministic inspection peek matched the original implementation. The generated
description was checked for consistency with that input evidence, not byte equality.
The exact DOT source and PNG matched the unchanged original renderer using the actual
EyeServer Graphviz 2.42.4 runtime; canonical and namespaced artifacts were both preserved.

Released image: [`data-utilities-v0.0.2`](https://github.com/liujs123456/aiscientist-mcp/releases/tag/data-utilities-v0.0.2), OCI `sha256:dd89779ffa9d42597d7dc3d856a2cf71fb8d0552e1e778713092ffdc09aa951c`.

The website diagram is 795 by 59 pixels and correctly reports a page-fit/aspect warning.
Byte-identical rendering does not imply publication-quality layout. The same workflow's
reviewed report-only revision `60213c4b8aa7` passed its targeted main/technical content checks,
with browser/gateway byte equality and unchanged scientific state. Generic numeric matching
limitations remain recorded in the scRNA README; this is not certification of every automated
report check, generated claim, supported input format or layout.
