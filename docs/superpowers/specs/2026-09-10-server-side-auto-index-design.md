# Server-Side Auto-Index — Design

**Date:** 2026-09-10
**Status:** Awaiting review
**Origin:** `docs/kilo-vs-jarvis-indexing.md` §4.2 ("Zero-setup"), the one Kilo
advantage that report never entered into its cross-pollination table.

---

## 1. Problem

An AI agent pointed at an unindexed repo receives an error only a *human* can
act on.

`server.py`'s tools all take `repo: str` — a slug. When no index is published,
`index_reader.read_pointer` raises `IndexNotFoundError`, and
`_error_payload` (`server.py:319-334`) renders prose telling the reader to run
`jarvis index <path>`. The agent cannot: it has no tool that indexes, and the
server has no way to learn the path.

The reverse mapping is the specific blocker. Slug derivation is one-way —
`slug = config.repo_slug(slug or repo_path.name)` (`index_cli.py:1088`) — so
path → slug is deterministic, but **slug → path requires a registry row, which
a never-indexed repo does not have.** `server.py:600` is a bare `mcp.run()`
with no cwd handling, no env config, and no configured roots.

### Why now

The tree-sitter syntax baseline (spec TSI-*) landed and removed the
*language*-toolchain blocker. A baseline index needs no npm, JDK, Xcode, or
`scip`: 17 tree-sitter abi3 grammar wheels are **base** dependencies
(`pyproject.toml:68-84`), and SCIP is demoted to optional enrichment
(`scip_enabled` / `scip_state`, capability-gated via
`CapabilityUnavailableError`).

`index_cli.py:1496` already anticipates this work, listing "reindex/watch/**MCP**"
as callers that must bypass the interactive semantic-install offer.

### Scope correction: the bootstrap is *not* toolchain-free

`index_cli.py:1247` is explicit — **"Stage 4: Zoekt (required; failure fails the
run)."** `zoekt-git-index` remains a hard prerequisite for any publish.

The residual tax is therefore **exactly one vendored binary** (`setup.sh --only
zoekt`) rather than per-language toolchains plus manual indexing. That is a
large win, not a zero. Any claim of a binary-free first index is false.

---

## 2. Source-report corrections

`docs/kilo-vs-jarvis-indexing.md` is partly stale. Recorded here because its
cross-pollination table was the input to this design.

**§5 is wrong.** It claims "nothing in `index_cli.py`, `query.py`, or
`server.py` imports `syntax_index`." All three do: `index_cli.py:47-52`,
`query.py` at eight call sites, `server.py:18`. The tree-sitter baseline
landed.

That invalidates two of its four "Jarvis ← Kilo" recommendations:

| Report recommendation | Verified state |
|---|---|
| Per-file incrementality | **Mostly landed.** `build_syntax_index` reuses rows by `(hash, grammar_identity)` from the previous snapshot; semantic carries hashes forward. Residual: Zoekt's shard rebuild (fast, git-blob scan) and SCIP (whole-repo by nature — an external subprocess). |
| Bounded auto-recovery | **Weakest remaining option.** TSI made the baseline always publish, and `_scip_suppressed` (`index_cli.py:346`) is a *smarter* anti-treadmill than blind retries: it skips a known-failing SCIP attempt at an unchanged HEAD. Kilo's `3 × 500ms·2ⁿ` addresses transient failures; jarvis's are deterministic toolchain absences. Retrying a missing JDK three times helps nobody. |
| ANN index | **Real.** `semantic.py:252` is brute-force exact cosine; zero `create_index` calls repo-wide. |
| Branch awareness | **Real, expensive.** `config.py:22-23` pins `PROJECT = BRANCH = "_"`, load-bearing in `index_dir()`, the connection-cache 3-tuple key, the LanceDB table name, the Zoekt `zoekt.name` slug, and the registry PK. |

---

## 3. Roadmap and sequencing

Four independent sub-projects, each with its own spec → plan → implementation
cycle. This document specifies **#1 only**.

| # | Sub-project | Touches |
|---|---|---|
| 1 | **Server-side auto-index** (this spec) | `server.py`, `index_cli.py`, `config.py` |
| 2 | ANN vector index | `semantic.py` only |
| 3 | Residual incrementality | `index_cli.py` pipeline (Zoekt + SCIP stages) |
| 4 | Branch/worktree awareness | `config.py` pin and every consumer of it |

**Order: 1 → 2 → 3 → 4.**

