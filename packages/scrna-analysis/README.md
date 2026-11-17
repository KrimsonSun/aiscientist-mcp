# scrna-analysis

Independent MCP server for the eleven single-cell RNA tools previously implemented in
`RCHENLAB/AiScientist`. The scientific functions were extracted from commit `48f64a6`; the
package does not import `aiscientist` or use `HarnessTool`. Source hashes are in
`src/aiscientist_mcp/scrna_analysis/provenance.json` and the original MIT license is retained.

## Tools and flow

Run `run_scanpy_qc` on a single count matrix, then use the same `analysis_id` for downstream
analysis. Cell Ranger deliveries use the separate `cellqc` server. The tools are
`run_scanpy_qc`, `run_clustering`, `run_de`, `run_enrichment`, `run_depth_matched_de`,
`run_gsea_prerank`, `run_doublet_detection`, `run_integration`, `run_pseudobulk_de`,
`run_composition`, and `run_marker_annotation`. Their original argument names, types, enums
and declared defaults are retained. Each also accepts:

| Parameter | Meaning |
| --- | --- |
| `analysis_id` | A safe workspace identifier; default `default`. Use a different identifier for independent analyses. |
| `dataset` | Optional admitted input path. Empty reuses the analysis's dataset or the configured default. |
| `rerun` | Re-execute an otherwise reusable completed task. Never interrupts an active task. |

With `AISCI_EXECUTOR=slurm` or `local`, a call returns `analysis_id`, `task_id`, `job_id`, and
`state`. Poll `scrna_job_status(analysis_id, task_id)` until `COMPLETED` before starting a
step that consumes its outputs. `job_id` is the actual Slurm identifier, empty for local and
inline jobs. With `inline`, the call blocks inside the caller's allocation and returns the
original scientific result together with those identity fields. Failed submission or an
inline scientific failure is an MCP error; polling retains the full failure result.

Results include `artifact_root` and `checkpoint_path` for an application-owned artifact
adapter. Figures/tables keep the original relative names. Raw matrices remain in `work/`.
No arbitrarily supplied result path is copied back to a client.

## Configuration

| Environment variable | Meaning |
| --- | --- |
| `AISCI_DATA_DIR` | Writable base; outputs are confined to `<base>/scrna-analysis/<analysis_id>/`. Default `./data`. |
| `SCRNA_DATASET` | Operator-admitted default input. `AISCIENTIST_DATASET` is accepted as a compatibility alias. |
| `SCRNA_READ_ROOTS` | `os.pathsep`-separated allowed roots for explicit datasets; canonical paths are checked. |
| `SCRNA_CHECKPOINT_ROOT` | Optional operator-set read-only checkpoint handoff directory. Missing `adata_*.h5ad` files are copied into a new workspace inside the worker; existing checkpoints are never overwritten. Symlinks are refused. |
| `SCRNA_GENESETS_DIR` | Read-only local `.gmt` libraries for enrichment. `AISCIENTIST_GENESETS_DIR` is also accepted. No gene libraries are downloaded automatically. |
| `AISCI_EXECUTOR` | `slurm` (default), `local`, or `inline`; shared common job executors. |
| `AISCI_SLURM`, `AISCI_SLURM_FALLBACK` | Submission and preemption fallback resources. Defaults: 4 CPUs, 16 GB, 4 hours. |
| `AISCI_JOB_PYTHON` | Trusted scientific worker executable. The image defaults to `/opt/aiscientist-mcp/worker-python`; a host-side container prefix must explicitly select this in-image launcher. |
| `AISCI_JOB_PREFIX` | Trusted container launcher prepended to the worker command, e.g. an Apptainer invocation with approved mounts. |

A dataset binding cannot be changed halfway through an analysis. Task identity incorporates
the tool, arguments, input file identities and configured gene sets. Changed outputs invalidate
completed-job reuse, including added files. Each actual execution gets a distinct `task_id`;
explicit reruns and retries after failure retain earlier request/result records. Successful
attempts are reusable only while both input identity and the complete output inventory match.
A workspace has at most one active analysis task; submission and worker locks prevent concurrent
checkpoint writers, while status remains available during inline work. Metadata is published
atomically and refuses symlinks. File identity uses canonical path, size, nanosecond mtime/ctime
and inode, not a content checksum. Operators must treat admitted reference datasets as immutable
while a task runs. A changed upstream handoff requires a new `analysis_id`.

The server imports the analysis stack only inside worker functions. It can list schemas without
loading Scanpy. Every long call uses `aiscientist_mcp.common.jobs`; `inline` is appropriate when
the AiScientist bridge has already allocated the outer Slurm job.

## Run

```bash
uv sync --all-packages
export AISCI_DATA_DIR=/path/to/results
export SCRNA_DATASET=/path/to/counts.h5ad
export AISCI_EXECUTOR=inline
uv run aiscientist-mcp --server scrna-analysis-mcp call run_scanpy_qc \
  '{"analysis_id":"example","min_genes":200,"min_cells":3,"max_pct_mt":10}'
```

