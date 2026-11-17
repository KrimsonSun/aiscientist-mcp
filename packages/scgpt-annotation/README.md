# scgpt-annotation

Reference-based per-cell cell-type annotation with scGPT, a gene-expression transformer.
The server uses the existing validated `scGPT_refactor/step1_preprocess.py` and
`step2_inference.py` unchanged. It does not substitute an LLM or invent an inference algorithm.

The wrapper, gene-symbol harmonization and prediction aggregation come from
`RCHENLAB/AiScientist` commit `48f64a6` (source hashes in `provenance.json`, MIT license retained).
The validated harness, trained model and its scientific Python environment are separately
provided assets. They are not embedded in this repository or downloaded at runtime.

## Tools

`scgpt_annotate(model_dir="", dataset="", analysis_id="default", rerun=False)` submits
annotation. `model_dir` retains the original tool parameter. Empty dataset/model parameters
use operator-configured defaults. Explicit paths must be inside configured read/model roots.

With Slurm or local execution, poll `scgpt_job_status(analysis_id, task_id)` until `COMPLETED`.
The returned `job_id` is the Slurm job identifier when applicable. With `inline`, annotation
runs inside the caller's existing GPU allocation and the call returns its completed result.
A failed inline inference is an MCP error, and polling retains the failed task's evidence.

Successful results retain `n_cells`, `n_cell_types`, `label_counts`, `predictions_csv`,
`model_dir`, and `species_harmonization`. Task identity is supplied by the shared runtime;
`artifact_root` identifies the deliverables for a client-owned adapter. Individual cell labels
and confidences live in `artifacts/data/scgpt_predictions.csv`, not in a large inline response.
The query and trained model are never modified. New AnnData checkpoints can encode indices or
metadata as nullable strings, which the older scientific environment reads but cannot rewrite.
The wrapper converts only that storage representation to object strings in its private copy.
`input_compatibility` lists affected fields and records that expression values were unchanged;
the original species-harmonization record remains separate.

The species step counts exact and uppercase gene matches. It changes names only when uppercase
matching gives at least five times the exact overlap and at least 1,000 extra matches. The
report explicitly states the name-based orthology assumption. The prior mouse-retina test
against a human-retina model showed stronger agreement for neurons than glial/vascular cells;
check those classes independently against markers, including high-confidence calls.

## Settings

| Variable | Meaning |
| --- | --- |
| `AISCI_DATA_DIR` | Outputs under `<base>/scgpt-annotation/<analysis_id>/`; default `./data`. |
| `SCGPT_DATASET` | Default admitted `.h5ad`; `AISCIENTIST_DATASET` is a compatibility alias. |
| `SCGPT_READ_ROOTS` | `os.pathsep`-separated roots for explicit query paths. |
| `SCGPT_MODEL_DIR` | Default approved reference directory; `AISCIENTIST_SCGPT_MODEL_DIR` is also accepted. |
| `SCGPT_MODEL_ROOTS` | Roots containing approved model directories for `model_dir` overrides. |
| `SCGPT_REFACTOR` | Trusted validated harness directory; default `/opt/scgpt/scGPT_refactor`. |
| `SCGPT_INFERENCE_PYTHON` | Scientific interpreter containing the original torch/scGPT stack. Default: the image's recorded interpreter, otherwise the current Python. |
| `SCGPT_REQUIRE_CUDA` | `1` (default) checks CUDA before preprocessing. `0` is an explicit operator setting for CPU diagnostics or synthetic harness tests, not evidence of a GPU run. |
| `AISCI_EXECUTOR` | Shared `slurm` (default), `local`, or `inline` executor. |
| `AISCI_SLURM` | Defaults to `--partition=gpu --gres=gpu:1 --cpus-per-task=4 --mem=32G --time=01:00:00`; set the site's authorized account/partition. |
| `AISCI_SLURM_FALLBACK` | Shared preemption fallback settings. |
| `AISCI_JOB_PREFIX` | Trusted launcher, e.g. an Apptainer command with `--nv` and read-only input/model mounts. |

The model must contain `best_model.pt`, `vocab.json`, `id2type.json`, and `dev_train_args.yml`.
The harness must contain both validated step scripts and their helpers. Input/model/harness
file identities are checked again in the worker, and changed configuration does not reuse a
completed task. No model argument selects a shell command or interpreter.

