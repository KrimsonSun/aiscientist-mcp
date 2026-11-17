"""Operator corpus maintenance remains external, citation-grounded, and explicitly configured."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_query():
    spec = importlib.util.spec_from_file_location("operator_query", ROOT / "scripts/query_paperqa_index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_operator_source_selection_retains_literal_citations_and_snippet_bounds():
    query = load_query()
    retrieval = {
        "hits": [
            {
                "file": "12.pdf",
                "papers": [{"citation": "Paper PMID:12 DOI:10.1/fixture"}],
                "snippets": [{"text": "first passage " * 20}, {"text": "second"}],
            }
        ]
    }
    sources = query.source_blocks(retrieval, 24)
    assert sources == [
        {
            "id": "S1",
            "citation": "Paper PMID:12 DOI:10.1/fixture",
            "file": "12.pdf",
            "text": "first passage first pass...",
        }
    ]


def test_operator_answer_requires_explicit_endpoint_and_does_not_use_public_defaults(monkeypatch):
    query = load_query()
    monkeypatch.delenv("LITERATURE_MODEL_ENDPOINT", raising=False)
    monkeypatch.delenv("LITERATURE_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-the-selected-credential")
    with pytest.raises(RuntimeError, match="requires LITERATURE_MODEL_ENDPOINT"):
        query.chat_completion(SimpleNamespace(), [])


def test_operator_answer_uses_key_file_and_redacts_provider_errors(tmp_path, monkeypatch):
    import urllib.error

    query = load_query()
    secret = tmp_path / "api.key"
    secret.write_text("private-token")
    monkeypatch.setenv("LITERATURE_MODEL_ENDPOINT", "http://operator.invalid/v1")
    monkeypatch.setenv("LITERATURE_MODEL", "chosen-model")
    monkeypatch.setenv("LITERATURE_MODEL_API_KEY_FILE", str(secret))
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            raise urllib.error.HTTPError(request.full_url, 401, "private-provider-message", {}, None)

    monkeypatch.setattr(query.urllib.request, "build_opener", lambda *handlers: Opener())
    args = SimpleNamespace(question="public research question", answer_snippet_chars=50, max_tokens=700)
    with pytest.raises(RuntimeError) as exc:
        query.chat_completion(args, [{"id": "S1", "citation": "PMID:12", "file": "12.pdf", "text": "evidence"}])
    assert str(exc.value) == "Configured model returned HTTP 401"
    request, _ = calls[0]
    assert request.headers["Authorization"] == "Bearer private-token"
    body = json.loads(request.data)
    assert body["model"] == "chosen-model" and body["temperature"] == 0.1
    assert "PMID:12" in body["messages"][1]["content"]
