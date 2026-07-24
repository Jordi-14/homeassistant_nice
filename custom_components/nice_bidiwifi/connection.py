"""Normalized connection and route health state for Nice."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models.config import NiceConnectionPolicy

CONNECTION_STATE_AUTH_FAILED = "auth_failed"
CONNECTION_STATE_CONNECTED = "connected"
CONNECTION_STATE_FAILED = "failed"
CONNECTION_STATE_RECONNECTING = "reconnecting"
CONNECTION_STATE_UNKNOWN = "unknown"


class NiceConnectionRoute(StrEnum):
    """A route that can carry the NHK protocol."""

    NONE = "none"
    LOCAL = "local"
    CLOUD = "cloud"


class NiceRouteState(StrEnum):
    """Current reachability of one configured route."""

    NOT_CONFIGURED = "not_configured"
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


@dataclass(slots=True)
class NiceConnectionHealth:
    """Route health shared by diagnostics, entities, and the future router."""

    active: NiceConnectionRoute
    local: NiceRouteState
    cloud: NiceRouteState

    @classmethod
    def from_policy(
        cls,
        policy: NiceConnectionPolicy,
    ) -> NiceConnectionHealth:
        """Initialize configured routes without claiming reachability."""
        return cls(
            active=NiceConnectionRoute.NONE,
            local=(
                NiceRouteState.UNKNOWN
                if policy.local is not None
                else NiceRouteState.NOT_CONFIGURED
            ),
            cloud=(
                NiceRouteState.UNKNOWN
                if policy.relay is not None
                else NiceRouteState.NOT_CONFIGURED
            ),
        )

    def set_route_state(
        self,
        route: NiceConnectionRoute,
        state: NiceRouteState,
        *,
        active: bool = False,
    ) -> None:
        """Update one route and keep the active route consistent."""
        if route is NiceConnectionRoute.NONE:
            raise ValueError("The none route does not have a health state")
        if state is NiceRouteState.NOT_CONFIGURED:
            raise ValueError("Configured route health cannot become not configured")
        current = (
            self.local
            if route is NiceConnectionRoute.LOCAL
            else self.cloud
        )
        if current is NiceRouteState.NOT_CONFIGURED:
            raise ValueError("An unconfigured route cannot report health")
        if route is NiceConnectionRoute.LOCAL:
            self.local = state
        else:
            self.cloud = state

        if active:
            if state is not NiceRouteState.CONNECTED:
                raise ValueError("Only a connected route can become active")
            self.active = route
        elif self.active is route and state is not NiceRouteState.CONNECTED:
            self.active = NiceConnectionRoute.NONE

    def mark_overall_state(
        self,
        state: str,
        *,
        route: NiceConnectionRoute,
    ) -> None:
        """Map the legacy coordinator state onto one route."""
        if state == CONNECTION_STATE_CONNECTED:
            self.set_route_state(
                route,
                NiceRouteState.CONNECTED,
                active=True,
            )
            return
        if state == CONNECTION_STATE_UNKNOWN:
            self.set_route_state(route, NiceRouteState.UNKNOWN)
            return
        self.set_route_state(route, NiceRouteState.DISCONNECTED)
