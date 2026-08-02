# Maven Java indexing on macOS: bash shim for scip-java's javac wrapper

Date: 2026-08-02
Status: designed, not implemented

## Problem

Every Maven-built Java repo fails to index on macOS. `codeintel index` reports:

```
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.13.0:compile
  (default-compile): Fatal error compiling: Could not retrieve version from
  <tmp>/scip-java<N>/bin/javac. Exit code 1, Output:
  <tmp>/scip-java<N>/bin/javac: line 38: LAUNCHER_ARGS[@]: unbound variable
```

Three repos in the local registry sat at status `failed` for this reason
(`polaris-api`, `polaris-directory`, `polaris-knowledge` — all Maven, all plain JVM).
No entry in `_SEARCH_ONLY_SIGNATURES` matches, so the failure surfaces as a raw
Maven stack trace.

This is distinct from the Android/AGP limitation (scip-java#177) and from
issue #14 (Gradle `ConcurrentModificationException`, fixed in v0.3.0). Gradle is
unaffected — it does not route through the forked-javac wrapper.

## Root cause

scip-java generates a `javac` wrapper into a temp dir and points
maven-compiler-plugin at it via `-Dmaven.compiler.executable`. The wrapper ends:

```bash
#!/usr/bin/env bash
set -eu
...
LAUNCHER_ARGS=()
...
for arg in "$@"; do
  if [[ $arg == -J* ]]; then
    LAUNCHER_ARGS+=("$arg")
  fi
done
...
javac "${JAVAC_JVM_OPTIONS[@]}" "@$NEW_JAVAC_OPTS" "${LAUNCHER_ARGS[@]}"   # line 38
```

`LAUNCHER_ARGS` only ever collects `-J*` flags. maven-compiler-plugin's version
probe invokes the executable with no `-J` flags, leaving the array empty.

Under `set -u`, expanding an empty array is an error in bash < 4.4. macOS ships
bash 3.2.57 as `/bin/bash` and has no newer bash by default. Confirmed directly:

```
$ /bin/bash -c 'set -eu; A=(); echo "${A[@]}"'
/bin/bash: A[@]: unbound variable
```

Both `LAUNCHER_ARGS` and `JAVAC_JVM_OPTIONS` are exposed to this; either can be
empty.

## Key enabling fact

The wrapper's shebang is `#!/usr/bin/env bash` — it resolves bash through
`PATH`. Placing a bash >= 4.4 earlier in `PATH` fixes the failure outright, with
no patching of scip-java and no racing its temp directory.

## Verification (performed before writing this spec)

With a shim dir containing only a `bash` symlink to Homebrew bash 5.3.15,
prepended to `PATH`:

| Repo | Before | After |
|---|---|---|
| `polaris-directory` | hard failure at `default-compile` | exit 0, `BUILD SUCCESS`, 1.5 MB `.scip` |
| `polaris-api` | hard failure | exit 0, zero `LAUNCHER_ARGS` errors |
| `polaris-knowledge` | hard failure | exit 0, 103/103 SCIP shards aggregated |

Full pipeline on `polaris-directory` (convert -> graph -> zoekt -> publish)
succeeded; registry status moved `failed` -> `indexed`, and `documentSymbols`
returned real symbols with Maven coordinates:

```
scip-java maven maven/com.axonivy.polaris/polaris-directory 1.0.0-SNAPSHOT
  com/axonivy/polaris/directory/api/AgentResource#listAgents().
```

One environment change clears all three repos.

## Design

### 1. Shim creation (`setup.sh`)

Add a bash probe alongside the existing dependency checks.

- Resolve the `bash` that `#!/usr/bin/env bash` would pick, and read its version.
- If >= 4.4, do nothing. This is the normal Linux case; the shim must not exist
  where it is not needed.
- If < 4.4, look for a newer bash at `/opt/homebrew/bin/bash` and
  `/usr/local/bin/bash`. This mirrors `already_installed()`'s existing
  "present in our dir OR on PATH" shape rather than inventing a new discovery rule.
- On finding one, create `<shims>/bash` as a symlink to it.
- On finding none, emit an actionable message advising `brew install bash`. Do not
  invoke Homebrew from `setup.sh` — installing a shell is the user's decision, and
  `setup.sh` currently only downloads pinned release binaries into its own bin dir.

**Which env var keys the shim dir.** The shim is written by `setup.sh` and read by
`index_cli.py`, so both must resolve it identically. These two currently use
*different* redirect variables: `setup.sh::bin_dir()` honours `CODEINTEL_BIN_DIR`,
while `config.py::data_dir()` honours `CODEINTEL_DATA_DIR`. The shim dir must key
off **`CODEINTEL_DATA_DIR`** — the one the Python reader already uses — resolving to
`${CODEINTEL_DATA_DIR:-$HOME/.codeintel}/shims`. Reusing `CODEINTEL_BIN_DIR` would
let a redirected test write a shim the reader never finds.

The shim directory is therefore `~/.codeintel/shims/`, a sibling of
`~/.codeintel/bin/` rather than a subdirectory of it — `bin/` holds pinned binaries
`setup.sh` downloaded and owns, `shims/` holds symlinks to system tools it did not.

### 2. Shim application (`index_cli.py::_java_indexer_env`)

Extend the existing function — the same seam already used to work around
scip-java's Gradle parallelism bug — to prepend the shim dir to `PATH`:

```python
def _java_indexer_env() -> dict[str, str]:
    env = {"GRADLE_OPTS": f"{existing} -Dorg.gradle.parallel=false".strip()}
    shims = config.shim_dir()
    if (shims / "bash").exists():
        env["PATH"] = f"{shims}{os.pathsep}{os.environ.get('PATH', '')}"
    return env
```

`_run()` already merges the returned dict over a copy of `os.environ`, so the
prepend composes with the caller's PATH rather than replacing it.

**Why a dedicated shim dir and not `/opt/homebrew/bin`.** Prepending Homebrew's
`bin` wholesale would also shadow `java`, `mvn`, and `git` for the build — a far
larger blast radius than this bug warrants, and a source of hard-to-diagnose
version skew. A directory containing exactly one symlink changes exactly one
lookup. This is the configuration that was verified.

**Why conditional on the file existing.** No shim is created on Linux or on a
macOS box with a modern default bash, so the branch is inert there and the
existing behaviour is untouched.

### 3. Failure path (`index_cli.py`)

If the shim is absent or ineffective, the build still fails. Detect the signature
and raise an `IndexingError` naming the remedy:

> scip-java's generated javac wrapper requires bash >= 4.4, but this machine's
> default bash is older (macOS ships 3.2). Install a newer bash
> (`brew install bash`) and re-run `codeintel index`.

**This deliberately does not go through `_SEARCH_ONLY_SIGNATURES`.** That path
persists `search_only=1`, and `--search-only` is declared `action="store_true",
default=None` — it can be set but never cleared, so the only escape is
`codeintel forget` plus a full reindex. That is the right trade for Android/AGP,
which is permanently unindexable. It is a trap for this bug, which one
`brew install` fixes: a user who degraded to search-only, then installed bash,
would silently keep getting no navigation. A hard error with a fix in it leaves
the repo at `failed` and recoverable by a plain reindex.

### 4. Upstream

File against scip-java. The wrapper needs bash-3.2-safe expansion on line 38:

```bash
javac ${JAVAC_JVM_OPTIONS[@]+"${JAVAC_JVM_OPTIONS[@]}"} "@$NEW_JAVAC_OPTS" ${LAUNCHER_ARGS[@]+"${LAUNCHER_ARGS[@]}"}
```

Reference this spec's reproduction. Once fixed upstream and the pinned scip-java
version moves past it, the shim becomes dead weight and should be removed —
note that in the code comment so it is findable.

## Files

| File | Change |
|---|---|
| `setup.sh` | bash version probe; create `<shims>/bash` when needed; advise `brew install bash` when no modern bash exists |
| `src/codeintel/config.py` | `shim_dir(root=None)` returning `data_dir(root) / "shims"`, alongside the existing `lancedb_dir()` helper it mirrors |
| `src/codeintel/index_cli.py` | `_java_indexer_env()` prepends the shim dir; detect the `LAUNCHER_ARGS` failure in the existing `except IndexingError` branch and re-raise with the remedy — a check kept separate from `_SEARCH_ONLY_SIGNATURES`, not a new entry in it |
| `tests/test_setup_sh.py` | probe returns no-op on bash >= 4.4; creates symlink on old bash + newer available; advises install when none found |
| `tests/test_index_cli.py` | `_java_indexer_env()` prepends only when the shim exists; PATH composes with existing; the `LAUNCHER_ARGS` signature raises `IndexingError` and does **not** publish search-only |
| `docs/` | note the macOS bash requirement for Maven Java repos |

## Testing

Unit tests follow existing conventions — `test_setup_sh.py` sources single
functions under `dash` with `CODEINTEL_SETUP_SOURCED=1`; `test_index_cli.py`
mocks subprocess and asserts on the env dict.

Explicitly assert the negative: the `LAUNCHER_ARGS` failure must **not** reach
`_publish_search_only()` and must **not** set `search_only=1`. That is the whole
point of §3 and is the regression most likely to be introduced later by someone
tidying the failure branches together.

Integration coverage is the existing `@pytest.mark.integration` Java path; it
already skips cleanly when `scip-java` is absent.

## Risks

- **A user's own `bash` on PATH.** Prepending the shim overrides it for the
  indexer subprocess only. Acceptable: the wrapper needs >= 4.4 to run at all.
- **Symlink goes stale** if Homebrew bash is uninstalled. The failure path in §3
  catches it with the same actionable message.
- **Homebrew assumption.** The probe checks two well-known paths but never runs
  `brew`. A user with bash installed elsewhere can put it on `PATH` themselves; a
  user with none gets a message, not a silent failure.
- **Linux regression risk: none.** No shim is created, so the new branch is inert.

## Out of scope

- Android/AGP (scip-java#177) — a real upstream limitation, correctly handled by
  the existing search-only fallback.
- Adding `--no-search-only`. It would make persisted search-only escapable and is
  worth considering on its own merits, but §3 avoids needing it here.

## Open questions

None blocking. The one assumption that mattered — that a PATH-resolved modern
bash fixes the wrapper — was verified end-to-end on all three affected repos
before this spec was written.
