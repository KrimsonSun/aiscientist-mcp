"""Native MCP + actual job subprocess tests; bcftools/VEP are deterministic fixture executables."""

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from aiscientist_mcp.common import tasks
from aiscientist_mcp.variant_annotation.runtime import CONFIG
from aiscientist_mcp.variant_annotation.server import annotate_variants, server
from aiscientist_mcp.variant_annotation.settings import snapshot

_ROWS = [
    {
        "input": "17 7676154 . G A . . .",
        "seq_region_name": "17",
        "start": 7676154,
        "allele_string": "G/A",
        "most_severe_consequence": "missense_variant",
        "transcript_consequences": [
            {"gene_symbol": "TP53", "impact": "MODERATE", "consequence_terms": ["missense_variant"]}
        ],
        "colocated_variants": [{"clin_sig": ["pathogenic"]}],
    },
    {
        "input": "13 32316508 . C T . . .",
        "seq_region_name": "13",
        "start": 32316508,
        "allele_string": "C/T",
        "most_severe_consequence": "stop_gained",
        "transcript_consequences": [{"gene_symbol": "BRCA2", "impact": "HIGH", "consequence_terms": ["stop_gained"]}],
        "custom_annotations": {"ClinVar": [{"fields": {"CLNSIG": "Pathogenic"}}]},
    },
]


