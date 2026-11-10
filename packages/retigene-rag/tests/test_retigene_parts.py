"""The evaluation fixture, the corpus helpers and the model paths, without the server."""

import http.server
import json
import threading

import pytest


def test_evaluation_fixture_is_the_curator_review():
    from aiscientist_mcp.retigene_rag import evaluate as ev

    ref = ev.load_reference()
    grades = {r["gene"]: r["review_grade"] for r in ref}
    assert len(ref) == 24 and [list(grades.values()).count(g) for g in "ABN"] == [15, 7, 2]
    assert {g for g, v in grades.items() if v == "A"} == {
        "CFH", "CTNNB1", "PRPF31", "PRPF4", "PRPH2", "BMP4", "OPA1", "OTX2", "PAX6", "SHH",
        "COL2A1", "JAG1", "NR2F1", "PAX2", "UCHL1"}  # fmt: skip
    assert {g for g, v in grades.items() if v == "B"} == {"CRX", "FZD4", "RCBTB1", "RP1", "SOX2", "COL11A1", "SOX5"}
    assert {g for g, v in grades.items() if v == "N"} == {"VCAN", "GATAD2B"}
    perfect = ev.score([{"gene": r["gene"], "grade": r["review_grade"]} for r in ref], ref)
    assert perfect["passed"] and perfect["reference"]["A_ocular"] == 10 and perfect["reference"]["A_syndromic"] == 5
    assert perfect["A_recall_ocular"] == "10/10" and perfect["A_recall_syndromic"] == "5/5"
    # B genes may be left out (listed apart means never as A); N genes may not be listed at all
    no_b = ev.score([{"gene": r["gene"], "grade": r["review_grade"]} for r in ref if r["review_grade"] == "A"], ref)
    assert no_b["passed"] and no_b["B_listed_apart"] == "7/7"
    bad = ev.score(
        [{"gene": "SOX2", "grade": "A"}, {"gene": "VCAN", "grade": "B"}, {"gene": "PAX6", "grade": "B"}], ref
    )
    assert bad["B_listed_as_A"] == ["SOX2"] and bad["N_listed"] == ["VCAN"] and bad["A_missed"]["PAX6"] == "B"
    assert bad["passes"] == {"all_A_listed": False, "B_listed_apart": False, "N_not_listed": False}
    assert "quote" not in ref[0] and "review_note" not in ref[0]  # grades only: no text from the papers


def test_folding_terms_and_symbols(tmp_path):
    from aiscientist_mcp.retigene_rag import corpus as C

    assert C.DOSAGE_WORDING.search(C.fold("haploinsuf" + chr(0xFB01) + "ciency"))
    assert C.DOSAGE_WORDING.search("one functional copy of the gene")
    rx, view, _ = C.term_regex(C.Term("loss of function"))
    assert view == "low" and rx.search("heterozygous loss-of-function variants")
    hgnc = tmp_path / "hgnc.txt"
    hgnc.write_text("symbol\talias_symbol\tprev_symbol\nUNC119\tHRG4\t\nCRX\tCORD2\t\nAAA1\tX1\t\nAAA2\tX1\t\n")
    h = C.Hgnc(hgnc)
    assert h.norm("HRG4") == ("UNC119", "alias") and h.norm("crx") == ("CRX", "ok")
    assert h.norm("X1")[1] == "ambiguous:AAA1|AAA2" and h.norm("ZZZ9")[1] == "unknown"
    assert C.json_object('<think>hmm</think>\n```json\n{"grade": "A"}\n```') == {"grade": "A"}


def test_reply_cache_keeps_each_reading(tmp_path):
    from aiscientist_mcp.retigene_rag import model as M

    calls = []

    def fake(messages):
        calls.append(1)
        return f"reply {len(calls)}", {"input_tokens": 10, "output_tokens": 2}

    cache, meter = M.ReplyCache(tmp_path), M.Meter()
    call = M.complete("anthropic", M.OFFLINE, meter=meter, cache=cache)
    M._offline, real = fake, M._offline
    try:
        call = M.complete("anthropic", M.OFFLINE, meter=meter, cache=cache)
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        assert [call(msgs), call(msgs)] == ["reply 1", "reply 2"]  # two readings, two entries
        again = M.complete("anthropic", M.OFFLINE, meter=M.Meter(), cache=M.ReplyCache(tmp_path))
        assert [again(msgs), again(msgs)] == ["reply 1", "reply 2"] and len(calls) == 2
    finally:
        M._offline = real
    assert meter.report()["total"]["calls"] == 2


@pytest.fixture
def chat_endpoint():
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
            reply = {"choices": [{"message": {"content": '<think>reading</think>{"grade": "B"}'}}],
                     "usage": {"prompt_tokens": 120, "completion_tokens": 30}}  # fmt: skip
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", seen
    srv.shutdown()


