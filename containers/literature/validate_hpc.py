"""Run native MCP acceptance inside one exact, operator-supplied SIF.

Submit this through sbatch, never run scientific work on a login node. Example:
  sbatch --account=<account> --partition=<cpu-partition> --cpus-per-task=4 --mem=24G \
    --wrap='module load python/<version> apptainer/<version>; python validate_hpc.py \
      literature --image /path/to/literature.sif --output /isolated/check \
      --corpus /readonly/retigene --binding /private/model/binding.json'
For phenotype use mode phenotype and add --lirical-data /readonly/lirical/data.
The binding JSON contains endpoint/model/api_key_file PATH, never a key value.
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("literature", "phenotype"))
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--lirical-data", type=Path)
    parser.add_argument("--only", help="one named action, for a focused follow-up check")
    parser.add_argument("--question", help="explicit public literature question for a follow-up")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def absolute(path):
    if path is None or not path.is_absolute() or not path.exists() or any(c in str(path) for c in ":\n\r"):
        raise ValueError("supply an existing absolute path without bind separators")
    return str(path.resolve())


def launch(args):
    if not os.environ.get("SLURM_JOB_ID"):
        raise SystemExit("submit this validator through sbatch")
    image, corpus = absolute(args.image), absolute(args.corpus)
    binding = json.loads(Path(absolute(args.binding)).read_text())
    key_file = absolute(Path(binding["api_key_file"]))
    for key in ("endpoint", "model"):
        if not isinstance(binding[key], str) or any(c in binding[key] for c in "\n\r\0"):
            raise ValueError("invalid nonsecret model binding metadata")
    if not args.output.is_absolute() or ":" in str(args.output):
        raise ValueError("output must be an absolute isolated directory")
    args.output.mkdir(parents=True, exist_ok=False)
    out = str(args.output)
    script = str(Path(__file__).resolve())
    binds = [f"{out}:{out}", f"{corpus}:{corpus}:ro", f"{key_file}:{key_file}:ro", f"{script}:/validation.py:ro"]
    values = {
        "AISCI_DATA_DIR": out + "/data",
        "AISCI_EXECUTOR": "inline",
        "SLURM_JOB_ID": os.environ["SLURM_JOB_ID"],
        "LITERATURE_CORPUS_ROOT": corpus,
        "LITERATURE_ALLOWED_ROOTS": corpus,
        "LITERATURE_EVIDENCE_K": "8",
        "LITERATURE_MAX_SOURCES": "4",
        "LITERATURE_SEARCH_COUNT": "6",
        "LITERATURE_CONCURRENCY": "1",
        "LITERATURE_ANSWER_LENGTH": "about 200 words",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    for prefix in ("LITERATURE", "PHENOTYPE"):
        values.update(
            {
                f"{prefix}_MODEL_ENDPOINT": binding["endpoint"],
                f"{prefix}_MODEL": binding["model"],
                f"{prefix}_MODEL_API_KEY_FILE": key_file,
                f"{prefix}_MODEL_TIMEOUT": "240",
                f"{prefix}_MODEL_MAX_TOKENS": "2048",
            }
        )
    if args.mode == "phenotype":
        references = absolute(args.lirical_data)
        binds.append(f"{references}:{references}:ro")
        values.update(LIRICAL_DATA_DIR=references, PHENOTYPE_ALLOWED_ROOTS=references)
    # No inherited APPTAINERENV_ values or source PYTHONPATH can mask the SIF's wheel.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("APPTAINERENV_", "SINGULARITYENV_"))}
    env.update({"APPTAINERENV_" + k: v for k, v in values.items()})
    with open(image, "rb") as image_file:
        digest = hashlib.file_digest(image_file, "sha256").hexdigest()
    (args.output / "image.json").write_text(
        json.dumps({"sif": image, "sha256": digest, "slurm_job_id": os.environ["SLURM_JOB_ID"]}, indent=2)
    )
    command = [
        "apptainer",
        "exec",
        "--cleanenv",
        "--containall",
        "--bind",
        ",".join(binds),
        image,
        "python",
        "/validation.py",
        args.mode,
        "--inside",
        "--output",
        out,
    ]
    for option in ("only", "question"):
        if getattr(args, option):
            command += ["--" + option, getattr(args, option)]
    raise SystemExit(subprocess.run(command, env=env, check=False).returncode)


async def check(args):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    namespace = "literature" if args.mode == "literature" else "phenotype-diagnosis"
    command = "literature-mcp" if args.mode == "literature" else "phenotype-diagnosis-mcp"
    record = {
        "kind": "native SIF + actual staged references/model; synthetic clinical input and bounded curation",
        "python": sys.version.split()[0],
        "mode": args.mode,
        "checks": {},
    }
    specs = (
        [
            ("literature_search", {"query": "PRPF31 retinitis pigmentosa", "limit": 3}),
            (
                "search_corpus",
                {"queries": ["What evidence supports PRPF31 haploinsufficiency in retinitis pigmentosa?"], "top_k": 3},
            ),
            (
                "deep_literature",
                {
                    "question": args.question
                    or "What evidence supports PRPF31 haploinsufficiency as a mechanism of retinitis pigmentosa?"
                },
            ),
            (
                "extract_haploinsufficient_genes",
                {
                    "n_requests": 1,
                    "k_per_request": 4,
                    "keyword_round": 0,
                    "term_round": 0,
                    "k_per_gene": 0,
                    "max_passages": 4,
                    "concurrency": 1,
                    "pilot_fraction": 0.25,
                    "check_unread": 0,
                    "max_unread_passages": 0,
                    "pmids": ["11545739", "24419317", "12714658"],
                },
            ),
        ]
        if args.mode == "literature"
        else [
            (
                "map_phenotype_to_hpo",
                {
                    "text": "Synthetic case: the patient has night blindness and constricted peripheral vision. The patient has no hearing loss."
                },
            ),
            ("run_lirical", {"hpo_terms": ["HP:0000662", "HP:0000510"]}),
            (
                "diagnose_disease",
                {"hpo_terms": ["HP:0000662", "HP:0000510"], "candidate_genes": ["PRPF31"], "max_literature_queries": 1},
            ),
        ]
    )
    if args.only:
        specs = [(name, parameters) for name, parameters in specs if name == args.only]
        if not specs:
            raise ValueError("--only must name an action in the selected server")
    params = StdioServerParameters(command=command, env=dict(os.environ))
    async with Client(params, read_timeout_seconds=1800) as client:
        listing = await client.list_tools()
        (args.output / "tools.json").write_text(
            json.dumps([t.model_dump(by_alias=True) for t in listing.tools], indent=2)
        )
        schema_tool, schema_arg = (
            ("literature_search", "query") if args.mode == "literature" else ("map_phenotype_to_hpo", "text")
        )
        for invalid in ({schema_arg: 7}, {schema_arg: "synthetic schema check", "unknown_parameter": True}):
            rejected = await client.call_tool(schema_tool, invalid)
            assert rejected.is_error, "native tool must reject wrong types and unknown parameters"
        record["schema_rejections"] = 2
        for tool, parameters in specs:
            parameters["analysis_id"] = "native-" + tool.replace("_", "-")
            start = time.monotonic()
            response = await client.call_tool(tool, parameters)
            (args.output / (tool + ".json")).write_text(response.model_dump_json(indent=2))
            if response.is_error or not isinstance(response.structured_content, dict):
                raise AssertionError(f"{tool} failed; inspect saved native response")
            data = response.structured_content
            assert data["state"] == "COMPLETED" and data["status"] == "ok", tool
            status_name = "literature_job_status" if args.mode == "literature" else "phenotype_job_status"
            polled = await client.call_tool(
                status_name, {"analysis_id": data["analysis_id"], "task_id": data["task_id"]}
            )
            assert not polled.is_error and polled.structured_content["state"] == "COMPLETED"
            assert len(response.model_dump_json().encode()) < 24000, tool
            if data.get("response_omitted"):
                full_path = Path(data["full_result"]).resolve()
                assert full_path.is_relative_to(args.output / "data" / namespace)
                full = json.loads(full_path.read_text())
            else:
                full = data
            if tool == "deep_literature":
                assert full.get("contexts") and full.get("answer")
                answer = full["answer"].strip().lower().rstrip(".! ")
                assert answer not in {"i cannot answer", "cannot answer", "i don't know", "no answer"}, (
                    "model abstention is recorded, not counted as substantive answer acceptance"
                )
            elif tool == "extract_haploinsufficient_genes":
                assert full["n_judged"] == 4
            elif tool == "map_phenotype_to_hpo":
                assert full["mode"] == "llm" and full["hpo_terms"] and full["excluded_hpo"]
            elif tool == "diagnose_disease":
                evidence = full["literature"]
                assert full["mode"] == "lirical+literature" and evidence
                assert evidence[0]["evidence_status"] in {"graded", "unsupported", "contradicted"}
                assert evidence[0]["evidence"]
            record["checks"][tool] = {
                "state": data["state"],
                "task_id": data["task_id"],
                "elapsed_s": round(time.monotonic() - start, 2),
                "wire_bytes": len(response.model_dump_json().encode()),
            }
            (args.output / "evidence.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    opts = arguments()
    if opts.inside:
        asyncio.run(check(opts))
    else:
        launch(opts)
