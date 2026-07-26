# `setup.sh`: Dependency Bootstrapper — Design

## Purpose

codeintel requires 7 external binaries on `PATH` (one SCIP indexer per language, `scip` for
SQLite conversion, `zoekt-index`/`zoekt-webserver` for search). Today, getting all of these
installed is manual, undocumented tribal knowledge scattered across READMEs of separate
projects. `setup.sh` is a single, curl-installable script that detects what's missing and
installs it, so one command —
`curl -fsSL https://raw.githubusercontent.com/phuongddx/codeintel/main/setup.sh | sh` —
is enough to go from a bare macOS or Linux machine to a fully-working `codeintel` install.

## Scope

**Platforms:** macOS + Linux only. Windows is explicitly out of scope for v1 — `scip` publishes
no Windows binaries (upstream issue open, unresolved) and `zoekt` publishes no binaries at all
on any platform, so Windows would require a Go toolchain with no shortcut; not worth the
complexity until there's real Windows demand.

**Dependencies covered (all 7):**

| Dependency | Install strategy |
|---|---|
| `scip` | Download prebuilt tarball from `scip-code/scip` GitHub releases (macOS arm64/amd64, Linux amd64/arm64), pinned to a specific tested version |
| `zoekt-index` / `zoekt-webserver` | Download from codeintel's own GitHub release assets — see "New CI: zoekt binaries" below |
| `scip-swift` | Download macOS-arm64 tarball from `phuongddx/scip-swift`'s v0.1.0 release. Any other OS/arch: print "not available" and skip (no build-from-source fallback in v1) |
| `scip-typescript` | `npm install -g @sourcegraph/scip-typescript` if `npm` present, else print manual instructions |
| `scip-python` | `npm install -g @sourcegraph/scip-python` if `npm` present, else print manual instructions |
| `scip-java` | **Detect-only.** Check for `docker` or a JVM on `PATH`. Explain what running `scip-java` would involve (Docker image `ghcr.io/scip-code/scip-java` or JVM launcher). Ask one y/n confirm (read from `/dev/tty`) before pulling/downloading. Decline, or neither present → print manual instructions and continue |

Rationale for including all 7 despite only 4 being in active use today: the user explicitly
requested full coverage rather than scoping down to current usage.

## Architecture

Single file: `setup.sh` at repo root.

**Strictly POSIX `sh`, not bash.** When invoked as `curl -fsSL <url> | sh`, the shebang line is
irrelevant — the interpreter is whatever `sh` is on that machine (bash-in-posix-mode on macOS,
frequently `dash` on Debian/Ubuntu). So: no arrays, no `[[ ]]`, no `$'...'`, no `local -a`, no
process substitution. Use `[ ]`, plain positional params, and newline-delimited strings where a
list is needed. This is a correctness requirement, not a style preference — a bashism here fails
only on Linux users' machines, which is exactly where it would go unnoticed during development
on macOS.

**Testability seam:** the script ends with a guard so tests can source it and call individual
functions without triggering a real install:

```sh
if [ "${CODEINTEL_SETUP_SOURCED:-}" != "1" ]; then
  main "$@"
fi
```

1. Parse flags: `--only <name>` (install one dependency only), `--force` (reinstall even if
   present and satisfies pinned version).
2. Detect `OS` (`uname -s`: Darwin/Linux) and `ARCH` (`uname -m`: arm64/x86_64) once. Fail fast
   with a clear message on Windows/unsupported OS.
3. Walk the 7 dependency-installer functions (or the `--only` subset). Each is isolated: a
   failure in one prints a clear error and the script continues to the next — one bad download
   must not abort the whole run.
4. All downloaded binaries land in a dedicated `~/.codeintel/bin`. If that directory isn't
   already on `PATH`, idempotently append an export line to the detected shell rc file
   (`~/.zshrc`/`~/.bashrc`, chosen from `$SHELL`).
5. Print a final summary table: installed / already-present-skipped /
   manual-action-needed / declined.

## New CI: zoekt binaries

The real upstream is **`github.com/sourcegraph/zoekt`** (confirmed via `go version -m` on the
working local binaries — *not* `google/zoekt`, which is the long-dormant original). It publishes
**zero GitHub releases and zero tags** — confirmed via `gh api repos/sourcegraph/zoekt/releases`
returning `0` and `.../tags` returning empty. There is no prebuilt binary to download from
upstream on any platform, ever. To give `setup.sh` a real zero-prerequisite path for zoekt,
codeintel itself will:

