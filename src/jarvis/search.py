"""searchCode: a real httpx client for zoekt-webserver's JSON search API,
plus a lazy lifecycle manager (spawn on first use, pidfile, health check,
kill on exit).

Ported from an internal reference implementation's `search_service.py`'s `search_zoekt`/
`_decode_line` — the authorization filtering (`_is_authorized_for_repo`,
`filter_hits_by_authorization`) is dropped entirely: jarvis is single-
user/local-first, every indexed repo belongs to the same person, so there is
no one to authorize against.
"""

from __future__ import annotations

import atexit
import base64
import binascii
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class ZoektHit:
    repo: str
    path: str
    line_number: int
    line_text: str


# Display caps sent with every search. zoekt's JSON API applies NO display
# limit and skips SetDefaults entirely when `Opts` is absent
# (internal/json/json.go at our pin 33f1f18af292), so a broad query streams
# every match into one MCP payload. These bounds mirror what an agent can
# consume; Result.Stats.MatchCount still reports the true total (accumulated
# before display truncation), so totalMatches stays honest. There is no
# TotalMatchCount option at this pin — do not add one.
MAX_DOC_DISPLAY = 50     # files returned per query
MAX_MATCH_DISPLAY = 200  # line matches returned per query


@dataclass(frozen=True)
class ZoektSearchResult:
    """Hits plus zoekt's own totals. `total_matches` is Stats.MatchCount
    (non-overlapping matches found, pre-display-truncation); `file_count`
    is Stats.FileCount (files containing a match)."""
    hits: list[ZoektHit]
    total_matches: int
    file_count: int


class ZoektUnavailableError(Exception):
    """Raised when zoekt-webserver cannot be reached or returns a non-2xx."""


# zoekt's LineMatch.Line carries the whole matched line; minified or
# one-line files make single "lines" megabytes long, which would flow
# verbatim into MCP responses. Cap the decoded length; the suffix keeps
# truncation visible to the consumer instead of silently losing text.
MAX_LINE_CHARS = 2000
_TRUNCATED_SUFFIX = " …[truncated]"


def _decode_line(raw_line: str) -> str:
    """`LineMatch.Line` arrives base64-encoded (Go `[]byte` via
    encoding/json). Never fabricate content on a bad payload — an
    undecodable line degrades to an empty string. Oversized lines are
    capped at MAX_LINE_CHARS with a visible suffix.
    """
    if not raw_line:
        return ""
    try:
        decoded = base64.b64decode(raw_line).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""
    if len(decoded) > MAX_LINE_CHARS:
        return decoded[:MAX_LINE_CHARS] + _TRUNCATED_SUFFIX
    return decoded


