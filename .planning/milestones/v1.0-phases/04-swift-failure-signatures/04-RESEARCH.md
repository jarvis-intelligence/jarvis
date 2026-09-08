# Phase 4: Swift Failure Signatures - Research

**Researched:** 2026-08-23
**Domain:** scip-swift 0.3.0 failure classification → automatic search-only degradation (substring signatures)
**Confidence:** HIGH

## Summary

The implementation surface for SWFT-04 is remarkably small and fully pre-wired. Every signature captured this session flows through the **existing** signature branch at `src/jarvis/index_cli.py:1064-1084`: `_search_only_reason()` is already consulted on every indexer-step `IndexingError`, and a match already publishes search-only with `origin='signature'`, `status_reason=<matched reason>`, `search_only=True` — with **zero** changes to branching, origin stamping, status reporting, MCP payloads, or recovery. The phase is therefore: (1) append two `(tokens, reason)` entries to `_SEARCH_ONLY_SIGNATURES`, (2) add pinning tests embedding the exact captured stderr with provenance comments, (3) add negative tests proving generic build-failure wrappers never match. The precedence requirement (signature beats the opt-in degrade gate regardless of flag) is verified structurally in code AND pinned by an existing phase-3 test.

Both SC1-named failure classes were **reproduced live on this machine** against the real pinned binary (scip-swift 0.3.0, brew tap `phuongddx/scip-swift`, sha256 `b0de7201…85a5`, Xcode 26.3 / Swift 6.2.4) and re-verified end-to-end through the real `jarvis index` pipeline — the verbatim stderr reaches the failure carrier unmodified, and today both shapes fail hard (`origin=failed_hard`), exactly the before-state the phase flips. The decisive capture insight: scip-swift 0.3.0 emits two *specific, stable, path-free* error strings for the known-unfixable classes, while all transient/configurable failures (broken manifest, tools-version too new, compile errors, UIKit-on-host-SPM) hide behind two *generic wrappers* (`Error: 'swift build' failed with exit code 1:` / `Error: 'xcodebuild' failed with exit code 65:`) that must NEVER be signature-matched — matching a wrapper would launder every broken Swift repo into silent degradation.

**Primary recommendation:** Append exactly two entries to `_SEARCH_ONLY_SIGNATURES` using the path-free tokens quoted verbatim below, each pinned by a unit test embedding the full captured stderr (with binary-version provenance comment), plus one negative test pinning that the generic `'swift build' failed with exit code 1:` wrapper does not match. No other production code changes.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Capture method: reproduce failures locally — craft known-failing Swift repo shapes (no build system, no IndexStore produced, …), run the real scip-swift 0.3.0, capture **verbatim stderr**. No curation from upstream prose.
- v1 signature set: the two SC1-named classes (no IndexStore produced; no build system detected) plus any other reproducible known-unfixable found during capture — every entry must meet the same real-stderr evidence bar.
- Test pinning: unit tests embed the **exact captured stderr** with a provenance comment (binary version, trigger repo shape); drift in either breaks loudly.
- Matching mechanism: substring match, identical to the existing Kotlin/AGP `_SEARCH_ONLY_SIGNATURES` entries. No regex.
- Precedence: a matched signature takes the automatic `origin='signature'` permanent search-only path (Kotlin/AGP parity per SC1) regardless of the phase-3 fallback flag; the opt-in `degraded` state remains for unclassified failures only.
- Auto-roll wording drift (future scip-swift releases changing stderr): **fails hard by design** (SC3 safety direction) — drift → unmatched → hard failure, never silent degradation. Documented in the signature constant block; re-capture on version bump.

