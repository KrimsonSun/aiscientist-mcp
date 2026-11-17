# run_lirical

Rank candidate diseases with LIRICAL 2.4.1 from observed `hpo_terms` and optional `excluded_hpo`. Obtain IDs from `map_phenotype_to_hpo`; each is validated against the real ontology before execution. Unknown IDs are dropped and reported; obsolete IDs are forwarded to their replacements. An unreadable configured ontology fails the job.

Optional `vcf_path` and `sample_id` enable genotype-aware scoring when the server has the assembly-matched Exomiser database. Otherwise the result is phenotype-only. Reference locations, assembly and executable are administrator settings (`LIRICAL_DATA_DIR`, `LIRICAL_EXOMISER_HG19/HG38`, `LIRICAL_ASSEMBLY`, `LIRICAL_COMMAND`); VCF/reference reads must fall within `PHENOTYPE_ALLOWED_ROOTS`.

Returns ranked candidates, original post-test probabilities, likelihood ratios, mode, grounded HPO terms, provenance TSV path and task identity/state. Every attempt uses a new TSV prefix. `analysis_id` defaults to `default`; `rerun=true` explicitly recomputes. Missing configuration returns diagnostic `not_installed` in a failed job; an action call surfaces an MCP error with task ID. No probability or disease is fabricated. For literature adjudication and uncurated candidate genes use `diagnose_disease`.
