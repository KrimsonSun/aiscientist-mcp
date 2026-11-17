# variant-annotation

Independent MCP server for VCF consequence, gene, impact, population frequency and ClinVar
annotation. The offline calculation is ported from AiScientist's `annotate_variants` line:
bcftools filtering/normalization, Ensembl VEP, optional predictor/IRD annotations, summaries and
the complete standard deliverable tables. There is no dependency on AiScientist or HarnessTool.

## Tools

| Tool | Inputs | Result |
| --- | --- | --- |
| `annotate_variants` | Authorized VCF and analysis settings below | `analysis_id`, `task_id`, execution state; inline calls include derived results and table paths. |
| `variant_job_status` | `analysis_id`, `task_id` | Current state, bounded findings, full-result and artifact paths. |

| `annotate_variants` argument | Default | Meaning |
| --- | --- | --- |
| `analysis_id` | `default` | An isolated analysis under this server's data directory. |
| `vcf_path` | bound dataset | VCF file under `VARIANT_READ_ROOTS`, or the `VARIANT_DATASET`/`AISCIENTIST_DATASET` binding. |
| `assembly` | `GRCh38` | `GRCh38` or `GRCh37`; choose the assembly of the input and matching reference cache. |
| `species` | `human` | Human only for offline VEP; Ensembl species identifier for REST. |
| `mode` | `offline` | `offline` or explicitly enabled `rest`; there is no automatic REST fallback. |
| `pass_only` | `true` | Keep FILTER=PASS or `.` records before annotation. |
| `max_variants` | `0` | Offline: unlimited annotation rows; a positive cap limits rows parsed after VEP. REST: 0 selects its 500-variant default cap. |
| `max_pop_af` | `0` | Exclude annotated rows above this population AF; 0 keeps all. |
| `genes` | empty | Restrict annotated rows to these gene symbols; empty keeps all. |
| `regions_bed` | empty | Authorized BED for an offline region subset before VEP. |
| `sample` | empty | Offline VCF sample selection before VEP. |
| `rerun` | `false` | Force a new attempt after the current job finishes. |

## Environment and executor

```bash
pip install ./packages/common ./packages/variant-annotation
export VARIANT_DATASET=/path/to/sample.vcf.gz
export VARIANT_VEP_CACHE_DIR_GRCH38=/path/to/references/GRCh38
export VARIANT_VEP_CLINVAR_GRCH38=/path/to/references/clinvar_GRCh38.vcf.gz
export AISCI_DATA_DIR=/path/to/results
export AISCI_EXECUTOR=slurm
export AISCI_SLURM='--partition=<partition> --account=<account> --cpus-per-task=8 --mem=32G --time=08:00:00'
aiscientist-mcp --server variant-annotation-mcp call annotate_variants '{"analysis_id":"vcf-01","genes":["RPGR","USH2A"],"max_pop_af":0.01}'
aiscientist-mcp --server variant-annotation-mcp call variant_job_status '{"analysis_id":"vcf-01","task_id":"<returned-task-id>"}'
```

Register `variant-annotation-mcp` as a stdio command in other MCP clients. Slurm and local
executors return promptly; poll `variant_job_status`. In an outer Slurm allocation/container,
use `AISCI_EXECUTOR=inline`; it waits for the real worker. A `local` background worker needs its
enclosing host/container to remain alive. `AISCI_SLURM_FALLBACK` handles preemption, and
`AISCI_JOB_PREFIX` may enter an image; that image must contain the worker's Python executable
at the same path. Running the MCP server in the target image provides this matching runtime.

`containers/variant-annotation/Dockerfile` extends the same official
`ensemblorg/ensembl-vep:release_112.0` image used by the app's `deploy/vep/vep.def`, adds bcftools
and an isolated Python 3.12/MCP runtime, and preserves the image's Perl environment. Reference
files are mounted read-only; no cache or licensed reference data is baked into the image.
Build and publish the image, record its digest, and use the matching release-112 cache before
registering a release in the application. No production image digest is claimed by this source.

| Server setting | Meaning |
| --- | --- |
| `VARIANT_DATASET` | Default VCF; falls back to `AISCIENTIST_DATASET`. |
| `VARIANT_READ_ROOTS` | Approved caller-selected VCF/BED roots, separated by `:` on Linux. Symlink targets must remain within these roots. |
| `VARIANT_VEP_CACHE_DIR_GRCH38`, `_GRCH37` | Matching offline cache directories. `VARIANT_VEP_CACHE_DIR` is the fallback. |
| `VARIANT_VEP_CLINVAR_GRCH38`, `_GRCH37` | ClinVar VCF with its tabix index; generic `VARIANT_VEP_CLINVAR` is the fallback. |
| `VARIANT_REF_FASTA_GRCH38`, `_GRCH37` | Optional FASTA for bcftools normalization and HGVS; generic `VARIANT_REF_FASTA` is the fallback. |
| `VARIANT_VEP_FORK` | VEP process count; default Slurm CPUs if available, otherwise 8. |
| `VARIANT_VEP_PLUGINS` | Enable configured CADD/REVEL/AlphaMissense predictors. |
| `VARIANT_VEP_CADD_SNV`, `_CADD_INDELS`, `_ALPHAMISSENSE`, `_REVEL`, `_PLUGINS_DIR` | Optional predictor files and Perl plugin directory. Missing optional files are skipped as in the original pipeline. |
| `VARIANT_SPLICEAI`, `_SPLICEAI_BIN`, `_SPLICEAI_MODELS`, `_SPLICEAI_MAX_VARIANTS` | Optional OpenSpliceAI executable/models and post-filter workload cap; executable and models must be available in the worker image. |
| `VARIANT_IRD_ANNOTATE`, `_IRD_HGMD`, `_IRD_RETINA_EXONS`, `_IRD_ATAC`, `_IRD_DBSCSNV` | Optional IRD layers. dbscSNV accepts a server-configured `{chrom}` path template. Licensed data must be staged separately. |
| `VARIANT_ALLOW_REST` | Exactly `1` permits an explicit REST call. REST sends variant loci and alleles to Ensembl. |
| `AISCI_DATA_DIR`, `AISCI_EXECUTOR`, `AISCI_SLURM`, `AISCI_SLURM_FALLBACK`, `AISCI_JOB_PREFIX` | Shared runtime settings; default Slurm request is 8 CPUs/32 GB/8 hours. |