## Environment and image

The scientific environment and the MCP protocol runtime are separate. The prior scientific
image may use Python 3.10 with torch 2.3.0/CUDA 12.1, while MCP requires Python >=3.11.
The Dockerfile starts from the original official PyTorch 2.3.0/CUDA 12.1 image, pinned to
its verified digest, and installs scientific versions recorded from the deployed environment.
The constraints retain torch 2.3.0, scGPT 0.2.4, scanpy 1.11.5, anndata 0.11.4 and NumPy 1.26.4.
It adds a separate MCP environment and records the original scientific interpreter for
`SCGPT_INFERENCE_PYTHON`. The external validated harness and trained model remain required.

```bash
docker build --platform linux/amd64 \
  -f containers/scgpt-annotation/Dockerfile -t mcp-scgpt-annotation:0.0.3 .
```

`SCGPT_BASE_IMAGE` can override the recorded official base with an approved OCI reference.
Mount the reviewed harness read-only and set `SCGPT_REFACTOR`. Bind the model and query
read-only and the analysis root writable. A `.sif` is not a Docker `FROM` reference.
The MCP package can also run outside the image with the common Slurm executor and a configured
worker launcher. A release requires its actual built image digest and GPU regression evidence.

## Verification

`uv run pytest packages/scgpt-annotation` checks the real MCP schema/stdio/inline/local paths,
Slurm GPU request, input/model admission, original species-harmonization rule and label summary.
Its small harness is explicitly synthetic and only verifies orchestration. It does not load
trained weights or claim GPU/model output parity.

For real parity, use the same approved query, model and validated harness in the old deployment
and this worker. Compare barcode-aligned predictions and confidence values, label counts and
the full species-harmonization record; retain the model/harness versions, GPU and image identity,
Slurm job ID/state and delivered artifacts. No old application tool is retired based on the
synthetic tests alone.

Version 0.0.2 supplies a writable Numba cache for read-only container launches with an
unmounted home. The worker uses its analysis workspace when no operator cache is configured;
the image also supplies `/tmp/numba` for direct scientific-interpreter use. Inference code,
model parameters and the scientific dependency pins are unchanged.

The 480-cell HECA regression preserves original predictions and confidences exactly when
the old and new images use the same GPU model: verified on both V100 and A30. Across those
GPU models, labels remained identical but 35 confidence values differed by up to 0.0026;
the preprocessed matrices were identical. Preserve the GPU model when comparing numerical
confidence baselines. This observation does not change inference settings or relax the
old-versus-new comparison tolerance (`1e-6`).

The released `scgpt-annotation-v0.0.3` image with common 0.0.4 passed native HPC3 GPU
acceptance in Slurm job `59536819` (V100, 1m41s). The real model and read-only reviewed harness
processed the original public 480-cell, 36,601-gene HECA query. All 480 labels and confidences
matched both the frozen original predictions and a fresh unchanged original wrapper in the
same image, with maximum confidence difference 0.0. This used the installed MCP package,
no source overlay and no operator Numba-cache override; the input-index compatibility path
was exercised without changing expression values. The native call took 42.5s.

## Normal website execution (2026-11-13)

The normal website run `0fb4aa8eb425` executed the final 0.0.3 image on an A30 in Slurm
`59540287`. The downloaded canonical and namespaced prediction CSVs were identical and
matched all 480 labels and confidence values from the original production image on the
same GPU model, with maximum absolute confidence difference 0.0 (unchanged tolerance `1e-6`).
The retained reference came from a successful original inference phase in a diagnostic job
whose later diagnostic phase failed; that job is not represented as wholly successful.

Released image: [`scgpt-annotation-v0.0.3`](https://github.com/liujs123456/aiscientist-mcp/releases/tag/scgpt-annotation-v0.0.3), OCI `sha256:1133d106342831e9d6cd7fc5961e38eb2043142852219105bac269065213b2d5`.

The scientific-source and exported-artifact gate passed for the recorded model, harness,
public subset and A30 GPU. These checks do not establish prediction accuracy, cross-GPU
numerical identity or a final model-written report audit. Earlier cross-GPU confidence
differences remain recorded above; no tolerance was widened to make those controls pass.