def search_zoekt(
    base_url: str, query: str, *, client: httpx.Client | None = None, timeout_seconds: float = 5.0
) -> ZoektSearchResult:
    """Query zoekt-webserver's real JSON search API: `POST /api/search` with
    body `{"Q": ..., "Opts": {...}}` (requires the webserver started with
    `-rpc`). Response shape: `{"Result": {"Files": [...], "Stats": ...}}` —
    `LineMatch.Line` is base64; `Stats.MatchCount`/`FileCount` are the true
    totals. A 400 carries zoekt's parse error in `{"Error": ...}` and is
    surfaced verbatim so a bad query explains itself.

    `client` is injectable (a real `httpx.Client`, or one backed by
    `httpx.MockTransport` in tests); defaults to a short-lived real client.
    """
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.post(
            f"{base_url.rstrip('/')}/api/search",
            json={"Q": query,
                  "Opts": {"MaxDocDisplayCount": MAX_DOC_DISPLAY,
                           "MaxMatchDisplayCount": MAX_MATCH_DISPLAY}},
            timeout=timeout_seconds,
        )
        if response.status_code == 400:
            try:
                detail = response.json().get("Error", response.text[:300])
            except ValueError:  # non-JSON body (e.g. a plain-text proxy error)
                detail = response.text[:300]
            raise ZoektUnavailableError(f"zoekt rejected the query: {detail}")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ZoektUnavailableError(f"zoekt-webserver request failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    payload = response.json()
    hits: list[ZoektHit] = []
    for file_result in payload.get("Result", {}).get("Files", []) or []:
        repo = file_result.get("Repository", "")
        path = file_result.get("FileName", "")
        for line_match in file_result.get("LineMatches", []) or []:
            hits.append(
                ZoektHit(
                    repo=repo,
                    path=path,
                    line_number=line_match.get("LineNumber", 0),
                    line_text=_decode_line(line_match.get("Line", "")),
                )
            )
    stats = payload.get("Result", {}).get("Stats", {}) or {}
    return ZoektSearchResult(
        hits=hits,
        # Absent Stats (older/mock servers) degrade to countable truths
        # rather than a fabricate-by-zero.
        total_matches=stats.get("MatchCount", len(hits)),
        file_count=stats.get("FileCount", len({(hit.repo, hit.path) for hit in hits})),
    )


def zoekt_repo_documents(
    base_url: str, repo: str, *, client: httpx.Client | None = None, timeout_seconds: float = 5.0
) -> int | None:
    """How many documents zoekt currently holds for `repo`, or None when
    zoekt does not know that repo at all.

    Authoritative in a way the indexer's own log is not: it reflects the
    shards on disk *now*, so it detects shards deleted after a successful
    index — the failure mode that made a truncated index look healthy.

    `client` is injectable (a real `httpx.Client`, or one backed by
    `httpx.MockTransport` in tests); defaults to a short-lived real client.
    """
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.post(
            f"{base_url.rstrip('/')}/api/list",
            json={"Q": f"r:{repo}"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ZoektUnavailableError(f"zoekt-webserver /api/list failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    for entry in (response.json().get("List", {}).get("Repos") or []):
        if entry.get("Repository", {}).get("Name") == repo:
            return entry.get("Stats", {}).get("Documents")
    return None


class ZoektLifecycle:
    """Lazy-spawns `zoekt-webserver -index <index_dir> -rpc -listen :<port>`
    on first `ensure_running()` call. A pidfile under `data_dir` survives
    across jarvis processes so a second `ensure_running()` call (e.g. a
    fresh MCP server process) reuses an already-running webserver instead
    of spawning a duplicate; `atexit` kills the process this instance
    itself started, so a clean process exit leaves no orphan."""

    def __init__(
        self,
        index_dir: Path,
        data_dir: Path,
        *,
        port: int = 6070,
        binary: str | list[str] | None = None,
        health_timeout_seconds: float = 5.0,
    ) -> None:
        self._index_dir = index_dir
        self._pidfile = data_dir / "zoekt-webserver.pid"
        self._port = port
        resolved_binary = binary or os.environ.get("JARVIS_ZOEKT_BIN", "zoekt-webserver")
        # A list lets a caller (test doubles, mainly) prefix an explicit
        # interpreter rather than relying on the target's own shebang.
        self._argv_prefix = resolved_binary if isinstance(resolved_binary, list) else [resolved_binary]
        self._health_timeout_seconds = health_timeout_seconds
        self._own_process: subprocess.Popen | None = None

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def base_url_if_running(self) -> str | None:
        """The base URL of an already-healthy webserver, or None — never
        spawns one.

        `getIndexStatus` reports search coverage, which needs zoekt's
        `/api/list`, but a status call must stay cheap: spawning a webserver
        as a side effect of asking for status would be surprising. In
        practice the server is already up whenever searches are happening.
        """
        pid = self._read_pidfile()
        if pid is None or not self._pid_alive(pid) or not self._is_healthy():
            return None
        return self.base_url()

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def _read_pidfile(self) -> int | None:
        try:
            return int(self._pidfile.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            return None

    def _is_healthy(self) -> bool:
        try:
            response = httpx.get(self.base_url(), timeout=1.0)
        except httpx.HTTPError:
            return False
        return response.status_code < 500

    def ensure_running(self) -> str:
        """Returns the base URL of a healthy zoekt-webserver, spawning one
        if none is already running."""
        existing_pid = self._read_pidfile()
        if existing_pid is not None and self._pid_alive(existing_pid) and self._is_healthy():
            return self.base_url()

        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [*self._argv_prefix, "-index", str(self._index_dir), "-rpc", "-listen", f":{self._port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._own_process = process
        self._pidfile.write_text(str(process.pid), encoding="utf-8")
        atexit.register(self.stop)

        deadline = time.monotonic() + self._health_timeout_seconds
        while time.monotonic() < deadline:
            if self._is_healthy():
                return self.base_url()
            if process.poll() is not None:
                raise ZoektUnavailableError(
                    f"{' '.join(self._argv_prefix)} exited immediately with code {process.returncode}"
                )
            time.sleep(0.1)
        raise ZoektUnavailableError(
            f"{' '.join(self._argv_prefix)} did not become healthy within {self._health_timeout_seconds}s"
        )

    def stop(self) -> None:
        if self._own_process is not None and self._own_process.poll() is None:
            self._own_process.send_signal(signal.SIGTERM)
            try:
                self._own_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._own_process.kill()
        self._own_process = None
        if self._pidfile.exists():
            self._pidfile.unlink()
