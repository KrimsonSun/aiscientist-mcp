"""Native schema, real stdio/worker, command and inter-server MCP tests."""

import asyncio
import json
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from aiscientist_mcp.common import tasks
from aiscientist_mcp.phenotype_diagnosis import runtime, server
from aiscientist_mcp.phenotype_diagnosis.settings import Settings
from mcp import Client


def cli(tool, arguments):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aiscientist_mcp.common.client",
            "--server",
            f"{shlex.quote(sys.executable)} -m aiscientist_mcp.phenotype_diagnosis.server",
            "call",
            tool,
            json.dumps(arguments),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)


def test_schema_and_strict_dispatch(monkeypatch):
    called = []
    monkeypatch.setattr(server, "submit", lambda *args: called.append(args) or {"status": "ok"})

    async def check():
        async with Client(server.server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == runtime.TOOLS | {"phenotype_job_status"}
            assert tools["map_phenotype_to_hpo"].input_schema["properties"]["text"]["type"] == "string"
            assert tools["run_lirical"].input_schema["properties"]["hpo_terms"]["items"]["type"] == "string"
            assert all(t.input_schema["additionalProperties"] is False for t in tools.values())
            for tool, args in [
                ("map_phenotype_to_hpo", {"text": "night blindness", "k": "8"}),
                ("map_phenotype_to_hpo", {"text": "night blindness", "k": True}),
                ("map_phenotype_to_hpo", {"text": "night blindness", "model_api_key": "secret"}),
                ("run_lirical", {"hpo_terms": [123]}),
                ("run_lirical", {"hpo_terms": ["HP:0000662"], "data_dir": "/unconfigured"}),
                ("diagnose_disease", {"hpo_terms": [], "candidate_genes": "CRB1"}),
            ]:
                assert (await client.call_tool(tool, args)).is_error
            assert called == []
            assert not (await client.call_tool("map_phenotype_to_hpo", {"text": "night blindness"})).is_error
            assert called[0][1] == {"text": "night blindness", "k": 8}

    asyncio.run(check())


def test_stdio_maps_without_model_and_reuses_job(tmp_path):
    args = {"text": "Patient has night blindness", "analysis_id": "lexical"}
    first = cli("map_phenotype_to_hpo", args)
    assert first["status"] == "ok" and first["state"] == "COMPLETED"
    assert first["mode"] == "lexical_only" and "HP:0000662" in first["hpo_terms"]
    assert cli("map_phenotype_to_hpo", args)["task_id"] == first["task_id"]
    assert (
        cli("phenotype_job_status", {"analysis_id": "lexical", "task_id": first["task_id"]})["hpo_terms"]
        == first["hpo_terms"]
    )
    unsafe = cli(
        "map_phenotype_to_hpo",
        {"text": "Mother has retinitis pigmentosa but patient denies night blindness", "analysis_id": "negation"},
    )
    assert unsafe["mode"] == "needs_llm" and unsafe["hpo_terms"] == []


def test_model_identity_ignores_only_trusted_ephemeral_relay_address(monkeypatch):
    monkeypatch.setenv("PHENOTYPE_MODEL", "selected")
    monkeypatch.setenv("PHENOTYPE_MODEL_ENDPOINT", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("PHENOTYPE_MODEL_RELAY_PROTOCOL", "aisci-relay-v1")
    monkeypatch.setenv("PHENOTYPE_MODEL_IDENTITY", "a" * 64)
    first = Settings.from_env().identity()
    monkeypatch.setenv("PHENOTYPE_MODEL_ENDPOINT", "http://127.0.0.1:9876/v1")
    assert Settings.from_env().identity() == first
    monkeypatch.setenv("PHENOTYPE_MODEL_IDENTITY", "b" * 64)
    assert Settings.from_env().identity() != first
    monkeypatch.setenv("PHENOTYPE_MODEL_IDENTITY", "")
    with pytest.raises(ValueError, match="stable SHA-256"):
        Settings.from_env().identity()


def staged_lirical(tmp_path, monkeypatch, *, fail=False, omit_tsv=False):
    reference = tmp_path / "reference"
    reference.mkdir(exist_ok=True)
    (reference / "hgnc_complete_set.txt").write_text("symbol\tentrez_id\nABCA4\t24\n")
    script = tmp_path / "fake_lirical.py"
    script.write_text(f"""
import sys
from pathlib import Path
args=sys.argv[1:]
assert args[0] == "prioritize"
assert "HP:0000662" in args[args.index("-p")+1]
assert "--assembly" not in args
if {fail!r}:
    print("synthetic command failure", file=sys.stderr)
    sys.exit(7)
if not {omit_tsv!r}:
    out=Path(args[args.index("-o")+1])/(args[args.index("-x")+1]+".tsv")
    out.write_text("rank\\tdiseaseName\\tdiseaseCurie\\tposttestprob\\tentrezGeneId\\n1\\tSynthetic retinal disease\\tOMIM:000000\\t82.5%\\tNCBIGene:24\\n")
""")
    monkeypatch.setenv("LIRICAL_DATA_DIR", str(reference))
    monkeypatch.setenv("LIRICAL_COMMAND", f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}")


def test_real_command_path_and_diagnosis_keep_probability(tmp_path, monkeypatch):
    staged_lirical(tmp_path, monkeypatch)
    result = cli("run_lirical", {"hpo_terms": ["HP:0000662"], "analysis_id": "ranking"})
    assert result["state"] == "COMPLETED" and result["mode"] == "phenotype_only"
    assert result["candidates"][0]["posttest_prob"] == 0.825
    assert result["candidates"][0]["gene"] == "ABCA4"
    assert Path(result["tsv_path"]).is_relative_to(tmp_path / "outputs" / "phenotype-diagnosis" / "ranking")
    diagnosis = cli("diagnose_disease", {"hpo_terms": ["HP:0000662"], "analysis_id": "diagnosis"})
    assert diagnosis["mode"] == "lirical_only" and diagnosis["top"]["posttest_prob"] == 0.825
    assert diagnosis["top"]["final_score"] == 0.825


def test_failed_command_and_missing_deployment_are_failed_jobs(tmp_path, monkeypatch):
    missing = runtime.submit("run_lirical", {"hpo_terms": ["HP:0000662"]}, "missing")
    assert missing["state"] == "FAILED"
    staged_lirical(tmp_path, monkeypatch, fail=True)
    result = runtime.submit("run_lirical", {"hpo_terms": ["HP:0000662"]}, "failure")
    assert result["state"] == "FAILED" and result["status"] == "error"
    directory = tasks.task_dir(tasks.workspace(runtime.CONFIG, "failure"), result["task_id"])
    assert json.loads((directory / "done.json").read_text())["state"] == "FAILED"
    assert "synthetic command failure" in result["error"]


def test_old_tsv_cannot_turn_a_failed_rerun_into_success(tmp_path, monkeypatch):
    staged_lirical(tmp_path, monkeypatch)
    first = runtime.submit("run_lirical", {"hpo_terms": ["HP:0000662"]}, "stale")
    assert first["state"] == "COMPLETED"
    staged_lirical(tmp_path, monkeypatch, omit_tsv=True)
    rerun = runtime.submit("run_lirical", {"hpo_terms": ["HP:0000662"]}, "stale", rerun=True)
    assert rerun["state"] == "FAILED" and "no TSV" in rerun["error"]


def test_inputs_and_outputs_are_confined(tmp_path, monkeypatch):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    try:
        vcf = outside / "case.vcf"
        vcf.write_text("##fileformat=VCFv4.2\n")
        link = tmp_path / "linked.vcf"
        link.symlink_to(vcf)
        with pytest.raises(ValueError, match="read roots"):
            runtime.submit("run_lirical", {"hpo_terms": ["HP:0000662"], "vcf_path": str(link)})
        monkeypatch.setenv("PHENOTYPE_HPO_LEXICON", str(vcf))
        with pytest.raises(ValueError, match="read roots"):
            Settings.from_env()
        monkeypatch.delenv("PHENOTYPE_HPO_LEXICON")
        ws = tasks.workspace(runtime.CONFIG, "linked")
        (ws / "artifacts").symlink_to(outside, target_is_directory=True)
        result = runtime.submit("map_phenotype_to_hpo", {"text": "night blindness"}, "linked")
        assert result["state"] == "FAILED"
    finally:
        for path in outside.iterdir():
            path.unlink()
        outside.rmdir()


def test_real_model_array_response_and_secret_free_job(tmp_path, monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(
                (self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
            reply = [{"phrase": "night blindness", "negated": True, "source": "patient"}]
            payload = json.dumps({"choices": [{"message": {"content": json.dumps(reply)}}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    key = tmp_path / "key"
    key.write_text("synthetic-private-key")
    monkeypatch.setenv("PHENOTYPE_MODEL_ENDPOINT", f"http://127.0.0.1:{http.server_port}/v1")
    monkeypatch.setenv("PHENOTYPE_MODEL", "fixture-model")
    monkeypatch.setenv("PHENOTYPE_MODEL_API_KEY_FILE", str(key))
    try:
        result = cli("map_phenotype_to_hpo", {"text": "Patient denies night blindness", "analysis_id": "model"})
        assert result["mode"] == "llm" and result["excluded_hpo"] == ["HP:0000662"]
        assert calls[0][0] == "Bearer synthetic-private-key"
        assert "response_format" not in calls[0][1]
        for path in (tmp_path / "outputs").rglob("*"):
            if path.is_file():
                assert "synthetic-private-key" not in path.read_text()
    finally:
        http.shutdown()
        thread.join()
        http.server_close()


def test_real_literature_mcp_bridge_polls_and_grades(tmp_path, monkeypatch):
    fake = tmp_path / "fixture_literature.py"
    fake.write_text("""
from mcp.server import MCPServer
from typing import Any
server=MCPServer("synthetic-literature")
@server.tool(structured_output=True)
def deep_literature(question: str, analysis_id: str) -> dict[str, Any]:
    assert "CRB1" in question and "Nyctalopia" in question
    assert analysis_id.startswith("phenotype-")
    return {"state":"RUNNING", "task_id":"synthetic-task"}
@server.tool(structured_output=True)
def literature_job_status(analysis_id: str, task_id: str) -> dict[str, Any]:
    return {"status":"ok", "state":"COMPLETED", "answer":"Independent families and knockout support. STRONG.", "contexts":[{"citation":"A PMID: 10508521", "summary":"12 unrelated families"},{"citation":"B PMID: 12915482", "summary":"knockout mouse model"}]}
if __name__ == "__main__": server.run("stdio")
""")
    monkeypatch.setenv("PHENOTYPE_LITERATURE_COMMAND", f"{shlex.quote(sys.executable)} {shlex.quote(str(fake))}")
    result = cli(
        "diagnose_disease", {"hpo_terms": ["HP:0000662"], "candidate_genes": ["CRB1"], "analysis_id": "evidence"}
    )
    assert result["status"] == "ok" and result["mode"] == "literature_only"
    assert result["top"]["gene"] == "CRB1" and result["top"]["posttest_prob"] is None
    assert result["top"]["evidence_tier"] == "STRONG"
    assert not (tmp_path / "outputs" / "literature").exists()


def test_real_stdio_large_literature_resource_is_complete_before_grading(tmp_path, monkeypatch):
    fake = tmp_path / "large_peer.py"
    fake.write_text("""
import base64, hashlib, json
from mcp.server import MCPServer
from typing import Any
server=MCPServer("synthetic-large-literature")
DATA=json.dumps({"status":"ok", "answer":"Independent families and knockout support. STRONG.",
 "contexts":[{"citation":"A PMID: 10508521", "summary":"12 unrelated families " * 700},
             {"citation":"B PMID: 12915482", "summary":"knockout mouse model " * 700}]}).encode()
@server.tool(structured_output=True)
def deep_literature(question: str, analysis_id: str) -> dict[str, Any]:
    return {"state":"COMPLETED", "task_id":"large-task", "response_omitted":True,
            "full_result_uri":f"literature://results/{analysis_id}/large-task/0"}
@server.resource("literature://results/{analysis_id}/{task_id}/{offset}")
def page(analysis_id: str, task_id: str, offset: str) -> str:
    start=int(offset); chunk=DATA[start:start+8192]; end=start+len(chunk)
    return json.dumps({"analysis_id":analysis_id,"task_id":task_id,"offset":start,
      "total_bytes":len(DATA),"sha256":hashlib.sha256(DATA).hexdigest(),"encoding":"base64",
      "data":base64.b64encode(chunk).decode(),"next_offset":end if end<len(DATA) else None})
if __name__ == "__main__": server.run("stdio")
""")
    monkeypatch.setenv("PHENOTYPE_LITERATURE_COMMAND", f"{shlex.quote(sys.executable)} {shlex.quote(str(fake))}")
    result = cli(
        "diagnose_disease",
        {"hpo_terms": ["HP:0000662"], "candidate_genes": ["CRB1"], "analysis_id": "complete-evidence"},
    )
    if result.get("response_omitted"):
        result = json.loads(Path(result["full_result"]).read_text())
    assert result["status"] == "ok" and result["mode"] == "literature_only"
    assert result["top"]["evidence_tier"] == "STRONG"
    assert {e["pmid"] for e in result["literature"][0]["evidence"]} == {"10508521", "12915482"}
    assert not (tmp_path / "outputs" / "literature").exists()


def test_native_wire_preview_preserves_full_differential(tmp_path, monkeypatch):
    # Exercise the public status tool with a realistically large completed worker result.
    ws = tasks.workspace(runtime.CONFIG, "large")
    directory = tasks.task_dir(ws, "large-result")
    directory.mkdir(parents=True)
    result = {
        "status": "ok",
        "n_candidates": 8621,
        "candidates": [
            {"disease_name": "Synthetic candidate " + str(i), "posttest_prob": 0.4, "variants": "A" * 2000}
            for i in range(8621)
        ],
    }
    (directory / "result.json").write_text(json.dumps(result))
    (directory / "state.json").write_text(json.dumps({"state": "COMPLETED", "job_id": "inline"}))
    (directory / "done.json").write_text(json.dumps({"state": "COMPLETED"}))

    async def check():
        async with Client(server.server) as client:
            response = await client.call_tool(
                "phenotype_job_status", {"analysis_id": "large", "task_id": "large-result"}
            )
            assert not response.is_error
            assert len(response.model_dump_json().encode()) < 24000
            preview = response.structured_content
            assert preview["n_candidates"] == 8621 and preview["state"] == "COMPLETED"
            assert preview["response_omitted"] and len(preview["candidates"]) <= 5
            full = json.loads(Path(preview["full_result"]).read_text())
            assert len(full["candidates"]) == 8621 and len(full["candidates"][0]["variants"]) == 2000

    asyncio.run(check())


def test_server_model_output_cap_is_part_of_request_identity(monkeypatch):
    from aiscientist_mcp.phenotype_diagnosis.settings import model_max_tokens

    assert model_max_tokens() == 10000
    before = Settings.from_env().identity()
    monkeypatch.setenv("PHENOTYPE_MODEL_MAX_TOKENS", "2048")
    assert model_max_tokens() == 2048 and before != Settings.from_env().identity()
    monkeypatch.setenv("PHENOTYPE_MODEL_MAX_TOKENS", "-1")
    with pytest.raises(ValueError, match="positive"):
        Settings.from_env().identity()


def test_literature_peer_model_and_corpus_are_part_of_task_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("PHENOTYPE_LITERATURE_COMMAND", "literature-mcp")
    monkeypatch.setenv("LITERATURE_CORPUS_ROOT", str(tmp_path))
    monkeypatch.setenv("LITERATURE_MODEL_ENDPOINT", "http://localhost:1234/v1")
    monkeypatch.setenv("LITERATURE_MODEL", "one")
    before = Settings.from_env().identity()
    monkeypatch.setenv("LITERATURE_MODEL_API_KEY", "never-persist-this")
    assert before == Settings.from_env().identity()
    assert "never-persist-this" not in json.dumps(before)
    monkeypatch.setenv("LITERATURE_MODEL", "two")
    assert before != Settings.from_env().identity()
