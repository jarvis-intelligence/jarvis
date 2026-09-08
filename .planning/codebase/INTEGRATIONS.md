---
focus: tech
last_mapped_commit: 7911fc568fbdc8c4736c068477cb47157fd5cfea
---

# External Integrations

**Analysis Date:** 2026-09-08

jarvis is local-first: at runtime the only network client is httpx talking to a localhost zoekt-webserver, plus an optional Hugging Face model download when the `semantic` extra is used. The heavyweight "integrations" are local subprocesses (external binaries) and the release/distribution pipeline.

## APIs & External Services

**Local subprocess indexers (driven by `src/jarvis/index_cli.py`):**
- `scip expt-convert --output <db> <scip-file>` — converts a `.scip` occurrence file into the SQLite index jarvis queries (`src/jarvis/index_cli.py:1151`). Version-gated by `MIN_SCIP_VERSION = (0, 9, 0)`.
- `scip-python index`, `scip-typescript index` — per-language SCIP emitters, dispatched via `_LANGUAGE_INDEXERS` (`src/jarvis/index_cli.py:46-59`).
- `scip-java index` — Java/Kotlin; run with a patched env from `_java_indexer_env()` (`src/jarvis/index_cli.py:271`): `GRADLE_OPTS` forced single-threaded (upstream `scip-java#987` race) and the bash-shim dir prepended to PATH (bash >= 4.4 requirement, upstream `scip-java#987`).
- `scip-swift` — Swift; argv extended by `_swift_indexer_cmd()` with `--build-tool xcodebuild` / `--scheme` for `.xcodeproj` repos and always `--cache-dir` pointing under `~/.jarvis/cache/scip-swift/<slug>` (tree-cleanliness contract, asserted in `.github/workflows/setup-smoke.yml`). Runtime version gate `>= 0.3.0` in `check_scip_swift_version()` (`src/jarvis/index_cli.py:505`).
- `zoekt-git-index -index <dir> -incremental=false -submodules=false …` — builds search shards from the git tree so gitignored content never enters the index (`_zoekt_index_cmd`, `src/jarvis/index_cli.py:559-590`). No fallback to `zoekt-index`, deliberately.
- `git` — `ls-files`, tracked-blob counts, remote-origin resolution for zoekt repo naming.

**zoekt-webserver HTTP API (localhost):**
- Client: `src/jarvis/search.py` (httpx).
- `POST /api/search` with body `{"Q": "<query>"}` (webserver must run with `-rpc`); response `{"Result": {"Files": [{"FileName", "Repository", "LineMatches": [{"LineNumber", "Line", …}]}]}}` with base64-encoded `Line`.
- `GET /api/list` — repo document counts, used by the searchCoverage status field (`src/jarvis/server.py:79`).
- Lifecycle: `ZoektLifecycle` (`src/jarvis/search.py:128`) lazily spawns `zoekt-webserver -index <data>/.zoekt -rpc -listen :6070` on first use; a pidfile (`<data>/zoekt-webserver.pid`) lets a fresh MCP server process reuse a running webserver; `atexit` kills only the process this instance started. Base URL `http://127.0.0.1:6070`; binary overridable via `JARVIS_ZOEKT_BIN`. Status calls never spawn a webserver — `base_url_if_running()` (`src/jarvis/search.py:158`) is probe-only.
- Failure mode: `ZoektUnavailableError` on connection errors or non-2xx; surfaced to MCP clients as `{"error": …}`.

**MCP clients ( inbound, stdio ):**
- `jarvis-server` (`src/jarvis/server.py`) speaks MCP over stdio via FastMCP; 9 tools: documentSymbols, goToDefinition, findReferences, callHierarchy, typeHierarchy, getIndexStatus, searchCode, semanticSearch, blastRadius.
- Registration: `claude mcp add jarvis --scope user -- jarvis-server`, or JSON config `{"mcpServers": {"jarvis": {"command": "jarvis-server"}}}` (`README.md`). The Claude Code plugin in the public distribution repo launches it via `uvx --from jarvis-mcp jarvis-server`.

