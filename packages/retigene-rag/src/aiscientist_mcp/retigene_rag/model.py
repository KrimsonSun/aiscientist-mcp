"""The models behind the curation: Claude, any OpenAI-compatible endpoint, or an offline stand-in.

``complete(provider, model, ...)`` returns ``call(messages) -> text``, the shape ``grading`` takes. Every
call is counted in a ``Meter`` (calls, tokens, seconds, cost per model) and its reply kept in a
``ReplyCache``: the same request again (the same gene with the same sentences, rules and model) is
answered from the cache, so a repeated or reworded question, or a corpus update, reads only what changed.

Providers (``RETIGENE_PROVIDER``):
  anthropic  Claude through the Anthropic SDK; key ``ANTHROPIC_API_KEY`` (``ANTHROPIC_BASE_URL`` for a proxy).
  openai     an OpenAI-compatible ``/chat/completions`` endpoint, such as the lab's shared model on vLLM;
             ``RETIGENE_BASE_URL`` and ``RETIGENE_API_KEY`` (``OPENAI_BASE_URL``, ``OPENAI_API_KEY``).
The model name ``offline-test`` is a deterministic stand-in for the tests: no key, no network, never a
substitute for a real reading.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

OFFLINE = "offline-test"
PRICES = {  # USD per million input / output tokens
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    OFFLINE: (0.0, 0.0),
}
Complete = Callable[[list[dict[str, str]]], str]


def price(model: str) -> tuple[float, float]:
    """USD per million input and output tokens: the Claude price table, else ``RETIGENE_PRICE_IN`` /
    ``RETIGENE_PRICE_OUT`` (0 for a lab GPU)."""
    if model in PRICES:
        return PRICES[model]
    return float(os.environ.get("RETIGENE_PRICE_IN") or 0), float(os.environ.get("RETIGENE_PRICE_OUT") or 0)


def cost(use: dict, model: str) -> float:
    """USD for a usage record: cache writes at 1.25 and cache reads at 0.1 times the input price."""
    pin, pout = price(model)
    return (use.get("input_tokens", 0) * pin + use.get("output_tokens", 0) * pout
            + use.get("cache_write_tokens", 0) * pin * 1.25 + use.get("cache_read_tokens", 0) * pin * 0.1) / 1e6  # fmt: skip


class Meter:
    """Calls, tokens and seconds per model, safe across threads."""

    KEYS = ("calls", "cached", "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")

    def __init__(self):
        self.by_model: dict[str, dict] = {}
        self._lock = threading.Lock()

    def add(self, model: str, use: dict, seconds: float = 0.0, cached: bool = False) -> None:
        with self._lock:
            m = self.by_model.setdefault(model, {k: 0 for k in self.KEYS} | {"model_seconds": 0.0})
            m["cached" if cached else "calls"] += 1
            for k in self.KEYS[2:]:
                m[k] += int(use.get(k) or 0)
            m["model_seconds"] += seconds

    def report(self) -> dict:
        with self._lock:
            out = {m: {**u, "model_seconds": round(u["model_seconds"], 1), "cost_usd": round(cost(u, m), 4)}
                   for m, u in self.by_model.items()}  # fmt: skip
        total = {k: sum(u[k] for u in out.values()) for k in self.KEYS}
        total["cost_usd"] = round(sum(u["cost_usd"] for u in out.values()), 4)
        return {"by_model": out, "total": total}


class ReplyCache:
    """Model replies on disk, keyed by the whole request. The ``n``-th identical request of a run is the
    ``n``-th reading (the check model reads each gene twice), so each reading has its own entry."""

    def __init__(self, root: Path | None):
        self.root = Path(root) if root else None
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def key(self, request: dict) -> str:
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self._lock:
            n = self._seen.get(digest, 0)
            self._seen[digest] = n + 1
        return f"{digest}-{n}"

    def get(self, key: str) -> str | None:
        if not self.root:
            return None
        p = self.root / key[:2] / f"{key}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))["text"]
        except (OSError, ValueError, KeyError):
            return None

    def put(self, key: str, text: str) -> None:
        if not self.root:
            return
        p = self.root / key[:2] / f"{key}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)


def complete(provider: str, model: str, *, effort: str = "low", max_tokens: int = 16000,
             meter: Meter | None = None, cache: ReplyCache | None = None, attempts: int = 6) -> Complete:  # fmt: skip
    """``call(messages) -> text`` for one model; a rate limit, a server error or a lost connection is waited
    out and tried again, up to ``attempts`` times."""
    meter = meter or Meter()
    if model == OFFLINE:
        raw = _offline
    elif provider == "anthropic":
        raw = _anthropic(model, effort, max_tokens, attempts)
    elif provider == "openai":
        raw = _openai(model, effort, max_tokens, attempts)
    else:
        raise ValueError(f"RETIGENE_PROVIDER={provider!r}: expected anthropic or openai")

    def call(messages: list[dict[str, str]]) -> str:
        key = None
        if cache is not None:
            key = cache.key({"provider": provider, "model": model, "effort": effort, "max_tokens": max_tokens,
                             "messages": messages})  # fmt: skip
            hit = cache.get(key)
            if hit is not None:
                meter.add(model, {}, cached=True)
                return hit
        t0 = time.time()
        text, use = raw(messages)
        meter.add(model, use, time.time() - t0)
        if key is not None and text.strip():
            cache.put(key, text)
        return text

    return call


def _retry(attempts: int, transient, fn):
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if not transient(exc) or attempt == attempts - 1:
                raise
            print(f"[model] {type(exc).__name__}; retry {attempt + 1}", file=sys.stderr, flush=True)
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def _anthropic(model: str, effort: str, max_tokens: int, attempts: int):
    import anthropic

    client = anthropic.Anthropic(timeout=600.0, max_retries=2)

    def transient(exc) -> bool:
        if not isinstance(exc, anthropic.APIStatusError | anthropic.APIConnectionError):
            return False
        status = getattr(exc, "status_code", None)
        return status is None or status == 429 or status >= 500

    def raw(messages: list[dict[str, str]]) -> tuple[str, dict]:
        system = [{"type": "text", "text": messages[0]["content"], "cache_control": {"type": "ephemeral"}}]
        kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system,
                                  "messages": messages[1:]}  # fmt: skip
        if effort:
            kwargs["output_config"] = {"effort": effort}

        def once():
            with client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()

        final = _retry(attempts, transient, once)
        text = "\n".join(b.text for b in final.content if getattr(b, "type", "") == "text")
        u = final.usage
        return text, {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                      "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                      "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}  # fmt: skip

    return raw


def _openai(model: str, effort: str, max_tokens: int, attempts: int):
    """An OpenAI-compatible chat endpoint. ``RETIGENE_OPENAI_BODY`` (JSON) is merged into every request,
    e.g. ``{"chat_template_kwargs": {"enable_thinking": false}}`` for a reasoning model served by vLLM."""
    import urllib.error
    import urllib.request

    e = os.environ.get
    base = (e("RETIGENE_BASE_URL") or e("OPENAI_BASE_URL") or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("RETIGENE_PROVIDER=openai needs RETIGENE_BASE_URL (the endpoint, e.g. http://host:8000/v1)")
    key = e("RETIGENE_API_KEY") or e("OPENAI_API_KEY") or ""
    extra = json.loads(e("RETIGENE_OPENAI_BODY") or "{}")
    headers = {"Content-Type": "application/json"} | ({"Authorization": f"Bearer {key}"} if key else {})

    def transient(exc) -> bool:
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code == 429 or exc.code >= 500
        return isinstance(exc, urllib.error.URLError | TimeoutError | ConnectionError)

    def raw(messages: list[dict[str, str]]) -> tuple[str, dict]:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0, **extra}

        def once():
            req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(), headers=headers)
            with urllib.request.urlopen(req, timeout=900) as r:  # an http(s) URL, checked above
                return json.loads(r.read())

        data = _retry(attempts, transient, once)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        text = re.sub(r"^\s*<think>.*?</think>\s*", "", str(msg.get("content") or ""), flags=re.S)
        u = data.get("usage") or {}
        return text, {"input_tokens": int(u.get("prompt_tokens") or 0),
                      "output_tokens": int(u.get("completion_tokens") or 0)}  # fmt: skip

    return raw


# ---- the offline stand-in --------------------------------------------------------------------------

_HEDGES = re.compile(r"\b(may|might|likely|proposed|possibly|suggests?|consistent with)\b", re.I)
_EYE = re.compile(r"retin|macul|optic|aniridia|anophthalm|microphthalm|coloboma|fovea|ocular|eye disease", re.I)
_LINE = re.compile(r"^\[(\d+)\] \(PMID [^)]*\) (.*)$", re.M)


def _offline(messages: list[dict[str, str]]) -> tuple[str, dict]:
    """A stand-in reader for the tests. Check: a sentence claims haploinsufficiency when it says
    "haploinsufficien" or "loss of one copy"; hedged with a hedge word; ruled out with "does not cause";
    ocular when it names an eye disease. Review: A when a sentence is marked MEETS_A, B when one claims,
    N otherwise."""
    system, user = messages[0]["content"], messages[-1]["content"]
    gene = (re.search(r"^GENE: (\S+)", user, re.M) or [None, ""])[1]
    if "HOW TO APPLY THEM" in system:
        lines = user.splitlines()
        meets = [int(m[1]) for i, m in enumerate(_LINE.finditer(user)) if "MEETS_A" in _check_of(lines, m[1])]
        claims = re.findall(r"dosage_claim=(yes|hypothesis_only)", user)
        grade = "A" if meets else "B" if claims else "N"
        reply = {"grade": grade, "phenotype_kind": "retinal", "reason": "offline", "confidence": "high",
                 "best_sentence": meets[0] if meets else 1}  # fmt: skip
    else:
        items = []
        for m in _LINE.finditer(user):
            s = m[2]
            claim = bool(re.search(r"haplo-?insufficien|loss of one copy", s, re.I))
            ruled_out = "does not cause" in s
            items.append({"sentence": int(m[1]), "dosage_claim": "yes" if claim else "no",
                          "hedge": "hedged" if _HEDGES.search(s) else "none",
                          "hedge_target": "mechanism" if _HEDGES.search(s) else "none",
                          "subject_is_gene_dosage": bool(gene) and gene in s,
                          "mechanism_stated": "hi_ruled_out" if ruled_out else "haploinsufficiency" if claim
                          else "unspecified", "phenotype": "disease" if _EYE.search(s) else None,
                          "phenotype_kind": "retinal" if _EYE.search(s) else "non_ocular",
                          "evidence_type": "primary_finding", "quote": s})  # fmt: skip
        reply = {"items": items}
    text = json.dumps(reply)
    return text, {"input_tokens": (len(system) + len(user)) // 4, "output_tokens": len(text) // 4}


def _check_of(lines: list[str], number: str) -> str:
    for i, line in enumerate(lines):
        if line.startswith(f"[{number}] "):
            return lines[i + 1] if i + 1 < len(lines) else ""
    return ""
