"""Run the migrated VEP pipeline in a task's own output directory."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from aiscientist_mcp.common.tasks import worker_main

from .runtime import CONFIG


def _execute(request: dict, output_dir: Path) -> dict:
    args = dict(request["arguments"])
    settings = request["settings"]
    env = settings["env"]
    output_dir.mkdir(parents=True, exist_ok=True)
    mode = args.pop("mode")
    src = args.pop("vcf_path")
    species = args.pop("species")
    if mode == "offline":
        from .offline import run_offline_annotation

        result = run_offline_annotation(
            src,
            output_dir,
            cache_dir=env["VARIANT_VEP_CACHE_DIR"],
            clinvar_vcf=env["VARIANT_VEP_CLINVAR"],
            fork=settings["fork"],
            spliceai_max_variants=settings["spliceai_max_variants"],
            **args,
        )
    else:
        from .tool import annotate_variants_rest

        if env["VARIANT_ALLOW_REST"] != "1":
            raise ValueError("REST requires deployment authorization")
        os.environ["VARIANT_ARTIFACTS"] = str(output_dir / "artifacts")
        os.environ["VARIANT_WORK"] = str(output_dir / "work")
        result = annotate_variants_rest(
            args | {"vcf_path": src, "species": species},
            SimpleNamespace(workspace=output_dir, decisions={"dataset_path": src}),
        )
    result["artifact_root"] = str(output_dir / "artifacts")
    result["reference_settings"] = settings
    return result


def execute(request: dict, output_dir: Path) -> dict:
    env = request["settings"]["env"] | {
        "VARIANT_ARTIFACTS": str(output_dir / "artifacts"),
        "VARIANT_WORK": str(output_dir / "work"),
    }
    previous = {key: os.environ.get(key) for key in env}
    try:
        os.environ.update(env)
        return _execute(request, output_dir)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main():
    directory = Path(sys.argv[1]).resolve()
    worker_main(CONFIG, lambda request, ws: execute(request, directory / "output"))


if __name__ == "__main__":
    main()