Build the image from the repository root with
`docker build --platform linux/amd64 -f containers/scrna-analysis/Dockerfile -t mcp-scrna-analysis:0.0.6 .`.
The image separates MCP Python 3.13 from scientific Python 3.11.15. Its scientific lock
contains 63 original `analysis.sif` / `analysis-pydeps` packages, checked against the deployed
distribution RECORDs, plus 12 packages for optional Harmony/scikit-image and worker JSON
Schema validation. Exact archive hashes and package-file provenance are committed under
`containers/scrna-analysis/`. The build verifies installed file hashes; no original scientific
package is upgraded. MCP dependencies follow `uv.lock` in their own interpreter. Numba and
Matplotlib caches are private to each worker attempt, including inside read-only SIFs.
A deployment must pin the released image digest; this source package does not invent one.

## Validation and compatibility

The tests carry forward the original scientific regression cases, exercise real MCP listing,
input validation and stdio, run real inline/local workers, and check Slurm command generation,
path admission, checkpoint handoff and job reuse. Synthetic real AnnData fixtures cover QC and
retinal marker annotations. An opt-in cross-repository regression runs all eleven algorithms
against the original implementation:

```bash
AISCI_LEGACY_SCRNA_ROOT=/path/to/AiScientist/src \
  OMP_NUM_THREADS=2 NUMBA_NUM_THREADS=2 LOKY_MAX_CPU_COUNT=2 \
  uv run pytest packages/scrna-analysis
```

The port explicitly copies ComBat's corrected matrix before in-place scaling: recent Scanpy
can return a read-only array. This changes mutability, not the numeric correction. Harmony is
bounded to its pre-1.0 API because the current Scanpy integration expects that result layout.
New AnnData nullable-string metadata is converted only in a private dataset copy or imported
checkpoint. The HDF5 conversion preserves expression datasets, labels and missing positions;
its provenance is exported via `input_compatibility_path`. Caller files stay unchanged.
External/soft HDF5 metadata links are refused. No global AnnData setting is changed.
Scientific limitations and refusal conditions are documented with each tool below.

The released `scrna-analysis-v0.0.3` image with common 0.0.4 passed native HPC3 acceptance
in Slurm job `59536842` (standard partition, 3m45s). On the public 480-cell, 1,550-gene HECA
fixture, all eleven tools matched the unchanged original code running in the same new scientific
environment at the recorded test parameters:
returned scientific results, exported CSV values and five AnnData checkpoints (including X,
obs, var, layers and raw). The installed-image test also passed cache reuse, distinct rerun and
failed-attempt history, status and strict argument checks. This is algorithm-port parity, not a comparison with the old production `analysis.sif`
dependency environment. Subsequent old-environment and website checks are recorded below;
this earlier same-environment result alone was not a retirement gate.

A subsequent actual old-environment comparison found marker-score and first-pass label
differences with the newer dependencies; a direct same-checkpoint comparison confirmed the
marker difference, while doublet detection, clustering and DE passed. Version 0.0.4 therefore
freezes the deployed scientific stack. The original environment lacks Harmony: explicit
Harmony requests are tested as added capability, not claimed as an old-runtime success.
Version 0.0.5 additionally preserves the deployed `auto` selection of ComBat through an
explicit adapter policy, independently of installed optional packages. It retains the
requested arguments and records the effective selection in `integration_policy`; no
scientific function or numeric threshold is changed. A direct local-image comparison against
the actual old default-auto run (HPC3 job `59539876`) matches ComBat scientific fields, CSV
and all AnnData matrix/embedding slots at the original tolerances. The obsolete
missing-Harmony fallback note is intentionally absent; the new policy record explains
the selection without claiming a missing dependency.
The 0.0.4 local Docker smoke restores marker result/CSV/AnnData parity against the actual
old-runtime checkpoint at the unchanged tolerances, and executes QC on the original modern
AnnData input without modifying it. Default CLI/schema listing, installed archive/file
verification and private worker caches also pass. The final released `scrna-analysis-v0.0.5` SIF passed HPC3 job `59540145` (5m04s): all
11 tools match the unchanged original code in the frozen scientific environment. An
independent fresh old production-image run (`59540080`) matches the ten originally
supported calls, CSVs and all AnnData X/obs/var/layers/raw/obsm/obsp/varm/varp values
(38 checks, unchanged `rtol=1e-6`, `atol=1e-8`). The old image lacks Harmony, so explicit
Harmony is separately verified against original code in the final environment. Both omitted
and explicit `auto` requests match the actual old default-ComBat run (`59539876`), including
the integration embedding. Only QC's displayed private-copy basename and the obsolete
missing-Harmony note differ as described above. Cache reuse, retained rerun/failure history,
status and strict argument checks pass; the largest tool response is 9,719 bytes. The server
runs from the installed image without a source overlay or operator cache override.

Release identity: OCI `sha256:6bf03b30e2c9a3af6043eef1751518494ae59b728cb175faf003ecab697b7afa`,
converted SIF `sha256:7fa796929464cb9d769f14529ee7ddc51667aa289219c50d0fa3c9642d4d3341`.
Default CLI, full schema and all 75 package / 13,117 file checks passed in staging job
`59540097`. The later normal website execution is recorded below.

### Normal website execution (2026-11-13)

