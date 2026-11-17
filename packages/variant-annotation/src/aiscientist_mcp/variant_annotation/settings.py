"""Capture server-owned reference settings in each annotation request."""

import glob
import os
from pathlib import Path

OPTIONAL = (
    "VEP_PLUGINS",
    "VEP_CADD_SNV",
    "VEP_CADD_INDELS",
    "VEP_ALPHAMISSENSE",
    "VEP_REVEL",
    "VEP_PLUGINS_DIR",
    "SPLICEAI",
    "SPLICEAI_BIN",
    "SPLICEAI_MODELS",
    "IRD_ANNOTATE",
    "IRD_HGMD",
    "IRD_RETINA_EXONS",
    "IRD_ATAC",
    "IRD_DBSCSNV",
)


def snapshot(assembly: str, mode: str) -> tuple[dict, list[str]]:
    env = {}
    for key in (*OPTIONAL, "REF_FASTA", "VEP_CACHE_DIR", "VEP_CLINVAR"):
        name = "VARIANT_" + key
        env[name] = os.environ.get(name + "_" + assembly.upper(), os.environ.get(name, ""))
    env["VARIANT_ALLOW_REST"] = os.environ.get("VARIANT_ALLOW_REST", "")
    if mode == "rest" and env["VARIANT_ALLOW_REST"] != "1":
        raise ValueError("REST sends variants to Ensembl; deployment must explicitly set VARIANT_ALLOW_REST=1")
    try:
        fork = int(os.environ.get("VARIANT_VEP_FORK") or os.environ.get("SLURM_CPUS_PER_TASK") or "8")
        splice_cap = int(os.environ.get("VARIANT_SPLICEAI_MAX_VARIANTS", "0"))
    except ValueError as exc:
        raise ValueError("VARIANT_VEP_FORK and VARIANT_SPLICEAI_MAX_VARIANTS must be integers") from exc
    if fork <= 0 or splice_cap < 0:
        raise ValueError("VEP fork must be positive; SpliceAI cap cannot be negative")
    if mode == "offline":
        cache = Path(env["VARIANT_VEP_CACHE_DIR"])
        if not env["VARIANT_VEP_CACHE_DIR"] or not cache.is_dir():
            raise ValueError(f"configure VARIANT_VEP_CACHE_DIR_{assembly.upper()} with the release-112 cache")
        clinvar = env["VARIANT_VEP_CLINVAR"]
        if clinvar and not Path(clinvar).is_file():
            raise ValueError("configured VARIANT_VEP_CLINVAR file does not exist")
    reference_files = []
    for key, value in env.items():
        if key in {"VARIANT_ALLOW_REST", "VARIANT_VEP_PLUGINS", "VARIANT_SPLICEAI", "VARIANT_IRD_ANNOTATE"}:
            continue
        if value and Path(value).exists():
            reference_files.append(str(Path(value).resolve()))
            for suffix in (".tbi", ".csi", ".fai"):
                sidecar = Path(value + suffix)
                if sidecar.is_file():
                    reference_files.append(str(sidecar.resolve()))
    template = env["VARIANT_IRD_DBSCSNV"]
    if template and "{chrom}" in template:
        reference_files.extend(str(Path(p).resolve()) for p in sorted(glob.glob(template.replace("{chrom}", "*"))))
    return {"env": env, "fork": fork, "spliceai_max_variants": splice_cap, "vep_release": "112"}, sorted(
        set(reference_files)
    )
