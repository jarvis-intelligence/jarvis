# Lexical Search P0 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the five P0 defects research found in jarvis's lexical search stack: unbounded line payloads, missing result totals/truncation signal, unsafe webserver lifecycle, a silently-dead `sym:` signal (no universal-ctags), and a dead lexical leg on natural-language `semanticSearch` queries.

**Architecture:** All changes stay inside the existing seams — `search.py` (zoekt HTTP client + `ZoektLifecycle`), `server.py` (MCP tool wrappers), `semantic.py` (fusion), `setup.sh` (binary bootstrapper). No new modules, no new dependencies, no zoekt pin change. Zoekt-side facts verified against the pinned commit `33f1f18af292`: the JSON `/api/search` accepts `Opts` (Go field names verbatim: `MaxDocDisplayCount`, `MaxMatchDisplayCount`), returns totals in `Result.Stats.MatchCount`/`FileCount` (accumulated *before* display truncation), registers `GET /healthz` unconditionally (200 = zoekt identity + shards loaded, canary search, 500 until ready), and applies **no caps of any kind when `Opts` is absent** (`internal/json/json.go` skips `SetDefaults`). `TotalMatchCount` does **not** exist at this pin — do not reference it.

**Tech Stack:** Python 3.12+ (stdlib `httpx` client already a dependency), pytest with `httpx.MockTransport` + fake-server scripts, POSIX `sh` (dash-compatible) for setup.sh, `shutil.which` for ctags detection.

## Global Constraints

- Python ≥3.12 syntax: `X | None` (never `Optional[T]`), `list[T]`, `dict[K, V]`; `from __future__ import annotations` already present in every touched module.
- Result shapes are frozen dataclasses (never Pydantic); `server.py` converts to dicts by hand, key by key.
- Broad `except Exception` exists ONLY at the MCP tool boundary (`server.py`); everything below uses typed errors (`ZoektUnavailableError`).
- Unit tests mock every external boundary: HTTP via `httpx.MockTransport`, subprocesses via fake scripts. Always isolate the data dir: `monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))` in any test that constructs server-level singletons.
- `setup.sh` is POSIX `sh` (dash-compatible): no bashisms, tab indentation, `log_info`/`log_warn`/`log_error`/`have_cmd`/`already_installed` helpers already exist.
- Commits: Conventional Commits, lowercase imperative (`fix(search): …`, `feat(setup): …`).
- Test gate: `uv run pytest -m "not integration" -rs` must be green. `tests/test_setup_sh.py` requires `dash` (already installed).
- Never spawn `zoekt-webserver` outside `ZoektLifecycle`; status paths must never spawn it.
- Env overrides are `JARVIS_`-prefixed. New one in this plan: `JARVIS_ZOEKT_PORT`.

---

## File Structure

- `src/jarvis/search.py` — all zoekt-client changes: line truncation (Task 1), `ZoektSearchResult` + `Opts` (Task 2), `ZoektLifecycle` hardening (Task 3).
- `src/jarvis/server.py` — `searchCode` response shape + docstring (Task 2), `ctagsInstalled` capability field (Task 4).
- `src/jarvis/semantic.py` — `_zoekt_lexical_query` NL→OR preprocessing (Task 5).
- `setup.sh` — `install_ctags` + wiring + help text (Task 4).
- `README.md` — binary table: add ctags row, fix stale `zoekt-index` name (Task 4).
- Tests mirror src: `tests/test_search.py`, `tests/test_server_tools.py`, `tests/test_semantic.py`, `tests/test_setup_sh.py`.

Task order is dependency-safe: 1 and 2 both touch `search.py` (1 first, trivial); 5 depends on 2's return-type change; 3 and 4 are independent.

---

### Task 1: Truncate oversized zoekt line payloads

Minified/one-line files make a single zoekt `LineMatch.Line` megabytes long; `_decode_line` returns it verbatim into MCP responses.

**Files:**
- Modify: `src/jarvis/search.py:39-48` (`_decode_line` + new constants near the top of the module)
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: module constants `MAX_LINE_CHARS: int` and `_TRUNCATED_SUFFIX: str`; `_decode_line(raw_line: str) -> str` behavior change (long lines capped + suffixed). `ZoektHit.line_text` now bounded to ≤ `MAX_LINE_CHARS + len(_TRUNCATED_SUFFIX)` chars.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_search.py` (after the existing `search_zoekt` tests, ~line 62; `base64` and `pytest` are already imported):

```python
def test_decode_line_truncates_oversized_lines():
    from jarvis.search import MAX_LINE_CHARS, _TRUNCATED_SUFFIX, _decode_line

    encoded = base64.b64encode(("x" * (MAX_LINE_CHARS + 5000)).encode()).decode()
    decoded = _decode_line(encoded)
    assert decoded == "x" * MAX_LINE_CHARS + _TRUNCATED_SUFFIX


def test_decode_line_leaves_normal_lines_alone():
    from jarvis.search import _decode_line

    encoded = base64.b64encode(b"def greet(name):").decode()
    assert _decode_line(encoded) == "def greet(name):"