### Claude's Discretion
None flagged — all areas resolved with explicit answers.

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SWFT-04 | Verified scip-swift failure signatures join `_SEARCH_ONLY_SIGNATURES`, captured from the pinned binary's real stderr | Two signatures captured verbatim from scip-swift 0.3.0 on live known-failing repos (below); existing consult site `index_cli.py:1064-1084` verified to flow them to the `origin='signature'` path; pinning-test pattern extracted from `tests/test_index_cli.py:1896-1947`; unmatched-failure hard-fall verified live (exit 1, `failed_hard`) |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Failure signature capture (stderr strings) | Local CLI tooling (scip-swift binary) | — | Ground truth lives in the pinned binary's stderr; no amount of code inspection substitutes for running it |
| Signature matching (substring scan) | CLI indexing pipeline (`index_cli._search_only_reason`) | — | Already implemented at the single failure-carrier site; matching is language-agnostic |
| Degradation decision + publish | CLI indexing pipeline (`index_repo`) | — | The signature branch (inner except) preempts the phase-3 degrade gate (outer except) by construction |
| Origin/reason persistence | Registry (`registry.upsert` status_origin/status_reason) | — | Columns exist since phase 1; signature path already stamps them |
| Recovery/status rendering | Registry read-time (`recovery_for`, `origin_of`) + `jarvis status` + MCP `getIndexStatus` | — | All read-time mappings exist; zero reshaping (phase-1 D-09/D-11, phase-3 03-02 verified additive) |
| Signature pinning tests | pytest unit tests (mocked `_run`) | — | CI unit legs have no scip-swift binary; tests embed captured text, never shell out |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| (none new) | — | Substring matching is pure Python (`all(token in output ...)`) | The entire feature rides existing code; no dependency change of any kind |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | existing (`testpaths = ["tests"]`, pyproject.toml:110) | pinning + negative tests | always — this phase's only deliverables beside the constant |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Substring tuples | Regex signatures | Rejected by locked decision; regex adds false-match surface and review cost for zero need |
| Capturing at test time (run scip-swift in test) | Embedded captured stderr | CI unit legs lack the binary and Linux cannot run it at all; embedded-text pinning is the only portable form (and matches the locked decision) |

**Installation:** nothing to install.

**Version verification:** no new packages. Instrument provenance: `scip-swift --version` → `0.3.0 (swift 6.2.4)` [VERIFIED: local run]; binary `/opt/homebrew/Cellar/scip-swift/0.3.0/bin/scip-swift`, sha256 `b0de72016121e8d469f0f482730da2cd71ff5f6b8a976733fac6db67924985a5` [VERIFIED: shasum]; installed from tap `phuongddx/scip-swift` → `github.com/jarvis-intelligence/scip-swift` [VERIFIED: brew info].

## Package Legitimacy Audit

No packages are installed by this phase. **Gate not applicable** — the change is two constant-tuple entries plus tests over existing stdlib code.

## Architecture Patterns

### System Architecture Diagram

Failure flow after this phase (Swift branch; ★ = changed by this phase, everything else existing):

```
jarvis index <swift-repo>
  │
  ├─ pre-pipeline wrap (index_cli.py:947-1005)
  │    ├─ check_scip_version() ────────────── fail → failed_hard (STAYS HARD — never degrades)
  │    └─ check_scip_swift_version() ──────── fail → failed_hard (STAYS HARD — never degrades)
  │
  ├─ main pipeline: _run(scip-swift … --output <scratch>/index.scip)   [cwd=repo]
  │    │
  │    └─ IndexingError ──▶ inner except (index_cli.py:1064)
  │         │
  │         ├─ java-only bash-shim re-check → remedy + re-raise (not Swift)
  │         │
  │         ├─ _search_only_reason(carrier)            ◀── ★ new Swift entries consulted here
  │         │    │
  │         │    ├─ MATCH (no build system / no IndexStore)
  │         │    │     → _publish_search_only (zoekt shards live)
  │         │    │     → upsert status='search-only', search_only=True,
  │         │    │              origin='signature', reason=<matched text>
  │         │    │     → return slug  (REGARDLESS of fallback flag — preempts gate)
  │         │    │
  │         │    └─ NO MATCH → raise (unverified failure is never degraded)
  │         │           │
  │         ▼           ▼
  │    outer except (index_cli.py:1142)  ← only reached when NO signature matched
  │         ├─ degrade gate (1162): not published AND fallback_enabled
  │         │    AND not MissingBinaryError AND not bash-shim
  │         │    → DEGRADED_STATUS / origin='fallback' (opt-in, self-healing)
  │         └─ else → failed_hard row + raise (exit ≠ 0)
  │
  └─ success → indexed / partial → atomic publish
```

Trace: a Swift repo with no Package.swift/.xcodeproj enters the indexer step, scip-swift exits 1 with the detection error, the carrier text matches a ★ signature, search-only publishes, and the row reports `origin='signature'` with the matched reason — no opt-in, no degrade-gate involvement.

