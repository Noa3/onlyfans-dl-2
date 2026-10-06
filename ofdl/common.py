"""Thread-safe cooperative cancellation and user-safe errors."""
from __future__ import annotations
import threading
import time
from typing import Any, Callable

EventSink = Callable[[str, Any], None]


def no_events(kind: str, value: Any) -> None:
    pass


class AppError(Exception):
    """Only deliberately sanitized messages may be put in this exception."""


class Cancelled(AppError):
    pass


class Control:
    def __init__(self) -> None:
        self.stopped = threading.Event()
        self.paused = threading.Event()

    def stop(self) -> None:
        self.stopped.set()

    def toggle_pause(self) -> bool:
        if self.paused.is_set():
            self.paused.clear()
        else:
            self.paused.set()
        return self.paused.is_set()

    def checkpoint(self) -> None:
        while self.paused.is_set() and not self.stopped.is_set():
            self.stopped.wait(0.1)
        if self.stopped.is_set():
            raise Cancelled('Stopped. Completed files are kept; eligible partial files can resume on the next run.')

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self.checkpoint()
            left = deadline - time.monotonic()
            if left <= 0:
                return
            self.stopped.wait(min(left, 0.1))