- Add a GitHub Actions workflow that cross-compiles `zoekt-index` and `zoekt-webserver` for
  macOS (arm64, amd64) and Linux (amd64, arm64). **Feasibility verified during design:**
  `GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build github.com/sourcegraph/zoekt/cmd/zoekt-index`
  from macOS arm64 produces a statically-linked Linux ELF binary. No cgo required.
- Pin the exact upstream commit SHA to build in a `ZOEKT_COMMIT` file at repo root. Current
  known-good value: `33f1f18af292` (pseudo-version `v0.0.0-20260709064101-33f1f18af292`, the
  build the working local binaries came from). The workflow only rebuilds/republishes when that
  file changes (manual PR bump) or on `workflow_dispatch` — no scheduled/automatic tracking of
  upstream `main`, to avoid inheriting upstream breakage without warning.

**Runtime caveat:** `zoekt-index` optionally shells out to `universal-ctags` for symbol
extraction (`-disable_ctags`/`-require_ctags` flags). A `CGO_ENABLED=0` static build still runs
without ctags present; search works, symbol-aware ranking is reduced. codeintel's
`index_cli.py` invokes `zoekt-index` without either flag, so ctags is best-effort. Not a
blocker; noted so the absence of ctags isn't later mistaken for a broken install.
- Publish the built binaries as assets on codeintel's own GitHub Releases.
- `setup.sh`'s zoekt installer downloads from codeintel's release assets, not upstream.

## Resolved: `scip` version pinning

`query.py` explicitly targets the **v0.7.0-era `scip expt-convert` SQLite schema**, and the
locally verified-working binary is exactly `scip v0.7.0` (confirmed via `scip --version` and
`go version -m ~/go/bin/scip`). Upstream is now at v0.9.0, raising the question of whether
fetching a newer version would silently break the query layer.

**Investigated and resolved during design:** the `expt-convert` SQLite schema is **byte-identical
between v0.7.0 and v0.9.0** — verified by diffing every `CREATE TABLE`/`CREATE INDEX`/`PRAGMA`
and column-definition line in `cmd/scip/convert.go` at both tags. All five tables
(`documents`, `chunks`, `global_symbols`, `mentions`, `defn_enclosing_ranges`) and all indices
match exactly.

**Decision:** pin to `v0.9.0` via a single `SCIP_VERSION` variable at the top of `setup.sh`
(never "latest", so an upstream release can't silently change what gets installed). Because
v0.9.0 has not yet been *executed* against codeintel — only its schema proven identical —
Task 10 empirically verifies it by running the real integration suite
(`uv run pytest -m integration`) against the newly installed v0.9.0. If that fails, the pin
drops to `v0.7.0`, the known-good version.

## Error handling

- Every installer function is wrapped so network failures, missing prerequisites, or download
  errors print a clear, dependency-scoped message and the script moves on to the next
  dependency rather than aborting.
- Exit code 0 when every requested dependency ends in a "satisfied" or "explicitly
  skipped-by-design" state (e.g. `scip-swift` on non-arm64, `scip-java` declined). Non-zero only
  on unexpected failures (bad download, network error) so CI or scripted callers can detect real
  problems.
- Interactive confirm (`scip-java` only) must read from `/dev/tty`, not stdin — because the
  script is normally invoked as `curl -fsSL <url> | sh`, where stdin is the piped script source,
  not the user's terminal. A prompt reading from stdin in that mode would silently get EOF/garbage
  instead of real input.

## Testing

- Manual verification on the machines actually available: macOS arm64 (primary), Linux via a
  container. No shell test framework (e.g. `bats`) planned for v1 — adding one is more
  infrastructure than a personal-tool install script currently warrants; revisit if the script
  grows complex enough that manual verification stops being reliable.
- The new CI workflow (zoekt cross-compile + publish) gets its own smoke test: after building,
  run the produced binary with `-version` (or equivalent) in CI to confirm it's a working
  executable before publishing as a release asset.

## Out of scope for v1

- Windows support (see Scope above).
- Auto-installing Docker or a JVM for `scip-java` — detect-and-report only.
- Building `scip-swift` from source as an Intel-Mac fallback — arm64-only for now, matching the
  upstream project's only published release asset.
- A `codeintel setup`/`doctor` CLI subcommand — this is a standalone shell script for now; folding
  it into the Python CLI is a separate future decision.
