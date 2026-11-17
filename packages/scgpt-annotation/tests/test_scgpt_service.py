"""Protocol/worker checks use an explicitly synthetic harness, never fake GPU evidence."""

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiscientist_mcp.scgpt_annotation.settings import admitted
from aiscientist_mcp.scgpt_annotation.worker import label_counts


@pytest.fixture
def fixture_harness(tmp_path, monkeypatch):
    import anndata as ad
    import numpy as np
    import pandas as pd

    query = tmp_path / "query.h5ad"
    ad.AnnData(
        np.ones((3, 2)), obs=pd.DataFrame(index=["AAA", "BBB", "CCC"]), var=pd.DataFrame(index=["RHO", "ARR3"])
    ).write_h5ad(query)
    model = tmp_path / "model"
    model.mkdir()
    (model / "best_model.pt").write_text("unit-test placeholder, not a model")
    (model / "vocab.json").write_text(json.dumps({"RHO": 1, "ARR3": 2}))
    (model / "id2type.json").write_text('{"0":"Rod","1":"Cone"}')
    (model / "dev_train_args.yml").write_text("fixture: true\n")
    harness = tmp_path / "fixture_harness"
    harness.mkdir()
    (harness / "step1_preprocess.py").write_text(
        "from pathlib import Path\nassert Path('../download/query.h5ad').is_file()\n"
        "assert Path('../download/reference_model/vocab.json').is_file()\n"
    )
    (harness / "step2_inference.py").write_text(
        "from pathlib import Path\np=Path('outdir_step2');p.mkdir()\n"
        "(p/'predictions.csv').write_text('barcode,predictions,confidence\\nAAA,Rod,0.98\\nBBB,Rod,0.95\\nCCC,Cone,0.81\\n')\n"
    )
    for key, value in {
        "AISCI_DATA_DIR": str(tmp_path / "data"),
        "AISCI_EXECUTOR": "inline",
        "SCGPT_DATASET": str(query),
        "SCGPT_MODEL_DIR": str(model),
        "SCGPT_REFACTOR": str(harness),
        "SCGPT_INFERENCE_PYTHON": sys.executable,
        "SCGPT_REQUIRE_CUDA": "0",
    }.items():
        monkeypatch.setenv(key, value)
    return query, model, harness


def test_label_summary_preserves_original_counts(tmp_path):
    path = tmp_path / "predictions.csv"
    path.write_text("barcode,prediction,confidence\na,Rod,0.9\nb,Cone,0.8\nc,Rod,0.7\nd,,0.1\n")
    assert label_counts(path) == (4, {"Rod": 2, "Cone": 1, "unknown": 1})
    path.write_text("barcode,wrong\na,Rod\n")
    with pytest.raises(ValueError, match="prediction column"):
        label_counts(path)


def test_real_mcp_worker_transport_with_fixture_harness(fixture_harness):
    from aiscientist_mcp.scgpt_annotation.server import server
    from mcp import Client

    async def check():
        async with Client(server, read_timeout_seconds=60) as client:
            assert {t.name for t in (await client.list_tools()).tools} == {"scgpt_annotate", "scgpt_job_status"}
            result = await client.call_tool("scgpt_annotate", {"analysis_id": "fixture"})
            assert not result.is_error, result
            result = result.structured_content
            assert result["state"] == "COMPLETED"
            assert result["n_cells"] == 3 and result["label_counts"] == {"Rod": 2, "Cone": 1}
            assert result["species_harmonization"]["rule"] == "none"
            assert Path(result["predictions_csv"]).is_file()
            polled = await client.call_tool(
                "scgpt_job_status", {"analysis_id": "fixture", "task_id": result["task_id"]}
            )
            assert polled.structured_content == result
            again = await client.call_tool("scgpt_annotate", {"analysis_id": "fixture"})
            assert again.structured_content == result
            assert (await client.call_tool("scgpt_annotate", {"dataset": "/outside/query.h5ad"})).is_error
            assert (await client.call_tool("scgpt_job_status", {"analysis_id": "../outside", "task_id": "x"})).is_error

    asyncio.run(check())


def test_dataset_and_model_roots(fixture_harness, tmp_path):
    query, model, _ = fixture_harness
    assert admitted("", "")[0] == query
    with pytest.raises((ValueError, FileNotFoundError)):
        admitted(str(tmp_path / "secret.h5ad"), "")
    (model / "vocab.json").unlink()
    (model / "vocab.json").symlink_to(query)
    with pytest.raises(ValueError, match="outside"):
        admitted("", "")


def test_gpu_slurm_request_is_explicit(fixture_harness, monkeypatch):
    from aiscientist_mcp.common import jobs
    from aiscientist_mcp.scgpt_annotation.server import scgpt_annotate

    monkeypatch.setenv("AISCI_EXECUTOR", "slurm")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="45678\n" if command[0] == "sbatch" else "PENDING\n")

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    result = scgpt_annotate(analysis_id="gpu-script")
    assert result["job_id"] == "45678" and result["state"] == "PENDING"
    script = Path(os.environ["AISCI_DATA_DIR"]) / "scgpt-annotation/gpu-script/jobs" / result["task_id"] / "job.sbatch"
    assert "--partition=gpu --gres=gpu:1" in script.read_text()
    assert sum(command[0] == "sbatch" for command in calls) == 1


