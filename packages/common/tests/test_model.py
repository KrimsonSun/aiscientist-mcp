"""Explicit, prefix-scoped model requests without credential leaks or redirects."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from aiscientist_mcp.common.model import ModelSettings


@pytest.fixture
def chat_endpoint():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body, self.headers.get("Authorization")))
            if self.path.startswith("/redirect/"):
                self.send_response(307)
                self.send_header("Location", "/other/chat/completions")
                self.end_headers()
                return
            if self.path.startswith("/failure/"):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"sensitive server diagnostic")
                return
            body = json.dumps({"choices": [{"message": {"content": '[{"phrase":"night blindness"}]'}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_prefix_key_file_priority_and_json_array(chat_endpoint, tmp_path, monkeypatch):
    endpoint, calls = chat_endpoint
    key = tmp_path / "key"
    key.write_text("file-secret\n")
    monkeypatch.setenv("TEST_MODEL_ENDPOINT", endpoint + "/v1")
    monkeypatch.setenv("TEST_MODEL", "local-clinical-model")
    monkeypatch.setenv("TEST_MODEL_API_KEY_FILE", str(key))
    monkeypatch.setenv("TEST_MODEL_API_KEY", "ignored-secret")
    settings = ModelSettings.from_env("TEST")
    answer = settings.judge(max_tokens=10000, json_object=False)([{"role": "user", "content": "clinical text"}])
    assert json.loads(answer)[0]["phrase"] == "night blindness"
    assert calls[0][0] == "/v1/chat/completions"
    assert calls[0][1]["model"] == "local-clinical-model" and calls[0][1]["max_tokens"] == 10000
    assert "response_format" not in calls[0][1]
    assert calls[0][2] == "Bearer file-secret"
    assert "secret" not in repr(settings)
    settings.judge()([])
    assert calls[-1][1]["response_format"] == {"type": "json_object"}


def test_unconfigured_model_has_no_network_default(monkeypatch):
    monkeypatch.delenv("TEST_MODEL", raising=False)
    monkeypatch.delenv("TEST_MODEL_ENDPOINT", raising=False)
    settings = ModelSettings.from_env("TEST")
    assert settings.endpoint_pair() is None
    with pytest.raises(ValueError, match="configure TEST_MODEL_ENDPOINT and TEST_MODEL"):
        settings.judge()


def test_redirect_and_http_failures_never_echo_credentials(chat_endpoint):
    endpoint, calls = chat_endpoint
    with pytest.raises(ValueError, match=r"redirects|HTTP 307"):
        ModelSettings(endpoint + "/redirect", "test", key="private-secret").judge()([])
    assert len(calls) == 1
    with pytest.raises(ValueError, match=r"^model endpoint returned HTTP 401$"):
        ModelSettings(endpoint + "/failure", "test", key="private-secret").judge()([])


@pytest.mark.parametrize(
    "endpoint", ["https://name:secret@example.org/v1", "https://example.org?key=secret", "file:///tmp/key"]
)
def test_endpoint_rejects_embedded_credentials(endpoint, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_ENDPOINT", endpoint)
    with pytest.raises(ValueError):
        ModelSettings.from_env("TEST")


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_timeout_is_positive_and_finite(timeout, monkeypatch):
    monkeypatch.setenv("TEST_MODEL_TIMEOUT", timeout)
    with pytest.raises(ValueError, match="positive and finite"):
        ModelSettings.from_env("TEST")
