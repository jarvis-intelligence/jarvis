---
focus: tech
last_mapped_commit: 7911fc568fbdc8c4736c068477cb47157fd5cfea
---

# Technology Stack

**Analysis Date:** 2026-09-08

## Languages

**Primary:**
- Python — all product code in `src/jarvis/` (18 modules, ~7,100 lines). Requires `>=3.12,<3.15`; `.python-version` pins `3.12` for local dev.

**Secondary:**
- POSIX shell — `setup.sh` (1,020 lines), the dependency bootstrapper. STRICTLY POSIX `sh` (no arrays, no `[[ ]]`): `curl | sh` runs it under dash on many Linux distros. CI guards this with `sh -n setup.sh` in `.github/workflows/setup-smoke.yml`.
- Cython — release-only compilation of the Python modules (`setup.py`), never used in dev installs.
- Go 1.25 — CI-only, for cross-compiling the vendored scip fork and zoekt (`.github/workflows/build-scip.yml`, `.github/workflows/build-zoekt.yml`). No Go source lives in this repo.
- SQL — SQLite schema consumed (not authored) by jarvis: `scip expt-convert` emits the index DBs; jarvis owns `registry.db`'s DDL in `src/jarvis/registry.py` and `src/jarvis/graph.py`.

## Runtime

**Environment:**
- Python 3.12 / 3.13 / 3.14. The `<3.15` cap is load-bearing for `uvx --from jarvis-mcp jarvis-server`: the wheel matrix only builds cp312–cp314 and there is NO sdist, so an uncapped floor would let uv resolve 3.15, find no wheel, and fail outright (comment in `pyproject.toml`).