Website run `db89ba14f023` invoked all eleven native tools in the reviewed order, alongside
the two data-utility tools, using the final 0.0.5 image and the verified public HECA subset.
All 47 scientific/artifact checks passed against final native job `59540145`: full scientific
result fields, 25 tables, and five AnnData checkpoints including X/obs/var/layers/raw and
embedding/graph slots. Checkpoint files were additionally byte-identical in this run.
Comparison tolerances remain `rtol=1e-6`, `atol=1e-8`; only known artifact-root/path display
differences are normalized. AnnData `uns`, figure bytes and generated model prose are not
claimed to be equal. This bounded 480-cell regression is not a full-atlas benchmark.

The actual data inspection and DOT/PNG checks are documented in data-utilities. Reviewed
report-only revision `60213c4b8aa7` passed 13 targeted checks across the complete main and
technical reports; the browser copies match gateway bytes and the scientific plan/state are
unchanged. Both distinguish the ORA `top_n_genes=100` cap from the full 1,502-gene GSEA
rankings, the executed periphery-versus-fovea contrast from descriptive age metadata, and
explicit Harmony from default ComBat and QC/clustering. Earlier failed report revisions
remain recorded.

This report gate does not validate the generic numeric matcher: it incorrectly associates
ARR3 detection 2.3% with an unrelated formula, and five values remain automatically untraced.
The actual marker warning and scientific tables are the relevant evidence. Limitations remain:
480 selected cells, 20 GSEA permutations, cell-level pseudoreplication, three samples per tissue
arm and a fixed marker panel. No discovery or clinical/general-population conclusion follows.

## Scientific tool reference

The following descriptions accompany the MCP tools. The execution location is the server's
configured executor and workspace described above; application-specific planning and data
attachment happen in the client.

# run_scanpy_qc

The first step of the single-cell line. It computes per-cell quality metrics (genes detected, total counts, mitochondrial share), removes low-quality cells and rarely detected genes by the declared thresholds, normalises and log-transforms the counts, and selects highly variable genes. Every later scRNA tool reads the checkpoint it writes.

## When the agent uses it

- First, on a single-cell matrix: `.h5ad`, a 10x `.h5` or `filtered_feature_bc_matrix/` folder, loom, or a text matrix.
- On a subset or merge that a `run_code` step saved, through `input`.

## When the agent does NOT use it

- The bound dataset is a folder of 10x Cell Ranger outputs with raw AND filtered matrices: use
  `run_cellqc`, which corrects ambient RNA from the raw matrix and QCs every library. This tool
  reads one filtered matrix and cannot correct ambient RNA. The gateway flags such a folder in the
  data profile (`⚠ INPUT is a 10x Cell Ranger delivery …`).
- After `run_cellqc`: it already wrote the filtered, normalised checkpoint. Running this tool on top
  would filter a second time with different thresholds.
- On a dataset an earlier run handed on (`<input>.processed.<time>.<run>.h5ad`, recognised by its
  `uns["aiscientist"]` record). It is already filtered and log-normalised, so the tool refuses it:
  QC there would filter on log values and normalise twice. The gateway has already written such a
  file back as this run's checkpoints (`_lib/result_dataset.adopt`), so the next tool reads it
  directly. Tests: `tests/test_session_continuation.py`.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `min_genes` | integer | `200` | drop a cell detecting fewer genes than this — an empty droplet or a dying cell |
| `min_cells` | integer | `3` | drop a gene detected in fewer cells than this — too sparse to support any test |
| `max_pct_mt` | number | `10.0` | drop a cell whose reads are more than this percent mitochondrial (a stressed or lysed cell). 10 is the common working threshold for tissue; the Seurat and scanpy tutorials use 5 for PBMC, and single NUCLEI need far less (1-5) because a nucleus should carry almost no mitochondrial signal. Raise it only for a tissue known to be mitochondria-rich, and say so |
| `n_top_genes` | integer | `2000` | how many highly-variable genes to keep for the embedding — more genes carry more structure and more noise. 2000 is Seurat's default and the usual starting point |
| `warn_removed_pct` | number | `50.0` | warn when QC discards more than this percent of cells. An ENGINEERING guard, not a literature threshold: losing half a dataset usually means a threshold is wrong for this tissue, and it should be checked before the result is used |
| `mito_prefix` | string | `'MT-'` | the name prefix that marks a mitochondrial gene, matched without regard to case, so the default also covers mouse 'mt-'. It is the only way `max_pct_mt` knows which genes to count: a dataset whose genes match nothing gets no mitochondrial filter at all |
| `gene_symbols_key` | string | `''` | a `var` column holding gene SYMBOLS, for data whose gene names are Ensembl IDs (cellxgene files keep the symbols in `feature_name`). The mitochondrial prefix is then matched against that column. Empty = match the gene names themselves |
| `input` | string | `''` | an .h5ad to QC INSTEAD of the bound dataset — e.g. a subset or merge a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the bound dataset. |
<!-- /generated:parameters -->

## Outputs

- `work/adata_qc.h5ad`, the checkpoint the rest of the line reads. The raw counts are kept in `layers["counts"]` for the tools that need them (doublets, pseudobulk, depth matching).
- Figures in `artifacts/figures/`: QC violins, genes-vs-counts and mitochondrial scatter plots, the highly-variable-gene plot.
- Result: cells and genes before and after, the thresholds used, `warnings` (for example more than `warn_removed_pct` of cells removed, or no gene matched `mito_prefix`), and `read_from`.


