"""RETIGENE_PROVIDER=relay: the caller's platform answers each prompt from a folder (AiScientist's
gateway/llm_relay.py). A thread here plays the platform on a local folder, answering with the offline reader."""

import asyncio
import json
import threading
import time

import pytest

QUESTION = "What are haploinsufficient genes in RetiGene papers?"


@pytest.fixture
def platform(tmp_path, monkeypatch):
    """The platform side of the relay: answers every ``req/*.json`` with ``handler(request)``."""
    from aiscientist_mcp.retigene_rag import model as M

    root = tmp_path / "relay"
    (root / "req").mkdir(parents=True)
    (root / "resp").mkdir()
    monkeypatch.setenv("RETIGENE_RELAY_DIR", str(root))
    monkeypatch.setenv("RETIGENE_RELAY_POLL", "0.01")
    real_sleep = time.sleep
    monkeypatch.setattr(M.time, "sleep", lambda s: real_sleep(min(s, 0.01)))  # no 5 s waits between retries
    seen, halt = [], threading.Event()

    def offline(req):
        text, use = M._offline(req["messages"])
        return {"text": text, "usage": {"prompt_tokens": use["input_tokens"],
                                        "completion_tokens": use["output_tokens"]}, "cost_usd": 0.001}  # fmt: skip

    state = {"handler": offline}

    def serve():
        while not halt.is_set():
            for p in sorted((root / "req").glob("*.json")):
                req = json.loads(p.read_text())
                seen.append(req)
                reply = state["handler"](req)
                tmp = root / "resp" / f".{p.name}.tmp"
                tmp.write_text(json.dumps(reply))
                tmp.replace(root / "resp" / p.name)
                p.unlink()
            halt.wait(0.005)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield {"root": root, "seen": seen, "state": state, "offline": offline}
    halt.set()
    t.join(2)


def test_relay_round_trip_takes_the_platforms_price(platform):
    from aiscientist_mcp.retigene_rag import model as M

    meter = M.Meter()
    call = M.complete("relay", "claude-haiku-5-5", meter=meter, max_tokens=16000)
    msgs = [{"role": "system", "content": "rules"}, {"role": "user", "content": "GENE: PAX6\n[1] (PMID 1) PAX6 x."}]
    assert json.loads(call(msgs))["items"]
    req = platform["seen"][0]
    assert set(req) == {"id", "messages", "max_tokens"} and req["max_tokens"] == 16000 and req["messages"] == msgs
    rep = meter.report()["total"]
    assert rep["calls"] == 1 and rep["input_tokens"] > 0 and rep["cost_usd"] == 0.001  # not the Haiku table price
    assert not list((platform["root"] / "req").iterdir()) and not list((platform["root"] / "resp").iterdir())


def test_relay_reply_kinds(platform):
    from aiscientist_mcp.retigene_rag import model as M

    msgs = [{"role": "system", "content": "rules"}, {"role": "user", "content": "GENE: PAX6"}]
    call = M.complete("relay", "claude-haiku-5-5", attempts=3)
    platform["state"]["handler"] = lambda req: {"error": "the run was stopped", "kind": "stop"}
    with pytest.raises(M.RelayStopped, match="the run was stopped"):
        call(msgs)
    # one prompt the platform could not send (refused by its payload guard, a 400): an unreadable reply
    platform["state"]["handler"] = lambda req: {"error": "role_egress: withheld", "kind": "model"}
    assert call(msgs) == ""
    # the model unreachable through the platform's own retries: tried again here
    n = {"k": 0}

    def flaky(req):
        n["k"] += 1
        return {"error": "endpoint: down", "kind": "transport"} if n["k"] == 1 else platform["offline"](req)

    platform["state"]["handler"] = flaky
    assert json.loads(call(msgs)) and n["k"] == 2


def test_relay_needs_its_folder(monkeypatch):
    from aiscientist_mcp.retigene_rag import model as M

    monkeypatch.delenv("RETIGENE_RELAY_DIR", raising=False)
    with pytest.raises(ValueError, match="RETIGENE_RELAY_DIR"):
        M.complete("relay", "m")


def test_relay_withdraws_a_prompt_nobody_answers(tmp_path, monkeypatch):
    from aiscientist_mcp.retigene_rag import model as M

    monkeypatch.setenv("RETIGENE_RELAY_DIR", str(tmp_path / "relay"))
    monkeypatch.setenv("RETIGENE_RELAY_TIMEOUT", "0.05")
    monkeypatch.setenv("RETIGENE_RELAY_POLL", "0.01")
    with pytest.raises(M.RelayStopped, match="no answer"):
        M.complete("relay", "m")([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert not list((tmp_path / "relay" / "req").iterdir())


def test_curation_through_the_relay(env, platform, monkeypatch):
    """A whole curation on the caller's model: the same genes as the offline reader, the platform's cost,
    and a stop mid-way that keeps what was read, so asking again reads only the rest."""
    from aiscientist_mcp.retigene_rag.server import server
    from mcp import Client

    monkeypatch.setenv("RETIGENE_PROVIDER", "relay")
    monkeypatch.setenv("RETIGENE_CHECK_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("RETIGENE_REVIEW_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("RETIGENE_WORKERS", "1")
    answered = {"n": 0}

    def stops_after_three(req):
        answered["n"] += 1
        if answered["n"] > 3:
            return {"error": "this curation reached its $0.01 spending cap", "kind": "stop"}
        return platform["offline"](req)

    async def flow():
        async with Client(server) as c:
            args = {"question": QUESTION, "terms": ["haploinsufficien"]}
            platform["state"]["handler"] = stops_after_three
            first = (await c.call_tool("submit_curation", args)).structured_content
            assert first["state"] == "FAILED" and "spending cap" in first["error"]
            platform["state"]["handler"] = platform["offline"]
            done = (await c.call_tool("submit_curation", args | {"rerun": True})).structured_content
            assert done["state"] == "COMPLETED" and done["genes_A"]
            assert done["total"]["cached"] == 3  # the three replies read before the stop
            assert done["total"]["cost_usd"] == pytest.approx(0.001 * done["total"]["calls"])
            assert {r["max_tokens"] for r in platform["seen"]} == {16000}

    asyncio.run(flow())