### Recommended Project Structure
```
src/jarvis/index_cli.py     # _SEARCH_ONLY_SIGNATURES (~106-123): +2 entries + drift note
tests/test_index_cli.py     # +2 pinning tests, +1 negative wrapper test (mirror 1896-1947)
```
No new files. No changes to server.py, registry.py, watch.py, config.py.

### Pattern 1: Signature entry shape (verbatim from source)
**What:** `(required-substring-tuple, human-reason)` pairs; a match needs EVERY token present in the carrier.
**When to use:** exactly as the existing Kotlin/AGP entries.
From `src/jarvis/index_cli.py:98-123` [VERIFIED: read this session]:
```python
# Indexer failures that are known to be unfixable from here, and so degrade to
# a search-only publish instead of a hard failure. Each entry is
# (required substrings, human reason) — EVERY substring must be present, which
# is what keeps a generic AbstractMethodError from some unrelated library out.
#
# Deliberately narrow. This is not "fall back on any failure": a transient
# Gradle break or a missing binary must still fail loudly rather than be
# laundered into an apparent success.
_SEARCH_ONLY_SIGNATURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("AbstractMethodError", "org.jetbrains.kotlin.fir"),
        "scip-kotlinc is compiled against one exact Kotlin version and this repo uses another "
        "(the compiler-plugin API is internal and unstable)",
    ),
    ...
)
```
Matcher (`index_cli.py:126-132`) [VERIFIED: read this session]:
```python
def _search_only_reason(output: str) -> str | None:
    for required, reason in _SEARCH_ONLY_SIGNATURES:
        if all(token in output for token in required):
            return reason
    return None
```

### Pattern 2: The carrier the matcher sees
`_run` raises (index_cli.py:434-435) [VERIFIED: read this session]:
```python
    if result.returncode != 0:
        raise IndexingError(f"{step} failed ({' '.join(cmd)}):\n{result.stdout}\n{result.stderr}")
```
For Swift the step name is always `scip-swift index` (index_cli.py:1061-1062: `step=f"{indexer_cmd[0]} index"`), so the carrier is: the step+argv header line, then **stdout, then the verbatim stderr**. Live-captured carrier (real `jarvis index`, this machine) [VERIFIED: /tmp/jarvis-p4-capture/out/jarvis-nobuildsystem.txt]:
```
error: scip-swift index failed (scip-swift --cache-dir /tmp/jarvis-p4-capture/jarvis-data/cache/scip-swift/p4-nobuildsystem --output /var/folders/…/T/jarvis-index-zoa3rhof/index.scip):

Error: Could not detect a build system at /tmp/jarvis-p4-capture/repos/swift-nobuildsystem: no Package.swift and no .xcodeproj/.xcworkspace found. Pass --build-tool swiftpm or --build-tool xcodebuild explicitly.
```
Consequence: tokens must avoid the header line entirely — it embeds per-run cache/scratch paths.

### Pattern 3: Pinning-test shape (mirror of the existing Kotlin test)
From `tests/test_index_cli.py:1896-1947` [VERIFIED: read this session] — fake `_run` raises `IndexingError(carrier)` on `step.endswith(" index")` (matches only the indexer step, letting `_publish_search_only` run for real against mocked zoekt), then assert the row. Swift variants must additionally monkeypatch `check_scip_swift_version` (pattern at tests/test_index_cli.py:1342) because the swift branch probes the binary pre-pipeline and **CI unit legs have no scip-swift** [VERIFIED: AGENTS.md CI section — unit tests are binary-free; scip-swift is darwin/arm64-only].

