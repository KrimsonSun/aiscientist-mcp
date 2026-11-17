"""Unit tests of settings and the HTTP client against the fake loopback API (no external network)."""

import json
import socket

import pytest
from aiscientist_mcp.visionkb_kg import api
from aiscientist_mcp.visionkb_kg.settings import ClientError, Settings
from vkg_fake import RELEASE, TOKEN, trim_note


def cfg(url, token_file=None, timeout=5.0):
    return Settings(api_url=url, token_file=token_file, timeout_s=timeout)


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VISIONKB_API_URL", "http://127.0.0.1:8770/v1/")
    monkeypatch.setenv("VISIONKB_API_TOKEN_FILE", str(tmp_path / "t"))
    monkeypatch.setenv("VISIONKB_TIMEOUT_S", "7.5")
    s = Settings.from_env()
    assert (s.base_url(), s.timeout_s, s.token_file) == ("http://127.0.0.1:8770", 7.5, tmp_path / "t")
    monkeypatch.delenv("VISIONKB_TIMEOUT_S")
    assert Settings.from_env().timeout_s == 30.0
    for bad in ("abc", "0", "-1", "601"):
        monkeypatch.setenv("VISIONKB_TIMEOUT_S", bad)
        with pytest.raises(ClientError) as e:
            Settings.from_env()
        assert e.value.code == "invalid_setting"


@pytest.mark.parametrize(
    ("url", "code"),
    [("", "not_configured"), ("ftp://h/x", "invalid_setting"), ("http://", "invalid_setting"),
     ("http://u:p@h:1", "invalid_setting"), ("http://h:1/?a=1", "invalid_setting")],
)  # fmt: skip
def test_bad_url(url, code):
    with pytest.raises(ClientError) as e:
        cfg(url).base_url()
    assert e.value.code == code


def test_token_file(tmp_path):
    p = tmp_path / "tok"
    assert cfg("http://h").token() is None
    for text, want in ((f"\n# comment\n {TOKEN} \n", TOKEN), ("", None), ("a b\n", None)):
        p.write_text(text)
        if want:
            assert cfg("http://h", p).token() == want
        else:
            with pytest.raises(ClientError) as e:
                cfg("http://h", p).token()
            assert e.value.code == "token_unreadable"
            assert TOKEN not in e.value.message
    with pytest.raises(ClientError) as e:
        cfg("http://h", tmp_path / "missing").token()
    assert e.value.code == "token_unreadable"


def test_curie_and_lists():
    assert api.check_curie(" HGNC:10012 ", "x") == "HGNC:10012"
    assert api.check_curie("visionkb:e-" + "a" * 40, "x").startswith("visionkb:e-")
    for bad in ("", "RHO", "HGNC:1/../x", "HGNC:1 2", "HGNC:1?x", "a:%2F", ":1"):
        with pytest.raises(ClientError):
            api.check_curie(bad, "x")
    assert api.split_list("RHO, GNAT1 ,,PDE6B") == ["RHO", "GNAT1", "PDE6B"]
    assert api.split_list([" RHO ", ""]) == ["RHO"]
    assert api.split_list(None) == []


def test_get_ok_sends_token_and_drops_none(fake_api, tmp_path):
    p = tmp_path / "tok"
    p.write_text(TOKEN)
    fake_api.log.clear()
    url = f"http://127.0.0.1:{fake_api.server_address[1]}"
    r = api.get(cfg(url, p), "/gene-sets", {"entity": "CL:0000604", "kind": None, "descendants": False, "x": []})
    assert r["status"] == "ok" and r["kg_release"] == RELEASE and r["http_status"] == 200
    assert r["request"] == {"path": "/v1/gene-sets", "params": {"entity": "CL:0000604", "descendants": "false"}}
    assert r["warnings"] == ["score = max pct_expressing over supporting edges"]
    hit = fake_api.log[-1]
    assert hit["auth"] == f"Bearer {TOKEN}"
    assert hit["query"] == {"entity": ["CL:0000604"], "descendants": ["false"]}
    assert TOKEN not in json.dumps(r)


