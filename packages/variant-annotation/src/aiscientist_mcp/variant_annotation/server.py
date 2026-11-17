"""Typed MCP interface to offline VEP and explicitly enabled Ensembl REST."""

from typing import Annotated, Any, Literal

from aiscientist_mcp.common import tasks
from aiscientist_mcp.common.server import make_server, quiet, run_stdio, tool
from pydantic import ConfigDict, Field, validate_call

from .inputs import input_bed, input_vcf
from .runtime import CONFIG, present
from .settings import snapshot

server = make_server(
    "variant-annotation",
    "Annotate authorized VCF files with offline Ensembl VEP and ClinVar. "
    "Use variant_job_status to poll asynchronous tasks. REST is an explicit, deployment-gated "
    "mode which sends variants to Ensembl. No raw genotypes are returned.",
    strict=True,
)


@tool(server)
@validate_call(config=ConfigDict(strict=True, extra="forbid"))
def annotate_variants(
    analysis_id: str = "default",
    vcf_path: str = "",
    assembly: Literal["GRCh38", "GRCh37"] = "GRCh38",
    species: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")] = "human",
    mode: Literal["offline", "rest"] = "offline",
    pass_only: bool = True,
    max_variants: Annotated[int, Field(ge=0)] = 0,
    max_pop_af: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.0,
    genes: list[str] | None = None,
    regions_bed: str = "",
    sample: str = "",
    rerun: bool = False,
) -> dict[str, Any]:
    """Annotate a VCF with consequence, gene, impact, population frequency and ClinVar significance.

    vcf_path defaults to VARIANT_DATASET/AISCIENTIST_DATASET. Explicit VCF and regions_bed files
    must be inside VARIANT_READ_ROOTS. Offline VEP uses server-configured release-112 reference
    caches, ClinVar and optional predictors; model arguments cannot change those paths/resources.
    pass_only keeps FILTER=PASS or '.'. max_variants=0 keeps all offline rows; a positive cap
    limits returned annotation rows after VEP. max_pop_af=0 and empty genes disable those filters.
    regions_bed and sample restrict the offline VCF before VEP; they are unavailable in REST mode.

    REST sends variant loci/alleles to Ensembl and requires VARIANT_ALLOW_REST=1. It defaults to
    500 variants when max_variants=0 and reports truncation and full-file PASS/non-PASS counts.
    Offline supports human GRCh37/GRCh38; REST species must be an Ensembl species identifier.

    Slurm/local return analysis_id/task_id/state; poll variant_job_status. Inline waits and
    returns counts, bounded findings and complete table/full_result paths. Repeated unchanged
    calls reuse the current result; rerun creates a separate attempt. Clinical labels are the
    source annotations, not a diagnosis. Review missing predictors and QC before interpretation.
    """
    with quiet():
        vcf = input_vcf(vcf_path)
        bed = str(input_bed(regions_bed)) if regions_bed else ""
        if mode == "offline" and species not in {"human", "homo_sapiens"}:
            raise ValueError("offline reference caches support human only")
        if mode == "rest" and (bed or sample):
            raise ValueError("regions_bed and sample are supported only in offline mode")
        settings, references = snapshot(assembly, mode)
        arguments = {
            "vcf_path": str(vcf),
            "assembly": assembly,
            "species": species,
            "mode": mode,
            "pass_only": pass_only,
            "max_variants": max_variants,
            "max_pop_af": max_pop_af,
            "genes": genes or [],
            "regions_bed": bed,
            "sample": sample,
        }
        result = tasks.submit(
            CONFIG,
            analysis_id,
            "annotate_variants",
            arguments,
            inputs=[str(vcf), *([bed] if bed else []), *references],
            settings=settings,
            rerun=rerun,
        )
        return present(result)


@tool(server)
def variant_job_status(analysis_id: str, task_id: str) -> dict[str, Any]:
    """Poll a variant annotation task; completed jobs include bounded results and full evidence paths."""
    with quiet():
        return present(tasks.status(CONFIG, analysis_id, task_id))


def main():
    run_stdio(server)


if __name__ == "__main__":
    main()
