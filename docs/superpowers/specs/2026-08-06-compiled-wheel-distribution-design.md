# Design: Compiled-Wheel Distribution (Unreadable Source on PyPI)

**Date:** 2026-08-06 | **Status:** approved (brainstorm) | **Decision:** Cython-compiled wheels via cibuildwheel

## Problem

jarvis-mcp ships to users as a pure-Python wheel on PyPI (`uvx --from jarvis-mcp jarvis-server`
in `plugin/.mcp.json`). A wheel is a zip of readable `.py` files, so the private dev repo hides
history/issues/plans but not the source itself (documented in CLAUDE.md). Requirement decided in
brainstorm: **distributed source must be unreadable**.

## Goal / Non-Goals

- Goal: future PyPI releases contain native compiled modules, no readable Python source.
- Non-goal: protecting already-published wheels (≤ 0.5.1 remain on PyPI forever).
- Non-goal: military-grade anti-RE. Compiled `.so` still yields to disassembly and still embeds
  strings/docstrings. The bar is "cannot read logic with a text editor / decompiler".
- Non-goal: leaving PyPI (Nuitka-binary route rejected: breaks semantic extra — torch
  unbundleable — MCP registry reference, and every distribution surface, for similar protection).

## Design

### Build pipeline

- Build backend: `uv_build` → setuptools + Cython 3. Compilation is **conditional** on the env
  flag `JARVIS_COMPILE=1`: unset for dev (`uv sync`, editable install, pytest run pure
  Python as today); set inside CI release builds only.
- `cibuildwheel` builds the matrix: **cp312, cp313, cp314 × {linux x86_64, linux aarch64,
  macOS arm64, macOS x86_64}** on `ubuntu-latest`, `ubuntu-24.04-arm`, `macos-latest`,
  `macos-13`. musllinux and Windows skipped — setup.sh does not support them.
- **No sdist built or uploaded.** Unsupported platforms fail loudly; no readable fallback.

### Wheel contents

- All modules under `src/jarvis/` compiled to `.so`; source `.py` stripped from the wheel.
- Allowlist stays plain Python: `scip_pb2.py` (machine-generated from the public SCIP proto,
  nothing proprietary) and `__init__.py` (trivial).
- `jarvis-mcp` dist-name → `jarvis` module mapping preserved in setuptools config.
- Console scripts `jarvis` / `jarvis-server` unchanged.
- Docstrings must survive compilation: FastMCP derives MCP tool descriptions from `server.py`
  docstrings (Cython preserves docstrings by default; guarded in smoke test below).

### publish-pypi.yml changes

- `uv build` step → build-matrix job (cibuildwheel) + single publish job downloading all wheel
  artifacts.
- cibuildwheel test phase runs the **full unit pytest suite (`-m "not integration"`) against the
  installed compiled wheel** on every platform/version — the empirical gate for Cython semantic
  fidelity (frozen dataclasses, `str | None`, etc.).
- Existing guards adapt, none dropped:
  - Wheel-content guard **inverts**: assert `.so` modules present AND no `.py` beyond
    `{__init__.py, scip_pb2.py}`. Catches a silent fall-back-to-pure-Python build.
  - Tag ↔ pyproject version guard: unchanged.
  - MCP registry `mcp-name` README marker: unchanged.
  - Clean-venv MCP handshake smoke: runs against compiled wheel; extend to assert the 9 tools
    also carry non-empty descriptions (docstring-survival check).
- Publish: `uv publish --trusted-publishing always` over collected wheels only.

### Unchanged surfaces

setup.sh, jarvis-index repo, `plugin/.mcp.json` (`uvx --from jarvis-mcp>=…`), `server.json` /
MCP registry entry, `scripts/check_versions.py`, jarvis-release skill, `semantic` extra (torch /
sentence-transformers stay ordinary PyPI deps installed by uv — never bundled or compiled here).

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Cython 3 semantic divergence | Full unit suite runs against compiled wheel per platform in CI |
| uvx picks CPython without a matching wheel | cp312–cp314 built from day one; `requires-python >=3.12` |
| Silent pure-Python wheel published | Inverted content guard fails the publish |
| Tool descriptions lost (docstrings) | Smoke test asserts non-empty descriptions |
| Worse user tracebacks from compiled frames | Accepted; note in docs |
| Build matrix slowness/flakiness | 12 wheels, native runners, no cross-compilation |

## Acceptance Criteria

- `pip download jarvis-mcp` (new version) yields wheels with no readable jarvis logic (`.so`
  only, allowlist aside); no sdist exists on PyPI.
- `uvx --from jarvis-mcp jarvis-server` completes MCP handshake with 9 described tools on
  macOS arm64 and linux x86_64, Python 3.12–3.14.
- `uv sync && uv run pytest` locally is unaffected (pure Python dev flow).
- publish-pypi.yml green end-to-end on a real release.

## Rollout

1. Implement + merge; cut next release (e.g. 0.6.0) via jarvis-release skill.
2. Verify clean-machine install (`uvx` spawn, plugin flow) post-publish.
3. CHANGELOG entry documents the packaging change and platform matrix.

## Open Questions

- Include musllinux (Alpine) wheels later if users ask? (Out of scope now.)
- Yank old readable versions ≤ 0.5.1? Cosmetic — files stay downloadable; default: leave them.
