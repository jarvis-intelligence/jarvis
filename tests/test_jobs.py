"""Tests for jobs.py: the per-slug build lock (flock), the write-once launch
record, and the ordered job-state derivation of the spec's section 9."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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
    """The record is write-once and immutable: exactly two fields, so there
    is nothing for any party to check-then-mutate."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    raw = json.loads(config.index_launchfile("app").read_text(encoding="utf-8"))
    assert set(raw) == {"started_at", "log"}


def test_concurrent_launch_record_writes_both_succeed(tmp_path, monkeypatch):
    """Two indexRepo calls for the same slug genuinely reach this before
    either child takes the build lock. A shared temp name lets one rename
    the other's temp out from under it; the loser's os.replace then raises."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def _writer(n: int) -> None:
        try:
            start.wait()
            for _ in range(25):
                jobs.write_launch_record("app", Path(f"/tmp/{n}.log"))
        except BaseException as exc:  # noqa: BLE001 - recorded and re-asserted
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert jobs.read_launch_record("app") is not None
    assert list(config.index_launchfile("app").parent.glob("*.tmp")) == []


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
    """A record left over from the run that PUBLISHED is older than the row
    it wrote, so it is not the current attempt. Omitting
    `registry_updated_at` here would make the record look current and report
    `starting` -- verified by running this file, not by inspection."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    published_at = datetime.now(UTC) + timedelta(seconds=5)
    assert jobs.job_state("app", indexed=True, registry_status="indexed",
                          registry_updated_at=published_at) is None


def test_job_state_none_when_failed(tmp_path, monkeypatch):
    """A stale record cannot resurrect a settled failure: the row is newer."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    later = datetime.now(UTC) + timedelta(seconds=5)
    assert jobs.job_state("app", indexed=False, registry_status="failed",
                          registry_updated_at=later) is None


def test_job_state_abandoned_when_row_indexing_and_lock_free(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    state = jobs.job_state("app", indexed=False, registry_status="indexing")
    assert state.state == "abandoned"


def test_job_state_retry_after_a_failed_run_reports_starting(tmp_path, monkeypatch):
    """The regression that silently breaks retries: a repo whose previous run
    failed has a `failed` row, and a freshly spawned child has not registered
    yet. Reading the stale row first reports 'nothing in flight', so the agent
    spawns again forever."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    earlier = datetime.now(UTC) - timedelta(hours=1)
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    state = jobs.job_state("app", indexed=False, registry_status="failed",
                           registry_updated_at=earlier,
                           child=_FakeChild(4242, None))
    assert (state.state, state.pid) == ("starting", 4242)


def test_job_state_retry_after_an_abandoned_run_reports_starting(tmp_path, monkeypatch):
    """Same defect, other stale value: an `indexing` row left by an earlier
    killed run must not terminate a new attempt's poll loop as 'abandoned'."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    earlier = datetime.now(UTC) - timedelta(hours=1)
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    state = jobs.job_state("app", indexed=False, registry_status="indexing",
                           registry_updated_at=earlier,
                           child=_FakeChild(4242, None))
    assert state.state == "starting"


def test_job_state_reindex_of_a_published_repo_reports_starting(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    earlier = datetime.now(UTC) - timedelta(hours=1)
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    state = jobs.job_state("app", indexed=True, registry_status="indexed",
                           registry_updated_at=earlier,
                           child=_FakeChild(4242, None))
    assert state.state == "starting"


def test_job_state_abandoned_once_the_child_registered_then_died(tmp_path, monkeypatch):
    """After the child registers, `upsert` stamps `last_indexed` later than
    the record, so the record stops being current and the row governs -- which
    is exactly when `abandoned` is the right answer."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    registered_at = datetime.now(UTC) + timedelta(seconds=5)
    state = jobs.job_state("app", indexed=False, registry_status="indexing",
                           registry_updated_at=registered_at)
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


def _exited_but_unreaped_child() -> subprocess.Popen:
    """A real child that has exited and has NEVER been polled.

    Waiting must not reap: `poll()` calls waitpid, which both reaps and sets
    returncode, so any spin on `poll()` destroys the very state under test.
    Worse, a later `poll()` on an already-reaped handle returns 0 -- CPython
    swallows ECHILD as exit 0 -- so a test that spins on `poll()` and then
    resets `returncode` asserts against a fabricated 0, not the real code.
    Reading the child's stdout to EOF proves it exited (the pipe closes when
    it does) without touching waitpid.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.write('up'); "
                               "sys.stdout.flush(); raise SystemExit(3)"],
        stdout=subprocess.PIPE,
    )
    assert child.stdout.read() == b"up"  # EOF => process gone, still unreaped
    child.stdout.close()
    assert child.returncode is None  # never polled
    return child


def test_job_state_reaps_an_exited_unpolled_child(tmp_path, monkeypatch):
    """The zombie regression, at the level that owns the reaping.
    `start_new_session=True` does not double-fork, so an exited child stays
    our unreaped zombie whose pid still answers `os.kill(pid, 0)`.
    Observation must reap to learn the truth."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    child = _exited_but_unreaped_child()
    try:
        assert os.kill(child.pid, 0) is None  # a zombie still "exists"
        state = jobs.job_state("app", indexed=False, registry_status=None,
                               child=child)
        assert state.state == "failed-at-startup"
        assert state.exit_code == 3
    finally:
        child.wait()


def test_clear_job_files_preserves_the_lock_inode(tmp_path, monkeypatch):
    """Unlinking a lock another process may have opened but not yet flocked
    lets the next writer lock a fresh inode instead -- two writers, one repo.
    The empty file is a trivial footprint; a broken mutex is not."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    jobs.write_launch_record("app", Path("/tmp/app.log"))
    config.index_log("app").parent.mkdir(parents=True, exist_ok=True)
    config.index_log("app").write_text("x", encoding="utf-8")
    with jobs.build_lock("app"):
        pass
    inode = config.index_lockfile("app").stat().st_ino

    jobs.clear_job_files("app")

    assert not config.index_launchfile("app").exists()
    assert not config.index_log("app").exists()
    assert config.index_lockfile("app").stat().st_ino == inode