def test_decode_line_empty_and_invalid_still_safe():
    from jarvis.search import _decode_line

    assert _decode_line("") == ""
    assert _decode_line("!!!not-base64!!!") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_search.py -k decode_line -v`
Expected: FAIL with `ImportError: cannot import name 'MAX_LINE_CHARS'`.

- [ ] **Step 3: Implement**

In `src/jarvis/search.py`, add constants directly above `_decode_line` (line ~38):

```python
# zoekt's LineMatch.Line carries the whole matched line; minified or
# one-line files make single "lines" megabytes long, which would flow
# verbatim into MCP responses. Cap the decoded length; the suffix keeps
# truncation visible to the consumer instead of silently losing text.
MAX_LINE_CHARS = 2000
_TRUNCATED_SUFFIX = " …[truncated]"
```

Replace `_decode_line` (lines 39-48) with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_search.py -v`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/search.py tests/test_search.py
git commit -m "fix(search): cap oversized zoekt line payloads"
```

---

### Task 2: Bound results and report true match totals

Two defects in one contract: (a) with bare `{"Q": q}` zoekt applies **no** display or shard caps, so a broad `searchCode` query streams every match into one MCP payload; (b) the response reports `"total": len(hits)`, conflating "few hits" with "truncated". Fix: send display caps via `Opts`, surface zoekt's own `Stats.MatchCount`/`FileCount` plus `truncated` and `indexedAt`, and surface zoekt 400 parse-error bodies instead of a bare status code.

**Files:**
- Modify: `src/jarvis/search.py:51-91` (`search_zoekt` + new dataclass + constants)
- Modify: `src/jarvis/server.py:413-433` (`search_code`)
- Modify: `src/jarvis/semantic.py:344-349` (call site: `.hits` slice)
- Test: `tests/test_search.py`, `tests/test_server_tools.py:174-197`, `tests/test_semantic.py` (search_zoekt fakes)

**Interfaces:**
- Consumes: Task 1's `_decode_line` (unchanged signature).
- Produces (later tasks rely on these exact names):
  - `ZoektSearchResult` — frozen dataclass, fields `hits: list[ZoektHit]`, `total_matches: int`, `file_count: int`.
  - `search_zoekt(base_url: str, query: str, *, client: httpx.Client | None = None, timeout_seconds: float = 5.0) -> ZoektSearchResult` — **return type changes** from `list[ZoektHit]`.
  - Constants `MAX_DOC_DISPLAY = 50`, `MAX_MATCH_DISPLAY = 200` in `jarvis.search`.
  - `searchCode` response keys: `query`, `hits`, `totalMatches`, `fileCount`, `returned`, `truncated`, `indexedAt` (the old `total` key is removed — clean cutover).

- [ ] **Step 1: Write the failing tests**

In `tests/test_search.py`:

1. Update the `_zoekt_response` fixture (lines 22-37) to include `Stats` — keep its existing `Files` payload (it must keep producing exactly `ZoektHit(repo="toy-repo", path="toy/greeter.py", line_number=5, line_text="def greet(name):")`), adding:

```python
def _zoekt_response(request: httpx.Request) -> httpx.Response:
    encoded_line = base64.b64encode(b"def greet(name):").decode()
    return httpx.Response(200, json={
        "Result": {
            "Files": [{
                "FileName": "toy/greeter.py",
                "Repository": "toy-repo",
                "LineMatches": [{"LineNumber": 5, "Line": encoded_line}],
            }],
            # MatchCount intentionally > the one returned match: proves
            # totals come from zoekt's Stats, not from len(hits).
            "Stats": {"MatchCount": 3, "FileCount": 1},
        },
    })
```

2. Rewrite the consumers and add new tests:

```python
def test_search_zoekt_decodes_base64_line_and_returns_hits():
    client = httpx.Client(transport=httpx.MockTransport(_zoekt_response))
    result = search_zoekt("http://localhost:6070", "greet", client=client)
    assert result.hits == [
        ZoektHit(repo="toy-repo", path="toy/greeter.py", line_number=5, line_text="def greet(name):")
    ]


def test_search_zoekt_reports_true_totals_from_stats():
    client = httpx.Client(transport=httpx.MockTransport(_zoekt_response))
    result = search_zoekt("http://localhost:6070", "greet", client=client)
    assert result.total_matches == 3
    assert result.file_count == 1


def test_search_zoekt_totals_fall_back_when_stats_absent():
    def no_stats(request: httpx.Request) -> httpx.Response:
        encoded = base64.b64encode(b"x = 1").decode()
        return httpx.Response(200, json={"Result": {"Files": [{
            "FileName": "a.py", "Repository": "r",
            "LineMatches": [{"LineNumber": 1, "Line": encoded}],
        }]}})

    result = search_zoekt("http://localhost:6070", "x", client=httpx.Client(transport=httpx.MockTransport(no_stats)))
    assert result.total_matches == 1  # len(hits)
    assert result.file_count == 1    # distinct (repo, path)


def test_search_zoekt_no_hits_returns_empty_result():
    def empty_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Result": {"Files": [], "Stats": {"MatchCount": 0, "FileCount": 0}}})

    client = httpx.Client(transport=httpx.MockTransport(empty_response))
    assert search_zoekt("http://localhost:6070", "no-such-query", client=client).hits == []


