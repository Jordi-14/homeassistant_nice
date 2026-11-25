"""Executable contributor examples: no unrelated coordinator/platform rewrite."""

from unittest.mock import AsyncMock

import pytest

from custom_components.nice_bidiwifi.feature_catalog import (
    FeatureBinding,
    FeatureBlocked,
)
from custom_components.nice_bidiwifi.installer_catalog import (
    InstallerProcedure,
    InstallerStep,
)
from custom_components.nice_bidiwifi.procedures import ProcedureRunner


def test_unknown_slot_cannot_activate_but_zero_is_not_mistaken_for_missing():
    with pytest.raises(FeatureBlocked):
        FeatureBinding("t4", "example", (("code", None),), ("code",)).require_resolved()
    FeatureBinding("t4", "example", (("code", 0),), ("code",)).require_resolved()


async def test_verified_reader_plugs_into_existing_procedure_runner():
    reader = AsyncMock(return_value={"observed_count": 2})
    runner = ProcedureRunner(
        {"example_reader": reader},
        prepare=AsyncMock(),
        guard=lambda spec: None,
        persist=AsyncMock(),
    )
    spec = InstallerProcedure(
        "fixture_reader",
        "Fixture-only reader",
        (InstallerStep("read", "example_reader"),),
        (),
        "Checked read-only reply",
        "Repeat the read after checking connectivity",
        state_machine_verified=True,
    )
    assert await runner.run(spec) == {"observed_count": 2}
    assert not runner.record.dispatched and runner.record.state == "completed"
    runner.persist.assert_not_awaited()
