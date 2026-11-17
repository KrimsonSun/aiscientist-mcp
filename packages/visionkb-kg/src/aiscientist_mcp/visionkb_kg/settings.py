"""Server settings from environment variables (set them where the server is registered).

The bearer token is read from a file (``VISIONKB_API_TOKEN_FILE``) at each call; it is never logged, echoed in a
result or put on a command line.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_TIMEOUT_S = 30.0


class ClientError(ValueError):
    """A setting or argument is missing or invalid; reported to the caller as ``status: error``."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Settings:
    api_url: str  # base URL of visionkb-api, without /v1 (e.g. http://127.0.0.1:8770)
    token_file: Path | None  # file whose first non-empty line is the bearer token (scope "read")
    timeout_s: float  # seconds per HTTP request

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        token_file = e("VISIONKB_API_TOKEN_FILE", "").strip()
        raw_timeout = e("VISIONKB_TIMEOUT_S", "").strip()
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_S
        except ValueError:
            raise ClientError("invalid_setting", f"VISIONKB_TIMEOUT_S is not a number: {raw_timeout!r}") from None
        if not 0 < timeout <= 600:
            raise ClientError("invalid_setting", "VISIONKB_TIMEOUT_S must be in (0, 600] seconds")
        return cls(
            api_url=e("VISIONKB_API_URL", "").strip().rstrip("/"),
            token_file=Path(token_file).expanduser() if token_file else None,
            timeout_s=timeout,
        )

    def base_url(self) -> str:
        """The API base URL, checked: http(s), a host, no credentials, query or fragment."""
        if not self.api_url:
            raise ClientError("not_configured", "VISIONKB_API_URL is not set (base URL of visionkb-api)")
        u = urlsplit(self.api_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ClientError("invalid_setting", "VISIONKB_API_URL must be an http(s) URL with a host")
        if u.username or u.password or u.query or u.fragment:
            raise ClientError("invalid_setting", "VISIONKB_API_URL must not contain credentials, query or fragment")
        base = self.api_url
        if base.endswith("/v1"):  # accept both http://host:port and http://host:port/v1
            base = base[: -len("/v1")]
        return base

    def token(self) -> str | None:
        """The bearer token, or None when no token file is configured."""
        if self.token_file is None:
            return None
        try:
            text = self.token_file.read_text()
        except OSError as exc:
            raise ClientError(
                "token_unreadable", f"cannot read VISIONKB_API_TOKEN_FILE ({exc.strerror or type(exc).__name__})"
            ) from None
        for line in text.splitlines():
            tok = line.strip()
            if tok and not tok.startswith("#"):
                if any(c.isspace() for c in tok) or not tok.isprintable():
                    raise ClientError(
                        "token_unreadable", "VISIONKB_API_TOKEN_FILE: the token line must be one printable word"
                    )
                return tok
        raise ClientError("token_unreadable", "VISIONKB_API_TOKEN_FILE is empty")
