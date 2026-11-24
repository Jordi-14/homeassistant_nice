"""Priority and deadline scheduling for the single module session."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from itertools import count
import threading
import time

from .errors import NiceBidiConnectionError


class OperationPriority(IntEnum):
    STOP = 0
    CONTROL = 10
    READBACK = 20
    STATUS = 30
    DIAGNOSTIC = 40
    METADATA = 50


class OperationExpired(NiceBidiConnectionError):
    """The operation never acquired a valid dispatch slot."""

    def __init__(self, message="Operation expired before dispatch"):
        super().__init__(message)
        self.delivery = "not_sent"


@dataclass(frozen=True, order=True, slots=True)
class _Ticket:
    priority: int
    order: int
    deadline: float


class SessionScheduler:
    """Reentrant lock interface with FIFO order within a priority and bounded waits."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._condition = threading.Condition()
        self._tickets: list[_Ticket] = []
        self._counter = count()
        self._owner: int | None = None
        self._priority = OperationPriority.STATUS
        self._depth = 0
        self.closed = False
        self.expired = 0
        self._on_idle = None

    def acquire(
        self,
        blocking=True,
        timeout=-1,
        *,
        priority=OperationPriority.STATUS,
        allow_closed=False,
    ):
        thread = threading.get_ident()
        with self._condition:
            if self.closed and not allow_closed:
                raise OperationExpired("Entry is shutting down")
            if self._owner == thread:
                self._depth += 1
                return True
            deadline = self._clock() + timeout if timeout >= 0 else float("inf")
            ticket = _Ticket(int(priority), next(self._counter), deadline)
            self._tickets.append(ticket)
            try:
                while True:
                    now = self._clock()
                    if self.closed and not allow_closed:
                        raise OperationExpired("Entry is shutting down")
                    if timeout >= 0 and now >= deadline:
                        self.expired += 1
                        return False
                    if self._owner is None and ticket == min(self._tickets):
                        self._owner, self._priority, self._depth = thread, priority, 1
                        return True
                    if not blocking:
                        return False
                    self._condition.wait(min(0.1, max(0, deadline - now)))
            finally:
                self._tickets.remove(ticket)
                self._condition.notify_all()

    def release(self):
        callback = None
        with self._condition:
            if self._owner != threading.get_ident():
                raise RuntimeError("Session released by a non-owner")
            self._depth -= 1
            if not self._depth:
                self._owner = None
                callback, self._on_idle = self._on_idle, None
                self._condition.notify_all()
        if callback is not None:
            callback()

    def should_yield(self):
        with self._condition:
            return self.closed or any(
                ticket.priority < self._priority for ticket in self._tickets
            )

    def cancel_pending(self, on_idle=None):
        callback = None
        with self._condition:
            self.closed = True
            if self._owner is None:
                callback = on_idle
            else:
                self._on_idle = on_idle
            self._condition.notify_all()
        if callback is not None:
            callback()

    @contextmanager
    def hold(
        self, priority=OperationPriority.STATUS, *, timeout=30.0, allow_closed=False
    ):
        if not self.acquire(
            timeout=timeout, priority=priority, allow_closed=allow_closed
        ):
            raise OperationExpired()
        try:
            yield
        finally:
            self.release()

    def __enter__(self):
        if not self.acquire(timeout=30):
            raise OperationExpired()
        return self

    def __exit__(self, *args):
        self.release()

    def diagnostics(self):
        with self._condition:
            return {
                "closed": self.closed,
                "pending": len(self._tickets),
                "expired": self.expired,
                "active_priority": int(self._priority)
                if self._owner is not None
                else None,
            }