**Hugging Face Hub (implicit, `semantic` extra only):**
- `sentence-transformers` downloads `BAAI/bge-m3` (pinned revision `5617a9f61b028005a4858fdac845db406aefb181`) on first embed (`src/jarvis/embeddings.py:13-14`). Model never loaded at server startup — lazy on first embed, mirroring ZoektLifecycle. Override with `JARVIS_EMBEDDING_MODEL` (revision then unpinned); query/doc instruction prefixes auto-selected per model family (`MODEL_PREFIXES` in `src/jarvis/embeddings.py:33`).
- Model identity `(model_name, revision, prefixes)` is persisted in every LanceDB row; a mismatch forces a full semantic rebuild (`table_identity` in `src/jarvis/semantic.py:171`) — vectors from different models must never mix.

**GitHub Releases (downloads made by `setup.sh`):**
- `jarvis-intelligence/jarvis-index` (PUBLIC artifact repo) — scip and zoekt tarballs + `.sha256` sidecars, pinned to exact commits: scip `56791658a873` (repo-root `SCIP_COMMIT`), zoekt `33f1f18af292` (`ZOEKT_COMMIT`). Sidecar format `<digest>  <filename>` verified by `verify_sha256`.
- `jarvis-intelligence/scip-swift` — `install_scip_swift` (`setup.sh:624`) queries `https://api.github.com/repos/…/releases/latest`, picks the macOS asset by name, verifies GitHub's server-computed immutable `digest` field (sha256:…, 64 hex chars), and enforces the `>= 0.3.0` floor. Every JSON field is shape-validated before use (untrusted network input).
- `scip-code/scip-java` — v0.13.1 single-file launcher + `.sha256` sidecar via `install_raw_binary` (`setup.sh:461-485` area).
- Exit ramp documented in `setup.sh:17-25` and `.github/workflows/build-scip.yml`: when upstream scip-code/scip merges #465 and releases, repoint at upstream and delete the fork pin.

**npm registry:**
- `npm install -g @sourcegraph/scip-python` and `@sourcegraph/scip-typescript` (`install_npm_indexer`, `setup.sh`). Missing npm is a soft skip with instructions, not a failure.

**PyPI (runtime, optional):**
- `_install_semantic_extra()` (`src/jarvis/index_cli.py:761`) runs `uv pip install --python <sys.executable> jarvis-mcp[semantic]` when an interactive user accepts the post-index offer — a torch-scale download bounded at 600 s; any failure degrades to one stderr warning, rc 0.

## Data Storage

**Databases (all local, no server, no ORM — raw stdlib `sqlite3`):**
- `<data>/registry.db` — repo registry (`src/jarvis/registry.py`) AND package dependency graph (`src/jarvis/graph.py` `GraphStore`). Opened per-call in server tools; single RW database.
- `<data>/scip/_/<slug>/_/<version>.db` — per-repo index DBs produced by `scip expt-convert`, read through `IndexConnectionCache` (`src/jarvis/index_reader.py`, LRU + threading lock) and selected by the atomic `current` pointer file. Queried by `src/jarvis/query.py`, `src/jarvis/symbols.py`, `src/jarvis/symbol_search.py`, `src/jarvis/semantic.py`.
- Publishing is atomic-by-construction: `zoekt-git-index` and the SCIP convert both run BEFORE the pointer swap, so a failed index never leaves a repo half-published (`src/jarvis/index_cli.py:985`).

**Vectors:**
- LanceDB (semantic extra): one table per repo under `<data>/lancedb/`, on-disk `<slug>.lance/` (`SemanticStore`, `src/jarvis/semantic.py:148`). Table opens avoid `table_names()` pagination traps via direct `open_table` + `ValueError` handling.

**File Storage:**
- Local filesystem only (tarball/cache/pidfile/pointer management under `~/.jarvis`). No cloud object storage.

**Caching:**
- `~/.jarvis/cache/scip-swift/<slug>/` — scip-swift incremental build cache (`swift_cache_dir`, `src/jarvis/config.py`); removed on `jarvis forget`.
- uv tool cache pre-warmed by `setup.sh`'s `install_jarvis_mcp` so the plugin's `uvx` launch stays inside the MCP client's ~30 s connect window.

## Authentication & Identity

**Auth Provider (end users):**
- None. Everything is local and unauthenticated by design (single-tenant, localhost-only zoekt listener on 127.0.0.1).

