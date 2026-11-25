"""Periodic permission context cannot repeatedly abort a broad telemetry scan."""

import threading
import time

import pytest

from custom_components.nice_bidiwifi.scheduler import OperationPriority
from tests.test_connection_router import _router


@pytest.mark.parametrize(
    "active_priority", [OperationPriority.STATUS, OperationPriority.METADATA]
)
@pytest.mark.parametrize("method", ["read_pairings", "read_controller_menu"])
@pytest.mark.parametrize("background", [True, False])
def test_periodic_context_waits_for_telemetry_but_foreground_context_can_preempt(
    method, background, active_priority
):
    router, _, clients = _router("local_only")
    results = []
    failures = []
    setattr(clients[0], method, lambda **kwargs: "context")

    def request():
        try:
            results.append(getattr(router, method)(background=background))
        except Exception as error:
            failures.append(error)

    thread = threading.Thread(target=request)
    router._lock.acquire(priority=active_priority)
    try:
        thread.start()
        deadline = time.monotonic() + 2
        while (
            router._lock.diagnostics()["pending"] == 0 and time.monotonic() < deadline
        ):
            time.sleep(0.001)
        assert router._lock.diagnostics()["pending"] == 1
        assert router._lock.should_yield() is (
            not background or active_priority is OperationPriority.METADATA
        )
    finally:
        router._lock.release()
        thread.join(2)
        router.close()
    assert not thread.is_alive() and not failures
    assert results == ["context"]
