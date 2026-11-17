"""Confined analysis workspaces and common Slurm/local/inline jobs.

The scientific functions have no dependency on the AiScientist application. Their
small context contains only the admitted dataset and this analysis's workspace.
"""

import contextlib
import fcntl
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from aiscientist_mcp.common import jobs
from aiscientist_mcp.common.paths import child_dir, safe_id
from jsonschema import Draft202012Validator

from . import SERVER_NAME, __version__

SPECS = json.loads(Path(__file__).with_name("tool_specs.json").read_text())
INTEGRATION_POLICY = "deployed-auto-combat-v1"
TERMINAL = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "BOOT_FAIL",
    "DEADLINE",
    "REVOKED",
    "SPECIAL_EXIT",
}
CHAINS = {
    "run_scanpy_qc": (),
    "run_clustering": ("adata_qc.h5ad",),
    "run_de": ("adata_clustered.h5ad", "adata_qc.h5ad"),
    "run_depth_matched_de": ("adata_clustered.h5ad", "adata_qc.h5ad"),
    "run_doublet_detection": ("adata_qc.h5ad",),
    "run_integration": ("adata_qc.h5ad",),
    "run_pseudobulk_de": ("adata_qc.h5ad", "adata_annotated.h5ad", "adata_de.h5ad", "adata_clustered.h5ad"),
    "run_composition": ("adata_annotated.h5ad", "adata_de.h5ad", "adata_clustered.h5ad", "adata_qc.h5ad"),
    "run_marker_annotation": ("adata_de.h5ad", "adata_clustered.h5ad"),
}


def _json(path, value):
    jobs.write_json(path.parent, path.name, value)


def _metadata(path):
    return jobs.managed_file(path.parent, path.name)


def _read(path, default=None):
    path = _metadata(path)
    return json.loads(path.read_text()) if path.exists() else default


def _signature(request):
    identity = {key: value for key, value in request.items() if key != "analysis_id"}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()[:32]


def _settings():
    return {
        "genesets": os.environ.get("SCRNA_GENESETS_DIR") or os.environ.get("AISCIENTIST_GENESETS_DIR", ""),
        "checkpoint_root": os.environ.get("SCRNA_CHECKPOINT_ROOT", ""),
        "integration_policy": INTEGRATION_POLICY,
    }


def _execution_args(name, args):
    """Preserve the deployed default even though explicit Harmony is now bundled.

    The original request remains unchanged in metadata and cache identity. This
    policy belongs to the service adapter; the extracted scientific functions
    still receive an ordinary supported method and report what actually ran.
    """
    if name != "run_integration":
        return args, None
    requested = args.get("method", "auto")
    effective = "combat" if requested == "auto" else requested
    return args | {"method": effective}, {
        "policy": INTEGRATION_POLICY,
        "requested_method": requested,
        "method_argument_supplied": "method" in args,
        "effective_method": effective,
        "reason": "auto preserves deployed ComBat; Harmony requires explicit method='harmony'",
    }


def workspace(analysis_id):
    configured = Path(os.environ.get("AISCI_DATA_DIR", "./data")).expanduser()
    if configured.is_symlink():
        raise ValueError("configured data directory must not be a symlink")
    base = configured.resolve()
    root = base / SERVER_NAME
    if root.is_symlink() or root.resolve().parent != base:
        raise ValueError("server workspace escapes the configured data directory")
    root.mkdir(parents=True, exist_ok=True)
    path = child_dir(root, analysis_id, "analysis_id")
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError("analysis workspace escapes the configured data directory")
    path.mkdir(exist_ok=True)
    # An existing symlink could redirect a fixed checkpoint, figure or state path.
    if any(p.is_symlink() for p in path.rglob("*")):
        raise ValueError("analysis workspace contains a symlink; use regular files")
    for name in ("work", "artifacts", "jobs"):
        (path / name).mkdir(exist_ok=True)
    return path


@contextlib.contextmanager
def lock(path):
    with _metadata(path).open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("this analysis is busy; poll its active task before submitting another") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def bind_dataset(ws, supplied):
    binding = _metadata(ws / "dataset.json")
    previous = _read(binding, {}).get("path", "")
    configured = os.environ.get("SCRNA_DATASET") or os.environ.get("AISCIENTIST_DATASET", "")
    text = supplied or previous or configured
    if not text:
        return ""
    path = Path(text).expanduser().resolve()
    allowed = [Path(p).expanduser().resolve() for p in os.environ.get("SCRNA_READ_ROOTS", "").split(os.pathsep) if p]
    if configured:
        allowed.append(Path(configured).expanduser().resolve())
    if not any(path == root or (root.is_dir() and root in path.parents) for root in allowed):
        raise ValueError("dataset is outside SCRNA_READ_ROOTS and is not the configured SCRNA_DATASET")
    if not path.exists():
        raise ValueError("dataset does not exist")
    if path.is_relative_to(ws) or (path.is_dir() and ws.is_relative_to(path)):
        raise ValueError("dataset and analysis workspace must be separate; use input for a work checkpoint")
    if previous and previous != str(path):
        raise ValueError("analysis_id is already bound to another dataset; use a new analysis_id")
    _json(binding, {"path": str(path)})
    return str(path)


