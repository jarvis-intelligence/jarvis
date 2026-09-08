# Phase 4: Swift Failure Signatures - Pattern Map

**Mapped:** 2026-08-23
**Files analyzed:** 2 (both modifications to existing files)
**Analogs found:** 2 / 2

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `src/jarvis/index_cli.py` (`_SEARCH_ONLY_SIGNATURES` append) | config (constant tuple) | request-response (substring match on failure carrier) | `src/jarvis/index_cli.py:98-123` (existing Kotlin/AGP entries in same tuple) | exact (same list, same file, append-only) |
| `tests/test_index_cli.py` (3 new test functions) | test | request-response (mocked `_run` raises `IndexingError`) | `tests/test_index_cli.py:1914-1958` (`test_signature_fallback_stamps_signature_origin_and_matched_reason`) | exact (same test shape, Swift adds one extra monkeypatch) |

## Pattern Assignments

### `src/jarvis/index_cli.py` — `_SEARCH_ONLY_SIGNATURES` append (config, substring match)

**Analog:** `src/jarvis/index_cli.py:98-123` (the existing 3 Kotlin/AGP entries in the same constant)

**Comment block pattern** (lines 89-98 — the docstring above the tuple):
```python
# Indexer failures that are known to be unfixable from here, and so degrade to
# a search-only publish instead of a hard failure. Each entry is
# (required substrings, human reason) — EVERY substring must be present, which
# is what keeps a generic AbstractMethodError from some unrelated library out.
#
# Deliberately narrow. This is not "fall back on any failure": a transient
# Gradle break or a missing binary must still fail loudly rather than be
# laundered into an apparent success.
```
Planner: append a drift-warning comment after the existing AGP entries and before the closing `)`, e.g.:
```python
    # --- scip-swift 0.3.0 (captured 2026-08-23) -------------------------------
    # Re-capture on version bump: wordings are binary-embedded and may drift.
    # Unmatched failures fail hard by design (SC3 safety direction).
```

**Entry shape pattern** (lines 104-123 — each tuple entry):
```python
    (
        ("AbstractMethodError", "org.jetbrains.kotlin.fir"),
        "scip-kotlinc is compiled against one exact Kotlin version and this repo uses another "
        "(the compiler-plugin API is internal and unstable)",
    ),
    (
        ("NoSuchMethodError", "org.jetbrains.kotlin.fir"),
        "scip-kotlinc is compiled against one exact Kotlin version and this repo uses another "
        "(the compiler-plugin API is internal and unstable)",
    ),
    (
        ("No SCIP shards found",),
        "the build produced no SCIP shards — for Android/AGP this is expected, because "
        "scip-java's Gradle plugin keys off standard source sets that AGP replaces with "
        "variants (upstream scip-java#177)",
    ),
```

Key conventions:
- Two-token guard preferred when available (Kotlin entries use 2 tokens); single token acceptable when first token is already unique (AGP entry uses 1).
- Reason text is a **cause** description, never a remedy (D-11 locked decision).
- Each entry is a `(tuple[str, ...], str)` inside the outer tuple.

**Matcher (unchanged)** (lines 128-132):
```python
def _search_only_reason(output: str) -> str | None:
    for required, reason in _SEARCH_ONLY_SIGNATURES:
        if all(token in output for token in required):
            return reason
    return None
```
Planner: do NOT modify the matcher or the signature-consult branch (lines 1064-1084). Only append to the constant.

---

### `tests/test_index_cli.py` — pinning tests (test, mocked request-response)

**Analog:** `tests/test_index_cli.py:1914-1958` (`test_signature_fallback_stamps_signature_origin_and_matched_reason`)

**Import pattern** (lines 1-25 — module-level):
```python
import shutil
import subprocess
from pathlib import Path
import pytest
from jarvis import config
from jarvis.index_cli import PARTIAL_STATUS, UnsupportedLanguageError, detect_language, index_repo
from jarvis.registry import Registry

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_py_repo"
SWIFT_FIXTURE_REPO = Path(__file__).parent / "fixtures" / "mini_swift_repo"
```
New tests use `SWIFT_FIXTURE_REPO` (already defined at line 23), NOT `FIXTURE_REPO`.

**Test helper pattern** (lines 38-44):
```python
def _fake_completed_process(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
```

