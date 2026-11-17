"""Exercise the real MCP boundary, worker and original output parity."""

import asyncio
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiscientist_mcp.data_utilities.guards import check_dot, check_hdf5_links
from aiscientist_mcp.data_utilities.inspect import inspect_dataset as inspect_core


@pytest.fixture
def environment(tmp_path, monkeypatch):
    query = tmp_path / "case.vcf"
    query.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=1,length=249250621,assembly=GRCh37>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPATIENT\n"
        "1\t100\t.\tG\tA\t.\tPASS\t.\tGT\t0/1\n"
    )
    monkeypatch.setenv("AISCI_DATA_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("DATA_UTILITIES_DATASET", str(query))
    for key in ("AISCI_EXECUTOR", "DATA_UTILITIES_MODEL_ENDPOINT", "DATA_UTILITIES_MODEL"):
        monkeypatch.delenv(key, raising=False)
    return query


def test_real_mcp_inspection_rendering_and_validation(environment):
    from aiscientist_mcp.data_utilities.server import server
    from mcp import Client

    async def check():
        async with Client(server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert set(tools) == {"inspect_dataset", "make_schematic", "data_utilities_job_status"}
            assert tools["make_schematic"].input_schema["required"] == ["source"]
            assert (await client.call_tool("inspect_dataset", {"unexpected": True})).is_error
            result = await client.call_tool("inspect_dataset", {"analysis_id": "triage"})
            assert not result.is_error, result
            out = result.structured_content
            assert out["state"] == "COMPLETED"
            assert {k: out[k] for k in inspect_core(str(environment))} == inspect_core(str(environment))
            assert (await client.call_tool("inspect_dataset", {"analysis_id": "triage"})).structured_content == out
            assert (await client.call_tool("inspect_dataset", {"path": "/etc/hosts"})).is_error
            assert (await client.call_tool("inspect_dataset", {"path": 123})).is_error
            assert (await client.call_tool("make_schematic", {"source": ""})).is_error
            assert (await client.call_tool("make_schematic", {"source": 'digraph{a[image="/etc/hosts"]}'})).is_error
            assert (await client.call_tool("inspect_dataset", {"analysis_id": "../escape"})).is_error
            if shutil.which("dot"):
                result = await client.call_tool(
                    "make_schematic",
                    {"source": "digraph{QC -> Clustering}", "basename": "path/way!", "analysis_id": "figure"},
                )
                assert not result.is_error, result
                fig = result.structured_content
                assert fig["state"] == "COMPLETED" and fig["figure"] == "figures/schematic_pathway.png"
                assert Path(fig["path"]).read_bytes().startswith(b"\x89PNG")
                assert Path(fig["artifact_root"]) / fig["figure"] == Path(fig["path"])
                assert fig["width"] > 0 and fig["height"] > 0

    asyncio.run(check())


@pytest.mark.parametrize(
    "source",
    [
        'digraph{a[image="/secret.png"]}',
        'digraph{subgraph s{a["image"="/secret.png"]}}',
        'digraph{graph[imagepath="/secret"]}',
        'digraph{a[label=<<TABLE><TR><TD><IMG SRC="/secret.png"/></TD></TR></TABLE>>]}',
        'digraph{a[shapefile="/secret.ps"]}',
        'digraph{graph[_background="I 0 0 10 10 11 -/secret.png"]}',
    ],
)
def test_diagram_cannot_read_unrelated_files(source):
    with pytest.raises(ValueError, match="not allowed"):
        check_dot(source)


def test_hdf5_external_links_and_output_symlinks_are_refused(environment, tmp_path):
    import h5py
    from aiscientist_mcp.data_utilities.worker import execute

    linked = tmp_path / "linked.h5"
    with h5py.File(linked, "w") as f:
        f["obs"] = h5py.ExternalLink("/unrelated.h5", "/obs")
    with pytest.raises(ValueError, match="external"):
        check_hdf5_links(linked)
    workspace = tmp_path / "workspace"
    figs = workspace / "artifacts/figures"
    figs.mkdir(parents=True)
    (figs / "schematic_x.png").symlink_to(environment)
    with pytest.raises(ValueError, match="regular file"):
        execute({"operation": "make_schematic", "arguments": {"source": "digraph{a->b}", "basename": "x"}}, workspace)
    assert environment.read_text().startswith("##fileformat")


def test_local_worker_and_changed_input(environment, monkeypatch):
    from aiscientist_mcp.data_utilities.server import data_utilities_job_status, inspect_dataset

    monkeypatch.setenv("AISCI_EXECUTOR", "local")
    pending = inspect_dataset(analysis_id="local")
    for _ in range(100):
        out = data_utilities_job_status("local", pending["task_id"])
        if out["state"] not in {"PENDING", "RUNNING"}:
            break
        time.sleep(0.05)
    assert out["state"] == "COMPLETED"
    environment.write_text("x,y\n1,2\n")
    next_ = inspect_dataset(analysis_id="local")
    assert next_["task_id"] != pending["task_id"]
    for _ in range(100):
        out = data_utilities_job_status("local", next_["task_id"])
        if out["state"] not in {"PENDING", "RUNNING"}:
            break
        time.sleep(0.05)
    assert out["peek"]["detected_format"] == "tabular"


def test_slurm_submission(environment, monkeypatch):
    from aiscientist_mcp.common import jobs
    from aiscientist_mcp.data_utilities.server import inspect_dataset

    monkeypatch.setenv("AISCI_EXECUTOR", "slurm")
    monkeypatch.setattr(
        jobs.subprocess,
        "run",
        lambda command, **kw: SimpleNamespace(stdout="56789\n" if command[0] == "sbatch" else "PENDING\n"),
    )
    pending = inspect_dataset(analysis_id="slurm")
    assert pending["state"] == "PENDING" and pending["job_id"] == "56789"


def test_stdio_lists_actual_protocol_tools(environment):
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "aiscientist_mcp.common.client",
            "--server",
            shlex.join([sys.executable, "-m", "aiscientist_mcp.data_utilities.server"]),
            "tools",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert {t["name"] for t in json.loads(out.stdout)} == {
        "inspect_dataset",
        "make_schematic",
        "data_utilities_job_status",
    }


def test_original_deterministic_output_parity(environment, tmp_path):
    root = os.environ.get("AISCI_LEGACY_SOURCE")
    if not root:
        pytest.skip("set AISCI_LEGACY_SOURCE to the old app checkout for actual-source parity")
    modules = {}
    for name in ("inspect_dataset", "make_schematic"):
        path = Path(root) / "src/aiscientist/tools" / name / "tool.py"
        spec = importlib.util.spec_from_file_location("legacy_" + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[name] = module
    assert modules["inspect_dataset"].inspect_dataset(str(environment)) == inspect_core(str(environment))
    from aiscientist_mcp.data_utilities import schematic

    steps = ["**Quality control** — measurements", "Clustering. Estimate groups", "Differential expression"]
    assert modules["make_schematic"].workflow_schematic_dot(steps) == schematic.workflow_schematic_dot(steps)
    if shutil.which("dot"):
        a = modules["make_schematic"].render_dot("digraph{a->b}", tmp_path / "old")
        b = schematic.render_dot("digraph{a->b}", tmp_path / "new")
        assert Path(a["path"]).read_bytes() == Path(b["path"]).read_bytes()
        assert {k: v for k, v in a.items() if k not in {"path", "dot_path"}} == {
            k: v for k, v in b.items() if k not in {"path", "dot_path"}
        }


def test_large_inspection_bounds_both_mcp_copies_and_keeps_full_evidence(environment):
    from aiscientist_mcp.data_utilities.server import server
    from mcp import Client

    environment.write_text("x" * 260000)

    async def check():
        async with Client(server) as client:
            reply = await client.call_tool("inspect_dataset", {"analysis_id": "long-line"})
            assert not reply.is_error, reply
            result = reply.structured_content
            assert result["response_omitted"] is True
            assert len(json.dumps(reply.model_dump(mode="json", by_alias=True)).encode()) < 24000
            full = Path(result["full_result"])
            assert full.is_relative_to(Path(result["artifact_root"]))
            original = json.loads(full.read_text())
            assert original["peek"]["line_sample"] == ["x" * 260000]
            polled = await client.call_tool(
                "data_utilities_job_status", {"analysis_id": "long-line", "task_id": result["task_id"]}
            )
            assert polled.structured_content == result
            assert len(json.dumps(polled.model_dump(mode="json", by_alias=True)).encode()) < 24000
            # A second attempt cannot overwrite a previous result's full evidence.
            environment.write_text("a,b\n1,2\n")
            again = await client.call_tool("inspect_dataset", {"analysis_id": "long-line"})
            assert not again.is_error
            assert again.structured_content["full_result"] != str(full)
            assert json.loads(full.read_text()) == original

    asyncio.run(check())
