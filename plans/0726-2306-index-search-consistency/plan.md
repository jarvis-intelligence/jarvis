# Index/Search Consistency and Honest Capability Reporting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate four paths where jarvis returns syntactically valid but semantically false answers — invisible SCIP ranges (which is what actually breaks Swift navigation), zoekt shards named by directory instead of slug, `forget` leaving shards searchable, and `typeHierarchy` reporting empty instead of unavailable.

**Architecture:** All four are the same defect class: the pipeline trusts external binaries to produce semantically complete output and has no gate for "succeeded but empty," and the query layer cannot distinguish "no results" from "cannot answer." The fix regenerates the vendored SCIP protobuf so ranges are readable, adds a version floor for the `scip` converter, makes the zoekt shard set track the registry lifecycle, and teaches the query layer to report unavailability explicitly.

**Tech Stack:** Python 3.12+, `protobuf` (regenerated gencode), `zstandard`, direct `sqlite3`, `httpx`, pytest. External: `scip` CLI (`expt-convert`), `zoekt-index`/`zoekt-webserver`.

## Global Constraints

- **`scip` must be >= v0.9.0.** v0.7.0's Go bindings have no `occurrence_range.go` and cannot read the `typed_range` oneof, so every occurrence range is silently dropped and `chunks`/`mentions` come out empty. Verified empirically: same `.scip` file gives `chunks=0, mentions=0` under v0.7.0 and `chunks=1, mentions=14` under v0.9.0.
- **Regenerated gencode requires `protobuf>=7.35.1`.** Local `protoc` is `libprotoc 35.1`; gencode from it emits `ValidateProtobufRuntimeVersion(..., 7, 35, 1, ...)`. The current `pyproject.toml` floor of `>=7.35.0` becomes too low and must be raised.
- **`scip_pb2.py` stays vendored generated code — never hand-edited.** Regenerate it; do not patch it. `scip_decoder.py` remains the *only* module importing `scip_pb2`/`zstandard`.
- **Read `typed_range` first, fall back to deprecated `range`.** This mirrors upstream Go's `Occurrence.SourceRange()` exactly, and scip.proto states `typed_range` takes precedence when both are set. Never read only one.
- **Result types stay frozen dataclasses** (`@dataclass(frozen=True)`), converted at the MCP boundary via `dataclasses.asdict()`. No Pydantic.
- **Direct `sqlite3`, always parameterized.** No ORM.
- **`server.py` keeps catching all exceptions per-tool** and returning `{"error": ...}` so the stdio server survives query bugs.
- **Never mutate a published `index-<sha>.db`.** Queries open it `mode=ro&immutable=1`.
- **Modern type hints:** `str | None`, `list[T]`, `dict[K, V]`.

## File Structure