### Anti-Patterns to Avoid
- **Matching a generic wrapper string** (`'swift build' failed with exit code 1:`, `'xcodebuild' failed with exit code 65:`): scip-swift wraps *every* build failure in these; 4 different failure shapes produced the same wrapper this session. Matching one launders broken repos into silent degradation — the exact thing the design forbids.
- **Embedding paths in tokens:** both error strings interpolate the repo path and cache path; tokens must be path-free or the signature only matches one machine.
- **Touching the degrade gate or origin plumbing:** the signature path already exists and is pinned by phase-1/phase-3 tests; any wiring change is scope creep and regression risk.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Failure classification | New Swift-specific matcher/branch | `_SEARCH_ONLY_SIGNATURES` + `_search_only_reason` | Exists, tested, language-agnostic; two more tuples complete the feature |
| Origin stamping / recovery / status | New status field or payload | Existing `ORIGIN_SIGNATURE` path (`registry.py:34`, `recovery_for` registry.py:159-160) | Phase-1 D-01..D-15 + phase-3 03-02 already ship end-to-end reporting for this origin |
| Test scaffolding | New fixture repos in git | `SWIFT_FIXTURE_REPO` (mini_swift_repo) + mocked `_run` raising the captured carrier | Detection just needs tracked `.swift` files; the binary is never invoked in unit tests |

**Key insight:** phase 1 built the whole degradation-reporting pipeline around a *generic* signature mechanism precisely so that phases like this one are data-only.

## Common Pitfalls

### Pitfall 1: The generic build-failure wrappers
**What goes wrong:** signing the wrapper text (`Error: 'swift build' failed with exit code 1:` or `Error: 'xcodebuild' failed with exit code 65:`) silently degrades every broken Swift repo — compile errors, invalid manifests, missing targets, unbuildable-on-host packages.
**Why it happens:** the wrapper is the most prominent line of every Swift failure; the real classifier text sits *after* it.
**How to avoid:** tokens must come from the class-specific strings quoted below; add the negative test (wrapper-only carrier → `_search_only_reason() is None`).
**Warning signs:** a review diff whose tokens are a prefix of `Error: 'swift build' failed` / `Error: 'xcodebuild' failed`.

### Pitfall 2: Paths inside tokens
**What goes wrong:** signatures that only match the capture machine (repo path, `/tmp/...` cache path, DerivedData path, scratch tmpdir are all interpolated into the carrier).
**Why it happens:** the error strings themselves embed absolute paths.
**How to avoid:** keep every token a stable substring away from any `<path>` interpolation point (both proposed token sets are).
**Warning signs:** a token containing `/`, `derived-data`, or `DataStore`.

### Pitfall 3: Forgetting `check_scip_swift_version` in tests
**What goes wrong:** new Swift pinning tests pass locally (binary present) and fail on ubuntu CI legs with `scip-swift not found on PATH` — a pre-pipeline failure that fires *before* the mocked indexer step.
**Why it happens:** the swift branch probes the binary at index_cli.py:986; the existing Kotlin signature test doesn't need the analogous mock.
**How to avoid:** monkeypatch `check_scip_swift_version` alongside `check_scip_version` (pattern at tests/test_index_cli.py:1342).
**Warning signs:** `monkeypatch.setattr("jarvis.index_cli.check_scip_version", ...)` without the swift twin.

### Pitfall 4: Assuming project settings can suppress the IndexStore (they can't)
**What goes wrong:** trying to re-capture the "no IndexStore" class by setting `COMPILER_INDEX_STORE_ENABLE = NO` in a project produces a *successful* index — scip-swift force-passes `COMPILER_INDEX_STORE_ENABLE=YES` on the xcodebuild command line (visible verbatim in the captured invocation below), overriding project settings.
**Why it happens:** scip-swift pins its own build settings to guarantee store emission.
**How to avoid:** reproduce the class with a build that compiles zero Swift (empty Sources build phase — the exact trigger used this session; preserved at `/tmp/jarvis-p4-capture/repos/xcode-emptysources`).
**Warning signs:** any re-capture recipe that edits pbxproj build settings.

### Pitfall 5: Confusing the permanent search-only consequence with the phase-3 degraded state
**What goes wrong:** expecting `jarvis reindex` to retry the full build after a signature match. It will not: the signature branch persists `search_only=True` (index_cli.py:1079), so every later reindex takes the search-only branch (index_cli.py:1007) and skips the indexer. The only escape is `jarvis forget` + `jarvis index` — Kotlin/AGP parity, and the locked decision accepts it.
**Why it happens:** `recovery_for(ORIGIN_SIGNATURE)` returns the generic `jarvis reindex {slug}` (registry.py:159-160, D-11 locked) which is honest for "refresh the search-only index" but does not restore SCIP navigation.
**How to avoid:** don't change `recovery_for` (locked); document the forget-escape in the signature comment block and surface it at UAT.
**Warning signs:** a plan task editing `recovery_for` or adding remedies to reason text (D-11: reasons only).