def _identity(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("input and output identities must not follow symlinks")
    if not path.exists():
        return {"path": str(path), "missing": True}
    if path.is_dir():
        entries = sorted(path.rglob("*"))
        if any(p.is_symlink() for p in entries):
            raise ValueError("input directory contains a symlink")
        return {"path": str(path), "files": [_identity(p) for p in entries if p.is_file()]}
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "inode": stat.st_ino,
    }


def _handoff_checkpoints(ws):
    """Read-only files from an operator-configured shared checkpoint directory."""
    configured = os.environ.get("SCRNA_CHECKPOINT_ROOT", "")
    if not configured:
        return []
    root = Path(configured).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("SCRNA_CHECKPOINT_ROOT must be a regular directory")
    root = root.resolve()
    imported = _read(ws / "checkpoint_imports.json", {})
    for name, original in imported.items():
        source = root / name
        if source.is_symlink() or _identity(source) != original:
            raise ValueError("upstream checkpoint changed after import; use a new analysis_id to avoid mixing analyses")
    paths = []
    for path in sorted(root.glob("adata_*.h5ad")):
        if not re.fullmatch(r"adata_[A-Za-z0-9_.-]+\.h5ad", path.name):
            continue
        if path.is_symlink() or path.resolve().parent != root or not path.is_file():
            raise ValueError("checkpoint handoff must contain regular files inside its configured root")
        if not (ws / "work" / path.name).exists():
            paths.append(path)
    return paths


def _input_identity(ws, name, args, dataset):
    from ._lib.scrna import _genesets_dir, _run_files

    ctx = SimpleNamespace(workspace=ws, decisions={"dataset_path": dataset})
    explicit = str(args.get("input") or "").strip()
    handoffs = _handoff_checkpoints(ws)
    paths = []
    if explicit:
        suffixes = (".rnk",) if name == "run_gsea_prerank" else (".h5ad",)
        paths, why = _run_files(ctx, explicit, suffixes, pattern_ok=name == "run_gsea_prerank")
        if why:
            # The same reading as _run_files: a bare name or a relative path means work/<it>.
            requested = Path(explicit)
            if requested.is_absolute():
                target = requested
            elif requested.parts and requested.parts[0] in ("work", "artifacts"):
                target = ws / requested
            else:
                target = ws / "work" / requested
            available = [p for p in handoffs if target == ws / "work" / p.name]
            if len(available) != 1:
                raise ValueError(why)
            paths = available
    elif name == "run_scanpy_qc":
        if not dataset:
            raise ValueError("run_scanpy_qc needs a dataset; configure SCRNA_DATASET or pass an admitted dataset")
        paths = [Path(dataset)]
    elif name in CHAINS:
        candidates = [ws / "work" / p for p in CHAINS[name]]
        paths = [p for p in candidates if p.exists()]
        if name != "run_pseudobulk_de":
            paths = paths[:1]
    elif name == "run_gsea_prerank":
        paths = sorted((ws / "artifacts/tables").glob("rank_*.rnk"))
    if name == "run_enrichment":
        paths += sorted((ws / "artifacts/tables").glob("de_*"))
    if name in {"run_enrichment", "run_gsea_prerank"}:
        paths += sorted(_genesets_dir().glob("*.gmt"))
    paths += [p for p in handoffs if p not in paths]
    return [_identity(p) for p in paths]


def _validate(name, args):
    if name not in SPECS:
        raise ValueError("unknown single-cell tool")
    Draft202012Validator(SPECS[name]["inputSchema"]).validate(args)
    # These are names, never paths; legacy algorithms interpolate them in file names.
    for value in [args.get("groupby", ""), *(args.get("gene_sets") or [])]:
        if any(ch in str(value) for ch in ("/", "\\", "\x00")) or str(value) in {".", ".."}:
            raise ValueError("groupby and gene_sets must be names, not paths")
    json.dumps(args, allow_nan=False)


def _task_dir(ws, task_id):
    parent = ws / "jobs"
    path = child_dir(parent, task_id, "task_id")
    if parent.is_symlink() or path.is_symlink() or path.resolve().parent != parent.resolve():
        raise ValueError("task path escapes this analysis")
    if not _metadata(path / "request.json").is_file():
        raise ValueError("unknown task_id")
    return path


