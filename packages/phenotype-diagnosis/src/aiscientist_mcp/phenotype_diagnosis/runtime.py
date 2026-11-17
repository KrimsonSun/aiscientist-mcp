"""Confined native jobs for phenotype mapping and differential diagnosis."""

import json
import subprocess
from pathlib import Path
from uuid import uuid4

from aiscientist_mcp.common import tasks
from aiscientist_mcp.common.model import ModelSettings
from aiscientist_mcp.common.response import bounded_result

from . import __version__
from .settings import Settings, input_path, model_max_tokens

CONFIG = tasks.TaskConfig("phenotype-diagnosis", "aiscientist_mcp.phenotype_diagnosis.worker", __version__)
TOOLS = {"map_phenotype_to_hpo", "run_lirical", "diagnose_disease"}


def submit(name, arguments, analysis_id="default", rerun=False):
    if name not in TOOLS:
        raise ValueError("unknown phenotype operation")
    settings = Settings.from_env()
    args = dict(arguments)
    inputs = settings.inputs()
    if args.get("vcf_path"):
        args["vcf_path"] = str(input_path(args["vcf_path"]))
        inputs.append(args["vcf_path"])
    return preview(
        tasks.submit(CONFIG, analysis_id, name, args, inputs=inputs, settings=settings.identity(), rerun=rerun)
    )


def status(analysis_id, task_id):
    return preview(tasks.status(CONFIG, analysis_id, task_id))


def preview(result):
    full = tasks.task_dir(tasks.workspace(CONFIG, result["analysis_id"]), result["task_id"]) / "result.json"
    return bounded_result(result, full_result=full)


def _prepare(ws):
    # Core routines write descendants of these directories; reject existing symlinks
    # before allowing an executor to create/update provenance and result artifacts.
    for name in ("work", "artifacts"):
        path = tasks.confined_child(ws, name)
        path.mkdir(exist_ok=True)
        if any(p.is_symlink() for p in path.rglob("*")):
            raise ValueError("analysis output contains a symlink")


def _lirical(args, ws, settings):
    from .hpo.mapper import validate_hpo_ids
    from .lirical import run_lirical

    # Fail closed if a configured ontology is unreadable; the standalone server
    # must not let the legacy core fallback bypass the grounded-ID gate.
    validate_hpo_ids(args["hpo_terms"])
    validate_hpo_ids(args.get("excluded_hpo", []))

    def execute(argv):
        try:
            return subprocess.run(argv, cwd=ws, text=True, capture_output=True, timeout=settings.timeout, check=False)
        except FileNotFoundError:
            raise ValueError("LIRICAL_COMMAND executable is unavailable") from None
        except subprocess.TimeoutExpired:
            raise TimeoutError("LIRICAL exceeded LIRICAL_TIMEOUT") from None

    return run_lirical(
        hpo_terms=args["hpo_terms"],
        excluded_hpo=args.get("excluded_hpo", []),
        sample_id=args.get("sample_id", "sample-1"),
        vcf_path=args.get("vcf_path", ""),
        workspace=str(ws),
        data_dir=settings.data_dir,
        assembly=settings.assembly,
        exomiser_hg19=settings.exomiser_hg19,
        exomiser_hg38=settings.exomiser_hg38,
        output_prefix="lirical-" + uuid4().hex,
        base_cmd=settings.command,
        exec_fn=execute if settings.data_dir else None,
    )


def execute(request, ws: Path):
    settings = Settings.from_env()
    if settings.identity() != request["settings"]:
        raise ValueError("server configuration changed after submission; submit a new task")
    _prepare(ws)
    args, operation = request["arguments"], request["operation"]
    if args.get("vcf_path"):
        input_path(args["vcf_path"])
    from .hpo.mapper import map_text_to_hpo, validate_hpo_ids

    if operation == "map_phenotype_to_hpo":
        model = ModelSettings.from_env("PHENOTYPE")
        chat = model.judge(max_tokens=model_max_tokens(), json_object=False) if model.endpoint_pair() else None
        return map_text_to_hpo(args["text"], chat_fn=chat, k=args.get("k", 8)) | {"text_source": "argument"}
    if operation == "run_lirical":
        return _lirical(args, ws, settings)
    if operation != "diagnose_disease":
        raise ValueError("unknown phenotype operation")
    from .diagnose import diagnose
    from .evidence import make_deep_literature_runner
    from .literature_client import executor

    checked = validate_hpo_ids(args["hpo_terms"])
    # Literature must receive the same grounded IDs as LIRICAL, including replacements.
    if not checked["valid"]:
        return {"status": "error", "error": "no valid HPO terms; first use map_phenotype_to_hpo"}
    runner = None
    if settings.literature_command:
        runner = make_deep_literature_runner(
            executor(settings.literature_command, request["analysis_id"], settings.literature_timeout),
            hpo_labels=checked["labels"],
        )
    return diagnose(
        hpo_terms=checked["valid"],
        candidate_genes=args.get("candidate_genes", []),
        max_literature_queries=args.get("max_literature_queries", 6),
        lirical_result=_lirical(args, ws, settings),
        literature_runner=runner,
        hpo_labels=checked["labels"],
    )


def safe_execute(request, ws):
    model = ModelSettings.from_env("PHENOTYPE")
    key = model.api_key() if request["operation"] == "map_phenotype_to_hpo" and model.endpoint_pair() else ""
    try:
        result = execute(request, ws)
        result["artifact_root"] = str(ws / "artifacts")
        # Model responses are data, including an accidental secret echo.
        return json.loads(json.dumps(result).replace(key, "[REDACTED]")) if key else result
    except Exception as exc:
        message = str(exc).replace(key, "[REDACTED]") if key else str(exc)
        raise ValueError(message) from None