## Known limits

- Thresholds are fixed numbers for the whole run, not adaptive per sample. Single-nucleus data needs a much lower `max_pct_mt` than the tissue default.
- Mitochondrial genes are found by name prefix. Data whose gene names are Ensembl IDs needs `gene_symbols_key`, or it gets no mitochondrial filter at all.
- It loads the whole matrix into memory inside the job.

# run_clustering

Groups cells into clusters for data that carries no cell-type labels: PCA on the highly variable genes, a nearest-neighbour graph, Leiden clustering and a UMAP layout for display. With `select_resolution` it re-clusters resampled subsets at each candidate resolution and keeps the most stable one (adjusted Rand index) instead of accepting the default 1.0.

## When the agent uses it

- After `run_scanpy_qc` (and `run_doublet_detection` / `run_integration` when used), only when the data has no labels to reuse.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `resolution` | number | `1.0` | Leiden granularity: higher splits the cells into more, smaller clusters |
| `n_pcs` | integer | `30` | principal components fed into the neighbourhood graph |
| `n_neighbors` | integer | `15` | neighbours per cell in that graph — larger gives smoother, coarser structure |
| `select_resolution` | boolean | `False` | choose the resolution by bootstrap stability instead of accepting the default |
| `n_bootstrap` | integer | `10` | resampling rounds per candidate resolution when selecting one |
| `subsample_frac` | number | `0.8` | fraction of cells per bootstrap round |
| `stability_min` | number | `0.9` | minimum adjusted Rand index a resolution must clear to be called stable |
| `max_sweep_cells` | integer | `20000` | cap on the cells used for the stability sweep |
| `resolution_candidates` | array of number | — | resolutions to try when select_resolution is on |
| `input` | string | `''` | an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. |
<!-- /generated:parameters -->

## Outputs

- `work/adata_clustered.h5ad` and `artifacts/figures/umap_clusters.png`; `tables/resolution_sweep.csv` when a resolution was selected.
- Result: `n_clusters`, cluster sizes, `resolution_source` (`default`, `explicit` or `bootstrap_stability`), and warnings, for example when the data already has a cell-type column or the partition collapsed to one cluster.


## Known limits

- Cluster numbers are not cell types. Naming them is `run_marker_annotation` or `scgpt_annotate`.
- Without `select_resolution` it uses scanpy's `leidenalg` backend, which iterates to convergence; scanpy warns that igraph will become the default. With `select_resolution`, the sweep and the final partition use igraph's two-pass Leiden (`flavor="igraph", n_iterations=2`, undirected), reported as `params.leiden_flavor`. The sweep is about 88 Leiden calls, and leidenalg took 50-56 s per call at resolution 1-2 on 16,750 cells against 0.7 s for igraph, which pushed the sweep into the job's 1-hour limit.

# run_de

Tests genes for expression differences with scanpy's `rank_genes_groups` (Wilcoxon by default). In marker mode each level of `groupby` is compared with all other cells. Given `reference` it compares a condition with a control, and adding `stratify_by` repeats that contrast separately within each cell type, listing the cell types it had to skip.

## When the agent uses it

- Markers per cluster after clustering.
- KO vs WT or disease vs control per cell type on labelled data, with no re-clustering.
- When the design has replicate samples, the replicate-aware comparison is `run_pseudobulk_de`; `run_de` treats cells as independent, and its result carries an `inference` label that says so.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `groupby` | string | `'leiden'` | the obs column whose levels are compared |
| `method` | string (`wilcoxon`, `t-test`, `t-test_overestim_var`, `logreg`) | `'wilcoxon'` | the rank test rank_genes_groups uses |
| `n_genes` | integer | `50` | rows kept per group — per DIRECTION when it is a contrast |
| `reference` | string | `'rest'` | the baseline level. 'rest' gives one-vs-rest MARKERS; a named level (e.g. 'WT') gives a real condition-vs-control contrast |
| `stratify_by` | string | `''` | an EXISTING cell-type column — runs the contrast separately within each cell type instead of pooling them |
| `min_cells` | integer | `30` | a group with fewer cells than this in either arm is skipped. 30 is the usual rule-of-thumb floor for the rank test's normal approximation — an ENGINEERING guard, not a literature threshold |
| `min_pct` | number | `0.1` | a gene must be detected in at least this fraction of the cells of one of the two populations compared, or it is not tested at all (Seurat FindMarkers' min.pct). Without it most genes enter the test undetected — on the DDX41 retina object 2/3 did — which triples the BH denominator and floods the ranking with divide-by-zero fold-changes above 2^20. 0 disables |
| `tie_correct` | boolean | `True` | tie-correct the Wilcoxon normal approximation. A single-cell matrix is ~90% zeros, so ties dominate every comparison; scanpy's default (False) is anti-conservative on sparse data |
| `padj` | number | `0.05` | adjusted-p (Benjamini-Hochberg) cutoff for calling a gene significant |
| `lfc` | number | `0.25` | minimum \|log2 fold-change\| for calling a gene significant — applied together with `padj`, and drawn as the volcano's vertical line. 0.25 is the single-cell convention (Seurat's FindMarkers threshold); 1.0 is a bulk-RNA habit and hides most real single-cell effects |
| `force` | boolean | `False` | run a pooled per-cell test across a CONDITION column anyway. The result is pseudoreplicated and must be reported as non-inferential |
| `groups` | array of string | — | restrict the comparison to these levels of groupby |
| `input` | string | `''` | an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. |
<!-- /generated:parameters -->

