"""Operator-selected input roots, model and validated inference interpreter."""

import os
import shutil
import sys
from pathlib import Path

from aiscientist_mcp.common.tasks import TaskConfig, input_stat, read_path

from . import SERVER_NAME, __version__

CONFIG = TaskConfig(
    SERVER_NAME,
    "aiscientist_mcp.scgpt_annotation.worker",
    __version__,
    "--partition=gpu --gres=gpu:1 --cpus-per-task=4 --mem=32G --time=01:00:00",
)
MODEL_FILES = ("best_model.pt", "vocab.json", "id2type.json", "dev_train_args.yml")


def _roots(key, default):
    values = [Path(p) for p in os.environ.get(key, "").split(os.pathsep) if p]
    if default:
        values.append(Path(default))
    return values


def admitted(dataset, model_dir):
    default_data = os.environ.get("SCGPT_DATASET") or os.environ.get("AISCIENTIST_DATASET", "")
    default_model = os.environ.get("SCGPT_MODEL_DIR") or os.environ.get("AISCIENTIST_SCGPT_MODEL_DIR", "")
    if not dataset and not default_data:
        raise ValueError("configure SCGPT_DATASET or pass a dataset inside SCGPT_READ_ROOTS")
    if not model_dir and not default_model:
        raise ValueError("configure SCGPT_MODEL_DIR or pass an approved model inside SCGPT_MODEL_ROOTS")
    source = read_path(dataset or default_data, _roots("SCGPT_READ_ROOTS", default_data), directory=False)
    if source.suffix.lower() != ".h5ad":
        raise ValueError("scGPT requires an AnnData .h5ad query dataset")
    model = read_path(model_dir or default_model, _roots("SCGPT_MODEL_ROOTS", default_model), directory=True)
    files = [str(source)]
    for name in MODEL_FILES:
        files.append(str(read_path(str(model / name), [model], directory=False)))
    return source, model, files


def inference_settings():
    source = os.environ.get("SCGPT_REFACTOR") or os.environ.get(
        "AISCIENTIST_SCGPT_REFACTOR", "/opt/scgpt/scGPT_refactor"
    )
    harness = Path(source).expanduser().resolve(strict=True)
    if not harness.is_dir():
        raise ValueError("SCGPT_REFACTOR must point to the validated scGPT_refactor directory")
    for filename in ("step1_preprocess.py", "step2_inference.py"):
        read_path(str(harness / filename), [harness], directory=False)
    marker = Path("/opt/scgpt-scientific-python")
    default_python = marker.read_text().strip() if marker.is_file() else sys.executable
    interpreter = os.environ.get("SCGPT_INFERENCE_PYTHON") or default_python
    python = shutil.which(interpreter)
    if not python:
        raise ValueError("SCGPT_INFERENCE_PYTHON is not an executable interpreter")
    require_cuda = os.environ.get("SCGPT_REQUIRE_CUDA", "1")
    if require_cuda not in {"0", "1"}:
        raise ValueError("SCGPT_REQUIRE_CUDA must be 0 or 1")
    inputs = sorted(str(path) for path in harness.rglob("*") if path.is_file())
    # Ensure any helper symlink also remains within the trusted harness directory.
    for path in inputs:
        read_path(path, [harness], directory=False)
    return {
        "harness": str(harness),
        "python": str(Path(python).absolute()),
        "python_identity": input_stat([python]),
        "require_cuda": require_cuda == "1",
        "harness_identity": input_stat(inputs),
    }, inputs