| File | Responsibility / change |
|---|---|
| `src/jarvis/scip_pb2.py` (regenerate) | Vendored gencode, moved from scip.proto v0.7.0 → v0.9.0 so `typed_range` exists |
| `src/jarvis/scip_decoder.py` (modify) | Read `typed_range` with deprecated-`range` fallback (no change to `ScipOccurrence`'s shape) |
| `src/jarvis/index_cli.py` (modify) | `scip` version floor; `-meta` slug naming for zoekt; shard teardown on `forget`; post-convert ingestion gate |
| `src/jarvis/query.py` (modify) | Capability probes (`occurrence_data_present`, `relationship_data_present`); `type_hierarchy` reports unavailability |
| `src/jarvis/server.py` (modify) | Surface `typeHierarchy` unavailability as an explicit payload |
| `pyproject.toml` (modify) | `protobuf` floor → `>=7.35.1` |
| `tests/test_scip_decoder.py` (modify) | typed_range decode + precedence + fallback |
| `tests/test_index_cli.py` (modify) | version floor, shard naming, forget teardown, ingestion gate |
| `tests/test_query.py` (modify) | capability probes |
| `tests/test_server_tools.py` (modify) | typeHierarchy unavailable payload |
| `docs/codebase-summary.md`, `docs/project-roadmap.md`, `README.md` (modify) | Correct the Swift diagnosis and document the scip floor |

Tasks 1–2 are the Swift fix and must land in order. Tasks 3–7 are independent of each other.

---

### Task 1: Regenerate `scip_pb2.py` from scip.proto v0.9.0

**Files:**
- Regenerate: `src/jarvis/scip_pb2.py`
- Modify: `pyproject.toml`
- Test: `tests/test_scip_decoder.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `scip_pb2.Occurrence` gains the `typed_range` oneof with `single_line_range` (field 8) and `multi_line_range` (field 9), plus `scip_pb2.SingleLineRange` / `scip_pb2.MultiLineRange` message types. `scip_pb2.Occurrence.range` (field 1) remains, now deprecated upstream. Task 2 depends on these names.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scip_decoder.py`:

```python
def test_scip_pb2_exposes_typed_range_oneof():
    """scip.proto gained a typed_range oneof; the old v0.7.0 gencode lacks it.

    Without these fields the decoder cannot see ranges produced by any modern
    indexer (scip-swift writes single_line_range and never the deprecated
    repeated-int32 range), so every occurrence looks position-less.
    """
    from jarvis import scip_pb2

    field_names = {f.name for f in scip_pb2.Occurrence.DESCRIPTOR.fields}
    assert "single_line_range" in field_names
    assert "multi_line_range" in field_names
    # The deprecated field must remain readable for older indexes.
    assert "range" in field_names

    oneof_names = {o.name for o in scip_pb2.Occurrence.DESCRIPTOR.oneofs}
    assert "typed_range" in oneof_names


def test_single_line_range_message_shape():
    from jarvis import scip_pb2

    r = scip_pb2.SingleLineRange()
    r.line = 4
    r.start_character = 2
    r.end_character = 9
    assert (r.line, r.start_character, r.end_character) == (4, 2, 9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scip_decoder.py -k "typed_range or single_line_range_message" -v`
Expected: FAIL — `assert 'single_line_range' in field_names` fails, and `scip_pb2.SingleLineRange` raises `AttributeError`, because the vendored gencode is from v0.7.0.

- [ ] **Step 3: Regenerate the module**

```bash
cd /tmp && rm -rf scipgen && mkdir scipgen && cd scipgen
curl -fsSL -o scip.proto "https://raw.githubusercontent.com/scip-code/scip/v0.9.0/scip.proto"
protoc --version   # must report libprotoc 35.x
protoc --python_out=. scip.proto
head -3 scip_pb2.py
cp scip_pb2.py /Users/ddphuong/Projects/jarvis/src/jarvis/scip_pb2.py
```

Then restore the vendoring header. Open `src/jarvis/scip_pb2.py` and insert this immediately after the first `# -*- coding: utf-8 -*-` line, replacing any header the previous version had:

```python
# =============================================================================
# VENDORED — DO NOT HAND-EDIT.
#
# Generated from `scip.proto` at scip-code/scip tag v0.9.0 — the same version
# setup.sh pins for the `scip expt-convert` converter.
#
# v0.9.0 (not v0.7.0) is required: scip.proto introduced the `typed_range`
# oneof (`single_line_range` = 8, `multi_line_range` = 9) and deprecated the
# repeated-int32 `range` field. Modern indexers write ONLY the typed variant —
# scip-swift, for instance — so gencode without `typed_range` reads every
# occurrence as position-less, which silently empties chunks/mentions and
# breaks all per-file navigation.
#
# Source: https://raw.githubusercontent.com/scip-code/scip/v0.9.0/scip.proto
# protoc:  libprotoc 35.1
#
# To regenerate:
#   curl -fsSL -o scip.proto \
#     https://raw.githubusercontent.com/scip-code/scip/v0.9.0/scip.proto
#   protoc --python_out=. scip.proto
# =============================================================================
```

- [ ] **Step 4: Raise the protobuf floor**

In `pyproject.toml`, replace the `protobuf` dependency line and its comment:

```toml
    # Runtime must stay >= the gencode version vendored in scip_pb2.py
    # (generated against libprotoc 35.1 — see that file's header). Gencode
    # calls ValidateProtobufRuntimeVersion and refuses an older runtime.
    "protobuf>=7.35.1,<8.0.0",
```

Then: `uv sync --extra watch`

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_scip_decoder.py -v`
Expected: PASS — the two new tests plus every pre-existing decoder test. If a pre-existing test fails, the regeneration changed something beyond the range fields; stop and inspect rather than editing the generated file.

- [ ] **Step 6: Confirm no wider regression**

Run: `uv run pytest -m "not integration"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/jarvis/scip_pb2.py pyproject.toml uv.lock tests/test_scip_decoder.py
git commit -m "feat(scip): regenerate vendored gencode from scip.proto v0.9.0

v0.7.0 gencode has no typed_range oneof, so occurrences written by modern
indexers (scip-swift writes only single_line_range) decode as position-less."
```

---

### Task 2: Read `typed_range` in the decoder — the actual Swift navigation fix

**Files:**
- Modify: `src/jarvis/scip_decoder.py`
- Test: `tests/test_scip_decoder.py`

**Interfaces:**
- Consumes: `scip_pb2.Occurrence.single_line_range` / `.multi_line_range` / `.range` from Task 1
- Produces:
  - `ScipOccurrence.range` keeps its existing type `tuple[int, ...]` and semantics (SCIP wire order), so `scip_range_to_positions()`, `query.py`, and all callers are unchanged.
  - New module function `occurrence_range(occ) -> tuple[int, ...]`, returning `typed_range` converted to SCIP wire order when set, else the deprecated `range`, else `()`.

Wire order reminder: SCIP packs a 3-element range as `[start_line, start_char, end_char]` (single-line) and a 4-element one as `[start_line, start_char, end_line, end_char]`. `scip_range_to_positions()` already handles both, so converting `SingleLineRange` into the 3-element form keeps every downstream consumer working untouched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scip_decoder.py`:

```python
def test_decode_occurrences_reads_single_line_typed_range():
    """The case that broke Swift: only single_line_range is set."""
    from jarvis import scip_pb2
    from jarvis.scip_decoder import decode_occurrences
    from tests.fixtures.scip_encoder import encode_occurrences

    occ = scip_pb2.Occurrence()
    occ.symbol = "swift . `s:5MyLib7GreeterV`."
    occ.symbol_roles = scip_pb2.Definition
    occ.single_line_range.line = 7
    occ.single_line_range.start_character = 4
    occ.single_line_range.end_character = 11

    decoded = decode_occurrences(encode_occurrences([occ]))
    assert len(decoded) == 1
    # SCIP single-line wire order: [start_line, start_char, end_char]
    assert decoded[0].range == (7, 4, 11)
    assert decoded[0].is_definition()


def test_decode_occurrences_reads_multi_line_typed_range():
    from jarvis import scip_pb2
    from jarvis.scip_decoder import decode_occurrences
    from tests.fixtures.scip_encoder import encode_occurrences

    occ = scip_pb2.Occurrence()
    occ.symbol = "sym"
    occ.multi_line_range.start_line = 3
    occ.multi_line_range.start_character = 2
    occ.multi_line_range.end_line = 5
    occ.multi_line_range.end_character = 8

    decoded = decode_occurrences(encode_occurrences([occ]))
    # SCIP multi-line wire order: [start_line, start_char, end_line, end_char]
    assert decoded[0].range == (3, 2, 5, 8)


def test_decode_occurrences_still_reads_deprecated_range():
    """Older indexes set only the deprecated repeated-int32 field."""
    from jarvis import scip_pb2
    from jarvis.scip_decoder import decode_occurrences
    from tests.fixtures.scip_encoder import encode_occurrences

    occ = scip_pb2.Occurrence()
    occ.symbol = "sym"
    occ.range.extend([2, 1, 9])

    decoded = decode_occurrences(encode_occurrences([occ]))
    assert decoded[0].range == (2, 1, 9)


def test_typed_range_takes_precedence_over_deprecated_range():
    """scip.proto: "When both are present, typed_range takes precedence"."""
    from jarvis import scip_pb2
    from jarvis.scip_decoder import decode_occurrences
    from tests.fixtures.scip_encoder import encode_occurrences

    occ = scip_pb2.Occurrence()
    occ.symbol = "sym"
    occ.range.extend([99, 99, 99])
    occ.single_line_range.line = 1
    occ.single_line_range.start_character = 2
    occ.single_line_range.end_character = 3

    decoded = decode_occurrences(encode_occurrences([occ]))
    assert decoded[0].range == (1, 2, 3), "typed_range must win"


def test_decode_occurrences_tolerates_absent_range():
    from jarvis import scip_pb2
    from jarvis.scip_decoder import decode_occurrences
    from tests.fixtures.scip_encoder import encode_occurrences

    occ = scip_pb2.Occurrence()
    occ.symbol = "sym"

    decoded = decode_occurrences(encode_occurrences([occ]))
    assert decoded[0].range == ()


def test_zero_valued_typed_range_is_preserved_not_treated_as_absent():
    """A range at line 0, chars 0-1 is real data, not "missing".

    Guards against resolving the range with a falsy check that discards a
    legitimate all-zero start position.
    """
    from jarvis import scip_pb2
    from jarvis.scip_decoder import decode_occurrences
    from tests.fixtures.scip_encoder import encode_occurrences

    occ = scip_pb2.Occurrence()
    occ.symbol = "sym"
    occ.single_line_range.line = 0
    occ.single_line_range.start_character = 0
    occ.single_line_range.end_character = 1

    decoded = decode_occurrences(encode_occurrences([occ]))
    assert decoded[0].range == (0, 0, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scip_decoder.py -k "typed_range or deprecated_range or absent_range or zero_valued" -v`
Expected: FAIL — `decoded[0].range == ()` where `(7, 4, 11)` was expected, because the decoder still reads only the deprecated `occ.range`. The two tests that set only the deprecated field will already pass; that is expected and fine.

- [ ] **Step 3: Write the implementation**

`ScipOccurrence` needs no new fields — `range` keeps its type and meaning, so every existing caller is untouched. Add the range-resolution helpers above `decode_occurrences`:

```python
def _typed_range_to_wire(occ) -> tuple[int, ...]:
    """Convert the `typed_range` oneof into SCIP's packed wire order.

    Returns () when the oneof is unset. SCIP packs single-line ranges as
    [start_line, start_char, end_char] and multi-line as
    [start_line, start_char, end_line, end_char] — the two shapes
    `scip_range_to_positions()` already understands, so every downstream
    caller keeps working unchanged.
    """
    which = occ.WhichOneof("typed_range")
    if which == "single_line_range":
        r = occ.single_line_range
        return (r.line, r.start_character, r.end_character)
    if which == "multi_line_range":
        r = occ.multi_line_range
        return (r.start_line, r.start_character, r.end_line, r.end_character)
    return ()


def occurrence_range(occ) -> tuple[int, ...]:
    """Resolve an occurrence's source range, newest encoding first.

    Mirrors upstream Go's `Occurrence.SourceRange()`: scip.proto specifies
    that `typed_range` takes precedence when both encodings are set, and the
    repeated-int32 `range` field is deprecated. Reading only the deprecated
    field makes modern indexers (scip-swift emits solely
    `single_line_range`) look position-less.
    """
    typed = _typed_range_to_wire(occ)
    if typed:
        return typed
    return tuple(occ.range)


def _enclosing_range(occ) -> tuple[int, ...]:
    """Counterpart of `occurrence_range` for the enclosing-range fields."""
    which = occ.WhichOneof("typed_enclosing_range")
    if which == "single_line_enclosing_range":
        r = occ.single_line_enclosing_range
        return (r.line, r.start_character, r.end_character)
    if which == "multi_line_enclosing_range":
        r = occ.multi_line_enclosing_range
        return (r.start_line, r.start_character, r.end_line, r.end_character)
    return tuple(occ.enclosing_range)
```

Then use them in `decode_occurrences`:

```python
    return [
        ScipOccurrence(
            range=occurrence_range(occ),
            symbol=occ.symbol,
            symbol_roles=occ.symbol_roles,
            enclosing_range=_enclosing_range(occ),
        )
        for occ in document.occurrences
    ]
```

Note: verify the enclosing-range oneof field names against the regenerated module before relying on them —

```bash
uv run python -c "
from jarvis import scip_pb2
o = scip_pb2.Occurrence()
print('oneofs:', [x.name for x in o.DESCRIPTOR.oneofs])
print('fields:', [f.name for f in o.DESCRIPTOR.fields])
"
```

If the enclosing oneof is absent or differently named in v0.9.0, drop `_enclosing_range` and keep `enclosing_range=tuple(occ.enclosing_range)`; `enclosing_range` is not used by any nav tool today, so it must not block this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scip_decoder.py -v`
Expected: PASS.

- [ ] **Step 5: Prove the real Swift fix end-to-end**

This is the acceptance check for the whole Swift problem. It needs `scip >= v0.9.0` and `scip-swift` — get a clean pair first so a stale `~/.local/bin/scip` (v0.7.0) cannot shadow them:

```bash
rm -rf /tmp/swiftproof && mkdir -p /tmp/swiftproof/bin
JARVIS_SETUP_SOURCED=1 JARVIS_BIN_DIR=/tmp/swiftproof/bin FORCE=1 \
  sh -c '. ./setup.sh; install_scip darwin arm64; install_scip_swift darwin arm64'
/tmp/swiftproof/bin/scip --version        # expect v0.9.0
```

Then index the committed Swift fixture and assert real navigation data:

```bash
cp -R tests/fixtures/mini_swift_repo /tmp/swiftproof/repo
cd /tmp/swiftproof/repo && git init -q . && git add -A && git -c commit.gpgsign=false commit -qm init
cd /Users/ddphuong/Projects/jarvis
PATH="/tmp/swiftproof/bin:$PATH" JARVIS_DATA_DIR=/tmp/swiftproof/data \
  uv run jarvis index /tmp/swiftproof/repo --slug swiftproof
sqlite3 "$(ls /tmp/swiftproof/data/scip/_/swiftproof/_/index-*.db)" \
  "SELECT 'chunks='||(SELECT COUNT(*) FROM chunks), 'mentions='||(SELECT COUNT(*) FROM mentions);"
```
Expected: `chunks=` and `mentions=` both **non-zero** (previously both 0).

Then confirm nav actually returns positions:

```bash
JARVIS_DATA_DIR=/tmp/swiftproof/data uv run python -c "
from jarvis import config
from jarvis.query import QueryService
svc = QueryService(config.new_connection_cache())
syms, fresh = svc.get_document_symbols('swiftproof', 'Sources/MiniSwiftRepo/Greeter.swift')
print('documentSymbols:', len(syms))
for s in syms[:5]:
    print('  ', s)
assert syms, 'documentSymbols must no longer be empty on a Swift repo'
"
```
Expected: a non-empty symbol list with real line/character positions. **This is the moment Swift navigation starts working.**

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/scip_decoder.py tests/test_scip_decoder.py
git commit -m "fix(scip): read typed_range so occurrence positions are visible

Mirrors upstream Go SourceRange(): typed_range first, deprecated range as
fallback. This is what makes Swift navigation work -- scip-swift emits only
single_line_range, so the old decoder saw every occurrence as position-less."
```

---

### Task 3: Refuse a `scip` converter older than v0.9.0

**Files:**
- Modify: `src/jarvis/index_cli.py`
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `IndexingError` and `_run` already in `index_cli.py`
- Produces:
  - `MIN_SCIP_VERSION: tuple[int, int, int]` = `(0, 9, 0)`
  - `parse_scip_version(output: str) -> tuple[int, int, int] | None` — parses `scip version v0.9.0`
  - `check_scip_version() -> None` — raises `IndexingError` when the binary is too old

Rationale: v0.7.0 converts without error and produces a schema-valid database with zero `chunks`/`mentions`. That silent degradation cost real debugging time; a version floor turns it into an immediate, explicit failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def test_parse_scip_version_reads_standard_output():
    from jarvis.index_cli import parse_scip_version

    assert parse_scip_version("scip version v0.9.0") == (0, 9, 0)
    assert parse_scip_version("scip version v0.10.2\n") == (0, 10, 2)
    assert parse_scip_version("v1.0.0") == (1, 0, 0)


def test_parse_scip_version_returns_none_on_junk():
    from jarvis.index_cli import parse_scip_version

    assert parse_scip_version("") is None
    assert parse_scip_version("not a version") is None


def test_check_scip_version_rejects_v070(monkeypatch):
    """v0.7.0 converts successfully but silently drops every range."""
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v0.7.0")
    with pytest.raises(cli.IndexingError) as exc:
        cli.check_scip_version()
    message = str(exc.value)
    assert "0.7.0" in message
    assert "0.9.0" in message, "must state the required floor"


def test_check_scip_version_accepts_v090(monkeypatch):
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v0.9.0")
    cli.check_scip_version()  # must not raise


def test_check_scip_version_accepts_newer(monkeypatch):
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "scip version v1.2.3")
    cli.check_scip_version()