**Test function shape — Kotlin pinning test** (lines 1914-1958, the exact analog to mirror):
```python
def test_signature_fallback_stamps_signature_origin_and_matched_reason(
    tmp_path: Path, monkeypatch
):
    from jarvis.index_cli import (
        SEARCH_ONLY_STATUS,
        _SEARCH_ONLY_SIGNATURES,
        IndexingError,
        index_repo,
    )
    from jarvis.registry import ORIGIN_SIGNATURE

    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    data_root = tmp_path / "data"

    carrier = (
        "e: java.lang.AbstractMethodError: "
        "org.jetbrains.kotlin.fir.analysis.checkers.expression.FirSafeCallChecker.check()"
    )
    kotlin_reason = next(
        reason for tokens, reason in _SEARCH_ONLY_SIGNATURES
        if tokens == ("AbstractMethodError", "org.jetbrains.kotlin.fir")
    )

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError(carrier)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=data_root)

    registry = Registry(data_root / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
        assert entry.status_origin == ORIGIN_SIGNATURE
        assert entry.status_reason == kotlin_reason  # verbatim, not a paraphrase
    finally:
        registry.close()
```

**Swift-specific difference — extra monkeypatch** (lines 1342-1343, from `test_language_override_to_swift_still_gets_xcodebuild`):
```python
    monkeypatch.setattr(cli, "check_scip_version", lambda: None)
    # Hermetic on machines without scip-swift on PATH: the phase-02 runtime
    # floor is the first statement of the swift branch and would raise
    # "scip-swift not found on PATH" before the indexer command is built.
    monkeypatch.setattr(cli, "check_scip_swift_version", lambda: None)
```
CRITICAL: Every new Swift test MUST include `monkeypatch.setattr("jarvis.index_cli.check_scip_swift_version", lambda: None)` — the swift branch probes the binary at pre-pipeline and CI unit legs have no scip-swift. The Kotlin analog test only needs `check_scip_version` (no swift twin).

**Negative test pattern — generic wrapper never matches** (no existing analog, but uses the same `_search_only_reason` import):
```python
from jarvis.index_cli import _search_only_reason

def test_swift_generic_build_failure_wrapper_never_matches():
    assert _search_only_reason(
        "Error: 'swift build' failed with exit code 1:\nerror: manifest parse error"
    ) is None
    assert _search_only_reason(
        "Error: 'xcodebuild' failed with exit code 65:\n** BUILD FAILED **"
    ) is None
```
This is a pure-function test — no fixtures, no monkeypatch, no tmp_path needed.

**Alternative test helper** — `_mock_failing_indexer` (lines 3475-3491) already encapsulates the mock pattern but does NOT include `check_scip_swift_version`. Swift tests must either add the extra monkeypatch after calling `_mock_failing_indexer`, or inline the mock (as the Kotlin pinning test does). Recommend inlining to keep the provenance comment adjacent to the carrier.

## Shared Patterns

### Signature-matched failure path (production)
**Source:** `src/jarvis/index_cli.py:1064-1084`
**Apply to:** No new code needed — this is the existing path the new signatures flow through.
```python
            except IndexingError as exc:
                if language == "java" and _bash_shim_failure(str(exc)):
                    raise IndexingError(f"{_BASH_SHIM_REMEDY}\n\n{exc}") from exc
                reason = _search_only_reason(str(exc))
                if reason is None:
                    raise
                print(
                    f"note: {slug} cannot be SCIP-indexed — {reason}. "
                    "Falling back to search-only; this is remembered, so reindex/watch "
                    "will not repeat the build.",
                    file=sys.stderr,
                )
                semantic_ok, tracked = _publish_search_only(repo_path, slug, root, semantic_include)
                registry.upsert(slug, str(repo_path), language, sha, SEARCH_ONLY_STATUS,
                                scheme_override=scheme, semantic_include=semantic_include,
                                language_override=language_override, search_only=True,
                                status_origin=ORIGIN_SIGNATURE, status_reason=reason)
```

### Registry assertions (test)
**Source:** `tests/test_index_cli.py:1949-1957`
**Apply to:** Both new Swift pinning tests.
```python
        entry = registry.get(slug)
        assert entry is not None
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
        assert entry.status_origin == ORIGIN_SIGNATURE
        assert entry.status_reason == swift_reason  # verbatim, not a paraphrase
```

## No Analog Found

No files without analogs — both modifications are to existing files with exact same-role analogs already present.

## Metadata

**Analog search scope:** `src/jarvis/index_cli.py`, `tests/test_index_cli.py`
**Files scanned:** 2 (target files, fully read at relevant ranges)
**Pattern extraction date:** 2026-08-23