def test_search_zoekt_sends_display_cap_opts():
    from jarvis.search import MAX_DOC_DISPLAY, MAX_MATCH_DISPLAY

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"Result": {"Files": [], "Stats": {"MatchCount": 0, "FileCount": 0}}})

    search_zoekt("http://localhost:6070", "q", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert captured["body"]["Opts"] == {
        "MaxDocDisplayCount": MAX_DOC_DISPLAY,
        "MaxMatchDisplayCount": MAX_MATCH_DISPLAY,
    }


def test_search_zoekt_surfaces_parse_error_body():
    def bad_query(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"Error": "parse error: unexpected ')' in query"})

    with pytest.raises(ZoektUnavailableError, match=r"unexpected '\)' in query"):
        search_zoekt("http://localhost:6070", "greet(", client=httpx.Client(transport=httpx.MockTransport(bad_query)))
```

Update `test_search_zoekt_raises_unavailable_on_http_error` unchanged (500 still raises via `raise_for_status`).

In `tests/test_server_tools.py`, update `test_search_code_roundtrip` (lines ~194-197): replace `assert payload["total"] == 1` with:

```python
            assert payload["totalMatches"] == 1
            assert payload["returned"] == 1
            assert payload["truncated"] is False
            assert payload["hits"][0]["repo"] == "toy-repo"
```

(The test's fake zoekt-webserver script serves the canned response without `Stats`, exercising the fallback path.)

In `tests/test_semantic.py`, every fake of `jarvis.semantic.search_zoekt` whose return value flows into `semantic_search` must now return a `ZoektSearchResult`. Known sites (grep `search_zoekt` in the file to catch all): line ~184 becomes

```python
    from jarvis.search import ZoektSearchResult
    monkeypatch.setattr(semantic, "search_zoekt",
                        lambda url, q: ZoektSearchResult(
                            [ZoektHit(repo="myrepo", path="mod_0.py",
                                      line_number=1, line_text="def f_0():")], 1, 1))
```

and any other fake returning a bare list used by `semantic_search` gets the same shape: `lambda url, q: ZoektSearchResult([...], n, n)` (raising fakes like `_boom` stay unchanged).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_search.py tests/test_server_tools.py::test_search_code_roundtrip tests/test_semantic.py -x -v`
Expected: FAIL with `AttributeError: 'list' object has no attribute 'hits'` (or `ImportError: cannot import name 'ZoektSearchResult'`).

- [ ] **Step 3: Implement search.py**

Add below `ZoektHit` (~line 33):

```python
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
```

Replace `search_zoekt` (lines 51-91):

```python
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
            detail = response.json().get("Error", response.text[:300])
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
```

- [ ] **Step 4: Implement server.py search_code**

Replace `search_code` (lines 413-433):

```python
@mcp.tool(name="searchCode")
def search_code(query: str, repo: str | None = None) -> dict[str, Any]:
    """Lexical code search via an embedded Zoekt index (lazy-started on
    first call). `repo`, if given, is applied as a Zoekt `r:` query filter
    scoping results to that one indexed repo; omitted, results span every
    indexed repo. Results answer over the last published index snapshot,
    not the working tree — `indexedAt` says when that was. Zoekt query
    syntax is live: `sym:`, `file:`, `lang:`, `case:` filters, `-term`
    negation, quoted phrases, and `(a or b)` grouping; `sym:` needs
    universal-ctags installed at index time. `truncated` is true when
    zoekt found more matches than the returned cap."""
    scoped_query = f"r:{repo} {query}" if repo else query
    try:
        base_url = _zoekt().ensure_running()
        result = search_zoekt(base_url, scoped_query)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return {"error": str(exc)}
    entry = _registry_entry(repo) if repo else None  # best-effort; None-safe
    return {
        "query": query,
        "hits": [
            {"repo": hit.repo, "path": hit.path, "lineNumber": hit.line_number, "lineText": hit.line_text}
            for hit in result.hits
        ],
        "totalMatches": result.total_matches,
        "fileCount": result.file_count,
        "returned": len(result.hits),
        "truncated": result.total_matches > len(result.hits),
        "indexedAt": entry.last_indexed.isoformat() if entry is not None else None,
    }
```

(`_registry_entry` at `server.py:66` never raises and returns `RegisteredRepo | None`; `last_indexed` is a `datetime` — verified in `registry.py:95`.)

- [ ] **Step 5: Implement semantic.py call site**

Replace lines 344-349:

```python
    zoekt_hits: list[ZoektHit] = []
    if zoekt_base_url is not None:
        try:
            zoekt_hits = search_zoekt(
                zoekt_base_url, f"r:{slug} {query}"
            ).hits[:ZOEKT_TOP_K]
        except ZoektUnavailableError:
            pass  # hybrid degrades to vector-only; sources fields reflect it
```