## Outputs

- `work/adata_de.h5ad`.
- Tables: `tables/de_<key>_all.csv` with exactly the columns `group,gene,log2fc,pval,pval_adj,score`, one CSV per group, `de_<key>_universe.txt` (the tested genes, which `run_enrichment` uses as its background), `rank_<key>_*.rnk` (for `run_gsea_prerank`), `de_<key>_inference.json`, written on every run, so the design (`mode`: `contrast` or `markers`) and the inference label travel with the tables, and `de_<key>_significance.json` (the true significant counts per group, and whether the per-group tables are complete or a top-`n_genes` view) so `run_enrichment` can tell a top-N from the significant set.
- Figures: a marker dotplot; in a contrast, one volcano per group.
- Result: `significant_by_group` (true counts in both shapes; for markers the tables are the top `n_genes` by score, and `table_truncation_note` says so), `skipped_groups`, `cells_by_group_and_arm`, `inference`, `warnings`.


## Known limits

- A cell-level test. With few donors its p-values are pseudoreplicated; the result says so, and later steps must not report them as inferential.
- `n_genes` caps the rows per group (per direction in a contrast).
- `method` is one of scanpy's backends; DESeq2 lives in `run_pseudobulk_de`.

# run_enrichment

Asks which pathways and GO terms the genes from a DE step over-represent, with gseapy's `enrich` against local `.gmt` libraries (no Enrichr web call: the analysis jobs have no network). The background is the set of genes the DE step tested, read from its `de_<key>_universe.txt`.

## When the agent uses it

- After `run_de` or `run_pseudobulk_de`. Called without `genes`, it finds the DE tables itself, per group.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `top_n_genes` | integer | `0` | optional cap on how many genes enter the over-representation test. 0 means no cap — use every gene passing the thresholds below, which is the standard way ORA is run. Set it only to deliberately shorten a very long list, and say that you did |
| `top_n_terms` | integer | `10` | how many enriched terms to report per group |
| `padj` | number | `0.05` | adjusted-p cutoff a DE gene must clear to enter the test — the same Benjamini-Hochberg cutoff run_de applies |
| `lfc` | number | `0.25` | minimum \|log2 fold-change\| a DE gene must clear to enter the test. Pairing an effect-size floor with the p-value cutoff is what keeps a list of thousands of barely-changed genes from returning only large generic terms; 0.25 is the single-cell convention |
| `gene_sets` | array of string | — | local gene-set libraries to test against |
| `background` | integer | — | ORA background size; omit to use the tested universe |
| `split_direction` | boolean | — | test up- and down-regulated genes separately |
| `groupby` | string | — | which DE table to read, by the key in its file name tables/de_<groupby>_all.csv: the column run_de grouped by (e.g. leiden, majorclass), or the `de_key` run_pseudobulk_de returned (pseudobulk_<group_key>), not the `group` column inside the table; omit to find the table automatically, a replicated pseudobulk test first |
| `genes` | array of string | — | explicit gene list; omit so the DE tables are found instead |
<!-- /generated:parameters -->

## Outputs

- `tables/enrichment_<key>.csv` per group and bar plots in `figures/enrichment_<key>.png`.
- Result: the top terms per group, `background_size`, `background_source`, `warnings`, the DE step's `inference` label, and `de_table_read`, the DE table the input came from.

With no `groupby` the DE table is chosen in this order: a `run_pseudobulk_de` result (`de_pseudobulk_<key>_all.csv`, a replicated test), then an annotated grouping, then `leiden`. Each group's genes come from its own complete table (`de_<key>_<group>.csv`; a group named `all` is `de_<key>_all_group.csv`).


## Known limits

- Only the libraries in `tools/genesets/` exist (`describe_environment` lists them). The `.gmt` files are not in git; deploys keep them in place.
- A cut list throws away ranking information; `run_gsea_prerank` is the whole-ranking complement.
- Marker tables from `run_de` are the top `n_genes` per group, so on large data every row passes the gate and the input is that top-N, not the significant set. The result then says so in `warnings` and `selection.upstream_table`, with the true counts from `de_<key>_significance.json` (or, for older runs, inferred from every row passing).
- Without per-group DE tables it falls back to one pooled list and a fixed 20,000-gene background, which inflates p-values; the result warns when that happens.

# run_depth_matched_de

Checks whether a DE result survives equal sequencing depth. It randomly removes reads from the deeper arm until both arms match, re-runs the same gene comparison, and reports how much of the original ordering survives (Spearman correlation) and which genes keep their place.

## When the agent uses it

