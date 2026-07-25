---
phase: 1
title: Scaffold and Vendor Core
status: completed
effort: ~2h
priority: P1
dependencies: []
---

# Phase 1: Scaffold and Vendor Core

`$POLARIS_CI` = `~/Projects/epost-workspace/polaris-ai-plaform/polaris-code-intelligence`

## Overview

Create the repo skeleton, vendor the 4 core modules from polaris-ci unchanged, port the synthetic-SCIP test fixtures, and prove the decoder + index reader work via ported tests (TDD: fixtures + tests land before vendored modules are wired).

## Requirements

- Functional: `uv run pytest` green on decoder + index-reader tests against a synthetic index.
- Non-functional: Python >=3.12 (uv-managed; system python is 3.9), deps limited to `mcp[cli]`, `protobuf`, `zstandard`, `httpx`; dev deps `pytest`.

## Architecture

Package `src/codeintel/` (snake_case modules per Python convention). Vendored files keep source names so future diffs against polaris-ci stay trivial. No `__init__` re-exports beyond package marker.

## Related Code Files

- Create: `pyproject.toml` (uv, requires-python >=3.12, hatchling or uv_build backend)
- Create: `src/codeintel/__init__.py`
- Create: `src/codeintel/scip_pb2.py` ← copy `$POLARIS_CI/src/polaris_code_intelligence/scip_pb2.py` (111 LOC, unchanged)
- Create: `src/codeintel/scip_decoder.py` ← copy `$POLARIS_CI/.../service/scip_decoder.py` (279 LOC; fix import `polaris_code_intelligence.scip_pb2` → `codeintel.scip_pb2`)
- Create: `src/codeintel/index_reader.py` ← copy `$POLARIS_CI/.../service/index_reader.py` (160 LOC; stdlib sqlite3 + OrderedDict LRU + threading — unchanged logic)
- Create: `src/codeintel/models.py` ← copy the model classes `query_service.py` imports from `$POLARIS_CI/.../models.py` (only: Location, Position, DocumentSymbolEntry, CallHierarchyEntry, Freshness + whatever else query_service references — trim the FastAPI/registration models)
- Create: `tests/fixtures/scip_encoder.py`, `tests/fixtures/synthetic_index.py` ← port from `$POLARIS_CI/tests/fixtures/` (strip polaris imports)
- Create: `tests/test_scip_decoder.py`, `tests/test_index_reader.py`
- Create: `README.md` (one-screen: what, install, index, register)

## Implementation Steps (TDD order)

1. `uv init --package`-style scaffold; write `pyproject.toml`; `uv sync`.
2. Port `tests/fixtures/scip_encoder.py` + `synthetic_index.py`; adjust imports; commit fixture-only.
3. Write `tests/test_scip_decoder.py` (decode occurrence blobs from synthetic chunks — assert symbols/ranges roundtrip) and `tests/test_index_reader.py` (open synthetic index.db via cache; LRU eviction at max size; concurrent access smoke). **Run: red** (modules missing).
4. Vendor `scip_pb2.py`, `scip_decoder.py`, `index_reader.py`, trimmed `models.py`; fix imports only.
5. **Run: green.** No source-logic edits allowed to make tests pass — if a test fails, the port (imports/fixtures) is wrong, not the vendored logic.

## Success Criteria

- [ ] `uv run pytest` green (decoder + reader suites)
- [ ] `git diff --no-index` of each vendored file vs source shows ONLY import-path changes
- [ ] `uv run python -c "import codeintel.scip_decoder"` works on py3.12+

## Risk Assessment

- Fixture coupling to polaris models → port fixtures first (step 2) to surface coupling early; inline minimal stand-ins rather than dragging extra modules.
- protobuf version drift vs generated `scip_pb2.py` → pin `protobuf` to the major version in `$POLARIS_CI/pyproject.toml`.