def test_get_repeated_params(fake_api):
    url = f"http://127.0.0.1:{fake_api.server_address[1]}"
    fake_api.log.clear()
    api.get(cfg(url), "/health", {"genes": ["RHO", "GNAT1"]}, auth=False)
    assert fake_api.log[-1]["query"] == {"genes": ["RHO", "GNAT1"]}


def test_http_errors_are_reported(fake_api, tmp_path):
    url = f"http://127.0.0.1:{fake_api.server_address[1]}"
    r = api.get(cfg(url), "/release")  # no token configured
    assert (r["status"], r["http_status"], r["error"]["code"]) == ("error", 401, "unauthorized")
    assert r["kg_release"] == RELEASE
    assert r["warnings"][0].startswith("VISIONKB_API_TOKEN_FILE is not set")
    r = api.get(cfg(url), "/not-json", auth=False)
    assert (r["status"], r["error"]["code"], r["http_status"]) == ("error", "bad_response", 500)
    r = api.get(cfg(url), "/redirect", auth=False)
    assert (r["error"]["code"], r["http_status"]) == ("redirect_refused", 307)


def test_unreachable_and_timeout():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    r = api.get(cfg(f"http://127.0.0.1:{port}"), "/health", auth=False)
    assert (r["status"], r["error"]["code"], r["kg_release"]) == ("error", "unreachable", None)
    with socket.socket() as hang:  # accepts connections (backlog) but never answers
        hang.bind(("127.0.0.1", 0))
        hang.listen(1)
        r = api.get(cfg(f"http://127.0.0.1:{hang.getsockname()[1]}", timeout=0.5), "/health", auth=False)
    assert r["error"]["code"] == "timeout"


def test_not_an_envelope():
    r = api._normalize({"detail": "x"}, 200, {"path": "/v1/x", "params": {}})
    assert (r["status"], r["error"]["code"]) == ("error", "bad_response")
    r = api._normalize({"status": "ok", "kg_release": RELEASE, "data": 1}, 500, {"path": "/v1/x", "params": {}})
    assert (r["status"], r["error"]["code"], r["warnings"]) == ("error", "http_500", [])


def _env(rows, page, warnings=(), data=None):
    return {"status": "ok", "kg_release": RELEASE, "data": rows if data is None else data, "page": page,
            "warnings": list(warnings), "error": None}  # fmt: skip


def test_paging_trimmed_list():
    rows = [{"i": i} for i in range(7)]
    r = api.add_paging(_env(rows, {"limit": 20, "offset": 0, "total": 20}, [trim_note("data", 7, 20)]))
    p = r["paging"]
    assert (p["returned"], p["limit"], p["total"], p["trimmed"], p["next_offset"]) == (7, 20, 20, True, 7)
    assert p["summary"].startswith("trimmed: 7 of 20 rows returned") and "offset=7" in p["summary"]
    assert p["trimmed_lists"] == {"data": {"returned": 7, "of": 20}}
    assert r["warnings"][-1] == p["summary"]  # visible next to the API's own note


def test_paging_untrimmed_pages():
    mid = api.add_paging(_env([{}] * 10, {"limit": 10, "offset": 0, "total": 132}))["paging"]
    assert (mid["trimmed"], mid["next_offset"], mid["summary"]) == (False, 10, "rows 1-10 of 132; next page: offset=10")
    last = api.add_paging(_env([{}] * 2, {"limit": 10, "offset": 130, "total": 132}))
    assert (last["paging"]["next_offset"], last["paging"]["summary"]) == (None, "rows 131-132 of 132; last page")
    assert last["warnings"] == []
    empty = api.add_paging(_env([], {"limit": 10, "offset": 200, "total": 132}))["paging"]
    assert (empty["returned"], empty["next_offset"]) == (0, None)


