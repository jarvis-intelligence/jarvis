# scip-java indexing: real launcher install + search-only fallback

Date: 2026-08-01
Status: design approved, pending spec review
Baseline: `ab561f7` (origin/main). None of the files this spec touches — `setup.sh`,
`index_cli.py`, `chunker.py`, `registry.py`, `server.py`, `tests/` — changed between the original
design baseline (`4c42165`) and this one; the four intervening merges were packaging/CI only.

## Problem

`jarvis index` fails on every Java/Kotlin repo with
`error: [Errno 2] No such file or directory: 'scip-java'`.

`setup.sh`'s `install_scip_java()` is detect-only: it probes for `docker`/`java` and at most
offers `docker pull ghcr.io/scip-code/scip-java:latest`. It never puts an executable named
`scip-java` on `PATH`. `index_cli.py` maps `.java`/`.kt` to `["scip-java", "index"]` and runs it
via `subprocess.run(shell=False)`, which does a literal `PATH` lookup. Java/Kotlin is presented as
a supported language (valid `--language`, picked by `detect_language`) but can never run.

## Evidence

All findings below are empirical, from runs against a real Android repo
(`android_theme_ui`: AGP 8.13.2, Kotlin 2.3.20, Gradle 8.13, 132 `.kt` / 4 `.java`)
and purpose-built Gradle fixtures.

