"""Real literature MCP dispatch and workers against local synthetic fixtures."""

import asyncio
import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiscientist_mcp.literature import runtime
from aiscientist_mcp.literature.settings import ModelSettings, Settings, checked_input


@pytest.fixture
def endpoint():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            payload = {
                "resultList": {
                    "result": [
                        {
                            "title": "PRPF31 haploinsufficiency causes retinal disease",
                            "pmid": "12345678",
                            "pubYear": "2024",
                            "authorString": "Example A",
                            "journalTitle": "Synthetic fixture",
                        }
                    ]
                }
            }
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            calls.append(
                {
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                }
            )
            body = json.dumps({"choices": [{"message": {"content": '{"items":[]}'}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", calls
    finally:
        http.shutdown()
        thread.join()
        http.server_close()


def test_schema_types_and_unknown_arguments_are_rejected(monkeypatch):
    from aiscientist_mcp.literature import server as module
    from mcp import Client

    calls = []

    def submit(name, args, analysis_id, rerun):
        calls.append((name, args))
        return {"status": "ok"}

    monkeypatch.setattr(module, "submit", submit)

    async def check():
        async with Client(module.server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == runtime.TOOLS | {"literature_job_status"}
            assert all(tool.input_schema["additionalProperties"] is False for tool in tools.values())
            assert tools["search_corpus"].input_schema["properties"]["queries"]["maxItems"] == 6
            for args in (
                {"query": "PRPF31", "limit": "8"},
                {"query": "PRPF31", "unknown": True},
                {"query": "PRPF31", "limit": True},
                {"query": "PRPF31", "model_api_key": "secret"},
            ):
                assert (await client.call_tool("literature_search", args)).is_error
            assert not calls
            result = await client.call_tool("literature_search", {"query": "PRPF31", "limit": 3})
            assert not result.is_error
            assert calls[0][1]["limit"] == 3

    asyncio.run(check())


def test_real_stdio_and_inline_worker(endpoint, monkeypatch, tmp_path):
    url, calls = endpoint
    monkeypatch.setenv("LITERATURE_SEARCH_URL", url)
    env = dict(os.environ)
    base = [
        sys.executable,
        "-m",
        "aiscientist_mcp.common.client",
        "--server",
        f"{sys.executable} -m aiscientist_mcp.literature.server",
    ]
    args = json.dumps({"query": "PRPF31 haploinsufficiency", "analysis_id": "public"})
    completed = subprocess.run(
        [*base, "call", "literature_search", args], env=env, text=True, capture_output=True, timeout=60, check=True
    )
    result = json.loads(completed.stdout)
    assert result["status"] == "ok" and result["state"] == "COMPLETED"
    assert result["results"][0]["pmid"] == "12345678"
    assert result["analysis_id"] == "public" and result["task_id"]
    again = subprocess.run(
        [*base, "call", "literature_search", args], env=env, text=True, capture_output=True, timeout=60, check=True
    )
    assert json.loads(again.stdout)["task_id"] == result["task_id"] and len(calls) == 1
    task = runtime.task_dir(runtime.workspace("public"), result["task_id"])
    assert json.loads((task / "done.json").read_text())["state"] == "COMPLETED"
    assert "API_KEY" not in (task / "request.json").read_text()
    assert runtime.status("public", result["task_id"])["count"] == result["count"]


def test_missing_model_fails_without_completed_state(tmp_path):
    with pytest.raises(ValueError, match="configure LITERATURE_MODEL"):
        runtime.submit("deep_literature", {"question": "What is supported?"}, "no_model")
    ws = runtime.workspace("no_model")
    task_id = json.loads((ws / "active.json").read_text())["task_id"]
    result = runtime.status("no_model", task_id)
    assert result["status"] == "not_enabled" and result["state"] == "FAILED"
    assert json.loads((ws / "jobs" / task_id / "done.json").read_text())["state"] == "FAILED"


def test_paths_are_confined_and_corpus_is_read_only(tmp_path, monkeypatch):
    outside = tmp_path.parent / (tmp_path.name + "_outside")
    outside.mkdir()
    escape = tmp_path / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        checked_input(escape)
    with pytest.raises(ValueError, match="analysis_id"):
        runtime.workspace("../escape")
    ws = runtime.workspace("safe")
    (ws / "work" / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        runtime.workspace("safe")
    monkeypatch.setenv("LITERATURE_PAPERS", str(escape))
    with pytest.raises(ValueError, match="outside"):
        Settings.from_env()
    outside.rmdir()


def test_explicit_model_endpoint_and_key_file(endpoint, monkeypatch, tmp_path):
    assert ModelSettings.from_env().endpoint_pair() is None
    url, calls = endpoint
    key_file = tmp_path / "key"
    key_file.write_text("fixture-key")
    monkeypatch.setenv("LITERATURE_MODEL_ENDPOINT", url + "/v1")
    monkeypatch.setenv("LITERATURE_MODEL", "fixture-model")
    monkeypatch.setenv("LITERATURE_MODEL_API_KEY", "ignored-key")
    monkeypatch.setenv("LITERATURE_MODEL_API_KEY_FILE", str(key_file))
    cfg = ModelSettings.from_env()
    assert "fixture-key" not in repr(cfg) and "ignored-key" not in repr(cfg)
    assert cfg.judge()([{"role": "user", "content": "synthetic prompt"}]) == '{"items":[]}'
    assert calls[0]["authorization"] == "Bearer fixture-key"
    assert calls[0]["body"]["model"] == "fixture-model"
    monkeypatch.setenv("LITERATURE_MODEL_ENDPOINT", "https://user:password@example.invalid/v1")
    with pytest.raises(ValueError, match="without credentials"):
        ModelSettings.from_env()


def test_budget_cap_is_not_silently_ignored():
    with pytest.raises(ValueError, match="cannot be enforced"):
        runtime.submit("extract_haploinsufficient_genes", {"max_cost_usd": 1.0})


def test_same_relay_identity_reuses_analysis_across_ephemeral_ports(monkeypatch):
    monkeypatch.setenv("LITERATURE_MODEL", "bound-model")
    monkeypatch.setenv("LITERATURE_MODEL_ENDPOINT", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LITERATURE_MODEL_RELAY_PROTOCOL", "aisci-relay-v1")
    monkeypatch.setenv("LITERATURE_MODEL_IDENTITY", "a" * 64)
    calls = []

    def finish(_settings, _argv, task, _name):
        calls.append(task)
        runtime.write_json(task / "result.json", {"status": "ok", "answer": "synthetic"})
        runtime.write_json(task / "done.json", {"state": "COMPLETED"})

    monkeypatch.setattr(runtime.jobs, "submit", finish)
    first = runtime.submit("deep_literature", {"question": "synthetic"}, "relay-identity")
    monkeypatch.setenv("LITERATURE_MODEL_ENDPOINT", "http://127.0.0.1:9876/v1")
    second = runtime.submit("deep_literature", {"question": "synthetic"}, "relay-identity")
    assert second["task_id"] == first["task_id"] and len(calls) == 1
    assert "endpoint" not in runtime.fingerprint("deep_literature")["model"]
    monkeypatch.setenv("LITERATURE_MODEL_IDENTITY", "b" * 64)
    with pytest.raises(ValueError, match="another model"):
        runtime.submit("deep_literature", {"question": "synthetic"}, "relay-identity")
    assert len(calls) == 1


def test_declared_relay_requires_valid_identity(monkeypatch):
    monkeypatch.setenv("LITERATURE_MODEL_RELAY_PROTOCOL", "aisci-relay-v1")
    with pytest.raises(ValueError, match="stable SHA-256"):
        runtime.fingerprint("deep_literature")


def test_paperqa_pins_endpoint_and_keeps_its_answer_index_local(tmp_path, monkeypatch):
    from aiscientist_mcp.literature import deep

    root = tmp_path / "corpus"
    (root / "papers").mkdir(parents=True)
    (root / "index_pubmedbert" / "retigene_full_pubmedbert").mkdir(parents=True)
    monkeypatch.setenv("LITERATURE_CORPUS_ROOT", str(root))
    monkeypatch.setenv("LITERATURE_MODEL_ENDPOINT", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("LITERATURE_MODEL", "fixture-model")
    monkeypatch.setenv("LITERATURE_MODEL_API_KEY", "fixture-key")
    seen = []
    context = SimpleNamespace(
        context="Verified synthetic evidence",
        score=5,
        text=SimpleNamespace(doc=SimpleNamespace(citation="PMID 12345678")),
    )

    def ask(question, settings):
        seen.append(settings)
        return SimpleNamespace(answer="Synthetic cited answer", contexts=[context], status="success")

    monkeypatch.setitem(sys.modules, "paperqa", SimpleNamespace(ask=ask, Settings=SimpleNamespace))
    monkeypatch.setitem(
        sys.modules,
        "paperqa.settings",
        SimpleNamespace(
            AgentSettings=SimpleNamespace,
            AnswerSettings=SimpleNamespace,
            IndexSettings=SimpleNamespace,
            MultimodalOptions=SimpleNamespace(OFF="off"),
            ParsingSettings=SimpleNamespace,
        ),
    )
    ws = runtime.workspace("pqa")
    result = deep.run_paperqa({"question": "What is supported?"}, SimpleNamespace(workspace=ws))
    assert result["status"] == "ok" and result["n_contexts"] == 1
    settings = seen[0]
    assert settings.llm == settings.summary_llm == settings.agent.agent_llm == "openai/fixture-model"
    assert settings.llm_config["model_list"][0]["litellm_params"]["api_base"] == "http://127.0.0.1:9999/v1"
    assert settings.agent.index.sync_with_paper_directory is False
    assert settings.agent.index.index_directory == str(ws / "pqa_index")
    private_index = ws / "pqa_index" / "retigene_full_pubmedbert"
    assert private_index.is_dir() and not private_index.is_symlink()
    assert private_index.resolve() != root / "index_pubmedbert" / "retigene_full_pubmedbert"
    assert not (root / "index_pubmedbert" / "answers").exists()
    monkeypatch.setattr(sys.modules["paperqa"], "ask", lambda *args, **kwargs: SimpleNamespace(contexts=[], answer=""))
    assert deep.run_paperqa({"question": "Nothing matches"}, SimpleNamespace(workspace=ws))["status"] == "failed"


@pytest.mark.parametrize("hf_home", [True, False])
def test_offline_embedding_uses_hub_cache_layout(tmp_path, monkeypatch, hf_home):
    from aiscientist_mcp.literature.corpus import sentence_transformer_embed

    configured = tmp_path / "cache"
    hub = configured / "hub" if hf_home else configured
    hub.mkdir(parents=True)
    calls = []

    class Encoder:
        def __init__(self, name, **kwargs):
            calls.append((name, kwargs))

        def encode(self, values, **kwargs):
            return [[1.0]] * len(values)

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Encoder))
    encode = sentence_transformer_embed(configured)
    assert encode(["PRPF31"]) == [[1.0]]
    assert calls == [("NeuML/pubmedbert-base-embeddings", {"cache_folder": str(hub), "local_files_only": True})]


def test_failed_retry_keeps_original_attempt_evidence(tmp_path):
    args = {"question": "What evidence exists?"}
    with pytest.raises(ValueError, match="configure LITERATURE_MODEL"):
        runtime.submit("deep_literature", args, "retry")
    ws = runtime.workspace("retry")
    first_id = json.loads((ws / "active.json").read_text())["task_id"]
    first = runtime.task_dir(ws, first_id)
    evidence = (first / "done.json").read_text()
    with pytest.raises(ValueError, match="configure LITERATURE_MODEL"):
        runtime.submit("deep_literature", args, "retry")
    second_id = json.loads((ws / "active.json").read_text())["task_id"]
    assert first_id != second_id
    assert (first / "done.json").read_text() == evidence
    assert runtime.status("retry", first_id)["state"] == "FAILED"


def test_paperqa_explicit_model_output_cap(monkeypatch):
    from aiscientist_mcp.literature.deep import _llm_config

    monkeypatch.setenv("LITERATURE_MODEL_MAX_TOKENS", "2048")
    config = _llm_config("openai/fixture", "http://node:8000/v1", "off")
    assert config["model_list"][0]["litellm_params"]["max_tokens"] == 2048
    monkeypatch.setenv("LITERATURE_MODEL_MAX_TOKENS", "0")
    with pytest.raises(ValueError, match="positive"):
        _llm_config("openai/fixture", "http://node:8000/v1", "off")


def test_readonly_prebuilt_index_gets_private_writable_copy(tmp_path, monkeypatch):
    from aiscientist_mcp.literature.deep import _writable_index_root

    shared = tmp_path / "shared"
    index = shared / "retigene_full_pubmedbert" / "index"
    index.mkdir(parents=True)
    source = index / "meta.json"
    source.write_text('{"fixture":"prebuilt embeddings"}')
    source.chmod(0o444)
    monkeypatch.setenv("LITERATURE_CORPUS_ROOT", str(shared))
    workspace = tmp_path / "analysis"
    root = Path(_writable_index_root(shared, SimpleNamespace(workspace=workspace)))
    copied = root / "retigene_full_pubmedbert" / "index" / "meta.json"
    assert copied.read_text() == source.read_text() and copied.stat().st_ino != source.stat().st_ino
    assert copied.stat().st_mode & 0o200
    copied.write_text("private index update")
    (copied.parent / ".tantivy-writer.lock").write_text("local writer")
    assert source.read_text() == '{"fixture":"prebuilt embeddings"}'
    assert not (index / ".tantivy-writer.lock").exists()
    assert _writable_index_root(shared, SimpleNamespace(workspace=workspace)) == str(root)


def test_paperqa_breadth_changes_task_identity_without_persisting_credentials(monkeypatch):
    before = runtime.fingerprint("deep_literature")
    monkeypatch.setenv("LITERATURE_EVIDENCE_K", "8")
    after = runtime.fingerprint("deep_literature")
    assert before != after and after["paperqa_settings"]["EVIDENCE_K"] == "8"
    monkeypatch.setenv("LITERATURE_MODEL_API_KEY", "credential-not-configuration")
    assert runtime.fingerprint("deep_literature") == after
    assert "credential-not-configuration" not in json.dumps(after)


@pytest.mark.parametrize("override", [None, "/inside/custom-python"])
def test_prefixed_worker_uses_image_interpreter(endpoint, monkeypatch, tmp_path, override):
    url, _ = endpoint
    monkeypatch.setenv("LITERATURE_SEARCH_URL", url)
    wrapper = tmp_path / "container-prefix"
    expected = override or "python"
    wrapper.write_text(
        "#!/bin/sh\n"
        + f'[ "$1" = {shlex.quote(expected)} ] || exit 87\n'
        + "shift\n"
        + f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv("AISCI_JOB_PREFIX", str(wrapper))
    if override:
        monkeypatch.setenv("AISCI_JOB_PYTHON", override)
    else:
        monkeypatch.delenv("AISCI_JOB_PYTHON", raising=False)
    result = runtime.submit("literature_search", {"query": "synthetic PRPF31", "limit": 1}, "prefix")
    assert result["state"] == "COMPLETED" and result["status"] == "ok"
    task = runtime.task_dir(runtime.workspace("prefix"), result["task_id"])
    state = json.loads((task / "state.json").read_text())
    assert state["cmd"][:2] == [str(wrapper), expected]


def test_metadata_writer_ignores_stale_shared_temp_and_refuses_symlinks(tmp_path):
    victim = tmp_path / "unrelated"
    victim.write_text("unchanged")
    target = tmp_path / "request.json"
    target.with_suffix(".tmp").symlink_to(victim)
    runtime.write_json(target, {"complete": True})
    assert json.loads(target.read_text()) == {"complete": True}
    assert victim.read_text() == "unchanged"
    target.unlink()
    target.symlink_to(victim)
    with pytest.raises(ValueError, match="regular file"):
        runtime.write_json(target, {"changed": True})
    assert victim.read_text() == "unchanged"


def test_configured_data_root_symlink_is_rejected(monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "data-link"
    alias.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("AISCI_DATA_DIR", str(alias))
    with pytest.raises(ValueError, match="symlink"):
        runtime.workspace("case")
    assert not list(outside.iterdir())
