---
last_mapped_commit: 55a25abf97c4ffd41cd326e8216b1497145b72d4
---

# Technology Stack

**Analysis Date:** 2026-08-21

## Languages

**Primary:**
- Python 3.12+ (3.12–3.14 supported) — all source in `src/jarvis/`, tests in `tests/`, CLI entry points, MCP server
- POSIX sh — `setup.sh` dependency bootstrapper (strictly POSIX, no bashisms for `curl | sh` compat)
- Protocol Buffers — `src/jarvis/scip_pb2.py` is vendored gencode (generated against `libprotoc` 35.1 at SCIP v0.9.0)

**Secondary:**
- Cython 3.x — compile gate in `setup.py`; only active under `JARVIS_COMPILE=1` (release CI). Dev builds stay pure Python.

## Runtime

**Environment:**
- Python ≥3.12, <3.15 (capped: compiled-wheel matrix only covers cp312–cp314)
- `.python-version` pins 3.12 for local development
- CI matrix: ubuntu-latest py3.12, ubuntu-latest py3.13, macos-latest py3.13

**Package Manager:**
- `uv` — primary package manager
- Lockfile: `uv.lock` (present, version 1 revision 3)
- Distribution name on PyPI: `jarvis-mcp`; import package: `jarvis`

## Frameworks

**Core:**
- MCP (Model Context Protocol) 1.28.1 via `mcp[cli]>=1.2.0,<2.0.0` — `src/jarvis/server.py` uses `FastMCP` to register 9 tools over stdio transport

**Testing:**
- pytest 9.1.1 — `tests/` directory, markers for `integration`

**Build/Dev:**
- setuptools 83.0.0 + Cython 3.x — `setup.py` overrides `build_py.find_package_modules` to strip `.py` sources when compiled extensions ship
- cibuildwheel — compiled wheel matrix: cp312–cp314 × {linux x86_64/aarch64, macOS arm64/x86_64}; musllinux and Windows skipped

## Key Dependencies

**Critical (required at runtime):**
- `mcp[cli]` 1.28.1 — MCP stdio server framework (`FastMCP`)
- `protobuf` 7.35.1 — decode SCIP protobuf blobs in `src/jarvis/scip_decoder.py`; floor matches vendored gencode
- `zstandard` 0.25.0 — decompress zstd-compressed blob columns from SCIP indexes in `src/jarvis/scip_decoder.py`
- `httpx` 0.28.1 — HTTP client for `zoekt-webserver` JSON API in `src/jarvis/search.py`

**Optional — `watch` extra:**
- `watchdog` 6.0.0 — filesystem event observer for `jarvis watch` subcommand; `src/jarvis/watch.py` itself is pure (no watchdog import), the integration lives in `src/jarvis/index_cli.py`'s `_cmd_watch`

**Optional — `semantic` extra:**
- `lancedb` 0.34.0 — per-repo vector storage in `src/jarvis/semantic.py` (`SemanticStore`)
- `sentence-transformers` 5.6.1 — embedding model wrapper in `src/jarvis/embeddings.py`; default model: `BAAI/bge-m3`
- `tree-sitter` 0.26.0 — AST-based code chunking in `src/jarvis/chunker.py`
- `tree-sitter-language-pack` 1.13.6 — bundled grammar binaries for 30+ languages

**Dev:**
- `pytest` 9.1.1 — test runner

## Configuration

**Environment:**
- No `.env` file pattern; all overrides via `JARVIS_`-prefixed environment variables:
  - `JARVIS_DATA_DIR` — override `~/.jarvis` data directory (`src/jarvis/config.py`)
  - `JARVIS_ZOEKT_BIN` — override `zoekt-webserver` binary path (`src/jarvis/search.py`)
  - `JARVIS_EMBEDDING_MODEL` — override default embedding model name (`src/jarvis/embeddings.py`)
  - `JARVIS_EMBEDDING_BATCH_SIZE` — override embedding batch size (`src/jarvis/embeddings.py`)
  - `JARVIS_EMBEDDING_QUERY_PREFIX` / `JARVIS_EMBEDDING_DOC_PREFIX` — override model instruction prefixes (`src/jarvis/embeddings.py`)
  - `JARVIS_COMPILE` — set to `1` to enable Cython compilation (`setup.py`)
  - `JARVIS_BIN_DIR` — override binary install directory for `setup.sh`
  - `JARVIS_SETUP_SOURCED` — test seam to source `setup.sh` without running main
- `server.json` — MCP registry manifest (name, version, PyPI package reference, stdio transport)

**Build:**
- `pyproject.toml` — `[build-system]` requires `setuptools>=80.9,<90` + `Cython>=3.1,<4`; src-layout with explicit `packages = ["jarvis"]`
- `setup.py` — Cython compile gate; `StripCompiledSources` class removes `.py` sources from wheel when `JARVIS_COMPILE=1`
- `[tool.cibuildwheel]` in `pyproject.toml` — build matrix, skip musllinux/Windows, sets `JARVIS_COMPILE=1`, runs unit tests inside wheel
- `[tool.pytest.ini_options]` in `pyproject.toml` — `testpaths = ["tests"]`, custom `integration` marker

## Platform Requirements

**Development:**
- Python 3.12+ with `uv`
- External binaries on PATH (from `setup.sh`): `scip`, `zoekt-index`/`zoekt-webserver` (zoekt-git-index), plus per-language indexers: `scip-typescript`, `scip-python`, `scip-java` (requires JVM), `scip-swift` (macOS arm64 only, requires Xcode)
- `uv sync --extra semantic --extra watch` for full feature set

**Production:**
- Published to PyPI as `jarvis-mcp` (wheels: cp312–cp314 × linux/macOS)
- No sdist published — platforms outside the wheel matrix fail loudly
- MCP stdio transport — no network server, no Docker
- Installable via `uvx --from jarvis-mcp jarvis-server` or `uv tool install jarvis-mcp`

---

*Stack analysis: 2026-08-21*
