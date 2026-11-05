"""The model behind the grading: Claude through the Anthropic SDK, or an offline stand-in.

``caller(model, effort)`` returns ``call(messages) -> (text, usage)``. The model name ``offline-test``
selects a deterministic stand-in that needs no key and no network, so the tools and jobs are tested
end to end in CI; it is never a substitute for a real read.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

from .core import MAX_TOKENS, Call

OFFLINE = "offline-test"
HEDGES = ("may", "might", "likely", "proposed", "possibly", "suggest")


def caller(model: str, effort: str, max_tokens: int = MAX_TOKENS, attempts: int = 6) -> Call:
    return _offline if model == OFFLINE else claude_caller(model, effort, max_tokens, attempts)


def claude_caller(model: str, effort: str, max_tokens: int = MAX_TOKENS, attempts: int = 6) -> Call:
    """Streaming calls with the system prompt marked for caching and ``effort`` sent as
    ``output_config``; a rate limit, a server error or a lost connection is waited out and tried
    again, up to ``attempts`` times. The key comes from ``ANTHROPIC_API_KEY`` (``ANTHROPIC_BASE_URL``
    for a proxy)."""
    import anthropic

    client = anthropic.Anthropic(timeout=600.0, max_retries=2)

    def call(messages: list[dict]) -> tuple[str, dict]:
        system = [{"type": "text", "text": messages[0]["content"], "cache_control": {"type": "ephemeral"}}]
        kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system,
                                  "messages": messages[1:]}  # fmt: skip
        if effort:
            kwargs["output_config"] = {"effort": effort}
        for attempt in range(attempts):
            try:
                with client.messages.stream(**kwargs) as stream:
                    final = stream.get_final_message()
                break
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
                status = getattr(exc, "status_code", None)
                retry = status is None or status == 429 or status >= 500
                if not retry or attempt == attempts - 1:
                    raise
                print(f"[claude] {type(exc).__name__}; retry {attempt + 1}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        text = "\n".join(b.text for b in final.content if getattr(b, "type", "") == "text")
        u = final.usage
        use = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
               "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
               "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}  # fmt: skip
        return text, use

    return call


def _offline(messages: list[dict]) -> tuple[str, dict]:
    """A judge for the tests: a sentence makes the claim when it says haploinsufficiency or loss of function; it
    is explicit without a hedge word and hedged with one; it is about an eye disease when it names one; it is
    contradicted when another sentence says the gene "does not cause" it."""
    system, user = messages[0]["content"], messages[1]["content"]
    claims = [(pmid, s) for pmid, block in re.findall(r"^\[\d+\] PMID (\S+?),.*\n(.*)$", user, re.M)
              for s in re.split(r"(?<=\.)\s+", block)]  # fmt: skip
    hits = [(pmid, s) for pmid, s in claims if _claim(s)]
    firm = [(pmid, s) for pmid, s in hits if not any(re.search(rf"\b{h}\b", s, re.I) for h in HEDGES)]
    best = firm or hits
    quote = best[0][1] if best else ""
    eye = bool(re.search(r"retin|ocular|optic|eye|fovea|aniridia|anophthalm|microphthalm|coloboma", quote, re.I))
    against = any("does not cause" in s for _, s in claims)
    pmids = sorted({p for p, _ in best})
    if "explicit" in system:
        obj = {"explicit": bool(firm), "hedged": bool(hits) and not firm, "subject": True, "eye_disease": eye,
               "contradicted": against, "pmids": pmids, "quote": quote, "reason": "offline"}  # fmt: skip
    else:
        obj = {"grade": "A" if firm and eye else "B" if hits else "N", "pmids": pmids, "quote": quote,
               "reason": "offline"}  # fmt: skip
    reply = json.dumps(obj)
    return reply, {"input_tokens": (len(system) + len(user)) // 4, "output_tokens": len(reply) // 4}


def _claim(sentence: str) -> bool:
    return bool(re.search(r"haplo-?insufficien|loss[- ]of[- ]function", sentence, re.I))