| Claim in the original bug report | Verdict |
| --- | --- |
| No `scip-java` executable exists upstream | **False.** v0.13.1 ships `scip-java-v0.13.1` + `.sha256` — an 86MB `#!/usr/bin/env sh` coursier bootstrap with an embedded JAR. Runs on any JVM. |
| Docker is the intended path | **Reject.** Image entrypoint is `jshell` (`/usr/bin/scip-java` exists but isn't the entrypoint). Also loses `ANDROID_HOME`, Gradle caches, wrapper distribution. |
| Android SDK / network access will block indexing | **False.** Neither blocked it — the native host had both. |
| Android should work once an executable exists | **False.** Unsupported upstream. |

Verified outcomes after installing the launcher:

| Case | Result |
| --- | --- |
| Java, plain Gradle | `documents=1 chunks=1 global_symbols=8 mentions=10` |
| Kotlin **2.2.0**, plain Gradle | `documents=1 chunks=1 global_symbols=11 mentions=16` |
| Android (Java or Kotlin) | Build succeeds, **zero SCIP shards** |

### Kotlin compatibility is a single exact version

Same fixture, four Kotlin versions, control re-verified last (`rc=0`, 4045-byte index):

| Kotlin | Result |
| --- | --- |
| 2.1.21 | `AbstractMethodError` |
| **2.2.0** | works |
| 2.2.20 | `NoSuchMethodError: CheckerContext.getContainingFile()` |
| 2.3.20 | `AbstractMethodError` |

The window is **exactly 2.2.0** — not major.minor. Even a patch release inside the 2.2 line breaks.

Kotlin compiler plugins hook *internal* FIR APIs (`FirDeclarationChecker`, `CheckerContext`) that
JetBrains does not keep stable. `AbstractMethodError` means an abstract signature changed;
`NoSuchMethodError` means a method was removed. scip-kotlinc must be recompiled per Kotlin release,
and scip-java publishes one artifact per release — so exactly one Kotlin version works. The
industry fix is KSP's scheme (one artifact per Kotlin version, e.g. `2.2.0-1.0.29`); that is an
upstream change, not something jarvis can work around.

Java is unaffected: `scip-javac` uses javac's *stable* plugin API, and upstream targets the three
most recent Java LTS releases. Java is robust, Kotlin is brittle — they should not be described as
one capability.

Three distinct failure modes, only two of which jarvis can act on:

1. **Multi-module Gradle race (workaroundable).** With `org.gradle.parallel=true`,
   `:app:scipPrintDependencies` and `:theme:scipPrintDependencies` interleave and die with
   `java.util.ConcurrentModificationException`. `GRADLE_OPTS="-Dorg.gradle.parallel=false"`
   fixes it (`BUILD SUCCESSFUL in 37s`). Upstream bug; affects any parallel multi-module Gradle
   build, not just Android.
2. **Kotlin compiler-plugin ABI pin (not fixable).** `scip-kotlinc` is built against Kotlin `2.2.0`
   (`gradle/libs.versions.toml`). Any other Kotlin version crashes `compileKotlin` — see the exact
   -version table above. Fatal, not partial: a mixed Java+Kotlin repo indexes nothing, because the
   failing Kotlin compile fails the whole Gradle build.
3. **Android unsupported (not fixable here).** `scip-java`'s Gradle plugin keys off Gradle's
   standard `SourceSetContainer`; AGP replaces it with a variant model. The log says
   `SourceSet with name 'main' not found`, `scipCompileAll` is `UP-TO-DATE`, and
   `build/scip-targetroot/` holds only `.dependencies.txt` files. Upstream documents this:
   `docs/getting-started.md` lists Android as ❌, tracking issue
   [scip-java#177](https://github.com/scip-code/scip-java/issues/177).

`android_theme_ui` is blocked by **both** #3 and #2 (Kotlin 2.3.20 > scip-kotlinc's 2.2.0).

### Bonus finding: scip-java emits `relationships`

Decoded with jarvis's own `scip_pb2` against a fixture with a real hierarchy:

```
demo/Speaker#     -> [(demo/BaseGreeter#, is_implementation)]
demo/BaseGreeter# -> [(demo/Speaker#, is_implementation), (demo/Greeter#, is_implementation)]
symbols_with_relationships = 5
```

`typeHierarchy` is currently disabled because `scip expt-convert` declares
`global_symbols.relationships` but never writes it (scip#464, PR scip#465 open). This is the first
indexer where the data is proven to exist *upstream of the converter* — so #465 directly unlocks
`typeHierarchy` for Java/Kotlin. Out of scope here; recorded because it makes #465 concrete and
supplies a minimal verification fixture.

## Goals / non-goals

**Goals.** Java and Kotlin indexing works for standard JVM Gradle/Maven repos. Unsupported repos
fail fast with an explanation naming the real cause. Repos that cannot get SCIP navigation can
still get lexical + semantic search.

**Non-goals.** SCIP navigation for Android Gradle repos — impossible without upstream #177.
Indexing arbitrary non-source files (binaries, lockfiles, vendored blobs): the chunker keeps an
allowlist. Custom init-script + `scip-java aggregate` plumbing to bypass AGP (recorded as a future
option below).

## Design

### 1. `setup.sh` — install the real launcher

Pins beside the existing ones:

```sh
SCIP_JAVA_VERSION="v0.13.1"
SCIP_JAVA_REPO="scip-code/scip-java"
```

New `install_raw_binary <url> <sha_url> <dest_name>`: identical to `install_tarball_binary` minus
the `tar -xzf` step, reusing `download_to` / `verify_sha256` / `ensure_bin_dir`. A sibling rather
than a refactor of `install_tarball_binary` — the shared body is ~15 lines of error-path
boilerplate, and four callers depend on the existing helper.

`install_scip_java()` becomes a real installer:

- skip when `already_installed scip-java` and not `FORCE`
- when `java` is absent: warn and `return 0` (the launcher is inert without a JVM; mirrors the
  existing npm-missing branch)
- otherwise download the asset + `.sha256`, verify, install as `scip-java`

Install **unattended**, like `scip` / `zoekt` / `scip-swift`. The old `confirm()` prompt existed
because the docker image is 6.75GB; 86MB is not in that class, and `confirm()` returns false
without a TTY, which would make `curl | sh` silently skip scip-java.

No os/arch gating — unlike `scip-swift`, this is a JVM launcher and is platform-independent.

Delete `SCIP_JAVA_IMAGE` and every docker/`confirm` path, plus the stale "upstream ships no
standalone binary" comment.

### 2. `index_cli.py` — serialize Gradle

`_run` gains an `env: dict[str, str] | None` parameter, merged over a copy of `os.environ`.

For `language == "java"`, pass `GRADLE_OPTS` **appended, not replaced**:

```python
f"{os.environ.get('GRADLE_OPTS', '')} -Dorg.gradle.parallel=false".strip()
```

Clobbering would silently discard a user's heap settings. Applied at the same point
`_swift_indexer_cmd` is applied for Swift — one language-specific hook, same shape.

### 3. Signature-based fallback to search-only

Both unfixable failures — the Kotlin ABI crash and Android's empty shard set — are handled by one
mechanism instead of two bespoke pre-flight warnings.

Run the indexer normally. If it fails, match its combined stdout/stderr against a table of known
signatures:

| Signature | Cause |
| --- | --- |
| `AbstractMethodError` or `NoSuchMethodError` referencing `org.jetbrains.kotlin.fir` | scip-kotlinc ABI mismatch |
| `No SCIP shards found` | Android/AGP, or any build the plugin could not instrument |

On a match: publish **search-only** (§4) and persist that choice in the registry, so subsequent
`reindex` / `watch` runs skip the doomed build entirely. The first index pays one wasted build; no
run after it does.

On no match: raise `IndexingError` exactly as today. A transient Gradle break, a compile error, or
a missing binary still fails loudly.

**Why signatures rather than a pre-flight version check.** Kotlin versions hide in `buildSrc`,
`pluginManagement`, settings plugins, and convention plugins, so parsing them is unreliable — and a
wrong guess would deny navigation to a repo that would have indexed fine. Reacting to the actual
compiler error needs no heuristic, cannot produce a false positive, and folds Android and Kotlin
into a single code path.

**Guardrails.** Only listed signatures trigger the fallback — this is deliberately *not* a general
"fall back on any failure", which would mask real breakage as success. The resulting status is
always `search-only`, never `indexed`, so the registry never claims a navigation index it does not
have. Signature strings may drift when scip-java updates; the failure mode is safe, since an
unmatched signature simply hard-fails.

Deliberately **not** reusing `PARTIAL_STATUS`, which already means something different (symbols
published but zero chunks/mentions).

### 4. Search-only indexing

Repos that cannot get SCIP navigation currently get *nothing* from jarvis: `index_repo()` runs
the indexer first, and its `IndexingError` aborts before `zoekt-index` and the semantic stage ever
run. Yet Zoekt is language-agnostic and `chunker.py` already maps `.kt` → `kotlin`, so lexical and
semantic search would work fine on those files.

**Two triggers.** An explicit `--search-only` flag, persisted in the registry like `--language` /
`--scheme` so `reindex` / `watch` reuse it; and the automatic signature fallback in §3, which
persists the same registry field. Both converge on one code path.

The flag is not merely a convenience: it lets a user skip the doomed first build entirely when they
already know the repo cannot index.

**Pipeline.** When search-only: skip the indexer, `scip expt-convert`, graph population, and the
atomic publish entirely. Run `zoekt-index` and the semantic stage, then write the registry row.

Language detection becomes best-effort in this mode: `detect_language()` still runs (Android Kotlin
detects as `java` fine) and its result is recorded, but `UnsupportedLanguageError` is not fatal — a
repo with no SCIP-indexable language records a sentinel and proceeds. `registry.language` is
`NOT NULL`, so the sentinel is a value, not `NULL`. That is what lets a Go or Ruby repo be indexed
search-only.

**Status.** `SEARCH_ONLY_STATUS = "search-only"`. No schema migration — `status` is free text.

**Nav tools.** No `current` pointer is written, so `read_pointer` raises `IndexNotFoundError` and
every nav tool already fails safely through `server.py`'s per-tool `{"error": ...}` handler. The
only change is message quality: `server.py` consults the registry when `IndexNotFoundError` is
raised and, when the status is `search-only`, returns an explanation instead of a bare "index not
found" — mirroring the existing `typeHierarchy` unavailable-error precedent.

This lookup belongs in `server.py`, not `QueryService`: `QueryService.__init__` takes only an
`IndexConnectionCache`, and injecting a `Registry` would widen a deliberately SCIP-facing seam.
`server.py` is already the error-shaping boundary.

**`getIndexStatus`** gains the registry `status` so a search-only repo is legible without guessing.

`searchCode` and `semanticSearch` need no changes — the former scopes via Zoekt's `r:<repo>` filter,
the latter via the per-repo LanceDB table; neither touches the SCIP database.

### 5. Extend the chunker allowlist

Search-only is only worth much if `semanticSearch` covers the repo. The fallback machinery already
exists: `chunk_file()` drops to `_fixed_windows()` both when no tree-sitter parser is available
*and* when a parser has no `_DEF_NODE_TYPES` entry. Verified — `go`, `ruby`, and even a nonsense
`text` language all produce fixed-window chunks today (`symbol_name=None`).

The only gate is `iter_source_files()`, which filters on `path.suffix in LANGUAGES`.

So the change is to extend `LANGUAGES` with common source extensions — `.go`, `.rb`, `.rs`, `.c`,
`.h`, `.cpp`, `.cs`, `.php`, `.scala`, `.sh`, `.sql` — mapped to their tree-sitter names. Languages
with a `_DEF_NODE_TYPES` entry keep symbol-aware chunking; the rest get fixed windows automatically.
Adding `_DEF_NODE_TYPES` for one of them later upgrades it for free, with no restructuring.

Kept as an **allowlist**, not "everything except binaries". The allowlist is what currently keeps
images, lockfiles, and vendored blobs out of the chunker; replacing it would mean building binary
sniffing plus a denylist for little gain.

### 6. Docs

- `CLAUDE.md`: Android known-gap paragraph beside the existing scip#464 entry, citing #177; note the
  Kotlin ABI pin; document `--search-only`.
- `.claude/skills/jarvis-setup/SKILL.md`: `scip-java (detect-only)` becomes a real install.
- `CHANGELOG.md`: entry under `[Unreleased]`. The project adopted Keep a Changelog during the 0.2.x
  releases (after this spec's original baseline), and this change is user-facing on every count —
  Java/Kotlin indexing starts working, `--search-only` is a new flag, and the chunker covers more
  languages. Split `Added` (launcher install, `--search-only`, wider chunker allowlist) from `Fixed`
  (Java/Kotlin repos were un-indexable).

**Version-sync guard.** `.github/workflows/publish-mcp-registry.yml` fails the run when
`server.json`'s version drifts from `pyproject.toml`. This change bumps neither, so no action is
required — but the two must never be moved independently.

### 7. Upstream

File the `ConcurrentModificationException` against `scip-code/scip-java` with the repro — it hits
any multi-module Gradle repo with `org.gradle.parallel=true`, not only Android.

## Testing

**Unit** (`test_index_cli.py`): `_run` env merge; `GRADLE_OPTS` appends rather than clobbers; each
failure signature maps to search-only; an *unrecognized* failure still raises `IndexingError`;
the fallback persists to the registry so a second run skips the indexer; search-only skips the SCIP
stages and still runs Zoekt/semantic; search-only writes no pointer file; a repo with no
SCIP-indexable language records the sentinel instead of raising.

**`test_chunker.py`**: a newly allowlisted extension with no `_DEF_NODE_TYPES` entry yields
fixed-window chunks (`symbol_name is None`); an existing language still yields symbol-aware chunks.

**`test_setup_sh.py`**: replace the two docker-era tests
(`test_install_scip_java_reports_and_returns_zero_without_docker`,
`test_install_scip_java_never_pulls_without_confirmation`) with launcher-installed and
java-missing-warns cases.

**`test_server_tools.py`**: nav tool against a search-only repo returns the explanatory error;
`getIndexStatus` reports the status.

**Integration** (`@pytest.mark.integration`, gated on `scip-java`): the plain-JVM Gradle fixture
verified during design — `demo/Greeter` indexed end to end through `scip expt-convert`, asserting
non-zero chunks and mentions. A `gradle-wrapper.jar` (~43KB) is committed to `tests/fixtures/` so
the test is deterministic rather than skipped on machines without a Gradle CLI.

## Risks

- **Version pin drift.** scip-java v0.13.1 is pinned; a future release could change the asset name
  or CLI shape. Same exposure as the existing `scip` / `scip-swift` pins.
- **New compatibility axis.** scip-kotlinc's viability depends on the *indexed repo's* Kotlin
  version, not the host toolchain. Every existing pin is host-side; this is the first target-side
  one. Not eliminated — the signature fallback degrades it to search-only rather than a hard failure.
  Record the expected Kotlin version next to `SCIP_JAVA_VERSION` in `setup.sh` so the two are
  updated together.
- **Signature drift.** Failure strings could change when scip-java updates, silently disabling the
  fallback. Failure mode is safe (unmatched → hard fail, today's behavior), but the repo then looks
  broken rather than degraded. The integration test is the tripwire.
- **One wasted build per repo.** The first index of an un-indexable repo runs a full Gradle build
  before falling back. Persisting the decision bounds this to once; `--search-only` avoids it
  entirely when the user already knows.
- **Wider chunker allowlist.** More extensions means more files embedded, so larger LanceDB tables
  and longer semantic indexing. Existing guards (`MAX_FILE_BYTES`, `MAX_LINE_CHARS`, generated-banner
  detection, gitignore) still apply.

## Future option (not in scope)

`docs/manual-configuration.md` configures the plugin via `tasks.withType(JavaCompile)`, and AGP's
compile tasks *are* `JavaCompile` tasks. A jarvis-owned Gradle init script plus
`scip-java aggregate` would bypass the `SourceSetContainer` assumption that breaks Android — a path
upstream's `index` command cannot reach. Substantial work, and Kotlin-heavy Android would still hit
the ABI pin, so it is recorded rather than planned.

## Resolved during design

- *Kotlin version tolerance* — measured, not assumed: the window is exactly `2.2.0`; 2.2.20 already
  breaks. This retired the proposed pre-flight version check in favour of the §3 signature fallback,
  which needs no version parsing at all.
- *Search-only for languages with no SCIP indexer* — in scope. The chunker's fixed-window fallback
  already exists, so the cost is an allowlist entry (§5), not new machinery.

## Unresolved questions

- Sentinel value for `registry.language` when no SCIP-indexable language is found (`"none"`,
  `"unknown"`, `"-"`). Cosmetic, but it surfaces in `jarvis list` / `status`.
- Whether `getIndexStatus`'s existing `indexed` boolean should read `false` for a search-only repo
  (accurate about SCIP, but understates that search works) or `true` (overstates navigation). The
  added `status` field makes either legible; the boolean's meaning still needs a decision.
