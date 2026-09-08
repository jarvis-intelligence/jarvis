---
last_mapped_commit: 7911fc568fbdc8c4736c068477cb47157fd5cfea
focus: concerns
---

# Codebase Concerns

**Analysis Date:** 2026-09-08

<!-- refreshed: 2026-09-08 -->

Sources: full-repo scan at HEAD `7911fc568fbdc8c4736c068477cb47157fd5cfea`, `.planning/v1.0-MILESTONE-AUDIT.md` (frontmatter `tech_debt`, ~20 recorded items), `.planning/milestones/v1.0-phases/*/REVIEW*.md`, `.planning/WINDOWS.md` (2 open entries), `docs/code-standards.md`, `docs/codebase-summary.md`. Notably, `grep -rn '\b(TODO|FIXME|XXX|HACK)\b' src/ tests/ scripts/` finds **zero** source TODO markers — the only byte matches are inside the serialized protobuf blob in `src/jarvis/scip_pb2.py`. Debt here is architectural and recorded in planning docs, not in inline comments.

## Tech Debt

**Source-level TODOs are absent by convention (documentation debt instead):**
- Issue: No TODO/FIXME/XXX/HACK markers exist anywhere in `src/jarvis/`, `tests/`, or `scripts/`. Known limitations live in module docstrings (e.g. `src/jarvis/scip_decoder.py` documents the empty-`relationships` gap) and `.planning/` review files. This keeps source clean but makes debt invisible to tooling that scans for markers — the `.planning/` audit trail is the only machine-readable register.
- Files: `src/jarvis/` (all modules)
- Impact: Debt discovery depends on GSD planning artifacts staying in sync with code; a stale audit under-reports real debt.
- Fix approach: Treat `.planning/v1.0-MILESTONE-AUDIT.md` frontmatter `tech_debt` as the canonical list and reconcile it at each milestone close (current practice).