def _outputs(ws):
    if any((ws / folder).is_symlink() for folder in ("work", "artifacts")):
        raise ValueError("analysis output directory contains a symlink")
    paths = [path for folder in ("work", "artifacts") for path in sorted((ws / folder).rglob("*"))]
    if any(path.is_symlink() for path in paths):
        raise ValueError("analysis output contains a symlink")
    return [_identity(path) for path in paths if path.is_file()]


def _cache_valid(task):
    saved = _read(task / "outputs.json")
    if saved is None:
        return False
    return saved == _outputs(task.parent.parent)


def _response(ws, analysis_id, task_id):
    path = _task_dir(ws, task_id)
    # A concurrent poll can see the atomically published request just before the
    # executor publishes state. Do not overwrite that initial state with {}.
    state = (
        jobs.status(jobs.JobSettings.from_env(), path)
        if _metadata(path / "state.json").exists()
        else {"state": "PENDING", "note": "waiting for executor submission"}
    )
    result_path = _metadata(path / "result.json")
    # Preemption may leave a previous allocation's payload beside the new active
    # executor state. Expose scientific output only when the attempt is terminal.
    result = _read(result_path, {}) if state.get("state") in TERMINAL else {}
    if state.get("state") == "COMPLETED" and not result_path.exists():
        state = state | {"state": "FAILED", "error": "job completed without result.json"}
    if state.get("state") in TERMINAL - {"COMPLETED"} and result.get("status") in {None, "ok", "empty"}:
        result["status"] = "error"
    # Identity cannot be overwritten by a domain result.
    return (
        result
        | {
            "analysis_id": analysis_id,
            "task_id": task_id,
            "job_id": state.get("job_id", ""),
            "state": state.get("state", "UNKNOWN"),
        }
        | {key: state[key] for key in ("error", "note", "elapsed_s") if key in state}
    )


def submit(name, args, analysis_id="default", dataset="", rerun=False):
    """Submit once per effective input and arguments; inline returns original result fields."""
    _validate(name, args)
    ws = workspace(analysis_id)
    with lock(ws / ".submit.lock"):
        bound = bind_dataset(ws, dataset)
        identity = _input_identity(ws, name, args, bound)
        request = {
            "version": __version__,
            "tool": name,
            "args": args,
            "dataset": bound,
            "inputs": identity,
            **_settings(),
        }
        signature = _signature(request)
        active = _metadata(ws / "active.json")
        if active.exists():
            prior = _read(active)
            active_id = prior["task_id"]
            current = _response(ws, analysis_id, active_id)
            if current["state"] not in TERMINAL:
                if signature == prior.get("signature", active_id) and not rerun:
                    return current
                raise ValueError(f"analysis {analysis_id} has active task {active_id}; poll scrna_job_status")
            if (
                not rerun
                and current["state"] == "COMPLETED"
                and signature == prior.get("signature", active_id)
                and _cache_valid(_task_dir(ws, active_id))
            ):
                return current
        cached = _read(ws / "cache.json", {}).get(signature) if not rerun else None
        if cached:
            old = _response(ws, analysis_id, cached)
            if old["state"] == "COMPLETED" and _cache_valid(_task_dir(ws, cached)):
                return old
        # Every actual execution gets an immutable attempt directory. A failure or
        # explicit rerun must never erase the request/result of an earlier attempt.
        task_id = signature + "-" + uuid4().hex[:8]
        task = child_dir(ws / "jobs", task_id, "task_id")
        task.mkdir(exist_ok=False)
        _json(task / "request.json", request | {"analysis_id": analysis_id})
        _json(active, {"task_id": task_id, "signature": signature})
        try:
            executor = jobs.JobSettings.from_env()
            python = os.environ.get("AISCI_JOB_PYTHON") or ("python" if executor.prefix else sys.executable)
            jobs.submit(
                executor,
                [
                    python,
                    "-m",
                    "aiscientist_mcp.scrna_analysis.worker",
                    "--analysis-id",
                    analysis_id,
                    "--task-id",
                    task_id,
                ],
                task,
                f"scrna_{name}_{task_id[:8]}",
            )
        except Exception:
            # No orphaned active record should block every later submission.
            failed = {"state": "FAILED", "error": "executor could not submit the job"}
            jobs.write_state(task, failed)
            _json(task / "done.json", failed)
            raise
        result = _response(ws, analysis_id, task_id)
        if result["state"] == "FAILED":
            raise ValueError(
                f"scrna task {task_id} failed: {result.get('error') or result.get('note') or result.get('status')}; "
                f"poll scrna_job_status with analysis_id={analysis_id!r} for its result"
            )
        return result