#4 goes last because unpinning `BRANCH` changes the coordinate space the other
three key off — auto-index gains "which branch," the LanceDB table name gains a
dimension, incremental reuse's previous-snapshot lookup gains one. Built first,
1–3 would target a coordinate system that does not exist; built last, it is a
mechanical re-key of three settled features. It is also the highest-risk item
and should inherit the most settled ground.

**#1 and #3 decouple only because #1 is scoped to bootstrap.** A server that
also refreshed *stale* indexes would need per-file incrementality first,
flipping the order to 3 → 1. Staleness stays with the existing freshness
reporting.

---

## 4. Design decisions

| ID | Decision | Rejected alternatives |
|---|---|---|
| AI-01 | **Agent-actionable error + `indexRepo` tool.** The agent knows its workspace path and self-heals in one round trip. | Server cwd inference (client convention, not a guarantee; wrong silently). `repo_path` on all 8 tools (widens 8 signatures, every tool a potential writer). Configured roots (reintroduces the setup tax this removes). |
| AI-02 | **Subprocess, non-blocking + poll.** Spawn `jarvis index`, return immediately, poll `getIndexStatus`. Preserves the writer/reader process split; survives client timeouts; an indexing crash cannot kill the stdio server. | Blocking subprocess (exceeds MCP client timeouts on large repos). In-process (makes the server a writer holding registry locks and occupying stdio). Background thread (writer + thread-safety obligations on the connection cache). |
| AI-03 | **Baseline + SCIP; semantic opt-in.** | Full default pipeline — one agent tool call would trigger a `BAAI/bge-m3` load (~2 GB first use) plus full-corpus embedding with no user consent. Baseline-only (agent silently gets a weaker index than the CLI). Caller-decides (pushes a policy choice onto an agent with no basis for it). |
| AI-04 | **`flock`-based per-slug exclusion.** OS-backed, kernel-released on death. | Pid metadata alone (see §7 — `ZoektLifecycle`'s pidfile is not its arbiter). Timestamp staleness (threshold is a guess). Server-held `Popen` (blind to CLI runs, lost on restart). No tracking (poll loop has no terminating condition). |

---

## 5. Tool surface

```python
@mcp.tool(name="indexRepo")
def index_repo_tool(path: str, semantic: bool = False,
                    scip: bool | None = None) -> dict[str, Any]:
```

`path`, not `repo` — the only tool that takes a filesystem path, because that
is precisely what the agent knows and the registry lacks. It returns the slug
every other tool will accept.

**Returns on spawn:**

```json
{"repo": "<slug>", "path": "<resolved>", "status": "indexing",
 "state": "starting", "pid": 12345, "log": "~/.jarvis/index-<slug>.log"}
```

**When a job is already live:** the same shape plus `"alreadyRunning": true` —
adopt, never duplicate.

**Pre-flight errors**, all returned as `{"error": ...}` before anything is
spawned (the MCP boundary never raises):

| Condition | Payload |
|---|---|
| `zoekt-git-index` absent from PATH | `error` + `recovery: "sh setup.sh --only zoekt"` |
| Not a git working tree | `NotAGitRepositoryError` text |
| Path yields no safe slug | `ValueError` text |
| Slug bound to a different, still-existing path | `error` naming both paths + recovery |
| Neither `jarvis` console script nor PATH entry resolves | `error` naming the resolution failure |

`semantic: bool = False` and `scip: bool | None = None` map to child flags;
`scip=None` leaves the persisted tri-state choice untouched.

---

## 6. Slug resolution

`_reject_duplicate_slug_for_path` (`index_cli.py:975-1006`) enforces **one slug
per path** — it `continue`s when `existing.slug == slug` (line 994). The
inverse direction is **unguarded**: `/a/app` and `/b/app` both derive slug
`app`, and the second silently overwrites the first's published index.

Pre-existing, but auto-index makes it far likelier to fire, since the agent
supplies paths and never chooses slugs.

```python
def resolve_slug_for_path(registry: Registry, repo_path: Path) -> str:
    """1. Already registered at this resolved path -> reuse that slug. Honors a
          custom --slug, and makes indexRepo idempotent (a re-index, not a
          collision).
       2. Else derive config.repo_slug(repo_path.name).
       3. If that slug is bound to a DIFFERENT path that still exists -> reject.
    """
```

Step 3's **"still exists"** qualifier is load-bearing: a *moved* repo must keep
working (`registry.upsert` already does `path=excluded.path`), so rejection
fires only when both paths resolve. This mirrors the existing guard's own
`except OSError: continue` reasoning (`index_cli.py:998-1000`).

Enforced **inside the locked writer** in `index_repo`, not only in the MCP
tool — otherwise it is pure TOCTOU. The tool runs the same resolution as a
pre-flight solely to return a fast, actionable error instead of a log line.

---

## 7. Exclusion and liveness

### Why pid metadata is insufficient

`ZoektLifecycle`'s pidfile is a **discovery cache, not an arbiter**. Its
sibling-adoption path works only because exclusive TCP binding kills the
loser — `search.py:298`: *"its server holding the port is exactly what kills
our child."* Index workers bind nothing, so two callers can both read no
pidfile and both proceed to write.

### Design

A single file, `~/.jarvis/index-<slug>.lock`, whose contents are the holder's
pid.

Every writer — `jarvis index`, `reindex`, `watch`'s debounced reindex, and
MCP-spawned children alike — acquires `fcntl.flock(fd, LOCK_EX | LOCK_NB)` in
`index_repo`, **before** the duplicate guards and before the transitional
`upsert` (`index_cli.py:1167`), releasing at the end.

The loser exits rc 1 with `another index is already running for <slug>`,
touching neither registry nor artifacts.

The kernel releases a flock on process death, so **there is no stale-lock state
and no reclamation race** in steady state. Liveness is the lock probe itself:

- acquired → nothing is running (release immediately); file contents are stale
- `BlockingIOError` → a job is live; contents name the pid for reporting

### The startup window

`Popen` returns **before** the child has acquired the lock or written its
transitional `"indexing"` row. During that window — and permanently if the
child dies before registering at all (bad argv, an import failure, or
`_git_head` raising at `index_cli.py:1089-1106`) — there is *no row and no
lock*, which is indistinguishable from "never started". A poll issued
immediately after `indexRepo` returns would see `indexed: false` with nothing
running, and a naive agent would spawn again in a loop.

A bounded ready-handshake in the tool would paper over this at the cost of
added latency and a magic timeout that a loaded machine can still exceed.

An earlier draft of this spec used a token plus a "compare-and-set" of the
child's pid into the record. **That was not atomic** — it was read, check,
write, with a window in both directions: the parent could read a matching
token, the child could unlink, and the parent's `os.replace` would then
recreate a record nobody will ever remove; symmetrically, a child could read a
matching token and unlink a *newer* launch's record written in between.

Rather than serialize those under a second, short-held metadata mutex, the
design **removes the shared mutable handoff entirely.** No mutex is needed
because no participant ever reads-then-mutates.

#### Write-once launch record

`~/.jarvis/index-<slug>.launch`, JSON `{started_at, log}`.

1. The parent writes it **before `Popen`** — write-to-unique-temp then
   `os.replace`, matching the publish idiom.
2. The parent retains the `Popen` in a module-level `dict[str, Popen]` keyed by
   slug.
3. **That is the entire protocol.** The parent never re-reads or mutates it.
   The child never reads, writes, or unlinks it.

No token, and no `pid` in the file: nothing branches on either, because
liveness comes from reaping (below).

The property that removes the race is that each record is **written once and
never mutated** — not that only one process ever writes. Concurrent
`indexRepo` calls for the same slug genuinely can both reach step 1 before
either child takes the build lock, so the temp file **must be unique per
write** (`tempfile.mkstemp` in the target directory). A shared
`<slug>.launch.tmp` would let one caller rename the other's temp out from
under it, and the loser's `os.replace` would then raise. With unique temps
both renames succeed and the later one wins, which is correct: either record
describes a live attempt.

A leftover record cannot mislead, because §9 consults it only after the
published / failed / registered signals — and correlates it against the
registry row's timestamp so a *stale* record loses to the row while a
*current* one beats it.

#### Exit observation, not pid liveness

`os.kill(pid, 0)` is the **wrong primitive here**: the child remains the
server's child (`start_new_session=True` does not double-fork), so an exited
but unreaped child is a zombie whose pid still answers "alive". A
`failed-at-startup` job would report `starting` forever.

Liveness therefore comes from reaping, in two tiers:

- **Tracked child** (the common case): every `indexRepo` / `getIndexStatus`
  call first calls `poll()` on the retained `Popen`. That reaps the child and
  yields its **exit code** — strictly better evidence than pid liveness, and
  reported as `exitCode`.
- **Untracked child** (the MCP server restarted, so the handle is gone): fall
  back to `now - started_at` against a documented `STARTUP_GRACE` constant.
  Past it, an unregistered launch is `failed-at-startup` with the reason that
  the launching server is gone.

The age fallback is a heuristic, but a bounded one confined to the
server-restart case rather than the primary mechanism.

**Division of labour:** flock is exclusion plus steady-state liveness; the
write-once record plus `Popen.poll()` covers the startup window only.

**The lock file is never unlinked.** Removing a lock another process has
opened but not yet flocked lets the next writer create and lock a *fresh
inode*, so two writers each believe they hold the lock for the same repo.
`jarvis forget` therefore **acquires** the build lock before destroying a
repo's artifacts and leaves the lock file itself in place — an empty file is a
trivial footprint; a broken mutex is not. A forget that cannot take the lock
fails with rc 1 rather than deleting an index out from under a live writer.

New helpers in `config.py`, alongside `swift_cache_dir`:
`index_lockfile(slug, root)`, `index_launchfile(slug, root)`, and
`index_log(slug, root)`.

### Limitations, named

- **POSIX only.** Acceptable: the cibuildwheel matrix is Linux + macOS with no
  Windows leg.
- **NFS.** `flock` semantics over network mounts are unreliable, so a
  `JARVIS_DATA_DIR` on NFS degrades to best-effort exclusion. A docstring
  caveat, not a redesign — `~/.jarvis` on NFS is not a supported configuration.
- **Residual, out of scope.** Two *different* slugs racing on the *same* path
  hold different locks, so `_reject_duplicate_slug_for_path`'s `registry.list()`
  read stays racy. Pre-existing and unchanged by this work.

---

## 8. Agent-actionable error payload

`_error_payload`'s `IndexNotFoundError` branch (`server.py:319-334`) gains two
keys: `recoveryTool: "indexRepo"` and `recoveryToolArgs: {"path": "<the repo's
working directory>"}` — a hint template, since the server genuinely does not
know the path.

**This requires a structural change, not only an addition.** Today the
structured keys appear only when `origin_of(entry)` is non-None — i.e. only
when a registry row exists and explains itself. A never-indexed repo has no
row and currently receives bare `{"error": ...}` — exactly the case this
feature serves. So `recoveryTool` must be emitted **independent of row
existence**.

`error` / `state` / `cause` / `recovery` stay untouched, preserving the
documented additive contract (`server.py:300-302`): prose-only clients keep
working. `status_stderr` still never enters a payload.

---

## 9. Poll contract

The terminal states are free: `getIndexStatus` (`server.py:479`) already
returns `status` as the raw registry string, and `index_cli.py:1167` already
writes a transitional `"indexing"` row **carrying the path**.

| Observation | Meaning |
|---|---|
| `indexed: true` | Published; proceed |
| `status: "degraded"` | Baseline published, SCIP stage failed; nav tools capability-gated |
| `status: "failed"` + `cause` / `recovery` | Run failed and recorded why |

The addition is the loop's terminating condition. Because the launch record is
never deleted (§7), the state is **not** a lookup keyed on the record's
presence — it is an ordered evaluation where the first match wins, and the
record is **correlated against the registry row's `last_indexed`** so a stale
record and a stale row cannot each mislead. Every observation reaps first:

A record is *current* when it is newer than the row (or no row exists).

| # | Condition | Result | Agent |
|---|---|---|---|
| 0 | reap: `poll()` every retained handle | — | — |
| 1 | lock held | `indexing.state = "running"` | keep polling |
| 2 | current record, tracked child running | `"starting"` | keep polling |
| 3 | current record, tracked child exited | `"failed-at-startup"` + `exitCode` + `log` | **stop** |
| 4 | current record, untracked, age < `STARTUP_GRACE` | `"starting"` | keep polling |
| 5 | current record, untracked, age ≥ `STARTUP_GRACE` | `"failed-at-startup"` + `log` | **stop** — launching server gone |
| 6 | `indexed: true` | terminal success | proceed |
| 7 | row `status == "failed"` | terminal failure + `cause` / `recovery` | stop |
| 8 | row `status == "indexing"`, lock free | `"abandoned"` + `log` | **stop** — registered, then died mid-run |
| 9 | otherwise | not indexing | — |

**Correlation is what makes both stale states harmless.** A *current* record
outranks the row (rows 2-5 before 6-8), because a repo whose previous run
failed still has a `failed` row while a freshly spawned child is starting —
reading the row first would report "nothing in flight" and the agent would
spawn again, forever. The symmetric case is a row left at `indexing` by an
earlier killed run, which must not report `abandoned` for a new attempt.

Once the child registers, `upsert` stamps `last_indexed` later than the
record, so the record stops being current and the row governs again — which
is precisely when `abandoned` becomes the right answer for a child that
registered and then died.

No branch consults pid liveness; `os.kill(pid, 0)` would report an unreaped
zombie as alive and wedge the loop in `"starting"` permanently.

Without the terminating rows the poll loop has no bound — the acknowledged
cost of choosing the non-blocking model (AI-02).

---

## 10. Execution

```python
subprocess.Popen(
    [jarvis_bin, "index", str(resolved), "--slug", slug, *flags],
    stdout=subprocess.DEVNULL, stderr=log_file,
    stdin=subprocess.DEVNULL, start_new_session=True,
    env={**os.environ, "JARVIS_DATA_DIR": str(config.data_dir())},
)
```

`--slug` is explicit and **not** optional. §6 resolves by path first, so it
may return a slug the child would never derive on its own — a repo registered
under a custom `--slug`. Omitting it makes the child derive the basename, trip
`_reject_duplicate_slug_for_path`, and die, while the tool has already
reported the custom slug for the caller to poll.

Ordering inside the tool is fixed and load-bearing: pre-flight → **write the
launch record** → `Popen` → retain the handle → return the payload. There is no
post-spawn mutation of the record, which is what removes the read-then-write
race entirely (§7).

Five load-bearing details:

**The child must never inherit the server's stdout.** stdio *is* the MCP
transport; one inherited write corrupts the JSON-RPC stream and kills the
session. `stdout=DEVNULL`, `stderr=` a log file at
`~/.jarvis/index-<slug>.log`, mirroring `ZoektLifecycle`'s `_log_path`
(`search.py:285-291`). `stdin=DEVNULL` additionally guarantees the TTY-gated
semantic install prompt (`index_cli.py:1502`) can never engage.

**Binary resolution cannot rely on PATH.** MCP clients spawn servers with
sanitized environments, and `uv tool install` places `jarvis` in a bin
directory that may not be on it. Resolve `Path(sys.executable).parent /
"jarvis"` first (correct for both venv and `uv tool` layouts), fall back to
`shutil.which("jarvis")`, and if neither resolves return an error naming the
failure rather than spawning nothing silently.

**`JARVIS_DATA_DIR` is passed explicitly** so the child provably writes where
the server reads, rather than depending on both processes resolving the
environment identically.

**`start_new_session=True`** detaches the job so the index survives the MCP
client tearing down the server — the entire point of AI-02.

### New CLI surface

No flag currently suppresses the semantic stage: `_prepare_semantic_stage`
runs whenever `import jarvis.semantic` succeeds (`index_cli.py:778-786`), and
only the interactive *install offer* is TTY-gated (`index_cli.py:1499-1503`).
`--semantic-include` filters paths only.

This adds `index_repo(..., semantic: bool = True)` plus a
`--no-semantic` / `--semantic` tri-state pair on `index` / `reindex` / `watch`,
following `_add_scip_flag`'s exact shape (`index_cli.py:1862`).
Independently useful: `jarvis index --no-semantic` is a reasonable thing to
want.

---

## 11. Failure modes

| Case | Behavior |
|---|---|
| `zoekt-git-index` absent | Pre-flight error + `setup.sh --only zoekt` recovery; no spawn |
| Not a git repo | Pre-flight error; no row, no lock |
| Slug bound to another still-existing path | Pre-flight error naming both paths; re-checked inside the lock |
| Repo moved (old path gone) | Allowed; `upsert` updates `path` |
| Concurrent `indexRepo` | Live lock **or** a live retained handle → `alreadyRunning: true`, no second spawn; the handle check closes the window before the child takes the lock, which also prevents dropping a handle and leaking an unreapable zombie |
| Concurrent launch-record writes | Each publishes through its own unique temp, so both renames succeed and the later record wins; either describes a live attempt |
| Lock race lost by the child | rc 1, registry and artifacts untouched; the agent's poll correctly observes the *other* live job |
| Child killed mid-run, after registering | Lock released by kernel, row still `"indexing"`, record now older than the row → §9 row 8, `"abandoned"` + log |
| Child dies before registering | Reaped `poll()` yields its exit code → §9 row 3, `"failed-at-startup"` + `exitCode` + log |
| Child exited but not yet reaped | Observation reaps at row 0 before deciding, so the zombie is never read as `"starting"` |
| Poll issued immediately after spawn | Record already on disk (written pre-spawn) and current → row 2, `"starting"`; never mistaken for "not indexed" |
| Retry after a failed or abandoned run | The new record is newer than the row, so rows 2-5 win; stale `failed` / `indexing` state cannot terminate the new attempt's poll loop |
| Child registers before the parent returns | Nothing to race: the parent never touches the record after `Popen`, and the child never touches it at all |
| Record left over from a completed run | Older than the row, so rows 6-8 govern; the next launch overwrites it via `os.replace` |
| `jarvis forget` during a live index | Forget cannot take the build lock → rc 1, nothing destroyed |
| MCP server restarted mid-index | Handle lost; record age vs `STARTUP_GRACE` decides rows 4 vs 5 |
| `semantic=true`, extra absent | Child prints the existing hint; baseline still publishes (`index_cli.py:781-786`) |
| SCIP toolchain absent | Already degrades; `status: "degraded"` |

---

## 12. Testing

`JARVIS_DATA_DIR` isolation via `monkeypatch.setenv` is mandatory in every new
test.

**`tests/test_server_tools.py`**
- `indexRepo` returns the spawn payload; argv asserted, including `--slug`
  (with a registered custom slug), `--no-semantic` by default, and its
  absence under `semantic=true`.
- **`stdout` is `subprocess.DEVNULL`** — a real regression risk with a silent,
  catastrophic failure mode (corrupted JSON-RPC).
- Each pre-flight error returns `{"error"}` and spawns nothing (`Popen`
  monkeypatched to fail the test if called).
- `IndexNotFoundError` payload carries `recoveryTool` **both** with and
  without a registry row — the row-less case is the regression that matters.
- Existing `_error_payload` tests checked for exhaustive-dict assertions the
  new keys would break.
- Poll immediately after `indexRepo` reports `state: "starting"`, **not**
  `indexed: false` with nothing running — the loop-forever regression.
- A child that exits nonzero before the transitional `upsert` reports
  `state: "failed-at-startup"` with its `exitCode` and log path, from a real
  spawned process.
- **Exited-but-unreaped child**: hand observation a real child that has
  exited and has **never been polled** — must report `failed-at-startup`
  with its true exit code, not `starting`. Wait for the exit via stdout EOF,
  never by spinning on `poll()`: `poll()` reaps, and a later `poll()` on an
  already-reaped handle returns `0` because CPython swallows `ECHILD` as
  exit 0, so a spin-then-reset test asserts against a fabricated code. The
  same test should assert `os.kill(pid, 0)` succeeds on the zombie, which is
  the whole reason pid liveness is unusable. Cannot be caught with a mocked
  `Popen`.
- **Child-ready-before-parent-returns**: a real child that acquires the lock
  and registers before the tool returns; the poll must report `running`
  (row 1), and the record must be exactly what the parent wrote — proving no
  post-spawn mutation exists to interleave with.
- **Leftover record does not mask a later state**: with a record on disk from a
  prior run, assert a published index reports success (row 2), a `"failed"` row
  reports failure (row 3), and an `"indexing"` row with a free lock reports
  `abandoned` (row 4) — never `starting`.
- Untracked record past `STARTUP_GRACE` reports `failed-at-startup`; within it,
  `starting`.

No test pauses a participant "between a token check and a mutation," because
after this revision no such check-then-mutate sequence exists — that is the
property under test, asserted by the child-ready case above.

**`tests/test_index_cli.py`**
- Lock acquired before the transitional `upsert`; loser exits rc 1 with the
  registry unmodified.
- Lock released on normal exit and on exception.
- The child never reads, writes, or unlinks the launch record: assert its
  bytes are unchanged across a full successful index.
- `jarvis forget` removes the lock, launch record, and log alongside the index.
- `resolve_slug_for_path`: reuse of an existing custom slug; rejection on
  same-slug/different-existing-path; **acceptance** when the registered path no
  longer exists (the moved-repo case).
- `--no-semantic` suppression asserted by the semantic stage not running.

Real `flock` against throwaway `tmp_path` files and a real child process for
the race — git and filesystem behavior are the contract here, so neither is
mocked.

---

## 13. Non-goals

- **Auto-refresh of stale indexes.** Needs sub-project #3 first; staleness
  stays with existing freshness reporting.
- **Branch dimension.** Sub-project #4.
- **ANN index.** Sub-project #2.
- **`jarvis watch` auto-start** from the server.
- **Making Zoekt optional.** The change that would render bootstrap genuinely
  binary-free, but it alters the publish contract. Separate spec.