- After `run_de`, when the arms differ in depth (the dataset profile reports the per-arm depth), and before a global up or down shift is read as biology.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `groupby` | string | `'sampleid'` | the obs column holding the experimental CONDITION |
| `reference` | string | `''` | the CONTROL level of `groupby` (e.g. 'WT'). Required — the check is a two-level contrast |
| `stratify_by` | string | `''` | the CELL-TYPE column; the check runs separately within each type, because depth imbalance differs per type and a pooled answer hides it |
| `n_genes` | integer | `100` | how many top-ranked genes per direction enter the comparison. Deep enough that the correlation is not driven by three genes, shallow enough to stay the list a reader would actually look at |
| `min_cells` | integer | `30` | skip a cell type with fewer cells than this in either arm — the usual floor for the rank test's normal approximation |
| `seed` | integer | `0` | random seed for the down-sampling, so the check reproduces |
| `min_ratio` | number | `1.05` | skip a cell type whose arms already differ by less than this ratio in median library size — there is no imbalance to correct |
| `input` | string | `''` | an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. |
<!-- /generated:parameters -->

## Outputs

- `tables/depth_matched_genes.csv`, `tables/depth_matched_summary.csv` and `figures/depth_matched_correlation.png`.
- Result: the correlation, a verdict per group, the depth-robust genes, `skipped_groups`, `warnings`.


## Known limits

- One seeded downsample per call. A low correlation says the original ordering depended on depth; it does not say which individual genes are artefacts.

# run_gsea_prerank

Runs gseapy's preranked GSEA on the complete ranked gene list of each group (the `.rnk` files `run_de` and `run_pseudobulk_de` write), so it detects coordinated shifts that no per-gene cutoff would keep. It reports a signed NES (positive means up in that group), FDR and the leading-edge genes.

## When the agent uses it

- After `run_de` or `run_pseudobulk_de`, as a complement to `run_enrichment`: the two use different null hypotheses, so disagreement between them is not an error. With no `groupby` it ranks a `run_pseudobulk_de` result first (a replicated test), then an annotated grouping, then `leiden`, and reads exactly the groups that result's index names.
- On ranked lists a `run_code` step wrote, through `input`.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `gene_sets` | array of string | — |  |
| `groupby` | string | — | which ranked lists to read, by the key in tables/rank_<groupby>_<group>.rnk: the column run_de grouped by, or the `de_key` run_pseudobulk_de returned; omit to choose automatically, a replicated pseudobulk test first |
| `top_n_terms` | integer | — |  |
| `min_size` | integer | — |  |
| `max_size` | integer | — |  |
| `permutations` | integer | — |  |
| `fdr_max` | number | — |  |
| `seed` | integer | — |  |
| `input` | string | `''` | ranked gene list(s) to test INSTEAD of the rank files run_de wrote — a .rnk file (gene<TAB>score per line) or a file-name pattern such as `my_rank_*.rnk`, inside this run's work/ or artifacts/ directory. Each file is one group, named by its file stem, ranked by the score as given. Empty = run_de's rank files. |
<!-- /generated:parameters -->

## Outputs

- `tables/gsea_<group>.csv` per group.
- Result: the top terms per group, and the permutations, seed and gene-set size bounds used.


## Known limits

- Permutation-based and seeded; few permutations or small libraries give coarse FDR values.
- A group where nothing passes FDR is a null result to report, not a group to drop.

# run_doublet_detection

Runs Scrublet on the raw counts after QC and before clustering. Two cells in one droplet express both parents' programmes and can form an intermediate cluster that reads as a new cell type; this step removes them (or, with `filter: false`, only marks them).

## When the agent uses it

- After `run_scanpy_qc`, before `run_clustering`.

## When the agent does NOT use it

- After `run_cellqc`: DoubletFinder and scDblFinder have already called the doublets and the decider's
  were removed. If obs already holds doublet calls (`doubletfinder_class`, `scdblfinder_class`,
  `predicted_doublet`) the tool returns `skipped` without running Scrublet, unless `force: true`.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `expected_doublet_rate` | number | `0.06` | prior doublet rate handed to the detector — roughly the ~0.8% per 1,000 cells that 10x Genomics quotes, so 0.06 suits a ~8,000-cell lane. Set it from YOUR loading, not from this default |
| `flag_rate_above` | number | `0.2` | warn when the detected doublet fraction exceeds this. An ENGINEERING guard, not a literature threshold: it exists to catch a mis-set expected rate or an overloaded lane, and a run above it is a prompt to check the loading, not a finding |
| `filter` | boolean | — | remove the predicted doublets (default) or only annotate them |
| `batch_key` | string | — | obs column to simulate doublets within, per batch |
| `threshold` | number | — | explicit score cutoff; omit to let scrublet choose one |
| `force` | boolean | — | score again even though obs already holds doublet calls (from run_cellqc or an earlier run); off by default |
| `input` | string | `''` | an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. Doublets are scored and filtered IN PLACE, in that file. |
<!-- /generated:parameters -->

## Outputs

- Updates `work/adata_qc.h5ad` in place (filtered or annotated) and writes `tables/doublet_summary.csv`.
- Result: `doublet_rate`, cells before and after.


## Known limits

- Scrublet needs scikit-image, which `analysis.sif` lacks. The job installs it into the run's own workspace when it is reported missing (`run_deps.py`) and removes it after publish.
- A rate above about 20% usually means the threshold, not the biology. `batch_key` simulates doublets within each batch.

# run_integration