def test_check_scip_version_tolerates_unparseable(monkeypatch):
    """An unrecognized format must not block indexing outright."""
    import jarvis.index_cli as cli

    monkeypatch.setattr(cli, "_scip_version_output", lambda: "weird build")
    cli.check_scip_version()  # must not raise
```

Ensure `import pytest` is present at the top of `tests/test_index_cli.py` (it already is).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "scip_version" -v`
Expected: FAIL — `ImportError`/`AttributeError` for `parse_scip_version` and `check_scip_version`.

- [ ] **Step 3: Write the implementation**

In `src/jarvis/index_cli.py`, add near the other module constants (after `_IGNORED_DIRS`):

```python
# `scip expt-convert` below this version cannot read scip.proto's `typed_range`
# oneof (its Go bindings predate occurrence_range.go), so it silently writes a
# schema-valid database with zero chunks and zero mentions -- every navigation
# query then returns empty. Verified: the same .scip file yields chunks=0/
# mentions=0 under v0.7.0 and chunks=1/mentions=14 under v0.9.0.
MIN_SCIP_VERSION = (0, 9, 0)
```

Add the helpers above `index_repo`:

```python
def parse_scip_version(output: str) -> tuple[int, int, int] | None:
    """Parse `scip --version` output, e.g. "scip version v0.9.0".

    Returns None when the format is unrecognized, so an unexpected build
    string degrades to "cannot verify" rather than blocking indexing.
    """
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", output)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _scip_version_output() -> str:
    """Isolated for tests to monkeypatch."""
    try:
        result = subprocess.run(["scip", "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise IndexingError("scip not found on PATH — run setup.sh") from exc
    return f"{result.stdout}\n{result.stderr}"


def check_scip_version() -> None:
    """Raise IndexingError when `scip` is too old to preserve ranges."""
    version = parse_scip_version(_scip_version_output())
    if version is None:
        # Unknown format: warn-by-omission rather than block. A wrong guess
        # here would make indexing impossible against a valid future build.
        return
    if version < MIN_SCIP_VERSION:
        current = ".".join(str(p) for p in version)
        required = ".".join(str(p) for p in MIN_SCIP_VERSION)
        raise IndexingError(
            f"scip v{current} is too old (need >= v{required}): it cannot read scip.proto's "
            "typed_range oneof, so occurrence positions are dropped and navigation returns "
            "empty results. Re-run setup.sh, and remove any older scip earlier on PATH."
        )
```

