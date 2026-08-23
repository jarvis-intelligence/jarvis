---
last_mapped_commit: 55a25abf97c4ffd41cd326e8216b1497145b72d4
---

# External Integrations

**Analysis Date:** 2026-08-21

## External Binaries (subprocess)

All external binaries are driven via `subprocess.run()` in `src/jarvis/index_cli.py` (the `_run()` helper merges env over `os.environ`). None are Python libraries — they are CLI tools installed to `~/.jarvis/bin/` by `setup.sh`.

**SCIP ecosystem:**
- `scip` — `scip index` (language-specific), `scip expt-convert` (protobuf → SQLite), `scip --version` (version gate)
  - Installed from fork `phuongddx/scip` at commit `56791658a873` (upstream v0.9.0 + relationships fix scip-code/scip#465)
  - Pinned in `setup.sh` (`SCIP_COMMIT_PIN`) and `SCIP_COMMIT` (must match; enforced by `tests/test_setup_sh.py`)
  - Version gate: `src/jarvis/index_cli.py` rejects `scip` below v0.9.0 (`MIN_SCIP_VERSION`)
- `scip-typescript` (`@sourcegraph/scip-typescript`) — npm global, indexes TypeScript/TSX repos
  - Invoked: `scip-typescript npm <package_name> <version> <output.scip>`
- `scip-python` (`@sourcegraph/scip-python`) — npm global, indexes Python repos
  - Invoked: `scip-python <output.scip>`
- `scip-java` (`scip-code/scip-java` v0.13.1) — self-contained launcher (embedded JAR), indexes Java/Kotlin repos
  - Requires JVM; Kotlin plugin compiled against Kotlin 2.2.0 exactly
  - `src/jarvis/index_cli.py`'s `_java_indexer_env()` puts shim dir first on PATH (bash ≥4.4 requirement)
  - Known failure: `AbstractMethodError` / `NoSuchMethodError` with wrong Kotlin version → degrades to search-only
  - Known failure: bash 3.2 `unbound variable` → degrades to search-only with remedy message
- `scip-swift` (`jarvis-intelligence/scip-swift` v0.1.2) — macOS arm64 only, indexes Swift repos
  - Uses xcodebuild backend when `.xcodeproj`/`.xcworkspace` detected (`src/jarvis/index_cli.py` `_prefers_xcodebuild()`)
  - Requires Xcode

**Zoekt:**
- `zoekt-git-index` (from `sourcegraph/zoekt` at commit `33f1f18af292`) — indexes git-tracked files into Zoekt shard format
  - Pinned in `setup.sh` (`ZOEKT_COMMIT_PIN`) and `ZOEKT_COMMIT` (must match; enforced by `tests/test_setup_sh.py`)
  - Invoked: `zoekt-git-index -repo <slug> -branch <branch> <repo_path>`
- `zoekt-webserver` — serves search API over HTTP
  - Lazily spawned by `ZoektLifecycle` in `src/jarvis/search.py`; owned PID with pidfile, health-checked, killed on process exit
  - Binary path overridable via `JARVIS_ZOEKT_BIN`

**Git:**
- `git` — used for: `git ls-files` (language detection, tracked file count), `git rev-parse HEAD` (commit SHA), `git config zoekt.name` (repo name pinning), `git ls-files -z --others --exclude-standard` (gitignored detection in `src/jarvis/chunker.py`)

## Data Storage

**SQLite (stdlib `sqlite3`, no ORM):**
- `~/.jarvis/registry.db` — repo registry table (`repos`: slug, path, language, commit_sha, last_indexed, status, scheme_override, language_override, search_only, semantic_include, tracked_files)
  - Written by: `src/jarvis/registry.py` (`Registry` class)
- `~/.jarvis/scip/_/<slug>/_/index-<sha>.db` — per-repo SCIP navigation index (versioned, immutable after publish)
  - Schema: vendored from `scip expt-convert` v0.7.0-era (tables: `documents`, `global_symbols`, `external_symbols`, `chunks`, `mentions`)
  - Opened `mode=ro&immutable=1` for queries; never mutated in place
  - Connection cache: `src/jarvis/index_reader.py` (`IndexConnectionCache`, keyed on `(project, repo, branch, pointer_content)`)
  - Queried by: `src/jarvis/query.py` (`QueryService`) and `src/jarvis/graph.py` (`extract_package_names`)
  - Atomic publish: write new `index-<sha>.db`, then `os.replace()` on `current` pointer (`src/jarvis/index_cli.py` `_publish_atomically()`)
- `~/.jarvis/registry-graph.db` — package dependency graph (tables: `packages`, `edges`)
  - Written by: `src/jarvis/graph.py` (`GraphStore`)
  - Rebuild-not-accumulate: `populate_graph_for_repo()` deletes old edges before recomputing

**LanceDB (optional, `semantic` extra):**
- `~/.jarvis/lancedb/<slug>.lance/` — one LanceDB table per repo
  - Written by: `src/jarvis/semantic.py` (`SemanticStore`)
  - Stores: file-path, start/end line, content, vector (1024-dim from BAAI/bge-m3), file hash, language, content format version
  - Model-identity rule: a table only ever holds vectors from one model+revision (`TableIdentity` dataclass in `src/jarvis/semantic.py`)

**Zoekt shard files:**
- `~/.jarvis/zoekt/` — Zoekt index shards, managed by `zoekt-git-index`
  - Stranded `.tmp` orphans cleaned by `src/jarvis/index_cli.py` `_sweep_zoekt_tmp_orphans()`

**Filesystem:**
- `~/.jarvis/scip/_/<slug>/_/current` — pointer file containing versioned db filename
- `~/.jarvis/scip/_/<slug>/_/index-<sha>.metadata.json` — sibling metadata (commit, generatedAt)
- `~/.jarvis/bin/` — pinned external binaries downloaded by `setup.sh`
- `~/.jarvis/shims/` — symlinks to system tools (currently bash ≥4.4 for scip-java compat)

## Authentication & Identity

**Auth Provider:** None
- jarvis is a single-user, local-first tool with no authentication, no multi-tenancy, no network surface
- Single-tenant hardcoding in `src/jarvis/config.py`: `PROJECT = "_"`, `BRANCH = "_"`

## Monitoring & Observability

**Error Tracking:** None

**Logs:**
- `src/jarvis/index_cli.py`: CLI output to stdout, warnings/errors to stderr
- `src/jarvis/embeddings.py`: warnings for missing prefix configuration
- `src/jarvis/server.py`: tools catch exceptions broadly and return `{"error": "..."}` dicts (never raise — keeps stdio server alive)

## CI/CD & Deployment

**Hosting:**
- PyPI (`jarvis-mcp` distribution)
- GitHub: `jarvis-intelligence/jarvis` (private source), `jarvis-intelligence/jarvis-index` (public, hosts release assets for zoekt/scip binaries)

**CI Pipeline:**
- `.github/workflows/test.yml` — unit tests on every push/PR (ubuntu py3.12, ubuntu py3.13, macos py3.13); installs `--extra semantic`; runs `scripts/check_versions.py`
- `.github/workflows/publish-pypi.yml` — builds compiled wheels via cibuildwheel, uploads to PyPI
- `.github/workflows/build-scip.yml` — cross-compiles forked scip binary at pinned commit
- `.github/workflows/setup-smoke.yml` — verifies `setup.sh` installs real binaries
- `.github/workflows/build-zoekt.yml` — builds zoekt from pinned commit
- `.github/workflows/publish-mcp-registry.yml` — updates MCP registry
- `.github/workflows/sync-public-distribution.yml` — syncs public distribution repo

## Environment Configuration

**Required env vars (runtime):**
- None required — all have sensible defaults (`~/.jarvis` data dir, `zoekt-webserver` on PATH, BAAI/bge-m3 model)

**Optional `JARVIS_`-prefixed overrides:**
- `JARVIS_DATA_DIR` — data directory (default: `~/.jarvis`)
- `JARVIS_ZOEKT_BIN` — zoekt-webserver binary path or argv list
- `JARVIS_EMBEDDING_MODEL` — embedding model name (default: `BAAI/bge-m3`)
- `JARVIS_EMBEDDING_BATCH_SIZE` — embedding batch size (default: 8)
- `JARVIS_EMBEDDING_QUERY_PREFIX` — query instruction prefix
- `JARVIS_EMBEDDING_DOC_PREFIX` — document instruction prefix
- `JARVIS_COMPILE` — enable Cython compilation (default: off; only set by release CI)
- `JARVIS_BIN_DIR` — override binary install directory (for `setup.sh`)
- `JARVIS_SETUP_SOURCED` — test seam flag for `setup.sh`

**Secrets location:**
- No secrets — local-first tool with no remote API keys
- HuggingFace model download (sentence-transformers) uses default cache (`~/.cache/huggingface/`)

## Webhooks & Callbacks

**Incoming:** None

**Outgoing:** None

## MCP stdio Surface

**Server:** `src/jarvis/server.py` — `FastMCP("jarvis")`, entry point `jarvis-server` (defined in `pyproject.toml` `[project.scripts]`)
- Transport: stdio only (defined in `server.json`)
- 9 registered tools: `documentSymbols`, `goToDefinition`, `findReferences`, `callHierarchy`, `typeHierarchy`, `getIndexStatus`, `searchCode`, `semanticSearch`, `blastRadius`
- All tools catch exceptions and return error dicts rather than raising
- Manifest: `server.json` — MCP registry schema 2025-12-11, references `jarvis-mcp` on PyPI

---

*Integration audit: 2026-08-21*