Removes systematic differences between samples or batches so that cells group by cell type rather than by library. Omitted `method` or `auto` uses ComBat to preserve the deployed default. Set `method="harmony"` explicitly to use bundled Harmony. It measures the batch silhouette before and after. The request is retained unchanged; `integration_policy` records the requested and effective method, while `method_used` reports the algorithm that actually ran. The original missing-Harmony fallback note is not reproduced because Harmony is available when explicitly selected.

## When the agent uses it

- Before clustering, whenever the object holds more than one sample; without it cells can cluster by donor, and every downstream label is really a donor label.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `batch_key` | string | — |  |
| `method` | string (`auto`, `harmony`, `combat`) | — | omitted/auto uses ComBat; Harmony requires explicit selection |
| `n_pcs` | integer | — |  |
| `input` | string | `''` | an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. |
<!-- /generated:parameters -->

## Outputs

- `work/adata_integrated.h5ad` and `tables/integration_batches.csv`.
- Result: `method_used` (the method that actually ran), batch silhouette before and after, and a warning when it did not drop.


## Known limits

- ComBat corrects the expression values themselves, Harmony only the embedding; check `method_used` before comparing results across runs.

# run_pseudobulk_de

Sums each sample's raw counts into one profile per sample (per cell type when grouped), then tests conditions across samples with DESeq2 (pydeseq2). This is the comparison that treats the animal or donor, not the cell, as the unit of replication.

## When the agent uses it