def test_worker_failure_remains_failed(fixture_harness, monkeypatch):
    from aiscientist_mcp.scgpt_annotation.server import scgpt_annotate, scgpt_job_status

    _, _, harness = fixture_harness
    (harness / "step2_inference.py").write_text("raise RuntimeError('fixture inference failure')\n")
    monkeypatch.setenv("AISCI_EXECUTOR", "local")
    submitted = scgpt_annotate(analysis_id="failure")
    for _ in range(200):
        result = scgpt_job_status("failure", submitted["task_id"])
        if result["state"] == "FAILED":
            break
        time.sleep(0.1)
    assert result["state"] == "FAILED" and result["status"] == "error"


def test_stdio_lists_without_loading_gpu_libraries(fixture_harness):
    cmd = [
        sys.executable,
        "-m",
        "aiscientist_mcp.common.client",
        "--server",
        shlex.join([sys.executable, "-m", "aiscientist_mcp.scgpt_annotation.server"]),
        "tools",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    assert {tool["name"] for tool in json.loads(out.stdout)} == {"scgpt_annotate", "scgpt_job_status"}


def test_harmonization_retains_the_existing_threshold_rule(tmp_path):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from aiscientist_mcp.scgpt_annotation import worker

    names = [f"Gene{i}" for i in range(1100)]
    source = tmp_path / "mouse.h5ad"
    a = ad.AnnData(np.ones((2, len(names))), var=pd.DataFrame(index=names))
    a.raw = a.copy()
    a.write_h5ad(source)
    vocab = tmp_path / "vocab.json"
    vocab.write_text(json.dumps({name.upper(): i for i, name in enumerate(names)}))
    output, report = tmp_path / "harmonized.h5ad", tmp_path / "report.json"
    script = Path(worker.__file__).with_name("harmonize.py")
    subprocess.run([sys.executable, str(script), str(source), str(vocab), str(output), str(report)], check=True)
    record = json.loads(report.read_text())
    assert record["rule"] == "uppercase" and record["uppercase_match"] == 1100 and record["exact_match"] == 0
    assert "orthology is assumed by NAME" in record["assumption"]
    changed = ad.read_h5ad(output)
    assert list(changed.var_names) == [name.upper() for name in names]
    assert list(changed.raw.var_names) == list(changed.var_names)
    assert list(ad.read_h5ad(source).var_names) == names


def test_new_nullable_string_checkpoint_is_rewritten_without_value_changes(tmp_path):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from aiscientist_mcp.scgpt_annotation import worker

    values = np.array([[1, 2], [3, 4]], dtype=np.float32)
    original = tmp_path / "new-strings.h5ad"
    a = ad.AnnData(
        values,
        obs=pd.DataFrame(
            {"label": pd.array(["Rod", "Cone"], dtype="string")}, index=pd.Index(["cell1", "cell2"], dtype="string")
        ),
        var=pd.DataFrame(index=pd.Index(["RHO", "ARR3"], dtype="string")),
    )
    with ad.settings.override(allow_write_nullable_strings=True):
        a.write_h5ad(original)
    before = original.read_bytes()
    vocab = tmp_path / "vocab.json"
    vocab.write_text('{"RHO":0,"ARR3":1}')
    changed = tmp_path / "prepared.h5ad"
    report = tmp_path / "species_harmonization.json"
    subprocess.run(
        [
            sys.executable,
            str(Path(worker.__file__).with_name("harmonize.py")),
            str(original),
            str(vocab),
            str(changed),
            str(report),
        ],
        check=True,
    )
    actual = ad.read_h5ad(changed)
    np.testing.assert_array_equal(actual.X, values)
    assert actual.obs_names.tolist() == a.obs_names.tolist()
    assert actual.var_names.tolist() == a.var_names.tolist()
    assert actual.obs["label"].tolist() == a.obs["label"].tolist()
    assert original.read_bytes() == before
    compat = json.loads((tmp_path / "input_compatibility.json").read_text())
    assert set(compat["nullable_strings_to_object"]) >= {"obs.index", "var.index", "obs.label"}
    assert compat["expression_values_changed"] is False
    assert json.loads(report.read_text())["rule"] == "none"


def test_failed_inline_tool_exposes_pollable_task_identity(fixture_harness):
    from aiscientist_mcp.scgpt_annotation.server import server
    from mcp import Client

    _, _, harness = fixture_harness
    (harness / "step2_inference.py").write_text("raise RuntimeError('fixture inference failure')\n")

    async def check():
        async with Client(server, read_timeout_seconds=60) as client:
            reply = await client.call_tool("scgpt_annotate", {"analysis_id": "expected-failure"})
            assert reply.is_error
            message = "\n".join(item.text for item in reply.content if item.type == "text")
            assert "scgpt_annotate-" in message and "poll scgpt_job_status" in message

    asyncio.run(check())


def test_worker_provides_writable_numba_cache_without_home(fixture_harness, monkeypatch, tmp_path):
    from aiscientist_mcp.scgpt_annotation.server import scgpt_annotate

    monkeypatch.delenv("NUMBA_CACHE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "unavailable-home"))
    _, _, harness = fixture_harness
    step = harness / "step1_preprocess.py"
    step.write_text(
        step.read_text() + "import os\ncache=Path(os.environ['NUMBA_CACHE_DIR'])\n"
        "assert cache.is_dir()\n(cache/'write-probe').write_text('writable')\n"
    )
    result = scgpt_annotate(analysis_id="readonly-image")
    assert result["state"] == "COMPLETED"
    cache = Path(os.environ["AISCI_DATA_DIR"]) / "scgpt-annotation/readonly-image/work/numba-cache"
    assert (cache / "write-probe").read_text() == "writable"
    assert not (tmp_path / "unavailable-home").exists()