**Package Manager:**
- uv (astral). `uv.lock` present (99 packages); CI uses `astral-sh/setup-uv@v5` everywhere.
- Install paths: `uv tool install jarvis-mcp` (end users), `uv sync --extra semantic` (source checkouts), `uvx --from jarvis-mcp jarvis-server` (Claude Code plugin — cache pre-warmed by `setup.sh`'s `install_jarvis_mcp`).
- Distribution name on PyPI is `jarvis-mcp`; the import package, both CLIs, and the MCP server are all `jarvis` (naming comment in `pyproject.toml`). This mapping MUST stay in sync with the PyPI trusted publisher.

**External binaries (installed by `setup.sh` into `~/.jarvis/bin`, resolved from PATH at runtime):**
- `scip` — Go binary from the fork `phuongddx/scip` pinned via repo-root `SCIP_COMMIT` (`56791658a873`; `SCIP_COMMIT_PIN` in `setup.sh` must not drift — `tests/test_setup_sh.py` asserts it). Used only for `scip expt-convert` (`src/jarvis/index_cli.py:1151`). Runtime floor `MIN_SCIP_VERSION = (0, 9, 0)` in `src/jarvis/index_cli.py:78`.
- `zoekt-git-index` / `zoekt-webserver` — Go binaries from `sourcegraph/zoekt` pinned via `ZOEKT_COMMIT` (`33f1f18af292`). Upstream publishes no releases, so `build-zoekt.yml` cross-compiles and publishes to the public `jarvis-intelligence/jarvis-index` repo.
- `scip-python`, `scip-typescript` — npm globals (`@sourcegraph/scip-python`, `@sourcegraph/scip-typescript`) via `install_npm_indexer` in `setup.sh`.
- `scip-java` — v0.13.1 self-contained launcher (~86 MB) from `scip-code/scip-java`; needs a JDK; its kotlinc plugin requires Kotlin 2.2.0 EXACTLY (`SCIP_JAVA_KOTLIN` in `setup.sh`).
- `scip-swift` — macOS arm64 only, auto-rolls to latest release with a `>= 0.3.0` floor (`SCIP_SWIFT_MIN_VERSION`); needs Xcode/xcodebuild.
- `git` — language detection, tracked-file counts, zoekt repo naming (`src/jarvis/index_cli.py`, `src/jarvis/chunker.py`).
- `bash` >= 4.4 — required by scip-java's generated javac wrapper; macOS ships 3.2, so `setup.sh`'s `install_bash_shim` symlinks a Homebrew bash into `~/.jarvis/shims/` on darwin (probed via `BASH_SHIM_CANDIDATES`), and `src/jarvis/index_cli.py:271` (`_java_indexer_env`) prepends that shim dir to PATH for Java indexing.

## Frameworks

**Core:**
- `mcp[cli]>=1.2.0,<2.0.0` (locked 1.28.1) — MCP server via `from mcp.server.fastmcp import FastMCP` in `src/jarvis/server.py:14`. The `<2.0.0` cap exists because mcp 2.0.0 removed `mcp.server.fastmcp`; 9 tools registered, stdio transport (`mcp.run()` in `src/jarvis/server.py:494`).

**Testing:**
- `pytest>=8.3` (dev group; locked 9.1.1). Config in `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, custom `integration` marker for tests that exercise real external binaries.

**Build/Dev:**
- setuptools `>=80.9,<90` + Cython `>=3.1,<4` build backend (`pyproject.toml` `[build-system]`).
- cibuildwheel v4.2.0 — 12-wheel matrix: cp312/cp313/cp314 × linux x86_64/aarch64 × macOS arm64/x86_64; musllinux and Windows skipped (`[tool.cibuildwheel]` in `pyproject.toml`, runners supplied by `.github/workflows/publish-pypi.yml`). No sdist is ever built.
- `scripts/check_wheel_contents.py` — guards that published wheels contain compiled modules only.
- `scripts/check_versions.py` — asserts version consistency (`pyproject.toml` vs `server.json`).

## Key Dependencies

**Critical (base install):**
- `mcp[cli]>=1.2.0,<2.0.0` — the entire server surface.
- `protobuf>=7.35.1,<8.0.0` (locked 7.35.1) — runtime must stay >= the gencode version in the vendored `src/jarvis/scip_pb2.py` (generated against libprotoc 35.1; `ValidateProtobufRuntimeVersion` refuses older runtimes).
- `zstandard>=0.23.0,<1.0.0` (locked 0.25.0) — decompresses `.scip` files in `src/jarvis/scip_decoder.py:37`.
- `httpx>=0.27` (locked 0.28.1) — HTTP client for zoekt-webserver's JSON API (`src/jarvis/search.py:24`). The ONLY runtime network client.

**Optional extras (all lazily imported — a base install never needs them):**
- `watch` extra: `watchdog>=4.0` (locked 6.0.0) — imported inside `_cmd_watch` in `src/jarvis/index_cli.py:1570`.
- `semantic` extra: `lancedb>=0.20` (0.34.0), `sentence-transformers>=3.0` (5.6.1, pulls torch 2.13.0), `tree-sitter>=0.25` (0.26.0), `tree-sitter-language-pack>=0.1` (1.13.6).
  - `lancedb` imported lazily in `SemanticStore._connect` (`src/jarvis/semantic.py:157`); `sentence_transformers` lazily in `EmbeddingModel._load` (`src/jarvis/embeddings.py:102`); `tree_sitter_language_pack.get_parser` lazily in `src/jarvis/chunker.py:387` with fixed-window fallback.
  - Extra detection uses `importlib.util.find_spec` (`src/jarvis/index_cli.py:746`); `jarvis index` interactively offers to install it via `uv pip install --python <sys.executable> jarvis-mcp[semantic]` (`_install_semantic_extra`, `src/jarvis/index_cli.py:761`).

**Standard-library-only storage:** raw `sqlite3` — no ORM, no SQL builder. See `src/jarvis/registry.py`, `src/jarvis/graph.py`, `src/jarvis/index_reader.py`, `src/jarvis/query.py`, `src/jarvis/symbols.py`, `src/jarvis/symbol_search.py`.

## Configuration

**Environment (runtime):**
- `JARVIS_DATA_DIR` — overrides the data dir (default `~/.jarvis`); read ONLY in `src/jarvis/config.py:66` (`data_dir`), the house pattern: env reads live in config, not at call sites.
- `JARVIS_FALLBACK_SEARCH_ONLY` — global default for opt-in self-healing fallback; accepts exactly `1/true/yes/on` case-insensitively, anything else warns to stderr and reads off (`src/jarvis/config.py:76`).
- `JARVIS_ZOEKT_BIN` — zoekt-webserver binary override (`src/jarvis/search.py:148`).
- `JARVIS_EMBEDDING_MODEL`, `JARVIS_EMBEDDING_BATCH_SIZE`, `JARVIS_EMBEDDING_QUERY_PREFIX`, `JARVIS_EMBEDDING_DOC_PREFIX` — embedding model overrides (`src/jarvis/embeddings.py:52-62`). Default model `BAAI/bge-m3` @ pinned revision.
- Build-only: `JARVIS_COMPILE=1` selects Cython compilation in `setup.py` (set only by release CI via `[tool.cibuildwheel] environment`).

**Environment (setup.sh):**
- `JARVIS_BIN_DIR` — override install dir (default `~/.jarvis/bin`; test seam).
- `JARVIS_DATA_DIR` — where the bash shim dir goes (must match Python's `config.shim_dir()` resolution).
- `ZOEKT_BASE_URL`, `SCIP_SWIFT_API_URL` — download-URL overrides (test seams for local fixtures).
- `BASH_SHIM_CANDIDATES`, `FORCE`, `JARVIS_SETUP_SOURCED` (tests source the script instead of executing it).

**Data-dir layout (under `~/.jarvis` unless `JARVIS_DATA_DIR`):**
- `bin/` — downloaded binaries; `shims/` — bash symlink; `registry.db` — repo registry + package graph.
- `scip/_/<slug>/_/` — versioned SQLite index DBs plus the atomic `current` pointer (`index_dir` in `src/jarvis/config.py`).
- `.zoekt/` — zoekt shards; `lancedb/<slug>.lance/` — semantic vectors; `cache/scip-swift/<slug>/` — per-repo Swift incremental cache; `zoekt-webserver.pid`.

**Build:**
- `pyproject.toml` (single source of truth for deps, extras, entry points, cibuildwheel, pytest).
- `setup.py` — Cython compilation with `StripCompiledSources` build_py override; keeps only `__init__.py` and `scip_pb2.py` plain in compiled wheels.
- `uv.lock` — lockfile for dev/CI resolution only; PyPI installs resolve fresh.

## Platform Requirements

**Development:**
- macOS or Linux, arm64 or amd64 (`detect_os`/`detect_arch` in `setup.sh` reject everything else). Python 3.12 via `.python-version`; uv required.
- Full indexing capability additionally needs: Node/npm (TS + Python indexers), JDK (Java/Kotlin, Kotlin 2.2.0 exact), Xcode (Swift — macOS arm64 only, `scip-swift --version` prints e.g. `0.3.0 (swift 6.2.4)`), Homebrew bash >= 4.4 (Java on macOS).

**Production:**
- Published as `jarvis-mcp` on PyPI: 12 compiled wheels (no sdist, no musllinux, no Windows) via trusted publishing in `.github/workflows/publish-pypi.yml`.
- Also distributed through the official MCP Registry (`server.json`, published by `.github/workflows/publish-mcp-registry.yml`) and a Claude Code plugin living in the public `jarvis-intelligence/jarvis-index` repo.
- End users run the MCP server over stdio (`jarvis-server`), typically registered with `claude mcp add jarvis --scope user -- jarvis-server`.

---

*Stack analysis: 2026-09-08*
