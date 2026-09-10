# Server-Side Auto-Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an AI agent bootstrap a missing jarvis index itself, by adding an `indexRepo` MCP tool that spawns `jarvis index` as a detached child and a poll contract that always terminates.

**Architecture:** A new `src/jarvis/jobs.py` owns everything about an in-flight index run — a per-slug `flock` build lock, a single-writer launch record, and one pure state-derivation function. `index_cli.py` (the writer) acquires the lock; `server.py` (the reader) writes the launch record, spawns the child, retains the handle, and reports state. No shared mutable handoff exists between parent and child, so no metadata mutex is needed.

**Tech Stack:** Python 3.12+, stdlib `fcntl` / `subprocess` / `json` / `sqlite3`, FastMCP (`mcp<2.0.0`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-10-server-side-auto-index-design.md`

## Global Constraints

- Python `>=3.12,<3.15`. Use `X | None`, `list[T]`, `dict[K, V]` — never `Optional[T]`.
- Every new module starts with `from __future__ import annotations`.
- Result/value objects are `@dataclass(frozen=True)`. Closed string vocabularies are `StrEnum`.
- Persistence is raw stdlib `sqlite3` with parameterized queries. Never add async to the query path.
- Env overrides are `JARVIS_`-prefixed and resolved in the owning module, never at call sites.
- The MCP boundary never raises: every `@mcp.tool` body wraps in broad `except Exception` returning `{"error": ...}`.
- `stdout` stays machine-parseable (`indexed <slug>`); warnings and notes go to stderr.
- Any test touching config/registry/index state MUST call `monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))` before constructing anything.
- `flock` is POSIX-only. Acceptable: the cibuildwheel matrix is Linux + macOS with no Windows leg.
- Commits follow Conventional Commits with lowercase imperative subjects: `feat(scope):`, `fix(scope):`, `test:`, `docs:`.
- No linter or formatter is configured. Match surrounding style; do not add one.
- Run only the specific tests each task names. Never run the full suite mid-plan.

---

### Task 1: `jobs.py` — build lock, launch record, state derivation

**Files:**
- Create: `src/jarvis/jobs.py`
- Modify: `src/jarvis/config.py:40` (add three path helpers after `swift_cache_dir`)
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `config.data_dir()` (existing).
- Produces:
  - `config.index_lockfile(slug: str, root: Path | None = None) -> Path`
  - `config.index_launchfile(slug: str, root: Path | None = None) -> Path`
  - `config.index_log(slug: str, root: Path | None = None) -> Path`
  - `jobs.STARTUP_GRACE_SECONDS: int`
  - `jobs.BuildLockHeld(Exception)`
  - `jobs.build_lock(slug: str, *, root: Path | None = None)` — context manager, raises `BuildLockHeld`
  - `jobs.LockProbe(held: bool, pid: int | None)` frozen dataclass
  - `jobs.lock_state(slug: str, *, root: Path | None = None) -> LockProbe`
  - `jobs.LaunchRecord(started_at: datetime, log: str)` frozen dataclass
  - `jobs.write_launch_record(slug: str, log_path: Path, *, root: Path | None = None) -> None`
  - `jobs.read_launch_record(slug: str, *, root: Path | None = None) -> LaunchRecord | None`
  - `jobs.JobState(state: str, pid: int | None, exit_code: int | None, log: str | None)` frozen dataclass
  - `jobs.job_state(slug, *, indexed, registry_status, child=None, root=None, now=None) -> JobState | None`
  - `jobs.clear_job_files(slug: str, *, root: Path | None = None) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jobs.py`:

```python
"""Tests for jobs.py: the per-slug build lock (flock), the single-writer
launch record, and the ordered job-state derivation of the spec's section 9."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis import config, jobs


def test_build_lock_excludes_a_second_holder(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with jobs.build_lock("app"):
        probe = jobs.lock_state("app")
        assert probe.held is True
        assert probe.pid == os.getpid()
        # A second acquisition in the same process must also be refused:
        # flock is per-open-file-description, and this is a different fd.
        with pytest.raises(jobs.BuildLockHeld):
            with jobs.build_lock("app"):
                pass
    assert jobs.lock_state("app").held is False


def test_build_lock_releases_on_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError):
        with jobs.build_lock("app"):
            raise RuntimeError("boom")
    assert jobs.lock_state("app").held is False


def test_build_lock_is_per_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with jobs.build_lock("app"):
        with jobs.build_lock("other"):
            assert jobs.lock_state("other").held is True


def test_lock_state_false_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert jobs.lock_state("never").held is False


def test_lock_held_across_processes(tmp_path, monkeypatch):
    """The real contract: a lock held by another process is visible here.
    Not mocked — cross-process exclusion is the whole point."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    code = (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n" % str(Path(__file__).resolve().parents[1] / "src")
        + "from jarvis import jobs\n"
        "with jobs.build_lock('app'):\n"
        "    print('locked', flush=True)\n"
        "    time.sleep(30)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE,
        env={**os.environ, "JARVIS_DATA_DIR": str(tmp_path)},
    )
    try:
        assert child.stdout.readline().strip() == b"locked"
        probe = jobs.lock_state("app")
        assert probe.held is True
        assert probe.pid == child.pid
        with pytest.raises(jobs.BuildLockHeld):
            with jobs.build_lock("app"):
                pass
    finally:
        child.kill()
        child.wait()
    assert jobs.lock_state("app").held is False


def test_launch_record_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    record = jobs.read_launch_record("app")
    assert record is not None
    assert record.log == "/tmp/app.log"
    assert (datetime.now(UTC) - record.started_at).total_seconds() < 5


def test_launch_record_has_no_pid_or_token(tmp_path, monkeypatch):
    """The record is single-writer and immutable: exactly two fields, so
    there is nothing for a second party to check-then-mutate."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    raw = json.loads(config.index_launchfile("app").read_text(encoding="utf-8"))
    assert set(raw) == {"started_at", "log"}


def test_launch_record_overwrite_is_atomic_replace(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/one.log"))
    jobs.write_launch_record("app", Path("/tmp/two.log"))
    assert jobs.read_launch_record("app").log == "/tmp/two.log"
    leftovers = list(config.index_launchfile("app").parent.glob("*.tmp"))
    assert leftovers == []


def test_read_launch_record_none_when_absent_or_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert jobs.read_launch_record("app") is None
    path = config.index_launchfile("app")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert jobs.read_launch_record("app") is None


class _FakeChild:
    def __init__(self, pid: int, rc: int | None) -> None:
        self.pid = pid
        self._rc = rc

    def poll(self) -> int | None:
        return self._rc


def test_job_state_running_when_lock_held(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with jobs.build_lock("app"):
        state = jobs.job_state("app", indexed=False, registry_status="indexing")
    assert state.state == "running"
    assert state.pid == os.getpid()


def test_job_state_running_wins_over_published(tmp_path, monkeypatch):
    """Row 1 precedes row 2: a re-index of an already-published repo is
    running, not 'not indexing'."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with jobs.build_lock("app"):
        state = jobs.job_state("app", indexed=True, registry_status="indexing")
    assert state.state == "running"