def test_openai_compatible_endpoint(chat_endpoint, monkeypatch):
    """The shared-model path: an OpenAI-compatible endpoint, its key and extra request fields from the
    environment, the reasoning removed from the reply, the tokens counted and priced at 0 by default."""
    from aiscientist_mcp.retigene_rag import model as M

    url, seen = chat_endpoint
    monkeypatch.setenv("RETIGENE_BASE_URL", url)
    monkeypatch.setenv("RETIGENE_API_KEY", "test-token")
    monkeypatch.setenv("RETIGENE_OPENAI_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
    meter = M.Meter()
    call = M.complete("openai", "deepseek-v4-flash", meter=meter)
    assert call([{"role": "system", "content": "rules"}, {"role": "user", "content": "GENE: PAX6"}]) == '{"grade": "B"}'
    assert seen[0]["path"] == "/v1/chat/completions" and seen[0]["auth"] == "Bearer test-token"
    assert seen[0]["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen[0]["body"]["messages"][0] == {"role": "system", "content": "rules"}
    assert "temperature" not in seen[0]["body"]  # the endpoint's default, as for Claude: two readings differ
    rep = meter.report()
    assert rep["total"]["input_tokens"] == 120 and rep["total"]["output_tokens"] == 30 and rep["total"]["cost_usd"] == 0


def test_request_body_keys_the_cache(chat_endpoint, monkeypatch, tmp_path):
    """Another RETIGENE_OPENAI_BODY (thinking on, a temperature) is another request: never an old reply."""
    from aiscientist_mcp.retigene_rag import model as M

    url, seen = chat_endpoint
    monkeypatch.setenv("RETIGENE_BASE_URL", url)
    msgs = [{"role": "system", "content": "rules"}, {"role": "user", "content": "GENE: PAX6"}]
    for body in ('{"chat_template_kwargs": {"enable_thinking": false}}', '{"temperature": 0}'):
        monkeypatch.setenv("RETIGENE_OPENAI_BODY", body)
        M.complete("openai", "m", cache=M.ReplyCache(tmp_path))(msgs)
    assert len(seen) == 2


def test_replies_read_leniently():
    """What an open model sent in a real run: a quote holding the PDF's raw control character, and the
    items as a bare list. Both are read; the strict reading lost three A genes (PAX6, PRPH2) and SOX5."""
    from aiscientist_mcp.retigene_rag import grading
    from aiscientist_mcp.retigene_rag.corpus import json_object

    item = (
        '{"sentence": 1, "dosage_claim": "yes", "hedge": "none", "hedge_target": "none", '
        '"subject_is_gene_dosage": true, "mechanism_stated": "haploinsufficiency", "phenotype": "aniridia", '
        '"phenotype_kind": "other_ocular", "evidence_type": "primary_finding", "quote": "0.1 (\x0313.0 D) PAX6"}'
    )
    for reply in (f'```json\n{{"items": [{item}]}}\n```', f"[{item}]"):
        assert json_object(reply)["items"][0]["quote"] == "0.1 (\x0313.0 D) PAX6"
        (c,) = grading.parse_gene_check(reply, 1)
        assert c["dosage_claim"] == "yes" and c["phenotype_kind"] == "other_ocular"
    assert json_object('{"grade": "B"}') == {"grade": "B"} and json_object("not json") is None


def test_fast_reference_entry_check_matches_the_plain_one():
    import random

    from aiscientist_mcp.retigene_rag import grading
    from aiscientist_mcp.retigene_rag.corpus import nz

    rng = random.Random(7)
    words = [
        "retinal",
        "dystrophy",
        "haploinsufficiency",
        "of",
        "PAX6",
        "causes",
        "aniridia",
        "in",
        "families",
        "mutation",
        "analysis",
        "gene",
    ]
    titles = [" ".join(rng.choice(words) for _ in range(rng.randint(4, 14))) for _ in range(300)]
    tl = [t for t in (nz(t) for t in titles) if len(t) > 30]
    fast = grading.make_is_reference_entry(titles)
    for _ in range(2000):
        kind = rng.random()
        t = rng.choice(titles)
        if kind < 0.3:  # a part of a title
            a = rng.randint(0, len(t) // 2)
            q = t[a : a + rng.randint(10, len(t))]
        elif kind < 0.6:  # a title inside a longer sentence
            q = "As reported, " + t + " was confirmed. " + rng.choice(words)
        else:
            q = " ".join(rng.choice(words) for _ in range(rng.randint(3, 20)))
        n = nz(q)
        plain = (len(n) > 30 and any(n in x or x in n for x in tl)) or bool(grading.AUTHOR_LIST.search(q.strip()))
        assert fast(q) == plain, q


def test_a_shared_dataset_version_is_an_index(index):
    """A version of the AiScientist shared dataset retigene is the index folder plus the store's manifest.json,
    SHA256SUMS and CHANGELOG.md: the server reads it in place and names the version it read."""
    import json

    from aiscientist_mcp.retigene_rag.settings import Index

    plain = Index(index, index / "unused").check()
    before = plain.fingerprint()
    assert plain.version() == {"fingerprint": before}
    store = {"dataset": "retigene", "version": "v2026.11", "version_uuid": "u-1", "files": []}
    (index / "manifest.json").write_text(json.dumps(store))
    (index / "SHA256SUMS").write_text("")
    v = Index(index, index / "unused").version()
    assert v["dataset"] == "retigene" and v["version"] == "v2026.11" and v["version_uuid"] == "u-1"
    assert v["fingerprint"].startswith("sha256:") and v["fingerprint"] != before
    assert Index(index, index / "unused").manifest.name == "manifest.csv"  # the papers, not the store's manifest
