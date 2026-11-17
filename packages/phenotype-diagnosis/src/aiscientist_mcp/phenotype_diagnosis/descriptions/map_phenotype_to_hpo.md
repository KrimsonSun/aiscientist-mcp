# map_phenotype_to_hpo

Map the patient's verbatim clinical `text` to the packaged HPO closed set. A configured model extracts observed/absent findings and selects from ontology candidates; it cannot invent HPO IDs. Each output term includes its source phrase, label, method and ontology version. Forward `hpo_terms` and `excluded_hpo` to `run_lirical`/`diagnose_disease`; report unmapped findings without substituting IDs.

`text` is required. Optional `k=8` controls candidate retrieval; `analysis_id="default"` and `rerun=false` control durable jobs. The service has no app case-note context. An app caller should inject its authoritative case note into `text` before calling this server.

Set `PHENOTYPE_MODEL_ENDPOINT` and `PHENOTYPE_MODEL` explicitly for model mapping, with `PHENOTYPE_MODEL_API_KEY_FILE` when needed. With no model the original conservative lexical path remains available; negation/family-history text yields `mode="needs_llm"` and no asserted terms. A configured but unreachable model degrades according to the original mapper, recording its fallback mode.

Runs through the common executor and returns original mapping fields plus durable task identity/state; poll `phenotype_job_status` when asynchronous. Output stays in the configured phenotype analysis workspace.