Add `import re` to the imports if absent, and confirm `subprocess` is already imported (it is — `_run` uses it).

Call it at the top of `index_repo`, immediately after `sha = _git_head(repo_path)`:

```python
    check_scip_version()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "scip_version" -v`
Expected: PASS.

- [ ] **Step 5: Verify against the real stale binary**

Your `~/.local/bin/scip` is v0.7.0, which makes this a live check:

```bash
uv run jarvis index /tmp/swiftproof/repo --slug floor-check 2>&1 | tail -3
```
Expected: fails with the explicit "too old" message rather than silently producing an empty index. Then confirm the good path:
```bash
PATH="/tmp/swiftproof/bin:$PATH" JARVIS_DATA_DIR=/tmp/swiftproof/data \
  uv run jarvis index /tmp/swiftproof/repo --slug floor-check 2>&1 | tail -2
```
Expected: `indexed floor-check`.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "feat(index): require scip >= v0.9.0

Older converters cannot read typed_range and silently emit an index with no
chunks or mentions, so every nav query returns empty. Fail loudly instead."
```

---

### Task 4: Name zoekt shards by slug so `searchCode(repo=...)` matches

**Files:**
- Modify: `src/jarvis/index_cli.py`
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `_run`, `config.data_dir` already in `index_cli.py`
- Produces: `_write_zoekt_meta(scratch: Path, slug: str) -> Path` — writes `{"Name": slug}` and returns the file path. `index_repo` passes it via `zoekt-index -meta`.

Verified empirically this session: `zoekt-index -meta '{"Name":"custom-slug-abc"}' <dir named mydirname>` produces shard `custom-slug-abc_v16.00000.zoekt`, search results carry `Repository = "custom-slug-abc"`, `r:custom-slug-abc` matches (1 hit) and `r:mydirname` does not (0 hits). Without `-meta`, Zoekt names the shard from the directory basename, so `searchCode(repo=<slug>)` silently returns zero hits whenever slug ≠ directory name.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def test_write_zoekt_meta_contains_slug(tmp_path: Path):
    import json as _json

    from jarvis.index_cli import _write_zoekt_meta

    meta_path = _write_zoekt_meta(tmp_path, "my-slug")
    assert meta_path.is_file()
    assert _json.loads(meta_path.read_text())["Name"] == "my-slug"


@pytest.mark.integration
@pytest.mark.skipif(_missing, reason=f"missing required binaries: {_missing}")
def test_zoekt_shard_is_named_by_slug_not_directory(tmp_path: Path):
    """Regression: searchCode(repo=<slug>) silently returned zero hits.

    Zoekt names shards from the directory basename unless -meta says
    otherwise, so a slug differing from the directory produced a shard the
    r: filter could never match.
    """
    repo_dir = tmp_path / "directoryname"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)

    data_root = tmp_path / "data"
    index_repo(repo_dir, slug="totally-different-slug", root=data_root)

    shards = list((data_root / ".zoekt").glob("*.zoekt"))
    names = [s.name for s in shards]
    assert any(n.startswith("totally-different-slug") for n in names), names
    assert not any(n.startswith("directoryname") for n in names), names
```

These names are the module's existing ones, verified: `FIXTURE_REPO` (mini_py_repo), `SWIFT_FIXTURE_REPO`, the module-level `_missing` list, and `_init_git_repo`. The module gates integration tests with `@pytest.mark.skipif(_missing, ...)` — there is no `_require_binaries()` helper, so do not invent one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "zoekt_meta or named_by_slug" -v`
Expected: FAIL — `_write_zoekt_meta` does not exist; the integration test finds a shard named `directoryname_v16.00000.zoekt`.

- [ ] **Step 3: Write the implementation**

In `src/jarvis/index_cli.py`, add above `index_repo`:

```python
def _write_zoekt_meta(scratch: Path, slug: str) -> Path:
    """Write the `.meta` file that names the Zoekt shard after `slug`.

    Without this, zoekt-index derives the repository name from the indexed
    directory's basename. `searchCode(repo=<slug>)` builds a Zoekt `r:<slug>`
    filter, so a slug that differs from the directory name matches nothing
    and the tool returns zero hits with no error -- a silent wrong answer.
    """
    meta_path = scratch / "zoekt.meta.json"
    meta_path.write_text(json.dumps({"Name": slug}), encoding="utf-8")
    return meta_path
```

Replace the `zoekt-index` invocation in `index_repo` (currently `index_cli.py:154`):

```python
            zoekt_dir = config.data_dir(root) / ".zoekt"
            zoekt_dir.mkdir(parents=True, exist_ok=True)
            meta_path = _write_zoekt_meta(Path(scratch), slug)
            _run(
                ["zoekt-index", "-index", str(zoekt_dir), "-meta", str(meta_path), str(repo_path)],
                cwd=repo_path,
                step="zoekt-index",
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "zoekt_meta" -v` then
`PATH="/tmp/swiftproof/bin:$PATH" uv run pytest tests/test_index_cli.py -k "named_by_slug" -v`
Expected: PASS.

- [ ] **Step 5: Prove the end-to-end search path**

```bash
rm -rf /tmp/searchproof && mkdir -p /tmp/searchproof
cp -R tests/fixtures/mini_py_repo /tmp/searchproof/somedirname
cd /tmp/searchproof/somedirname && git init -q . && git add -A && git -c commit.gpgsign=false commit -qm init
cd /Users/ddphuong/Projects/jarvis
PATH="/tmp/swiftproof/bin:$PATH" JARVIS_DATA_DIR=/tmp/searchproof/data \
  uv run jarvis index /tmp/searchproof/somedirname --slug renamed-slug
JARVIS_DATA_DIR=/tmp/searchproof/data uv run python -c "
from jarvis import config
from jarvis.search import ZoektLifecycle, search_zoekt
d = config.data_dir()
lc = ZoektLifecycle(index_dir=d / '.zoekt', data_dir=d)
url = lc.ensure_running()
scoped = search_zoekt(url, 'r:renamed-slug greet')
print('hits with r:renamed-slug =', len(scoped))
assert scoped, 'the slug-scoped filter must match'
"
```
Expected: non-zero hits. Before this task it was 0.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "fix(index): name zoekt shards by slug via -meta

