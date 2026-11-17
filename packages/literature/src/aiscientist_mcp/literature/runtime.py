"""One corpus analysis per workspace, with resumable common-runtime jobs."""

import contextlib
import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from aiscientist_mcp.common import jobs
from aiscientist_mcp.common.paths import child_dir, safe_id
from aiscientist_mcp.common.response import bounded_result

from . import SERVER_NAME, __version__
from .settings import ModelSettings, Settings, checked_input

TOOLS = {"literature_search", "search_corpus", "deep_literature", "extract_haploinsufficient_genes"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "BOOT_FAIL", "DEADLINE"}


def write_json(path, value):
    jobs.write_json(path.parent, path.name, value)


def workspace(analysis_id):
    configured = Path(os.environ.get("AISCI_DATA_DIR", "./aiscientist-mcp-data")).expanduser()
    if configured.is_symlink():
        raise ValueError("literature data root cannot be a symlink")
    root = configured.resolve() / SERVER_NAME
    if root.is_symlink():
        raise ValueError("literature data root cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    ws = child_dir(root, analysis_id, "analysis_id")
    if ws.is_symlink() or ws.resolve().parent != root:
        raise ValueError("analysis workspace escapes the data root")
    ws.mkdir(exist_ok=True)
    for path in ws.rglob("*"):
        if path.is_symlink():
            # PaperQA reads an admitted corpus index link and writes answers beside it.
            cfg = Settings.from_env()
            name = os.environ.get("LITERATURE_INDEX_NAME", "retigene_full_pubmedbert")
            expected = ws / "pqa_index" / name
            if path != expected or cfg.index_dir is None or path.resolve() != checked_input(cfg.index_dir / name):
                raise ValueError("unexpected symlink in the analysis workspace")
    for name in ("work", "artifacts", "jobs"):
        (ws / name).mkdir(exist_ok=True)
    return ws


@contextlib.contextmanager
def lock(path):
    with jobs.managed_file(path.parent, path.name).open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("analysis is busy; poll literature_job_status") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _identity(path):
    if path is None:
        return None
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def fingerprint(tool):
    if tool == "literature_search":
        return {
            "search_url": os.environ.get(
                "LITERATURE_SEARCH_URL", "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            )
        }
    cfg = Settings.from_env()
    files = [cfg.manifest, cfg.hgnc]
    if cfg.index_dir:
        name = os.environ.get("LITERATURE_INDEX_NAME", "retigene_full_pubmedbert")
        safe_id(name, "index name")
        for relative in ("files.zip", "index/meta.json", "index/.managed.json"):
            path = cfg.index_dir / name / relative
            if path.exists():
                files.append(checked_input(path))
    if cfg.cache:
        files += [
            path
            for name in ("chunks.jsonl", "sentences.jsonl", "chunk_vecs.npy")
            if (path := checked_input(cfg.cache) / name).exists()
        ]
    if cfg.papers and not any(path and path.name == "chunks.jsonl" for path in files):
        files += [checked_input(path) for path in sorted(cfg.papers.rglob("*.pdf"))]
    result = {
        "files": [_identity(checked_input(path)) if path else None for path in files],
        "papers": str(cfg.papers),
        "index": str(cfg.index_dir),
        "embedding": os.environ.get("LITERATURE_EMBEDDING", "st-NeuML/pubmedbert-base-embeddings"),
    }
    if tool == "deep_literature":
        result["paperqa_settings"] = {
            name: os.environ.get("LITERATURE_" + name, "")
            for name in (
                "EVIDENCE_K",
                "MAX_SOURCES",
                "SEARCH_COUNT",
                "CONCURRENCY",
                "ANSWER_LENGTH",
                "REASONING_EFFORT",
                "SUMMARY_REASONING_EFFORT",
                "TEMPERATURE",
                "LLM_TIMEOUT",
                "AGENT_TIMEOUT",
                "AGENT_TYPE",
            )
        }
    if tool in {"deep_literature", "extract_haploinsufficient_genes"}:
        model = ModelSettings.from_env()
        from .settings import model_location

        result["model"] = {
            **model_location(model),
            "name": model.model,
            "max_tokens": os.environ.get("LITERATURE_MODEL_MAX_TOKENS", ""),
            "usage_protocol": os.environ.get("LITERATURE_MODEL_USAGE_PROTOCOL", ""),
        }
    return result


def task_dir(ws, task_id):
    path = child_dir(ws / "jobs", task_id, "task_id", must_exist="request.json")
    if path.is_symlink() or path.resolve().parent != ws / "jobs":
        raise ValueError("task path escapes the analysis")
    return path


def response(ws, analysis_id, task_id):
    task = task_dir(ws, task_id)
    state = jobs.status(jobs.JobSettings.from_env(), task)
    result_file = jobs.managed_file(task, "result.json")
    result = json.loads(result_file.read_text()) if result_file.is_file() else {}
    if state.get("state") == "COMPLETED" and (not result or result.get("status") != "ok"):
        state = state | {"state": "FAILED", "error": "job has no successful result"}
    output = (
        result
        | {
            "analysis_id": analysis_id,
            "task_id": task_id,
            "job_id": state.get("job_id", ""),
            "state": state.get("state", "UNKNOWN"),
        }
        | {key: state[key] for key in ("error", "elapsed_s", "note") if key in state}
    )
    preview = bounded_result(output, full_result=result_file)
    if preview.get("response_omitted") and state.get("state") == "COMPLETED":
        from .result_resource import result_uri

        preview["full_result_uri"] = result_uri(analysis_id, task_id)
    return preview


def submit(tool, args, analysis_id="default", rerun=False):
    if tool not in TOOLS:
        raise ValueError("unknown literature tool")
    from .priced_model import enabled as priced_model_enabled

    if tool == "extract_haploinsufficient_genes" and args.get("max_cost_usd", 0) > 0 and not priced_model_enabled():
        raise ValueError(
            "max_cost_usd cannot be enforced for an unpriced endpoint; configure budget enforcement at the model service"
        )
    ws = workspace(analysis_id)
    with lock(ws / ".submit.lock"):
        request = {"tool": tool, "args": args, "version": __version__, "inputs": fingerprint(tool)}
        if tool != "literature_search":
            binding = jobs.managed_file(ws, "corpus_binding.json")
            identity = {
                key: value for key, value in request["inputs"].items() if key not in {"model", "paperqa_settings"}
            }
            if binding.exists() and json.loads(binding.read_text()) != identity:
                raise ValueError("analysis is bound to another corpus version; use a new analysis_id")
            write_json(binding, identity)
        if "model" in request["inputs"]:
            binding = jobs.managed_file(ws, "model_binding.json")
            if binding.exists() and json.loads(binding.read_text()) != request["inputs"]["model"]:
                raise ValueError("analysis is bound to another model; use a new analysis_id")
            write_json(binding, request["inputs"]["model"])
        signature = hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest()[:32]
        active = jobs.managed_file(ws, "active.json")
        if active.exists():
            previous = json.loads(active.read_text())
            prior_id = previous["task_id"]
            prior = response(ws, analysis_id, prior_id)
            same = signature == previous.get("signature", prior_id)
            if same and not rerun and (prior["state"] == "COMPLETED" or prior["state"] not in TERMINAL):
                return prior
            if prior["state"] not in TERMINAL:
                raise ValueError(f"analysis has active task {prior_id}; poll literature_job_status")
        # A fresh attempt retains prior failure evidence and never rewrites a
        # completed job. Cached successful work is reused only while current.
        task_id = signature + "-" + uuid4().hex[:8]
        task = child_dir(ws / "jobs", task_id, "task_id")
        task.mkdir(exist_ok=False)
        write_json(task / "request.json", request | {"analysis_id": analysis_id})
        write_json(active, {"task_id": task_id, "signature": signature})
        try:
            executor = jobs.JobSettings.from_env()
            python = os.environ.get("AISCI_JOB_PYTHON") or ("python" if executor.prefix else sys.executable)
            jobs.submit(
                executor,
                [
                    python,
                    "-m",
                    "aiscientist_mcp.literature.worker",
                    "--analysis-id",
                    analysis_id,
                    "--task-id",
                    task_id,
                ],
                task,
                f"literature_{task_id[:12]}",
            )
        except Exception:
            write_json(task / "done.json", {"state": "FAILED", "error": "executor could not submit the job"})
            raise ValueError(f"could not submit literature task {task_id}") from None
        result = response(ws, analysis_id, task_id)
        if result["state"] == "FAILED":
            raise ValueError(
                f"literature task {task_id} failed: {result.get('error') or result.get('note') or result.get('status')}; "
                f"poll literature_job_status with analysis_id={analysis_id!r}"
            )
        return result


def status(analysis_id, task_id):
    safe_id(task_id, "task_id")
    return response(workspace(analysis_id), analysis_id, task_id)


def dispatch(tool, args, ws):
    ctx = SimpleNamespace(workspace=ws)
    if tool == "literature_search":
        from .public_search import search_europepmc

        return search_europepmc(**args)
    if tool == "search_corpus":
        from .search import run_search

        return run_search(args, ctx)
    if tool == "deep_literature":
        from .deep import run_paperqa

        return run_paperqa(args, ctx)
    if tool == "extract_haploinsufficient_genes":
        from .extract import run_extract

        return run_extract(args, ctx)
    raise ValueError("unknown literature tool")


def execute(analysis_id, task_id):
    ws = workspace(analysis_id)
    task = task_dir(ws, task_id)
    request = json.loads(jobs.managed_file(task, "request.json").read_text())
    with lock(ws / ".analysis.lock"):
        if request["inputs"] != fingerprint(request["tool"]):
            raise ValueError("corpus or model configuration changed after submission; submit again")
        result = dispatch(request["tool"], request["args"], ws)
        result["artifact_root"] = str(ws / "artifacts")
        if request["tool"] in {"deep_literature", "extract_haploinsufficient_genes"}:
            key = ModelSettings.from_env().api_key()
            if key:
                result = json.loads(json.dumps(result).replace(key, "[REDACTED]"))
        write_json(task / "result.json", result)
        if result.get("status") != "ok":
            raise ValueError(result.get("error") or result.get("note") or result.get("status", "analysis failed"))
