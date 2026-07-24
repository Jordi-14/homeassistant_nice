"""Tests for normalized Nice zeroconf discovery."""

from __future__ import annotations

import pytest

from custom_components.nice_bidiwifi.models.capabilities import ProductFamily
from custom_components.nice_bidiwifi.models.discovery import (
    DEFAULT_NICE_PORT,
    NiceDiscoveryInfo,
    NiceDiscoveryService,
    normalize_device_id,
)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:FF"),
        ("AA-BB-CC-DD-EE-FF", "AA:BB:CC:DD:EE:FF"),
        ("aabb.ccdd.eeff", "AA:BB:CC:DD:EE:FF"),
        (" AABBCCDDEEFF ", "AA:BB:CC:DD:EE:FF"),
        ("not-an-identity", None),
        ("", None),
    ],
)
def test_normalize_device_id(raw: str, normalized: str | None) -> None:
    """Test stable device identities use one canonical representation."""
    assert normalize_device_id(raw) == normalized


def test_hap_discovery_parses_observed_txt_metadata() -> None:
    """Test preferred MyNice Bonjour TXT keys and all advertised addresses."""
    discovered = NiceDiscoveryInfo.from_service(
        host="192.0.2.20",
        addresses=("192.0.2.20", "2001:db8::20"),
        port=8443,
        name="Driveway._hap._tcp.local.",
        hostname="driveway.local.",
        service_type="_hap._tcp.local.",
        properties={
            b"DEVICEID": b"aa-bb-cc-dd-ee-ff",
            "model": "Nice - BIDIWIFI - SB725A1-R0-R01",
            "protovers": "1.1",
            "sf": "0",
        },
    )

    assert discovered.unique_id == "AA:BB:CC:DD:EE:FF"
    assert discovered.name == "Driveway"
    assert discovered.addresses == ("192.0.2.20", "2001:db8::20")
    assert discovered.port == 8443
    assert discovered.service is NiceDiscoveryService.HAP
    assert discovered.family is ProductFamily.BIDI_WIFI
    assert discovered.manufacturer == "Nice"
    assert discovered.hardware == "SB725A1-R0-R01"
    assert discovered.protocol_version == "1.1"
    assert discovered.status_flag == "0"
    assert discovered.operational is True
    assert discovered.provisioning is False
    assert discovered.supported_family is True
    assert discovered.entry_metadata() == {
        "discovery_service_type": "_hap._tcp.local.",
        "discovery_name": "Driveway",
        "discovery_addresses": ["192.0.2.20", "2001:db8::20"],
        "discovery_model": "Nice - BIDIWIFI - SB725A1-R0-R01",
        "discovery_manufacturer": "Nice",
        "discovery_hardware": "SB725A1-R0-R01",
        "discovery_protocol": "1.1",
        "discovery_status_flag": "0",
    }


def test_nap_discovery_uses_txt_fallbacks_and_default_port() -> None:
    """Test the id/md fallbacks observed in the app."""
    discovered = NiceDiscoveryInfo.from_service(
        host="2001:db8::21",
        port=None,
        name="Garage._nap._tcp.local.",
        hostname="garage.local.",
        service_type="_nap._tcp.local.",
        properties={
            "id": "112233445566",
            "md": "Nice - CU_WIFI - CU1",
        },
    )

    assert discovered.unique_id == "11:22:33:44:55:66"
    assert discovered.addresses == ("2001:db8::21",)
    assert discovered.port == DEFAULT_NICE_PORT
    assert discovered.family is ProductFamily.CU_WIFI
    assert discovered.service is NiceDiscoveryService.NAP
    assert discovered.operational is True


@pytest.mark.parametrize(
    "service_type",
    [
        "_mfi-config._tcp.local.",
        "_wnc-config._tcp.local.",
    ],
)
def test_provisioning_services_are_not_operational(
    service_type: str,
) -> None:
    """Test setup access-point services cannot be offered for NHK control."""
    discovered = NiceDiscoveryInfo.from_service(
        host="192.0.2.22",
        port=443,
        name=f"Nice setup.{service_type}",
        hostname="nice-setup.local.",
        service_type=service_type,
        properties={"deviceid": "AA:BB:CC:DD:EE:FF"},
    )

    assert discovered.provisioning is True
    assert discovered.operational is False


def test_identity_falls_back_to_service_name_or_hostname() -> None:
    """Test operational advertisements without TXT identity stay stable."""
    from_name = NiceDiscoveryInfo.from_service(
        host="192.0.2.23",
        port=443,
        name="Nice-AA-BB-CC-DD-EE-FF._nap._tcp.local.",
        hostname="nice.local.",
        service_type="_nap._tcp.local.",
        properties={},
    )
    from_hostname = NiceDiscoveryInfo.from_service(
        host="192.0.2.24",
        port=443,
        name="Nice._nap._tcp.local.",
        hostname="nice-112233445566.local.",
        service_type="_nap._tcp.local.",
        properties={},
    )

    assert from_name.unique_id == "AA:BB:CC:DD:EE:FF"
    assert from_hostname.unique_id == "11:22:33:44:55:66"


@pytest.mark.parametrize(
    ("model", "family", "supported"),
    [
        ("Nice - IT4WIFI - HW1", ProductFamily.IT4_WIFI, True),
        ("Nice - CORE - HW1", ProductFamily.CORE, False),
        ("Nice - PROVIEW - HW1", ProductFamily.PROVIEW, False),
        ("Unidentified Nice interface", ProductFamily.UNKNOWN, True),
    ],
)
def test_product_family_classification(
    model: str,
    family: ProductFamily,
    supported: bool,
) -> None:
    """Test all product-family names observed in the decompiled app."""
    discovered = NiceDiscoveryInfo.from_service(
        host="192.0.2.25",
        port=443,
        name="Nice._nap._tcp.local.",
        hostname="nice.local.",
        service_type="_nap._tcp.local.",
        properties={
            "deviceid": "AA:BB:CC:DD:EE:FF",
            "model": model,
        },
    )

    assert discovered.family is family
    assert discovered.supported_family is supported


def test_unknown_service_and_missing_identity_remain_unusable() -> None:
    """Test unknown services and absent stable identity remain explicit."""
    discovered = NiceDiscoveryInfo.from_service(
        host="192.0.2.26",
        port=443,
        name="Nice._http._tcp.local.",
        hostname="nice.local.",
        service_type="_http._tcp.local.",
        properties={},
    )

    assert discovered.service is NiceDiscoveryService.UNKNOWN
    assert discovered.operational is False
    assert discovered.unique_id is None