### Pitfall 6: Treating `Wrote 0 document(s)` as a failure
**What goes wrong:** building a repo whose targets compile no Swift (e.g. C-only SPM target) makes scip-swift exit **0** with `Wrote 0 document(s)` — that is the existing `PARTIAL_STATUS` publish path, not a signature case.
**Why it happens:** exit-0 runs never reach the failure carrier.
**How to avoid:** signatures live only on non-zero exits; don't add post-hoc output scanning for successful runs.
**Warning signs:** any proposal matching against stdout of exit-0 processes.

## Code Examples

### Captured Signature 1 — no build system detected (SC1 class A) [VERIFIED: /tmp/jarvis-p4-capture/out/swift-nobuildsystem/stderr.txt]
Trigger shape: git repo, ≥1 tracked `.swift` file, **no** `Package.swift` and **no** `.xcodeproj`/`.xcworkspace` at root. jarvis argv (auto, no `--build-tool`): `scip-swift --cache-dir <cache> --output <scratch>/index.scip` (cwd=repo). Exit 1, stdout empty. Full verbatim stderr (single line):
```
Error: Could not detect a build system at /tmp/jarvis-p4-capture/repos/swift-nobuildsystem: no Package.swift and no .xcodeproj/.xcworkspace found. Pass --build-tool swiftpm or --build-tool xcodebuild explicitly.
```
Proposed entry (planner lifts verbatim; reason is a cause, not a remedy — D-11):
```python
(
    ("Could not detect a build system", "no Package.swift and no .xcodeproj/.xcworkspace found"),
    "the repo has Swift sources but neither a Package.swift nor an Xcode project, so "
    "scip-swift has no build system to run",
),
```

### Captured Signature 2 — build succeeded but no IndexStore (SC1 class B) [VERIFIED: /tmp/jarvis-p4-capture/out/xcode-emptysources/stderr.txt]
Trigger shape: `.xcodeproj` whose target's Sources build phase is empty (build exits 0, zero Swift compiled). jarvis argv: `scip-swift --build-tool xcodebuild --cache-dir <cache> --output <scratch>/index.scip`. Exit 1, stdout empty. Full verbatim stderr (single line):
```
Error: Build succeeded but no IndexStore was produced at /tmp/jarvis-p4-capture/caches/xcode-emptysources/derived-data/Index.noindex/DataStore. This commonly happens when the code being indexed cannot compile on this host (for example, Apple-platform-only imports such as UIKit/WatchKit/WidgetKit on a non-macOS host, or a missing SDK).
```
The same wording exists on the swiftpm path (store path `<cache>/build-scratch/<triple>/debug/index/store` — confirmed via the `--index-only` probe below and binary strings), so one entry covers both backends. Proposed entry:
```python
(
    ("Build succeeded but no IndexStore was produced",),
    "the build completed but produced no index store — scip-swift cannot extract "
    "symbols from a build that emits none",
),
```
(Consider a second token `"This commonly happens when the code being indexed cannot compile on this host"` if the planner wants the Kotlin-style two-token guard; the first token is already unique to this class in every capture.)

