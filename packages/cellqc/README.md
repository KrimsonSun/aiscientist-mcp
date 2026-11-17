# cellqc

Standalone MCP tools for CellQC 0.3.6 quality control of raw 10x Cell Ranger deliveries.
The calculation is ported from AiScientist's `run_cellqc`: ambient correction, count/feature/
mitochondrial filtering, doublet removal, nuclear fraction when a BAM exists, species-aware
merging, normalization and highly-variable genes. It has no dependency on AiScientist,
`HarnessTool`, the app's catalog, or a per-call `_skill` attachment.

## Tools

| Tool | Purpose |
| --- | --- |
| `run_cellqc` | Submit QC of one or more authorized Cell Ranger folders. |
| `cellqc_job_status` | Poll by `analysis_id` and `task_id`; return the QC result when finished. |

Use CellQC when libraries contain both raw and filtered matrices (`.h5`, unpacked matrix
folders or 10x Cloud `.tar.gz`). An `.h5ad` alone cannot support ambient correction; use the
`scrna-analysis` server's `run_scanpy_qc` for it. Do not run a second doublet-removal step after
CellQC. Different species are processed separately with their own gene sets and checkpoints.

`run_cellqc` accepts `analysis_id` (default `default`), an optional `dataset` and additional
`dataset_paths`, optional `samples` library IDs, `rerun` (default false), and these parameters:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `species` | `"auto"` | the reference the libraries were aligned to; it decides the mitochondrial, ribosomal and hemoglobin gene sets. 'auto' reads it from Cell Ranger's web summary (Transcriptome), else from the gene names |
| `max_pct_mt` | `10.0` | drop a barcode whose counts are more than this percent mitochondrial. CellQC's default 10 suits whole cells; single NUCLEI should carry almost no mitochondrial signal, and the CellQC reference snRNA-seq run used 5 |
| `min_counts` | `500` | drop a barcode with fewer total counts than this (counted after ambient correction) |
| `min_features` | `300` | drop a barcode detecting fewer genes than this |
| `ambient_method` | `"soupx"` | ambient-RNA correction APPLIED to the counts: soupx (estimated from Cell Ranger's clustering), decontx, or none |
| `ambient_compare` | `"decontx"` | a second ambient method that is estimated and reported but NOT applied, to show where the methods disagree; '' for none |
| `chemistry` | `"auto"` | the 10x chemistry, which sets the expected doublet rate: next_gem (3' v2/v3/v3.1, ~0.8% per 1,000 cells) or gem_x (3' v4, about half that). 'auto' reads it from Cell Ranger's web summary |
| `nreaction` | `1` | 10x reactions (GEM wells) pooled into each library. It only changes the expected doublet rate (rate x cells / (nreaction x capacity)), describes how the libraries were made and is NOT measured from the data: confirm it with whoever prepared them |
| `doublet_decider` | `"doubletfinder"` | the doublet caller whose calls REMOVE cells; detailed diagnostics remain in the downloadable QC files |
| `n_top_genes` | `2000` | highly-variable genes kept for the embedding after the libraries are merged |
| `seed` | `42` | seed supplied to CellQC's stochastic steps; this does not guarantee identical diagnostics across executions |
| `warn_ambient_frac` | `0.25` | flag a library whose mean SoupX contamination is above this. An ENGINEERING guard from the CellQC validation guide, not a literature threshold |
| `warn_retained_below` | `0.5` | flag a library that keeps less than this fraction of its Cell Ranger cells. An ENGINEERING guard: losing half a library usually means a threshold, or the library, is the problem |

## Installation and execution

Install the package with `packages/common` in the CellQC environment, or build the image from
`containers/cellqc/Dockerfile` for Linux amd64. Version 0.0.4 locks every scientific package
archive to the validated production environment, including Python 3.12.14, R builds and
native numerical libraries. MCP runs in a separate interpreter; the worker launcher selects
the original scientific Python and executable search path. See the
[container provenance](../../containers/cellqc/README.md) for the two exact environment locks.
Python 3.14 changes the multiprocessing start method and breaks CellQC's nuclear-fraction
step, so this package requires Python 3.12–3.13. Image publication and site acceptance remain
separate validation steps.

```bash
pip install ./packages/common ./packages/cellqc
export CELLQC_DATASET=/path/to/cellranger-delivery
export AISCI_DATA_DIR=/path/to/output
export AISCI_EXECUTOR=slurm
export AISCI_SLURM='--partition=<partition> --account=<account> --cpus-per-task=16 --mem=80G --time=08:00:00'
# If jobs enter an image, configure its shared input/output bindings here:
export AISCI_JOB_PREFIX='apptainer exec --bind /path/to /path/to/cellqc.sif'
# When the server itself runs outside this image, select its scientific worker explicitly:
export AISCI_JOB_PYTHON=/opt/aiscientist-mcp/worker-python
aiscientist-mcp --server cellqc-mcp call run_cellqc '{"analysis_id":"qc-01","max_pct_mt":5.0}'
aiscientist-mcp --server cellqc-mcp call cellqc_job_status '{"analysis_id":"qc-01","task_id":"<returned-task-id>"}'
```

Register `cellqc-mcp` as a stdio command with the same environment in an MCP client. Heavy
analysis libraries are loaded only in the worker; tools/list does not import scanpy.
For an existing outer Slurm allocation or one-shot container, set `AISCI_EXECUTOR=inline`;
then `run_cellqc` waits for the real worker and returns its result. `local` starts a background
worker and requires the enclosing process/container to remain alive. Slurm/local return
`analysis_id`, `task_id`, `state`, and a real Slurm `job_id` where applicable. Poll until
`COMPLETED` or `FAILED`; preemptions use `AISCI_SLURM_FALLBACK` from the common executor.
Version 0.0.2 publishes request, active-task and result JSON atomically and rejects linked
runtime metadata. A completed task with a missing result is reported as failed; cancelled or
failed jobs cannot retain a successful scientific status.
Version 0.0.3 also confines worker output links, checks that the submitted package/defaults
still match, and hides stale prior results while a task is active or retrying. With
`AISCI_JOB_PREFIX`, it runs `python` inside that command rather than a host virtualenv path;
the operator may override the executable with `AISCI_JOB_PYTHON`.
The 0.0.4 image sets this variable to its scientific worker launcher. Preserve that setting
when running the image; its default `python` is the separate MCP transport interpreter.

| Setting | Meaning |
| --- | --- |
| `CELLQC_DATASET` | Default input folder; falls back to `AISCIENTIST_DATASET`. |
| `CELLQC_READ_ROOTS` | Server-approved input roots separated by the platform path separator (`:` on Linux). Required for explicit paths outside the bound folder. |
| `AISCI_DATA_DIR` | Writable output root; the server uses its `cellqc/` child. |
| `AISCI_EXECUTOR`, `AISCI_SLURM`, `AISCI_SLURM_FALLBACK`, `AISCI_JOB_PREFIX`, `AISCI_JOB_PYTHON` | Shared executor configuration. Default resources: 16 CPUs, 80 GB, 8 hours. |
| `CELLQC_CPUS` | CellQC thread count; falls back to `AISCIENTIST_JOB_CPUS`, `SLURM_CPUS_PER_TASK`, then detected CPUs. |
| `CELLQC_TOOL_CACHE` | Optional persistent tectonic cache root; falls back to `AISCIENTIST_TOOL_CACHE`. Otherwise each job uses its own cache. |

Explicit paths are resolved under the configured read roots. Symlinks escaping a dataset are
refused, including nested links; the delivery is never modified. Use a separate output root.
No model argument can choose an executable, image, Slurm account or job prefix.

One analysis admits one active task. Repeated identical calls reuse the active or completed
attempt. Parameter changes or changes to input file identity/size/mtime/ctime cause a new task;
`rerun=true` forces a fresh attempt after the previous job finishes. These metadata detect input
changes without reading every BAM byte; they are not content checksums. Each attempt has its own
output directory, so earlier results remain readable after another attempt.

## Outputs and interpretation

Every task has `request.json`, `state.json`, `done.json`, `job.log`, and `result.json` under
`$AISCI_DATA_DIR/cellqc/<analysis_id>/jobs/<task_id>/`. Its `output/` contains:

- `work/adata_qc.h5ad`: the primary species' checkpoint; other species use
  `work/adata_qc.<species>.h5ad`. Integer ambient-corrected counts are in `layers["counts"]`;
  `.X` and `.raw` are log-normalized. `obs.sampleid`, both doublet callers' diagnostics,
  mitochondrial metrics, and nuclear fraction when available are retained.
- `artifacts/result/`: CellQC's reports, slides, source TeX, metrics and per-cell tables,
  excluding matrices and tables larger than 64 MB.
- `result/cellqc/`: the complete original result directory, including per-library `.h5ad`.
- `artifacts/figures/` and `artifacts/tables/cellqc_metrics.csv`: summary violin and metrics.
- `artifacts/data/cellqc_result.json`: complete result listing and matrix paths.

The result preserves the original QC fields: library counts, cells before/after, thresholds,
assumptions, ambient/doublet details, warnings, checkpoint names and artifact-relative paths.
It adds `checkpoint_path`, `artifact_root`, `result_directory`, and `settings_source` for
standalone consumers. Pass the returned checkpoint to an authorized downstream scRNA input.
The application must explicitly collect these outputs; returning a path does not publish it.

Check the run status, excluded libraries, cells removed by each threshold, mitochondrial genes
matched, retained cells, ambient load and doublet agreement. Low retention, an inert mitochondrial
filter or weak caller agreement require review even when execution completed. CellQC uses
Cell Ranger's cell calls; it does not re-call cells. Chemistry and `nreaction` affect expected
doublet rates and are assumptions, not measurements. A failed slide/LaTeX step with usable
matrices is retained as a warning; a missing usable matrix is an error.

## Default settings and provenance

`assets/cellqc-defaults.json` is an exact copy of
`aiscientist-skills` release `v0.2.1`, skill `qc-snrna-with-cellqc-standalone` version `1.2.0`.
`assets/source.json` records the full source commit and asset SHA-256. The package checks that
checksum and records this provenance in every QC result. Future settings changes require a
reviewed server release; clients cannot inject a `_skill` asset to alter the calculation.

The analysis and fixtures come from
[RCHENLAB/AiScientist run_cellqc](https://github.com/RCHENLAB/AiScientist/tree/48f64a6be1b9f44eb2948eea1ac448df6a4219e2/src/aiscientist/tools/run_cellqc)
and its `tests/test_run_cellqc.py`. Tests retain the original fixture expectations for staging,
metrics, merged counts, multi-species processing, warnings and the complete result directory.
Native MCP tests exercise tools/list, schema rejection, the actual inline worker subprocess,
request reuse/invalidation, path bounds, concurrent submissions and stdio startup.

The regression fixture substitutes a small executable for CellQC itself. It verifies port
behavior, not R/SoupX/DoubletFinder correctness. A separate real acceptance run used three
Cell Ranger libraries spanning two species, the existing CellQC 0.3.6 production environment,
identical inputs and settings (`seed=42`, `max_pct_mt=5`), and independent executions of this
MCP port and the original application tool. Both completed; native tools/list, invalid-input
rejection, job status and unchanged-request reuse also passed.

The resulting normalized matrices, integer counts, raw values, observation/gene metadata and
retained cell counts matched for both species. Strict equality of the complete metrics table
**did not pass**: the four diagnostic `ambient_decontx_contamination_{min,median,mean,max}`
columns differed, with a largest absolute mean difference of approximately 0.01173. DecontX
was the comparison method in these runs; SoupX supplied the applied counts, which matched.
The discrepancy remains recorded rather than being hidden by a looser equality threshold.
Focused controls found identical R-decoded input matrices and gene/barcode order. The saved
native and baseline R scripts were identical after normalizing workspace paths and timestamps;
isolated replays of both reproduced the baseline diagnostics. Direct upstream DecontX calls
instead reproduced the initial native diagnostics on both tested CPU families and with
explicit single-thread limits. At that stage these controls had not explained the original execution context difference.
That complete-metrics comparison remains recorded as failed; subsequent controls are below.

A later native acceptance of the published 0.0.3 image completed the same three-library
pipeline and protocol controls, but **also failed strict scientific parity**. The fresh conda
solve had changed scientific dependencies. Total retention was still 34,274 cells; the human
checkpoint exchanged 24 retained cells, and doublet scores/classes, highly-variable genes
and eight metrics columns differed. Counts on aligned common cells were identical; normalized
values differed by at most approximately 8.9e-16. This is not equivalence of the full outputs.
Version 0.0.4 replaces the fresh solve with the complete original scientific environment lock.
The dependency difference is established. Subsequent frozen-environment and website
acceptance are recorded below; the 0.0.3 failures are not relabeled as passes.

These runs also retained warnings about doublet-caller disagreement and strong mitochondrial
filtering in one library. The chosen 5% cutoff is an acceptance-test parameter, not a universal
recommendation. No BAM was supplied, so this acceptance does not cover nuclear-fraction
calculation. These fixture-specific results do not establish a universal QC threshold.

## Final-image and website evidence (2026-11-13)

The frozen 0.0.4 scientific environment completed all three original libraries in Slurm
`59537319`. Its strict comparison against a fresh, full original-tool replay (`59539631`)
passed all nine checks in `59539874`, at the unchanged `1e-10` comparison tolerance. The
fresh original replay itself differs from the first original baseline in the same four
DecontX diagnostic columns. A controlled experiment on one AMD host, with identical inputs
and recorded RNG state, reproduced both diagnostic outcomes by changing OpenBLAS threads
from one to sixteen. This supports a thread-sensitive explanation; it does not prove the
unobserved thread state of every earlier run or turn historical failures into passes.

Version 0.0.5 preserves the frozen scientific closure and fixes default CLI interpreter
selection. Its actual SIF passed default CLI/schema and dual-interpreter checks in `59538515`.
The normal website run `a1608e696d7b` then executed the final image in `59539880`, using all
34 verified input files from the three original libraries plus a provenance manifest.
All nine scientific comparisons against the first original baseline passed: matrices,
integer counts, raw values, cell/gene metadata and complete CSV tables, retaining 34,274 cells
across mouse and human. Plot/PDF timestamp bytes are not part of scientific equality.

The first website artifact collection failed across filesystems after science completed;
that failure and a cancelled retry remain recorded. An approved status-only recovery of the
original task completed in `59541576`, without new QC. Inventory `59541627` verified both
species checkpoints, both saved processed datasets and the full 24-file result tree.
The browser-downloaded full CellQC ZIP matched every exported file by SHA-256
(201,849,813 uncompressed bytes). No unused BAM was staged.

Released image: [`cellqc-v0.0.5`](https://github.com/liujs123456/aiscientist-mcp/releases/tag/cellqc-v0.0.5), OCI `sha256:c9e83a362e6bc7a10609d76d53861f5ac59fade870e262d86aba8399d4c724e1`.

The reviewed report-only revision `f698fa3d06d7` passed its requested scientific and delivery
content gates in both main and technical reports. The browser copies match the gateway
bytes, and the original scientific state and plan remained unchanged. The reports distinguish
the two saved processed datasets and complete 24-file CellQC ZIP from the ordinary report
bundle and temporary work checkpoints; they identify interface 0.0.5 and engine 0.3.6 as
separate components. The schema declares `ambient_compare`; seed 42 is request provenance,
not independently confirmed by the status snapshot. Earlier failed report revisions remain
recorded. Generic numeric/QC-accounting checks still miss the cross-library doublet sum and
native status identity; their original warnings are preserved rather than treated as passed.
The technical retention value 84.8% uses the rounded native fraction, whereas the main
report's 84.7% uses the exact cell counts: the recorded 0.1 percentage-point double-rounding
difference does not alter any counts. This content gate does not certify all automated review
or layout claims, alternate thresholds, or BAM-derived nuclear fraction.
