"""Composition helpers for coordinator-owned domain controllers."""

from __future__ import annotations


class RuntimeController[ContextT]:
    """Explicit context dependency; each controller owns its actual attributes."""

    def __init__(self, coordinator: ContextT) -> None:
        self._coordinator = coordinator
