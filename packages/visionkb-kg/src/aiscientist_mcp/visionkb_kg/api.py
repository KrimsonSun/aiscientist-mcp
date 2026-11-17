"""Thin HTTP client of visionkb-api ``/v1`` (standard library only).

Every call returns the API envelope ``{status, kg_release, data, page, warnings, error}`` (contracts §8), plus
``http_status`` and ``request`` (path and parameters, never the token). Transport and protocol failures become
``status: "error"`` envelopes with an explicit code; nothing is retried, cached or substituted.
"""

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urlencode

from . import __version__
from .settings import ClientError, Settings

USER_AGENT = f"aiscientist-mcp-visionkb-kg/{__version__}"
MAX_BODY_BYTES = 4 * 1024 * 1024  # the API caps a response near 24,000 characters; anything far larger is wrong
CURIE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}:[^\s/?#%\\]{1,200}$")
ENVELOPE_KEYS = ("status", "kg_release", "data", "page", "warnings", "error")
# visionkb-api's note when it cuts a list to fit its character cap (D13), e.g.
# "data: returned 7 of 20 items to stay within 24000 characters; page with limit/offset"
TRIM_RE = re.compile(
    r"^(data(?:\.[A-Za-z_][A-Za-z0-9_]*)?): returned (\d+) of (\d+) items to stay within (\d+) characters"
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: the token must only go to the configured host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def error_envelope(code: str, message: str, *, kg_release: str | None = None, warnings=None, **extra) -> dict:
    env = {
        "status": "error",
        "kg_release": kg_release,
        "data": None,
        "page": None,
        "warnings": list(warnings or []),
        "error": {"code": code, "message": message},
    }
    env.update(extra)
    return env


def check_curie(value: str, what: str) -> str:
    """A CURIE that is safe to put in a URL path segment (``prefix:local``, no '/', '?', '#', '%' or spaces)."""
    v = (value or "").strip()
    if not CURIE_RE.match(v):
        raise ClientError("invalid_argument", f"{what} must be a CURIE like HGNC:10012, got {value!r}")
    return v


def split_list(value: str | list[str] | None) -> list[str]:
    """``"RHO, GNAT1"`` or ``["RHO", "GNAT1"]`` -> ``["RHO", "GNAT1"]`` (order kept, blanks dropped)."""
    if value is None:
        return []
    items = re.split(r"[,\s]+", value) if isinstance(value, str) else [str(x).strip() for x in value]
    return [x for x in (i.strip() for i in items) if x]


def _query(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    clean: dict[str, Any] = {}
    for k, v in params.items():
        if v is None or (isinstance(v, (list, tuple)) and not v):
            continue
        clean[k] = ("true" if v else "false") if isinstance(v, bool) else v
    return urlencode(clean, doseq=True), clean


def _normalize(body: Any, status: int, request: dict) -> dict:
    if not isinstance(body, dict) or body.get("status") not in ("ok", "error"):
        return error_envelope(
            "bad_response",
            f"HTTP {status}: the response is not a visionkb-api envelope",
            http_status=status,
            request=request,
        )
    env = {k: body.get(k) for k in ENVELOPE_KEYS}
    env["warnings"] = list(env["warnings"] or [])
    if env["status"] == "error" and not isinstance(env["error"], dict):
        env["error"] = {"code": f"http_{status}", "message": "error without details"}
    if env["status"] == "ok" and status >= 400:  # never report an HTTP failure as success
        env["status"] = "error"
        env["error"] = {"code": f"http_{status}", "message": f"HTTP {status} with status ok in the body"}
    env["http_status"] = status
    env["request"] = request
    return env


def get(settings: Settings, path: str, params: dict[str, Any] | None = None, *, auth: bool = True) -> dict:
    """GET ``/v1<path>``; ``path`` segments must already be validated or quoted."""
    query, clean = _query(params or {})
    request = {"path": f"/v1{path}", "params": clean}
    try:
        url = f"{settings.base_url()}/v1{path}" + (f"?{query}" if query else "")
        token = settings.token() if auth else None
    except ClientError as exc:
        return error_envelope(exc.code, exc.message, request=request)
    warnings = []
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    if token:
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    elif auth:
        warnings.append("VISIONKB_API_TOKEN_FILE is not set: request sent without a bearer token")
    try:
        with _OPENER.open(req, timeout=settings.timeout_s) as resp:
            status, raw = resp.status, resp.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read(MAX_BODY_BYTES + 1)
        except OSError:
            raw = b""
        finally:
            exc.close()
        if 300 <= status < 400:
            return error_envelope(
                "redirect_refused",
                f"HTTP {status}: redirects are not followed (check VISIONKB_API_URL)",
                warnings=warnings,
                http_status=status,
                request=request,
            )
    except TimeoutError:
        return error_envelope(
            "timeout",
            f"no response within {settings.timeout_s:g} s (VISIONKB_TIMEOUT_S)",
            warnings=warnings,
            request=request,
        )
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):  # socket.timeout is TimeoutError (3.10+)
            return error_envelope(
                "timeout",
                f"no response within {settings.timeout_s:g} s (VISIONKB_TIMEOUT_S)",
                warnings=warnings,
                request=request,
            )
        return error_envelope(
            "unreachable",
            f"cannot reach visionkb-api: {getattr(reason, 'strerror', None) or reason}",
            warnings=warnings,
            request=request,
        )
    except OSError as exc:
        return error_envelope(
            "unreachable",
            f"cannot reach visionkb-api: {exc.strerror or type(exc).__name__}",
            warnings=warnings,
            request=request,
        )
    if len(raw) > MAX_BODY_BYTES:
        return error_envelope(
            "bad_response",
            f"HTTP {status}: response larger than {MAX_BODY_BYTES} bytes",
            warnings=warnings,
            http_status=status,
            request=request,
        )
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return error_envelope(
            "bad_response",
            f"HTTP {status}: the response is not JSON",
            warnings=warnings,
            http_status=status,
            request=request,
        )
    env = _normalize(body, status, request)
    env["warnings"] = warnings + env["warnings"]
    return env


def segment(value: str) -> str:
    """Quote one validated path segment (keeps ':' readable)."""
    return quote(value, safe=":")


RESULT_MAX_CHARS = 24_000  # contracts D13: a tool result (compact JSON) stays within the AiScientist tool-result cap


def result_size(env: dict) -> int:
    """Characters of the compact JSON result (the API measures its envelope the same way)."""
    return len(json.dumps(env, ensure_ascii=False, separators=(",", ":"), default=str))


def add_paging(
    env: dict, list_key: str | None = None, *, has_offset: bool = True, max_chars: int = RESULT_MAX_CHARS
) -> dict:
    """Add ``paging``: how many rows came back and where the next page starts.

    The API may return FEWER rows than ``limit``: it trims the list to stay within its character cap and says so
    in ``warnings``, while ``page.limit`` stays the requested limit. The next page therefore starts at
    ``offset + rows returned`` (``paging.next_offset``), never at ``offset + limit``, or rows are skipped.
    ``list_key`` names the paged list inside a dict ``data`` (``study_results`` for get_evidence).
    If this client's own fields (``request``, ``paging``) push the result over ``max_chars``, rows are dropped
    from the end of the (ranked) list the same way, and ``paging`` reports the rows actually returned.
    """
    env["paging"] = None
    data = env.get("data")
    rows = data.get(list_key) if list_key and isinstance(data, dict) else data
    if env.get("status") != "ok" or not isinstance(rows, list):
        return env
    page = env.get("page") or {}
    label = f"data.{list_key}" if list_key else "data"
    trims = {}
    for w in env.get("warnings") or []:
        m = TRIM_RE.match(w)
        if m:
            trims[m.group(1)] = {"returned": int(m.group(2)), "of": int(m.group(3)), "max_chars": int(m.group(4))}
    off, lim, total = page.get("offset") or 0, page.get("limit"), page.get("total")
    t = trims.get(label)
    page_rows = t["of"] if t else len(rows)  # rows this page held before any trimming
    cap = min(t["max_chars"], max_chars) if t else max_chars
    base_warnings = list(env.get("warnings") or [])

    def build(n: int) -> int:
        kept = rows[:n]
        if list_key:
            data[list_key] = kept
        else:
            env["data"] = kept
        trimmed = n < page_rows
        more = (off + n < total) if total is not None else False
        nxt = off + n if more and has_offset and n > 0 else None
        if trimmed:
            summary = (
                f"trimmed: {n} of {page_rows} rows returned to stay within {cap} characters"
                + (f" ({total} in total)" if total is not None else "")
                + (f"; next page: offset={nxt} (offset + rows returned, not offset + limit)" if nxt is not None else "")
                + ("" if has_offset else "; this endpoint has no offset: lower limit or narrow the query")
            )
        elif total is None:
            summary = f"{n} rows returned (ranked top {lim}; no total)"
        elif nxt is not None:
            summary = f"rows {off + 1}-{off + n} of {total}; next page: offset={nxt}"
        else:
            summary = (
                f"rows {off + 1}-{off + n} of {total}; last page"
                if n
                else f"no rows at offset {off} ({total} in total)"
            )
        lists = {k: {"returned": v["returned"], "of": v["of"]} for k, v in trims.items()}
        if trimmed:
            lists[label] = {"returned": n, "of": page_rows}
        env["paging"] = {
            "returned": n,
            "offset": off,
            "limit": lim,
            "total": total,
            "trimmed": trimmed,
            "next_offset": nxt,
            "trimmed_lists": lists,
            "summary": summary,
        }
        warnings = list(base_warnings)
        if t and n < t["returned"]:  # the API's note counts rows this client dropped: say so, keep the API's text
            warnings = [
                f"{w} [this result keeps {n}; see paging]" if TRIM_RE.match(w) and w.startswith(f"{label}:") else w
                for w in warnings
            ]
        env["warnings"] = warnings + ([summary] if trimmed else [])
        return result_size(env)

    if build(len(rows)) > max_chars:
        lo, hi = 0, len(rows) - 1
        while lo < hi:  # largest n whose result fits
            mid = (lo + hi + 1) // 2
            if build(mid) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        build(lo)
    return env
