"""Original harmonization and validated step1/step2 inference inside a GPU allocation."""

import csv
import json
import os
import shutil
import subprocess
from pathlib import Path

from aiscientist_mcp.common.tasks import confined_child, worker_main

from .settings import CONFIG, admitted, inference_settings


def label_counts(csv_path):
    """The original gateway aggregation; accepts predictions or prediction columns."""
    n = 0
    counts = {}
    with Path(csv_path).open(newline="") as fh:
        reader = csv.DictReader(fh)
        if not {"predictions", "prediction"} & set(reader.fieldnames or []):
            raise ValueError("predictions.csv has no prediction column")
        col = "predictions" if "predictions" in (reader.fieldnames or []) else "prediction"
        for row in reader:
            n += 1
            label = (row.get(col) or "").strip() or "unknown"
            counts[label] = counts.get(label, 0) + 1
    if not n:
        raise ValueError("predictions.csv contains no cells")
    return n, dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def execute(request, ws):
    args, settings = request["arguments"], request["settings"]
    source, model, _ = admitted(args["dataset"], args["model_dir"])
    current, _ = inference_settings()
    if settings != current:
        raise ValueError("inference settings changed after submission; submit a new task")
    work = confined_child(ws, "work")
    work.mkdir(exist_ok=True)
    out = confined_child(work, "scgpt")
    out.mkdir(exist_ok=True)
    # Scanpy imports cached Numba functions before inference. A read-only SIF and
    # unmounted home provide no writable default cache, even when CUDA is ready.
    cache = confined_child(work, "numba-cache")
    cache.mkdir(exist_ok=True)
    artifacts = confined_child(ws, "artifacts")
    artifacts.mkdir(exist_ok=True)
    data = confined_child(artifacts, "data")
    data.mkdir(exist_ok=True)
    python = settings["python"]
    env = os.environ | {
        "SCGPT_REFACTOR": settings["harness"],
        "WANDB_MODE": "offline",
        "WANDB_SILENT": "true",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "MPLBACKEND": "Agg",
    }
    env.setdefault("NUMBA_CACHE_DIR", str(cache))
    if settings["require_cuda"]:
        subprocess.run(
            [python, "-c", "import torch; assert torch.cuda.is_available(), 'scGPT requires a CUDA GPU allocation'"],
            env=env,
            check=True,
        )
    package = Path(__file__).resolve().parent
    harmonized = confined_child(out, "query_harmonized.h5ad")
    report = confined_child(out, "species_harmonization.json")
    harmonized.unlink(missing_ok=True)
    subprocess.run(
        [python, str(package / "harmonize.py"), str(source), str(model / "vocab.json"), str(harmonized), str(report)],
        env=env,
        check=True,
    )
    actual = harmonized if harmonized.exists() else source
    predictions = confined_child(out, "predictions.csv")
    predictions.unlink(missing_ok=True)
    subprocess.run(
        [python, str(package / "infer.py"), "--input", str(actual), "--model", str(model), "--out", str(out)],
        env=env,
        check=True,
    )
    if not predictions.is_file() or predictions.is_symlink():
        raise ValueError("scGPT inference produced no regular predictions.csv")
    n_cells, counts = label_counts(predictions)
    destination = confined_child(data, "scgpt_predictions.csv")
    shutil.copyfile(predictions, destination)
    harmonization = json.loads(report.read_text())
    shutil.copyfile(report, confined_child(data, "species_harmonization.json"))
    compatibility = confined_child(out, "input_compatibility.json")
    input_compatibility = json.loads(compatibility.read_text())
    shutil.copyfile(compatibility, confined_child(data, "input_compatibility.json"))
    return {
        "status": "ok",
        "tool": "scgpt_annotate",
        "n_cells": n_cells,
        "n_cell_types": len(counts),
        "label_counts": counts,
        "predictions_csv": str(destination),
        "model_dir": str(model),
        "species_harmonization": harmonization,
        "input_compatibility": input_compatibility,
        "artifact_root": str(artifacts),
    }


def main():
    worker_main(CONFIG, execute)


if __name__ == "__main__":
    main()
