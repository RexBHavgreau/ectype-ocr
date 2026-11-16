"""Stopping a run: the Stop button sets a flag; the work checks it at safe points (between
pages, between training steps) and raises Stopped, which ends the run without a failure.
Dependency-free."""

from __future__ import annotations

import subprocess
import threading
from contextlib import contextmanager


class Stopped(BaseException):
    """The person pressed Stop. Not an Exception (like KeyboardInterrupt), so code that catches
    errors to carry on or report them can't swallow it."""


class StopFlag:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Stopped("Stopped by you.")


# The flags of runs in progress, so code far from the button (a training step, a tool's run)
# can look without being passed the flag.
_active: list[StopFlag] = []
_lock = threading.Lock()


@contextmanager
def active(flag: StopFlag | None):
    """While the block runs, `check()` and `watch()` follow this flag."""
    if flag is None:
        yield
        return
    with _lock:
        _active.append(flag)
    try:
        yield
    finally:
        with _lock:
            _active.remove(flag)


def requested() -> bool:
    with _lock:
        return any(f.requested for f in _active)


def check() -> None:
    """Raise Stopped if a run in progress was told to stop."""
    if requested():
        raise Stopped("Stopped by you.")


def watch(proc: subprocess.Popen, interval: float = 0.3) -> threading.Event:
    """Kill the process when a stop is requested while it runs. Set the returned event when
    the process is done with (or just let it end: the watcher also stops when it exits)."""
    done = threading.Event()

    def look() -> None:
        while not done.wait(interval):
            if proc.poll() is not None:
                return
            if requested():
                try:
                    proc.kill()
                except OSError:
                    pass
                return

    threading.Thread(target=look, daemon=True).start()
    return done


class Ticker:
    """Progress and Stop for a long loop of many small items: `step()` after each item checks the
    active run's Stop (cheap, every item) and tells `progress(done, total, what)` at most once
    every `every` seconds, and always at the end. `progress` may be None."""

    def __init__(self, progress, what: str, total: int, every: float = 1.0) -> None:
        import time

        self._time = time.monotonic
        self.progress, self.what, self.total, self.every = progress, what, total, every
        self.done = 0
        self._sent = -1
        self._last = self._time()

    def step(self, n: int = 1) -> None:
        check()
        self.done += n
        if self.progress is not None and (self.done >= self.total or self._time() - self._last >= self.every):
            self._send()

    def finish(self) -> None:
        """The phase is over: report it complete (once)."""
        check()
        self.done = self.total
        if self.progress is not None and self._sent != self.total:
            self._send()

    def _send(self) -> None:
        if self.done != self._sent:
            self._sent = self.done
            self._last = self._time()
            self.progress(min(self.done, self.total), self.total, self.what)