def status(analysis_id, task_id):
    safe_id(task_id, "task_id")
    ws = workspace(analysis_id)
    # common.jobs serializes executor status/retry updates itself. The submit lock
    # is held throughout inline execution, so taking it here would block polling.
    return _response(ws, analysis_id, task_id)


def execute(analysis_id, task_id):
    ws = workspace(analysis_id)
    task = _task_dir(ws, task_id)
    request = _read(task / "request.json")
    with lock(ws / ".analysis.lock"):
        if request.get("analysis_id") != analysis_id or request.get("version") != __version__:
            raise ValueError("worker request belongs to another analysis or package version")
        if any(request.get(key) != value for key, value in _settings().items()):
            raise ValueError("analysis configuration changed after submission; submit a new task")
        name, args = request["tool"], request["args"]
        _validate(name, args)
        dataset = bind_dataset(ws, request["dataset"])
        if request["inputs"] != _input_identity(ws, name, args, dataset):
            raise ValueError("input changed after submission; submit again against the current input")
        manifest = _metadata(ws / "checkpoint_imports.json")
        imported = _read(manifest, {})
        for source in _handoff_checkpoints(ws):
            shutil.copy2(source, _metadata(ws / "work" / source.name))
            imported[source.name] = _identity(source)
        if imported:
            _json(manifest, imported)
        compatibility = []
        from .input_compatibility import normalize_h5ad

        # Caller-owned datasets remain read-only. Work checkpoints are already
        # private imports or our own outputs, and retain their selected filenames.
        if dataset and Path(dataset).suffix.lower() == ".h5ad" and name == "run_scanpy_qc" and not args.get("input"):
            private = task / "input"
            private.mkdir(exist_ok=True)
            staged = _metadata(private / Path(dataset).name)
            changes = normalize_h5ad(Path(dataset), staged)
            if changes:
                compatibility.append({"source": _identity(dataset), "staged": str(staged), "changes": changes})
                dataset_for_tool = str(staged)
            else:
                dataset_for_tool = dataset
        else:
            dataset_for_tool = dataset
        selected = _input_identity(ws, name, args, dataset)
        checkpoints = {
            Path(item["path"])
            for item in selected
            if Path(item["path"]).suffix.lower() == ".h5ad" and Path(item["path"]).is_relative_to(ws)
        }
        for checkpoint in sorted(checkpoints):
            original_identity = _identity(checkpoint)
            changes = normalize_h5ad(checkpoint, checkpoint)
            if changes:
                compatibility.append({"source": original_identity, "staged": str(checkpoint), "changes": changes})
        compatibility_path = ""
        if compatibility:
            data = ws / "artifacts" / "data"
            data.mkdir(exist_ok=True)
            report = data / f"input_compatibility_{task_id}.json"
            _json(report, {"expression_values_changed": False, "inputs": compatibility})
            compatibility_path = str(report)
        module = importlib.import_module(f"aiscientist_mcp.scrna_analysis.{name}.tool")
        ctx = SimpleNamespace(workspace=ws, decisions={"dataset_path": dataset_for_tool})
        execution_args, integration_policy = _execution_args(name, args)
        result = getattr(module, name)(execution_args, ctx)
        if integration_policy:
            result["integration_policy"] = integration_policy
        checkpoint = str(result.get("checkpoint") or "")
        checkpoint_name = Path(checkpoint)
        candidate = (
            ws / checkpoint_name
            if checkpoint_name.parts and checkpoint_name.parts[0] == "work"
            else ws / "work" / checkpoint_name
        )
        if not checkpoint:
            candidates = sorted((ws / "work").glob("adata_*.h5ad"), key=lambda path: path.stat().st_mtime_ns)
            candidate = candidates[-1] if candidates else ws / "work"
        checkpoint_path = ""
        if candidate.is_file() and candidate.resolve().is_relative_to(ws.resolve()):
            checkpoint_path = str(candidate.resolve())
        result = result | {"artifact_root": str(ws / "artifacts"), "checkpoint_path": checkpoint_path}
        if compatibility_path:
            result["input_compatibility_path"] = compatibility_path
        _json(task / "result.json", result)
        if result.get("status") != "ok":
            raise ValueError(result.get("error") or result.get("note") or result.get("status", "analysis failed"))
        _json(task / "outputs.json", _outputs(ws))
        # Some tools update the checkpoint they read (doublet detection), and
        # handoff imports replace external input identity with a local checkpoint.
        # Both pre- and post-execution inputs may reuse this successful attempt,
        # but only while the full output inventory remains unchanged.
        after = request | {"inputs": _input_identity(ws, name, args, dataset)}
        cache = _read(ws / "cache.json", {})
        for identity in (_signature(request), _signature(after)):
            cache[identity] = task_id
        _json(ws / "cache.json", cache)
