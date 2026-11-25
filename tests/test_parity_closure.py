"""Every indexed source has an explicit, provenance-backed non-dispatch disposition."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.nice_bidiwifi.feature_catalog import (
    FEATURES,
    FeatureBlocked,
    app_inventory,
)
from custom_components.nice_bidiwifi.parity import (
    FAMILIES,
    parity_summary,
    reconcile,
    summary,
    validate_closure,
    validation_matrix,
)
from tests.test_coordinator import _coordinator

ROOT = Path(__file__).resolve().parents[1]


def closed_index():
    return json.loads(
        (ROOT / "custom_components/nice_bidiwifi/parity_inventory.json").read_text()
    )


def test_every_indexed_source_has_a_final_disposition_and_preserved_provenance():
    raw = app_inventory()["entries"]
    index = closed_index()
    facts = {row["id"]: row["source_evidence"] for row in index["records"]}
    assert reconcile(raw, facts) == index
    assert summary(index) == parity_summary()
    assert {r["id"] for r in raw} == {r["id"] for r in index["records"]}
    assert not index["dispatch_source"]
    assert not any(str(feature.implementation) == "planned" for feature in FEATURES)
    validate_closure(index, validation_matrix(), ROOT)
    assert not any(
        r["disposition"] in {"planned", "review_required"} for r in index["records"]
    )


def test_changed_source_hash_requires_explicit_evidence_review():
    index = closed_index()
    raw = [dict(app_inventory()["entries"][0])]
    raw[0]["sha256"] = "0" * 64
    facts = {r["id"]: r["source_evidence"] for r in index["records"]}
    with pytest.raises(ValueError, match="hash changed"):
        reconcile(raw, facts)


def test_core_protocol_is_not_mislabelled_as_an_unimplemented_native_transport():
    rows = [
        r
        for r in closed_index()["records"]
        if r["id"].endswith(".nhk_core.CoreProtocol")
    ]
    assert rows and rows[0]["effective_role"] == "protocol"
    assert rows[0]["family"] == "core" and "core.tables" in rows[0]["features"]
    assert rows[0]["source_evidence"]["core_table_read_references"]


def test_alarm_demo_disposition_has_direct_dummy_flow_evidence():
    rows = [r for r in closed_index()["records"] if r["family"] == "app_prototype"]
    assert rows and any(r["source_evidence"]["prototype_markers"] for r in rows)
    assert not any(r["disposition"] == "implemented" for r in rows)


@pytest.mark.parametrize(
    "family",
    [f for f in FAMILIES if f.disposition in {"blocked", "external_dependency"}],
    ids=lambda f: f.key,
)
async def test_blocked_family_has_a_real_negative_entry_point_before_preparation(
    hass, family
):
    owner = _coordinator(hass)
    owner.command_executor.execute = AsyncMock()
    # These family links point to real feature/procedure/transport preflights,
    # not an invented JSON-to-operation dispatcher.
    for key in family.features:
        with pytest.raises(FeatureBlocked):
            await owner.features.execute(key)
    owner.command_executor.execute.assert_not_awaited()


async def test_runtime_summary_never_reads_the_large_source_index_during_normal_use(
    hass, monkeypatch
):
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path.name == "parity_inventory.json":
            pytest.fail("full source index must not be loaded by runtime reporting")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    owner = _coordinator(hass)
    report = await owner.features.execute("parity.report")
    assert report["all_sources_dispositioned"] and not report["index_used_for_dispatch"]


def test_validation_levels_do_not_claim_universal_hardware_or_authorize_stable():
    matrix = validation_matrix()
    assert (
        not matrix["universal_hardware_validation"]
        and not matrix["stable_release_authorized"]
    )
    assert any(c["level"] == "source_only_blocked" for c in matrix["cases"])
    assert any("pending" in c["level"] for c in matrix["cases"])
    assert all(case["limitation"] for case in matrix["cases"])


def test_partial_umbrella_features_have_concrete_gaps_instead_of_planned_messages():
    for feature in FEATURES:
        if str(feature.implementation)=='partial':
            assert feature.gaps
            with pytest.raises(FeatureBlocked) as error:
                feature.require_implemented()
            assert 'planned for phase' not in str(error.value)
