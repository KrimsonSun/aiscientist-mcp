"""Operator-controlled dataset and optional model settings."""

import os
from dataclasses import replace
from pathlib import Path

from aiscientist_mcp.common.model import ModelSettings
from aiscientist_mcp.common.tasks import TaskConfig, read_path

from . import SERVER_NAME, __version__


class UtilityConfig(TaskConfig):
    def settings(self):
        settings = super().settings()
        # These original in-process tools are short; no global environment mutation.
        return replace(settings, executor=os.environ.get("AISCI_EXECUTOR", "inline"))


CONFIG = UtilityConfig(
    SERVER_NAME, "aiscientist_mcp.data_utilities.worker", __version__, "--cpus-per-task=1 --mem=2G --time=00:10:00"
)


def admitted(path):
    default = os.environ.get("DATA_UTILITIES_DATASET") or os.environ.get("AISCIENTIST_DATASET", "")
    if not path and not default:
        raise ValueError("pass path inside DATA_UTILITIES_READ_ROOTS or configure DATA_UTILITIES_DATASET")
    roots = [Path(p) for p in os.environ.get("DATA_UTILITIES_READ_ROOTS", "").split(os.pathsep) if p]
    if default:
        roots.append(Path(default))
    return read_path(path or default, roots, directory=False)


def model_configuration():
    model = ModelSettings.from_env("DATA_UTILITIES")
    # Credentials stay in the environment or configured key file, never a saved request.
    return {
        "endpoint": model.endpoint,
        "model": model.model,
        "timeout": model.timeout,
        "protocol": getattr(model, "protocol", "openai"),
    }