@pytest.fixture
def configured(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("VARIANT_"):
            monkeypatch.delenv(key)
    vcf = tmp_path / "sample.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n17\t7676154\t.\tG\tA\t.\tPASS\t.\n"
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("VARIANT_DATASET", str(vcf))
    monkeypatch.setenv("VARIANT_VEP_CACHE_DIR_GRCH38", str(cache))
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AISCI_EXECUTOR", "inline")
    return vcf


@pytest.fixture
def fake_commands(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "argv.jsonl"
    script = """#!__PYTHON__
import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_VEP_LOG"], "a") as fh:
    fh.write(json.dumps([Path(sys.argv[0]).name, *args])+"\\n")
if Path(sys.argv[0]).name == "bcftools":
    shutil.copyfile(args[-1], args[args.index("-o")+1])
else:
    assert "--offline" in args
    if os.environ.get("FAKE_VEP_FAIL"):
        print("fixture VEP failure",file=sys.stderr)
        sys.exit(3)
    Path(args[args.index("--output_file")+1]).write_text("\\n".join(json.dumps(row) for row in __ROWS__)+"\\n")
""".replace("__PYTHON__", sys.executable).replace("__ROWS__", repr(_ROWS))
    for name in ("bcftools", "vep"):
        exe = bindir / name
        exe.write_text(script)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_VEP_LOG", str(log))
    return log


def test_native_inline_pipeline_cache_reuse_and_setting_changes(configured, fake_commands, monkeypatch):
    from mcp import Client

    async def flow():
        async with Client(server, raise_exceptions=True) as client:
            schemas = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
            assert set(schemas) == {"annotate_variants", "variant_job_status"}
            props = schemas["annotate_variants"]["properties"]
            assert props["mode"]["default"] == "offline"
            assert "cache_dir" not in props and "fork" not in props and "ref_fasta" not in props
            first = (await client.call_tool("annotate_variants", {})).structured_content
            assert first["status"] == "ok" and first["state"] == "COMPLETED", first
            assert first["execution_mode"] == "offline_vep" and first["n_pathogenic"] == 2
            assert Path(first["annotated_table"]).is_file()
            assert len(list((Path(first["artifact_root"]) / "tables").glob("*.csv"))) == 5
            assert json.loads(Path(first["full_result"]).read_text())["n_variants"] == 2
            again = (await client.call_tool("annotate_variants", {})).structured_content
            assert again["task_id"] == first["task_id"]
            assert len(fake_commands.read_text().splitlines()) == 2
            monkeypatch.setenv("VARIANT_VEP_FORK", "4")
            changed = (await client.call_tool("annotate_variants", {})).structured_content
            assert changed["task_id"] != first["task_id"]
            assert changed["reference_settings"]["fork"] == 4
            assert Path(first["annotated_table"]).is_file()
            configured.write_text(configured.read_text() + "\n")
            changed_input = (await client.call_tool("annotate_variants", {})).structured_content
            assert changed_input["task_id"] != changed["task_id"]
            state = (
                await client.call_tool("variant_job_status", {"analysis_id": "default", "task_id": first["task_id"]})
            ).structured_content
            assert state["n_pathogenic"] == first["n_pathogenic"]

    asyncio.run(flow())


@pytest.mark.parametrize(
    "bad",
    [
        {"assembly": "hg19"},
        {"mode": "auto"},
        {"max_variants": -1},
        {"max_pop_af": 1.1},
        {"max_pop_af": float("nan")},
        {"analysis_id": "../escape"},
        {"species": "../../elsewhere"},
        {"cache_dir": "/other"},
        {"ref_fasta": "/other"},
        {"fork": 100},
    ],
)
def test_invalid_arguments_never_submit(configured, monkeypatch, bad):
    monkeypatch.setattr(tasks.jobs, "submit", lambda *a, **k: pytest.fail("submitted invalid arguments"))
    with pytest.raises((ValueError, TypeError)):
        annotate_variants(**bad)


def test_explicit_rest_needs_deployment_opt_in(configured, monkeypatch):
    monkeypatch.setattr(tasks.jobs, "submit", lambda *a, **k: pytest.fail("REST was scheduled without opt-in"))
    with pytest.raises(ValueError, match="VARIANT_ALLOW_REST"):
        annotate_variants(mode="rest")


def test_caller_vcf_bed_and_symlinks_obey_read_roots(configured, tmp_path, monkeypatch):
    other = tmp_path / "other.vcf"
    other.write_text(configured.read_text())
    with pytest.raises(ValueError, match="read roots"):
        annotate_variants(vcf_path=str(other))
    bed = tmp_path / "panel.bed"
    bed.write_text("17\t7676153\t7676154\n")
    with pytest.raises(ValueError, match="read roots"):
        annotate_variants(regions_bed=str(bed))
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "leak.vcf").symlink_to(other)
    monkeypatch.setenv("VARIANT_READ_ROOTS", str(allowed))
    with pytest.raises(ValueError, match="read roots"):
        annotate_variants(vcf_path=str(allowed / "leak.vcf"))


def test_failed_worker_keeps_real_error(configured, fake_commands, monkeypatch):
    monkeypatch.setenv("FAKE_VEP_FAIL", "1")
    result = annotate_variants()
    assert result["state"] == "FAILED" and result["status"] == "error"
    assert "fixture VEP failure" in result["error"]


def test_authorized_rest_preserves_original_filter_counts(configured, tmp_path, monkeypatch):
    from aiscientist_mcp.variant_annotation import tool, worker

    monkeypatch.setenv("VARIANT_ALLOW_REST", "1")
    monkeypatch.setattr(tool, "vep_annotate", lambda variants, **kwargs: [tool.parse_vep_result(_ROWS[0])])
    configured.write_text(configured.read_text() + "17\t2\t.\tC\tT\t.\tLowQual\t.\n")
    settings, _ = snapshot("GRCh38", "rest")
    result = worker.execute(
        {
            "settings": settings,
            "arguments": {
                "vcf_path": str(configured),
                "assembly": "GRCh38",
                "mode": "rest",
                "species": "human",
                "max_variants": 0,
                "pass_only": True,
            },
        },
        tmp_path / "rest-output",
    )
    assert result["status"] == "ok" and result["n_pass"] == 1 and result["n_nonpass"] == 1
    assert result["n_input_variants"] == 1 and result["n_pathogenic"] == 1
    assert Path(result["annotated_table"]).is_file()


def test_workspace_has_one_writer(configured):
    ws = tasks.workspace(CONFIG, "default")
    with tasks.lock(ws / ".submit.lock"), pytest.raises(ValueError, match="active"):
        annotate_variants()


def test_stdio_catalog_and_rejected_call(configured):
    base = [
        sys.executable,
        "-m",
        "aiscientist_mcp.common.client",
        "--server",
        f"{sys.executable} -m aiscientist_mcp.variant_annotation.server",
    ]
    listed = subprocess.run([*base, "tools"], text=True, capture_output=True, check=True, timeout=30)
    assert {x["name"] for x in json.loads(listed.stdout)} == {"annotate_variants", "variant_job_status"}
    bad = subprocess.run(
        [*base, "call", "annotate_variants", '{"assembly":"invalid"}'], text=True, capture_output=True, timeout=30
    )
    assert bad.returncode != 0
