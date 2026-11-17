"""Fixtures: the fake visionkb-api of ``vkg_fake`` on 127.0.0.1 and the environment pointing at it."""

import threading
from http.server import ThreadingHTTPServer

import pytest
from vkg_fake import TOKEN, Handler


@pytest.fixture(scope="session")
def fake_api():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.log = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def api_env(fake_api, tmp_path, monkeypatch):
    """Point the server at the fake API with a token file; returns (server, env dict)."""
    tok = tmp_path / "token"
    tok.write_text(f"# read token\n{TOKEN}\n")
    env = {"VISIONKB_API_URL": f"http://127.0.0.1:{fake_api.server_address[1]}", "VISIONKB_API_TOKEN_FILE": str(tok),
           "VISIONKB_TIMEOUT_S": "10"}  # fmt: skip
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    fake_api.log.clear()
    return fake_api, env
