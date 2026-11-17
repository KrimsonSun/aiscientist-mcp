"""Explicit OpenAI-compatible model settings for standalone computational workers.

No default endpoint or model, no credentials in repr, and no redirect-following.
Model credentials are read in the worker; never include this object in job requests.
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("model endpoint redirects are not allowed")


@dataclass(frozen=True)
class ModelSettings:
    endpoint: str
    model: str
    key_file: str = field(default="", repr=False)
    key: str = field(default="", repr=False)
    timeout: float = 180.0
    prefix: str = "LITERATURE"

    @classmethod
    def from_env(cls, prefix="LITERATURE"):
        endpoint = os.environ.get(f"{prefix}_MODEL_ENDPOINT", "").strip().rstrip("/")
        model = os.environ.get(f"{prefix}_MODEL", "").strip()
        if endpoint:
            parsed = urlsplit(endpoint)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError(f"{prefix}_MODEL_ENDPOINT must be an HTTP(S) URL without credentials")
            if parsed.query or parsed.fragment:
                raise ValueError(f"{prefix}_MODEL_ENDPOINT cannot contain a query or fragment")
        timeout = float(os.environ.get(f"{prefix}_MODEL_TIMEOUT", "180"))
        if not 0 < timeout < float("inf"):
            raise ValueError(f"{prefix}_MODEL_TIMEOUT must be positive and finite")
        return cls(
            endpoint,
            model,
            os.environ.get(f"{prefix}_MODEL_API_KEY_FILE", ""),
            os.environ.get(f"{prefix}_MODEL_API_KEY", ""),
            timeout,
            prefix,
        )

    def endpoint_pair(self):
        return (self.model, self.endpoint) if self.endpoint and self.model else None

    def api_key(self):
        if self.key_file:
            try:
                return Path(self.key_file).read_text().strip()
            except OSError:
                raise ValueError("configured model API key file cannot be read") from None
        return self.key

    def judge(self, max_tokens=2000, *, json_object=True):
        if self.endpoint_pair() is None:
            raise ValueError(f"configure {self.prefix}_MODEL_ENDPOINT and {self.prefix}_MODEL")
        key = self.api_key()
        opener = urllib.request.build_opener(_NoRedirect)

        def call(messages):
            body = {
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            if json_object:
                body["response_format"] = {"type": "json_object"}
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            request = urllib.request.Request(
                self.endpoint + "/chat/completions", method="POST", headers=headers, data=json.dumps(body).encode()
            )
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                try:
                    return str(payload["choices"][0]["message"].get("content") or "")
                except (KeyError, IndexError, TypeError, AttributeError):
                    raise ValueError("model endpoint returned an invalid chat response") from None
            except urllib.error.HTTPError as exc:
                # No response body, URL, or request headers enter model failures or saved job logs.
                raise ValueError(f"model endpoint returned HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                raise ConnectionError("configured model endpoint is unavailable") from None

        return call