def test_job_state_none_when_published(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    assert jobs.job_state("app", indexed=True, registry_status="indexed") is None


def test_job_state_none_when_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    assert jobs.job_state("app", indexed=False, registry_status="failed") is None


def test_job_state_abandoned_when_row_indexing_and_lock_free(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    state = jobs.job_state("app", indexed=False, registry_status="indexing")
    assert state.state == "abandoned"


def test_job_state_starting_with_live_tracked_child(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    state = jobs.job_state("app", indexed=False, registry_status=None,
                           child=_FakeChild(4242, None))
    assert (state.state, state.pid, state.exit_code) == ("starting", 4242, None)


def test_job_state_failed_at_startup_with_exited_tracked_child(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    state = jobs.job_state("app", indexed=False, registry_status=None,
                           child=_FakeChild(4242, 1))
    assert state.state == "failed-at-startup"
    assert state.exit_code == 1
    assert state.log == "/tmp/app.log"


def test_job_state_untracked_uses_bounded_grace(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    fresh = datetime.now(UTC)
    assert jobs.job_state("app", indexed=False, registry_status=None,
                          now=fresh).state == "starting"
    stale = fresh + timedelta(seconds=jobs.STARTUP_GRACE_SECONDS + 1)
    assert jobs.job_state("app", indexed=False, registry_status=None,
                          now=stale).state == "failed-at-startup"


def test_job_state_none_when_nothing_in_flight(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert jobs.job_state("app", indexed=False, registry_status=None) is None


def test_clear_job_files_removes_all_three(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    config.index_log("app").parent.mkdir(parents=True, exist_ok=True)
    config.index_log("app").write_text("x", encoding="utf-8")
    with jobs.build_lock("app"):
        pass
    jobs.clear_job_files("app")
    assert not config.index_launchfile("app").exists()
    assert not config.index_log("app").exists()
    assert not config.index_lockfile("app").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_jobs.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.jobs'`

- [ ] **Step 3: Add the three path helpers to `config.py`**

Insert directly after `swift_cache_dir` (which ends at `config.py:40`), matching its docstring style:

```python
def index_lockfile(slug: str, root: Path | None = None) -> Path:
    """Per-slug build lock. Held (via `flock`) for the whole duration of one
    index run by whichever process is writing — CLI, watch, or an
    MCP-spawned child alike. Never unlinked while jarvis is running: removing
    a file another waiter still holds open is the classic flock footgun."""
    return data_dir(root) / f"index-{slug}.lock"


def index_launchfile(slug: str, root: Path | None = None) -> Path:
    """Single-writer record of an index run that has been spawned but has not
    yet acquired the build lock. Written once, before spawn, by the process
    doing the spawning; never mutated, and never touched by the child."""
    return data_dir(root) / f"index-{slug}.launch"


def index_log(slug: str, root: Path | None = None) -> Path:
    """stderr of an MCP-spawned index child. The child must never inherit the
    server's stdio — that stream is the MCP transport."""
    return data_dir(root) / f"index-{slug}.log"
```

- [ ] **Step 4: Create `src/jarvis/jobs.py`**

```python
"""In-flight index-run coordination: the per-slug build lock, the launch
record covering the pre-registration window, and the ordered state
derivation both the CLI and the MCP server read.

Split out of `index_cli.py` (already the largest module) because both the
writer and the reader need it: `index_cli` acquires the lock, `server`
writes the launch record and derives state. Nothing here touches SQLite or
the published snapshot.

Two coordination primitives, with a strict division of labour:

* **`flock` build lock** — mutual exclusion plus steady-state liveness. The
  kernel releases it on process death, so there is no stale-lock state and
  no reclamation race.
* **Launch record** — covers only the window between `Popen` returning and
  the child acquiring the lock. Written once by the spawning process before
  the spawn, never mutated, never read or removed by the child. That
  single-writer discipline is deliberate: an earlier design mutated the
  record from both sides and had an unavoidable check-then-write race in
  both directions.

Liveness of a spawned-but-unregistered child comes from **reaping**, never
from `os.kill(pid, 0)`: `start_new_session=True` does not double-fork, so an
exited-but-unreaped child is a zombie whose pid still answers "alive".
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from jarvis import config

# How long an index run whose spawning process is gone (MCP server restarted,
# so the Popen handle died with it) may sit unregistered before it is reported
# as failed. Only ever consulted for an untracked child -- a bounded fallback,
# not the primary mechanism.
STARTUP_GRACE_SECONDS = 120


class BuildLockHeld(Exception):
    """Another process is already indexing this slug."""


class _Reapable(Protocol):
    """The slice of `subprocess.Popen` state derivation needs."""

    pid: int

    def poll(self) -> int | None: ...


@dataclass(frozen=True)
class LockProbe:
    held: bool
    pid: int | None


@dataclass(frozen=True)
class LaunchRecord:
    started_at: datetime
    log: str


@dataclass(frozen=True)
class JobState:
    state: str  # running | starting | failed-at-startup | abandoned
    pid: int | None
    exit_code: int | None
    log: str | None


@contextlib.contextmanager
def build_lock(slug: str, *, root: Path | None = None) -> Iterator[None]:
    """Hold the per-slug build lock for the duration of the block, writing the
    holder's pid into the file for reporting.

    Fail-fast (`LOCK_NB`) rather than queueing: a debounced `watch` reindex
    will retry on the next event, and an explicit `jarvis index` should say
    what is happening instead of blocking silently.
    """
    path = config.index_lockfile(slug, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BuildLockHeld(
                f"another index is already running for {slug!r}"
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        yield
    finally:
        # Closing the descriptor releases the flock. The file itself stays:
        # unlinking it would let a later waiter lock a detached inode.
        os.close(fd)


def lock_state(slug: str, *, root: Path | None = None) -> LockProbe:
    """Whether a build lock is currently held, and by which pid.

    Probing means trying to take the lock: acquired means nobody holds it, so
    it is released immediately. The pid is read from the file only when the
    lock is genuinely held, and is reporting-only -- no caller branches on
    its liveness.
    """
    path = config.index_lockfile(slug, root)
    if not path.exists():
        return LockProbe(held=False, pid=None)
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return LockProbe(held=False, pid=None)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return LockProbe(held=True, pid=_read_pid(fd))
        fcntl.flock(fd, fcntl.LOCK_UN)
        return LockProbe(held=False, pid=None)
    finally:
        os.close(fd)


def _read_pid(fd: int) -> int | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 32).decode("utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def write_launch_record(slug: str, log_path: Path, *,
                        root: Path | None = None) -> None:
    """Record that an index child is about to be spawned.

    Write-temp-then-`os.replace`, matching the snapshot publish idiom: a
    reader sees the old record or the new one, never a torn one. Called
    exactly once per launch, BEFORE `Popen`, so a fast child can never
    observe a state where it has registered but no record exists.
    """
    path = config.index_launchfile(slug, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "started_at": datetime.now(UTC).isoformat(),
        "log": str(log_path),
    })
    tmp = path.parent / f"{path.name}.tmp"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def read_launch_record(slug: str, *,
                       root: Path | None = None) -> LaunchRecord | None:
    """The launch record, or None when absent or unreadable. Broad on
    purpose: a corrupt record must degrade to "no record" rather than break a
    status response."""
    try:
        raw: Any = json.loads(
            config.index_launchfile(slug, root).read_text(encoding="utf-8")
        )
        return LaunchRecord(
            started_at=datetime.fromisoformat(raw["started_at"]),
            log=str(raw["log"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def job_state(
    slug: str, *, indexed: bool, registry_status: str | None,
    child: _Reapable | None = None, root: Path | None = None,
    now: datetime | None = None,
) -> JobState | None:
    """The spec's section 9 ordered evaluation; first match wins. `None` means
    "no index run is in flight" -- the caller's other fields already say
    whether one succeeded or failed.

    The ORDER is what makes a leftover launch record harmless: the record is
    never deleted, so it is consulted only after the stronger published /
    failed / registered signals have been ruled out. Callers must have
    already reaped `child` -- or pass it here, since this calls `poll()`.
    """
    probe = lock_state(slug, root=root)
    if probe.held:  # row 1
        return JobState(state="running", pid=probe.pid, exit_code=None,
                        log=str(config.index_log(slug, root)))
    if indexed or registry_status == "failed":  # rows 2, 3
        return None
    record = read_launch_record(slug, root=root)
    log = record.log if record is not None else str(config.index_log(slug, root))
    if registry_status == "indexing":  # row 4
        return JobState(state="abandoned", pid=None, exit_code=None, log=log)
    if record is None:  # row 9
        return None
    if child is not None:  # rows 5, 6
        code = child.poll()
        state = "starting" if code is None else "failed-at-startup"
        return JobState(state=state, pid=child.pid, exit_code=code, log=log)
    age = ((now or datetime.now(UTC)) - record.started_at).total_seconds()
    if age < STARTUP_GRACE_SECONDS:  # row 7
        return JobState(state="starting", pid=None, exit_code=None, log=log)
    return JobState(state="failed-at-startup", pid=None,  # row 8
                    exit_code=None, log=log)


def clear_job_files(slug: str, *, root: Path | None = None) -> None:
    """Remove every job artifact for a slug (`jarvis forget`). Best-effort:
    forgetting a repo must not fail over a cleanup step."""
    for path in (config.index_launchfile(slug, root),
                 config.index_log(slug, root),
                 config.index_lockfile(slug, root)):
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_jobs.py -q`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/jobs.py src/jarvis/config.py tests/test_jobs.py
git commit -m "feat(jobs): add per-slug build lock, launch record, and job-state derivation"
```

---

### Task 2: `--no-semantic` flag and `semantic` kwarg

**Files:**
- Modify: `src/jarvis/index_cli.py` (`index_repo` signature ~line 1034; the `_prepare_semantic_stage` call site; `_add_scip_flag` neighbourhood ~line 1862; the `index` / `reindex` / `watch` subparsers ~lines 1924, 1940, 1961)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `index_repo(..., semantic: bool = True)`; a `--semantic` / `--no-semantic` pair whose `dest` is `semantic` and whose default is `None` (meaning "unspecified").

**Why:** no flag currently suppresses the semantic stage — `_prepare_semantic_stage` runs whenever `import jarvis.semantic` succeeds (`index_cli.py:778-786`), and only the interactive install offer is TTY-gated. Without this, one agent tool call can trigger a ~2 GB model download.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_cli.py`:

```python
def test_index_repo_semantic_false_skips_the_stage(tmp_path, monkeypatch):
    """An agent-triggered index must never implicitly download embedding
    weights. `semantic=False` skips prepare entirely -- not merely the
    install offer, which is TTY-gated and therefore already unreachable."""
    from jarvis import index_cli

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    calls: list[str] = []
    monkeypatch.setattr(
        index_cli, "_prepare_semantic_stage",
        lambda *a, **k: calls.append("prepared") or None,
    )
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    _stub_pipeline(monkeypatch)

    index_cli.index_repo(repo_dir, semantic=False)
    assert calls == []

    index_cli.index_repo(repo_dir, semantic=True)
    assert calls == ["prepared"]


def test_no_semantic_flag_parses_to_false(tmp_path, monkeypatch):
    from jarvis import index_cli

    parser = index_cli._build_parser()
    assert parser.parse_args(["index", "/r", "--no-semantic"]).semantic is False
    assert parser.parse_args(["index", "/r", "--semantic"]).semantic is True
    assert parser.parse_args(["index", "/r"]).semantic is None
    assert parser.parse_args(["reindex", "s", "--no-semantic"]).semantic is False
    assert parser.parse_args(["watch", "/r", "--no-semantic"]).semantic is False
```

`_stub_pipeline` and `_init_git_repo` are the existing helpers in this file — reuse them; do not write new ones. If the local helper names differ, use whatever the neighbouring `index_repo` tests use (they monkeypatch `index_cli._run` dispatching on `step.endswith(" index")`).

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_index_cli.py -q -k "semantic_false or no_semantic_flag"`
Expected: FAIL — `index_repo() got an unexpected keyword argument 'semantic'`

- [ ] **Step 3: Add the kwarg and the flag**

In `index_repo`'s signature (after `scip: bool | None = None`, ~line 1037) add:

```python
    semantic: bool = True,
```

Extend the docstring's parameter notes with one line:

```
    `semantic=False` skips the optional semantic stage even when the extra is
    installed -- the MCP `indexRepo` path passes it so an agent tool call can
    never implicitly download embedding weights.
```

Guard the existing `_prepare_semantic_stage` call site so it becomes:

```python
        sem_work = (
            _prepare_semantic_stage(repo_path, slug, root, include_prefixes, manifest)
            if semantic else None
        )
```

Add the flag helper next to `_add_scip_flag` (~line 1862):

```python
def _add_semantic_flag(parser: argparse.ArgumentParser) -> None:
    """The mutually exclusive `--semantic` / `--no-semantic` pair. One
    tri-state dest, mirroring `_add_scip_flag`: omitted means "leave the
    default in force" (the stage runs when the extra is installed), explicit
    means the caller decided. Unlike --scip this is NOT persisted -- it is a
    per-run cost decision, not a property of the repo."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--semantic", dest="semantic", action="store_true", default=None,
        help="build the semantic (vector) index when the `semantic` extra is installed",
    )
    group.add_argument(
        "--no-semantic", dest="semantic", action="store_false", default=None,
        help="skip the semantic stage even when the `semantic` extra is installed",
    )
```

Call `_add_semantic_flag(...)` immediately after each existing `_add_scip_flag(...)` call for the `index`, `reindex`, and `watch` subparsers.

- [ ] **Step 4: Thread the flag through the three commands**

In `_cmd_index`'s `index_repo(...)` call add:

```python
            semantic=getattr(args, "semantic", None) is not False,
```

Do the same in `_cmd_reindex`'s `index_repo(...)` call and in `_cmd_watch`'s `_reindex()` body. `is not False` is deliberate: `None` (unspecified) and `True` both mean "run the stage".

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -q -k "semantic"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "feat(index): add --no-semantic to suppress the semantic stage"
```

---

### Task 3: Hold the build lock across the whole index run

**Files:**
- Modify: `src/jarvis/index_cli.py:1085-1096` (`index_repo` prologue) and its `main()` error handling (`_cmd_index` ~1487, `_cmd_reindex`, `_cmd_watch`)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `jobs.build_lock`, `jobs.BuildLockHeld` (Task 1).
- Produces: `index_repo` raising `jobs.BuildLockHeld` when another run owns the slug.

- [ ] **Step 1: Write the failing test**

```python
def test_index_repo_refuses_when_build_lock_held(tmp_path, monkeypatch):
    """Exclusion must cover the registry write too: the loser leaves the row
    exactly as it found it."""
    from jarvis import index_cli, jobs
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    _stub_pipeline(monkeypatch)

    with jobs.build_lock(config.repo_slug(repo_dir.name)):
        with pytest.raises(jobs.BuildLockHeld):
            index_cli.index_repo(repo_dir)

    registry = Registry(config.data_dir() / "registry.db")
    try:
        assert registry.get(config.repo_slug(repo_dir.name)) is None
    finally:
        registry.close()


def test_index_repo_lock_is_held_at_the_transitional_upsert(tmp_path, monkeypatch):
    """The lock must be acquired BEFORE the row flips to 'indexing', or two
    writers can both register."""
    from jarvis import index_cli, jobs

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    _stub_pipeline(monkeypatch)
    slug = config.repo_slug(repo_dir.name)

    seen: list[bool] = []
    real_upsert = index_cli.Registry.upsert

    def _spy(self, *args, **kwargs):
        seen.append(jobs.lock_state(slug).held)
        return real_upsert(self, *args, **kwargs)

    monkeypatch.setattr(index_cli.Registry, "upsert", _spy)
    index_cli.index_repo(repo_dir)
    assert seen and all(seen)


def test_index_repo_releases_lock_on_failure(tmp_path, monkeypatch):
    from jarvis import index_cli, jobs

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo_dir)
    _init_git_repo(repo_dir)
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr(
        index_cli, "_pin_zoekt_repo_name",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(Exception):
        index_cli.index_repo(repo_dir)
    assert jobs.lock_state(config.repo_slug(repo_dir.name)).held is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_index_cli.py -q -k "build_lock or transitional_upsert or releases_lock"`
Expected: FAIL — `index_repo` completes without raising `BuildLockHeld`

- [ ] **Step 3: Wrap the run in the lock**

Import at the top of `index_cli.py`, alongside the other `from jarvis import ...` lines:

```python
from jarvis import jobs
```

`index_repo`'s body currently starts (line 1087) with `repo_path = repo_path.resolve()`. Restructure the prologue so the lock encloses everything after slug derivation, by extracting the existing body into an inner function and calling it inside the lock:

```python
    repo_path = repo_path.resolve()
    slug = config.repo_slug(slug or repo_path.name)
    # Acquired before the duplicate guards and before the transitional
    # `indexing` upsert: exclusion has to cover registry writes and artifact
    # writes alike, or two writers race on the same slug. `flock` and not a
    # pidfile -- a pidfile is a discovery cache, and nothing here binds a port
    # to arbitrate a lost race the way ZoektLifecycle does.
    with jobs.build_lock(slug, root=root):
        return _index_repo_locked(
            repo_path, slug=slug, root=root, scheme=scheme,
            semantic_include=semantic_include, language=language,
            scip=scip, watch=watch, semantic=semantic,
        )
```

Move the remainder of the current body (from `sha = _git_head(repo_path)` at line 1089 through the final `return slug`) into a new module-level `def _index_repo_locked(repo_path: Path, *, slug: str, root, scheme, semantic_include, language, scip, watch, semantic) -> str:` with the docstring:

```python
    """`index_repo`'s body, running under the per-slug build lock. Split out
    only so the lock's extent is visible in one place; the staged pipeline is
    unchanged."""
```

Inside `_index_repo_locked`, delete the now-duplicated `repo_path = repo_path.resolve()` and `slug = config.repo_slug(...)` lines — both are done by the caller.

- [ ] **Step 4: Report the refusal at the CLI boundary**

In `_cmd_index`, `_cmd_reindex`, and `_cmd_watch`, add `jobs.BuildLockHeld` to the caught exception tuple so it prints `error: another index is already running for 'app'` and returns rc 1 rather than raising a traceback. For `_cmd_index` that means:

```python
    except (UnsupportedLanguageError, NotAGitRepositoryError, IndexingError,
            jobs.BuildLockHeld, ValueError) as exc:
```

`_cmd_watch`'s `_reindex()` already catches broadly and prints `[watch] reindex failed: ...`; leave it — a debounced run that loses the race retries on the next event.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -q -k "build_lock or transitional_upsert or releases_lock or semantic"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "feat(index): hold a per-slug build lock across the whole index run"
```

---

### Task 4: `resolve_slug_for_path` and the same-slug/different-path guard

**Files:**
- Modify: `src/jarvis/index_cli.py` (new functions near `_reject_duplicate_slug_for_path` at line 975; `_git_head`'s git check at lines 324-333; `_index_repo_locked`'s guard sequence)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `Registry.list()`, `Registry` (existing).
- Produces:
  - `index_cli.ensure_git_repo(repo_path: Path) -> None` — raises `NotAGitRepositoryError`
  - `index_cli.resolve_slug_for_path(registry: Registry, repo_path: Path) -> str` — raises `IndexingError` on collision

**Why:** `_reject_duplicate_slug_for_path` enforces one slug per *path* and explicitly `continue`s when `existing.slug == slug` (line 994). The inverse is unguarded: `/a/app` and `/b/app` both derive slug `app`, and the second silently overwrites the first's published index.

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_slug_reuses_the_registered_slug_for_a_known_path(tmp_path, monkeypatch):
    """A repo indexed under an explicit --slug must keep it: resolution is by
    path first, basename second. Otherwise indexRepo would derive a different
    slug and index the same repo twice."""
    from jarvis import index_cli
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "app"
    repo.mkdir()
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("custom-name", str(repo), "python", None, "indexed")
        assert index_cli.resolve_slug_for_path(registry, repo) == "custom-name"
    finally:
        registry.close()


def test_resolve_slug_rejects_same_basename_at_a_different_live_path(tmp_path, monkeypatch):
    from jarvis import index_cli
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    first = tmp_path / "a" / "app"
    second = tmp_path / "b" / "app"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("app", str(first), "python", None, "indexed")
        with pytest.raises(index_cli.IndexingError) as exc:
            index_cli.resolve_slug_for_path(registry, second)
        assert str(first) in str(exc.value)
        assert str(second) in str(exc.value)
    finally:
        registry.close()


def test_resolve_slug_allows_a_moved_repo(tmp_path, monkeypatch):
    """The registered path no longer exists, so this is a move, not a
    collision -- `upsert` already updates `path`."""
    from jarvis import index_cli
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    moved_to = tmp_path / "new" / "app"
    moved_to.mkdir(parents=True)
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("app", str(tmp_path / "gone" / "app"), "python", None, "indexed")
        assert index_cli.resolve_slug_for_path(registry, moved_to) == "app"
    finally:
        registry.close()


def test_resolve_slug_derives_basename_when_unregistered(tmp_path, monkeypatch):
    from jarvis import index_cli
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "My Repo"
    repo.mkdir()
    registry = Registry(config.data_dir() / "registry.db")
    try:
        assert index_cli.resolve_slug_for_path(registry, repo) == "my-repo"
    finally:
        registry.close()


def test_index_repo_rejects_same_slug_at_a_different_path(tmp_path, monkeypatch):
    """The guard must hold in the locked writer, not only in the MCP
    pre-flight -- otherwise it is pure TOCTOU."""
    from jarvis import index_cli
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    first = tmp_path / "a" / "app"
    first.mkdir(parents=True)
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("app", str(first), "python", None, "indexed")
    finally:
        registry.close()

    second = tmp_path / "b" / "app"
    second.mkdir(parents=True)
    shutil.copytree(FIXTURE_REPO, second, dirs_exist_ok=True)
    _init_git_repo(second)
    _stub_pipeline(monkeypatch)
    with pytest.raises(index_cli.IndexingError):
        index_cli.index_repo(second)


def test_ensure_git_repo_rejects_a_plain_directory(tmp_path):
    from jarvis import index_cli

    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(index_cli.NotAGitRepositoryError):
        index_cli.ensure_git_repo(plain)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_index_cli.py -q -k "resolve_slug or ensure_git_repo or same_slug_at_a_different_path"`
Expected: FAIL — `module 'jarvis.index_cli' has no attribute 'resolve_slug_for_path'`

- [ ] **Step 3: Extract `ensure_git_repo` from `_git_head`**

`_git_head` (lines 324-333) currently runs the `--is-inside-work-tree` check inline. Replace that block with a call, keeping the error message **byte-for-byte identical** so existing assertions still pass:

```python
def ensure_git_repo(repo_path: Path) -> None:
    """Raise `NotAGitRepositoryError` unless `repo_path` is inside a git work
    tree. Extracted from `_git_head` so a caller that only needs the check --
    the MCP `indexRepo` pre-flight -- does not also need a commit to exist."""
    check = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise NotAGitRepositoryError(
            f"{repo_path} is not a git repository "
            f"(git rev-parse --is-inside-work-tree: {check.stderr.strip()})"
        )
```

In `_git_head`, replace the inline `check = subprocess.run(...)` / `if check.returncode != 0: raise ...` block with `ensure_git_repo(repo_path)`, leaving its surrounding comment about "has no commits yet" intact.

- [ ] **Step 4: Add `resolve_slug_for_path`**

Place it immediately after `_reject_duplicate_slug_for_path` (line 1006), with the sibling guard:

```python
def _reject_slug_bound_to_another_path(
    registry: Registry, slug: str, repo_path: Path,
) -> None:
    """One path per slug -- the inverse of `_reject_duplicate_slug_for_path`,
    which enforces one slug per path and deliberately skips this direction.

    Without it, `/a/app` and `/b/app` both derive the slug `app` and the
    second silently overwrites the first's published index. That mattered
    little while every index was a deliberate `jarvis index` invocation; the
    MCP `indexRepo` path supplies paths and never chooses slugs, so it fires
    routinely.

    A registered path that no longer exists is a MOVE, not a collision --
    `upsert` already does `path=excluded.path` -- so rejection requires both
    paths to resolve. Mirrors the sibling guard's `except OSError: continue`.
    """
    existing = registry.get(slug)
    if existing is None:
        return
    try:
        registered = Path(existing.path).resolve(strict=True)
    except OSError:
        return
    if registered == repo_path:
        return
    raise IndexingError(
        f"slug {slug!r} is already bound to {registered}, not {repo_path}. "
        f"Index this repo under a different name with "
        f"`jarvis index {repo_path} --slug <name>`, or run "
        f"`jarvis forget {slug}` first."
    )


def resolve_slug_for_path(registry: Registry, repo_path: Path) -> str:
    """The slug an index run for `repo_path` should use.

    Resolution order matters: a repo registered under an explicit `--slug`
    must keep it, so a path lookup precedes basename derivation. Only a
    genuinely unregistered path falls through to `config.repo_slug`.
    """
    resolved = repo_path.resolve()
    for entry in registry.list():
        try:
            if Path(entry.path).resolve() == resolved:
                return entry.slug
        except OSError:
            continue
    slug = config.repo_slug(resolved.name)
    _reject_slug_bound_to_another_path(registry, slug, resolved)
    return slug
```

- [ ] **Step 5: Enforce it in the locked writer**

In `_index_repo_locked`, the existing guard block is:

```python
    try:
        _reject_duplicate_slug_for_path(registry, slug, repo_path)
    except Exception:
        registry.close()
        raise
```

Extend it so both directions are checked under the lock:

```python
    try:
        _reject_duplicate_slug_for_path(registry, slug, repo_path)
        _reject_slug_bound_to_another_path(registry, slug, repo_path)
    except Exception:
        registry.close()
        raise
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_cli.py -q -k "resolve_slug or ensure_git_repo or same_slug_at_a_different_path or git_head or duplicate"`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "fix(index): reject a slug already bound to another live path"
```

---

### Task 5: `jarvis forget` clears job files

**Files:**
- Modify: `src/jarvis/index_cli.py:1756-1764` (`_cmd_forget`)
- Test: `tests/test_index_cli.py`

**Interfaces:**
- Consumes: `jobs.clear_job_files` (Task 1).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

```python
def test_forget_removes_job_files(tmp_path, monkeypatch, capsys):
    """`jarvis forget` removes everything jarvis stored for a repo (D-06) --
    the lock, launch record, and index log included, so no footprint is left."""
    from jarvis import index_cli, jobs
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "app"
    repo.mkdir()
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("app", str(repo), "python", None, "indexed")
    finally:
        registry.close()

    jobs.write_launch_record("app", config.index_log("app"))
    config.index_log("app").write_text("stderr", encoding="utf-8")
    with jobs.build_lock("app"):
        pass

    parser = index_cli._build_parser()
    args = parser.parse_args(["forget", "app"])
    assert args.func(args) == 0

    assert not config.index_launchfile("app").exists()
    assert not config.index_log("app").exists()
    assert not config.index_lockfile("app").exists()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_index_cli.py -q -k forget_removes_job_files`
Expected: FAIL — `assert not True` on the launch record

- [ ] **Step 3: Clear the files**

In `_cmd_forget`, immediately after the existing `shutil.rmtree(config.swift_cache_dir(slug), ignore_errors=True)` line and before `print(f"forgot {slug}")`:

```python
    # D-06 continued: the build lock, launch record, and index log are jarvis
    # state too. Best-effort, like the sweeps above -- a cleanup failure must
    # not fail a forget.
    jobs.clear_job_files(slug)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_index_cli.py -q -k forget`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/jarvis/index_cli.py tests/test_index_cli.py
git commit -m "feat(index): clear job lock, launch record, and log on forget"
```

---

### Task 6: The `indexRepo` MCP tool

**Files:**
- Modify: `src/jarvis/server.py` (module docstring line 4-5; imports lines 10-24; new module state near line 29; new tool after `get_index_status`, ~line 497)
- Test: `tests/test_server_tools.py`

**Interfaces:**
- Consumes: `jobs.lock_state`, `jobs.write_launch_record` (Task 1); `index_cli.ensure_git_repo`, `index_cli.resolve_slug_for_path` (Task 4).
- Produces:
  - `server._launched: dict[str, subprocess.Popen]`
  - `server._jarvis_bin() -> str` (raises `RuntimeError`)
  - `server.index_repo_tool(path: str, semantic: bool = False, scip: bool | None = None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

First update two existing things at the top of `tests/test_server_tools.py`:
add `"indexRepo"` to the `EXPECTED_TOOLS` set (lines 24-34) and change the
module docstring's "the 9 tools" to "the 10 tools". Then append the tests
below.

An autouse fixture already sets `JARVIS_DATA_DIR` to `tmp_path` for every
test in this file; the explicit `setenv` calls below re-point it to a
subdirectory so these tests do not collide with the published fixture index
that fixture builds.

```python
def test_index_repo_tool_spawns_and_reports_starting(tmp_path, monkeypatch):
    from jarvis import config, jobs, server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "app"
    repo.mkdir()
    monkeypatch.setattr(server, "_jarvis_bin", lambda: "/fake/jarvis")
    monkeypatch.setattr(server.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(server.index_cli_module(), "ensure_git_repo", lambda p: None)

    captured: dict = {}

    class _Fake:
        pid = 999

        def poll(self):
            return None

    def _fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Fake()

    monkeypatch.setattr(server.subprocess, "Popen", _fake_popen)

    result = server.index_repo_tool(path=str(repo))

    assert result["repo"] == "app"
    assert result["state"] == "starting"
    assert result["pid"] == 999
    assert captured["argv"][:3] == ["/fake/jarvis", "index", str(repo.resolve())]
    assert "--no-semantic" in captured["argv"]
    # The record exists by the time the tool returns, so an immediate poll
    # can never see "nothing running".
    assert jobs.read_launch_record("app") is not None


def test_index_repo_tool_never_inherits_server_stdout(tmp_path, monkeypatch):
    """stdio IS the MCP transport: one inherited write corrupts the JSON-RPC
    stream and kills the session."""
    from jarvis import server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "app"
    repo.mkdir()
    monkeypatch.setattr(server, "_jarvis_bin", lambda: "/fake/jarvis")
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(server.index_cli_module(), "ensure_git_repo", lambda p: None)

    captured: dict = {}

    class _Fake:
        pid = 1
        def poll(self):
            return None

    monkeypatch.setattr(server.subprocess, "Popen",
                        lambda argv, **kw: (captured.update(kw), _Fake())[1])
    server.index_repo_tool(path=str(repo))

    assert captured["stdout"] is server.subprocess.DEVNULL
    assert captured["stdin"] is server.subprocess.DEVNULL
    assert captured["stderr"] is not None
    assert captured["stderr"] is not server.subprocess.DEVNULL
    assert captured["start_new_session"] is True
    assert captured["env"]["JARVIS_DATA_DIR"] == str(config.data_dir())


def test_index_repo_tool_semantic_true_omits_the_flag(tmp_path, monkeypatch):
    from jarvis import server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "app"
    repo.mkdir()
    monkeypatch.setattr(server, "_jarvis_bin", lambda: "/fake/jarvis")
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(server.index_cli_module(), "ensure_git_repo", lambda p: None)

    captured: dict = {}

    class _Fake:
        pid = 1
        def poll(self):
            return None

    monkeypatch.setattr(server.subprocess, "Popen",
                        lambda argv, **kw: (captured.update(argv=argv), _Fake())[1])
    server.index_repo_tool(path=str(repo), semantic=True, scip=False)

    assert "--no-semantic" not in captured["argv"]
    assert "--no-scip" in captured["argv"]


def test_index_repo_tool_preflight_missing_zoekt(tmp_path, monkeypatch):
    """TSI removed the language-toolchain blocker, not every binary: Stage 4
    Zoekt is still required, so say so instead of spawning a doomed child."""
    from jarvis import server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "app"
    repo.mkdir()
    monkeypatch.setattr(server, "_jarvis_bin", lambda: "/fake/jarvis")
    monkeypatch.setattr(server.shutil, "which",
                        lambda name: None if name == "zoekt-git-index" else f"/usr/bin/{name}")

    def _no_spawn(*a, **k):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(server.subprocess, "Popen", _no_spawn)
    result = server.index_repo_tool(path=str(repo))

    assert "zoekt-git-index" in result["error"]
    assert result["recovery"] == "sh setup.sh --only zoekt"


def test_index_repo_tool_preflight_not_a_git_repo(tmp_path, monkeypatch):
    from jarvis import index_cli, server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "plain"
    repo.mkdir()
    monkeypatch.setattr(server, "_jarvis_bin", lambda: "/fake/jarvis")
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _no_spawn(*a, **k):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(server.subprocess, "Popen", _no_spawn)
    result = server.index_repo_tool(path=str(repo))
    assert "not a git repository" in result["error"]


def test_index_repo_tool_adopts_a_live_job(tmp_path, monkeypatch):
    from jarvis import jobs, server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "app"
    repo.mkdir()
    monkeypatch.setattr(server, "_jarvis_bin", lambda: "/fake/jarvis")
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(server.index_cli_module(), "ensure_git_repo", lambda p: None)

    def _no_spawn(*a, **k):
        raise AssertionError("must not spawn a duplicate")

    monkeypatch.setattr(server.subprocess, "Popen", _no_spawn)
    with jobs.build_lock("app"):
        result = server.index_repo_tool(path=str(repo))

    assert result["alreadyRunning"] is True
    assert result["state"] == "running"


def test_index_repo_tool_reports_missing_binary(tmp_path, monkeypatch):
    from jarvis import server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    repo = tmp_path / "app"
    repo.mkdir()
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    result = server.index_repo_tool(path=str(repo))
    assert "jarvis" in result["error"]


@pytest.mark.anyio
async def test_index_repo_tool_is_registered():
    """Extends the file's existing EXPECTED_TOOLS convention rather than
    reaching into FastMCP internals."""
    async with create_connected_server_and_client_session(server.mcp) as client:
        listed = {tool.name for tool in (await client.list_tools()).tools}
    assert listed == EXPECTED_TOOLS
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_server_tools.py -q -k index_repo_tool`
Expected: FAIL — `module 'jarvis.server' has no attribute 'index_repo_tool'`

- [ ] **Step 3: Add imports and module state to `server.py`**

Update the module docstring's tool list (lines 4-5) to read `Registers 10 tools:` and append `indexRepo` to the enumeration.

Add to the imports (keeping alphabetical order within each group):

```python
import subprocess
import sys
from pathlib import Path
```

and extend the jarvis import at line 18:

```python
from jarvis import config, jobs, syntax, syntax_index
```

Add module state after `_graph_store` (line 29):

```python
# Handles for index children this server spawned. Retained so observation can
# reap them: `start_new_session=True` does not double-fork, so an exited child
# stays our zombie and `os.kill(pid, 0)` would report it alive forever. Lost
# on server restart by design -- `jobs.job_state` falls back to the launch
# record's age for that case.
_launched: dict[str, subprocess.Popen] = {}


def index_cli_module():
    """Deferred `index_cli` import. It pulls in the whole indexing pipeline,
    so the reader must not pay for it at module import; tests also
    monkeypatch through this seam."""
    from jarvis import index_cli

    return index_cli


def _jarvis_bin() -> str:
    """Absolute path to the `jarvis` console script.

    PATH is not reliable here: MCP clients spawn servers with sanitized
    environments, and `uv tool install` puts the script in a bin directory
    that may not be on it. The interpreter's own directory is correct for
    both venv and `uv tool` layouts, so try that first.
    """
    candidate = Path(sys.executable).parent / "jarvis"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("jarvis")
    if found is not None:
        return found
    raise RuntimeError(
        "the `jarvis` command could not be located next to this interpreter "
        f"({Path(sys.executable).parent}) or on PATH; reinstall jarvis-mcp"
    )
```

- [ ] **Step 4: Add the tool**

Insert after `get_index_status` (after line 497):

```python
@mcp.tool(name="indexRepo")
def index_repo_tool(path: str, semantic: bool = False,
                    scip: bool | None = None) -> dict[str, Any]:
    """Build an index for the git repository at `path`, so the other tools
    have something to read.

    Takes a filesystem `path` rather than a `repo` slug -- deliberately the
    only tool that does. Slug derivation is one-way, so the server cannot
    turn a slug into a path for a repo it has never indexed; `path` is
    exactly what the caller knows and the registry lacks. The returned `repo`
    is the slug every other tool accepts.

    Returns immediately with `state: "starting"`; the index runs in a
    detached child. Poll `getIndexStatus` until it reports `indexed: true` or
    a terminal `indexing.state` (`failed-at-startup`, `abandoned`) -- the
    loop always terminates.

    `semantic` defaults to False: the semantic stage loads embedding weights
    (a multi-gigabyte download on first use), which is not something a single
    tool call should trigger implicitly. `scip=None` leaves the repo's
    persisted SCIP choice alone.
    """
    try:
        return _spawn_index(path, semantic=semantic, scip=scip)
    except Exception as exc:  # Broad on purpose: the MCP boundary never raises.
        return {"error": str(exc)}


def _spawn_index(path: str, *, semantic: bool,
                 scip: bool | None) -> dict[str, Any]:
    """`indexRepo`'s body. Every pre-flight check runs before anything is
    spawned, so a caller gets an actionable error instead of a log file to go
    read."""
    index_cli = index_cli_module()
    jarvis_bin = _jarvis_bin()
    if shutil.which("zoekt-git-index") is None:
        # Stage 4 of the pipeline is required and fails the run without it
        # (index_cli.py's "Zoekt (required; failure fails the run)"). The
        # tree-sitter baseline needs no LANGUAGE toolchain, but it is not
        # binary-free.
        return {
            "error": "zoekt-git-index was not found on PATH; a first index "
                     "cannot be built without it",
            "recovery": "sh setup.sh --only zoekt",
        }
    resolved = Path(path).expanduser().resolve()
    index_cli.ensure_git_repo(resolved)

    registry = index_cli.Registry(config.data_dir() / "registry.db")
    try:
        slug = index_cli.resolve_slug_for_path(registry, resolved)
        entry = registry.get(slug)
    finally:
        registry.close()

    probe = jobs.lock_state(slug)
    if probe.held:
        return {"repo": slug, "path": str(resolved), "status": "indexing",
                "state": "running", "pid": probe.pid,
                "log": str(config.index_log(slug)), "alreadyRunning": True}

    flags: list[str] = []
    if not semantic:
        flags.append("--no-semantic")
    if scip is True:
        flags.append("--scip")
    elif scip is False:
        flags.append("--no-scip")

    log_path = config.index_log(slug)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Before Popen, never after: a fast child (tiny repo, warm caches) can
    # acquire the lock and register before the parent gets another turn, and
    # a post-spawn write would then recreate state nobody removes. The record
    # is written once and never mutated, so there is no handoff to serialize.
    jobs.write_launch_record(slug, log_path)

    log_file = open(log_path, "ab")
    try:
        child = subprocess.Popen(
            [jarvis_bin, "index", str(resolved), *flags],
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "JARVIS_DATA_DIR": str(config.data_dir())},
        )
    finally:
        log_file.close()  # the child keeps its own duplicated fd
    _launched[slug] = child

    payload: dict[str, Any] = {
        "repo": slug, "path": str(resolved), "status": "indexing",
        "state": "starting", "pid": child.pid, "log": str(log_path),
    }
    if entry is not None:
        payload["reindex"] = True
    return payload
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_server_tools.py -q -k index_repo_tool`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/server.py tests/test_server_tools.py
git commit -m "feat(server): add indexRepo tool spawning a detached index child"
```

---

### Task 7: `getIndexStatus` reports job state

**Files:**
- Modify: `src/jarvis/server.py:479-497` (`get_index_status`)
- Test: `tests/test_server_tools.py`

**Interfaces:**
- Consumes: `jobs.job_state`, `jobs.JobState` (Task 1); `server._launched` (Task 6).
- Produces: `server._indexing_fields(repo: str, indexed: bool) -> dict[str, Any]` — `{}` or `{"indexing": {...}}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_status_reports_starting_immediately_after_spawn(tmp_path, monkeypatch):
    """The regression that makes the whole poll contract usable: between
    Popen returning and the child registering there is no row and no lock, and
    a naive reading is 'not indexed, nothing running' -- so the agent spawns
    again, forever."""
    from jarvis import config, jobs, server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    jobs.write_launch_record("app", config.index_log("app"))

    class _Live:
        pid = 4242
        def poll(self):
            return None

    monkeypatch.setitem(server._launched, "app", _Live())
    monkeypatch.setattr(server, "_service", lambda: _NotIndexedStub())

    result = server.get_index_status(repo="app")
    assert result["indexed"] is False
    assert result["indexing"]["state"] == "starting"
    assert result["indexing"]["pid"] == 4242


def test_status_reports_failed_at_startup_for_an_unreaped_child(tmp_path, monkeypatch):
    """A real child that exited but was never waited on is a zombie whose pid
    still answers os.kill(pid, 0). Observation must reap instead, or the loop
    sits in 'starting' forever. Cannot be caught with a mocked Popen."""
    import subprocess
    import sys

    from jarvis import config, jobs, server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    jobs.write_launch_record("app", config.index_log("app"))
    child = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    while child.poll() is None:  # let it actually exit, but do not reap it yet
        pass
    child.returncode = None  # simulate a not-yet-reaped handle
    monkeypatch.setitem(server._launched, "app", child)
    monkeypatch.setattr(server, "_service", lambda: _NotIndexedStub())

    result = server.get_index_status(repo="app")
    assert result["indexing"]["state"] == "failed-at-startup"
    assert result["indexing"]["exitCode"] == 3
    assert result["indexing"]["log"].endswith("index-app.log")


def test_status_reports_running_when_the_child_holds_the_lock(tmp_path, monkeypatch):
    """Child-ready-before-parent-returns: the child got there first. The
    launch record must be exactly what the parent wrote, proving there is no
    post-spawn mutation to interleave with."""
    from jarvis import config, jobs, server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    jobs.write_launch_record("app", config.index_log("app"))
    before = config.index_launchfile("app").read_bytes()
    monkeypatch.setattr(server, "_service", lambda: _NotIndexedStub())

    with jobs.build_lock("app"):
        result = server.get_index_status(repo="app")

    assert result["indexing"]["state"] == "running"
    assert config.index_launchfile("app").read_bytes() == before


def test_status_leftover_record_does_not_mask_later_states(tmp_path, monkeypatch):
    """Rows 2-4 precede the record, which is never deleted."""
    from jarvis import config, jobs, server
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    jobs.write_launch_record("app", config.index_log("app"))

    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.upsert("app", "/tmp/app", "python", None, "indexing")
    finally:
        registry.close()
    monkeypatch.setattr(server, "_service", lambda: _NotIndexedStub())
    assert server.get_index_status(repo="app")["indexing"]["state"] == "abandoned"

    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.mark_status("app", "failed")
    finally:
        registry.close()
    assert "indexing" not in server.get_index_status(repo="app")


def test_status_omits_indexing_when_nothing_in_flight(tmp_path, monkeypatch):
    from jarvis import server

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(server, "_service", lambda: _NotIndexedStub())
    assert "indexing" not in server.get_index_status(repo="app")
```

Add this stub next to the other test helpers in the file:

```python
class _NotIndexedStub:
    """A QueryService whose repo has no published index."""

    def get_index_status(self, repo: str, repo_path: str | None = None):
        return False, None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_server_tools.py -q -k "status_reports or leftover_record or omits_indexing"`
Expected: FAIL — `KeyError: 'indexing'`

- [ ] **Step 3: Add `_indexing_fields`**

Insert before `get_index_status` in `server.py`:

```python
def _indexing_fields(repo: str, indexed: bool) -> dict[str, Any]:
    """The `indexing` block, or nothing when no run is in flight.

    Reaping happens inside `jobs.job_state` via the retained handle: a
    tracked child's exit code is stronger evidence than any liveness check,
    and reaping is what stops an exited-but-unwaited child from reading as
    alive forever.

    Never raises: a status response must survive a bug in here.
    """
    try:
        state = jobs.job_state(
            repo, indexed=indexed, registry_status=_registry_status(repo),
            child=_launched.get(repo),
        )
    except Exception:  # Broad on purpose, same contract as the helpers above.
        return {}
    if state is None:
        return {}
    block: dict[str, Any] = {"state": state.state}
    if state.pid is not None:
        block["pid"] = state.pid
    if state.exit_code is not None:
        block["exitCode"] = state.exit_code
    if state.log is not None:
        block["log"] = state.log
    return {"indexing": block}
```

- [ ] **Step 4: Wire it into `get_index_status`**

Extend the tool's docstring with:

```
    While an index is being built, an `indexing` block reports its state:
    `starting`, `running`, `failed-at-startup`, or `abandoned`. The first two
    mean keep polling; the last two are terminal.
```

and change the return statement to include the new fields:

```python
    return {"repo": repo, "indexed": indexed, "status": _registry_status(repo),
            **_freshness_fields(freshness), **_search_coverage_fields(repo),
            **_indexing_fields(repo, indexed), **capability_fields}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_server_tools.py -q -k "status_reports or leftover_record or omits_indexing or index_repo_tool"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/server.py tests/test_server_tools.py
git commit -m "feat(server): report in-flight index state from getIndexStatus"
```

---

### Task 8: Make the missing-index error agent-actionable

**Files:**
- Modify: `src/jarvis/server.py:319-334` (`_error_payload`'s `IndexNotFoundError` branch)
- Test: `tests/test_server_tools.py`

**Interfaces:**
- Consumes: `index_repo_tool`'s name (Task 6) — as the literal string `"indexRepo"`.
- Produces: `recoveryTool` and `recoveryToolArgs` keys on `IndexNotFoundError` payloads.

**Why:** today the structured keys appear only when `origin_of(entry)` is non-None — i.e. only when a registry row exists and explains itself. A never-indexed repo has no row and gets bare `{"error": ...}`, which is exactly the case this feature serves.

- [ ] **Step 1: Write the failing tests**

```python
def test_missing_index_names_the_recovery_tool_without_a_row(tmp_path, monkeypatch):
    """The row-less case is the one that matters: a never-indexed repo has no
    registry row, so the pre-existing structured keys never appeared."""
    from jarvis import server
    from jarvis.index_reader import IndexNotFoundError

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    payload = server._error_payload("absent", IndexNotFoundError("no published index"))

    assert payload["recoveryTool"] == "indexRepo"
    assert "path" in payload["recoveryToolArgs"]
    assert "state" not in payload  # no row to explain anything


def test_missing_index_keeps_row_explanation_and_adds_the_tool(tmp_path, monkeypatch):
    from jarvis import config, server
    from jarvis.index_reader import IndexNotFoundError
    from jarvis.registry import Registry

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = Registry(config.data_dir() / "registry.db")
    try:
        registry.record_failure("app", "/tmp/app", "python",
                                origin="manual", reason="boom", stderr="trace")
    finally:
        registry.close()

    payload = server._error_payload("app", IndexNotFoundError("no published index"))

    assert payload["error"] == "no published index"
    assert payload["cause"] == "boom"
    assert payload["recovery"]  # prose recovery preserved
    assert payload["recoveryTool"] == "indexRepo"
    assert "status_stderr" not in payload
    assert "trace" not in str(payload)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_server_tools.py -q -k missing_index`
Expected: FAIL — `KeyError: 'recoveryTool'`

- [ ] **Step 3: Extend the branch**

Replace the `IndexNotFoundError` branch body so the tool keys are added unconditionally, keeping the row-derived keys exactly as they were:

```python
    if isinstance(exc, IndexNotFoundError):
        # Spec §12: the old search-only explanation branch is gone -- no
        # writer produces that status, and capability facts come from the
        # snapshot, not the row. The error passes through; rows that do
        # explain themselves gain the structured keys below.
        #
        # `recoveryTool` is emitted whether or not a row exists -- and a
        # never-indexed repo has no row, which is precisely the case an agent
        # hits first. The prose `recovery` above it names a shell command only
        # a human can run; this names a tool the caller can invoke itself.
        # `path` cannot be filled in here: slug -> path needs a registry row.
        entry = _registry_entry(repo)
        payload = {"error": str(exc)}
        origin = origin_of(entry) if entry is not None else None
        if origin is not None:
            payload["state"] = origin
            if entry.status_reason:
                payload["cause"] = entry.status_reason
            recovery = recovery_for(entry)
            if recovery is not None:
                payload["recovery"] = recovery
        payload["recoveryTool"] = "indexRepo"
        payload["recoveryToolArgs"] = {
            "path": "<the repo's local git working directory>"
        }
        return payload
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_server_tools.py -q -k "missing_index or error_payload"`
Expected: PASS

- [ ] **Step 5: Check the neighbouring assertions**

Run: `uv run pytest tests/test_server_tools.py -q`
Expected: PASS. If any pre-existing test asserts an exact payload dict for `IndexNotFoundError` (`assert payload == {...}`), update it to include the two new keys — the additive contract only promises that *prose-only* clients keep working, not that the dict is closed.

- [ ] **Step 6: Commit**

```bash
git add src/jarvis/server.py tests/test_server_tools.py
git commit -m "feat(server): name indexRepo as recovery on a missing index"
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md` (the 9-tool reference table and its count)
- Modify: `docs/codebase-summary.md` (module index — add `jobs.py`)
- Modify: `docs/project-roadmap.md` (append a dated entry)
- Test: none (documentation)

- [ ] **Step 1: Update the README tool table**

Change every "9 tools" occurrence to "10 tools" and add a row:

```markdown
| `indexRepo` | Build an index for a git repo at `path` so the other tools have something to read. Returns immediately; poll `getIndexStatus`. `semantic` defaults to false. |
```

Document the two new flags in the configuration section:

```markdown
`jarvis index --no-semantic` skips the semantic (vector) stage even when the
`semantic` extra is installed. The MCP `indexRepo` tool passes it by default,
so an agent tool call never implicitly downloads embedding weights.
```

Do **not** touch the `<!-- mcp-name: io.github.jarvis-intelligence/jarvis -->` marker on line 3 — `publish-pypi.yml`'s preflight hard-fails without it.

- [ ] **Step 2: Update `docs/codebase-summary.md`**

Add `jobs.py` to the module index, described as: *in-flight index-run coordination — per-slug `flock` build lock, single-writer launch record, ordered job-state derivation; shared by the CLI writer and the MCP reader.*

- [ ] **Step 3: Append a roadmap entry**

```markdown
### 2026-09-10 — Server-side auto-index

`indexRepo` MCP tool: an agent can now bootstrap a missing index itself
instead of receiving prose only a human can act on. Spawns `jarvis index` as
a detached child, reports progress through `getIndexStatus`'s new `indexing`
block, and terminates the poll loop in every failure mode. Adds a per-slug
`flock` build lock covering registry and artifact writes for CLI, watch, and
MCP writers alike, plus `--no-semantic`. Fixes a pre-existing hole where two
repos with the same basename silently overwrote each other's index.
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/codebase-summary.md docs/project-roadmap.md
git commit -m "docs: document indexRepo, --no-semantic, and the jobs module"
```

---

### Task 10: Full verification

**Files:** none modified.

- [ ] **Step 1: Run the unit suite (the CI gate)**

Run: `uv run pytest -m "not integration" -rs`
Expected: PASS. Investigate any failure in `test_index_cli.py` first — Task 3 restructured `index_repo`'s prologue, so a test that monkeypatched into its body is the likeliest breakage.

- [ ] **Step 2: Verify version lockstep is untouched**

Run: `uv run python scripts/check_versions.py`
Expected: prints `versions consistent`. This change ships no version bump — releases are a separate runbook (`.claude/skills/jarvis-release/SKILL.md`).

- [ ] **Step 3: Smoke test the real path end to end**

With `zoekt-git-index` on PATH, run the tool against a real throwaway repo and confirm the loop terminates:

```bash
uv run python - <<'PY'
import subprocess, sys, tempfile, time
from pathlib import Path
from jarvis import server

repo = Path(tempfile.mkdtemp()) / "smoke"
repo.mkdir()
(repo / "a.py").write_text("def f():\n    return 1\n")
subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
subprocess.run(["git", "add", "."], cwd=repo, check=True)
subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "init"], cwd=repo, check=True)

print(server.index_repo_tool(path=str(repo)))
for _ in range(120):
    status = server.get_index_status(repo="smoke")
    block = status.get("indexing")
    print(status["indexed"], block)
    if status["indexed"] or (block and block["state"] in
                             {"failed-at-startup", "abandoned"}):
        break
    time.sleep(1)
else:
    sys.exit("poll loop did not terminate")
PY
```

Expected: `indexRepo` returns `state: "starting"`, subsequent polls show `starting` then `running`, and the loop exits on `indexed: True`. A terminal `failed-at-startup` is also an acceptable *pass for the loop contract* — read the reported `log` path to diagnose the index itself.

- [ ] **Step 4: Confirm the stdio invariant on the real child**

Run the smoke script above with the server's stdout captured, and confirm the captured stream contains no `indexed smoke` line — the child's stdout must be `DEVNULL`, never inherited. Any indexer output appearing on the parent's stdout would corrupt a live JSON-RPC session.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: address verification findings from server-side auto-index"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §5 Tool surface | 6 |
| §6 Slug resolution | 4 |
| §7 Exclusion (flock) | 1, 3 |
| §7 Launch record + exit observation | 1, 6, 7 |
| §7 `jarvis forget` cleanup | 5 |
| §8 Agent-actionable error payload | 8 |
| §9 Poll contract (ordered evaluation) | 1 (`job_state`), 7 (surfacing) |
| §10 Execution (stdio, binary resolution, env, detach) | 6 |
| §10 New CLI surface (`--no-semantic`) | 2 |
| §11 Failure modes | 1, 3, 4, 6, 7 |
| §12 Testing | every task's test steps |
| §13 Non-goals | none — deliberately unimplemented |

**Type consistency:** `jobs.job_state(slug, *, indexed, registry_status, child, root, now)` is called from `server._indexing_fields` with `indexed` and `registry_status` (Task 7) matching Task 1's signature. `JobState.exit_code` (snake, Python) is surfaced as `exitCode` (camel, MCP JSON) only in `_indexing_fields` — the same boundary convention as the `@mcp.tool(name="camelCase")` names. `index_cli.resolve_slug_for_path(registry, repo_path)` is called with exactly that argument order in both Task 4's writer and Task 6's pre-flight. `_index_repo_locked`'s keyword names match `index_repo`'s call site in Task 3.

**Known deviation from the spec, resolved here:** the spec's §5 payload example shows `pid` from the launch record; Task 1 deliberately omits `pid` from the record's two fields (`started_at`, `log`) because nothing branches on it, and Task 6 reports the pid from the live `Popen` handle instead. Same observable payload, one fewer mutable field.
