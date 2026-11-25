"""Bounded, cancellable hand-off from discovery to a single resource-owning worker."""
from __future__ import annotations

import queue
import threading
from typing import Callable, Generic, Iterator, TypeVar

from .common import Cancelled, Control

T = TypeVar('T')


class BoundedWorker(Generic[T]):
    def __init__(self, control: Control, consume: Callable[[Iterator[T]], None], *, capacity: int = 64):
        self.control = control
        self.consume = consume
        self.pending: queue.Queue[T] = queue.Queue(maxsize=max(1, capacity))
        self.finished = threading.Event()
        self.thread: threading.Thread | None = None
        self.failure: BaseException | None = None

    def _items(self) -> Iterator[T]:
        while True:
            self.control.checkpoint()
            try:
                yield self.pending.get(timeout=0.1)
            except queue.Empty:
                if self.finished.is_set() and self.pending.empty():
                    return

    def _run(self) -> None:
        try:
            self.consume(self._items())
        except BaseException as exc:
            self.failure = exc
            self.control.stop()

    def submit(self, item: T) -> None:
        self.control.checkpoint()
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='ofdl-media', daemon=True)
            self.thread.start()
        while True:
            self.control.checkpoint()
            try:
                self.pending.put(item, timeout=0.1)
                return
            except queue.Full:
                pass

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if kind is not None:
            self.control.stop()
        self.finished.set()
        if self.thread is not None:
            self.thread.join()
        if self.failure is not None and (error is None or isinstance(error, Cancelled)):
            raise self.failure
        return False
