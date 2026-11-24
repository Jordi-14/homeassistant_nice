"""Bounded session recovery with explicit credential/session/transport outcomes."""

from __future__ import annotations

from dataclasses import dataclass
import time

from .errors import NiceBidiConnectionError


class SessionRecoveryDeferred(NiceBidiConnectionError):
    def __init__(self):
        super().__init__("Session recovery is cooling down", layer="session")
        self.delivery = "not_sent"


@dataclass(slots=True)
class SessionState:
    state: str = "disconnected"
    last_code: str | None = None
    attempts: int = 0
    window_start: float = 0
    retry_at: float = 0
    connected_once: bool = False
    generation: int = 0

    def before_connect(self):
        now = time.monotonic()
        if now < self.retry_at:
            raise SessionRecoveryDeferred()
        if now - self.window_start >= 300:
            self.window_start = now
            self.attempts = 0
        self.state = "authenticating"

    def rejected(self, code):
        self.last_code = code
        if code in {"7", "15"}:
            now = time.monotonic()
            if not self.attempts:
                self.window_start = now
            self.attempts += 1
            self.retry_at = (
                now + min(60, 2**self.attempts)
                if self.attempts < 3
                else self.window_start + 300
            )
            self.state = "session_rejected_backoff"
        else:
            self.state = (
                "pairing_identity_rejected"
                if code == "1"
                else "authentication_rejected"
            )

    def connected(self):
        self.state = "authenticated"
        self.last_code = None
        self.attempts = 0
        self.retry_at = 0
        self.connected_once = True
        self.generation += 1

    def invalidate(self, code=None):
        self.state = "session_invalidated"
        self.last_code = code
        self.generation += 1

    def closed(self):
        if self.state not in {
            "pairing_identity_rejected",
            "authentication_rejected",
            "session_rejected_backoff",
        }:
            self.state = "disconnected"
        self.generation += 1

    def diagnostics(self):
        return {
            "state": self.state,
            "last_code": self.last_code,
            "bounded_recovery_attempts": self.attempts,
            "retry_after_seconds": max(0, round(self.retry_at - time.monotonic(), 1)),
            "connected_once": self.connected_once,
            "code1_policy": "explicit pairing/credential rejection; cause unresolved; no automatic pairing or reset",
        }