**CI/distribution identities:**
- PyPI trusted publishing — OIDC, short-lived token minted from the publish job (`id-token: write`, `uv publish --trusted-publishing always` in `.github/workflows/publish-pypi.yml`). Publisher pinned to workflow name `publish-pypi.yml`, EMPTY environment (free-plan org cannot provide environments on private repos). Renaming the workflow file breaks publishing.
- MCP Registry — `mcp-publisher login github-oidc` (v1.8.0, `.github/workflows/publish-mcp-registry.yml`); unlocks the `io.github.jarvis-intelligence/*` namespace with no stored secret. Registry verifies ownership via the `mcp-name: io.github.jarvis-intelligence/jarvis` marker in the PyPI README (`README.md:3`; asserted by publish-pypi preflight).
- `JARVIS_DIST_TOKEN` — fine-grained PAT, Contents:write on `jarvis-intelligence/jarvis-index` only, used by build-scip/build-zoekt/sync-public-distribution to publish artifacts to the public repo.

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry/crash reporting).

**Logs:**
- CLI progress and warnings via `print` to stdout/stderr (`src/jarvis/index_cli.py`); MCP tools return structured `{"error": …}` dicts rather than raising across the stdio boundary (`src/jarvis/server.py`). `setup.sh` logs `log_info`/`log_warn`/`log_error` and a per-dependency summary.

## CI/CD & Deployment

**Hosting:**
- None at runtime (local-first). Distribution surfaces: PyPI (`jarvis-mcp`), official MCP Registry (`server.json`), public GitHub repo `jarvis-intelligence/jarvis-index` (binaries, `setup.sh`, Claude Code plugin).

**CI Pipeline (`.github/workflows/`, 7 workflows):**
- `test.yml` — unit suite on push/PR; matrix ubuntu 3.12/3.13 + macos 3.13, `uv sync --extra semantic`, `pytest -m "not integration" -rs`, plus `scripts/check_versions.py`. `UV_PYTHON` env (not `--python`) because `.python-version` otherwise wins.
- `setup-smoke.yml` — runs `setup.sh` on real runners (dash parse check, scip/zoekt install + run, scip-swift latest-resolution against the real GitHub API, idempotency, `tests/test_setup_sh.py` with dash, and a macOS Swift fixture index through `jarvis index` with tree-cleanliness assertions).
- `publish-pypi.yml` — on GitHub Release: preflight (unit tests, tag/version match, README registry marker) → 12 compiled wheels via cibuildwheel on 4 native runners → smoke (fresh `uv pip install` from dist + real MCP handshake, 9-tools check, `.so` compiled-module check) → `uv publish --trusted-publishing always`.
- `publish-mcp-registry.yml` — `workflow_run` after publish-pypi succeeds (ordering matters: registry 404s if PyPI is not yet consistent; publish retries 6×20 s); validates `server.json` versions match `pyproject.toml` and publishes via mcp-publisher.
- `build-scip.yml` / `build-zoekt.yml` — on `SCIP_COMMIT`/`ZOEKT_COMMIT` change or manual dispatch: Go 1.25 cross-compile for darwin/linux × arm64/amd64, sha256 sidecars, native smoke test, release to `jarvis-intelligence/jarvis-index` with `JARVIS_DIST_TOKEN`.
- `sync-public-distribution.yml` — on release: rsyncs `setup.sh` (only) into the public repo and pushes if changed.

## Environment Configuration

**Required env vars:**
- None required. All optional with defaults (see STACK.md Configuration section for the full `JARVIS_*` list: `JARVIS_DATA_DIR`, `JARVIS_FALLBACK_SEARCH_ONLY`, `JARVIS_ZOEKT_BIN`, `JARVIS_EMBEDDING_*`; setup.sh: `JARVIS_BIN_DIR`, `ZOEKT_BASE_URL`, `SCIP_SWIFT_API_URL`, `BASH_SHIM_CANDIDATES`, `FORCE`, `JARVIS_SETUP_SOURCED`).

**Secrets location:**
- `.env`-style files: none. CI secrets (`JARVIS_DIST_TOKEN`) live in GitHub Actions; end users need no credentials.

## Webhooks & Callbacks

**Incoming:**
- None (MCP stdio requests are the only inbound surface).

**Outgoing:**
- None. No telemetry, no update pings.

---

*Integration audit: 2026-09-08*