Reference/predictor environment settings also accept an `_GRCH38` or `_GRCH37` suffix, which
wins over their generic value. Model arguments never choose caches, plugins, reference paths,
executables, fork width or job settings. Reference configuration and existing file identity,
size/mtime/ctime are recorded in the request and invalidate reuse when changed; these metadata
are not content checksums. One analysis has one active writer; each attempt retains its own
output and evidence, so changing parameters cannot overwrite an earlier result.

## Results

Tasks live in `$AISCI_DATA_DIR/variant-annotation/<analysis_id>/jobs/<task_id>/` with request,
executor state, logs and complete `result.json`. Each attempt's `output/artifacts/` contains:

- `tables/variant_annotation.tsv`, the complete retained per-variant annotation table;
- five CSV deliverables: consequence, impact and significance distributions, ClinVar pathogenic
  findings, and rare unclassified high-priority candidates;
- `data/annotated_results_summary.json` for downstream consumers.

The native response preserves the original summaries and provenance and adds `artifact_root`
and `reference_settings`. Lists are previewed at 20 rows; `rows_omitted` reports omissions and
`full_result` points to the complete JSON. Large responses retain counts/paths with
`response_omitted=true`. Applications must explicitly collect authorized output paths.

Offline annotation does not send variants over the network. REST is intended for small VCFs,
reports whole-file PASS/non-PASS counts and flags its cap; the counts then describe the retained
slice, not the whole cohort. BED/sample selection is refused in REST mode rather than ignored.
Optional predictors and normalization are reported only when they ran. Offline parsing streams
JSONL, but retained annotation rows are held for summary/table generation; use gene/region/AF
restriction and appropriate job memory for large call sets. Source labels and prioritization
heuristics are annotations for review, not a clinical diagnosis.

## Validation and source

The calculation and regression fixtures come from
[RCHENLAB/AiScientist](https://github.com/RCHENLAB/AiScientist/tree/48f64a6be1b9f44eb2948eea1ac448df6a4219e2/src/aiscientist/tools/annotate_variants).
Tests retain VCF parsing, VEP/ClinVar field handling, predictor/IRD behavior, inheritance-model
ranking, filters, command construction and table expectations. Native tests use actual MCP
and the common worker subprocess with deterministic bcftools/VEP fixture executables. They
exercise offline selection, isolated outputs, input/reference-setting reuse, path confinement,
invalid input, concurrent submissions, REST opt-in and stdio calls without network.

The unit fixtures do not execute real VEP. Separate Slurm acceptance runs used the published
0.0.1 and 0.0.2 images directly, without a Python source overlay, and the site's read-only VEP 112/ClinVar
references. Two public example variants completed offline annotation; the original tool's
counts and all seven result artifacts matched. Native tools/list, invalid-input rejection,
status and unchanged-request reuse also passed. The 0.0.2 image verified common runtime 0.0.4.
This acceptance used plugins disabled. It does not cover REST, optional predictors or
patient VCFs. The final 0.0.2 native-image check ran in Slurm `59536747`.

## Normal website execution (2026-11-13)

Website run `25e53774b36d` invoked the final native 0.0.2 image through the reviewed typed
plan in Slurm `59540165`. Offline GRCh38 annotation of the two public example variants
completed. All seven downloaded scientific artifacts matched the original-tool baseline
byte-for-byte: the summary JSON, annotation TSV and five clinical/priority/distribution CSVs.
The ordinary website result ZIP was downloaded. Server reference settings remained
operator-controlled; no source overlay was used.

Released image: [`variant-annotation-v0.0.2`](https://github.com/liujs123456/aiscientist-mcp/releases/tag/variant-annotation-v0.0.2), OCI `sha256:92c875d268b8f89054860cedd13dcbd5b53498e9321868180e9e56a2b56eb388`.

The scientific-source and exported-artifact gate passed for this bounded VEP112/ClinVar
migration check with plugins disabled. It does not establish whole-genome, all-assembly,
optional-predictor, REST-path or clinical validation. Scientific-file/download parity does
not by itself certify every statement in a generated report.
