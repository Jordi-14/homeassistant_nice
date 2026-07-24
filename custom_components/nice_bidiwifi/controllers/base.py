"""Composition helpers for coordinator-owned domain controllers."""

from __future__ import annotations

from typing import Any


class OwnerBoundController[OwnerT]:
    """Run extracted behavior against state owned by a coordinator."""

    __slots__ = ("_owner",)

    def __init__(self, owner: OwnerT) -> None:
        object.__setattr__(self, "_owner", owner)

    def __getattribute__(self, name: str) -> Any:
        """Honor runtime overrides installed on the owning coordinator."""
        if name in {"_owner", "owner", "__class__", "__dict__", "__slots__"}:
            return object.__getattribute__(self, name)

        owner = object.__getattribute__(self, "_owner")
        owner_vars = getattr(owner, "__dict__", {})
        if name in owner_vars:
            return owner_vars[name]

        return object.__getattribute__(self, name)

    @property
    def owner(self) -> OwnerT:
        """Return the owning coordinator."""
        return object.__getattribute__(self, "_owner")

    def __getattr__(self, name: str) -> Any:
        """Resolve shared coordinator state and cross-controller operations."""
        return getattr(self.owner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Keep mutable runtime state on the owning coordinator."""
        if name == "_owner":
            object.__setattr__(self, name, value)
            return
        setattr(self.owner, name, value)


def controller_defines(controller: object, name: str) -> bool:
    """Return whether a controller class explicitly provides an attribute."""
    return any(name in cls.__dict__ for cls in type(controller).__mro__)
