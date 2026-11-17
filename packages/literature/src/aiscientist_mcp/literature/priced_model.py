"""Usage-aware extraction calls to an explicitly configured priced relay.

This protocol is an operator assertion, never a tool argument. Both the worker
and the gateway stop subsequent calls at the cap. A provider's in-flight call
can cross that threshold; no token-price prediction is presented as a hard
prepaid limit. Unknown/missing response prices fail closed, never become zero.
"""

import json
import math
import os
import threading
import urllib.error
import urllib.request

PROTOCOL = "aisci-relay-v1"


def enabled():
    return os.environ.get("LITERATURE_MODEL_USAGE_PROTOCOL") == PROTOCOL


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("priced model endpoint redirects are not allowed")


class PricedModel:
    def __init__(self, settings, meter, *, max_cost_usd=0):
        if not enabled():
            raise ValueError("a priced model requires the declared aisci-relay-v1 usage protocol")
        if settings.endpoint_pair() is None:
            raise ValueError("configure the literature model endpoint and name")
        self.settings, self.meter, self.cap = settings, meter, float(max_cost_usd or 0)
        self._key = settings.api_key()
        self._opener = urllib.request.build_opener(_NoRedirect)
        # Extraction's concurrent readers share this object, so a waiting reader
        # checks the newly recorded spend before it submits another request.
        self._lock = threading.Lock()

    def judge(self, max_tokens=2000):
        from .extract import ScanStopped

        def unknown_accounting():
            self.meter.mark_unknown_cost()
            raise ScanStopped("priced model request returned no reliable accounting; further calls were stopped")

        def call(messages):
            with self._lock:
                spent = self.meter.public()["cost_usd"]
                if spent is None:
                    raise ScanStopped("the model relay did not provide a known price")
                if self.cap > 0 and spent >= self.cap:
                    raise ScanStopped(f"the model calls reached the ${self.cap:.2f} spending cap")
                body = {
                    "model": self.settings.model,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                    "chat_template_kwargs": {"enable_thinking": False},
                }
                headers = {"Content-Type": "application/json"}
                if self._key:
                    headers["Authorization"] = "Bearer " + self._key
                request = urllib.request.Request(
                    self.settings.endpoint + "/chat/completions",
                    method="POST",
                    headers=headers,
                    data=json.dumps(body).encode(),
                )
                http_error = None
                try:
                    with self._opener.open(request, timeout=self.settings.timeout) as response:
                        payload = json.loads(response.read(2 * 1024 * 1024))
                except urllib.error.HTTPError as exc:
                    http_error = exc.code
                    try:
                        payload = json.loads(exc.read(2 * 1024 * 1024))
                    except (ValueError, OSError):
                        unknown_accounting()
                except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
                    unknown_accounting()
                if not isinstance(payload, dict):
                    unknown_accounting()
                accounting = payload.get("aiscientist_usage")
                if isinstance(accounting, dict) and accounting.get("billed") is True:
                    cost = accounting.get("cost_usd")
                    valid = (
                        type(accounting.get("version")) is int
                        and accounting["version"] == 1
                        and type(cost) in (int, float)
                        and math.isfinite(cost)
                        and cost >= 0
                    )
                    self.meter.add(payload.get("usage"), cost if valid else None)
                    if not valid:
                        raise ScanStopped("the model relay did not provide a known price; further calls were stopped")
                elif not http_error:
                    self.meter.add(payload.get("usage"), None)
                    raise ScanStopped("the configured model omitted priced usage; further calls were stopped")
                if http_error:
                    kind = (payload.get("error") or {}).get("type") if isinstance(payload.get("error"), dict) else None
                    if kind == "stop":
                        raise ScanStopped("the gateway stopped the model call (budget, key guard or run cancellation)")
                    if not accounting:
                        unknown_accounting()
                    raise ValueError(f"priced model endpoint returned HTTP {http_error}")
                try:
                    return str(payload["choices"][0]["message"].get("content") or "")
                except (KeyError, IndexError, TypeError, AttributeError):
                    raise ValueError("priced model returned an invalid chat response") from None

        return call
