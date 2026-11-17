"""Variant job configuration and bounded tool responses."""

import json

from aiscientist_mcp.common import tasks

from . import __version__

CONFIG = tasks.TaskConfig(
    "variant-annotation",
    "aiscientist_mcp.variant_annotation.worker",
    __version__,
    "--cpus-per-task=8 --mem=32G --time=08:00:00",
)


def present(output: dict) -> dict:
    output = dict(output)
    full = tasks.task_dir(tasks.workspace(CONFIG, output["analysis_id"]), output["task_id"]) / "result.json"
    if full.is_file():
        output["full_result"] = str(full)
    omitted = {}
    for key, value in output.items():
        if isinstance(value, list) and len(value) > 20:
            omitted[key] = len(value) - 20
            output[key] = value[:20]
    if omitted:
        output["rows_omitted"] = omitted
    if len(json.dumps(output)) > 16000:
        output = {
            key: output[key]
            for key in (
                "analysis_id",
                "task_id",
                "state",
                "status",
                "job_id",
                "error",
                "full_result",
                "artifact_root",
                "annotated_table",
                "n_variants",
                "n_pathogenic",
                "execution_mode",
            )
            if key in output
        } | {"response_omitted": True}
    return output