- For any condition contrast whose design has replicate samples per arm, instead of reading `run_de`'s cell-level p-values as inference.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `min_cells_per_sample` | integer | `10` | a sample contributing fewer cells than this to a cell type is dropped from that cell type's test |
| `min_samples_per_condition` | integer | `2` | refuse to test an arm backed by fewer samples than this — below it there is no replication and no valid p-value. 2 is the COMPUTABILITY floor, not a recommendation: the single-cell DE literature (Squair et al. 2021) asks for >=3 replicates per arm, and a 2-vs-2 result should be reported as underpowered |
| `min_count` | integer | `10` | a gene must reach this many summed counts in at least as many samples as the smaller arm, or it is not tested (edgeR filterByExpr's rule of thumb). Genes nobody detected cannot be tested — they only inflate the BH denominator |
| `padj` | number | `0.05` | adjusted-p (Benjamini-Hochberg) cutoff used to count significant genes |
| `sample_key` | string | — | obs column holding the sample / donor / library id |
| `condition_key` | string | — | obs column holding the experimental condition |
| `group_key` | string | — | cell-type column to test within, one test per cell type |
| `input` | string | `''` | an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. It must carry the raw counts in layers['counts'], as run_scanpy_qc's checkpoint does. |
<!-- /generated:parameters -->

## Outputs

- `tables/pseudobulk_all.csv` and `tables/pseudobulk_<group>.csv` (the first 2,000 genes of each group by p-value).
- The hand-off files `run_enrichment` and `run_gsea_prerank` read, under the tool's own key `pseudobulk_<group_key>` (`pseudobulk` without a group key), so they never write over a `run_de` result of the same column: `de_pseudobulk_<group_key>_all.csv`, one complete table per group (`de_pseudobulk_<group_key>_<group>.csv`, every tested gene), the tested universe, `rank_pseudobulk_<group_key>_<group>.rnk` and the slug index, and the two labels `run_de` also writes: `de_pseudobulk_<group_key>_inference.json` (`inference: replicated`) and `de_pseudobulk_<group_key>_significance.json` (complete per-group tables, significant counts per group).
- Result: per-group counts, the test used, `skipped_groups`, `warnings`, and `de_key`: the key `run_enrichment` and `run_gsea_prerank` take as `groupby`. With no `groupby` they prefer this replicated test over a cell-level `run_de` table.


## Known limits

- It refuses an arm with too few samples to support a test.
- Without pydeseq2 (provided on HPC3 through `AISCIENTIST_HPC_PYDEPS`) it falls back to Welch's t-test on log2 CPM and labels the result as a fallback.

# run_composition

Counts what fraction of each sample's cells belongs to each cell type and compares those proportions between conditions: whether a population expanded or shrank, as opposed to whether its genes changed.

## When the agent uses it

- In a condition study, usually as the first analysis after QC on labelled data.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `group_key` | string | — |  |
| `sample_key` | string | — |  |
| `condition_key` | string | — |  |
| `input` | string | `''` | an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. |
<!-- /generated:parameters -->

## Outputs

- `tables/composition.csv`, `tables/composition_by_sample.csv`, `tables/composition_by_condition.csv` and `tables/composition_test.csv`.


## Known limits

- Proportions are compositional: one population growing shrinks every other share, so a single shift can look like several.
- The test needs replicate samples per arm; with one sample per arm it can describe, not test.

# run_marker_annotation

Scores each cluster against a marker panel (`{cell type: [gene symbols]}`), sharpened by a few lineage-specific `discriminators`, and assigns the best-scoring cell type. A cluster without a clear winner stays `Unassigned` instead of being pushed into the nearest label.

**The markers come from a curated reference when one exists for the tissue** (`references/<tissue>.json`; today `retina`, human and mouse symbols). Pass `reference: "retina"` and no panel. With the default `reference: "auto"`, a model-written panel whose cell types match a reference (three or more) is replaced by the reference's definitions, and any gene the model put under one lineage that is another lineage's specific marker is dropped with a warning. `reference: "none"` does not opt out of this: run 4de7ab04a239 (2026-10-08) passed `none` with a panel whose names and genes were crossed ("Cone photoreceptor" = Rho/Nrl/Cnga1) and 8,132 rods came out cones; the reference is applied and the contradictions are listed in `reference.caller_panel_contradictions`. This exists because a model-written retina panel (2026-10-02) put OPN4/NEFM/MEF2C under cones, RLBP1 under bipolar cells and astrocyte markers under Muller glia: 1,631 Muller glia were labelled "Unassigned" or "ganglion" and the Critic accepted it.

Two checks run after the labels are assigned:

- **Canonical markers.** A label stands only if one of its lineage-specific markers is enriched in the cluster (mean log-normalised expression at least 0.5 above clusters given other labels, detected in at least 20% of the cluster's cells). Where the reference names `defining` markers for a lineage (retina: RLBP1 for Muller glia, PAX2 for astrocytes), one of those must be the enriched one: GLUL or GFAP alone is shared with other glia and ocular epithelia. Otherwise the label is withdrawn (`withdrawn_label`) and the cluster is `Unassigned`. Run f3b8268c4fd4 called a 27-cell Best2/Cldn2/Slc13a4 cluster Muller glia on GLUL alone.
- **Why a cluster is unassigned.** `unassigned_reason` is `mixed` (two lineages' markers both present: doublets, ambient RNA, or a type the panel lacks; the evidence names both), `weak` (no lineage's markers reach the minimum) or `withdrawn`. Every cluster carries `top_markers`, its own most distinguishing genes (run_de's marker test when the checkpoint has it), so an unassigned cluster is described by what it expresses.
- **Another lineage's genes on top.** When another lineage's marker outranks every marker of a cluster's own label among its top genes (run f3b8268c4fd4: a Rod photoreceptor cluster led by Nefm/Nefl), the cluster gets `top_markers_of_other_lineages` and a warning: possible doublets or a mixed cluster.
- **Plausibility.** A lineage every sample of the tissue contains (retina: Muller glia) that no cluster received is a warning, with how often its markers are detected; no photoreceptor, bipolar or amacrine cluster at all is another.

## When the agent uses it

- After `run_clustering` (and ideally `run_de`), to name the clusters. For retina, pass `reference: "retina"`.

## When the agent does NOT use it

- To discover cell types nobody expects: a reference only finds the lineages it lists. Report unassigned clusters as such.
- With a hand-written panel for a tissue that has a reference: the reference replaces it anyway.

## Inputs

<!-- generated:parameters -->
| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `reference` | string (`auto`, `none`, `retina`) | — | curated marker reference for the tissue; 'auto' (default) applies one when your panel's cell types match it. 'none' is for a tissue without one: a panel naming three or more of a reference's cell types uses the reference whatever is passed |
| `panel` | object | — |  |
| `discriminators` | object | — |  |
| `cluster_key` | string | — |  |
| `min_discriminator_mean` | number | — |  |
| `dominance_ratio` | number | — |  |
| `input` | string | `''` | an .h5ad to read INSTEAD of the checkpoint the previous tool wrote — e.g. one a run_code step saved in AISCIENTIST_WORK. A file name or a path inside this run's work/ or artifacts/ directory; nothing outside the run is read. Empty = the usual checkpoint. To feed a variant of an upstream result to the next tool, save it under a NEW name and pass it here — never overwrite a tool's checkpoint. |
<!-- /generated:parameters -->

## Outputs

- `work/adata_annotated.h5ad`.
- `tables/cluster_cell_types.csv` and `.json` (label, evidence, `unassigned_reason`, `top_markers`, `top_markers_of_other_lineages` per cluster), `tables/celltype_scores_by_cluster.csv`, `tables/celltype_composition.csv`.
- In the result: `unassigned_detail` (reason, evidence and top markers of each unassigned cluster).
- `figures/umap_cell_types.png` (when the checkpoint has a UMAP) and `figures/dotplot__cell_type_markers.png` (the deciding markers by assigned cell type). Written by the tool, so a plan needs no figure step for them.


## Known limits

- Only as good as the panel: a cell type that is missing from it cannot be found.
- Report `Unassigned` clusters as such; do not rename them by hand to complete the picture.

A checkpoint imported from `SCRNA_CHECKPOINT_ROOT` has a stored source identity. If a later
CellQC run changes that upstream checkpoint, this analysis refuses further work and asks for
a new `analysis_id`; it never combines old clustering with the replacement QC. Existing local
checkpoints remain untouched. An initial explicit `input="work/adata_qc.h5ad"` may refer to an
admitted handoff checkpoint before it is copied.

### Runtime revision 0.0.2

This revision preserves the scientific functions and tool schemas. It adds durable attempt
history, atomic metadata, independent concurrent polling and cache invalidation for complete
output inventories. Executor failures cannot be reported as successful scientific results;
preempted active jobs do not expose a stale prior payload. Runtime regressions cover these
behaviors, changed worker configuration and metadata/output symlink refusal.
