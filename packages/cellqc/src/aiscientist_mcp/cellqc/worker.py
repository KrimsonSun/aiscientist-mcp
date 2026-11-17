"""Execute one persisted CellQC request inside its selected executor."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from aiscientist_mcp.common import jobs

from . import __version__
from .runtime import input_identity, lock, task_dir, workspace
from .settings import defaults_source


def checked_directory(directory: Path) -> Path:
    directory = directory.absolute()
    analysis = workspace(directory.parent.parent.name)
    if directory != task_dir(analysis, directory.name) or not directory.is_dir():
        raise ValueError("worker request is outside its configured workspace")
    return directory


def checked_output(output: Path, folders: list[dict]) -> None:
    """Reject redirected writes while allowing links the real pipeline creates on a retry."""
    if output.is_symlink():
        raise ValueError("CellQC output cannot be a symlink")
    roots = [Path(folder["path"]).resolve() for folder in folders]
    for path in output.rglob("*"):
        if not path.is_symlink():
            continue
        parts = path.relative_to(output).parts
        resolved = path.resolve(strict=True)
        # _stage creates exactly one level of read-only delivery links per library.
        staged = parts[:3] == ("work", "cellqc", "lnfiles") and len(parts) == 5
        if staged and any(resolved == root or resolved.is_relative_to(root) for root in roots):
            continue
        # Fontconfig creates version aliases beside its regular cache file. Keep these
        # on retry, but never permit a cache alias to redirect writes outside the task.
        cache_alias = len(parts) >= 3 and parts[-2] in {"fontconfig", ".fontconfig"}
        if cache_alias and resolved.is_file() and resolved.parent == path.parent and resolved.is_relative_to(output):
            continue
        raise ValueError("CellQC output contains a symlink outside its permitted input/cache links")


def execute(directory: Path):
    from .core import run_cellqc

    directory = checked_directory(directory)
    request = json.loads(jobs.managed_file(directory, "request.json").read_text())
    analysis = directory.parent.parent
    if request["analysis_id"] != analysis.name:
        raise ValueError("worker request does not belong to this analysis")
    if request.get("version") != __version__ or request["settings"] != defaults_source():
        raise ValueError("CellQC package/defaults changed after submission; submit a new task")
    ws = directory / "output"
    with lock(analysis / ".work.lock"):
        jobs.managed_file(directory, "result.json")
        checked_output(ws, request["folders"])
        if input_identity(request["folders"]) != request["inputs"]:
            raise ValueError("input delivery changed after submission; submit a new task")
        ctx = SimpleNamespace(workspace=ws, decisions={"input_folders": request["folders"]})
        output = run_cellqc(request["arguments"], ctx)
        output["settings_source"] = request["settings"]
        if output.get("status") == "ok":
            output["checkpoint_path"] = str(ws / "work" / output["checkpoint"])
            output["artifact_root"] = str(ws / "artifacts")
            output["result_directory"] = str(ws / "result/cellqc")
        jobs.write_json(directory, "result.json", output)
        if output.get("status") != "ok":
            raise RuntimeError(output.get("error") or output.get("note") or "CellQC did not complete")


def main():
    directory = checked_directory(Path(sys.argv[1]))
    jobs.record_done(directory, lambda: execute(directory))


if __name__ == "__main__":
    main()
