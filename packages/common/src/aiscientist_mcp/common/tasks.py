"""Confined analysis workspaces and durable jobs for computational MCP servers.

Configuration and inputs participate in request identity; credentials must stay in
the worker environment. Each workspace has one writer, while each attempt retains
its own request, result and executor evidence.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from . import jobs
from .paths import safe_id

ACTIVE = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED", "UNKNOWN", "REQUEUED"}


@dataclass(frozen=True)
class TaskConfig:
    server: str
    worker: str
    version: str
    slurm: str = "--cpus-per-task=4 --mem=16G --time=04:00:00"

    def settings(self):
        return jobs.JobSettings.from_env(self.slurm)


def confined_child(root: Path, name: str) -> Path:
    safe_id(name)
    path = root / name
    if name == "." or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("path escapes the configured workspace")
    return path


def workspace(cfg: TaskConfig, analysis_id: str) -> Path:
    root = Path(os.environ.get("AISCI_DATA_DIR", "./data")).expanduser().resolve()
    server = confined_child(root, cfg.server)
    path = confined_child(server, analysis_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_path(value: str, roots: list[Path], *, directory: bool | None = None) -> Path:
    path = Path(value).expanduser().resolve(strict=True)
    roots = [root.expanduser().resolve() for root in roots]
    if not any(path == root or path.is_relative_to(root) for root in roots):
        raise ValueError("input is outside the configured read roots")
    if directory is True and not path.is_dir():
        raise ValueError("input must be a directory")
    if directory is False and not path.is_file():
        raise ValueError("input must be a file")
    return path


def input_stat(paths: list[str]) -> list[dict]:
    """Metadata identity, not a content checksum; avoids reading whole large datasets."""
    result = []
    for value in paths:
        path = Path(value).resolve(strict=True)
        st = path.stat()
        result.append(
            {
                "path": str(path),
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "ctime_ns": st.st_ctime_ns,
                "inode": st.st_ino,
            }
        )
    return result


@contextlib.contextmanager
def lock(path: Path):
    if path.is_symlink():
        raise ValueError("workspace lock cannot be a symlink")
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("analysis workspace already has an active operation") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def task_dir(ws: Path, task_id: str) -> Path:
    return confined_child(confined_child(ws, "jobs"), task_id)


def status(cfg: TaskConfig, analysis_id: str, task_id: str) -> dict:
    directory = task_dir(workspace(cfg, analysis_id), task_id)
    state_file = jobs.managed_file(directory, "state.json")
    if not state_file.is_file():
        raise ValueError("unknown task_id")
    state = jobs.status(cfg.settings(), directory)
    current = state.get("state") or "UNKNOWN"
    output = {}
    result_file = jobs.managed_file(directory, "result.json")
    # A preempted allocation can leave its payload beside the new active state.
    # Scientific output belongs to a terminal attempt, never to its pending retry.
    if result_file.is_file() and current not in ACTIVE:
        output = json.loads(result_file.read_text())
    output |= {key: state[key] for key in ("job_id", "elapsed_s", "note", "error") if key in state}
    output |= {"state": current, "analysis_id": analysis_id, "task_id": task_id}
    if current == "COMPLETED" and not result_file.is_file():
        output.update(state="FAILED", status="error", error="completed task result is missing; submit a new task")
    if current not in ACTIVE | {"COMPLETED"} and output.get("status") in {None, "ok", "empty"}:
        output["status"] = "error"
    return output


def submit(
    cfg: TaskConfig,
    analysis_id: str,
    operation: str,
    arguments: dict,
    *,
    inputs: list[str] = (),
    settings: dict | None = None,
    rerun: bool = False,
) -> dict:
    ws = workspace(cfg, analysis_id)
    request = {
        "operation": safe_id(operation),
        "arguments": arguments,
        "inputs": input_stat(inputs),
        "settings": settings or {},
        "version": cfg.version,
        "analysis_id": analysis_id,
    }
    signature = hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest()[:24]
    with lock(ws / ".submit.lock"):
        active = jobs.managed_file(ws, "active.json")
        if active.exists():
            previous = json.loads(active.read_text())
            found = status(cfg, analysis_id, previous["task_id"])
            if previous["signature"] == signature and not rerun and found.get("state") in ACTIVE | {"COMPLETED"}:
                return found
            if found.get("state") in ACTIVE:
                raise ValueError("another task is active in this analysis; wait for it to finish")
        task_id = operation[:32] + "-" + signature + "-" + uuid4().hex[:8]
        directory = task_dir(ws, task_id)
        directory.mkdir(parents=True, exist_ok=False)
        jobs.write_json(directory, "request.json", request)
        jobs.write_json(ws, "active.json", {"task_id": task_id, "signature": signature})
        try:
            executor = cfg.settings()
            # A host venv's absolute interpreter is generally absent inside an image.
            # The operator can choose the image's executable; tool arguments cannot.
            python = os.environ.get("AISCI_JOB_PYTHON") or ("python" if executor.prefix else sys.executable)
            jobs.submit(
                executor,
                [python, "-m", cfg.worker, str(directory)],
                directory,
                safe_id(f"{cfg.server}-{analysis_id}"[:60]),
                extra={"task_id": task_id},
            )
        except Exception:
            jobs.write_state(directory, {"state": "FAILED", "error": "job submission failed"})
            raise
        return status(cfg, analysis_id, task_id)


def worker_main(cfg: TaskConfig, execute):
    directory = Path(sys.argv[1]).resolve(strict=True)
    request = json.loads(jobs.managed_file(directory, "request.json").read_text())
    ws = workspace(cfg, request["analysis_id"])
    if directory != task_dir(ws, directory.name).resolve():
        raise ValueError("worker request is outside its configured workspace")

    def run():
        with lock(ws / ".work.lock"):
            if input_stat([p["path"] for p in request["inputs"]]) != request["inputs"]:
                raise ValueError("input changed after submission; submit a new task")
            output = execute(request, ws)
            if not isinstance(output, dict):
                raise TypeError("analysis must return a structured object")
            jobs.write_json(directory, "result.json", output)
            if output.get("status") not in {"ok", "empty"}:
                raise RuntimeError(output.get("error") or output.get("note") or "analysis did not complete")

    jobs.record_done(directory, run)
