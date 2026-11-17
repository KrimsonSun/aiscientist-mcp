# atlas-expression-exploration

Where is a gene expressed across the annotated cell types of a single-cell atlas?

1. Resolve the gene names.
2. Stream the full atlas raw counts in row chunks, retaining only query genes; full-transcriptome library totals still determine CP10k.
3. Keep the query genes and write `query.h5ad` plus donor-aware summary tables at major-class,
   cell-type and region-by-class resolution.
4. Make scanpy dot, violin and UMAP feature plots and a Markdown report.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `resolve_genes` | `genes`, `atlas` | `resolution` (exact, case-insensitive, HGNC approved/previous/alias/ID) |
| `submit_query` | `genes`, `atlas`, `rerun` | `query_id`, `state`, `job_id`, `resolution`; returns at once |
| `query_status` | `query_id` | `state`, `job_id`, `note`, `elapsed_s` |
| `get_summary` | `query_id`, `level` (`majorclass`, `celltype`, `region_class`; default `majorclass`), `gene`, `top` | `rows` ranked by pseudobulk CP10k, `table` |
| `plot_dotplot` / `plot_violin` / `plot_featureplot` | `query_id` (`groupby` for violin) | `files` (PDF + PNG) |
| `write_report` | `query_id` | `file`, `markdown` |
| `list_queries` | none | `queries` |

| Variable | Meaning | Default |
| --- | --- | --- |
| `ATLAS_H5AD` | default atlas | required |
| `ATLAS_HGNC` | HGNC complete set (downloaded if missing) | `$AISCI_DATA_DIR/atlas-expression-exploration/hgnc_complete_set.txt` |
| `ATLAS_OBS_COLS` | class, cell type, region, donor[, extra] | `majorclass,celltype,tissue,donor,sampleid` |
| `ATLAS_CLASS_ORDER` | display order of classes | retina layer order |
| `ATLAS_TARGET_SUM`, `ATLAS_MIN_CELLS_DONOR` | normalization target; donor x group minimum | `10000`, `10` |
| `AISCI_SLURM` | sbatch options for `submit_query` | `--cpus-per-task=4 --mem=300G --time=04:00:00` |

Tested on the HRCA snRNA-seq atlas: 3,177,310 nuclei x 36,601 genes. The full-atlas step took
11 min with a 218 GiB peak RSS.
