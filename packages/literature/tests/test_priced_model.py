"""Real HTTP calls carry priced usage into the unchanged curation pilot."""

import importlib.util
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from aiscientist_mcp.common.model import ModelSettings
from aiscientist_mcp.literature import extract as hi
from aiscientist_mcp.literature.priced_model import PROTOCOL, PricedModel


@contextmanager
def endpoint(callback):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            code, payload = callback(body)
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def payload(text='{"items": []}', cost=0.01):
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "aiscientist_usage": {"version": 1, "billed": True, "cost_usd": cost},
    }


def test_concurrent_readers_stop_after_cap_without_queued_paid_calls(monkeypatch):
    monkeypatch.setenv("LITERATURE_MODEL_USAGE_PROTOCOL", PROTOCOL)
    meter = hi.Meter()
    with endpoint(lambda _: (200, payload(cost=0.03))) as (url, calls):
        judge = PricedModel(ModelSettings(url, "fixture"), meter, max_cost_usd=0.02).judge()

        def read(_):
            try:
                return judge([{"role": "user", "content": "synthetic"}])
            except hi.ScanStopped:
                return "stopped"

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(read, range(8)))
    assert len(calls) == 1 and results.count("stopped") == 7
    assert meter.public() == {"calls": 1, "input_tokens": 100, "output_tokens": 20, "cost_usd": 0.03}


@pytest.mark.parametrize("cost", [None, -1, "0.01", float("inf")])
def test_missing_or_invalid_price_stops_and_is_never_reported_as_free(monkeypatch, cost):
    monkeypatch.setenv("LITERATURE_MODEL_USAGE_PROTOCOL", PROTOCOL)
    meter = hi.Meter()
    with endpoint(lambda _: (200, payload(cost=cost))) as (url, calls):
        judge = PricedModel(ModelSettings(url, "fixture"), meter, max_cost_usd=1).judge()
        for _ in range(2):
            with pytest.raises(hi.ScanStopped, match="known price"):
                judge([])
    assert len(calls) == 1 and meter.public()["cost_usd"] is None and meter.calls == 1


def test_billed_invalid_output_is_counted_before_error(monkeypatch):
    monkeypatch.setenv("LITERATURE_MODEL_USAGE_PROTOCOL", PROTOCOL)
    reply = payload(cost=0.04)
    reply.pop("choices")
    reply["error"] = {"message": "invalid JSON", "type": "model"}
    meter = hi.Meter()
    with endpoint(lambda _: (502, reply)) as (url, _):
        judge = PricedModel(ModelSettings(url, "fixture"), meter).judge()
        with pytest.raises(ValueError, match="HTTP 502"):
            judge([])
    assert meter.calls == 1 and meter.cost_usd == 0.04


def test_gateway_budget_refusal_is_not_billed(monkeypatch):
    monkeypatch.setenv("LITERATURE_MODEL_USAGE_PROTOCOL", PROTOCOL)
    meter = hi.Meter()
    with (
        endpoint(lambda _: (403, {"error": {"type": "stop"}})) as (url, _),
        pytest.raises(hi.ScanStopped, match="gateway stopped"),
    ):
        PricedModel(ModelSettings(url, "fixture"), meter, max_cost_usd=1).judge()([])
    assert meter.calls == 0


def test_gateway_failure_without_accounting_does_not_retry_as_free(monkeypatch):
    monkeypatch.setenv("LITERATURE_MODEL_USAGE_PROTOCOL", PROTOCOL)
    meter = hi.Meter()
    with endpoint(lambda _: (502, {"error": {"type": "transport"}})) as (url, calls):
        judge = PricedModel(ModelSettings(url, "fixture"), meter, max_cost_usd=1).judge()
        for _ in range(2):
            with pytest.raises(hi.ScanStopped):
                judge([])
    assert len(calls) == 1 and meter.public()["cost_usd"] is None


def test_positive_cap_pilot_stops_after_real_http_usage(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "curation_fixtures", Path(__file__).with_name("test_extract_haploinsufficient_genes.py")
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)

    root = fixtures._corpus(tmp_path)
    cache = root / "mechanism_cache"
    # Expand the synthetic fixture to 40 passages so its five-passage pilot is
    # meaningfully smaller than the complete scan.
    chunks = [json.loads(row) for row in (cache / "chunks.jsonl").read_text().splitlines()]
    sentences = [json.loads(row) for row in (cache / "sentences.jsonl").read_text().splitlines()]
    first = chunks[0]
    group = [row for row in sentences if row["pmid"] == first["pmid"]]
    expanded_chunks, expanded_sentences = [], []
    for i in range(40):
        pmid = str(80000000 + i)
        expanded_chunks.append({**first, "pmid": pmid})
        expanded_sentences.extend({**row, "pmid": pmid, "sid": f"{pmid}:0:{row['sent']}"} for row in group)
    (cache / "chunks.jsonl").write_text("\n".join(map(json.dumps, expanded_chunks)) + "\n")
    (cache / "sentences.jsonl").write_text("\n".join(map(json.dumps, expanded_sentences)) + "\n")
    ctx = fixtures._ctx(tmp_path, monkeypatch, root)
    monkeypatch.setenv("LITERATURE_MODEL_USAGE_PROTOCOL", PROTOCOL)
    monkeypatch.setenv("LITERATURE_MODEL", "fixture")
    read = []
    model = fixtures._fake_model(read)
    with endpoint(lambda body: (200, payload(model(body["messages"])))) as (url, calls):
        monkeypatch.setenv("LITERATURE_MODEL_ENDPOINT", url)
        result = hi.run_extract(
            {
                "max_cost_usd": 0.10,
                "n_requests": 1,
                "k_per_request": 40,
                "max_passages": 40,
                "keyword_round": 0,
                "term_round": 0,
                "k_per_gene": 0,
                "check_unread": 0,
                "pilot_fraction": 0.01,
                "concurrency": 8,
            },
            ctx,
            embed_fn=fixtures._embed,
            log=lambda _: None,
        )
    assert result["status"] == "failed" and result["over_budget"] and result["stopped"]
    assert result["n_passages"] == 40 and result["n_judged"] == len(read) == 5
    assert result["pilot"] == {"passages": 5, "read": 5, "calls": 5, "cost_usd": 0.05, "estimated_total_usd": 0.8}
    assert result["api_usage"]["calls"] == len(calls) == 6
    assert result["api_usage"]["cost_usd"] == 0.06
