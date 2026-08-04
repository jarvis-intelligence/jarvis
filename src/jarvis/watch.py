"""`jarvis watch`: debounced auto-reindex on file changes.

`Debouncer` is pure and thread-free — driven externally by `notify()` (call
on every relevant filesystem event) and `poll()` (call periodically; fires
`on_fire()` at most once per burst of `notify()` calls, once `delay_seconds`
have elapsed since the LAST one). Kept free of `watchdog`/threading so the
debounce guarantee is unit-testable with a fake clock; the real `watch`
subcommand wraps it with a `watchdog` `Observer` + a background poll loop
(see `index_cli.py`'s `_cmd_watch`), which is exercised by manual acceptance
only, per the phase plan's own risk note on watcher/editor reliability.
"""

from __future__ import annotations

import time
from collections.abc import Callable

_IGNORED_PATH_PARTS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}


def should_ignore_path(path: str) -> bool:
    """True if any path component is a vendor/VCS directory this watcher
    should never trigger a reindex for."""
    return any(part in _IGNORED_PATH_PARTS for part in path.split("/"))


class Debouncer:
    def __init__(
        self,
        delay_seconds: float,
        on_fire: Callable[[], None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._delay = delay_seconds
        self._on_fire = on_fire
        self._clock = clock
        self._last_notify: float | None = None
        self._fired_for: float | None = None

    def notify(self) -> None:
        self._last_notify = self._clock()

    def poll(self) -> bool:
        """Call periodically. Fires `on_fire()` and returns `True` at most
        once per `notify()` burst, once `delay_seconds` has elapsed since
        the last `notify()`; returns `False` otherwise (nothing pending, or
        already fired for the current burst)."""
        if self._last_notify is None or self._last_notify == self._fired_for:
            return False
        if self._clock() - self._last_notify >= self._delay:
            self._fired_for = self._last_notify
            self._on_fire()
            return True
        return False