searchCode(repo=<slug>) applies a Zoekt r:<slug> filter, but shards were
named from the directory basename -- so any slug differing from the
directory returned zero hits with no error."
```

---

### Task 5: `forget` must tear down the Zoekt shard

**Files:**
- Modify: `src/jarvis/index_cli.py`
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `config.data_dir`, `_cmd_forget` in `index_cli.py`
- Produces: `_remove_zoekt_shards(slug: str, root: Path | None = None) -> list[Path]` — deletes `<data_dir>/.zoekt/<slug>_v*.zoekt` and returns what it removed.

Confirmed defect: `_cmd_forget` (`index_cli.py:231-249`) deletes the registry row and the SCIP index directory but never the shard. Observed live — the registry is empty while `~/.jarvis/.zoekt/` still holds `mini_py_repo`, `mini_swift_repo`, and `scip-swift` shards, so `searchCode` returns hits for repos jarvis no longer knows about, pointing at paths that may no longer exist.

Depends on Task 4: shard filenames only match the slug once `-meta` is passed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def test_remove_zoekt_shards_deletes_matching_shard(tmp_path: Path, monkeypatch):
    from jarvis.index_cli import _remove_zoekt_shards

    zoekt_dir = tmp_path / ".zoekt"
    zoekt_dir.mkdir(parents=True)
    (zoekt_dir / "myslug_v16.00000.zoekt").write_bytes(b"x")
    (zoekt_dir / "myslug_v16.00001.zoekt").write_bytes(b"x")
    (zoekt_dir / "otherslug_v16.00000.zoekt").write_bytes(b"x")

    removed = _remove_zoekt_shards("myslug", root=tmp_path)

    assert len(removed) == 2
    assert not (zoekt_dir / "myslug_v16.00000.zoekt").exists()
    assert not (zoekt_dir / "myslug_v16.00001.zoekt").exists()
    assert (zoekt_dir / "otherslug_v16.00000.zoekt").exists(), "must not touch other repos"


def test_remove_zoekt_shards_does_not_prefix_match_other_slugs(tmp_path: Path):
    """"api" must not delete "api-gateway"'s shard."""
    from jarvis.index_cli import _remove_zoekt_shards

    zoekt_dir = tmp_path / ".zoekt"
    zoekt_dir.mkdir(parents=True)
    (zoekt_dir / "api_v16.00000.zoekt").write_bytes(b"x")
    (zoekt_dir / "api-gateway_v16.00000.zoekt").write_bytes(b"x")

    removed = _remove_zoekt_shards("api", root=tmp_path)

    assert len(removed) == 1
    assert not (zoekt_dir / "api_v16.00000.zoekt").exists()
    assert (zoekt_dir / "api-gateway_v16.00000.zoekt").exists()


def test_remove_zoekt_shards_is_safe_when_absent(tmp_path: Path):
    from jarvis.index_cli import _remove_zoekt_shards

    assert _remove_zoekt_shards("nothing-here", root=tmp_path) == []


def test_forget_removes_the_zoekt_shard(tmp_path: Path, monkeypatch, capsys):
    """Regression: forgotten repos stayed searchable."""
    import argparse

    from jarvis import config
    from jarvis.index_cli import _cmd_forget
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))

    registry = Registry(config.data_dir() / "registry.db")
    registry.upsert("goneslug", str(tmp_path / "repo"), "python", "abc123", "indexed")
    registry.close()

    index_dir = config.index_dir("goneslug")
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index-abc123.db").write_bytes(b"x")

    zoekt_dir = config.data_dir() / ".zoekt"
    zoekt_dir.mkdir(parents=True, exist_ok=True)
    shard = zoekt_dir / "goneslug_v16.00000.zoekt"
    shard.write_bytes(b"x")

    rc = _cmd_forget(argparse.Namespace(slug="goneslug"))

    assert rc == 0
    assert not shard.exists(), "a forgotten repo must not stay searchable"
    assert not index_dir.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "zoekt_shards or forget_removes" -v`
Expected: FAIL — `_remove_zoekt_shards` missing; `test_forget_removes_the_zoekt_shard` fails on `assert not shard.exists()`.

- [ ] **Step 3: Write the implementation**

In `src/jarvis/index_cli.py`, add above `_cmd_forget`:

```python
def _remove_zoekt_shards(slug: str, root: Path | None = None) -> list[Path]:
    """Delete the Zoekt shards belonging to `slug`.

    Zoekt names each shard `<repo-name>_v<N>.<NNNNN>.zoekt`, and Task 4 makes
    `<repo-name>` the slug. The `_v` in the glob is deliberate: a bare
    `slug*` would let "api" also match "api-gateway"'s shard.

    Without this, `forget` left the shard in place and `searchCode` kept
    returning hits for a repo jarvis no longer knows about.
    """
    zoekt_dir = config.data_dir(root) / ".zoekt"
    if not zoekt_dir.is_dir():
        return []
    removed: list[Path] = []
    for shard in sorted(zoekt_dir.glob(f"{slug}_v*.zoekt")):
        shard.unlink(missing_ok=True)
        removed.append(shard)
    return removed
```

In `_cmd_forget`, add the teardown after the index directory is removed:

```python
    index_dir = config.index_dir(slug)
    if index_dir.exists():
        shutil.rmtree(index_dir)
    _remove_zoekt_shards(slug)
    print(f"forgot {slug}")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "zoekt_shards or forget" -v`
Expected: PASS — including the pre-existing `test_forget_rejects_dotdot_slug`, which must keep passing (the `..` guard runs before any deletion).

- [ ] **Step 5: Clear the orphaned shards already on disk**

Pre-existing shards were named by directory and predate Task 4, so `forget` cannot match them. The registry is currently empty, making a clean slate safe:

```bash
uv run jarvis list          # expect no output (empty registry)
ls ~/.jarvis/.zoekt/        # expect the 3 orphans
rm -f ~/.jarvis/.zoekt/*.zoekt
ls ~/.jarvis/.zoekt/        # expect empty
```
If `jarvis list` is NOT empty, do not delete blindly — reindex those repos after Task 4 instead, so their shards are recreated under the slug name.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "fix(index): remove the zoekt shard on forget

forget dropped the registry row and SCIP index but left the shard, so
searchCode kept returning hits for repos jarvis no longer knew about."
```

---

### Task 6: Ingestion gate — never report a position-less index as plain `indexed`

**Files:**
- Modify: `src/jarvis/index_cli.py`
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `sqlite3`, `Registry` already in `index_cli.py`
- Produces:
  - `PARTIAL_STATUS = "partial"`
  - `index_has_navigation_data(conn: sqlite3.Connection) -> bool` — True when `chunks` and `mentions` are both non-empty
  - `index_repo` records `PARTIAL_STATUS` instead of `"indexed"` when symbols exist but navigation data does not

This is the structural guard: it is exactly the fingerprint that cost real debugging time this session (`documents=1, global_symbols=7, chunks=0, mentions=0`). Publishing still happens — the symbol table is genuinely useful — but the status stops claiming full success, so `jarvis list`/`status` shows it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_cli.py`:

```python
def _make_index_db(path: Path, *, chunks: int, mentions: int, symbols: int = 3) -> None:
    """Minimal stand-in for the expt-convert schema, only the counted tables."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE documents (id INTEGER PRIMARY KEY, relative_path TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INTEGER);
        CREATE TABLE global_symbols (id INTEGER PRIMARY KEY, symbol TEXT);
        CREATE TABLE mentions (chunk_id INTEGER, symbol_id INTEGER, role INTEGER);
        """
    )
    conn.execute("INSERT INTO documents (relative_path) VALUES ('a.swift')")
    for i in range(symbols):
        conn.execute("INSERT INTO global_symbols (symbol) VALUES (?)", (f"sym{i}",))
    for i in range(chunks):
        conn.execute("INSERT INTO chunks (document_id) VALUES (1)")
    for i in range(mentions):
        conn.execute("INSERT INTO mentions (chunk_id, symbol_id, role) VALUES (1, 1, 1)")
    conn.commit()
    conn.close()


def test_index_has_navigation_data_false_when_chunks_and_mentions_empty(tmp_path: Path):
    """The exact fingerprint of a converter that dropped every range."""
    from jarvis.index_cli import index_has_navigation_data

    db = tmp_path / "i.db"
    _make_index_db(db, chunks=0, mentions=0)
    conn = sqlite3.connect(db)
    try:
        assert index_has_navigation_data(conn) is False
    finally:
        conn.close()


def test_index_has_navigation_data_true_when_populated(tmp_path: Path):
    from jarvis.index_cli import index_has_navigation_data

    db = tmp_path / "i.db"
    _make_index_db(db, chunks=1, mentions=14)
    conn = sqlite3.connect(db)
    try:
        assert index_has_navigation_data(conn) is True
    finally:
        conn.close()


def test_index_has_navigation_data_false_when_only_chunks(tmp_path: Path):
    from jarvis.index_cli import index_has_navigation_data

    db = tmp_path / "i.db"
    _make_index_db(db, chunks=2, mentions=0)
    conn = sqlite3.connect(db)
    try:
        assert index_has_navigation_data(conn) is False
    finally:
        conn.close()
```

Confirm `sqlite3` and `Path` are imported in the test module (they are).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_cli.py -k "has_navigation_data" -v`
Expected: FAIL — `cannot import name 'index_has_navigation_data'`.

- [ ] **Step 3: Write the implementation**

In `src/jarvis/index_cli.py`, add the constant beside `MIN_SCIP_VERSION`:

```python
# Registry status for an index that published real symbols but no navigable
# positions -- the fingerprint of a converter or indexer that dropped every
# occurrence range. Publishing still proceeds (the symbol table is useful),
# but the status must not claim unqualified success.
PARTIAL_STATUS = "partial"
```

Add the probe above `index_repo`:

```python
def index_has_navigation_data(conn: sqlite3.Connection) -> bool:
    """True when the index carries positional data, not just symbols.

    `chunks` holds the occurrence blobs and `mentions` the symbol/role rows
    that every per-file nav tool reads. Both empty while `global_symbols` is
    populated means positions were dropped somewhere upstream -- the index
    looks healthy and answers every nav query with an empty list.
    """
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    mentions = conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
    return chunks > 0 and mentions > 0
```

In `index_repo`, reuse the connection already opened for the graph step to also probe, then use the result for the final status. Replace the graph block and the success `upsert`:

```python
            graph_store = GraphStore(config.data_dir(root) / "registry.db")
            index_conn = sqlite3.connect(db_path)
            try:
                populate_graph_for_repo(graph_store, slug, index_conn)
                has_nav = index_has_navigation_data(index_conn)
            finally:
                index_conn.close()
                graph_store.close()
```

and, after `_publish_atomically(...)`:

```python
        final_status = "indexed" if has_nav else PARTIAL_STATUS
        registry.upsert(slug, str(repo_path), language, sha, final_status)
        if not has_nav:
            print(
                f"warning: {slug} published with symbols but no navigable positions "
                "(chunks/mentions empty) — per-file navigation will return no results. "
                "Check that the indexer emits occurrence ranges and that scip is >= v0.9.0.",
                file=sys.stderr,
            )
```

Note `has_nav` is assigned inside the `with tempfile.TemporaryDirectory(...)` block and read after it; both are inside the same `try`, so this is valid. Keep the assignment before `_publish_atomically` so a probe failure cannot leave a published index with an unset status.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -k "has_navigation_data" -v`
Expected: PASS.

- [ ] **Step 5: Verify the gate on a real index**

With the fixed decoder and a v0.9.0 `scip`, the Swift fixture must now come out fully `indexed`:
```bash
PATH="/tmp/swiftproof/bin:$PATH" JARVIS_DATA_DIR=/tmp/swiftproof/data \
  uv run jarvis index /tmp/swiftproof/repo --slug gatecheck
JARVIS_DATA_DIR=/tmp/swiftproof/data uv run jarvis status gatecheck
```
Expected: `status: indexed` with no warning — proving the gate does not produce false positives after Tasks 1-2.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "feat(index): flag indexes that publish symbols but no positions

chunks/mentions both empty while global_symbols is populated means every
nav query will return empty. Record status 'partial' and warn rather than
reporting unqualified success."
```

---

### Task 7: `typeHierarchy` reports unavailability instead of empty results

**Files:**
- Modify: `src/jarvis/query.py`, `src/jarvis/server.py`
- Test: `tests/test_query.py`, `tests/test_server_tools.py`

**Interfaces:**
- Consumes: `get_connection`, `_freshness_snapshot` in `query.py`
- Produces:
  - `relationship_data_present(conn: sqlite3.Connection) -> bool` in `query.py`
  - `QueryService.type_hierarchy` returns a 4-tuple `(supertypes, subtypes, freshness, available)` — **breaking change** to that method's contract, consumed only by `server.py`
  - `server.py`'s `typeHierarchy` returns `{"error": "..."}` when unavailable

Why: `scip expt-convert` never writes `global_symbols.relationships` — confirmed by reading `insertGlobalSymbols()` in `cmd/scip/convert.go` at v0.9.0, which binds only symbol/display_name/kind/documentation/enclosing_symbol. So `typeHierarchy` always returns `{"supertypes": [], "subtypes": []}`, which an agent reads as "this type has no supertypes" — a false statement rather than a missing capability. Reported upstream as [scip-code/scip#464](https://github.com/scip-code/scip/issues/464), with a fix proposed in [scip-code/scip#465](https://github.com/scip-code/scip/pull/465); until that lands, the capability is genuinely unavailable on every real index.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_query.py`:

```python
def test_relationship_data_present_false_when_all_null(tmp_path):
    from jarvis.query import relationship_data_present

    db = tmp_path / "i.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE global_symbols (
            id INTEGER PRIMARY KEY, symbol TEXT, relationships BLOB
        );
        INSERT INTO global_symbols (symbol, relationships) VALUES ('a', NULL);
        INSERT INTO global_symbols (symbol, relationships) VALUES ('b', NULL);
        """
    )
    conn.commit()
    try:
        assert relationship_data_present(conn) is False
    finally:
        conn.close()


def test_relationship_data_present_true_when_any_non_null(tmp_path):
    from jarvis.query import relationship_data_present

    db = tmp_path / "i.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE global_symbols (
            id INTEGER PRIMARY KEY, symbol TEXT, relationships BLOB
        );
        INSERT INTO global_symbols (symbol, relationships) VALUES ('a', NULL);
        INSERT INTO global_symbols (symbol, relationships) VALUES ('b', X'00');
        """
    )
    conn.commit()
    try:
        assert relationship_data_present(conn) is True
    finally:
        conn.close()
```

Append to `tests/test_server_tools.py`. This module's tests are `@pytest.mark.anyio async def`, drive the tool through a real MCP client session via `create_connected_server_and_client_session(server.mcp)`, and read `json.loads(result.content[0].text)`. Follow that shape exactly. To force the unavailable branch, monkeypatch the method on the class — the same seam `test_unexpected_exception_still_returns_structured_error_payload` already uses:

```python
@pytest.mark.anyio
async def test_type_hierarchy_reports_unavailable_instead_of_empty(monkeypatch):
    """Empty arrays read as "no supertypes exist" -- a false answer.

    scip expt-convert never populates global_symbols.relationships on a real
    index, so the tool cannot answer and must say so rather than imply one.
    """

    def _unavailable(self, repo, symbol):
        _, metadata = query.get_connection(self._cache, repo)
        return [], [], query._freshness_snapshot(metadata), False

    monkeypatch.setattr(server.QueryService, "type_hierarchy", _unavailable)
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("typeHierarchy", {"repo": REPO, "symbol": "x"})
        payload = json.loads(result.content[0].text)

    assert "error" in payload, payload
    assert "relationships" in payload["error"].lower()
    assert "supertypes" not in payload


@pytest.mark.anyio
async def test_type_hierarchy_returns_results_when_relationships_present():
    """The synthetic fixture DOES carry a contrived non-NULL relationships
    blob for `Greeter#`, so the available path is what this index exercises —
    no monkeypatching needed. Guards against the unavailable branch
    swallowing genuine results.
    """
    async with create_connected_server_and_client_session(server.mcp) as client:
        result = await client.call_tool("typeHierarchy", {"repo": REPO, "symbol": "x"})
        payload = json.loads(result.content[0].text)

    assert "error" not in payload, payload
    assert "supertypes" in payload
    assert "subtypes" in payload
```

Confirm `query` is imported in that module (it is — the `_wired_query_service` fixture calls `query.QueryService`) and that `REPO`, `json`, and `create_connected_server_and_client_session` are already in scope (they are).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_query.py -k relationship_data -v` and
`uv run pytest tests/test_server_tools.py -k type_hierarchy -v`
Expected: FAIL — `relationship_data_present` missing; the server tool returns no `error` key and unpacks a 3-tuple.

- [ ] **Step 3: Write the implementation**

In `src/jarvis/query.py`, add above `class QueryService`:

```python
def relationship_data_present(conn: sqlite3.Connection) -> bool:
    """True when any symbol carries relationship data.

    `scip expt-convert` declares `global_symbols.relationships` in its schema
    but never writes it -- `insertGlobalSymbols()` binds only symbol,
    display_name, kind, documentation and enclosing_symbol (verified against
    cmd/scip/convert.go at v0.9.0). Type hierarchy is therefore unanswerable
    on every real index, and reporting an empty result would assert that a
    type has no supertypes rather than that we cannot tell.

    Self-healing: this flips to True with no code change if a future
    converter starts populating the column.
    """
    row = conn.execute("SELECT 1 FROM global_symbols WHERE relationships IS NOT NULL LIMIT 1").fetchone()
    return row is not None
```

Change `QueryService.type_hierarchy` to report availability:

```python
    def type_hierarchy(
        self, repo: str, symbol: str
    ) -> tuple[list[TypeHierarchyEntry], list[TypeHierarchyEntry], FreshnessSnapshot, bool]:
        """Returns (supertypes, subtypes, freshness, available).

        `available` is False when the index carries no relationship data at
        all, which the caller must surface as "cannot answer" rather than as
        an empty hierarchy.
        """
        conn, metadata = get_connection(self._cache, repo)
        available = relationship_data_present(conn)
        if not available:
            return [], [], _freshness_snapshot(metadata), False
        supertypes = _type_hierarchy_supertypes(conn, symbol)
        subtypes = _type_hierarchy_subtypes(conn, symbol)
        return supertypes, subtypes, _freshness_snapshot(metadata), True
```

In `src/jarvis/server.py`, update the tool. Replace the body of `type_hierarchy`:

```python
@mcp.tool(name="typeHierarchy")
def type_hierarchy(repo: str, symbol: str) -> dict[str, Any]:
    """Single-level super/subtypes for `symbol` within `repo`.

    Returns an explicit error when the index carries no relationship data —
    `scip expt-convert` does not populate `global_symbols.relationships`, so
    an empty result would wrongly imply the symbol has no supertypes."""
    try:
        supertypes, subtypes, freshness, available = _service().type_hierarchy(repo, symbol)
    except Exception as exc:
        # Broad on purpose — keeps every tool's error shape the same {"error": ...} dict.
        return {"error": str(exc)}
    if not available:
        return {
            "error": (
                "typeHierarchy unavailable for this index: no symbol carries relationship "
                "data. `scip expt-convert` declares global_symbols.relationships but never "
                "writes it, so super/subtypes cannot be determined. This is an upstream "
                "converter limitation, not a missing symbol — do not read it as "
                "'this type has no supertypes'."
            ),
            "symbol": symbol,
            **_freshness_fields(freshness),
        }
    return {
        "symbol": symbol,
        "supertypes": [_json_safe(asdict(e)) for e in supertypes],
        "subtypes": [_json_safe(asdict(e)) for e in subtypes],
        **_freshness_fields(freshness),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_query.py tests/test_server_tools.py -v`
Expected: PASS. Any pre-existing test asserting `type_hierarchy`'s 3-tuple must be updated to the 4-tuple — that is an intentional contract change, so update the assertion rather than reverting the signature.

- [ ] **Step 5: Verify against a real index**

```bash
JARVIS_DATA_DIR=/tmp/swiftproof/data uv run python -c "
import jarvis.server as server
print(server.type_hierarchy(repo='gatecheck', symbol='does-not-matter'))
"
```
Expected: a payload containing the explicit `error` string, not empty arrays.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/query.py src/jarvis/server.py tests/test_query.py tests/test_server_tools.py
git commit -m "fix(query): typeHierarchy reports unavailability, not empty results

expt-convert never populates global_symbols.relationships, so empty arrays
asserted 'no supertypes' when the truth was 'cannot tell'."
```

---

### Task 8: Correct the documentation

**Files:**
- Modify: `docs/project-roadmap.md`, `docs/codebase-summary.md`, `README.md`

**Interfaces:**
- Consumes: the verified behavior from Tasks 1-7
- Produces: no code

The roadmap currently states the Swift gap is that "`scip-swift`'s emitted occurrences carry no `Range` data — a gap in `scip-swift` itself." **That diagnosis is wrong** and must be corrected: scip-swift emits `single_line_range` correctly; the ranges were lost because `scip v0.7.0` cannot read the `typed_range` oneof and jarvis's vendored gencode had no such field.

- [ ] **Step 1: Correct the roadmap's Swift diagnosis**

In `docs/project-roadmap.md`, replace the paragraph beginning `**Update (July 26):**` and the `**Invocation compatibility (July 26):**` paragraph's surroundings as needed, so the Swift section reads:

```markdown
**Update (July 26) — Swift navigation works; the earlier diagnosis was wrong.**
An earlier note here claimed `scip-swift` emitted no occurrence ranges. That was
incorrect. `scip-swift` sets `single_line_range`, the `typed_range` oneof
introduced in scip.proto (`SingleLineRange single_line_range = 8`), and does not
set the deprecated repeated-int32 `range` field — which is exactly what the
current spec tells producers to do.

The ranges were being dropped by two consumers:

1. **`scip expt-convert` v0.7.0** predates `bindings/go/scip/occurrence_range.go`
   and cannot read `typed_range`, so it silently produced a schema-valid database
   with `chunks=0, mentions=0`. Verified: the same `.scip` file yields
   `chunks=0/mentions=0` under v0.7.0 and `chunks=1/mentions=14` under v0.9.0.
   `setup.sh` pins v0.9.0, and `index_repo()` now refuses anything older.
2. **jarvis's vendored `scip_pb2.py`** was generated from scip.proto v0.7.0 and
   had no `typed_range` field, so even a v0.9.0-produced index decoded 0/16
   occurrence ranges. Regenerated from v0.9.0; `scip_decoder.py` now reads
   `typed_range` first with a deprecated-`range` fallback, mirroring upstream
   Go's `Occurrence.SourceRange()`.
```

- [ ] **Step 2: Record the remaining real limitation**

Still in `docs/project-roadmap.md`, replace the existing "Known gap" wording about `typeHierarchy` so it reflects the new behavior:

```markdown
**`typeHierarchy` is unavailable, and now says so.** `scip expt-convert` declares
`global_symbols.relationships` in its schema but never writes it —
`insertGlobalSymbols()` in `cmd/scip/convert.go` (v0.9.0) binds only symbol,
display_name, kind, documentation and enclosing_symbol. The tool therefore
returns an explicit `{"error": ...}` rather than empty arrays, because an empty
result would assert "this type has no supertypes" when the truth is "cannot
tell". `query.py`'s logic is complete and self-heals if a future converter
populates the column.

Reported upstream: [scip-code/scip#464](https://github.com/scip-code/scip/issues/464),
fixed by [scip-code/scip#465](https://github.com/scip-code/scip/pull/465) (open, CI
green). `global_symbols.signature` is left unpopulated there deliberately — the
column name and the proto field (`signature_documentation`) diverge. Once #465
lands, `typeHierarchy` starts working with no change here beyond installing the
newer `scip`.
```

- [ ] **Step 3: Refresh `docs/codebase-summary.md`**

Add the files introduced since it was last written — it currently mentions none of them. In the directory-structure block, add `setup.sh` and `ZOEKT_COMMIT` at repo root and a `.github/workflows/` entry. Then add to the module/test tables:

```markdown
| `setup.sh` | ~430 | POSIX-sh dependency bootstrapper: installs scip, zoekt, scip-swift, and the npm indexers into `~/.jarvis/bin`; detect-only for scip-java | `--only`, `--force` |
| `ZOEKT_COMMIT` | 1 | Pinned upstream `sourcegraph/zoekt` commit that CI cross-compiles | — |
```

```markdown
| `test_setup_sh.py` | setup.sh | Sources the script under `dash` (not `sh` — macOS `/bin/sh` accepts bashisms) and tests each function in isolation |
```

And a CI section:

```markdown
## CI Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `.github/workflows/build-zoekt.yml` | `ZOEKT_COMMIT` change or manual dispatch | Cross-compiles `zoekt-index`/`zoekt-webserver` for macOS+Linux (arm64/amd64) and publishes them to this repo's releases — upstream `sourcegraph/zoekt` ships no binaries at all |
| `.github/workflows/setup-smoke.yml` | `setup.sh`/test changes, PRs, manual | Runs `setup.sh` on `ubuntu-latest` (where `/bin/sh` is dash) and `macos-latest`: parse check, install, idempotency, full unit suite |
```

Also correct the "Known gap"/env-var notes in that file if they repeat the old Swift diagnosis, and update the `scip` requirement to `>= v0.9.0`.

- [ ] **Step 4: Update the README's version requirement**

In `README.md`, change the `scip` row of the install table so the floor is explicit:

```markdown
| SCIP → SQLite conversion | `scip` | prebuilt, pinned `v0.9.0` (**minimum** — older versions silently drop occurrence ranges) |
```

And replace the Swift caveat paragraph, which now states a fixed problem:

```markdown
Swift indexing works end-to-end. It requires `scip >= v0.9.0`: older converters
cannot read scip.proto's `typed_range` oneof, which is the only range encoding
`scip-swift` emits, and silently produce an index with no navigable positions.
`jarvis index` refuses an older `scip` rather than publishing one.
```

- [ ] **Step 5: Verify every claim in the changed docs**

Re-read each edited paragraph against the code as it now stands. Specifically confirm: `MIN_SCIP_VERSION` really is `(0, 9, 0)`; `setup.sh`'s `SCIP_VERSION` really is `v0.9.0`; `typeHierarchy` really returns `error`; the workflow filenames match; and `test_setup_sh.py` really prefers dash. Fix any drift in the docs, not the code.

- [ ] **Step 6: Full suite and commit**

```bash
uv run pytest
```
Expected: PASS (all tests, integration included, with a v0.9.0 `scip` on PATH).

```bash
git add docs/project-roadmap.md docs/codebase-summary.md README.md
git commit -m "docs: correct the Swift range diagnosis and record the scip floor

The earlier claim that scip-swift emits no ranges was wrong: it emits
single_line_range, and both scip v0.7.0 and jarvis's v0.7.0-era gencode
were unable to read the typed_range oneof."
```

---

## Notes for the implementer

**Clean your PATH before verifying anything.** `~/.local/bin/scip` is v0.7.0 and shadows whatever `setup.sh` installs. Several verification steps prepend a known-good directory for this reason. To fix it permanently:
```bash
rm -f ~/.local/bin/scip ~/.local/bin/scip-swift ~/.local/bin/zoekt-index ~/.local/bin/zoekt-webserver
sh setup.sh
```

**Tasks 1 and 2 are the Swift fix and must land together** to be meaningful — Task 1 only makes the fields available, Task 2 reads them. Task 5 depends on Task 4 (shards must be slug-named before `forget` can match them by slug). Tasks 3, 6 and 7 are independent.

**Do not "fix" `scip-swift`.** It is behaving correctly per the current scip.proto, which instructs producers to set `typed_range` and not the deprecated field. Every change in this plan is on the consumer side.

**Task 7 changes `QueryService.type_hierarchy`'s return arity** from 3 to 4 elements. `server.py` is its only consumer, but grep before assuming:
```bash
grep -rn "type_hierarchy" src/ tests/
```

**Out of scope, tracked elsewhere:** bumping the CI actions off Node 20 (`actions/checkout` v4→v7.0.1, `actions/setup-go` v5→v7.0.0, `astral-sh/setup-uv` v5→v9.0.0) is independent hygiene with no interaction with these changes.