def test_paging_search_has_no_offset():
    p = api.add_paging(_env([{}] * 4, {"limit": 50, "offset": 0, "total": None}, [trim_note("data", 4, 50)]),
                       has_offset=False)["paging"]  # fmt: skip
    assert (p["trimmed"], p["next_offset"], p["total"]) == (True, None, None)
    assert "no offset" in p["summary"]
    p = api.add_paging(_env([{}] * 4, {"limit": 5, "offset": 0, "total": None}), has_offset=False)["paging"]
    assert (p["trimmed"], p["summary"]) == (False, "4 rows returned (ranked top 5; no total)")


def test_paging_nested_list_and_other_lists():
    data = {"edge": {}, "study_results": [{}] * 3, "publications": ["PMID:1"]}
    w = [trim_note("data.publications", 1, 40)]  # another list cut: reported, but the paged list is complete
    p = api.add_paging(_env(None, {"limit": 3, "offset": 0, "total": 3}, w, data=data), "study_results")["paging"]
    assert (p["returned"], p["trimmed"], p["next_offset"]) == (3, False, None)
    assert p["trimmed_lists"] == {"data.publications": {"returned": 1, "of": 40}}
    w = [trim_note("data.study_results", 2, 3)]
    data["study_results"] = [{}] * 2
    p = api.add_paging(_env(None, {"limit": 3, "offset": 0, "total": 9}, w, data=data), "study_results")["paging"]
    assert (p["returned"], p["trimmed"], p["next_offset"]) == (2, True, 2)


def test_paging_absent_on_errors_and_non_lists():
    err = api.error_envelope("not_found", "x")
    assert api.add_paging(err)["paging"] is None
    assert api.add_paging(_env(None, None, data={"node": {}}), "study_results")["paging"] is None


def test_paging_keeps_result_within_budget():
    """API page just under its cap (23,000 chars) + this client's fields would exceed 24,000: rows are dropped from
    the end and next_offset follows the rows actually returned."""
    rows = [{"gene_id": f"HGNC:{i}", "pad": "x" * 450} for i in range(50)]
    env = _env(rows, {"limit": 200, "offset": 0, "total": 5482}, [trim_note("data", 50, 200)])
    env["request"] = {"path": "/v1/gene-sets", "params": {"entity": "CL:0000604", "limit": 200}}
    assert api.result_size(env) > 23_000
    r = api.add_paging(env)
    p = r["paging"]
    assert api.result_size(r) <= api.RESULT_MAX_CHARS
    assert p["returned"] == len(r["data"]) < 50 and p["next_offset"] == p["returned"] and p["trimmed"]
    assert p["trimmed_lists"]["data"] == {"returned": p["returned"], "of": 200}
    assert p["summary"].startswith(f"trimmed: {p['returned']} of 200 rows returned to stay within 24000 characters")
    note = next(w for w in r["warnings"] if w.startswith("data: returned 50 of 200"))
    assert note.endswith(f"[this result keeps {p['returned']}; see paging]")  # the API's count no longer applies
    full = api.result_size(api.add_paging(_env([{"a": "x" * 1000}] * 3, {"limit": 3, "offset": 0, "total": 3})))
    small = api.add_paging(_env([{"a": "x" * 1000}] * 3, {"limit": 3, "offset": 0, "total": 3}), max_chars=full - 1)
    assert api.result_size(small) <= full - 1
    assert (small["paging"]["returned"], small["paging"]["next_offset"], small["paging"]["trimmed"]) == (2, 2, True)
    none_fit = api.add_paging(_env([{"a": "x" * 1000}] * 3, {"limit": 3, "offset": 0, "total": 3}), max_chars=50)
    assert (none_fit["paging"]["returned"], none_fit["paging"]["next_offset"]) == (0, None)  # never loop on one offset