### Keep-hard exhibits — must NOT match (all captured this session)
| Shape | Wrapper + first class-specific line | Verdict |
|---|---|---|
| Malformed Package.swift [VERIFIED: out/spm-broken-manifest/stderr.txt] | `Error: 'swift build' failed with exit code 1:` + manifest compile errors | keep hard — repo bug, fixable |
| swift-tools-version 999.0 [VERIFIED: out/spm-tools-too-new/stderr.txt] | `Error: 'swift build' failed with exit code 1:` + `package … is using Swift tools version 999.0.0 but the installed version is 6.2.4` | keep hard — environment, upgradable |
| Zero-target package [VERIFIED: out/spm-notargets/stderr.txt] | `Error: 'swift build' failed with exit code 1:` + `The package does not contain a buildable target.` | keep hard — repo bug |
| UIKit-only SPM package [VERIFIED: out/spm-uikit/stderr.txt] | `Error: 'swift build' failed with exit code 1:` + `no such module 'UIKit'` | keep hard — generic compile failure on host build |
| xcodeproj with syntax-error source [VERIFIED: out/xcode-broken-code/stderr.txt, 17KB] | `Error: 'xcodebuild' failed with exit code 65:` + full xcodebuild log (also reveals scip-swift's invocation: `-derivedDataPath <cache>/derived-data COMPILER_INDEX_STORE_ENABLE=YES CODE_SIGNING_ALLOWED=NO …`) | keep hard — repo bug |
| `--index-only` with no prior build [VERIFIED: out/indexonly-probe/stderr.txt] | `Error: --index-only was used but no IndexStore was found at <cache>/build-scratch/<triple>/debug/index/store. Run scip-swift without --index-only first…` | unreachable from jarvis (never passes `--index-only`) — do NOT sign |
| C-only SPM target [VERIFIED: out/spm-conly/stdout.txt] | exit **0**, `Wrote 0 document(s) to …` | success → existing PARTIAL publish path, not a failure |

No other reproducible known-unfixable class surfaced: every remaining failure shape collapsed into the generic build wrappers above. **The v1 set is therefore exactly the two SC1-named classes.**

### Pinning-test skeleton (mirror tests/test_index_cli.py:1896-1947)
```python
def test_swift_no_build_system_signature_degrades_search_only(tmp_path, monkeypatch):
    # Provenance: captured 2026-08-23 from scip-swift 0.3.0
    # (sha256 b0de7201…85a5, Xcode 26.3 / Swift 6.2.4) against a git repo with
    # tracked .swift sources, no Package.swift, no .xcodeproj (carrier verified
    # end-to-end via real `jarvis index`).
    from jarvis.index_cli import SEARCH_ONLY_STATUS, _SEARCH_ONLY_SIGNATURES, IndexingError, index_repo
    from jarvis.registry import ORIGIN_SIGNATURE

    repo_dir = tmp_path / "repo"
    shutil.copytree(SWIFT_FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    carrier = (  # exact captured stderr; paths rewritten to this test's repo
        "Error: Could not detect a build system at "
        f"{repo_dir}: no Package.swift and no .xcodeproj/.xcworkspace found. "
        "Pass --build-tool swiftpm or --build-tool xcodebuild explicitly."
    )
    swift_reason = next(
        reason for tokens, reason in _SEARCH_ONLY_SIGNATURES
        if tokens == ("Could not detect a build system", "no Package.swift and no .xcodeproj/.xcworkspace found")
    )

    def _fake_run(cmd, *, cwd, step, env=None):
        if step.endswith(" index"):
            raise IndexingError(carrier)
        return _fake_completed_process(cmd)

    monkeypatch.setattr("jarvis.index_cli.check_scip_version", lambda: None)
    monkeypatch.setattr("jarvis.index_cli.check_scip_swift_version", lambda: None)  # Pitfall 3
    monkeypatch.setattr("jarvis.index_cli._run", _fake_run)
    monkeypatch.setattr("jarvis.index_cli._run_semantic_stage", lambda *a, **k: False)

    slug = index_repo(repo_dir, root=tmp_path / "data")
    registry = Registry(tmp_path / "data" / "registry.db")
    try:
        entry = registry.get(slug)
        assert entry.status == SEARCH_ONLY_STATUS
        assert entry.search_only is True
        assert entry.status_origin == ORIGIN_SIGNATURE
        assert entry.status_reason == swift_reason  # verbatim, not a paraphrase
    finally:
        registry.close()
```
(The IndexStore variant replaces carrier/reason; the negative test asserts
`_search_only_reason("Error: 'swift build' failed with exit code 1:\nerror: …")` is `None`.)

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Kotlin/AGP signatures only (`AbstractMethodError`, `NoSuchMethodError`, `No SCIP shards found`) | + Swift 0.3.0 signatures (this phase) | this phase | Swift known-unfixables degrade with no opt-in, matching Kotlin behavior |
| scip-swift v0.1.2 (pre-dispatch-fix), no cache-dir, watch self-triggered | 0.3.0 pinned floor, `--cache-dir` outside tree, watch ignores (phases 2-3) | phases 2-3 | capture instrument + no self-trigger; nothing further needed here |

**Deprecated/outdated:**
- `scip-swift index` subcommand token in jarvis argv: intentionally absent since phase 1 (index_cli.py:51-57 — bare form works on every version). Captures confirm 0.3.0 defaults to `index`.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | scip-swift ≥ 0.3.x auto-roll keeps these exact stderr wordings (no web access this session to check newer releases/changelog) | Common Pitfalls / drift | A future release rewording → signature stops matching → **hard failure by design** (locked decision); worst case is a lost degradation, never a false one. Re-capture recipe is fully documented above |
| A2 | The two-token guard on Signature 1 is desirable but the single first token is already unique (based on the 9 capture shapes; no cross-check beyond this machine possible offline) | Code Examples | Over- or under-specificity: too narrow → missed degrade (hard fail, safe direction); too broad → impossible short of the full first sentence, which never appears in other captures |

All other claims were verified this session by reading source, running the pinned binary, or running real `jarvis index` — no user confirmation needed for those.

## Open Questions

1. **Wording-stability watch on future scip-swift releases**
   - What we know: setup.sh auto-rolls to latest ≥ 0.3.0 (STATE.md 02-01); drift fails hard by locked decision; the pinning test pins *our* string, so it cannot detect upstream drift.
   - What's unclear: whether 0.3.x+ rewords these errors (offline session).
   - Recommendation: accept (locked); optionally note in the constant block that re-capture is a manual step on version bump. A cheap future hardening (out of scope): setup-smoke leg asserting the two strings against the freshly installed binary.
2. **Second token for Signature 2** (Kotlin-style two-token guard vs single token)
   - What we know: single token matched uniquely across all 9 shapes; two-token costs nothing.
   - Recommendation: planner's discretion; either satisfies the evidence bar.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| scip-swift | stderr capture (research, done) / post-change live UAT smoke | ✓ | 0.3.0 (swift 6.2.4), /opt/homebrew/bin | — |
| Xcode toolchain (xcodebuild) | IndexStore-class capture / UAT | ✓ | Xcode 26.3 (17C529) | — |
| zoekt-git-index, zoekt-webserver | search-only publish in live smoke | ✓ | ~/.jarvis/bin | — |
| scip (expt-convert) | full pipeline smoke | ✓ | ~/.local/bin/scip | — |
| git | repo shapes, detection | ✓ | system | — |

**Missing dependencies with no fallback:** none — and the *implementation itself* needs none: the production change is static strings; unit tests mock every subprocess. CI ubuntu legs require nothing new (they must keep working binary-free — Pitfall 3).
**Missing dependencies with fallback:** none.

Capture workspace (ephemeral, /tmp — re-create from the recipes above if gone): `/tmp/jarvis-p4-capture/` — `repos/` (9 shapes), `out/<shape>/{stdout,stderr,exit}.txt` (verbatim captures), `out/jarvis-*.txt` (live end-to-end carrier proof), `jarvis-data/` (scratch registry used for the live runs).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (testpaths `["tests"]`, `markers: integration` in pyproject.toml:110-111) |
| Config file | `pyproject.toml` |
| Quick run command | `uv run pytest tests/test_index_cli.py -m "not integration" -q` |
| Full suite command | `uv run pytest -m "not integration" -rs` (CI gate, 633 unit tests today) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SWFT-04a | "Could not detect a build system" carrier → search-only publish, origin='signature', reason verbatim | unit | `uv run pytest "tests/test_index_cli.py::test_swift_no_build_system_signature_degrades_search_only" -q` | ❌ Wave 0 (new) |
| SWFT-04b | "Build succeeded but no IndexStore was produced" carrier → same path | unit | `uv run pytest "tests/test_index_cli.py::test_swift_no_index_store_signature_degrades_search_only" -q` | ❌ Wave 0 (new) |
| SWFT-04c (SC3) | Generic `'swift build' failed with exit code 1:` carrier matches nothing (`_search_only_reason` → None) | unit | `uv run pytest "tests/test_index_cli.py::test_swift_generic_build_failure_wrapper_never_matches" -q` | ❌ Wave 0 (new) |
| SWFT-04d (existing, regression) | Signature match preempts degrade branch on opted-in repo | unit | `uv run pytest "tests/test_index_cli.py::test_signature_match_preempts_the_degrade_branch_on_an_opted_in_repo" -q` | ✅ (tests/test_index_cli.py:3669) |
| SWFT-04e (existing, regression) | Pre-pipeline scip-swift version gate stays hard | unit | `uv run pytest tests/test_index_cli.py -q -k check_scip_swift_version` | ✅ (tests/test_index_cli.py:676-706) |
| SWFT-04 UAT | Live: both capture shapes through real `jarvis index` degrade with `note: … cannot be SCIP-indexed`, status shows origin=signature, zoekt answers | manual smoke (integration-marked live run, not committed — phase-2 D-09/D-10 precedent) | rerun the two shapes from `/tmp/jarvis-p4-capture/repos/` with scratch `JARVIS_DATA_DIR` | n/a (manual) |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_index_cli.py -m "not integration" -q`
- **Per wave merge:** `uv run pytest -m "not integration" -rs`
- **Phase gate:** full unit suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_index_cli.py` — three new test functions above (SWFT-04a/b/c)
- [x] Fixtures: `SWIFT_FIXTURE_REPO` (mini_swift_repo) exists — no new fixture
- [x] Framework installed

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | local CLI/MCP, unchanged |
| V3 Session Management | no | unchanged |
| V4 Access Control | no | unchanged |
| V5 Input Validation | yes | signatures are static substring constants matched against subprocess stderr; matched reason text is persisted via existing parameterized sqlite upsert (`registry.upsert`) — no new parsing, no eval, no regex, no shell |
| V6 Cryptography | no | unchanged |

### Known Threat Patterns for {stack}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Hostile repo fakes a signature string in build output (e.g. malicious SPM prebuild plugin echoing the error text) to force a search-only publish instead of a hard failure | Tampering / Repudiation | Considered and dismissed: the zoekt shards index exactly the same git-tracked content a successful run would; no privilege or data boundary is crossed. Identical exposure has existed for the Kotlin/AGP signatures since phase 1. Worst case: a failing repo gets search-only instead of failed — visible via `origin='signature'` in status |
| Unverified failure silently degraded (SC3) | Tampering | The design direction itself: unmatched → re-raise → hard failure; pinned by the negative test SWFT-04c |

## Sources

### Primary (HIGH confidence)
- Live capture, scip-swift 0.3.0 (sha256 b0de7201…85a5) on this machine, 2026-08-23 — all stderr under `/tmp/jarvis-p4-capture/out/` (9 shapes + 2 live jarvis runs + index-only probe)
- `src/jarvis/index_cli.py` — read this session: 50-57 (bare argv), 78-84 (version floor), 98-132 (signatures + matcher), 233-250 (`_swift_indexer_cmd`), 410-436 (`_run` carrier), 480-508 (version gate), 901-1005 (pre-pipeline wrap), 1064-1084 (signature branch), 1142-1245 (degrade gate)
- `src/jarvis/registry.py` — read this session: 20-36 (status/origin constants), 140-167 (`origin_of`/`recovery_for`)
- `tests/test_index_cli.py` — read this session: 35-47 (helpers), 1484-1947 (search-only + signature tests), 3635-3696 (degrade + preempt tests), 1342 (swift-gate mock pattern), 676-706 (version-gate tests)
- `AGENTS.md` — CI unit legs binary-free; integration via setup-smoke
- `.planning/STATE.md`, `.planning/REQUIREMENTS.md`, `.planning/phases/04-swift-failure-signatures/04-CONTEXT.md` — decisions, SWFT-04
- Binary introspection: `strings /opt/homebrew/bin/scip-swift` — embedded wordings `Build succeeded but no IndexStore was produced at `, `--index-only was used but no IndexStore was found at `, `COMPILER_INDEX_STORE_ENABLE=YES`, `--enable-index-store` (cross-confirmed by live captures)

### Secondary (MEDIUM confidence)
- None — offline session, no web claims made

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Signature strings: HIGH — captured verbatim from the pinned binary and re-verified end-to-end through the real `jarvis index` pipeline on this machine
- Flow/precedence: HIGH — read in source at the exact branch sites, plus an existing test pinning the preempt behavior
- Pitfalls: HIGH — each reproduced live (wrapper generality, setting override, exit-0 empty docs, no-scheme auto-detect)
- Wording stability beyond 0.3.0: LOW (A1) — unknowable offline; fails safe (hard) by locked design

**Research date:** 2026-08-23
**Valid until:** the next scip-swift release changes stderr wording (A1) — otherwise 30 days
