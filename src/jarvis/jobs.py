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
  the spawn, never mutated, never read or removed by the child. Writing each
  record exactly once is the deliberate part: an earlier design mutated the
  record from both sides and had an unavoidable check-then-write race in
  both directions. Several parents may write records for the same slug
  concurrently, so each write publishes through its own unique temp file.

Liveness of a spawned-but-unregistered child comes from **reaping**, never
from `os.kill(pid, 0)`: `start_new_session=True` does not double-fork, so an
exited-but-unreaped child is a zombie whose pid still answers "alive".
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
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
    BEFORE `Popen`, so a fast child can never observe a state where it has
    registered but no record exists.

    Each record is written once and never mutated -- that, not writer count,
    is the property that removes the check-then-write race. Concurrent
    `indexRepo` calls for the same slug genuinely can both land here before
    either child takes the build lock, so the temp name MUST be unique per
    write: a shared `<slug>.launch.tmp` lets one caller rename the other's
    temp file out from under it and the loser's `os.replace` then raises
    FileNotFoundError. With unique temps both renames succeed and the later
    one wins, which is correct -- either record describes a live attempt.
    """
    path = config.index_launchfile(slug, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "started_at": datetime.now(UTC).isoformat(),
        "log": str(log_path),
    })
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp",
                                    dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


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
    registry_updated_at: datetime | None = None,
    child: _Reapable | None = None, root: Path | None = None,
    now: datetime | None = None,
) -> JobState | None:
    """The spec's section 9 ordered evaluation; first match wins. `None` means
    "no index run is in flight" -- the caller's other fields already say
    whether one succeeded or failed.

    Attempt correlation is what the ordering turns on. A launch record newer
    than the registry row describes the CURRENT attempt, and must be believed
    over the row: otherwise a repo whose previous run failed reports `None`
    (nothing in flight) while a freshly spawned child is still starting, and
    the agent spawns again forever. Symmetrically, a row left at `indexing`
    by an earlier abandoned run must not report `abandoned` for a new
    attempt.

    Once the child registers, `upsert` stamps `last_indexed` later than the
    record, so the record stops being current and the row's own state governs
    again -- which is exactly when `abandoned` becomes the right answer.

    Callers must pass `child` rather than pre-reaping it; this calls `poll()`.
    """
    probe = lock_state(slug, root=root)
    if probe.held:  # row 1
        return JobState(state="running", pid=probe.pid, exit_code=None,
                        log=str(config.index_log(slug, root)))
    record = read_launch_record(slug, root=root)
    log = record.log if record is not None else str(config.index_log(slug, root))
    current_attempt = record is not None and (
        registry_updated_at is None or record.started_at > registry_updated_at
    )
    if current_attempt:  # rows 5-8, promoted above the stale row state
        if child is not None:
            code = child.poll()
            state = "starting" if code is None else "failed-at-startup"
            return JobState(state=state, pid=child.pid, exit_code=code, log=log)
        age = ((now or datetime.now(UTC)) - record.started_at).total_seconds()
        if age < STARTUP_GRACE_SECONDS:
            return JobState(state="starting", pid=None, exit_code=None, log=log)
        return JobState(state="failed-at-startup", pid=None,
                        exit_code=None, log=log)
    if indexed or registry_status == "failed":  # rows 2, 3
        return None
    if registry_status == "indexing":  # row 4
        return JobState(state="abandoned", pid=None, exit_code=None, log=log)
    return None  # row 9


def clear_job_files(slug: str, *, root: Path | None = None) -> None:
    """Remove a slug's launch record and index log (`jarvis forget`).

    Deliberately does NOT unlink the build lock. Removing a lock file another
    process may have already opened but not yet flocked lets the next writer
    create a fresh inode and lock that instead, so two writers would each
    believe they hold the lock for the same repo. The empty lock file is a
    trivial footprint; a broken mutex is not. Callers that are about to
    destroy a repo's data must HOLD the lock while doing so -- see
    `_cmd_forget`.

    Best-effort: forgetting a repo must not fail over a cleanup step.
    """
    for path in (config.index_launchfile(slug, root),
                 config.index_log(slug, root)):
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