**Stale documentation drift (docs/code-standards.md §9 vs reality):**
- Issue: `docs/code-standards.md` §9 "Version-Consistency Guard Pattern" describes **five version fields across four files** (PyPI package, MCP Registry descriptor "twice over", Claude plugin manifest, Codex plugin manifest). The actual guard `scripts/check_versions.py` asserts **three fields across two files**: `pyproject.toml [project] version`, `server.json version`, `server.json packages[0].version`. The plugin manifests were moved to the separate public repo `jarvis-intelligence/jarvis-index` and version independently (stated correctly in `docs/codebase-summary.md` and the script's own docstring). §9 also claims the script validates `plugin/.mcp.json`'s `--from` floor — no such code exists. Additionally the section numbering in `docs/code-standards.md` jumps §9 → §11 (no §10).
- Files: `docs/code-standards.md:183-197`, `scripts/check_versions.py`, `tests/test_check_versions.py`
- Impact: A planner following §9 would add plugin-manifest paths to `check_versions.py` and break the release gate against files that live in another repo.
- Fix approach: Rewrite §9 to match `scripts/check_versions.py`'s docstring (3 fields / 2 files; plugin manifests out of scope by design) and renumber §11/§12 or restore §10.

**README zoek naming staleness (recorded as Phase 3 review IN finding):**
- Issue: `README.md` still refers to `zoekt-index` (lines ~113, 141, 202, 245, 290, 370) where the pipeline has switched to `zoekt-git-index` (`src/jarvis/index_cli.py:559-584`), and describes shard naming via "`zoekt-index -meta`" (`README.md:290`) when pinning is actually done via `git config zoekt.name` (`src/jarvis/index_cli.py:1475-1494`).
- Files: `README.md`
- Impact: Users following the README to manually inspect shards or reason about gitignored-content exclusion get wrong commands/wrong model.
- Fix approach: Sweep README for `zoekt-index` → `zoekt-git-index` and replace the `-meta` explanation with the `zoekt.name` git-config pin.

**Milestone v1.0 deferred items (~20 Info-tier or user-accepted, none blocking):**
- Issue: Consolidated in `.planning/v1.0-MILESTONE-AUDIT.md` frontmatter `tech_debt`. Highest-signal items:
  - P1 WR-01 (user-accepted edge): Ctrl-C during a retry wipes the prior failure record; the row can strand at `indexing`.
  - P1 WR-02: pre-pipeline handler in `index_repo()` lacks try/finally around `_record_failure_best_effort` (connection leak if `record_failure` itself raises).
  - P2: `api.github.com` anonymous call in `setup.sh:636` can 403 rate-limit on shared CI runner IPs (hardening candidate: honor `GH_TOKEN` or retry on 403 — CI workflows already use `secrets.JARVIS_DIST_TOKEN` but `setup.sh` does not).
  - P2 IN-01: `mv`/`chmod` failure tail in installer helpers reports success (sibling-parity); IN-02: `.swiftpm` ignore swallows root-level `Package.resolved` edits.
  - P3: nine Info findings in `.planning/milestones/v1.0-phases/03-opt-in-self-healing-fallback/03-REVIEW.md` (IN-01..IN-09).
  - P4 IN-01: reason-prose drift risk ACCEPTED (no mechanical pin on reason strings).
  - P5 IN-01: consented `semantic` install is silent up to 600 s; Ctrl-C DURING install still tracebacks (the only remaining traceback path in the offer block); IN-02: `uv`-absent failure prints "tried: uv pip install …" which did not actually run.
- Files: `.planning/v1.0-MILESTONE-AUDIT.md`, `src/jarvis/index_cli.py:759-783` (`_install_semantic_extra`)
- Impact: None block function; each degrades diagnostics or leaks a resource in a narrow corner.
- Fix approach: Work the list top-down when a follow-up milestone opens; WR-02 is a two-line `try/finally`.

**Unreleased completed milestone work:**
- Issue: The v1.0 feature commit `707e3f8` (registry degradation reporting, scip-swift v0.3.0 toolchain, merged 2026-08-21+) postdates the newest `CHANGELOG.md` entry `[0.6.2] - 2026-08-08`. The registry schema/status surface in `src/jarvis/registry.py` (`DEGRADED_STATUS`, `status_origin/reason/stderr` columns) and the new Swift degrade signatures in `src/jarvis/index_cli.py:124-141` have never shipped in a PyPI release.
- Files: `CHANGELOG.md:3`, `pyproject.toml` (`version = "0.6.2"`), `src/jarvis/registry.py`
- Impact: Users on released wheels cannot produce/consume degraded-state rows; support burden until the next release cuts.
- Fix approach: Cut 0.7.0 following the `jarvis-release` pipeline (version lockstep below).

**No lint / type-check / formatter configuration committed:**
- Issue: No ruff/flake8/mypy/pyright/black config anywhere (checked root and `pyproject.toml` — only `[tool.pytest.ini_options]` exists). Type hints are convention-only (`docs/code-standards.md` "Type Hints" section).
- Files: `pyproject.toml`
- Impact: Drift in hint coverage and style is invisible; nothing enforces the documented `str | None` / `list[T]` conventions.
- Fix approach: Add ruff (lint+format) and a mypy/pyright pass over `src/jarvis/` to CI `test.yml`; start non-blocking.

## Known Bugs

**WR-01 accepted edge — Ctrl-C mid-run strands the registry row at `indexing`:**
- Symptoms: A repo row stays `status='indexing'` forever; `jarvis status <slug>` shows a phantom in-flight run.
- Files: `src/jarvis/index_cli.py:966-1316` (`index_repo` — the `upsert(..., "indexing", ...)` at the top is never rolled back because `KeyboardInterrupt` is a `BaseException` and every handler catches `Exception` only)
- Trigger: Ctrl-C (or SIGKILL) between the initial `indexing` upsert and the terminal `indexed`/`failed` write.
- Workaround: Phase 3 FALL-03 self-heal (`_watch_should_retry_full_build`, `src/jarvis/index_cli.py:387-406`) un-strands the row on the next source change; the wiped *cause* is not recovered (user decision, Phase 1 UAT).

**Cache double-open race leaks a connection:**
- Symptoms: Under two concurrent first-queries for the same repo, one `sqlite3.Connection` is opened, overwritten in `self._connections[key]`, and never closed.
- Files: `src/jarvis/index_reader.py:118-149` (`IndexConnectionCache.get_connection` — the lock is released between the cache-miss check and the `self._connections[key] = new_conn` write)
- Trigger: Simultaneous cold cache misses for the same `(project, repo, branch, pointer)` key from two threads.
- Workaround: None needed in practice (single-user tool, bounded by race frequency); each leaked handle is `immutable=1`/`mode=ro`.
- Fix approach: Re-check the key inside the second `with self._lock:` block and close `new_conn` if another thread won.

**Concurrent publish can orphan a versioned `.db`:**
- Symptoms: `index-<sha>.db` files accumulate in `~/.jarvis/scip/_/<slug>/_/` beyond one old generation.
- Files: `src/jarvis/index_cli.py:457-473` (`_publish_atomically` — each run reads `old_pointer`, swaps, then unlinks only *its* observed old file; two interleaved runs can each miss the other's just-published file)
- Trigger: `jarvis watch` reindex racing a manual `jarvis index` on the same slug.
- Workaround: Harmless disk growth only; the cache never resolves orphaned pointers.
- Fix approach: After the swap, prune all `index-*.db` except the new one and the immediately-previous generation, or take a per-slug lock around publish.

**SEAM-18 (warning, not fixed) — `semantic.reason` cannot distinguish "declined" from "not installed":**
- Symptoms: A user who DECLINED the semantic offer (`semantic_declined=1`) sees the same static reason "semantic index not built for this repo (requires the `semantic` extra)" as a user without the extra installed.
- Files: `src/jarvis/server.py:214-216` (inside `_capability_fields`)
- Trigger: `getIndexStatus` on any repo with `semantic_indexed_at IS NULL`.
- Workaround: Registry row carries the distinction; the MCP payload does not.

## Security Considerations

**Single-tenant, no-auth design (deliberate, documented):**
- Risk: Everything trusts the local user. The MCP stdio server has no authentication; the SQLite registry and LanceDB store have no access control; `repo_slug()` sanitization (`src/jarvis/config.py:71-88`) is the only input hardening at the boundary (explicitly rejects `.`/`..` path components).
- Files: `src/jarvis/server.py`, `src/jarvis/config.py`
- Current mitigation: Documented posture — stdio transport means the MCP client's own auth is the perimeter; `docs/code-standards.md` §11 records the single-tenant hardcoding (`PROJECT = "_"`, `BRANCH = "_"`) as intentional. Parameterized SQL everywhere (no string interpolation).
- Recommendations: Keep the stance but state it in `README.md`'s security note; never expose the zoekt HTTP listener beyond loopback (next item).

**Zoekt webserver on a fixed loopback port, unauthenticated, with PID-reuse trust:**
- Risk: `zoekt-webserver -rpc -listen :6070` (`src/jarvis/search.py:141,178-184`) has no auth and binds a fixed port. `_is_healthy()` (`src/jarvis/search.py:174-179`) accepts *any* HTTP response `< 500` as proof the webserver is ours: an unrelated local process that grabs port 6070 (or a recycled PID matching a stale pidfile) makes jarvis silently query/return garbage from it. Conversely, another user's or app's zoekt on 6070 is adopted via pidfile.
- Files: `src/jarvis/search.py:127-231` (`ZoektLifecycle`)
- Current mitigation: Loopback-only bind; pidfile reuse avoids duplicate spawns; `atexit` kills self-spawned processes only.
- Recommendations: Probe `/api/list` (a zoekt-specific endpoint) instead of bare `/` for health; make the port configurable via `JARVIS_ZOEKT_PORT` (6070 default).

**Subprocess surface is argv-list only — no shell injection:**
- Risk: Low. All `_run()` invocations are fixed argv lists (`src/jarvis/index_cli.py:428-455`); the consented semantic install is deliberately "a fixed argv list — never a shell string" (`src/jarvis/index_cli.py:765-770`).
- Files: `src/jarvis/index_cli.py`
- Current mitigation: As described; `MissingBinaryError` translation keeps PATH-lookup failures legible.
- Recommendations: None; preserve the invariant when adding steps.

**`status_stderr` isolation (verified good, keep it):**
- Risk: Indexer stderr persisted unbounded (D-02) can reach megabytes; leaking it into MCP payloads would bloat responses and could embed secrets from build output.
- Files: `src/jarvis/registry.py` (`status_stderr` column), `src/jarvis/server.py:146-152` (`_capability_fields` — builds payload key-by-key, "never `asdict(entry)`")
- Current mitigation: Both `_capability_fields` and `_error_payload` docstrings pin the exclusion; tests assert it.
- Recommendations: Keep the assertion in `tests/test_server_tools.py` when touching payload builders.

**`git config zoekt.name` written into the user's repo:**
- Risk: jarvis mutates the *user's* repo config (shared across linked worktrees — two worktrees of one repo race to set it; `_reject_duplicate_slug_for_path` prevents only the single-worktree case).
- Files: `src/jarvis/index_cli.py:1475-1494` (`_pin_zoekt_repo_name`), `src/jarvis/index_cli.py:907-939` (duplicate gate)
- Current mitigation: `jarvis forget` unpins best-effort (`_unpin_zoekt_repo_name`); the duplicate-slug gate blocks the common collision.
- Recommendations: None urgent; consider `zoekt-git-index`'s repo-name override if upstream grows one.

## Performance Bottlenecks

**Full re-embed on any `TableIdentity` change:**
- Problem: A model/revision/prefix/`CONTENT_FORMAT` change re-embeds every chunk of every repo (`index_semantic()` "always fully re-embeds"; `docs/code-standards.md` §12).
- Files: `src/jarvis/semantic.py:227-305`
- Cause: Correctness requirement — vector spaces are not comparable across identities; carry-forward keys on `file_hash` within one identity only.
- Improvement path: Accepted cost. If it bites, add an identity-pair migration path (dual-embed during idle) rather than weakening the identity contract.

**Embedding model cold start on first `semanticSearch`/index:**
- Problem: `BAAI/bge-m3` (1024-dim) lazy-loads on first use — multi-second to multi-minute on cold cache; consented installs are silent up to 600 s (P5 IN-01).
- Files: `src/jarvis/embeddings.py`, `src/jarvis/index_cli.py:759-783`
- Cause: Model load + torch download inside the request path.
- Improvement path: Warm the model in a background thread at server start when the extra is installed; emit progress during consented install.

**Single-threaded, synchronous query path (by design):**
- Problem: All MCP tools are sync; one slow `semanticSearch` blocks the stdio server's other tool calls.
- Files: `src/jarvis/server.py` (all `@mcp.tool` wrappers)
- Cause: Documented convention (`docs/code-standards.md` "Single-Threaded Query Path") — MCP clients own parallelism.
- Improvement path: None needed until multi-client contention is real.

**Registry.db is a single RW SQLite contended by index/watch/query:**
- Problem: Every tool call constructs `Registry(config.data_dir() / "registry.db")` (`src/jarvis/server.py:96-118`); a `jarvis watch` reindex holding a write transaction blocks status reads and can itself be the trigger for degrade paths ("locked registry.db from a concurrent watch reindex", `src/jarvis/index_cli.py:1240-1246`).
- Files: `src/jarvis/server.py`, `src/jarvis/registry.py`, `src/jarvis/index_cli.py`
- Cause: No WAL mode or busy_timeout configured on the registry connection.
- Improvement path: Enable `PRAGMA journal_mode=WAL` + `busy_timeout` in `Registry.__init__`; cheap and removes the whole class.

## Fragile Areas

**The degrade-state machine in `index_repo()` (highest-complexity zone in the codebase):**
- Files: `src/jarvis/index_cli.py:966-1316`
- Why fragile: Six registry statuses (`indexed` / `indexing` / `failed` / `partial` / `search-only` / `degraded`) × four origins (`ORIGIN_MANUAL` / `ORIGIN_SIGNATURE` / `ORIGIN_FALLBACK` / `ORIGIN_FAILED_HARD`, `src/jarvis/registry.py:33-36`) interact across: the pre-pipeline failure wrap, the explicit search-only branch, the signature-based fallback, the opt-in degrade gate with its `published` flag, the `SearchPublishedButIncomplete` partial-publish marker, and the bookkeeping-failure-after-degraded-publish recovery. Correctness depends on ordering invariants documented only in comments (e.g. graph teardown BEFORE rmtree in `_retire_scip_artifacts`, `src/jarvis/index_cli.py:826-830`; zoekt publish BEFORE retire in `_publish_search_only`).
- Safe modification: Read the three docstrings (`_publish_search_only`, `_retire_scip_artifacts`, the degrade-gate comment at `src/jarvis/index_cli.py:1240-1252`) before touching any except clause; every branch has a matching prohibition test — run `uv run pytest tests/test_index_cli.py tests/test_registry.py -m "not integration"` after any edit.
- Test coverage: 657 unit tests green at audit; prohibitions human-confirmed per phase. Coverage is strong; fragility is inherent combinatorics, not gaps.

**Failure-signature matching is wording-pinned to exact tool versions:**
- Files: `src/jarvis/index_cli.py:107-141` (`_SEARCH_ONLY_SIGNATURES` — Kotlin `AbstractMethodError`/`NoSuchMethodError` + `org.jetbrains.kotlin.fir`, AGP "No SCIP shards found", and the scip-swift 0.3.0 pair "captured 2026-08-23"), `src/jarvis/index_cli.py:171-174` (`_bash_shim_failure`)
- Why fragile: Signatures are path-free substrings of indexer stderr. On any scip-java/scip-swift version bump, wording drift fails HARD (unmatched → hard failure) — deliberate ("must never silently degrade") but means every toolchain bump can flip previously-degrading repos to loud failures until tokens are re-captured. P4's accepted reason-prose drift risk compounds this.
- Safe modification: Any token change must re-capture from the pinned tool version; add a signature-table row + test together.
- Test coverage: Good (signature tests in `tests/test_index_cli.py`), but they pin current wording, not future tool output.

**Version lockstep burden (3 checked + 2 unchecked surfaces):**
- Files: `pyproject.toml` (`version = "0.6.2"`), `server.json` (two `0.6.2` fields), `uv.lock:406-408` (embeds `jarvis-mcp` 0.6.2 for the editable project), plus plugin manifests in the separate `jarvis-index` repo (deliberately out of scope)
- Why fragile: Release requires editing pyproject + server.json ×2 (guarded by `scripts/check_versions.py` / `tests/test_check_versions.py` / CI) and regenerating `uv.lock` (`uv lock`). The guard catches the 3 declared fields; a forgotten `uv lock` regen surfaces only at install time.
- Safe modification: Always run `uv run pytest tests/test_check_versions.py` after touching any version field; follow the `jarvis-release` skill pipeline.

**Cython compile gate (`JARVIS_COMPILE=1`) and the source-stripping wheel:**
- Files: `setup.py` (whole file — `StripCompiledSources.build_py` removes every `.py` except `__init__.py`/`scip_pb2.py` from wheels), `pyproject.toml` `[tool.cibuildwheel]`, `tests/conftest.py:4-23` (`BlockImportFinder` exists precisely because `sys.modules[name] = None` does NOT simulate missing modules under Cython's compiled `import` fast path)
- Why fragile: Compiled modules can diverge semantically from the `.py` source (frozen dataclasses, `str | None` unions); the empirical gate is running the unit suite against the installed wheel in cibuildwheel — with the `semantic` extra NOT installed there, so all `pytest.importorskip("lancedb")`-gated tests skip ("Full-extras compiled-wheel coverage is not yet automated", `pyproject.toml` comment). Dev machines never compile, so drift is caught only at release CI.
- Safe modification: Avoid module-level import side effects and metaprogramming; keep new test fakes on `BlockImportFinder`, never `sys.modules[...]=None`.
- Test coverage: Gap — no automated full-extras compiled-wheel run (see Test Coverage Gaps).

**Setup.sh is strict-POSIX sh parsed by sed/grep/awk, tested under dash:**
- Files: `setup.sh` (1020 lines), `tests/test_setup_sh.py:19-47`
- Why fragile: The `curl | sh` contract forbids bashisms; GitHub-API JSON is scraped with line-oriented `sed`/`grep`/`awk` (`setup.sh:647-664`) — an API response format change (compact JSON, key reorder) breaks scip-swift resolution silently. Tests hard-require `dash` (`POSIX_SH = shutil.which("dash") or "sh"` + an asserting first test) because macOS `/bin/sh` accepts bashisms — so contributors without `brew install dash` fail the file outright, and cibuildwheel excludes the file for exactly that reason (`pyproject.toml` test-command comment).
- Safe modification: Keep functions isolated (the test file sources and unit-tests each); change JSON scraping only with a fixture update in `tests/test_setup_sh.py`.

**Pinned-fork / exact-version toolchain matrix:**
- Files: `SCIP_COMMIT` (`56791658a873`), `ZOEKT_COMMIT` (`33f1f18af292`), `setup.sh:23-33` (scip fork rationale), `setup.sh:70-79` (`SCIP_JAVA_VERSION="v0.13.1"`, `SCIP_JAVA_KOTLIN="2.2.0"` EXACT — 2.1.21/2.2.20/2.3.20 all fail), `setup.sh:620-627` (scip-swift darwin/arm64-only gate), `src/jarvis/index_cli.py:78-85` (`MIN_SCIP_VERSION=(0,9,0)`, `MIN_SCIP_SWIFT_VERSION=(0,3,0)`)
- Why fragile: (a) `scip` comes from the personal `phuongddx/scip` fork at a pinned commit because upstream through v0.9.0 never populates `global_symbols.relationships` (scip-code/scip#464) — `typeHierarchy` is empty on any non-fork binary, and the exit ramp ("when upstream merges #465 … delete build-scip.yml + SCIP_COMMIT") is a manual TODO-shaped comment; (b) scip-kotlinc's compiler-plugin API is internal/unstable — Kotlin must be exactly 2.2.0; (c) scip-swift auto-rolls to LATEST at install time (only floored ≥ 0.3.0), so an upstream regression ships to users without a jarvis release; scip-swift is macOS-arm64-only (Intel Macs and Linux get no Swift indexing — skipped, not failed); bare `scip-swift --version` invocation (`src/jarvis/index_cli.py:496-503`) itself assumes the ≥0.3.0 CLI shape; (d) zoekt upstream publishes no binaries at all — jarvis CI cross-compiles and mirrors to the public `jarvis-index` repo because this repo is private (private-repo release assets 404 anonymously).
- Safe modification: Bumping any pin requires updating the sibling pin (`SCIP_COMMIT` ↔ `setup.sh:SCIP_COMMIT_PIN`, asserted by `tests/test_setup_sh.py`) and re-capturing degrade signatures.
- Test coverage: Pin-parity is tested; upstream-behavior drift is not (untestable without network).

**Connection-cache invalidation by pointer content (immutable readers vs atomic publish):**
- Files: `src/jarvis/index_reader.py:17-33` (design comment), `src/jarvis/index_cli.py:457-473` (`_publish_atomically`)
- Why fragile: Correctness rests on two invariants that are convention, not enforcement: versioned `index-<sha>.db` files are NEVER mutated in place (only the `current` pointer flips via `os.replace`), which is what makes `mode=ro&immutable=1` safe (`src/jarvis/index_reader.py:139-143`); and the cache keys on pointer CONTENT, not mtime, so a stale entry is simply never looked up again and is evicted lazily by the 64-entry bound. Any future code that writes into a versioned `.db` (or reuses a filename for different content) silently corrupts every cached immutable reader. The post-swap `unlink` of the old file is safe only because POSIX keeps open fds alive.
- Safe modification: Never write to `index-*.db` after publish; new commits get new filenames, always.
- Test coverage: `tests/test_index_reader.py` covers cache behavior; the never-mutate invariant is documented in `docs/code-standards.md` §3.

**Git-vs-working-tree asymmetry across the three search signals:**
- Files: `src/jarvis/index_cli.py:559-584` (`_zoekt_index_cmd` — zoekt reads git blobs at HEAD, so "search reflects HEAD, while SCIP navigation reflects the working tree; uncommitted edits are searchable only after a commit"; the semantic stage reads working-tree files via `src/jarvis/chunker.py`), `src/jarvis/server.py:86-95` (`getIndexStatus` docstring — `searchCoverage` reflects git HEAD at last index time, not the working tree)
- Why fragile: `semanticSearch` fuses three signals (vector + zoekt + SCIP symbols via `reciprocal_rank_fusion`, `src/jarvis/semantic.py`) that can each describe a *different* snapshot of the repo after local edits. Staleness reporting partially covers this (`freshness.stale` vs `repo_path` HEAD), but uncommitted-change divergence between signals is silent.
- Safe modification: Don't "fix" by switching zoekt to filesystem indexing — the git-blob approach is load-bearing (gitignored exclusion by construction; `-incremental=false` repair semantics).
- Test coverage: Behavioral asymmetry is tested in integration tests; the cross-signal staleness story is not.

**MCP boundary swallows all exceptions into `{"error": str(exc)}`:**
- Files: `src/jarvis/server.py:278-508` (every `@mcp.tool` wrapper catches `Exception` — "Broad on purpose"), `src/jarvis/server.py:96-152` (`_search_coverage_fields` / `_capability_fields` additionally never-raise)
- Why fragile: A `TypeError` from a code bug is indistinguishable from an expected `IndexNotFoundError` — the server keeps running (intended) but nothing logs the traceback anywhere, and `str(exc)` can be empty for message-less exceptions, yielding `{"error": ""}`. Debugging production issues reduces to re-running locally.
- Safe modification: Keep the uniform payload (documented contract, `docs/code-standards.md` §6) but add a `traceback.print_exc(file=sys.stderr)` before returning — stderr is invisible to the MCP client but visible in debug logs.
- Test coverage: Error-shape tests exist in `tests/test_server_tools.py`; no logging assertions (there is no logging).

**`jarvis watch` depends on broad catches staying broad:**
- Files: `src/jarvis/index_cli.py:1571-1615` (`_reindex` and the poll loop each catch `Exception` — "anything short of catching Exception here would … kill the whole watch process")
- Why fragile: Two stacked defenses whose rationale lives in comments; narrowing either breaks watch resilience silently (the loop stops watching with "no obvious signal beyond a scrollback line").
- Safe modification: Leave the catches; add tests if touching the loop body.

## Scaling Limits

**Single machine, single user, single zoekt instance:**
- Current capacity: One `~/.jarvis` data dir, one zoekt-webserver on fixed port 6070, one registry.db. Dozens of repos / low GBs of shards work; 64-connection cache bound is generous for that scale.
- Limit: No multi-host story; two jarvis installs on one machine share port 6070 (first webserver wins, second adopts it via pidfile regardless of whose index dir it serves — see Security). `blastRadius` only spans repos indexed into the same registry.
- Scaling path: Out of scope by charter ("local-first"). If ever needed: configurable port + per-datadir pidfile first.

**Registry grows unbounded failure text:**
- Current capacity: `status_stderr` persisted "verbatim and unbounded (D-02) — truncation is display-only" (`src/jarvis/index_cli.py:1232-1235`).
- Limit: Megabyte-scale rows per failed repo (the code itself warns `status_stderr` "can be megabytes", `src/jarvis/server.py:151-152`).
- Scaling path: Cap persisted stderr at a few hundred KB with a marker; keep display truncation as is.

## Dependencies at Risk

**`phuongddx/scip` fork at pinned commit (replaces upstream scip-code/scip):**
- Risk: Core `scip expt-convert` binary is a personal fork build. Upstream drift, fork abandonment, or GitHub asset loss breaks all new installs; `typeHierarchy` silently degrades to the "unpatched scip" error on any non-fork binary.
- Impact: Every indexing pipeline; the relationships fix is the difference between working and erroring `typeHierarchy` (`src/jarvis/server.py:341-357`).
- Migration plan: Documented exit ramp in `setup.sh:14-22`: when upstream merges scip#465 and cuts a release, repoint at scip-code/scip and delete `build-scip.yml` + `SCIP_COMMIT`.

**`mcp[cli]>=1.2.0,<2.0.0` hard cap:**
- Risk: mcp 2.0.0 removed `mcp.server.fastmcp`, which `src/jarvis/server.py` imports; the cap is load-bearing (comment in `pyproject.toml`).
- Impact: Server cannot start on a future resolve outside the cap; conversely jarvis is frozen out of FastMCP 2.x features/fixes until ported.
- Migration plan: Track FastMCP 2.x API and port `server.py` in a dedicated phase; then relax the cap.

**`protobuf>=7.35.1,<8.0.0` runtime/gencode coupling:**
- Risk: Runtime must stay ≥ the gencode version vendored in `src/jarvis/scip_pb2.py` (generated against libprotoc 35.1); gencode calls `ValidateProtobufRuntimeVersion` and refuses older runtimes.
- Impact: A generated-file refresh without a dependency bump (or vice versa) breaks every install at import time.
- Migration plan: Regenerate `scip_pb2.py` and bump the floor together, always; note it in the same commit.

**scip-java pinned to Kotlin 2.2.0 EXACT:**
- Risk: `SCIP_JAVA_KOTLIN="2.2.0"` (`setup.sh:74-79`): 2.1.21 → `AbstractMethodError`, even 2.2.20 → `NoSuchMethodError`. Any repo built with a different Kotlin fails SCIP indexing (degrades to search-only via signature, so not data loss).
- Impact: Kotlin repos off 2.2.0 are search-only by design until scip-java bumps.
- Migration plan: On each scip-java release, re-check the targeted Kotlin version (comment says exactly this) and update the pair `SCIP_JAVA_VERSION`/`SCIP_JAVA_KOTLIN` + signatures.

**scip-swift auto-rolls to latest (floor 0.3.0), macOS-arm64-only:**
- Risk: Installs track upstream latest at install time — an upstream release that regresses dispatch (as v0.2.0/v0.2.1 did, ignoring `--build-tool xcodebuild`) reaches users with only the semantic-version floor as protection. No Intel-mac/Linux Swift story (skipped with a log line, `setup.sh:620-627`).
- Impact: Swift indexing availability + correctness on non-arm64-macOS; upstream regressions become user-facing flakes.
- Migration plan: If upstream proves unstable, switch to an exact-tag pin like scip/zoekt (the mechanism exists: `installed_scip_matches_pin` pattern).

## Missing Critical Features

**Windows support:**
- Problem: `setup.sh` supports darwin/linux only (`setup.sh:117-123`); cibuildwheel skips musllinux and Windows; `setup.sh` is the only bootstrap path.
- Blocks: All Windows users. Explicitly out of scope (`pyproject.toml` cibuildwheel comment).

**Lint/type-check/coverage automation:**
- Problem: No ruff/mypy/coverage configuration or CI step (see Tech Debt).
- Blocks: Enforced style/typing invariants; coverage measurement (Nyquist VALIDATION files also remain `draft` — never reconciled by `/gsd-validate-phase`, recorded as coverage TODOs in `.planning/v1.0-MILESTONE-AUDIT.md`).

**Distinguishing declined vs not-installed semantic (SEAM-18):**
- Problem: `capabilities.semantic.reason` is static for both populations (`src/jarvis/server.py:214-216`).
- Blocks: Onboarding UX polish only (audit verdict: warning, no REQ mandates it).

**Telemetry-free failure diagnostics:**
- Problem: The MCP boundary discards tracebacks (see Fragile Areas); there is no log file at all — `~/.jarvis` holds indexes, not logs.
- Blocks: Post-hoc debugging of user-reported tool errors.

## Test Coverage Gaps

**Compiled-wheel × semantic-extra matrix:**
- What's not tested: Cython-compiled modules exercised with `lancedb`/`sentence_transformers`/`tree-sitter` actually installed — cibuildwheel's test phase skips every `pytest.importorskip`-gated test (stated in `pyproject.toml` `[tool.cibuildwheel]` comments).
- Files: `pyproject.toml`, `setup.py`, `tests/test_semantic.py`, `tests/test_chunker.py`, `tests/test_embeddings.py`
- Risk: A Cython semantic divergence (e.g. in `semantic.py`/`chunker.py` compiled form) ships undetected.
- Priority: Medium (release-quality gate; bites exactly when shipping semantic fixes).

**Integration tests silently skip without binaries:**
- What's not tested: `tests/test_index_cli.py` integration markers skip via `shutil.which` when scip-python/scip/zoekt are absent — a dev machine without `setup.sh` runs "green" while the pipeline is unexercised.
- Files: `tests/test_index_cli.py`
- Risk: Local green ≠ pipeline works; acceptable (CI smoke covers `setup.sh` on ubuntu/macos via `.github/workflows/setup-smoke.yml`).
- Priority: Low.

**`dash` absence fails `test_setup_sh.py` wholesale:**
- What's not tested: On hosts without dash the file errors at the guard test (`tests/test_setup_sh.py:40-47`) rather than skipping — macOS contributors must `brew install dash` (macOS `/bin/sh` accepts bashisms, giving false confidence).
- Files: `tests/test_setup_sh.py:19-47`
- Risk: Contributor friction only; the guard is intentional ("Guard the guard").
- Priority: Low (documented behavior).

**Two open unrun-verify windows from Phase 2:**
- What's not tested: Post-push CI proof of scip-swift resolution+floor+digest in `setup-smoke.yml`, and the first real macOS-leg Swift index smoke run — both `open` in `.planning/WINDOWS.md` (recorded 2026-08-21; the audit's phase table notes "CI run green (PR #39)" for Phase 2 overall, but the ledger rows were never marked fixed).
- Files: `.github/workflows/setup-smoke.yml`, `.planning/WINDOWS.md`
- Risk: The macOS-leg Swift smoke path in CI has no proven green run recorded in the ledger.
- Priority: Low-Medium (verify the workflow's history, then mark the windows fixed).

**Nyquist VALIDATION.md files all draft:**
- What's not tested: Sampling-coverage reconciliation for all 5 phases (never run through `/gsd-validate-phase`).
- Files: `.planning/milestones/v1.0-phases/*/VALIDATION.md`
- Risk: Coverage TODOs, not compliance failures (audit's own wording).
- Priority: Low.

---

*Concerns audit: 2026-09-08*
