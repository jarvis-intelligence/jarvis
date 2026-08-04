"""Debounce logic for `jarvis watch` — pure, thread-free, driven by an
injectable clock so a burst of file-change notifications is unit-testable
without any real waiting or a real filesystem watcher."""

from __future__ import annotations

from jarvis.watch import Debouncer


def test_debouncer_does_not_fire_before_delay_elapses():
    fired: list[None] = []
    clock = [0.0]
    d = Debouncer(delay_seconds=5.0, on_fire=lambda: fired.append(None), clock=lambda: clock[0])

    d.notify()
    clock[0] = 4.9
    assert d.poll() is False
    assert fired == []


def test_debouncer_fires_once_delay_elapses_since_last_notify():
    fired: list[None] = []
    clock = [0.0]
    d = Debouncer(delay_seconds=5.0, on_fire=lambda: fired.append(None), clock=lambda: clock[0])

    d.notify()
    clock[0] = 5.0
    assert d.poll() is True
    assert fired == [None]


def test_debouncer_coalesces_a_burst_into_a_single_fire():
    """The core debounce guarantee: repeated notify() calls during the
    delay window reset the clock, so a burst of edits (e.g. an editor's
    atomic save touching several files) produces exactly one reindex."""
    fired: list[None] = []
    clock = [0.0]
    d = Debouncer(delay_seconds=5.0, on_fire=lambda: fired.append(None), clock=lambda: clock[0])

    d.notify()
    clock[0] = 3.0
    d.notify()  # resets the quiet-period clock
    clock[0] = 7.0  # 5s since notify() at t=0, but only 4s since t=3
    assert d.poll() is False
    assert fired == []

    clock[0] = 8.0  # now 5s since the t=3 notify()
    assert d.poll() is True
    assert fired == [None]


def test_debouncer_does_not_refire_after_already_firing():
    fired: list[None] = []
    clock = [0.0]
    d = Debouncer(delay_seconds=5.0, on_fire=lambda: fired.append(None), clock=lambda: clock[0])

    d.notify()
    clock[0] = 5.0
    assert d.poll() is True
    clock[0] = 100.0
    assert d.poll() is False  # no new notify() since the last fire
    assert fired == [None]


def test_debouncer_fires_again_after_a_new_notify_post_fire():
    fired: list[None] = []
    clock = [0.0]
    d = Debouncer(delay_seconds=5.0, on_fire=lambda: fired.append(None), clock=lambda: clock[0])

    d.notify()
    clock[0] = 5.0
    d.poll()
    assert fired == [None]

    clock[0] = 10.0
    d.notify()
    clock[0] = 15.0
    assert d.poll() is True
    assert fired == [None, None]


def test_poll_before_any_notify_never_fires():
    fired: list[None] = []
    clock = [0.0]
    d = Debouncer(delay_seconds=5.0, on_fire=lambda: fired.append(None), clock=lambda: clock[0])
    clock[0] = 1000.0
    assert d.poll() is False
    assert fired == []


def test_should_ignore_path_skips_vendor_and_git_dirs():
    from jarvis.watch import should_ignore_path

    assert should_ignore_path("/repo/.git/index") is True
    assert should_ignore_path("/repo/node_modules/pkg/index.js") is True
    assert should_ignore_path("/repo/.venv/lib/foo.py") is True
    assert should_ignore_path("/repo/__pycache__/foo.pyc") is True
    assert should_ignore_path("/repo/src/main.py") is False