(Only the `.hits` slice changes here; Task 5 changes the query string itself.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_search.py tests/test_server_tools.py tests/test_semantic.py -v`
Expected: PASS. If a `test_semantic.py` fake still returns a bare list, the `AttributeError: 'list' object has no attribute 'hits'` names the line — wrap it in `ZoektSearchResult([...], n, n)`.

- [ ] **Step 7: Commit**

```bash
git add src/jarvis/search.py src/jarvis/server.py src/jarvis/semantic.py tests/test_search.py tests/test_server_tools.py tests/test_semantic.py
git commit -m "feat(search): bound zoekt results and report true match totals"
```

---

### Task 3: Harden ZoektLifecycle (identity, diagnosability, port, spawn race)

Four failure modes: `_is_healthy` adopts any HTTP server answering <500 on :6070 (zoekt's `GET /healthz` — 200 only after a canary search succeeds — is the correct probe, verified registered unconditionally at the pin, `web/server.go:243-266`); spawn failures are diagnosed blind (stderr DEVNULL); port 6070 is hardcoded with no override; two jarvis processes racing to spawn produce a spurious failure for the loser.

**Files:**
- Modify: `src/jarvis/search.py:128-231` (`ZoektLifecycle`)
- Test: `tests/test_search.py` (lifecycle section, lines ~110-179)

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `ZoektLifecycle.__init__(..., port: int | None = None, ...)` — `None` (new default) resolves from `JARVIS_ZOEKT_PORT` env, falling back to 6070 on absent/invalid values; explicit ints (all existing call sites and tests) still win. New private members `_log_path: Path`, helper `_default_port()`, `_stderr_tail()`. `stop()` now unlinks the pidfile only when it owned the spawned process. Error messages keep the phrase `exited immediately with code` (a test pins it).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_search.py` (lifecycle section; `textwrap`, `sys`, `stat`, `os` already imported):

```python
def test_port_env_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("JARVIS_ZOEKT_PORT", "16123")
    lifecycle = ZoektLifecycle(index_dir=tmp_path / "i", data_dir=tmp_path)
    assert lifecycle.base_url() == "http://127.0.0.1:16123"


def test_port_env_invalid_falls_back(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("JARVIS_ZOEKT_PORT", "not-a-port")
    lifecycle = ZoektLifecycle(index_dir=tmp_path / "i", data_dir=tmp_path)
    assert lifecycle.base_url() == "http://127.0.0.1:6070"


def test_explicit_port_beats_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("JARVIS_ZOEKT_PORT", "16123")
    lifecycle = ZoektLifecycle(index_dir=tmp_path / "i", data_dir=tmp_path, port=16070)
    assert lifecycle.base_url() == "http://127.0.0.1:16070"


def test_base_url_if_running_rejects_foreign_http_server(tmp_path: Path, monkeypatch):
    """A plain HTTP server on the port answers / with 200 but has no
    /healthz; it must not be adopted as zoekt."""
    from types import SimpleNamespace

    (tmp_path / "zoekt-webserver.pid").write_text(str(os.getpid()), encoding="utf-8")
    lifecycle = ZoektLifecycle(index_dir=tmp_path / "i", data_dir=tmp_path)
    monkeypatch.setattr(
        "jarvis.search.httpx.get",
        lambda url, timeout=None: SimpleNamespace(status_code=404 if url.endswith("/healthz") else 200),
    )
    assert lifecycle.base_url_if_running() is None


def test_is_healthy_requires_healthz_200(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    lifecycle = ZoektLifecycle(index_dir=tmp_path / "i", data_dir=tmp_path)
    monkeypatch.setattr("jarvis.search.httpx.get", lambda url, timeout=None: SimpleNamespace(status_code=200))
    assert lifecycle._is_healthy() is True
    monkeypatch.setattr("jarvis.search.httpx.get", lambda url, timeout=None: SimpleNamespace(status_code=500))
    assert lifecycle._is_healthy() is False


def test_failed_spawn_includes_stderr_tail(tmp_path: Path):
    bad = tmp_path / "bind-loser-zoekt"
    bad.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "sys.stderr.write('listen tcp :16071: bind: address already in use\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    bad.chmod(bad.stat().st_mode | stat.S_IEXEC)
    lifecycle = ZoektLifecycle(index_dir=tmp_path / "i", data_dir=tmp_path / "d",
                               port=16071, binary=str(bad))
    with pytest.raises(ZoektUnavailableError) as excinfo:
        lifecycle.ensure_running()
    assert "address already in use" in str(excinfo.value)
    assert "exited immediately" in str(excinfo.value)


def test_spawn_race_adopts_sibling_winner(tmp_path: Path, monkeypatch, fake_zoekt_binary: Path):
    winner = ZoektLifecycle(index_dir=tmp_path / "i", data_dir=tmp_path / "d",
                            port=16072, binary=[sys.executable, str(fake_zoekt_binary)])
    try:
        winner.ensure_running()
        bad = tmp_path / "loser-zoekt"
        bad.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n", encoding="utf-8")
        bad.chmod(bad.stat().st_mode | stat.S_IEXEC)
        loser = ZoektLifecycle(index_dir=tmp_path / "i", data_dir=tmp_path / "d",
                               port=16072, binary=str(bad))
        real_read = loser._read_pidfile
        seen: list[int] = []

        def miss_first_read() -> int | None:
            pid = None if not seen else real_read()
            seen.append(1)
            return pid

        monkeypatch.setattr(loser, "_read_pidfile", miss_first_read)
        # Loser misses the pidfile (the race), spawns, its child dies on the
        # bind conflict, and the re-check adopts the winner's healthy server.
        assert loser.ensure_running() == winner.base_url()
        assert loser._own_process is None
    finally:
        winner.stop()


def test_stop_does_not_unlink_a_pidfile_it_does_not_own(tmp_path: Path):
    lifecycle = ZoektLifecycle(index_dir=tmp_path / "i", data_dir=tmp_path)
    (tmp_path / "zoekt-webserver.pid").write_text("999999999", encoding="utf-8")
    lifecycle.stop()
    assert (tmp_path / "zoekt-webserver.pid").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_search.py -k "port or foreign or healthz or stderr or race or not_unlink" -v`
Expected: FAIL — `test_port_env_override` with `TypeError: ZoektLifecycle() got multiple values / unexpected keyword` (default is currently `port: int = 6070`, so env is ignored → base_url returns 6070, assertion fails); `test_failed_spawn...` fails on missing stderr tail; race test fails with `ZoektUnavailableError`.

- [ ] **Step 3: Implement**

In `src/jarvis/search.py`, add a module-level helper above `ZoektLifecycle`:

```python
def _default_port() -> int:
    """`JARVIS_ZOEKT_PORT` overrides the zoekt-webserver port; an invalid
    value degrades to the default rather than breaking every search."""
    raw = os.environ.get("JARVIS_ZOEKT_PORT", "")
    try:
        return int(raw) if raw else 6070
    except ValueError:
        return 6070
```

Replace `__init__` (lines 136-153) — changed/added lines only:

```python
    def __init__(
        self,
        index_dir: Path,
        data_dir: Path,
        *,
        port: int | None = None,
        binary: str | list[str] | None = None,
        health_timeout_seconds: float = 5.0,
    ) -> None:
        self._index_dir = index_dir
        self._pidfile = data_dir / "zoekt-webserver.pid"
        # stderr of the spawned webserver — surfaced in spawn-failure
        # errors; zoekt's bind failures ("address already in use") are
        # otherwise invisible (stdout/stderr were DEVNULL).
        self._log_path = data_dir / "zoekt-webserver.log"
        self._port = port if port is not None else _default_port()
        resolved_binary = binary or os.environ.get("JARVIS_ZOEKT_BIN", "zoekt-webserver")
        # A list lets a caller (test doubles, mainly) prefix an explicit
        # interpreter rather than relying on the target's own shebang.
        self._argv_prefix = resolved_binary if isinstance(resolved_binary, list) else [resolved_binary]
        self._health_timeout_seconds = health_timeout_seconds
        self._own_process: subprocess.Popen | None = None
```

Replace `_is_healthy` (lines 185-190):

```python
    def _is_healthy(self) -> bool:
        """GET /healthz: zoekt registers it unconditionally and it runs a
        real canary search, returning 500 until shards are loaded — so 200
        proves both identity (not some other HTTP server that grabbed the
        port) and readiness."""
        try:
            response = httpx.get(f"{self.base_url()}/healthz", timeout=1.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200
```

(The existing fake-webserver script answers 200 to every GET — including `/healthz` — so all pre-existing lifecycle tests keep passing.)

Add `_stderr_tail` and replace the spawn block inside `ensure_running` (lines 199-220):

```python
    def _stderr_tail(self, limit: int = 2000) -> str:
        try:
            return self._log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    def ensure_running(self) -> str:
        """Returns the base URL of a healthy zoekt-webserver, spawning one
        if none is already running."""
        existing_pid = self._read_pidfile()
        if existing_pid is not None and self._pid_alive(existing_pid) and self._is_healthy():
            return self.base_url()

        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(self._log_path, "ab")
        try:
            process = subprocess.Popen(
                [*self._argv_prefix, "-index", str(self._index_dir), "-rpc", "-listen", f":{self._port}"],
                stdout=subprocess.DEVNULL,
                stderr=log_file,
            )
        finally:
            log_file.close()  # the child keeps its own duplicated fd

        self._own_process = process
        self._pidfile.write_text(str(process.pid), encoding="utf-8")
        atexit.register(self.stop)

        deadline = time.monotonic() + self._health_timeout_seconds
        while time.monotonic() < deadline:
            if self._is_healthy():
                return self.base_url()
            if process.poll() is not None:
                # A sibling jarvis process may have won the spawn race — its
                # server holding the port is exactly what kills our child.
                # Re-check once before giving up, and adopt the winner.
                sibling = self._read_pidfile()
                if (sibling is not None and sibling != process.pid
                        and self._pid_alive(sibling) and self._is_healthy()):
                    self._own_process = None
                    return self.base_url()
                tail = self._stderr_tail()
                detail = f"\nzoekt-webserver stderr tail:\n{tail}" if tail else ""
                raise ZoektUnavailableError(
                    f"{' '.join(self._argv_prefix)} exited immediately with code {process.returncode}"
                    f" (port {self._port}; log: {self._log_path}){detail}"
                )
            time.sleep(0.1)
        raise ZoektUnavailableError(
            f"{' '.join(self._argv_prefix)} did not become healthy within {self._health_timeout_seconds}s"
        )
```

Replace `stop` (lines 222-231):

```python
    def stop(self) -> None:
        if self._own_process is not None and self._own_process.poll() is None:
            self._own_process.send_signal(signal.SIGTERM)
            try:
                self._own_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._own_process.kill()
            if self._pidfile.exists():
                # Only the spawner removes the pidfile: an adopter (pidfile
                # reuse or race adoption) never owned it.
                self._pidfile.unlink()
        self._own_process = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_search.py -v`
Expected: PASS — all new tests and all pre-existing lifecycle tests (`test_lifecycle_spawns_and_becomes_healthy` still sees the pidfile removed after `stop()`: it spawned, so it owns the pidfile).

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/search.py tests/test_search.py
git commit -m "fix(search): harden zoekt-webserver lifecycle health and spawn failures"
```

---

### Task 4: Install universal-ctags (revive `sym:`) and surface it

zoekt extracts symbols only when `universal-ctags` (or `CTAGS_COMMAND`) exists at index time (`index/builder.go:288-298` at the pin: `HasSymbols = !DisableCTags && CTagsPath != ""`). setup.sh never installs it, so every shard is built `HasSymbols=false` — `sym:` queries silently return nothing and zoekt's symbol-definition ranking tier never fires. This task installs it via the system package manager (warn-and-continue when impossible — ctags absence degrades `sym:` only), reports `ctagsInstalled` in `getIndexStatus`, and documents it.

**Files:**
- Modify: `setup.sh` (new `install_ctags` after `install_zoekt` ~line 614; wiring ~line 1003; help text ~line 916)
- Modify: `src/jarvis/server.py` (imports + `_capability_fields` search section, lines 197-211)
- Modify: `README.md:113-125` (binary table)
- Test: `tests/test_setup_sh.py`, `tests/test_server_tools.py`

**Interfaces:**
- Consumes: existing setup.sh helpers `have_cmd`, `already_installed`, `log_info`, `log_warn`.
- Produces: setup.sh `install_ctags()` (no args, exit 0 on skip/warn, nonzero only when a package-manager install genuinely fails); `--only ctags` supported; server field `capabilities.search.ctagsInstalled: bool`.

- [ ] **Step 1: Write the failing setup.sh tests**

Add to `tests/test_setup_sh.py` (mirrors the suite's fake-and-run_func style; `run_func` restricts PATH to `/usr/bin:/bin` so fakes are the only commands available):

```python
def test_install_ctags_skips_when_already_installed():
    result = run_func('already_installed() { return 0; }\ninstall_ctags')
    assert result.returncode == 0
    assert "already installed" in result.stdout


def test_install_ctags_uses_brew_when_available():
    result = run_func(
        'already_installed() { return 1; }\n'
        'have_cmd() { [ "$1" = brew ]; }\n'
        'brew() { echo "BREW $*"; }\n'
        'install_ctags'
    )
    assert result.returncode == 0
    assert "BREW install universal-ctags" in result.stdout


def test_install_ctags_apt_get_when_root():
    result = run_func(
        'already_installed() { return 1; }\n'
        'have_cmd() { [ "$1" = apt-get ]; }\n'
        'id() { echo 0; }\n'
        'apt-get() { echo "APT $*"; }\n'
        'install_ctags'
    )
    assert result.returncode == 0
    assert "APT install -y -qq universal-ctags" in result.stdout


def test_install_ctags_warns_when_no_installer():
    result = run_func(
        'already_installed() { return 1; }\n'
        'have_cmd() { return 1; }\n'
        'install_ctags'
    )
    # Warn, not fail: ctags absence degrades zoekt sym: only.
    assert result.returncode == 0
    assert "sym:" in (result.stdout + result.stderr)
```

And in `tests/test_server_tools.py` (near the other `_capability_fields`/`_search_coverage_fields` tests):

```python
def test_capability_fields_report_ctags_availability(monkeypatch, tmp_path):
    from jarvis import server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CTAGS_COMMAND", raising=False)
    monkeypatch.setattr(server.shutil, "which",
                        lambda name: None if name == "universal-ctags" else f"/usr/bin/{name}")
    fields = server._capability_fields("nosuchrepo", indexed=False, freshness=None)
    assert fields["capabilities"]["search"]["ctagsInstalled"] is False

    monkeypatch.setenv("CTAGS_COMMAND", "/opt/ctags/bin/ctags")
    fields = server._capability_fields("nosuchrepo", indexed=False, freshness=None)
    assert fields["capabilities"]["search"]["ctagsInstalled"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_setup_sh.py -k ctags tests/test_server_tools.py -k ctags -v`
Expected: FAIL — `install_ctags: not found` (or dash equivalent) in the sh runs; `AttributeError: module 'jarvis.server' has no attribute 'shutil'`.

- [ ] **Step 3: Implement setup.sh**

Insert after `install_zoekt`'s closing brace (after line 614):

```sh
# zoekt auto-discovers universal-ctags on PATH (or $CTAGS_COMMAND) at index
# time; shards built without it carry no symbol sections, so zoekt's sym:
# queries and its symbol-definition ranking silently return nothing
# (index/builder.go: HasSymbols = CTagsPath != ""). Unlike the pinned
# tarball binaries, ctags comes from the system package manager: it is a
# build tool zoo of parsers, not a single static binary. Absence is a warn,
# not a failure -- it degrades sym: only. NOTE: shards indexed before this
# install stay symbol-less until `jarvis reindex <slug>`.
install_ctags() {
	if already_installed universal-ctags; then
		log_info "universal-ctags: already installed, skipping"
		return 0
	fi
	if have_cmd brew; then
		log_info "universal-ctags: installing via brew"
		brew install universal-ctags
	elif have_cmd apt-get && [ "$(id -u)" = "0" ]; then
		log_info "universal-ctags: installing via apt-get"
		apt-get update -qq && apt-get install -y -qq universal-ctags
	else
		log_warn "universal-ctags: no supported installer (need brew, or apt-get as root) -- zoekt sym: queries will return nothing until it is installed (then reindex)"
		return 0
	fi
}
```

Wire into `main()` after the zoekt line (~line 1003):

```sh
	if should_run ctags; then run_one ctags install_ctags; fi
```

Update the `usage()` `--only` list (line ~916) to include `ctags`:

```sh
  --only <name>   Install just one dependency. One of:
                  scip, zoekt, ctags, scip-swift, scip-typescript,
                  scip-python, scip-java, bash-shim, jarvis-mcp
```

- [ ] **Step 4: Implement server.py capability field**

Add to imports (line ~10, alphabetical next to the other stdlib imports):

```python
import os
import shutil
```

Add a helper near `_capability_fields` (before line 144):

```python
def _ctags_available() -> bool:
    """zoekt auto-discovers universal-ctags on PATH (or $CTAGS_COMMAND) at
    index time; shards built without it carry no symbol sections, so sym:
    queries and zoekt's symbol-definition ranking silently do nothing.
    Reports the tool's presence, not per-shard truth: existing shards stay
    symbol-less until a reindex after installation."""
    return bool(os.environ.get("CTAGS_COMMAND")) or shutil.which("universal-ctags") is not None
```

In `_capability_fields`'s returned dict (line ~208), extend the `search` capability:

```python
                "search": {
                    "available": search_available,
                    "reason": None if search_available else "no zoekt shards on disk",
                    "ctagsInstalled": _ctags_available(),
                },
```

- [ ] **Step 5: Update README binary table**

In `README.md`, fix the stale binary name in the lexical-search row (line 118: `zoekt-index` → `zoekt-git-index` — the setup.sh switch to `zoekt-git-index` predates this plan and the row was never updated) and add a ctags row after it:

```markdown
  | Lexical search | `zoekt-git-index` · `zoekt-webserver` | cross-compiled by [our CI](.github/workflows/build-zoekt.yml) — upstream publishes no binaries |
  | Zoekt symbol queries (`sym:`) | `universal-ctags` | system package manager via setup.sh — without it `sym:` silently returns nothing |
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_setup_sh.py tests/test_server_tools.py -v`
Expected: PASS (if a test asserts the exact `--only` list, update it to include `ctags`).

- [ ] **Step 7: Commit**

```bash
git add setup.sh src/jarvis/server.py README.md tests/test_setup_sh.py tests/test_server_tools.py
git commit -m "feat(setup): install universal-ctags so zoekt sym: works"
```

---

### Task 5: OR-expand natural-language queries for the zoekt fusion leg

`semantic_search` sends the raw NL query to zoekt (`semantic.py:347`), but zoekt's default conjunction is implicit AND — `"how does the retry loop back off"` ANDs six words and typically matches nothing, so the lexical signal is dead exactly where `semanticSearch` is used. Fix: detect NL-shaped queries (multi-word, no zoekt syntax) and send an OR-group of `extract_tokens` output; pass identifier/syntax-shaped queries through untouched. zoekts atom-count ranking still favors docs matching more terms, so recall rises without wrecking rank order.

**Files:**
- Modify: `src/jarvis/semantic.py` (new `_zoekt_lexical_query` + call site lines 344-349; ensure `import re`; extend the symbol_search import with `extract_tokens`)
- Test: `tests/test_semantic.py`

**Interfaces:**
- Consumes: Task 2's `search_zoekt(...) -> ZoektSearchResult`; `jarvis.symbol_search.extract_tokens(query: str) -> list[str]` (existing: lowercases, drops <3-char tokens and a small stopword set, keeps dotted tokens whole).
- Produces: `_zoekt_lexical_query(query: str) -> str` — NL multi-word queries with ≥2 surviving tokens → `"(tok1 or tok2 or ...)"`; everything else (syntax-bearing, quoted, parenthesized, single-token) → input unchanged. Call site becomes `search_zoekt(zoekt_base_url, f"r:{slug} {_zoekt_lexical_query(query)}").hits[:ZOEKT_TOP_K]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_semantic.py` (near the RRF tests; imports at top gain `from jarvis.semantic import _zoekt_lexical_query` — extend the existing `from jarvis.semantic import ...` line):

```python
def test_zoekt_lexical_query_or_expands_nl_queries():
    # zoekt's default conjunction is implicit AND: a raw NL query matches
    # nothing. NL legs become OR-groups of identifier-ish tokens.
    assert _zoekt_lexical_query("how does the retry loop back off") == "(retry or loop or back or off)"


def test_zoekt_lexical_query_passes_code_shapes_through():
    assert _zoekt_lexical_query("ZoektLifecycle") == "ZoektLifecycle"
    assert _zoekt_lexical_query("user_profile") == "user_profile"
    assert _zoekt_lexical_query("sym:getUserById") == "sym:getUserById"
    assert _zoekt_lexical_query('"exact phrase"') == '"exact phrase"'
    assert _zoekt_lexical_query("(a or b)") == "(a or b)"


def test_zoekt_lexical_query_falls_back_when_tokens_vanish():
    # "how does it work?" yields a single token ("work") — an OR-group of
    # one is pointless; pass the original through.
    assert _zoekt_lexical_query("how does it work?") == "how does it work?"
```

And an integration-shape test mirroring the existing fake style:

```python
def test_semantic_search_or_expands_zoekt_leg(tmp_path, lancedb_available, monkeypatch):
    from jarvis import semantic
    from jarvis.search import ZoektSearchResult

    captured: dict = {}

    def fake_search(url, q):
        captured["q"] = q
        return ZoektSearchResult([], 0, 0)

    monkeypatch.setattr(semantic, "search_zoekt", fake_search)
    data = _indexed(tmp_path)
    semantic.semantic_search("myrepo", "function zero", root=data,
                             zoekt_base_url="http://x", model=FakeEmbedder())
    assert captured["q"] == "r:myrepo (function or zero)"
```

(`_indexed`, `FakeEmbedder`, `lancedb_available` are existing fixtures/helpers in this file — the test at line ~183 uses exactly this trio.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_semantic.py -k lexical_query -v`
Expected: FAIL with `ImportError: cannot import name '_zoekt_lexical_query'`.

- [ ] **Step 3: Implement**

In `src/jarvis/semantic.py`: ensure `import re` is in the stdlib import block; extend the symbol_search import to `from jarvis.symbol_search import SymbolHit, extract_tokens, search_symbols`. Add above `semantic_search` (near the other module constants, after `CONTENT_TRUNCATE`):

```python
# zoekt query-language field selectors and grouping characters: a query
# containing any of these is authored zoekt syntax (or a quoted/regex
# phrase) and must pass through untouched.
_ZOEKT_SYNTAX_RE = re.compile(
    r"(?:^|\s)(?:r|repo|f|file|sym|lang|l|case|content|c|branch|type|archived|fork|public|regex|meta):"
    r'|["()]'
)


def _zoekt_lexical_query(query: str) -> str:
    """The zoekt leg of semanticSearch. A natural-language query must not
    reach zoekt verbatim: the default conjunction is implicit AND, so six
    NL words typically match nothing and the lexical signal contributes
    zero on exactly the queries semanticSearch exists for. NL-shaped
    queries (multi-word, no zoekt syntax) become an OR-group of
    extract_tokens output — recall up, and zoekt's atom-count scoring
    still favors documents matching more tokens. Anything code-shaped
    (identifiers, sym:/lang: syntax, quoted phrases, grouping) passes
    through untouched.
    """
    if not query or _ZOEKT_SYNTAX_RE.search(query) or len(query.split()) <= 1:
        return query
    tokens = extract_tokens(query)
    if len(tokens) < 2:
        return query
    return "(" + " or ".join(tokens) + ")"
```

Replace the call site (the Task 2 version of lines 344-349):

```python
    zoekt_hits: list[ZoektHit] = []
    if zoekt_base_url is not None:
        try:
            zoekt_hits = search_zoekt(
                zoekt_base_url, f"r:{slug} {_zoekt_lexical_query(query)}"
            ).hits[:ZOEKT_TOP_K]
        except ZoektUnavailableError:
            pass  # hybrid degrades to vector-only; sources fields reflect it
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: PASS — including the pre-existing `test_hybrid_search_fuses_zoekt_and_vector` (its fake intercepts `search_zoekt` regardless of query string) and the degradation tests.

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/semantic.py tests/test_semantic.py
git commit -m "feat(semantic): or-expand natural-language zoekt fusion queries"
```

---

### Task 6: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the complete unit gate**

Run: `uv run pytest -m "not integration" -rs`
Expected: PASS, no failures.

- [ ] **Step 2: Run the version-consistency guard (unchanged code, cheap insurance)**

Run: `uv run python scripts/check_versions.py`
Expected: `versions consistent`.

- [ ] **Step 3: Manual smoke of the setup.sh surface (no install)**

Run: `sh setup.sh --help`
Expected: usage text lists `ctags` in the `--only` options.

---

## Self-Review (completed during planning)

1. **Spec coverage** — all five P0 items map to tasks: line truncation (T1), honest totals/truncation/indexedAt + parse errors (T2), healthz/stderr/port/race (T3), ctags install + surfacing + README (T4), NL OR-expansion (T5). Out of scope by design (P1, separate plan): camelCase splitting, commit-aware watch, BM25-in-fusion, flock/indexer timeout, eval harness.
2. **Placeholder scan** — every step carries actual code; no TBD/TODO/"add error handling" patterns. The one file whose exact body wasn't fully read (`_zoekt_response` fixture lines 24-37, `_FAKE_ZOEKT_SCRIPT` consumer tests) is handled by full replacement bodies that reproduce the asserted contract.
3. **Type consistency** — `ZoektSearchResult(hits, total_matches, file_count)` used identically in T2/T5 and test fakes; `_zoekt_lexical_query` name/signature consistent; `port: int | None = None` matches all existing explicit-int call sites (`server.py:41` passes nothing → env/default 6070, same behavior as before).
4. **Verified against the pin** — `Opts` field names, `Stats.MatchCount`/`FileCount` semantics, `/healthz` registration, no-`Opts`-means-no-caps: all read from `33f1f18af292` sources (`api.go`, `internal/json/json.go`, `web/server.go`) during planning, not assumed from later versions.
