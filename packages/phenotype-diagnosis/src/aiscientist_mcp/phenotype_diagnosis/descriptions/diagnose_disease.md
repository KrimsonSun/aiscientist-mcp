# diagnose_disease

Combine LIRICAL ranking with retrieved literature evidence into one differential. Accepts observed `hpo_terms`, optional `excluded_hpo`, `candidate_genes`, `max_literature_queries=6`, `sample_id`, `vcf_path`, `analysis_id="default"`, and `rerun=false`.

The literature track uses the explicitly configured `PHENOTYPE_LITERATURE_COMMAND` over MCP, calls `deep_literature`, and polls `literature_job_status`. It owns a separate literature analysis ID per case/question and never reads that service's workspace. Large evidence is recovered through bounded MCP resource pages with task, offset, size and SHA-256 validation before grading; incomplete evidence stays ungraded. With no literature command, the ranking remains LIRICAL-only. The original evidence adapter reports missing/unavailable evidence as ungraded, distinguishes unsupported from contradicted associations, and retains source passages/citations. Passage/source counts cap positive grades.

On disagreement, original adjudication may reorder candidates and marks conflicts. `final_score` is a ranking score, not a probability; `posttest_prob` remains LIRICAL's untouched calibrated result, and is null for literature-only rescued candidates. Return citations from the evidence records. Jobs use the same workspace/confinement/executor contract as `run_lirical`; inspect `phenotype_job_status` until terminal.
